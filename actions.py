import difflib
import importlib
import json
import os
import re
import subprocess
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import browser_bridge as bridge
from skills import tool as _tool

# Set when the user tells Jarvis to shut himself down. jarvis.py checks it after he says goodbye.
QUIT = threading.Event()

# Which Chrome profile to open. None = the profile you used last.
# If the wrong one opens, set it to that profile's folder name, e.g. "Profile 1".
# Find it by typing chrome://version in Chrome: the last folder of "Profile Path".
BROWSER_PROFILE = None

# Folders that CONTAIN your projects. Add more here later.
PROJECT_ROOTS = [
    {"kind": "wsl", "distro": "Ubuntu", "path": "/home/cj/LaravelProjects"},
    {"kind": "win", "path": r"C:\dev"},
]

START_MENU = [
    Path(os.environ.get("ProgramData", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]

BROWSER_OFFLINE = "I can't reach your browser, sir. Is the Jarvis Bridge extension switched on in Chrome?"
YES_WORDS = {"yes", "yeah", "yep", "yup", "sure", "okay", "ok", "please", "confirm", "proceed"}
NO_WORDS = {"no", "nope", "cancel", "stop", "nevermind"}

_pending = None            # a question Jarvis just asked, waiting for your answer

bridge.start()             # listens for the Chrome extension


def best_match(query, names):
    q = query.lower().strip()
    if q in names:
        return q
    subs = [n for n in names if q in n]
    if subs:
        return min(subs, key=len)
    close = difflib.get_close_matches(q, names, n=1, cutoff=0.6)
    return close[0] if close else None


def find_apps():
    apps = {}
    for base in START_MENU:
        if not base.exists():
            continue
        for lnk in base.rglob("*.lnk"):
            name = lnk.stem.lower()
            if "uninstall" in name:
                continue
            apps.setdefault(name, str(lnk))
    return apps


def find_projects():
    projects = {}
    for root in PROJECT_ROOTS:
        if root["kind"] == "wsl":
            base = Path(rf"\\wsl.localhost\{root['distro']}") / root["path"].lstrip("/")
        else:
            base = Path(root["path"])
        if not base.exists():
            continue
        for d in base.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                if root["kind"] == "wsl":
                    projects[d.name.lower()] = ("wsl", root["distro"], f"{root['path']}/{d.name}")
                else:
                    projects[d.name.lower()] = ("win", None, str(d))
    return projects


def lnk_target(lnk_path):
    ps = f'(New-Object -ComObject WScript.Shell).CreateShortcut("{lnk_path}").TargetPath'
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True,
    ).stdout.strip()
    return out


# ------------------------------------------------------------- browser

def chrome_exe():
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def chrome_profile():
    """Folder name of the Chrome profile to open, like 'Default' or 'Profile 1'."""
    if BROWSER_PROFILE:
        return BROWSER_PROFILE
    try:
        state = Path(os.environ["LOCALAPPDATA"]) / "Google/Chrome/User Data/Local State"
        data = json.loads(state.read_text(encoding="utf-8"))
        return data["profile"]["last_used"]
    except Exception:
        return "Default"


def launch_browser(url=None):
    """Open Chrome straight into a profile, so the account picker never shows."""
    exe = chrome_exe()
    if not exe:
        webbrowser.open(url or "about:blank")      # Chrome not found: use the default browser
        return
    args = [exe, f"--profile-directory={chrome_profile()}"]
    if url:
        args.append(url)
    subprocess.Popen(args)


# ---------------------------------------------------------------- tools

def open_app(name):
    apps = find_apps()
    match = best_match(name, list(apps))
    if not match:
        return f"Couldn't find an app like '{name}'"
    if "chrome" in match:                          # skip Chrome's account picker
        launch_browser()
        return f"Opening {match}"
    os.startfile(apps[match])
    return f"Opening {match}"


# Apps that keep running in the tray when you close their window have their own proper quit command.
QUIT_COMMANDS = {
    "docker": ["docker", "desktop", "stop"],
}


def _is_running(exe):
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
        capture_output=True, text=True,
    ).stdout
    return exe.lower() in out.lower()


