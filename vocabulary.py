"""data/vocabulary.txt, now with a part that Jarvis writes himself.

  The names YOU type at the top stay exactly as they are. Below the marker line, Jarvis adds the apps and
  projects he successfully opens or closes, so Whisper learns to spell them. Delete any line you don't want.

Two jobs:
  1. hint_names()    - the names Whisper is told to expect (this is what makes it spell them right).
  2. autocorrect()   - when Whisper spells a name your own way, like "odo rico" for Odoriko, he fixes it
                       to the vocabulary spelling before he acts. It only changes words that SOUND exactly like
                       a vocabulary name (same sound-alike spelling), so ordinary words are left alone.
"""
import os
import re

VOCAB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vocabulary.txt")
MARKER = "# --- Jarvis adds names below by himself (oldest first). Delete any line you don't want. ---"
MAX_AUTO = 30             # how many names he keeps in his own part of the file
MIN_FIX_LENGTH = 5        # names shorter than this are never auto-corrected, they match too many words
AUTO_FIX = True           # set to False to switch off the automatic spelling fix


def _plain(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def sound_key(text):
    """A rough 'how it sounds' spelling. Folds together letters that are often swapped:
    P/F, B/V, E/I, O/U, C/K, S/Z, TH/T, and drops doubled letters."""
    t = _plain(text)
    for a, b in (("ph", "f"), ("ck", "k"), ("qu", "kw"), ("th", "t"), ("sh", "s"), ("ch", "k"), ("x", "ks")):
        t = t.replace(a, b)
    t = re.sub(r"c(?=[eiy])", "s", t)
    t = t.replace("c", "k").replace("q", "k").replace("z", "s").replace("v", "b").replace("f", "p")
    t = t.replace("w", "u").replace("y", "i").replace("e", "i").replace("o", "u")
    return re.sub(r"(.)\1+", r"\1", t)


def _read():
    """(names you wrote, names Jarvis added), each in file order."""
    manual, auto, in_auto = [], [], False
    try:
        with open(VOCAB_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line == MARKER:
                    in_auto = True
                    continue
                if not line or line.startswith("#"):
                    continue
                (auto if in_auto else manual).append(line)
    except OSError:
        pass
    return manual, auto


_fix_cache = {"stamp": None, "entries": []}


def names():
    manual, auto = _read()
    return list(dict.fromkeys(manual + auto))


def hint_names():
    """Names for Whisper: yours first, then the newest ones he added."""
    manual, auto = _read()
    return list(dict.fromkeys(manual + list(reversed(auto))))


def add_auto(name):
    """Remember a name that worked. Does nothing if it is already in the file."""
    name = " ".join((name or "").split())
    if not 2 <= len(name) <= 40 or "uninstall" in name.lower():
        return
    manual, auto = _read()
    if name.lower() in {n.lower() for n in manual + auto}:
        return
    try:
        os.makedirs(os.path.dirname(VOCAB_FILE), exist_ok=True)
        if len(auto) >= MAX_AUTO:                                    # too many: drop the oldest ones
            keep = auto[-(MAX_AUTO - 1):] if MAX_AUTO > 1 else []
            with open(VOCAB_FILE, encoding="utf-8") as f:
                head = f.read().split(MARKER)[0].rstrip("\n")
            with open(VOCAB_FILE + ".tmp", "w", encoding="utf-8") as f:
                f.write(head + "\n\n" + MARKER + "\n" + "\n".join(keep + [name]) + "\n")
            os.replace(VOCAB_FILE + ".tmp", VOCAB_FILE)
            return
        exists = os.path.exists(VOCAB_FILE)
        text = open(VOCAB_FILE, encoding="utf-8").read() if exists else ""
        with open(VOCAB_FILE, "a", encoding="utf-8") as f:
            if text and not text.endswith("\n"):
                f.write("\n")
            if MARKER not in text:
                f.write(("\n" if text else "") + MARKER + "\n")
            f.write(name + "\n")
        print(f"[vocabulary] added '{name}'")
    except OSError as e:
        print("[vocabulary] couldn't save:", e)


def _fix_entries():
    """(sound key, spelling) for every vocabulary name that is safe to auto-correct. Reloaded when the file changes."""
    try:
        stamp = os.path.getmtime(VOCAB_FILE)
    except OSError:
        stamp = None
    if _fix_cache["stamp"] != stamp or stamp is None and _fix_cache["entries"]:
        entries = []
        for name in names():
            key = sound_key(name)
            if len(_plain(name)) >= MIN_FIX_LENGTH and len(key) >= 4:
                entries.append((key, name))
        _fix_cache["stamp"], _fix_cache["entries"] = stamp, entries
    return _fix_cache["entries"]


def autocorrect(text):
    """'open odo rico' -> 'open Odoriko', when Odoriko is in the vocabulary."""
    if not AUTO_FIX or not text:
        return text
    entries = _fix_entries()
    if not entries:
        return text
    by_key = {key: name for key, name in entries}
    words = list(re.finditer(r"[A-Za-z0-9']+", text))
    out, i, last = [], 0, 0
    while i < len(words):
        replaced = False
        for n in (3, 2, 1):                                           # longest span first
            if i + n > len(words):
                continue
            span = text[words[i].start():words[i + n - 1].end()]
            name = by_key.get(sound_key(span))
            if name and span != name and "'" not in span:
                out.append(text[last:words[i].start()])
                out.append(name)
                last = words[i + n - 1].end()
                i += n
                replaced = True
                break
        if not replaced:
            i += 1
    out.append(text[last:])
    return "".join(out)