import difflib
import os
import re
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from kokoro_onnx import Kokoro
from openai import OpenAI
from actions import run_tool, TOOL_SCHEMAS, QUIT
from body import Body

SAMPLE_RATE = 16000
BLOCK = 512                # 32 ms per read
END_SILENCE_BLOCKS = 25    # ~0.8 s of quiet ends an utterance
MAX_BLOCKS = 470           # ~15 s max per utterance
MIN_BLOCKS = 10            # ignore clicks shorter than ~0.3 s
WAKE_WORD = "jarvis"
WAKE_CUTOFF = 0.65

FOLLOWUP_SECONDS = 6       # how long he keeps listening after he answers
MAX_HISTORY = 8            # messages remembered during a conversation (4 exchanges)

VOICE = "bm_fable"
VOICE_SPEED = 1.0          # try 0.9 for a slower, calmer delivery

client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
body = Body()              # the floating panel
stt = None                 # loaded in the background thread, so the panel shows up at once
tts = None
history = []               # the current conversation, cleared when the follow-up window closes

tools = TOOL_SCHEMAS       # defined in actions.py, add new abilities there

SYSTEM = (
    "You are Jarvis, a personal AI assistant living on the user's Windows PC, "
    "in the style of the Jarvis from the Iron Man films: calm, dry, polite, quietly witty. "
    "Address the user as 'sir'. Your replies are spoken aloud, so keep them to one or two short sentences, "
    "with no lists, markdown, or emojis. "
    "Use a tool whenever one fits the request: open_app to open an app, close_app to close one, "
    "open_project to open a coding project, get_time for the time or date, "
    "open_website to open a website (pass its full https address), "
    "search_web to search the internet or look something up online, "
    "play_youtube to play a song or video (the song name is the query), "
    "quit_jarvis when the user tells YOU to shut down, quit, exit, turn off, or go to sleep "
    "(it only closes you, never the computer or other apps). "
    "Call one tool per action and pass only the bare app or project name, without words like 'the app' or 'please'. "
    "If no tool fits, just answer or chat naturally. Never say you can't do something a tool can do. "
    "Only offer or promise things your tools can actually do. "
    "Searching only opens the results in the browser, you cannot read them out. "
    "The user may reply briefly, like 'yes' or 'do that', so use the earlier messages to understand what they mean."
)

PROMPT_HINT = "Jarvis, open Docker Desktop, close Ubuntu, open AmeFil project, play Odoriko on YouTube."

PROMPT_HINT = "Jarvis, open Docker Desktop, close Ubuntu, open AmeFil project."


def rms(block):
    return float(np.sqrt(np.mean(block ** 2)))


def speak(text, stream):
    """Say the text out loud, then throw away anything the mic heard meanwhile."""
    print("Jarvis:", text)
    samples, rate = tts.create(text, voice=VOICE, speed=VOICE_SPEED, lang="en-gb")
    body.set_jarvis(text, duration=len(samples) / rate)   # text types out as he speaks
    body.set_state("speaking")
    sd.play(samples, rate)
    sd.wait()
    time.sleep(0.3)  # let the room echo die down
    waiting = stream.read_available
    if waiting:
        stream.read(waiting)
    body.set_state("idle")


def shutdown():
    """Exit the whole program, panel included, like pressing Ctrl+C."""
    body.set_state("idle")
    print("Jarvis offline.", flush=True)
    os._exit(0)     # a hard exit is fine for now: nothing needs saving yet


def calibrate(stream):
    print("Calibrating... stay quiet for 1 second.")
    levels = [rms(stream.read(BLOCK)[0][:, 0]) for _ in range(31)]
    noise = float(np.median(levels))
    threshold = max(noise * 3, 0.01)
    print(f"Noise level {noise:.4f}, speech threshold {threshold:.4f}")
    return threshold


def wait_for_utterance(stream, threshold, deadline=None):
    """Record one spoken phrase. With a deadline, gives up (returns None) if you haven't started talking by then."""
    preroll = deque(maxlen=10)  # keeps the ~0.3 s before speech starts
    frames = []
    speaking = False
    silence = 0
    while True:
        block = stream.read(BLOCK)[0][:, 0].copy()
        level = rms(block)
        if not speaking:
            preroll.append(block)
            if level > threshold:
                speaking = True
                body.set_state("listening")   # panel lights up when you start talking
                frames = list(preroll)
                silence = 0
            elif deadline and time.monotonic() > deadline:
                return None
        else:
            frames.append(block)
            silence = silence + 1 if level < threshold else 0
            if silence >= END_SILENCE_BLOCKS or len(frames) >= MAX_BLOCKS:
                break
    if len(frames) < MIN_BLOCKS:
        return None
    return np.concatenate(frames)