def close_app(name):
    apps = find_apps()
    match = best_match(name, list(apps))
    if not match:
        return f"Couldn't find an app like '{name}'"
    target = lnk_target(apps[match])
    if not target.lower().endswith(".exe"):
        return f"Can't tell which process '{match}' runs (shortcut target: '{target}')"
    exe = Path(target).name

    if not _is_running(exe):
        return f"{match} isn't running (looked for {exe})"

    # Apps that hide in the tray when their window closes: use their own quit command
    for key, command in QUIT_COMMANDS.items():
        if key in match:
            try:
                done = subprocess.run(command, capture_output=True, timeout=90)
                if done.returncode == 0:
                    return f"Quit {match}."
            except (OSError, subprocess.TimeoutExpired):
                pass           # command missing or too slow, fall back to the normal way

    # 1. Ask politely, which closes the window
    result = subprocess.run(["taskkill", "/IM", exe, "/T"], capture_output=True, text=True)
    if result.returncode == 0:
        time.sleep(2)
        if _is_running(exe):
            return f"I asked {match} to close, sir, but it's still running. It may be waiting on a prompt or hiding in the tray."
        return f"Closed {match}"

    # 2. It has no window left to close, or refused: force it
    result = subprocess.run(["taskkill", "/IM", exe, "/T", "/F"], capture_output=True, text=True)
    if result.returncode == 0:
        return f"Force closed {match}"
    return f"Couldn't close {match}: {(result.stderr or result.stdout).strip()}"


def open_project(name):
    projects = find_projects()
    match = best_match(name, list(projects))
    if not match:
        return f"Couldn't find a project like '{name}'"
    kind, distro, path = projects[match]
    if kind == "wsl":
        subprocess.Popen(f'code --remote wsl+{distro} "{path}"', shell=True)
    else:
        subprocess.Popen(f'code "{path}"', shell=True)
    return f"Opening project {match}"


def get_time():
    now = datetime.now()
    clock = now.strftime("%I:%M %p").lstrip("0")
    return f"It's {clock}, sir. Today is {now:%A}, {now:%B} {now.day}."


def quit_jarvis():
    QUIT.set()
    return "Shutting down. Goodbye, sir."


def search_web(query):
    launch_browser("https://www.google.com/search?q=" + quote_plus(query))
    return f"Searching for {query}"


def open_website(url):
    url = url.strip().strip(".")
    if "://" not in url:
        if "." not in url:                         # not an address, so treat it as a search
            return search_web(url)
        url = "https://" + url
    if not url.lower().startswith(("http://", "https://")):
        return "I only open web addresses, sir."
    launch_browser(url)
    host = urlparse(url).netloc.replace("www.", "")
    return f"Opening {host}"


_utterance = ""        # what you just said, set by run_tool, so a tool can recover details the model left out

# "and skip ads", "skip the ads", "then skip those ads"...
_SKIP_ADS = re.compile(
    r"\b(?:and\s+|then\s+|also\s+|but\s+)?skip(?:ping)?\s+(?:the\s+|all\s+|those\s+|any\s+)?(?:youtube\s+)?(?:ads?|adds?)\b",
    re.I)


def _query_from_speech(text):
    """Pull the song or artist out of a sentence like 'open YouTube and play some Charlie Puth music'."""
    t = _SKIP_ADS.sub(" ", (text or "").lower())
    t = re.sub(r"\b(open|go to|launch|start)\s+youtube\b\s*(and\s+|then\s+)?", " ", t)
    t = re.sub(r"\b(on|in|from|using)\s+youtube\b", " ", t)
    t = re.sub(r"\byoutube\b", " ", t)
    t = re.sub(r"\b(please|jarvis|sir|for me|can you|could you|would you)\b", " ", t)
    t = re.sub(r"^\W*(play|put on|listen to|search for|search|find|look up)\b", " ", t)
    t = re.sub(r"^\W*(some|any|a|an|the|something by|songs? by|music by)\b", " ", t)
    return " ".join(t.split()).strip(" .,!?")


