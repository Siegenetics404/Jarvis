import difflib
import json
import os
import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse

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


def close_app(name):
    apps = find_apps()
    match = best_match(name, list(apps))
    if not match:
        return f"Couldn't find an app like '{name}'"
    target = lnk_target(apps[match])
    if not target.lower().endswith(".exe"):
        return f"Can't tell which process '{match}' runs (shortcut target: '{target}')"
    exe = Path(target).name

    # 1. Is it really running?
    check = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
        capture_output=True, text=True,
    )
    if exe.lower() not in check.stdout.lower():
        return f"{match} isn't running (looked for {exe})"

    # 2. Ask politely
    result = subprocess.run(["taskkill", "/IM", exe, "/T"], capture_output=True, text=True)
    if result.returncode == 0:
        return f"Closing {match}"

    # 3. It refused, so force it
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


def play_youtube(query):
    """Find the best match on YouTube and open that video, which starts playing."""
    try:
        from yt_dlp import YoutubeDL
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch5:{query}", download=False)
        for entry in info.get("entries") or []:
            vid = entry.get("id") or ""
            if len(vid) == 11 and "/shorts/" not in (entry.get("url") or ""):
                title = str(entry.get("title")).encode("ascii", "replace").decode()   # safe for the Windows console
                print(f"[youtube] picked: {title} -> https://www.youtube.com/watch?v={vid}")
                launch_browser(f"https://www.youtube.com/watch?v={vid}")
                return f"Playing {query} on YouTube"
    except Exception as e:
        print("[youtube] lookup failed:", e)
    launch_browser("https://www.youtube.com/results?search_query=" + quote_plus(query))
    return f"I couldn't pick a video, sir, so I opened the YouTube results for {query}"


# ------------------------------------------- tool registry (add new tools here)

def _tool(name, description, params=None):
    """Build the schema the model sees. params = {arg_name: description}."""
    params = params or {}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {k: {"type": "string", "description": v} for k, v in params.items()},
                "required": list(params),
            },
        },
    }


TOOL_SCHEMAS = [
    _tool("open_app", "Open an application installed on the computer",
          {"name": "Name of the app, e.g. docker, ubuntu, chrome"}),
    _tool("close_app", "Close a running application. Use this when the user says close, quit, exit, or stop an app.",
          {"name": "Name of the app, e.g. docker, chrome"}),
    _tool("open_project", "Open a coding project in VS Code by its folder name",
          {"name": "Name of the project folder, e.g. amefil"}),
    _tool("get_time", "Get the current local time and today's date. Use this whenever the user asks what time or day it is."),
    _tool("open_website", "Open a website in the browser. Use this when the user asks to open or go to a site.",
          {"url": "Full web address of the site, e.g. https://github.com or https://laravel.com/docs"}),
    _tool("search_web", "Search the internet in the browser. Use this when the user asks to search, google, or look something up online.",
          {"query": "What to search for"}),
    _tool("play_youtube", "Find a song or video on YouTube and play it right away. Use this when the user asks to play "
                          "a song, music, or video, with or without saying YouTube.",
          {"query": "The song or video name, plus the artist if the user said one, e.g. odoriko"}),
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
    "quit_jarvis": quit_jarvis,
}


def run_tool(name, arguments_json):
    func = TOOLS.get(name)
    if not func:
        return f"Unknown tool: {name}"
    try:
        return func(**json.loads(arguments_json or "{}"))
    except Exception as e:
        return f"{name} failed: {e}"