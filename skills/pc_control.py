"""PC control: volume, brightness, lock screen, screenshots, dark mode and a quick status check.
Windows only. No extra packages needed."""
import ctypes
import os
import re
import shutil
import subprocess
import time
import winreg
from pathlib import Path

from skills import tool

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

KEYEVENTF_KEYUP = 0x0002
VK_LWIN = 0x5B
VK_SNAPSHOT = 0x2C          # Print Screen
VK_D = 0x44
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF


def _tap(vk, times=1):
    """Press a key a number of times, like on a keyboard."""
    for _ in range(times):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.004)


def _combo(*keys):
    """Hold the keys down together, then let go, like Win+D."""
    for vk in keys:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(keys):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


_NUMBER_WORDS = {"zero": 0, "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
                 "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
                 "half": 50, "max": 100, "maximum": 100, "full": 100}


def _percent(text, default=None):
    """Read a 0 to 100 number from what was said, like '40', '40%' or 'fifty'."""
    text = (text or "").lower()
    digits = re.search(r"\d+", text)
    if digits:
        return max(0, min(100, int(digits.group())))
    for word, value in _NUMBER_WORDS.items():
        if word in text:
            return value
    return default


# ---------------------------------------------------------------- volume

def volume(action, level=""):
    action = (action or "").strip().lower()

    if action in ("up", "down"):
        step = _percent(level, 10)                       # Windows moves 2 percent per key press
        _tap(VK_VOLUME_UP if action == "up" else VK_VOLUME_DOWN, max(1, round(step / 2)))
        return f"Volume {action}, sir."

    if action == "set":
        target = _percent(level)
        if target is None:
            return "What volume level, sir?"
        _tap(VK_VOLUME_DOWN, 50)                         # all the way down...
        _tap(VK_VOLUME_UP, round(target / 2))            # ...then up to the level
        return f"Volume set to {target} percent, sir."

    if action in ("mute", "unmute"):
        _tap(VK_VOLUME_DOWN)                             # any volume key unmutes, so we start from a known state
        _tap(VK_VOLUME_UP)
        if action == "mute":
            _tap(VK_VOLUME_MUTE)
            return "Muted, sir."
        return "Sound is back on, sir."

    return "I don't know that volume command, sir."


# ------------------------------------------------------------- brightness

def _powershell(script):
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, errors="replace", timeout=20,
    )


def _get_brightness():
    try:
        out = _powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness").stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = out.strip().splitlines()
    return int(lines[0]) if lines and lines[0].strip().isdigit() else None


def _set_brightness(value):
    script = ("Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
              f"Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=[uint32]1; Brightness=[byte]{value}}}")
    try:
        return _powershell(script).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def brightness(action, level=""):
    action = (action or "").strip().lower()
    current = _get_brightness()
    if current is None:
        return "I couldn't reach the screen brightness, sir. That only works on laptop screens."

    if action == "up":
        target = current + _percent(level, 20)
    elif action == "down":
        target = current - _percent(level, 20)
    elif action == "set":
        target = _percent(level)
        if target is None:
            return "What brightness level, sir?"
    else:
        return "I don't know that brightness command, sir."

    target = max(5, min(100, target))                    # never fully black, that looks like a crash
    if not _set_brightness(target):
        return "I couldn't change the brightness, sir."
    return f"Brightness at {target} percent, sir."


# ------------------------------------------------------------- quick actions

def _lock():
    user32.LockWorkStation()
    return "Locking the screen, sir."


def _screenshot():
    folders = [Path.home() / "Pictures" / "Screenshots",
               Path.home() / "OneDrive" / "Pictures" / "Screenshots"]
    started = time.time() - 1
    _combo(VK_LWIN, VK_SNAPSHOT)                         # Win + Print Screen saves a file by itself
    deadline = time.time() + 4
    while time.time() < deadline:
        for folder in folders:
            if folder.exists() and any(f.stat().st_mtime >= started for f in folder.glob("*.png")):
                return "Screenshot saved in your Pictures, Screenshots folder, sir."
        time.sleep(0.4)
    return "I pressed the screenshot keys, sir. Check your Screenshots folder."


