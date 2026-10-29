"""Jarvis learns the names you say, so you only have to correct him once.

He can't read your mind, so he learns from things he can actually see:

  1. He can't find "odo rico". He compares how it SOUNDS with the apps and projects that really exist on your
     PC (tuned for common Filipino-English swaps like P/F, B/V, E/I, O/U), and asks: "Did you mean Odoriko?"
     You say yes, he opens it, and he remembers: next time "odo rico" is Odoriko, with no question.

  2. He opens the wrong thing, or can't find it, and you say "no, I meant Odoriko" or "I said Spotify".
     He fixes it and remembers that too.

  3. He mishears a command ("watch the music" instead of "pause the music"), you say stop, then say it again
     correctly. Once the second try works he asks "Shall I remember that 'watch the music' means
     'pause the music'?" and a yes adds it to data/corrections.txt.

What he learns is saved in data/learned_names.json. Open it to see or delete anything he learned.
It only learns app and project names, because for those he can check that your answer really exists.
Every name that works is also added to data/vocabulary.txt (see vocabulary.py).
"""
import difflib
import json
import os
import re
import time

import session_memory
from vocabulary import add_auto, sound_key

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "learned_names.json")

NAME_TOOLS = {"open_app": "apps", "close_app": "apps", "open_project": "projects"}
SUCCESS_PREFIXES = ("opening", "closed", "quit", "force closed")      # only these count as "it worked"

SUGGEST_AT = 0.72              # how alike it must sound before he asks "did you mean...?"
SUGGEST_GAP = 0.05             # and it must beat the second best by this much, or he stays quiet
CORRECTION_AT = 0.60           # when YOU say "I meant X", X only has to be roughly like a real name
ASK_SECONDS = 20
CORRECTION_WINDOW = 300        # "I meant X" only fixes something he did in the last 5 minutes

_YES = {"yes", "yeah", "yep", "yup", "sure", "correct", "right", "okay", "ok"}
_NO = {"no", "nope", "wrong", "nevermind", "cancel", "not"}
_GLUE = {"that", "one", "please", "sir", "jarvis", "do", "it", "thats", "that's", "is"}
_NAME_FILLER = {"the", "app", "application", "project", "please", "sir", "called", "named", "to",
                "open", "close", "a", "program"}

_pending = None
_repair = None                 # a "shall I remember that X means Y?" question waiting for your answer
_last = None                   # the command he ran most recently: {"text", "time"}
_prev = None                   # the one before it
_complaint = 0.0               # when you last said stop
_save_correction = None        # set by jarvis.py: writes a line to data/corrections.txt
REPAIR_WINDOW = 90             # the first command must be this recent, in seconds
COMPLAINT_WINDOW = 60          # and you must have said stop within this long before the second one
_rejected = set()              # (heard, suggestion) pairs you said no to, so he doesn't ask again this session


# ------------------------------------------------------------------ memory file

def _load():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {"apps": dict(data.get("apps", {})), "projects": dict(data.get("projects", {}))}
    except (OSError, ValueError, AttributeError):
        return {"apps": {}, "projects": {}}


_learned = _load()


def _save():
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_learned, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, DATA_FILE)
    except OSError as e:
        print("[learning] couldn't save:", e)


# ------------------------------------------------------------------ how alike two names sound

