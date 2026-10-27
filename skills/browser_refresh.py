"""Refresh browser tabs by voice, through the Jarvis Bridge extension.

  "refresh this tab" / "refresh my current tab"  -> reloads the tab you are looking at
  "refresh youtube"                              -> reloads the YouTube tab (asks which one if there are several)
  "refresh the one republic tab"                 -> you named it, so no question
  "refresh all my github tabs" / "refresh the first youtube"
"""
import re
import time

import browser_bridge as bridge
from skills import tool

OFFLINE = "I can't reach your browser, sir. Is the Jarvis Bridge extension switched on in Chrome?"
OUTDATED = "My browser extension is out of date, sir. Please reload it in Chrome."

# Words that say nothing about WHICH tab. If only these are left, he means the tab you are on.
_FILLER = {"the", "a", "tab", "tabs", "with", "my", "this", "that", "page", "pages", "website",
           "websites", "window", "current", "active", "here", "now", "please", "it", "sir",
           "refresh", "reload", "open", "currently"}

_FIRST = {"first", "oldest"}
_LAST = {"last", "newest", "latest"}
_ALL = {"all", "every", "everything", "both", "them"}
_YES = {"yes", "yeah", "yep", "yup", "sure", "okay", "ok"}      # "which one, or all?" -> "yes" means all
_NO = {"no", "nope", "cancel", "stop", "nevermind", "don't", "never"}
_ORDINALS = {"second": 1, "2nd": 1, "two": 1, "third": 2, "3rd": 2, "three": 2,
             "fourth": 3, "4th": 3, "four": 3, "fifth": 4, "5th": 4}

ASK_WINDOW = 25            # seconds you have to answer "which one?"
SPEAK_TITLES_UP_TO = 4     # he reads the tab names out when there are this many or fewer

_pending = None            # {"tabs": [{"id", "title"}], "label", "time"}


def _tabs(n):
    return f"{n} tab" + ("" if n == 1 else "s")


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _FILLER]


def _matches(tab, words):
    """Every word is in the tab's title or address. Spaces and punctuation are ignored."""
    haystack = re.sub(r"[^a-z0-9]", "", f"{tab.get('title') or ''} {tab.get('url') or ''}".lower())
    return all(w in haystack for w in words)


def _short(title):
    """A title that sounds fine out loud: no '(3)' counter, no ' - YouTube' tail, five words at most."""
    title = re.sub(r"^\(\d+\)\s*", "", title or "")
    if " - " in title:
        title = title.rsplit(" - ", 1)[0]
    words = title.split()
    return " ".join(words[:5]) + ("..." if len(words) > 5 else "") or "that tab"


def _reload(tabs, label="", hard=False, single_note=""):
    ids = [t["id"] for t in tabs]
    try:
        reply = bridge.request("reload_ids", ids=ids, hard=hard)
    except Exception:
        return OFFLINE
    if reply.get("error"):
        return OUTDATED if "unknown action" in str(reply["error"]) else f"I couldn't do that, sir: {reply['error']}"
    done = reply.get("reloaded", 0)
    if done == 0:
        return "That tab seems to be gone, sir."
    verb = "Hard refreshed" if hard else "Refreshed"
    if done == 1:
        return single_note or f"{verb} {_short(tabs[0].get('title'))}, sir."
    return f"{verb} {_tabs(done)}" + (f" matching {label}" if label else "") + ", sir."