def _snip():
    try:
        os.startfile("ms-screenclip:")                   # the same overlay as Win + Shift + S
    except OSError:
        return "I couldn't open the snipping tool, sir."
    return "Drag over the part of the screen you want, sir."


def _show_desktop():
    _combo(VK_LWIN, VK_D)
    return "Desktop, sir."


def _set_theme(light):
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                         0, winreg.KEY_SET_VALUE)
    for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1 if light else 0)
    winreg.CloseKey(key)
    try:                                                 # tell open windows to refresh right away
        user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, ctypes.c_wchar_p("ImmersiveColorSet"),
                                   0x2, 5000, ctypes.byref(ctypes.c_ulong()))
    except Exception:
        pass
    return "Light mode, sir." if light else "Dark mode, sir."


class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def _battery():
    status = _PowerStatus()
    kernel32.GetSystemPowerStatus(ctypes.byref(status))
    if status.BatteryFlag == 128 or status.BatteryLifePercent == 255:
        return "This computer has no battery I can read, sir."
    plugged = status.ACLineStatus == 1
    text = f"Battery is at {status.BatteryLifePercent} percent, and {'charging' if plugged else 'on battery'}"
    if not plugged and status.BatteryLifeTime != 0xFFFFFFFF:
        hours, minutes = divmod(status.BatteryLifeTime // 60, 60)
        text += f", about {hours} hours {minutes} minutes left"
    return text + ", sir."


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _memory():
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    total = status.ullTotalPhys / 1024 ** 3
    used = total - status.ullAvailPhys / 1024 ** 3
    return f"Memory is {status.dwMemoryLoad} percent used, {used:.1f} of {total:.0f} gigabytes, sir."


def _disk():
    usage = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
    return f"Your main drive has {usage.free / 1024 ** 3:.0f} gigabytes free of {usage.total / 1024 ** 3:.0f}, sir."


_ACTIONS = {
    "lock": _lock,
    "screenshot": _screenshot,
    "snip": _snip,
    "show_desktop": _show_desktop,
    "dark_mode": lambda: _set_theme(light=False),
    "light_mode": lambda: _set_theme(light=True),
    "battery": _battery,
    "memory": _memory,
    "disk": _disk,
}


def pc_action(action):
    key = (action or "").strip().lower().replace(" ", "_")
    handler = _ACTIONS.get(key)
    if not handler:
        return "I don't know that one, sir."
    return handler()


# ---------------------------------------------------------------- registration

HINT = ("PC control: use volume to change the sound volume, brightness to change the screen brightness, "
        "and pc_action to lock the screen, take a screenshot, snip part of the screen, show the desktop, "
        "switch to dark or light mode, or report the battery, memory or disk space.")

SCHEMAS = [
    tool("volume",
         "Change the computer's sound volume: turn it up or down, set it to a level, mute or unmute.",
         {"action": "up, down, set, mute or unmute",
          "level": "A percent. For up or down: how much to change by (default 10). For set: the volume to set, 0 to 100."},
         optional=("level",), enums={"action": ["up", "down", "set", "mute", "unmute"]}),
    tool("brightness",
         "Change the screen brightness of a laptop: brighter, dimmer, or set to a level.",
         {"action": "up, down or set",
          "level": "A percent. For up or down: how much to change by (default 20). For set: the brightness to set, 0 to 100."},
         optional=("level",), enums={"action": ["up", "down", "set"]}),
    tool("pc_action",
         "Do a quick action on the computer or check its status: lock the screen, take a screenshot, snip part of "
         "the screen, show the desktop, switch to dark or light mode, or report the battery, memory or disk space.",
         {"action": "lock, screenshot, snip, show_desktop, dark_mode, light_mode, battery, memory or disk"},
         enums={"action": list(_ACTIONS)}),
]

TOOLS = {
    "volume": volume,
    "brightness": brightness,
    "pc_action": pc_action,
}