def _auto_skip_note():
    """Switch on 'press Skip when YouTube allows it'. Returns the words to add to what he says."""
    try:
        from skills.youtube import enable_auto_skip
        ok = enable_auto_skip()
    except Exception:
        try:
            bridge.request("yt_auto_skip", on=True)
            ok = True
        except Exception:
            ok = False
    if ok:
        return ", and I'll skip the ads, sir."
    return ", but I can't reach the browser extension to skip ads, sir."


def play_youtube(query=""):
    """Find the best match on YouTube and open that video, which starts playing.
    If you also said 'skip ads', auto skip is switched on so the Skip button gets pressed when it appears."""
    wants_skip = bool(_SKIP_ADS.search(_utterance))
    query = " ".join(_SKIP_ADS.sub(" ", query or "").split()).strip(" .,!?")
    if not query:                                   # the model left it out: take it from what you said
        query = _query_from_speech(_utterance)
        if query:
            print(f"[youtube] the model sent no song, so I used: {query}")
    if len(query) < 2:
        return "What should I play, sir?"
    try:
        from yt_dlp import YoutubeDL
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True,
                "socket_timeout": 8}
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch5:{query}", download=False)
        for entry in info.get("entries") or []:
            vid = entry.get("id") or ""
            if len(vid) == 11 and "/shorts/" not in (entry.get("url") or ""):
                title = str(entry.get("title")).encode("ascii", "replace").decode()   # safe for the Windows console
                print(f"[youtube] picked: {title} -> https://www.youtube.com/watch?v={vid}")
                note = _auto_skip_note() if wants_skip else ""      # switched on before the page loads, so a first ad is caught
                launch_browser(f"https://www.youtube.com/watch?v={vid}")
                return f"Playing {query} on YouTube" + (note if note else "")
    except Exception as e:
        print("[youtube] lookup failed:", e)
    launch_browser("https://www.youtube.com/results?search_query=" + quote_plus(query))
    return f"I couldn't pick a video, sir, so I opened the YouTube results for {query}"


# ------------------------------------------------------- browser tabs

OUTDATED_EXTENSION = "My browser extension is out of date, sir. Please reload it in Chrome."

_TAB_FILLER = {"the", "a", "tab", "tabs", "with", "my", "this", "that", "page", "pages",
               "website", "websites", "window"}

# Chrome numbers tabs in the order they were opened, so the lowest ID is the oldest tab
_FIRST_WORDS = {"first", "oldest"}
_LAST_WORDS = {"last", "newest", "latest"}
_ONE_WORDS = {"one", "single"}


def _tabs(n):
    return f"{n} tab" + ("" if n == 1 else "s")


def _words(target):
    """The words that matter in what you said, without fillers like 'the' or 'tab'."""
    return [w for w in re.findall(r"[a-z0-9]+", target.lower()) if w not in _TAB_FILLER]


def _tab_matches(tab, words):
    """True if every word is in the tab's title or address. Spaces and punctuation are
    ignored, so 'one republic' matches 'OneRepublic'."""
    haystack = re.sub(r"[^a-z0-9]", "", f"{tab.get('title') or ''} {tab.get('url') or ''}".lower())
    return all(w in haystack for w in words)


def _list_tabs():
    return bridge.request("list_tabs").get("tabs", [])


def _pick_one(ids, which):
    """Choose a single tab out of several. 'first'/'oldest' = the oldest, 'one'/'last'/'newest' = the newest.
    Returns (tab id, 'oldest' or 'newest'), or (None, '') if `which` doesn't ask for a single tab."""
    which = (which or "").strip().lower()
    if which in _FIRST_WORDS:
        return min(ids), "oldest"
    if which in _LAST_WORDS or which in _ONE_WORDS or which == "1":
        return max(ids), "newest"
    return None, ""


def _close_ids(ids, label, note=""):
    try:
        reply = bridge.request("close_ids", ids=list(ids))
    except Exception:
        return BROWSER_OFFLINE
    if reply.get("error"):
        return f"I couldn't do that, sir: {reply['error']}"
    closed = reply.get("closed", 0)
    if note and closed == 1:
        return note
    return f"Closed {_tabs(closed)}" + (f" matching {label}." if label else ".")


