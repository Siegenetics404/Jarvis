import difflib
import os
import re
import signal
import threading
import time
import traceback
import winsound
from collections import deque

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from kokoro_onnx import Kokoro
from openai import OpenAI
from actions import run_tool, resolve_pending, TOOL_SCHEMAS, QUIT
from body import Body
from skills import notifications

SAMPLE_RATE = 16000
BLOCK = 512                # 32 ms per read
END_SILENCE_BLOCKS = 25    # ~0.8 s of quiet ends an utterance
MAX_BLOCKS = 470           # ~15 s max per utterance
MIN_BLOCKS = 10            # ignore clicks shorter than ~0.3 s
WAKE_WORD = "jarvis"
WAKE_CUTOFF = 0.65

FOLLOWUP_SECONDS = 6       # how long he keeps listening after he answers
MAX_HISTORY = 8            # messages remembered during a conversation (4 exchanges)
SHOW_IGNORED = True        # flash the panel red when he hears speech without his name
BARGE_IN = True            # listen for "stop" while he is thinking or talking

VOICE = "bm_fable"
VOICE_SPEED = 1.0          # try 0.9 for a slower, calmer delivery

# If a tool's reply contains one of these, the panel shows it as an error
ERROR_HINTS = ("couldn't", "can't", "failed", "unknown tool", "isn't running", "don't see",
               "out of date", "only open", "which tabs", "not allowed")

# Saying one of these (alone, or with "Jarvis" or "it") stops him
STOP_TRIGGERS = {"stop", "cancel", "abort", "enough", "quiet", "nevermind"}
STOP_FILLER = {"it", "that", "this", "now", "please", "sir", "right", "oh", "just", "all",
               "everything", "talking", "never", "mind", "be", "okay", "ok"}

NOTICE = object()          # returned by wait_for_utterance when a reminder is waiting to be spoken

client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
body = Body()              # the floating panel
stt = None                 # loaded in the background thread, so the panel shows up at once
tts = None
history = []               # the current conversation, cleared when the follow-up window closes
cancel = threading.Event() # set when you say stop (or double-click the panel)
active_monitor = None      # the listener that watches for "stop" while he is busy

tools = TOOL_SCHEMAS       # defined in actions.py and the skills folder, add new abilities there

from actions import SYSTEM_HINTS   # every skill adds its own line about when to use its tools

SYSTEM = (
    "You are Jarvis, a personal AI assistant living on the user's Windows PC, "
    "in the style of the Jarvis from the Iron Man films: calm, dry, polite, quietly witty. "
    "Address the user as 'sir'. Your replies are spoken aloud, so keep them to one or two short sentences, "
    "with no lists, markdown, or emojis. "
    "Use a tool whenever one fits the request: open_app to open an app, close_app to close a desktop program, "
    "open_project to open a coding project, get_time for the time or date, "
    "open_website to open a website (pass its full https address), "
    "search_web to search the internet or look something up online, "
    "play_youtube to play a song or video on YouTube or when no music service is named, "
    "close_tabs to close browser tabs or websites, once for each tab "
    "(pass a few words from the tab's title or site), "
    "count_tabs to say how many tabs are open, "
    "quit_jarvis when the user tells YOU to shut down, quit, exit, turn off, or go to sleep "
    "(it only closes you, never the computer or other apps). "
    + " ".join(SYSTEM_HINTS) + " "
    "Call one tool per action and pass only the bare app or project name, without words like 'the app' or 'please'. "
    "If no tool fits, just answer or chat naturally. Never say you can't do something a tool can do. "
    "Only offer or promise things your tools can actually do. "
    "Searching only opens the results in the browser, you cannot read them out. "
    "The user may reply briefly, like 'yes' or 'do that', so use the earlier messages to understand what they mean."
)

PROMPT_HINT = "Jarvis, open Docker Desktop, close Ubuntu, open AmeFil project, play Odoriko on YouTube."