def refresh_tab(match="", which="", hard=""):
    global _pending
    _pending = None
    want_hard = str(hard or "").strip().lower() in ("true", "yes", "hard", "1")
    words = _words(match)

    # No name given: the tab you are looking at
    if not words:
        try:
            reply = bridge.request("current_tab")
        except Exception:
            return OFFLINE
        if reply.get("error"):
            return OUTDATED if "unknown action" in str(reply["error"]) else f"I couldn't do that, sir: {reply['error']}"
        tab = reply.get("tab")
        if not tab:
            return "I don't see an active browser tab, sir."
        return _reload([tab], hard=want_hard)

    label = " ".join(words)
    try:
        everything = bridge.request("list_tabs").get("tabs", [])
    except Exception:
        return OFFLINE
    hits = sorted((t for t in everything if _matches(t, words)), key=lambda t: t.get("id") or 0)
    if not hits:
        return f"I don't see any tabs matching {label}, sir."
    if any(t.get("id") is None for t in hits):
        return OUTDATED
    if len(hits) == 1:
        return _reload(hits, label, want_hard)

    choice = (which or "").strip().lower()
    if choice in _ALL:
        return _reload(hits, label, want_hard)
    picked = _pick(hits, choice)
    if picked:
        return _reload([picked], label, want_hard, single_note=f"Refreshed {_short(picked.get('title'))}, sir.")

    _pending = {"tabs": hits, "label": label, "hard": want_hard, "time": time.monotonic()}
    if len(hits) <= SPEAK_TITLES_UP_TO:
        names = [_short(t.get("title")) for t in hits]
        listing = ", ".join(names[:-1]) + " and " + names[-1]
        return f"You have {len(hits)} {label} tabs, sir: {listing}. Which one, or shall I refresh them all?"
    return f"You have {len(hits)} tabs matching {label}, sir. Which one, or shall I refresh them all?"


def _pick(tabs, choice):
    """first / second / newest ... -> one tab, or None."""
    choice = (choice or "").strip().lower()
    if choice in _FIRST:
        return tabs[0]
    if choice in _LAST:
        return tabs[-1]
    if choice in _ORDINALS and _ORDINALS[choice] < len(tabs):
        return tabs[_ORDINALS[choice]]
    return None


def cancel_pending():
    global _pending
    _pending = None


def resolve_choice(text):
    """Your answer to 'which one, or all?'. Returns the reply to speak, or None when the sentence
    isn't an answer (the model then handles it). Call this before the model, in jarvis.py."""
    global _pending
    pending, _pending = _pending, None                     # any sentence ends the question
    if not pending or time.monotonic() - pending["time"] > ASK_WINDOW:
        return None
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    if not words or len(words) > 8:
        return None
    wset = set(words)
    tabs, hard, label = pending["tabs"], pending["hard"], pending["label"]

    for word in words:                                      # "the second one", "newest", "first"
        picked = _pick(tabs, word)
        if picked:
            return _reload([picked], label, hard, single_note=f"Refreshed {_short(picked.get('title'))}, sir.")
    if wset & (_ALL | _YES):
        return _reload(tabs, label, hard)
    if wset & _NO:
        return "Very well, sir. I'll leave them."

    # You said part of a title, like "the laravel one"
    name_words = [w for w in words if w not in _FILLER and w not in {"one", "the", "refresh"}]
    if name_words:
        named = [t for t in tabs if _matches(t, name_words)]
        if len(named) == 1:
            return _reload(named, label, hard, single_note=f"Refreshed {_short(named[0].get('title'))}, sir.")
    return None


HINT = ("Refreshing: when the user says refresh, reload, or 'refresh my current tab', use refresh_tab. "
        "Leave match empty for the tab they are on, or pass a few words from the tab's title or site. "
        "Use close_tabs only to close tabs, never to refresh them.")

SCHEMAS = [
    tool("refresh_tab",
         "Refresh (reload) a browser tab. With no match it refreshes the tab the user is looking at. "
         "With a match it refreshes the tab whose title or website contains those words, and if several "
         "match it asks the user which one.",
         {"match": "A few words from the tab's title or site, e.g. youtube, github, one republic. "
                   "Leave empty for the current tab.",
          "which": "Only if the user said: all, first, last, second, or third, when several tabs match.",
          "hard": "Say yes only if the user asked for a hard refresh or to clear the cache."},
         optional=("match", "which", "hard")),
]

TOOLS = {
    "refresh_tab": refresh_tab,
}