def close_tabs(match, which=""):
    """One matching tab closes at once. Several make Jarvis ask first, unless you said which one."""
    global _pending
    words = _words(match or "")
    if len("".join(words)) < 3:
        return "Which tabs, sir? Give me a word from their title or website."
    label = " ".join(words)
    try:
        hits = [t for t in _list_tabs() if _tab_matches(t, words)]
    except Exception:
        return BROWSER_OFFLINE
    if not hits:
        return f"I don't see any tabs matching {label}, sir."
    if any(t.get("id") is None for t in hits):
        return OUTDATED_EXTENSION
    ids = sorted(t["id"] for t in hits)
    if len(ids) == 1:
        return _close_ids(ids, label)

    chosen, age = _pick_one(ids, which)
    if chosen is not None:
        return _close_ids([chosen], label, note=f"Closed the {age} of your {len(ids)} {label} tabs, sir.")

    if _pending and time.monotonic() - _pending["time"] < 15:       # a second question in the same sentence
        _pending["ids"] = sorted(set(_pending["ids"]) | set(ids))
        _pending["label"] = ""
        _pending["time"] = time.monotonic()
        return f"That's {_tabs(len(_pending['ids']))} altogether, sir. Shall I close them all?"
    _pending = {"ids": ids, "label": label, "time": time.monotonic()}
    return f"That's {_tabs(len(ids))} matching {label}, sir. Shall I close them all?"


def count_tabs(match=""):
    words = _words(match or "")
    try:
        tabs = _list_tabs()
    except Exception:
        return BROWSER_OFFLINE
    if len("".join(words)) < 3:
        return f"You have {_tabs(len(tabs))} open, sir."
    hits = [t for t in tabs if _tab_matches(t, words)]
    return f"You have {_tabs(len(hits))} matching {' '.join(words)}, out of {len(tabs)} in total, sir."


_QUIT_FILLER = {"please", "sir", "now", "hey", "ok", "okay", "can", "you", "could", "would",
                "will", "just", "the", "a", "kindly", "thanks", "thank"}
_QUIT_WORDS = {"shut", "down", "yourself", "your", "system", "offline", "go", "to", "sleep",
               "it", "up", "quit", "exit", "goodbye", "bye", "good", "turn", "off", "power"}
_QUIT_TRIGGERS = {"shut", "offline", "quit", "exit", "goodbye", "bye", "sleep", "off", "power"}


def wants_quit(text):
    """True only when the whole sentence is a request for Jarvis to shut himself down."""
    words = []
    for w in re.findall(r"[a-z']+", text.lower()):
        if w in _QUIT_FILLER or difflib.SequenceMatcher(None, w, "jarvis").ratio() >= 0.65:
            continue        # filler, or a leftover "Jarvis" that Whisper misspelled
        words.append(w)
    return bool(words) and all(w in _QUIT_WORDS for w in words) and any(w in _QUIT_TRIGGERS for w in words)


def resolve_pending(text):
    """Things that must not depend on the model: shutting down, and your answer to
    'shall I close them all?' (yes, no, only one, the first one...).
    Returns the reply, or None if the model should handle the text."""
    global _pending
    pending, _pending = _pending, None             # any answer, or any other sentence, ends the question
    if wants_quit(text):
        return quit_jarvis()
    if not pending or time.monotonic() - pending["time"] > 30:
        return None
    lowered = text.lower()
    words = re.findall(r"[a-z']+", lowered)
    if not words:
        return None
    wset = set(words)
    ids = sorted(pending["ids"])

    # "only one", "just one", "the first one", "the newest"...
    if len(words) <= 6 and wset & (_FIRST_WORDS | _LAST_WORDS | _ONE_WORDS):
        chosen, age = (min(ids), "oldest") if wset & _FIRST_WORDS else (max(ids), "newest")
        return _close_ids([chosen], pending["label"], note=f"Closed the {age} of those {len(ids)} tabs, sir.")

    if words[0] in NO_WORDS or "don't" in words or "never" in words:
        return "Very well, sir. I'll leave them."
    if words[0] in YES_WORDS or "go ahead" in lowered or "do it" in lowered or "close them" in lowered:
        return _close_ids(ids, pending["label"])
    return None


# ------------------------------------------- tool registry (built-in tools)

