"""Spotify and media keys.

Works for everyone, no account needed:
  - open Spotify, open a search inside it, press play on whatever Spotify last had
  - media keys (play/pause, next, previous) for any player, including YouTube in Chrome
Starting a specific song by itself needs Spotify Premium and a free Spotify developer app.
"""
import csv
import ctypes
import io
import os
import re
import subprocess
import time
from urllib.parse import quote

from skills import tool

VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREV = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002


def _press(vk):
    """Press a media key, like the one on a keyboard."""
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def _spotify_state():
    """'off', 'idle' (open but not playing) or 'playing'.
    Read from the window titles: Spotify shows 'Artist - Song' while playing, and just its own name otherwise."""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Spotify.exe", "/V", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, errors="replace",
    ).stdout
    titles = []
    for row in csv.reader(io.StringIO(out)):
        if len(row) >= 9 and row[0].lower() == "spotify.exe":
            titles.append(row[-1].strip())
    if not titles:
        return "off"
    if any(re.search(r".\s-\s.", t) for t in titles):
        return "playing"
    return "idle"


def _open(uri, web_url):
    """Open a spotify: link in the desktop app, or the web player if the app isn't installed."""
    try:
        os.startfile(uri)
        return True
    except OSError:
        from actions import launch_browser
        launch_browser(web_url)
        return False


def play_spotify(query=""):
    query = (query or "").strip()

    if query:
        in_app = _open(f"spotify:search:{quote(query)}", f"https://open.spotify.com/search/{quote(query)}")
        where = "in Spotify" if in_app else "on the Spotify web player"
        return f"I've opened {query} {where}, sir. Spotify Premium isn't linked, so please press play."

    state = _spotify_state()
    if state == "playing":
        return "Spotify is already playing, sir."
    in_app = _open("spotify:", "https://open.spotify.com")
    if not in_app:
        return "Opening the Spotify web player, sir. Please press play."
    if state == "off":
        time.sleep(6)               # give the app time to start
    _press(VK_MEDIA_PLAY_PAUSE)
    return "Opening Spotify and pressing play, sir."


_ACTIONS = {
    "play_pause": (VK_MEDIA_PLAY_PAUSE, "Done, sir."),
    "next": (VK_MEDIA_NEXT, "Next track, sir."),
    "previous": (VK_MEDIA_PREV, "Previous track, sir."),
}
_ALIASES = {"pause": "play_pause", "play": "play_pause", "resume": "play_pause", "toggle": "play_pause",
            "skip": "next", "forward": "next", "back": "previous", "last": "previous"}


def media_control(action):
    key = (action or "").strip().lower().replace(" ", "_")
    key = _ALIASES.get(key, key)
    if key not in _ACTIONS:
        return "I don't know that media command, sir."
    vk, reply = _ACTIONS[key]
    _press(vk)
    return reply


SCHEMAS = [
    tool("play_spotify",
         "Open Spotify and play music. Use this ONLY when the user says Spotify. With a song, artist or playlist "
         "name it opens that in Spotify. With no name it opens Spotify and plays whatever was last playing.",
         {"query": "Optional song, artist or playlist name. Leave empty to just play some music"},
         optional=("query",)),
    tool("media_control",
         "Control whatever music or video is playing right now: pause, resume, next track or previous track. "
         "Works with Spotify, YouTube and other players.",
         {"action": "play_pause to pause or resume, next to skip the track, previous to go back"},
         enums={"action": ["play_pause", "next", "previous"]}),
]

TOOLS = {
    "play_spotify": play_spotify,
    "media_control": media_control,
}

HINT = ("Spotify: use play_spotify only when the user says Spotify, and media_control to pause, resume, "
        "skip a track or go back a track.")