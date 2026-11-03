"""Talking to LM Studio without hard-coding a model.

Everything in Jarvis that needs the language model asks this file, so there is one place to change.

  Automatic (the default): he uses whatever model is loaded in LM Studio right now. Load a different one in
  LM Studio and he follows within a few seconds. No restart, no settings.
  Fixed: you pick one model in the model window ("Load model..." in the panel settings) and he always uses it.

Settings are saved in data/model_settings.json. The model window (model_setup.py) writes them for you.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from openai import OpenAI

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / "data" / "model_settings.json"
DEFAULTS = {
    "lm_url": "http://localhost:1234/v1",     # where LM Studio's server listens
    "mode": "auto",                           # "auto" = whatever is loaded, "fixed" = the model named below
    "model": "",                              # used when mode is "fixed"
    "vision": "auto",                         # "auto", "off", or the name of a vision model, for reading images
}
FALLBACK_NAME = "local-model"                 # what LM Studio accepts when nothing better is known
CACHE_SECONDS = 4                             # how long the list of models is remembered
REQUEST_TIMEOUT = 120

_VISION_HINTS = ("-vl", "vl-", "vision", "llava", "pixtral", "minicpm-v", "gemma-3", "internvl", "moondream",
                 "smolvlm", "qwen2.5-vl", "qwen3-vl", "llama-3.2-11b", "llama-3.2-90b")

_cache = {"time": 0.0, "url": "", "models": [], "typed": False, "error": ""}
_clients = {}


# ------------------------------------------------------------------ settings

def settings():
    values = dict(DEFAULTS)
    try:
        values.update({k: v for k, v in json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).items() if k in DEFAULTS})
    except (OSError, ValueError, AttributeError):
        pass
    values["lm_url"] = (values["lm_url"] or DEFAULTS["lm_url"]).strip().rstrip("/")
    if not values["lm_url"].endswith("/v1"):
        values["lm_url"] += "/v1"
    return values


def save_settings(**changes):
    values = settings()
    values.update({k: v for k, v in changes.items() if k in DEFAULTS})
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(values, indent=2), encoding="utf-8")
    _cache["time"] = 0.0                       # look again next time
    return values


def client():
    """An OpenAI-style client for the LM Studio address in the settings."""
    url = settings()["lm_url"]
    if url not in _clients:
        _clients[url] = OpenAI(base_url=url, api_key="lm-studio", timeout=REQUEST_TIMEOUT)
    return _clients[url]


# ------------------------------------------------------------------ what LM Studio has

def _root(url):
    return url[:-3] if url.endswith("/v1") else url


def _fetch(url, timeout=3):
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _guess_type(model_id):
    lowered = model_id.lower()
    if "embed" in lowered:
        return "embeddings"
    return "vlm" if any(hint in lowered for hint in _VISION_HINTS) else "llm"


def list_models(refresh=False):
    """Every model LM Studio knows, as dicts: id, type ('llm', 'vlm', 'embeddings'), loaded, tools (True/False/None
    when unknown), context, quant. Remembered for a few seconds. Returns [] when LM Studio can't be reached."""
    url = settings()["lm_url"]
    now = time.monotonic()
    if not refresh and _cache["url"] == url and now - _cache["time"] < CACHE_SECONDS:
        return _cache["models"]

    models, typed, error = [], False, ""
    try:                                                      # LM Studio's own list: says what is loaded and what each can do
        data = _fetch(_root(url) + "/api/v0/models").get("data", [])
        typed = True
        for entry in data:
            capabilities = entry.get("capabilities")
            models.append({
                "id": entry.get("id", ""),
                "type": entry.get("type") or _guess_type(entry.get("id", "")),
                "loaded": entry.get("state") == "loaded",
                "tools": ("tool_use" in capabilities) if isinstance(capabilities, list) else None,
                "context": entry.get("loaded_context_length") or entry.get("max_context_length"),
                "quant": entry.get("quantization") or "",
            })
    except Exception:
        try:                                                  # the standard list: only has what is loaded
            for entry in _fetch(url + "/models").get("data", []):
                model_id = entry.get("id", "")
                models.append({"id": model_id, "type": _guess_type(model_id), "loaded": True, "tools": None,
                               "context": None, "quant": ""})
        except (URLError, OSError, ValueError) as e:
            error = str(getattr(e, "reason", e))
            models = []
    _cache.update(time=now, url=url, models=models, typed=typed, error=error)
    return models


