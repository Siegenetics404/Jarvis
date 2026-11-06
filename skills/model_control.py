"""Ask which model Jarvis is using, and open the model window by voice.

  "Which model are you using?"        "Change the model"        "Load a model"        "Open model settings"
"""
import subprocess
import sys
from pathlib import Path

import jarvis_llm as llm
from skills import tool

ROOT = Path(__file__).resolve().parent.parent
_window = None            # the model window opened by voice, so it isn't opened twice


def model_status():
    state = llm.status()
    if not state["reachable"]:
        online = llm.online_summary()
        return ("I can't reach LM Studio, sir. " + online) if online else "I can't reach LM Studio, sir. Is its server running?"
    if not state["chat"] or state["chat"] == llm.FALLBACK_NAME and not state["loaded"]:
        return "No model is loaded in LM Studio, sir. Say load a model, and I'll open the model window."
    how = "the one you chose" if state["mode"] == "fixed" else "whatever is loaded"
    text = f"I'm running on {state['chat']}, sir, which is {how} in LM Studio."
    if state["tools"] is False:
        text += " It doesn't support tools, so I may not be able to act on your requests. Try another model."
    roles = state.get("roles") or {}
    if roles.get("coder"):
        text += f" For code I use {roles['code']}."
    else:
        text += " I have no coding model loaded, so my main model handles code too."
    if state["vision"]:
        text += f" For pictures I use {state['vision']}."
    online = llm.online_summary()
    if online:
        text += " " + online
    return text


def open_model_settings(tab=""):
    global _window
    if _window is not None and _window.poll() is None:
        return "The model window is already open, sir."
    command = [sys.executable, str(ROOT / "model_setup.py")] + (["--tab", "online"] if tab == "online" else [])
    try:
        _window = subprocess.Popen(command, cwd=str(ROOT))
    except Exception as e:
        print("[model] couldn't open the model window:", e)
        return "I couldn't open the model window, sir. Try running model underscore setup dot py."
    return "Opening the online models, sir." if tab == "online" else "Opening the model window, sir."


def online_mode(action=""):
    """'Go offline' keeps everything on this PC. 'Go online' lets online models help again."""
    wanted = str(action or "").strip().lower() not in ("off", "offline", "local", "false", "no")
    if wanted and not llm.settings()["providers"]:
        return "You haven't added any online models yet, sir. Say add an online model, and I'll open the window."
    llm.set_online(wanted)
    return ("Online models are on, sir." if wanted else "Going offline, sir. Everything stays on this PC.")


HINT = ("Model: use model_status when the user asks which model or brain you are using, or which models are loaded; "
        "use open_model_settings when they want to change, switch or load a model, or to add an online model or API "
        "key (tab online); use online_mode when they say go offline, stay local, or go online.")

SCHEMAS = [
    tool("model_status", "Say which language models Jarvis is running on right now, local and online."),
    tool("open_model_settings", "Open the window where the user chooses and loads models, or adds an online model "
         "such as Gemini, Claude or OpenAI.",
         {"tab": "Say 'online' to open the online models tab"}, optional=("tab",)),
    tool("online_mode", "Switch online models on or off. 'Go offline' keeps everything on this PC.",
         {"action": "on or off"}, enums={"action": ["on", "off"]}),
]

TOOLS = {
    "model_status": model_status,
    "open_model_settings": open_model_settings,
    "online_mode": online_mode,
}