TOOL_SCHEMAS = [
    _tool("open_app", "Open an application installed on the computer",
          {"name": "Name of the app, e.g. docker, ubuntu, chrome"}),
    _tool("close_app", "Close a desktop application, a program. Not for websites or browser tabs.",
          {"name": "Name of the app, e.g. docker, vscode"}),
    _tool("open_project", "Open a coding project in VS Code by its folder name",
          {"name": "Name of the project folder, e.g. amefil"}),
    _tool("get_time", "Get the current local time and today's date. Use this whenever the user asks what time or day it is."),
    _tool("open_website", "Open a website in the browser. Use this when the user asks to open or go to a site.",
          {"url": "Full web address of the site, e.g. https://github.com or https://laravel.com/docs"}),
    _tool("search_web", "Search the internet in the browser. Use this when the user asks to search, google, or look something up online.",
          {"query": "What to search for"}),
    _tool("play_youtube", "Find a song or video on YouTube and play it right away. Use this when the user asks to play "
                          "a song, music, or video on YouTube, or when no music service is named.",
          {"query": "The song or video name, plus the artist if the user said one, e.g. odoriko"}),
    _tool("close_tabs", "Close browser tabs (websites) whose title or address contains the given words. Use this for "
                        "'close youtube', 'close the odoriko tab', 'close the extension tab'. To close several "
                        "different tabs, call this once for each tab. If the user wants only one of several "
                        "matching tabs, set which.",
          {"match": "A few words from the tab's title or website, e.g. youtube, odoriko, youtube one republic, extensions",
           "which": "Leave empty to close every match. Use 'one' when the user wants only one of several matching "
                    "tabs, 'first' for the oldest one, 'last' for the newest one"},
          optional=("which",)),
    _tool("count_tabs", "Say how many browser tabs are open, optionally only the ones matching a word.",
          {"match": "Optional short word from the tab's title or website, e.g. youtube"}, optional=("match",)),
    _tool("quit_jarvis", "Shut down and exit Jarvis himself, this assistant program. Use it when the user tells Jarvis "
                         "to shut down, shut yourself down, shut your system, quit, exit, turn off, or go to sleep. "
                         "It does NOT turn off the computer and does NOT close any other app."),
]

TOOLS = {
    "open_app": open_app,
    "close_app": close_app,
    "open_project": open_project,
    "get_time": get_time,
    "open_website": open_website,
    "search_web": search_web,
    "play_youtube": play_youtube,
    "close_tabs": close_tabs,
    "count_tabs": count_tabs,
    "quit_jarvis": quit_jarvis,
}


SYSTEM_HINTS = []      # each skill can add a line telling the model when to use its tools


def _load_skills():
    """Every .py file in the skills folder adds its own tools. Delete a file to remove that ability."""
    folder = Path(__file__).parent / "skills"
    for file in sorted(folder.glob("*.py")):
        if file.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"skills.{file.stem}")
            TOOL_SCHEMAS.extend(module.SCHEMAS)
            TOOLS.update(module.TOOLS)
            hint = getattr(module, "HINT", "")
            if hint:
                SYSTEM_HINTS.append(hint)
            print(f"[skills] loaded {file.stem}")
        except Exception as e:
            print(f"[skills] could not load {file.name}: {e}")


_load_skills()


def run_tool(name, arguments_json, utterance=""):
    """Run a tool the model asked for. Wrong or missing arguments never crash it or get read out as Python errors."""
    import inspect
    global _utterance
    _utterance = utterance or ""
    func = TOOLS.get(name)
    if not func:
        return f"Unknown tool: {name}"
    try:
        args = json.loads(arguments_json or "{}")
        if not isinstance(args, dict):
            args = {}
    except ValueError:
        args = {}
    allowed = inspect.signature(func).parameters
    args = {key: value for key, value in args.items() if key in allowed}      # ignore arguments the tool doesn't have
    try:
        return func(**args)
    except TypeError as e:
        print(f"[tool] {name} got the wrong arguments: {e}")
        return "I couldn't catch the details for that, sir. Could you say it again?"
    except Exception as e:
        print(f"[tool] {name} failed: {e}")
        return f"{name} failed: {e}"