def rms(block):
    return float(np.sqrt(np.mean(block ** 2)))


def looks_like_error(text):
    lowered = text.lower()
    return any(hint in lowered for hint in ERROR_HINTS)


# ------------------------------------------------------------------ stop

def is_stop(text):
    """True when the whole sentence is just a stop request: 'stop', 'Jarvis, stop it', 'never mind'..."""
    words = [w for w in re.findall(r"[a-z']+", text.lower())
             if difflib.SequenceMatcher(None, w, WAKE_WORD).ratio() < WAKE_CUTOFF]   # drop "Jarvis"
    if not words or len(words) > 4:
        return False
    has_trigger = any(w in STOP_TRIGGERS for w in words) or "never mind" in " ".join(words)
    return has_trigger and all(w in STOP_TRIGGERS or w in STOP_FILLER for w in words)


def request_stop():
    """Cut his voice off and tell everything that is running to give up."""
    cancel.set()
    try:
        sd.stop()
    except Exception:
        pass


class StopMonitor(threading.Thread):
    """While he is thinking or talking, listens to the mic and reacts to a short 'stop'.
    It is the only thing reading the mic during that time, so the main loop must not read until stop() returns."""

    def __init__(self, stream, threshold):
        super().__init__(daemon=True)
        self.stream = stream
        self.threshold = threshold
        self.running = True
        self.current_text = ""       # what he is saying right now, so his own echo isn't mistaken for you

    def stop(self):
        self.running = False
        self.join(timeout=3)

    def heard_stop(self, audio):
        segments, _ = stt.transcribe(audio, language="en", vad_filter=True, beam_size=1)
        text = " ".join(s.text for s in segments).strip()
        if not text or not is_stop(text):
            return False
        mine = self.current_text.lower()
        spoken = set(re.findall(r"[a-z']+", text.lower())) & STOP_TRIGGERS
        if any(word in mine for word in spoken):          # he said that word himself, it's just his echo
            return False
        print(f"(stop heard: {text})")
        return True

    def run(self):
        preroll = deque(maxlen=6)
        frames, talking, silence = [], False, 0
        try:
            while self.running:
                block = self.stream.read(BLOCK)[0][:, 0].copy()
                level = rms(block)
                if not talking:
                    preroll.append(block)
                    if level > self.threshold:
                        talking, frames, silence = True, list(preroll), 0
                    continue
                frames.append(block)
                silence = silence + 1 if level < self.threshold else 0
                if silence >= 12 or len(frames) >= 62:      # ~0.4 s of quiet, or ~2 s of speech
                    audio = np.concatenate(frames)
                    talking, frames, silence = False, [], 0
                    if len(audio) >= BLOCK * 6 and self.heard_stop(audio):
                        request_stop()
                        return
        except Exception:
            pass        # the microphone failed; the main loop will notice and recover


# ---------------------------------------------------------------- speaking

def speak(text, stream, error=False):
    """Say the text out loud. With error=True the panel shows red while he speaks, then fades back to idle."""
    print("Jarvis:", text)
    monitor = active_monitor
    samples, rate = tts.create(text, voice=VOICE, speed=VOICE_SPEED, lang="en-gb")
    body.set_jarvis(text, duration=len(samples) / rate)   # text types out as he speaks
    body.set_state("error" if error else "speaking")
    if monitor:
        monitor.current_text = text
    sd.play(samples, rate)
    sd.wait()                # returns early if you say stop
    if monitor:
        monitor.current_text = ""
    time.sleep(0.3)  # let the room echo die down
    if monitor is None:      # with a stop monitor running, run_command clears the mic afterwards
        waiting = stream.read_available
        if waiting:
            stream.read(waiting)
    if error:
        body.flash("error", 2.5)
    else:
        body.set_state("idle")


def deliver_notifications(stream):
    """Speak anything the skills have queued, like a reminder that just came due. Two beeps first."""
    while not notifications.empty():
        message = notifications.get()
        try:
            winsound.Beep(880, 150)
            winsound.Beep(1175, 200)
        except Exception:
            pass
        body.set_user("Reminder")
        speak(message, stream)


