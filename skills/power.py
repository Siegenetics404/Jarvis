"""Shut down, restart, sleep or sign out of the PC. Always behind a confirmation (see safety.py).

Shutdown and restart wait 20 seconds before they happen. Say "stop" (or double-click the orb) in that time
and he cancels it.
"""
import subprocess
import time

import safety
from skills import tool

DELAY = 20                         # seconds between "okay" and the PC actually going off
_scheduled = 0.0                   # when a shutdown or restart was last scheduled
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(command):
    subprocess.run(command, creationflags=_NO_WINDOW, check=False, capture_output=True)


def counting_down():
    """True while a shutdown or restart can still be cancelled."""
    return time.monotonic() - _scheduled < DELAY


def abort_pending():
    """Cancel a scheduled shutdown or restart. Returns what to say, or '' if nothing was scheduled."""
    global _scheduled
    if not counting_down():
        return ""
    _scheduled = 0.0
    _run(["shutdown", "/a"])
    return "Cancelled, sir. Nothing will be switched off."


def power_action(action=""):
    global _scheduled
    if not safety.approved:                       # the language model can't run this on its own
        return "That needs your confirmation first, sir."
    action = (action or "").strip().lower().replace(" ", "_").replace("-", "_")
    action = {"shut_down": "shutdown", "poweroff": "shutdown", "power_off": "shutdown", "reboot": "restart",
              "signout": "sign_out", "log_out": "sign_out", "logout": "sign_out",
              "logoff": "sign_out", "log_off": "sign_out"}.get(action, action)
    if action == "shutdown":
        _run(["shutdown", "/s", "/t", str(DELAY)])
        _scheduled = time.monotonic()
        return f"Shutting down in {DELAY} seconds, sir. Say stop to cancel."
    if action == "restart":
        _run(["shutdown", "/r", "/t", str(DELAY)])
        _scheduled = time.monotonic()
        return f"Restarting in {DELAY} seconds, sir. Say stop to cancel."
    if action == "sleep":
        _run(["powershell", "-NoProfile", "-Command",
              "Add-Type -AssemblyName System.Windows.Forms; "
              "[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)"])
        return "Goodnight, sir."
    if action == "sign_out":
        _run(["shutdown", "/l"])
        return "Signing out, sir."
    return "I can shut down, restart, sleep, or sign out, sir. Which one?"


HINT = ("Power: use power_action only when the user clearly says the computer, PC, laptop or Windows, "
        "for example 'shut down the computer', 'restart my PC', 'put the computer to sleep', 'sign out'. "
        "Plain 'shut down', 'turn off' or 'go to sleep' with no computer named means YOU should use quit_jarvis. "
        "power_action asks the user to confirm by itself.")

SCHEMAS = [
    tool("power_action",
         "Shut down, restart, sleep, or sign out of the whole computer. Only when the user clearly says the "
         "computer, PC, laptop or Windows. It always asks the user to confirm first.",
         {"action": "One of: shutdown, restart, sleep, sign_out"},
         enums={"action": ["shutdown", "restart", "sleep", "sign_out"]}),
]

TOOLS = {
    "power_action": power_action,
}