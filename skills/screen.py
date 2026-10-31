"""Look at the screen and answer questions about it.

  "What's on my screen?"      "What am I looking at?"      "Summarize this page"
  "Read this message"      "Read the last message"      "What does this email say?"

Describing questions look at the whole screen. "Read ..." questions look only at the window you are using, and he
reads the message out word for word instead of summarizing it.

How it works:
  1. Takes a screenshot (kept in memory only, never saved to disk).
  2. Sends it to your local model in LM Studio. This needs a model that can see images (a "vision" model,
     for example Qwen2.5-VL, Gemma 3, or Llama 3.2 Vision). The picture never leaves your PC.
     Your normal chat model can stay as it is: set VISION_MODEL below to use a separate model just for pictures.
  3. If your model can't see images, it reads the text on the screen instead (OCR) and the model answers from that.
     This needs:  pip install rapidocr-onnxruntime

Limits: it captures your main monitor only, and it sees the screen at the moment you ask.
"""
import base64
import ctypes
import io
import re
import time

from openai import OpenAI

from skills import tool

LM_URL = "http://localhost:1234/v1"
MODEL = "local-model"            # LM Studio uses whichever model is loaded
VISION_MODEL = ""                # a separate model that can see images, used only for the picture,
                                 # for example "qwen2.5-vl-3b-instruct" (copy the exact name from LM Studio).
                                 # Leave empty to use the model that is already loaded.
MAX_SIDE = 1344                  # the screenshot is shrunk to this many pixels on its longest side
MAX_OCR_SIDE = 2000              # text reading uses a bigger picture than the model does, so small print stays readable
MAX_OCR_CHARS = 6000             # how much screen text is given to the model
MAX_READ_CHARS = 900             # the longest message he will read out loud
TIMEOUT = 90                     # seconds to wait for the model (vision can be slow on a laptop)
RETRY_VISION_AFTER = 600         # after a failure, try the picture again only after this many seconds

_client = OpenAI(base_url=LM_URL, api_key="lm-studio", timeout=TIMEOUT)
_vision_failed_at = 0.0
_ocr = None

READ_SYSTEM = ("You are Jarvis, reading something on the user's screen out loud. The text below was read from the "
               "screen, top to bottom, so it may contain menus, buttons, times and other clutter. Find the message or "
               "text the user asks for and read it back word for word. Do not summarize or explain it. Leave out "
               "menus, buttons, timestamps and names of apps. If several messages are shown and the user didn't say "
               "which, read the most recent one, which is usually the lowest. If there is nothing like that, say so "
               "in one short sentence. Start with who it is from only if that is clearly shown. Address the user "
               "as 'sir' only if you add words of your own.")

SYSTEM = ("You are Jarvis, answering out loud. Look at what is on the user's screen and answer their question. "
          "Reply in one or two short spoken sentences. No lists, markdown, or emojis. "
          "Say plainly what you can see. If you can't tell, say so. Never invent text that isn't visible. "
          "Address the user as 'sir'.")


# ------------------------------------------------------------------ capture

def active_window_title():
    """The title of the window you are using, so the model knows which app is in front."""
    try:
        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        return buffer.value.strip()
    except Exception:
        return ""


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def window_box():
    """Where the window you are using sits on screen: (left, top, right, bottom), or None."""
    try:
        user32 = ctypes.windll.user32
        rect = _Rect()
        if not user32.GetWindowRect(user32.GetForegroundWindow(), ctypes.byref(rect)):
            return None
        box = (rect.left, rect.top, rect.right, rect.bottom)
        return box if box[2] - box[0] >= 200 and box[3] - box[1] >= 150 else None   # ignore tiny or hidden windows
    except Exception:
        return None


def grab(window=False):
    """A PIL image of the main screen, or of just the window you are using."""
    from PIL import ImageGrab
    if window:
        box = window_box()
        if box:
            return ImageGrab.grab(bbox=box, all_screens=True)
    return ImageGrab.grab()


_READ_WORDS = re.compile(r"\b(read|reads|say|says|said)\b", re.IGNORECASE)
_WINDOW_WORDS = re.compile(r"\b(message|messages|email|emails|mail|chat|dm|text|notification|notifications|"
                           r"window|this page|this tab|this app|this error|this one|this)\b", re.IGNORECASE)