def shutdown():
    """Exit the whole program, panel included, like pressing Ctrl+C."""
    body.set_state("idle")
    print("Jarvis offline.", flush=True)
    os._exit(0)     # a hard exit is fine: reminders and notes are saved the moment they are made


def calibrate(stream):
    print("Calibrating... stay quiet for 1 second.")
    levels = [rms(stream.read(BLOCK)[0][:, 0]) for _ in range(31)]
    noise = float(np.median(levels))
    threshold = max(noise * 3, 0.01)
    print(f"Noise level {noise:.4f}, speech threshold {threshold:.4f}")
    return threshold


def wait_for_utterance(stream, threshold, deadline=None, allow_notice=False):
    """Record one spoken phrase. With a deadline, gives up (returns None) if you haven't started talking by then.
    With allow_notice, returns NOTICE when a reminder is waiting and you aren't in the middle of speaking."""
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
            elif allow_notice and not notifications.empty():
                return NOTICE
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

    # "Jarvis, stop" when nothing is running
    if is_stop(text):
        resolve_pending("stop")        # also ends any open question
        speak("Standing by, sir.", stream)
        return

    # Shutdown requests, and the yes or no to "shall I close them all?", are handled here, not by the model
    answer = resolve_pending(text)
    if answer:
        speak(answer, stream, error=looks_like_error(answer))
        if QUIT.is_set():          # he has said goodbye, now actually leave
            shutdown()
        remember(text, answer)
        return

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
        speak("I can't reach my brain, sir. Is LM Studio's server running?", stream, error=True)
        return

    if cancel.is_set():            # you said stop while he was thinking: do nothing
        return

    msg = resp.choices[0].message
    if not msg.tool_calls:
        reply = msg.content or "I'm not sure how to do that, sir."
        speak(reply, stream)
        if not cancel.is_set():
            remember(text, reply)
        return

    results = []
    for call in msg.tool_calls:
        if cancel.is_set():        # stop: skip the remaining actions
            break
        result = run_tool(call.function.name, call.function.arguments)
        results.append(result)
        speak(result, stream, error=looks_like_error(result))
    if QUIT.is_set():              # he has said goodbye, now actually leave
        shutdown()
    if not cancel.is_set():
        remember(text, " ".join(results))


def run_command(command, stream, threshold):
    global active_monitor
    print("You said:", command)
    body.set_user(command)
    body.set_jarvis("")            # clear the old reply while he thinks
    cancel.clear()

    monitor = StopMonitor(stream, threshold) if BARGE_IN else None
    active_monitor = monitor
    if monitor:
        monitor.start()
    try:
        handle(command, stream)
    finally:
        active_monitor = None
        if monitor:
            monitor.stop()
        waiting = stream.read_available      # drop whatever the mic caught of his own voice
        if waiting:
            stream.read(waiting)
        if cancel.is_set():
            history.clear()
            resolve_pending("stop")          # forget any open question
            body.set_jarvis("Stopped.")
            body.flash("ignored", 1.5)


def follow_up(stream, threshold):
    """After he answers, keep listening for a few seconds. No wake word needed."""
    deadline = time.monotonic() + FOLLOWUP_SECONDS
    while True:
        if cancel.is_set():             # you stopped him: no follow-up window
            break
        body.set_state("listening")     # cyan: he's waiting for you
        audio = wait_for_utterance(stream, threshold, deadline)
        text = transcribe(audio) if audio is not None else ""
        if text:
            heard, rest = split_wake_word(text)
            command = rest if heard else text
            if command:
                run_command(command, stream, threshold)
            else:
                speak("Yes, sir?", stream)       # you only said "Jarvis"
            deadline = time.monotonic() + FOLLOWUP_SECONDS
        elif time.monotonic() >= deadline:
            break
    history.clear()                     # conversation over, next "Jarvis" starts fresh
    body.set_state("idle")


