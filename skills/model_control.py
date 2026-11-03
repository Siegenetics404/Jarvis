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
        return "I can't reach LM Studio, sir. Is its server running?"
    if not state["chat"] or state["chat"] == llm.FALLBACK_NAME and not state["loaded"]:
        return "No model is loaded in LM Studio, sir. Say load a model, and I'll open the model window."
    how = "the one you chose" if state["mode"] == "fixed" else "whatever is loaded"
    text = f"I'm running on {state['chat']}, sir, which is {how} in LM Studio."
    if state["tools"] is False:
        text += " It doesn't support tools, so I may not be able to act on your requests. Try another model."
    if state["vision"]:
        text += f" For pictures I use {state['vision']}."
    return text


def open_model_settings():
    global _window
    if _window is not None and _window.poll() is None:
        return "The model window is already open, sir."
    try:
        _window = subprocess.Popen([sys.executable, str(ROOT / "model_setup.py")], cwd=str(ROOT))
    except Exception as e:
        print("[model] couldn't open the model window:", e)
        return "I couldn't open the model window, sir. Try running model underscore setup dot py."
    return "Opening the model window, sir."


HINT = ("Model: use model_status when the user asks which model or brain you are using; use open_model_settings when "
        "they want to change, switch or load a model.")

SCHEMAS = [
    tool("model_status", "Say which language model Jarvis is running on right now."),
    tool("open_model_settings", "Open the window where the user chooses and loads the language model in LM Studio."),
]

TOOLS = {
    "model_status": model_status,
    "open_model_settings": open_model_settings,
}