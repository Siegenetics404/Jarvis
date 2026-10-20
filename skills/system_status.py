"""System status: CPU load, running Docker containers, a project's git status, and the biggest memory users.
(Battery, memory and disk space live in pc_control.) Windows only, no extra packages."""
import ctypes
import os
import re
import subprocess
import time

from skills import tool

kernel32 = ctypes.windll.kernel32


def _run(command, timeout=20):
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def _join(items):
    """['a', 'b', 'c'] -> 'a, b and c'"""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# ------------------------------------------------------------------- CPU

class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]


def _cpu_times():
    idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
    kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    value = lambda t: (t.high << 32) | t.low
    return value(idle), value(kernel), value(user)


def _cpu():
    """Measure how busy the processor is over about half a second."""
    idle1, kernel1, user1 = _cpu_times()
    time.sleep(0.6)
    idle2, kernel2, user2 = _cpu_times()
    total = (kernel2 - kernel1) + (user2 - user1)        # kernel time already includes idle time
    busy = total - (idle2 - idle1)
    percent = 0 if total <= 0 else round(100 * busy / total)
    return f"CPU is at {percent} percent across {os.cpu_count()} cores, sir."


# ------------------------------------------------------------- containers

def _containers():
    try:
        out = _run(["docker", "ps", "--format", "{{.Names}}"], timeout=15)
    except FileNotFoundError:
        return "I can't find Docker on this computer, sir."
    except subprocess.TimeoutExpired:
        return "Docker isn't running, sir. It didn't answer."
    if out.returncode != 0:
        return "Docker isn't running, sir."
    names = [n.strip() for n in out.stdout.splitlines() if n.strip()]
    if not names:
        return "No containers are running, sir."
    text = _join(names[:5])
    if len(names) > 5:
        text += f", and {len(names) - 5} more"
    return f"{len(names)} container{'s' if len(names) != 1 else ''} running: {text}, sir."


# ------------------------------------------------------------------ git

def _git(project):
    from actions import find_projects, best_match          # imported here to avoid a circular import
    project = (project or "").strip()
    if not project:
        return "Which project, sir?"
    projects = find_projects()
    match = best_match(project, list(projects))
    if not match:
        return f"Couldn't find a project like '{project}'"
    kind, distro, path = projects[match]

    command = ["git", "-C", path, "status", "--porcelain=v1", "-b"]
    if kind == "wsl":                                       # projects inside Ubuntu are read through WSL
        command = ["wsl", "-d", distro, "--"] + command
    try:
        out = _run(command, timeout=30)
    except FileNotFoundError:
        return "I can't find git on this computer, sir."
    except subprocess.TimeoutExpired:
        return "Git took too long to answer, sir."
    if out.returncode != 0:
        return f"I couldn't read git for {match}, sir. It may not be a repository."

    lines = out.stdout.splitlines()
    header = lines[0] if lines and lines[0].startswith("##") else ""
    files = lines[1:] if header else lines
    untracked = sum(1 for line in files if line.startswith("??"))
    changed = len(files) - untracked

    branch = header[3:].split("...")[0].split(" ")[0] if header else "unknown"
    if "No commits yet on" in header:
        branch = header.split()[-1]
    sync = re.search(r"\[(.+?)\]", header)

    if not changed and not untracked:
        state = "clean"
    else:
        parts = []
        if changed:
            parts.append(f"{changed} changed file{'s' if changed != 1 else ''}")
        if untracked:
            parts.append(f"{untracked} untracked")
        state = " and ".join(parts)

    message = f"{match} is on branch {branch}, {state}"
    if sync:
        pretty = re.sub(r"(ahead|behind) (\d+)",
                        lambda m: f"{m.group(2)} {'commit' if m.group(2) == '1' else 'commits'} {m.group(1)}",
                        sync.group(1))
        message += f", {pretty}"
    return message + ", sir."


# ---------------------------------------------------------- memory users

def _top_apps():
    script = ("Get-Process | Group-Object Name | ForEach-Object { "
              "[pscustomobject]@{Name=$_.Name; MB=[int](($_.Group | Measure-Object WorkingSet -Sum).Sum / 1MB)} } | "
              "Sort-Object MB -Descending | Select-Object -First 3 | "
              "ForEach-Object { $_.Name + '=' + $_.MB }")
    try:
        out = _run(["powershell", "-NoProfile", "-Command", script], timeout=25)
    except (OSError, subprocess.TimeoutExpired):
        return "I couldn't read the running programs, sir."
    parts = []
    for line in out.stdout.splitlines():
        if "=" not in line:
            continue
        name, mb = line.rsplit("=", 1)
        if not mb.strip().isdigit():
            continue
        mb = int(mb)
        size = f"{mb / 1024:.1f} gigabytes" if mb >= 1024 else f"{mb} megabytes"
        parts.append(f"{name} {size}")
    if not parts:
        return "I couldn't read the running programs, sir."
    return f"The biggest memory users are {_join(parts)}, sir."


# ---------------------------------------------------------------- registration

def system_status(what, project=""):
    key = (what or "").strip().lower().replace(" ", "_")
    if key == "cpu":
        return _cpu()
    if key == "containers":
        return _containers()
    if key == "git":
        return _git(project)
    if key == "top_apps":
        return _top_apps()
    return "I don't know that status, sir."


HINT = ("System status: use system_status for CPU load, which Docker containers are running, "
        "a coding project's git status (pass the project name), or which apps use the most memory. "
        "Battery, memory and disk space are pc_action.")

SCHEMAS = [
    tool("system_status",
         "Check the computer's status: CPU load, which Docker containers are running, a coding project's "
         "git status, or which apps use the most memory. For battery, memory and disk space use pc_action.",
         {"what": "cpu, containers, git or top_apps",
          "project": "Only for git: the project folder name, e.g. amefil"},
         optional=("project",), enums={"what": ["cpu", "containers", "git", "top_apps"]}),
]

TOOLS = {
    "system_status": system_status,
}