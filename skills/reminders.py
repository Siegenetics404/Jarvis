"""Notes and reminders. They are stored as plain text files in the data folder (which is git-ignored),
so they survive restarts. When a reminder comes due, Jarvis speaks it as soon as he is idle."""
import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from skills import tool, notifications

DATA = Path(__file__).resolve().parent.parent / "data"
NOTES_FILE = DATA / "notes.json"
REMINDERS_FILE = DATA / "reminders.json"
MAX_MINUTES = 7 * 24 * 60          # reminders can be up to a week away
LATE_AFTER = 120                   # a reminder this many seconds late is announced with its original time

_lock = threading.Lock()
_last_fired = None                 # message of the most recent reminder, for "snooze"

_FILLER = {"the", "a", "an", "my", "note", "notes", "about", "what", "did", "i", "say", "said",
           "reminder", "reminders", "that", "to", "me", "of", "for", "on", "is", "was"}


# ----------------------------------------------------------------- storage

def _load(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(path, items):
    DATA.mkdir(exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


_notes = _load(NOTES_FILE)
_reminders = _load(REMINDERS_FILE)


# ----------------------------------------------------------------- helpers

def _norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _FILLER]


def _spoken(text, limit=240):
    """Plain text the voice can say: no symbols it can't pronounce, and not too long."""
    text = " ".join(text.encode("ascii", "ignore").decode().split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _clock(ts):
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _duration(minutes):
    if minutes < 1:
        return _plural(max(1, round(minutes * 60)), "second")
    if minutes < 60:
        return _plural(round(minutes), "minute")
    hours, rest = divmod(round(minutes), 60)
    text = _plural(hours, "hour")
    return text + (f" {_plural(rest, 'minute')}" if rest else "")


def _when(due):
    delta = due - time.time()
    if delta < 3600:
        return "in " + _duration(max(delta, 1) / 60)
    when = datetime.fromtimestamp(due)
    today = datetime.now().date()
    day = ""
    if when.date() == today + timedelta(days=1):
        day = " tomorrow"
    elif when.date() != today:
        day = f" on {when:%A}"
    return f"at {_clock(due)}{day}"


def _day(ts):
    then = datetime.fromtimestamp(ts).date()
    today = datetime.now().date()
    if then == today:
        return "Today"
    if then == today - timedelta(days=1):
        return "Yesterday"
    return f"On {datetime.fromtimestamp(ts):%A}"


def _number(text):
    found = re.search(r"\d+(?:\.\d+)?", text or "")
    return float(found.group()) if found else None


def _due_from_clock(text):
    """'15:30', '3:30 pm', 'noon' -> the next time that clock time happens, as a timestamp."""
    text = (text or "").lower()
    if "noon" in text:
        hour, minute = 12, 0
    elif "midnight" in text:
        hour, minute = 0, 0
    else:
        found = re.search(r"(\d{1,2})(?:[:.](\d{2}))?\s*(?:([ap])\.?m\.?)?", text)
        if not found:
            return None
        hour, minute = int(found.group(1)), int(found.group(2) or 0)
        meridiem = found.group(3)
        if meridiem == "p" and hour < 12:
            hour += 12
        elif meridiem == "a" and hour == 12:
            hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    due = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= datetime.now():
        due += timedelta(days=1)
    return due.timestamp()


def _clean_message(text):
    text = (text or "").strip().strip(".")
    return re.sub(r"^(please\s+)?(remind me\s+)?(to|that)\s+", "", text, flags=re.I)


# --------------------------------------------------------------- reminders

def _add(message, due):
    with _lock:
        rid = max((r["id"] for r in _reminders), default=0) + 1
        _reminders.append({"id": rid, "message": message, "due": due, "created": time.time()})
        _save(REMINDERS_FILE, _reminders)


def _announcement(reminder, late):
    message = _spoken(reminder["message"]).rstrip(".!?")
    if late > LATE_AFTER:
        return f"Reminder, sir, from {_clock(reminder['due'])}: {message}."
    return f"Reminder, sir: {message}."


def _scheduler():
    """Checks once a second, using the real clock, so it also works after the laptop sleeps."""
    global _last_fired
    while True:
        time.sleep(1)
        now = time.time()
        with _lock:
            due = [r for r in _reminders if r["due"] <= now]
            if due:
                for r in due:
                    _reminders.remove(r)
                _save(REMINDERS_FILE, _reminders)
        for r in sorted(due, key=lambda item: item["due"]):
            _last_fired = r["message"]
            notifications.put(_announcement(r, now - r["due"]))


threading.Thread(target=_scheduler, daemon=True).start()


def _set(message, minutes, at_time):
    message = _clean_message(message) or "your time is up"
    if (at_time or "").strip():
        due = _due_from_clock(at_time)
        if due is None:
            return "I didn't catch the time, sir. Say it like three thirty p.m."
    else:
        amount = _number(minutes)
        if amount is None:
            return "When should I remind you, sir?"
        if amount < 0.1 or amount > MAX_MINUTES:
            return "I can remind you from a few seconds up to a week away, sir."
        due = time.time() + amount * 60
    _add(message, due)
    return f"Alright, I'll remind you {_when(due)}: {_spoken(message)}."


def _list():
    with _lock:
        items = sorted(_reminders, key=lambda r: r["due"])
    if not items:
        return "You have no reminders, sir."
    parts = [f"{_spoken(r['message'])} {_when(r['due'])}" for r in items[:3]]
    text = f"You have {_plural(len(items), 'reminder')}, sir: " + "; ".join(parts)
    if len(items) > 3:
        text += f"; and {len(items) - 3} more"
    return text + "."


def _cancel(message):
    words = _words(message)
    with _lock:
        if not _reminders:
            return "You have no reminders to cancel, sir."
        if words == ["all"]:
            count = len(_reminders)
            _reminders.clear()
            _save(REMINDERS_FILE, _reminders)
            return f"Cancelled all {_plural(count, 'reminder')}, sir."
        if not words:
            if len(_reminders) > 1:
                return f"Which one, sir? You have {len(_reminders)} reminders."
            hits = list(_reminders)
        else:
            hits = [r for r in _reminders if all(w in _norm(r["message"]) for w in words)]
        if not hits:
            return f"I don't see a reminder like that, sir."
        for r in hits:
            _reminders.remove(r)
        _save(REMINDERS_FILE, _reminders)
    return f"Cancelled {_plural(len(hits), 'reminder')}, sir."


def _snooze(minutes):
    if _last_fired is None:
        return "I haven't reminded you of anything yet, sir."
    amount = _number(minutes) or 5
    _add(_last_fired, time.time() + amount * 60)
    return f"Snoozed for {_duration(amount)}, sir."


def reminder(action, message="", minutes="", at_time=""):
    action = (action or "").strip().lower()
    if action == "set":
        return _set(message, minutes, at_time)
    if action == "list":
        return _list()
    if action == "cancel":
        return _cancel(message)
    if action == "snooze":
        return _snooze(minutes)
    return "I don't know that reminder command, sir."


# ------------------------------------------------------------------- notes

def _looks_like_secret(text):
    """Someone dictating an actual password. Pointing to a vault or password manager is fine."""
    says_secret = re.search(r"\b(password|passcode|pin|token|secret|api key)\b\s*(is|are|:|=)\s*\S+", text, re.I)
    names_vault = re.search(r"vault|manager|keepass|bitwarden|1password|lastpass|dashlane", text, re.I)
    return bool(says_secret) and not names_vault


def _note_add(text):
    text = re.sub(r"^(that|to)\s+", "", (text or "").strip(), flags=re.I)
    if not text:
        return "What should I note, sir?"
    with _lock:
        _notes.append({"text": text, "time": time.time()})
        _save(NOTES_FILE, _notes)
    reply = "Noted, sir."
    if _looks_like_secret(text):
        reply += " Notes are plain text, so I'd keep the real password in your vault."
    return reply


def _note_latest():
    with _lock:
        latest = list(reversed(_notes))[:3]
    if not latest:
        return "You have no notes yet, sir."
    return "Your latest notes, sir. " + " ".join(f"{_day(n['time'])}: {_spoken(n['text']).rstrip('.')}." for n in latest)


def _note_search(text):
    words = _words(text)
    if not words:
        return _note_latest()
    with _lock:
        hits = [n for n in reversed(_notes) if all(w in _norm(n["text"]) for w in words)]
    if not hits:
        return f"I have no note about {' '.join(words)}, sir."
    if len(hits) == 1:
        return f"{_day(hits[0]['time'])}, you noted: {_spoken(hits[0]['text']).rstrip('.')}."
    shown = " ".join(f"{_day(n['time'])}: {_spoken(n['text']).rstrip('.')}." for n in hits[:3])
    return f"I found {len(hits)} notes, sir. {shown}"


def notes(action, text=""):
    action = (action or "").strip().lower()
    if action == "add":
        return _note_add(text)
    if action == "search":
        return _note_search(text)
    if action == "latest":
        return _note_latest()
    return "I don't know that notes command, sir."


# ---------------------------------------------------------------- registration

HINT = ("Notes and reminders: use reminder to set, list, cancel or snooze reminders, and notes to save or look up notes. "
        "When the user says they will be busy for a while, like 'I have a break for the next 10 minutes' or "
        "'I'm taking a bath for 5 minutes, remind me', set a reminder for those minutes with a message like "
        "'your break is over' or 'your bath time is up'.")

SCHEMAS = [
    tool("reminder",
         "Set, list, cancel or snooze reminders. To set one, give a message and either the minutes from now or a clock time. "
         "Use it for 'remind me in 20 minutes to ...', 'remind me at 3:30 to ...', and for 'I'm taking a break or a bath "
         "for the next N minutes, remind me', where the message says the time is up.",
         {"action": "set, list, cancel or snooze",
          "message": "What to remind about, like 'check the build', or 'your break is over'. For cancel: a word from the reminder, or 'all'.",
          "minutes": "How many minutes from now, as a number, like 20. Use 60 for an hour. For snooze: how long to snooze.",
          "at_time": "A clock time instead of minutes, like 15:30 for 3:30 pm. Leave empty when using minutes."},
         optional=("message", "minutes", "at_time"),
         enums={"action": ["set", "list", "cancel", "snooze"]}),
    tool("notes",
         "Save or look up the user's notes. Use add for 'remember that ...' or 'note that ...', search for "
         "'what did I note about ...', and latest to read back the newest notes.",
         {"action": "add, search or latest",
          "text": "The note to save, or the words to search for"},
         optional=("text",),
         enums={"action": ["add", "search", "latest"]}),
]

TOOLS = {
    "reminder": reminder,
    "notes": notes,
}