def listen_once(stream, threshold):
    """Wait for one thing you say. Acts on it if it starts with his name, otherwise ignores it.
    While waiting, he also speaks any reminder that comes due."""
    audio = wait_for_utterance(stream, threshold, allow_notice=True)
    if audio is NOTICE:
        deliver_notifications(stream)
        body.set_state("idle")
        return
    if audio is None:
        body.set_state("idle")
        return
    text = transcribe(audio)
    if not text:
        body.set_state("idle")
        return

    heard, command = split_wake_word(text)
    if not heard:
        print(f"(ignored: {text})")
        if SHOW_IGNORED:
            body.set_user(text)
            body.set_jarvis("Ignored: I didn't hear my name.")
            body.flash("ignored", 1.8)
        else:
            body.set_state("idle")
        return

    if not command:
        cancel.clear()
        speak("Yes, sir?", stream)
        audio = wait_for_utterance(stream, threshold)
        if audio is None:
            body.set_state("idle")
            return
        command = transcribe(audio)
        if not command:
            body.set_state("idle")
            return

    run_command(command, stream, threshold)
    if cancel.is_set():
        body.set_state("idle")
        return
    follow_up(stream, threshold)


def report_error(e):
    """Show the problem on the panel, print the details, and carry on listening."""
    traceback.print_exc()
    history.clear()
    body.set_jarvis(f"Error: {e}")
    body.flash("error", 5)


def mic_lost(e, attempts):
    """The microphone vanished (unplugged, Bluetooth switched mode, Windows changed the default, sleep).
    Wait a little, make Windows' device list refresh, and let the caller try again."""
    print(f"[mic] lost ({str(e).splitlines()[0][:90]}). Reconnecting, attempt {attempts}...")
    history.clear()
    body.set_state("error")
    body.set_jarvis("Microphone lost. Reconnecting...")
    try:
        sd.stop()
    except Exception:
        pass
    time.sleep(min(2 * attempts, 10))       # 2 s, 4 s, 6 s ... up to 10 s between tries
    try:
        sd._terminate()
        sd._initialize()                    # makes PortAudio look at the devices again
    except Exception as err:
        print("[mic] couldn't refresh the device list:", err)


def jarvis_loop():
    global stt, tts
    try:
        body.set_state("thinking")
        body.set_jarvis("Loading models...")
        stt = WhisperModel("base.en", device="cpu", compute_type="int8")
        tts = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")

        first_start = True
        attempts = 0
        while True:                         # outer loop: reopen the microphone whenever it drops
            try:
                with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=BLOCK) as stream:
                    body.set_jarvis("Calibrating... stay quiet for a second.")
                    threshold = calibrate(stream)
                    if first_start:
                        speak("Good evening, sir. All systems are online.", stream)
                        first_start = False
                    else:
                        speak("Microphone reconnected, sir.", stream)
                    attempts = 0
                    print("Jarvis is listening. Say 'Jarvis ...' (double-click the panel to stop him, right-click to quit)")
                    while True:
                        try:
                            listen_once(stream, threshold)
                        except sd.PortAudioError:
                            raise           # the microphone itself failed: leave, so it gets reopened
                        except Exception as e:
                            report_error(e)
                            time.sleep(1)   # avoid a tight loop if the same thing keeps failing
            except sd.PortAudioError as e:
                attempts += 1
                mic_lost(e, attempts)
    except Exception as e:
        traceback.print_exc()
        body.set_state("error")
        body.set_jarvis(f"I couldn't start, sir: {e}")


body.on_stop = request_stop                                       # double-click the panel to stop him
signal.signal(signal.SIGINT, lambda signum, frame: shutdown())    # Ctrl+C quits cleanly, like the voice command

threading.Thread(target=jarvis_loop, daemon=True).start()
body.run()   # the panel owns the main thread; right-click it to quit