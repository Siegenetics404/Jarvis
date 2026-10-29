"""Short-term memory for Jarvis: what he just did, so "it", "that" and "close it" make sense.

Two jobs:
1. context_block()     - a few lines added to the model's instructions listing the recent actions,
                         so the model understands "skip it", "that tab", "the song you just played".
2. resolve_followup()  - plain code (no model) for the most common follow-up, "close it" / "undo that",
                         which closes whatever Jarvis opened last. Small models get this wrong, code doesn't.

It lives in memory only and is forgotten when Jarvis restarts. It is NOT cleared by "stop" or when the
follow-up window closes, so you can say "Jarvis, close it" a minute later.
"""
import json
import re
import time
from urllib.parse import urlparse

MEMORY_SECONDS = 15 * 60      # how long an action stays "recent"
MAX_ACTIONS = 8               # how many actions are kept
SHOWN_ACTIONS = 5             # how many are shown to the model

# Tools that don't change what "it" means, so they are not remembered
FORGET_TOOLS = {"get_time", "count_tabs", "quit_jarvis", "refresh_tab"}

# Reply fragments that mean nothing was actually opened
FAILED_HINTS = ("what should i play", "couldn't", "which tabs", "unknown tool", "failed",
                "can't reach", "isn't running", "can't tell")

_actions = []
_target = None                # set by a tool while it runs, e.g. play_youtube knows the exact video

# Words that carry no meaning in "oh wait no, forget it, close that one"
_CLOSE_WORDS = {"close", "undo"}
_FILLER = {"oh", "wait", "no", "nope", "actually", "hmm", "um", "uh", "forget", "it", "never", "mind",
           "nevermind", "sorry", "please", "sir", "just", "and", "then", "that", "this", "the", "one",
           "tab", "video", "song", "music", "page", "window", "thing", "now", "you", "opened",
           "played", "playing", "again", "can", "could", "would", "jarvis", "i", "mean", "so", "okay",
           "ok", "yes", "yeah", "also", "too", "anyway", "last", "previous", "just", "now"}

# Words that don't help find a video's tab
_NOISE = {"music", "song", "songs", "video", "videos", "official", "lyrics", "audio", "some", "play",
          "playlist", "mix", "please", "jarvis"}


def set_target(kind, label, match):
    """Called by a tool while it runs: 'if the user says close it, close the tab matching `match`'."""
    global _target
    _target = {"kind": kind, "label": label, "match": match}


def _clean(text):
    words = [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _NOISE]
    return " ".join(words) or (text or "").lower().strip()


def _closer_for(tool, args, result):
    """How to undo this action: {'tool': ..., 'args': ..., 'label': ...} or None."""
    lowered = (result or "").lower()
    if any(hint in lowered for hint in FAILED_HINTS):
        return None
    if _target:
        return {"tool": "close_tabs", "args": {"match": _target["match"]}, "label": _target["label"]}
    if tool == "play_youtube" and args.get("query"):
        return {"tool": "close_tabs", "args": {"match": _clean(args["query"])}, "label": args["query"]}
    if tool == "open_website" and args.get("url"):
        host = (urlparse(args["url"]).netloc or args["url"]).lower().removeprefix("www.")
        return {"tool": "close_tabs", "args": {"match": host}, "label": host}
    if tool == "search_web" and args.get("query"):
        return {"tool": "close_tabs", "args": {"match": _clean(args["query"])}, "label": args["query"]}
    if tool == "open_app" and args.get("name"):
        return {"tool": "close_app", "args": {"name": args["name"]}, "label": args["name"]}
    return None


def record(utterance, tool, arguments_json, result):
    """Call this after every tool Jarvis runs."""
    global _target
    try:
        args = json.loads(arguments_json or "{}")
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    try:
        if tool in FORGET_TOOLS:
            return
        _actions.append({
            "time": time.monotonic(),
            "said": utterance or "",
            "tool": tool,
            "args": args,
            "result": result or "",
            "close": _closer_for(tool, args, result),
        })
        del _actions[:-MAX_ACTIONS]
    finally:
        _target = None


def _fresh():
    now = time.monotonic()
    return [a for a in _actions if now - a["time"] <= MEMORY_SECONDS]


def last_action():
    """The newest remembered action (a dict with tool, args, result, time), or None."""
    recent = _fresh()
    return recent[-1] if recent else None


def clear():
    _actions.clear()


def context_block():
    """Lines for the model's instructions. Empty when nothing recent happened."""
    recent = _fresh()[-SHOWN_ACTIONS:]
    if not recent:
        return ""
    now = time.monotonic()
    lines = []
    for a in recent:
        minutes = int((now - a["time"]) // 60)
        when = "just now" if minutes < 1 else f"{minutes} min ago"
        shown = ", ".join(f"{k}={str(v)[:40]!r}" for k, v in a["args"].items())
        lines.append(f"- {when}: {a['tool']}({shown}) -> {a['result'][:100]}")
    text = ("\nRecent actions, oldest first. When the user says it, that, this, the song, the tab, "
            "or the one you just opened, they mean the latest of these:\n" + "\n".join(lines) + "\n")
    if recent[-1]["tool"] == "play_youtube":
        text += ("A YouTube video is probably still playing. If the user says skip, next, back, faster, "
                 "or skip the ad without naming anything, they mean that video, so use the YouTube control tool.\n")
    return text


def resolve_followup(text):
    """'close it', 'oh wait no forget it, close that', 'undo that'.
    Returns {'tool', 'args', 'label'} to run directly, or None to let the model handle the sentence.
    Only the LATEST action counts, so a second 'close it' doesn't reach back to something older."""
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not words or len(words) > 10:
        return None
    if not any(w in _CLOSE_WORDS for w in words):
        return None
    if not all(w in _CLOSE_WORDS or w in _FILLER for w in words):
        return None                       # e.g. "close youtube" names something, so the model handles it
    recent = _fresh()
    if not recent or not recent[-1]["close"]:
        return None
    return recent[-1]["close"]