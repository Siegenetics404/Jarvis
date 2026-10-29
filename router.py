"""Plain commands that don't need the language model.

"open discord", "close spotify", "launch visual studio code", "open the luxara project",
"pause the music", "resume", "next song", "play the music in Spotify"

The local model sometimes answers "Closing discord." in words without actually calling the tool, or asks you a
question instead of acting. For these simple commands the program can do it by itself: it checks the name against
the apps and projects that really exist on your PC and calls the tool directly. It is also faster, because the
model isn't involved. Anything it isn't sure about goes to the model as before.
"""
import json
import re

import learning
import session_memory

OPEN_VERBS = {"open", "launch", "start", "run"}
CLOSE_VERBS = {"close", "quit", "exit", "kill"}

_LEADING = {"the", "my", "this", "that", "up", "a", "an", "please", "me", "for", "to", "open"}
_TRAILING = {"app", "application", "program", "please", "now", "again", "sir", "jarvis", "project", "folder"}
_PRONOUNS = {"it", "that", "this", "them", "everything", "all", "these", "those"}
_NOT_AN_APP = {"tab", "tabs", "window", "windows", "browser", "page", "pages", "website", "site", "video"}
_WEB_NAMES = {"youtube", "google", "gmail", "facebook", "github", "netflix", "twitter", "instagram", "tiktok",
              "reddit", "chatgpt", "claude", "wikipedia", "amazon", "maps", "drive", "linkedin"}

ROUTE_AT = 0.70            # how alike the name must be to an installed app or project
ROUTE_GAP = 0.12           # and how much better than the runner-up, otherwise the model decides


# Pause, resume, next and previous song, and "play the music in Spotify"
_MEDIA_FILLER = {"the", "my", "your", "this", "that", "current", "currently", "playing", "music", "song", "songs",
                 "track", "tracks", "playback", "audio", "video", "it", "please", "sir", "jarvis", "now", "in", "on",
                 "spotify", "yes", "yeah", "yep", "just", "a", "one", "then", "go", "to"}
_MUSIC_WORDS = {"music", "song", "songs", "track", "tracks", "playback", "audio", "video", "spotify", "it"}
_PAUSE_WORDS = {"pause", "resume", "unpause", "continue"}
_PAUSE_MISHEARD = {"post", "pose", "paws", "pours", "paus", "watch", "pass"}   # what Whisper sometimes hears for "pause"


def _media_call(words):
    ws = set(words)
    content = [w for w in words if w not in _MEDIA_FILLER or w == "go"]
    named = bool(ws & _MUSIC_WORDS)
    if len(content) == 1 and content[0] in _PAUSE_WORDS:
        return "media_control", json.dumps({"action": "play_pause"})
    if len(content) == 1 and content[0] in _PAUSE_MISHEARD and ws & (_MUSIC_WORDS - {"it", "video"}):
        return "media_control", json.dumps({"action": "play_pause"})
    if content == ["play"] and "spotify" in ws:
        return "play_spotify", "{}"
    last = session_memory.last_action()
    if last and last["tool"] == "play_youtube":                   # skipping inside YouTube is the YouTube tool's job
        return None
    wants_song = bool(ws & {"song", "songs", "track", "tracks", "music"})
    if content in (["next"], ["play", "next"]) and named:
        return "media_control", json.dumps({"action": "next"})
    if content == ["skip"] and wants_song:
        return "media_control", json.dumps({"action": "next"})
    if content in (["previous"], ["last"]) and wants_song:
        return "media_control", json.dumps({"action": "previous"})
    if content == ["go", "back"] and wants_song:
        return "media_control", json.dumps({"action": "previous"})
    return None


def _best(heard, names):
    top = learning.ranked(heard, names)[:2]
    if not top:
        return None, 0.0, 0.0
    second = top[1][0] if len(top) > 1 else 0.0
    return top[0][1], top[0][0], second


def quick_call(text):
    """Returns (tool, arguments_json) for a simple open/close command, or None to let the model decide."""
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    if not words or len(words) > 7:
        return None
    media = _media_call(words)
    if media:
        return media
    if len(words) < 2:
        return None
    verb, rest = words[0], words[1:]
    if verb in OPEN_VERBS:
        action = "open"
    elif verb in CLOSE_VERBS:
        action = "close"
    else:
        return None

    while rest and rest[0] in _LEADING:
        rest = rest[1:]
    while rest and rest[-1] in _TRAILING:
        rest = rest[:-1]
    if not rest or len(rest) > 4:
        return None
    if set(rest) & (_PRONOUNS | _NOT_AN_APP | {"and", "then", "also", "with", "on", "in"}):
        return None
    heard = " ".join(rest)

    # A name he learned earlier ("odo rico" means Odoriko)
    learned = learning._learned["apps"].get(heard) or (learning._learned["projects"].get(heard) if action == "open" else None)
    if learned:
        heard = learned

    apps = learning._candidates("apps")
    projects = learning._candidates("projects") if action == "open" else []
    if heard in apps:                                              # exact name: no doubt at all
        return ("open_app" if action == "open" else "close_app"), json.dumps({"name": heard})
    if heard in _WEB_NAMES:
        return None                                                # probably a website, let the model decide
    if len(heard) >= 4:                                            # part of exactly one app name: "cord" in "discord"
        inside = [name for name in apps if heard in name]
        if len(inside) == 1:
            return ("open_app" if action == "open" else "close_app"), json.dumps({"name": inside[0]})

    app, app_score, app_next = _best(heard, apps)
    project, project_score, project_next = _best(heard, projects)
    if action == "open" and project and project_score >= ROUTE_AT and project_score - app_score >= ROUTE_GAP \
            and project_score - project_next >= ROUTE_GAP:
        return "open_project", json.dumps({"name": project})
    if app and app_score >= ROUTE_AT and app_score - app_next >= ROUTE_GAP and app_score - project_score >= ROUTE_GAP:
        return ("open_app" if action == "open" else "close_app"), json.dumps({"name": app})
    return None