def transcribe(audio):
    body.set_state("thinking")
    segments, _ = stt.transcribe(
        audio, language="en", vad_filter=True, initial_prompt=PROMPT_HINT
    )
    return " ".join(s.text.strip() for s in segments).strip()


def split_wake_word(text):
    """Return (heard_wake_word, command_after_it)."""
    words = re.findall(r"[a-zA-Z']+", text)
    for i, w in enumerate(words):
        if difflib.SequenceMatcher(None, w.lower(), WAKE_WORD).ratio() >= WAKE_CUTOFF:
            return True, " ".join(words[i + 1:]).strip()
    return False, ""


def remember(user_text, reply):
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply})
    del history[:-MAX_HISTORY]


def handle(text, stream):
    body.set_state("thinking")
    messages = [{"role": "system", "content": SYSTEM}] + history[-MAX_HISTORY:] + [
        {"role": "user", "content": text}
    ]
    try:
        resp = client.chat.completions.create(
            model="local-model",
            messages=messages,
            tools=tools,
        )
    except Exception as e:
        print("LM Studio error:", e)
        speak("I can't reach my brain, sir. Is LM Studio's server running?", stream)
        return
    msg = resp.choices[0].message
    if not msg.tool_calls:
        reply = msg.content or "I'm not sure how to do that, sir."
        speak(reply, stream)
        remember(text, reply)
        return
    results = []
    for call in msg.tool_calls:
        result = run_tool(call.function.name, call.function.arguments)
        results.append(result)
        speak(result, stream)
    if QUIT.is_set():          # he has said goodbye, now actually leave
        shutdown()
    remember(text, " ".join(results))


def run_command(command, stream):
    print("You said:", command)
    body.set_user(command)
    body.set_jarvis("")        # clear the old reply while he thinks
    handle(command, stream)


def follow_up(stream, threshold):
    """After he answers, keep listening for a few seconds. No wake word needed."""
    deadline = time.monotonic() + FOLLOWUP_SECONDS
    while True:
        body.set_state("listening")     # cyan: he's waiting for you
        audio = wait_for_utterance(stream, threshold, deadline)
        text = transcribe(audio) if audio is not None else ""
        if text:
            heard, rest = split_wake_word(text)
            command = rest if heard else text
            if command:
                run_command(command, stream)
            else:
                speak("Yes, sir?", stream)       # you only said "Jarvis"
            deadline = time.monotonic() + FOLLOWUP_SECONDS
        elif time.monotonic() >= deadline:
            break
    history.clear()                     # conversation over, next "Jarvis" starts fresh
    body.set_state("idle")


def jarvis_loop():
    global stt, tts
    body.set_state("thinking")
    body.set_jarvis("Loading models...")
    stt = WhisperModel("base.en", device="cpu", compute_type="int8")
    tts = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=BLOCK) as stream:
        body.set_jarvis("Calibrating... stay quiet for a second.")
        threshold = calibrate(stream)
        speak("Good evening, sir. All systems are online.", stream)
        print("Jarvis is listening. Say 'Jarvis ...' (or right-click the panel to quit)")
        while True:
            audio = wait_for_utterance(stream, threshold)
            if audio is None:
                body.set_state("idle")
                continue
            text = transcribe(audio)
            if not text:
                body.set_state("idle")
                continue

            heard, command = split_wake_word(text)
            if not heard:
                print(f"(ignored: {text})")
                body.set_state("idle")
                continue

            if not command:
                speak("Yes, sir?", stream)
                audio = wait_for_utterance(stream, threshold)
                if audio is None:
                    body.set_state("idle")
                    continue
                command = transcribe(audio)
                if not command:
                    body.set_state("idle")
                    continue

            run_command(command, stream)
            follow_up(stream, threshold)


threading.Thread(target=jarvis_loop, daemon=True).start()
body.run()   # the panel owns the main thread; right-click it to quit