def _plain(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _alike(heard, name):
    a, b = _plain(heard), _plain(name)
    if not a or not b:
        return 0.0
    return max(difflib.SequenceMatcher(None, a, b).ratio(),
               difflib.SequenceMatcher(None, sound_key(heard), sound_key(name)).ratio())


def score(heard, name):
    """0 to 1: how likely 'heard' is a mishearing of 'name'. Also tries each word of a long name."""
    best = _alike(heard, name)
    for word in re.findall(r"[a-z0-9]+", name.lower()):
        if len(word) >= 4:
            best = max(best, _alike(heard, word) * 0.97)
    return best


def ranked(heard, names):
    return sorted(((score(heard, n), n) for n in names), reverse=True)


def _candidates(kind):
    try:
        import actions
        return list(actions.find_apps() if kind == "apps" else actions.find_projects())
    except Exception as e:
        print("[learning] couldn't list names:", e)
        return []


# ------------------------------------------------------------------ used by jarvis.py

def rewrite(tool, arguments_json):
    """Turns a name you said before into the name that worked: 'odo rico' -> 'odoriko'."""
    kind = NAME_TOOLS.get(tool)
    if not kind:
        return arguments_json
    try:
        args = json.loads(arguments_json or "{}")
    except ValueError:
        return arguments_json
    if not isinstance(args, dict):
        return arguments_json
    heard = " ".join(re.findall(r"[a-z0-9]+", str(args.get("name", "")).lower()))
    known = _learned[kind].get(heard)
    if known:
        args["name"] = known
        return json.dumps(args)
    return arguments_json


def waiting():
    now = time.monotonic()
    return (bool(_pending) and now - _pending["time"] <= ASK_SECONDS) or \
           (bool(_repair) and now - _repair["time"] <= ASK_SECONDS)


def cancel():
    global _pending, _repair
    _pending = None
    _repair = None


def set_saver(function):
    """jarvis.py gives this a function that writes one fix into data/corrections.txt."""
    global _save_correction
    _save_correction = function


def note_command(text):
    """Call when he is about to run a command, so a later 'say it again' can be compared with it."""
    global _last, _prev
    _prev, _last = _last, {"text": text, "time": time.monotonic()}


def note_complaint():
    """Call when you say stop (or he is cut off): the last thing he did was probably not what you wanted."""
    global _complaint
    _complaint = time.monotonic()


def repair_pair(first, second):
    """If 'second' is 'first' with one short stretch of words swapped, return (what he heard, what you meant),
    each with a couple of neighbouring words so the fix can't hit unrelated sentences. Otherwise None."""
    a = re.findall(r"[a-z0-9']+", first.lower())
    b = re.findall(r"[a-z0-9']+", second.lower())
    if not a or not b or a == b:
        return None
    ops = [op for op in difflib.SequenceMatcher(None, a, b).get_opcodes() if op[0] != "equal"]
    if len(ops) != 1 or ops[0][0] != "replace":
        return None
    _, i1, i2, j1, j2 = ops[0]
    if i2 - i1 > 2 or j2 - j1 > 2:
        return None
    shared = len(a) - (i2 - i1)
    if shared < 2:
        return None                                       # too different to be the same sentence said again
    after = min(2, len(a) - i2)
    if after:
        return " ".join(a[i1:i2 + after]), " ".join(b[j1:j2 + after])
    before = min(1, i1)
    return " ".join(a[i1 - before:i2]), " ".join(b[j1 - before:j2])


def offer_repair(text, worked):
    """Call after a command ran. If you had said stop and this is the same sentence said right, he asks whether
    to remember the difference. Returns the question to speak, or None."""
    global _repair, _complaint
    now = time.monotonic()
    first = _prev
    complained = now - _complaint <= COMPLAINT_WINDOW
    _complaint = 0.0
    if not worked or not complained or not first or now - first["time"] > REPAIR_WINDOW:
        return None
    pair = repair_pair(first["text"], text)
    if not pair or _save_correction is None:
        return None
    if (pair[0], pair[1]) in _rejected:
        return None
    _repair = {"wrong": pair[0], "right": pair[1], "time": now}
    return f"Shall I remember that '{pair[0]}' means '{pair[1]}', sir?"


def _resolve_repair(text):
    """Your answer to 'Shall I remember...?' Returns what to say, or None if it wasn't an answer."""
    global _repair
    repair, _repair = _repair, None
    if not repair or time.monotonic() - repair["time"] > ASK_SECONDS:
        return None
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not words or len(words) > 6:
        return None
    wset = set(words)
    if wset & _NO:
        _rejected.add((repair["wrong"], repair["right"]))
        return "Very well, sir. I won't."
    if wset & _YES and all(w in _YES or w in _GLUE for w in words):
        _save_correction(repair["wrong"], repair["right"])
        print(f"[learning] '{repair['wrong']}' now means '{repair['right']}'")
        return "Noted, sir."
    return None


def _heard_name(arguments_json):
    try:
        args = json.loads(arguments_json or "{}")
    except ValueError:
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", str(args.get("name", "")).lower())) if isinstance(args, dict) else ""


_SUCCESS = re.compile(r"^(?:opening project|opening|force closed|closed|quit)\s+(.+?)\.?$", re.IGNORECASE)


def note_success(tool, result):
    """A name worked, so add it to data/vocabulary.txt for Whisper to learn."""
    if tool in NAME_TOOLS:
        match = _SUCCESS.match((result or "").strip())
        if match:
            add_auto(match.group(1))


def after_tool(tool, arguments_json, result):
    """Call after a tool ran. Remembers names that worked. If it couldn't find the name,
    returns a 'Did you mean...?' question (or None)."""
    global _pending
    kind = NAME_TOOLS.get(tool)
    note_success(tool, result)
    lowered = (result or "").lower()
    if not kind or not lowered.startswith("couldn't find"):
        return None
    heard = _heard_name(arguments_json)
    if not heard:
        return None
    top = [(s, n) for s, n in ranked(heard, _candidates(kind)) if (heard, n) not in _rejected][:2]
    if not top or top[0][0] < SUGGEST_AT:
        return None
    if len(top) > 1 and top[0][0] - top[1][0] < SUGGEST_GAP:
        return None                                   # two names sound equally likely: don't guess
    guess = top[0][1]
    _pending = {"tool": tool, "kind": kind, "heard": heard, "guess": guess, "time": time.monotonic()}
    return f"I couldn't find {heard}, sir. Did you mean {guess}?"


def _learn(kind, heard, name):
    heard = " ".join(re.findall(r"[a-z0-9]+", heard.lower()))
    if not heard or _plain(heard) == _plain(name) or _learned[kind].get(heard) == name:
        return
    _learned[kind][heard] = name
    _save()
    print(f"[learning] '{heard}' now means '{name}'")


def _run_and_learn(run, tool, kind, heard, name, text):
    reply, asked = run(tool, json.dumps({"name": name}), text)
    if not asked and (reply or "").lower().startswith(SUCCESS_PREFIXES):
        _learn(kind, heard, name)
        note_success(tool, reply)
    return reply


def resolve_answer(text, run):
    """Your yes or no to 'Did you mean...?'. Returns what to say, or None if it wasn't an answer."""
    global _pending
    if _repair:
        return _resolve_repair(text)
    pending, _pending = _pending, None
    if not pending or time.monotonic() - pending["time"] > ASK_SECONDS:
        return None
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not words or len(words) > 6:
        return None
    wset = set(words)
    if wset & _NO:
        _rejected.add((pending["heard"], pending["guess"]))
        return "Very well, sir."
    if wset & _YES and all(w in _YES or w in _GLUE for w in words):
        return _run_and_learn(run, pending["tool"], pending["kind"], pending["heard"], pending["guess"], text)
    return None


_FIX = re.compile(r"^(?:(?:no|wrong|not that|nope)\W+)?(?:i\s+(?:said|meant|mean|want|wanted)|it\s*(?:is|s)|that\s*(?:is|s))\s+(.+)$")


def resolve_correction(text, run):
    """'No, I meant Odoriko' / 'I said Spotify': fixes the last app or project he handled, and remembers it."""
    cleaned = re.sub(r"[^a-z0-9' ]", " ", (text or "").lower().replace("'", ""))
    cleaned = " ".join(cleaned.split())
    words = cleaned.split()
    if not words or len(words) > 10:
        return None
    starts_like_fix = words[0] in {"no", "wrong", "nope", "not"} or cleaned.startswith("i said") \
        or cleaned.startswith("i meant") or cleaned.startswith("i mean")
    if not starts_like_fix:
        return None
    match = _FIX.match(cleaned)
    if not match:
        return None
    explicit = bool(re.match(r"^(?:(?:no|wrong|nope|not that)\W+)?i\s+(?:said|meant|mean)\b", cleaned))

    last = session_memory.last_action()
    if not last or last["tool"] not in NAME_TOOLS or time.monotonic() - last["time"] > CORRECTION_WINDOW:
        return None
    kind = NAME_TOOLS[last["tool"]]

    meant = match.group(1).split(" not ")[0]
    meant = " ".join(w for w in meant.split() if w not in _NAME_FILLER)
    if not meant:
        return None
    heard = " ".join(re.findall(r"[a-z0-9]+", str(last["args"].get("name", "")).lower()))

    names = _candidates(kind)
    top = ranked(meant, names)[:1]
    if not top or top[0][0] < CORRECTION_AT:
        return f"I don't have anything called {meant}, sir." if explicit else None   # "no, it's fine" isn't a name
    return _run_and_learn(run, last["tool"], kind, heard, top[0][1], text)