def wants_reading(question):
    """'Read this message', 'what does this email say' -> read it word for word."""
    return bool(_READ_WORDS.search(question)) and bool(_WINDOW_WORDS.search(question)) \
        and "screen" not in question.lower()


def wants_window(question):
    q = question.lower()
    return "screen" not in q and bool(_WINDOW_WORDS.search(q))


def _shrink(image, limit=None):
    image = image.convert("RGB")
    limit = limit or MAX_SIDE
    longest = max(image.size)
    if longest > limit:
        scale = limit / longest
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    return image


def _as_data_url(image):
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


# ------------------------------------------------------------------ reading text (fallback)

def read_text(image):
    """All the text on the screen, top to bottom, using RapidOCR. Returns '' if OCR isn't installed."""
    global _ocr
    try:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR()
        import numpy as np
        result, _ = _ocr(np.array(image))
    except ImportError:
        return ""
    except Exception as e:
        print("[screen] OCR failed:", e)
        return ""
    if not result:
        return ""
    lines = sorted(result, key=lambda item: (round(item[0][0][1] / 12), item[0][0][0]))   # top to bottom, left to right
    return "\n".join(item[1] for item in lines)[:MAX_OCR_CHARS]


# ------------------------------------------------------------------ asking the model

def _ask(content, max_tokens=220, model=None, system=None):
    reply = _client.chat.completions.create(
        model=model or MODEL,
        messages=[{"role": "system", "content": system or SYSTEM}, {"role": "user", "content": content}],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return (reply.choices[0].message.content or "").strip()


def _clean(text):
    """Spoken answers: no markdown, no leftover thinking tags."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"[*_`#>]+", "", text)
    return " ".join(text.split())


def look_at_screen(question=""):
    global _vision_failed_at
    question = (question or "").strip() or "What is on my screen?"
    title = active_window_title()
    context = f"The window in front is titled: {title}.\n" if title else ""

    reading = wants_reading(question)
    try:
        photo = grab(window=wants_window(question))
        image = _shrink(photo)
    except Exception as e:
        print("[screen] couldn't take a screenshot:", e)
        return "I couldn't capture the screen, sir."

    # 1. Let the model look at the picture
    if time.monotonic() - _vision_failed_at > RETRY_VISION_AFTER or not _vision_failed_at:
        try:
            answer = _ask([
                {"type": "text", "text": f"{context}The user asks: {question}"},
                {"type": "image_url", "image_url": {"url": _as_data_url(image)}},
            ], max_tokens=420 if reading else 220, model=VISION_MODEL or MODEL,
                system=READ_SYSTEM.replace("The text below was read from the screen, top to bottom, so it may "
                                           "contain", "The picture may contain") if reading else None)
            answer = _clean(answer)
            if answer:
                return answer[:MAX_READ_CHARS] if reading else answer
        except Exception as e:
            _vision_failed_at = time.monotonic()
            print(f"[screen] the model couldn't look at the picture ({str(e)[:140]}). Reading the text instead.")

    # 2. Read the text on the screen, and answer from that
    text = read_text(_shrink(photo, MAX_OCR_SIDE))
    if not text:
        print("[screen] to read screens without a vision model, run:  pip install rapidocr-onnxruntime")
        return ("My current model can't see images, sir, and text reading isn't installed. "
                "The terminal has the install command.")
    try:
        answer = _clean(_ask(f"{context}Here is the text on the screen, top to bottom:\n{text}\n\n"
                             f"The user asks: {question}",
                             max_tokens=420 if reading else 220, system=READ_SYSTEM if reading else None))
        if reading:
            answer = answer[:MAX_READ_CHARS]
        return answer or "I couldn't make sense of the screen, sir."
    except Exception as e:
        print("[screen] the model didn't answer:", e)
        return "My language model didn't answer, sir."


HINT = ("Screen: when the user asks what is on their screen, what they are looking at, to read a message, email "
        "or chat they are looking at, or what an error on the screen means, use look_at_screen and pass their "
        "question exactly as they said it. "
        "Use the clipboard tool instead only when they say something is copied.")

SCHEMAS = [
    tool("look_at_screen",
         "Look at the user's screen right now and answer a question about it, such as what is on it, what an error "
         "says, or what the page they are on is about.",
         {"question": "What the user wants to know about the screen, in their own words"},
         optional=("question",)),
]

TOOLS = {
    "look_at_screen": look_at_screen,
}