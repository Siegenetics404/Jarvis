"""Safety net for risky actions, and for commands he may have misheard.

Before a risky tool runs, he asks first and waits for your answer. This is plain code, so the language model
can't talk its way past it.

  HIGH risk    (power_action: shut down, restart, sleep, sign out)
               Always asks, and a bare "yes" is NOT enough: you must say "confirm" (or "do it" / "go ahead").
               A TV or a stray "yes" in the room can't switch your PC off.
  MEDIUM risk  (closing a program that may hold unsaved work, like Word or Chrome)
               Asks, and "yes" is enough.
  UNSURE       (closing apps or tabs when Whisper wasn't confident it heard you correctly)
               Asks "I'm not sure I heard that right".
"""
import json
import re
import time

CONFIRM_SECONDS = 20          # how long he waits for your answer
LOW_CONFIDENCE = -0.85        # Whisper's average log-probability. Lower than this = "I may have misheard".
                              # The terminal prints the number for every command, so you can tune this.

# Closing these asks first, because they may have unsaved work or many open tabs.
# It matches any part of the name, so "code" covers "vscode" and "visual studio code".
CONFIRM_CLOSE = ("word", "excel", "powerpoint", "notepad", "photoshop", "premiere", "illustrator",
                 "figma", "code", "visual studio", "android studio", "chrome", "browser", "edge", "firefox")

# When Whisper isn't confident, these tools ask first too
UNSURE_TOOLS = {"close_app", "close_tabs", "power_action"}

POWER_PHRASES = {
    "shutdown": "Shut down the PC",
    "restart": "Restart the PC",
    "sleep": "Put the PC to sleep",
    "sign_out": "Sign out of Windows",
}

_YES = {"yes", "yeah", "yep", "yup", "sure", "confirm", "confirmed", "proceed", "okay", "ok", "please"}
_STRONG = {"confirm", "confirmed", "proceed"}                 # alone these are enough, even for HIGH risk
_NO = {"no", "nope", "cancel", "stop", "nevermind", "don't", "dont", "never", "abort", "negative"}
_GLUE = {"sir", "jarvis", "do", "it", "go", "ahead", "and", "i", "the", "that", "yes", "please", "now"}

approved = False              # True only while a confirmed tool is running. Risky tools check this too.
_pending = None


def waiting():
    """True while he is waiting for your yes or no."""
    return bool(_pending) and time.monotonic() - _pending["time"] <= CONFIRM_SECONDS


def _short_app(name):
    return (name or "that program").strip()[:40]


def review(name, arguments_json, utterance, confidence):
    """Call before running a tool. Returns a question to ask you (and remembers the tool), or None to go ahead."""
    global _pending
    try:
        args = json.loads(arguments_json or "{}")
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        args = {}

    level, question = None, ""
    unsure = confidence is not None and confidence < LOW_CONFIDENCE and name in UNSURE_TOOLS

    if name == "power_action":
        action = str(args.get("action", "")).strip().lower().replace(" ", "_").replace("-", "_")
        if action in {"shut_down", "poweroff", "power_off"}:
            action = "shutdown"
        if action in {"reboot"}:
            action = "restart"
        if action in {"signout", "log_out", "logout", "log_off", "logoff"}:
            action = "sign_out"
        phrase = POWER_PHRASES.get(action)
        if not phrase:
            return None                      # the tool itself will say it doesn't know that one
        level = "high"
        question = f"{phrase}, sir? Say confirm to go ahead, or anything else to cancel."
    elif name == "close_app":
        app = _short_app(args.get("name"))
        if any(k in app.lower() for k in CONFIRM_CLOSE):
            level = "medium"
            question = f"Close {app}, sir? Anything unsaved could be lost."
        elif unsure:
            level = "medium"
            question = f"Close {app}, sir?"
    elif name == "close_tabs" and unsure:
        level = "medium"
        question = f"Close the tabs matching {str(args.get('match', 'that')).strip()[:40]}, sir?"
    elif unsure:
        level = "medium"
        question = "Shall I go ahead with that, sir?"

    if not level:
        return None
    if unsure and name != "power_action":
        question = "I may have misheard you. " + question
    _pending = {"name": name, "args_json": arguments_json or "{}", "text": utterance, "level": level,
                "time": time.monotonic(), "reminded": False}
    return question


def resolve_confirmation(text, run_tool):
    """Your answer to a question from review(). Returns the reply to speak, or None when the sentence
    isn't an answer (then it is handled as a normal command)."""
    global _pending, approved
    pending = _pending
    _pending = None                                           # any sentence ends the question
    if not pending or time.monotonic() - pending["time"] > CONFIRM_SECONDS:
        return None
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not words or len(words) > 6:
        return None
    wset = set(words)

    if wset & _NO:
        return "Cancelled, sir."

    if not all(w in _YES or w in _GLUE for w in words):
        return None                                           # a different command, not an answer
    confirmed = bool(wset & _STRONG) or ("do" in wset and "it" in wset) or ("go" in wset and "ahead" in wset)
    if pending["level"] == "medium":
        confirmed = confirmed or bool(wset & _YES)

    if not confirmed:
        if pending["level"] == "high" and not pending["reminded"]:
            pending["reminded"] = True                        # a bare "yes" isn't enough for this one
            _pending = pending
            return "Just to be certain, sir, say confirm."
        return "Cancelled, sir."

    approved = True
    try:
        return run_tool(pending["name"], pending["args_json"], pending["text"])
    finally:
        approved = False


def cancel():
    """Forget the open question (you said stop)."""
    global _pending
    _pending = None