def reachable():
    list_models()
    return not _cache["error"]


def loaded_chat_models():
    """Loaded models that can chat (embedding models are left out)."""
    return [m for m in list_models() if m["loaded"] and m["type"] != "embeddings"]


def chat_model():
    """The model name to put in a request."""
    s = settings()
    if s["mode"] == "fixed" and s["model"]:
        return s["model"]
    loaded = loaded_chat_models()
    if not loaded:
        return FALLBACK_NAME
    loaded.sort(key=lambda m: 0 if m["tools"] else (1 if m["tools"] is None else 2))     # prefer ones that use tools
    return loaded[0]["id"]


def vision_model():
    """A model that can look at pictures, or None (then screen reading falls back to reading the text)."""
    s = settings()
    if s["vision"] == "off":
        return None
    if s["vision"] not in ("auto", ""):
        return s["vision"]
    seeing = [m for m in loaded_chat_models() if m["type"] == "vlm"]
    return seeing[0]["id"] if seeing else None


def problem():
    """In plain words: why the language model can't answer right now. '' if it looks fine."""
    s = settings()
    if not reachable():
        return "I can't reach LM Studio, sir. Is its server running?"
    if s["mode"] == "fixed" and s["model"]:
        if not any(m["id"] == s["model"] and m["loaded"] for m in list_models()):
            return f"The model you chose, {s['model']}, isn't loaded in LM Studio, sir."
        return ""
    if not loaded_chat_models():
        return "No model is loaded in LM Studio, sir. Say load a model, and I'll open the model window."
    return ""


def status():
    """Facts for the status question and the model window."""
    chosen = chat_model()
    entry = next((m for m in list_models() if m["id"] == chosen), None)
    return {"reachable": reachable(), "chat": chosen if (entry or settings()["mode"] == "fixed") else None,
            "mode": settings()["mode"], "tools": entry["tools"] if entry else None,
            "vision": vision_model(), "loaded": [m["id"] for m in loaded_chat_models()]}


# ------------------------------------------------------------------ loading and testing

def _lms():
    found = shutil.which("lms")
    if found:
        return found
    candidate = Path.home() / ".lmstudio" / "bin" / "lms.exe"
    return str(candidate) if candidate.exists() else None


def load_model(model_id, replace=True):
    """Load a model in LM Studio. replace=True unloads the others first (a laptop rarely fits two).
    Returns (ok, message)."""
    cli = _lms()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if cli:
            if replace:
                subprocess.run([cli, "unload", "--all"], capture_output=True, timeout=60, creationflags=flags)
            result = subprocess.run([cli, "load", model_id, "--yes"], capture_output=True, text=True, timeout=600,
                                    creationflags=flags)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or ["unknown error"]
                return False, f"LM Studio couldn't load it: {detail[0]}"
        else:                                                 # no command line tool: asking for it makes LM Studio load it
            client().with_options(timeout=600).chat.completions.create(
                model=model_id, messages=[{"role": "user", "content": "hi"}], max_tokens=1)
    except Exception as e:
        return False, f"Couldn't load it: {str(e)[:160]}"
    list_models(refresh=True)
    ok = any(m["id"] == model_id and m["loaded"] for m in list_models())
    return (True, "Loaded.") if ok else (False, "LM Studio accepted the request, but the model isn't showing as loaded yet.")


_TEST_TOOL = {"type": "function", "function": {
    "name": "get_number", "description": "Returns the number the user asked for.",
    "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}}}


def test_model(model_id=None):
    """Asks the model to use a tiny tool. Returns (ok, seconds, message): whether it answers and can call tools."""
    model_id = model_id or chat_model()
    started = time.monotonic()
    try:
        reply = client().chat.completions.create(
            model=model_id, tools=[_TEST_TOOL], max_tokens=80, temperature=0,
            messages=[{"role": "user", "content": "Use the get_number tool with n=7."}])
    except Exception as e:
        return False, time.monotonic() - started, f"No answer: {str(e)[:140]}"
    seconds = time.monotonic() - started
    if reply.choices[0].message.tool_calls:
        return True, seconds, f"Works, and it can use tools. Answered in {seconds:.1f} seconds."
    return False, seconds, ("It answers, but didn't use the tool. Jarvis needs a model that supports tool use, "
                            "or he can only chat. Try another model.")