"""Clipboard helper: explain, summarize, translate, fix or read aloud whatever you just copied.
The answer comes from your local model in LM Studio. Windows only, no extra packages."""
import ctypes
import re
import time
from ctypes import wintypes

from openai import OpenAI

from skills import tool

LM_URL = "http://localhost:1234/v1"     # LM Studio's local server
MAX_CHARS = 6000                        # how much of the clipboard is sent to the model
MAX_SPOKEN = 420                        # longest thing he reads out loud

_client = OpenAI(base_url=LM_URL, api_key="lm-studio", timeout=90)

# ------------------------------------------------------------ clipboard access

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL


def _open_clipboard():
    for _ in range(10):                  # another program may be holding it for a moment
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def _read_clipboard():
    """The copied text, '' if there is none (for example an image), or None if the clipboard is locked."""
    if not _open_clipboard():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _write_clipboard(text):
    data = (text + "\0").encode("utf-16-le")
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(CF_UNICODETEXT, handle)      # the clipboard owns that memory now
        return True
    finally:
        user32.CloseClipboard()


# ------------------------------------------------------------------ the model

def _ask(system, user, max_tokens=400):
    reply = _client.chat.completions.create(
        model="local-model",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3,
        max_tokens=max_tokens,
    )
    text = reply.choices[0].message.content or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()      # some models show their reasoning


def _strip_fences(text):
    text = re.sub(r"^\s*```[a-zA-Z0-9_+-]*\s*\n", "", text)
    return re.sub(r"\n\s*```\s*$", "", text).strip()


def _spoken(text):
    """Make text fit for the voice: plain, short, no markdown, no symbols he can't pronounce."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"[`*#>]", "", text)
    text = text.encode("ascii", "ignore").decode()
    text = " ".join(text.split())
    if len(text) > MAX_SPOKEN:
        cut = text[:MAX_SPOKEN]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        text = cut[:end + 1] if end > 80 else cut.rstrip() + "..."
    return text


EXPLAIN = ("You help a developer. The user copied some text. Explain what it means in plain spoken English, "
           "in at most 3 short sentences, with no lists, no markdown and no code. "
           "If it is an error message, say what it means and the most likely fix.")
SUMMARIZE = "Summarize the text in at most 2 short sentences of plain spoken English, with no lists or markdown."
REPLY = ("Write a short, polite, natural reply to the message the user copied. "
         "Output only the reply text, with no explanation.")
FIX = ("The user copied code, a command or text that has a mistake. Reply in exactly this format: "
       "a first line starting with 'WHAT: ' and one short plain sentence about what was wrong, "
       "then a line containing only '---', then ONLY the corrected version, "
       "with no markdown fences and no commentary.")


# ------------------------------------------------------------------- the tool

def clipboard_helper(task, detail=""):
    task = (task or "explain").strip().lower()
    detail = (detail or "").strip()

    text = _read_clipboard()
    if text is None:
        return "I couldn't open the clipboard, sir. Another program is holding it."
    text = text.strip()
    if not text:
        return "Your clipboard has no text, sir. Copy something first."
    clipped = text[:MAX_CHARS]
    extra = f"Extra instruction from the user: {detail}\n\n" if detail else ""

    if task == "read":
        return _spoken(text) or "There's nothing I can read out loud in that, sir."

    try:
        if task == "explain":
            return _spoken(_ask(EXPLAIN, f"{extra}Text:\n{clipped}")) or "I'm not sure what that means, sir."

        if task == "summarize":
            return _spoken(_ask(SUMMARIZE, clipped)) or "I couldn't make a summary of that, sir."

        if task == "translate":
            language = detail or "English"
            result = _strip_fences(_ask(f"Translate the text into {language}. Output only the translation, nothing else.",
                                        clipped, max_tokens=1200))
            if not result or not _write_clipboard(result):
                return "I couldn't produce a translation, sir."
            return f"Translated to {_spoken(language)}, sir. It's on your clipboard."

        if task == "reply":
            result = _strip_fences(_ask(REPLY + (f" Tone: {detail}." if detail else ""), clipped, max_tokens=600))
            if not result or not _write_clipboard(result):
                return "I couldn't draft a reply, sir."
            return "A draft reply is on your clipboard, sir."

        if task == "fix":
            raw = _ask(FIX, f"{extra}{clipped}", max_tokens=1500)
            match = re.search(r"WHAT:\s*(.+?)\s*\n\s*-{3,}\s*\n(.*)", raw, flags=re.S)
            what = _spoken(match.group(1)) if match else ""
            fixed = _strip_fences(match.group(2).strip() if match else raw)
            if not fixed or not _write_clipboard(fixed):
                return "I couldn't work out a fix, sir."
            return (what + " " if what else "") + "The corrected version is on your clipboard, sir."
    except Exception as e:
        print("[clipboard] model error:", e)
        return "I couldn't reach my brain for that, sir. Is LM Studio's server running?"

    return "I don't know that clipboard task, sir."


# ---------------------------------------------------------------- registration

HINT = ("Clipboard: when the user says 'this' or 'what I copied' and asks to explain, summarize, translate, "
        "fix, reply to or read it, use clipboard_helper. For translate, put the language in detail.")

SCHEMAS = [
    tool("clipboard_helper",
         "Work with the text the user just copied (the clipboard): explain it, for example an error message, "
         "summarize it, translate it, fix the code in it, draft a reply to it, or read it aloud. "
         "Use this when the user says 'this', 'what I copied' or 'the clipboard'.",
         {"task": "explain, summarize, translate, fix, reply or read",
          "detail": "Optional. For translate: the target language. Otherwise any extra instruction, like 'keep it formal'."},
         optional=("detail",),
         enums={"task": ["explain", "summarize", "translate", "fix", "reply", "read"]}),
]

TOOLS = {
    "clipboard_helper": clipboard_helper,
}