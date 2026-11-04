"""Talking to LM Studio without hard-coding a model, and picking the right model for the job.

Everything in Jarvis that needs a language model goes through this file.

  ROLES: each kind of work has its own model.
    chat    fast conversation and tool calling (an "instruct" model is best)
    code    writing, fixing and explaining code (a "coder" model is best)
    vision  looking at pictures (a vision model)

  Automatic (the default): he looks at what is loaded in LM Studio and gives each role the best model there.
  A coder model is recognised by its name. Load a different one in LM Studio and he follows within seconds.
  You can also pin a model to a role in the model window ("Load model..." in the panel settings).

  FALLBACK: every role has an ordered list of models. If the first one fails (not loaded, too slow, no tool
  support, empty answer) he moves to the next, and remembers for a minute which one was failing.
  The coding role ends with the chat model, so coding still works with a single model loaded.

Settings are saved in data/model_settings.json. The model window (model_setup.py) writes them for you.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from openai import APIConnectionError, APITimeoutError, OpenAI

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / "data" / "model_settings.json"
DEFAULTS = {
    "lm_url": "http://localhost:1234/v1",     # where LM Studio's server listens
    "mode": "auto",                           # chat role: "auto" = whatever is loaded, "fixed" = the model below
    "model": "",                              # the chat model when mode is "fixed"
    "vision": "auto",                         # "auto", "off", or the name of a vision model
    "code": "auto",                           # "auto" = a loaded coder model, "off" = use the chat model, or a name
    "fallback": True,                         # try the next model when one fails
    "code_on_demand": False,                  # let LM Studio load the coder when it is needed (slow, can unload others)
}
FALLBACK_NAME = "local-model"                 # what LM Studio accepts when nothing better is known
CACHE_SECONDS = 4                             # how long the list of models is remembered
REQUEST_TIMEOUT = 120
BAD_SECONDS = 60                              # how long a model that just failed is tried last

_VISION_HINTS = ("-vl", "vl-", "vision", "llava", "pixtral", "minicpm-v", "gemma-3", "internvl", "moondream",
                 "smolvlm", "qwen2.5-vl", "qwen3-vl", "llama-3.2-11b", "llama-3.2-90b")
_CODER_HINTS = ("coder", "codestral", "starcoder", "deepseek-coder", "codellama", "code-llama", "devstral",
                "codegemma", "granite-code", "opencoder", "wizardcoder", "yi-coder", "codeqwen")


class ModelError(Exception):
    """Base for problems getting an answer from a model."""


class LMStudioDown(ModelError):
    """LM Studio itself can't be reached, so trying other models would not help."""


class AllModelsFailed(ModelError):
    def __init__(self, failures):
        self.failures = failures
        super().__init__("; ".join(f"{name}: {str(error)[:80]}" for name, error in failures) or "no model available")


_cache = {"time": 0.0, "url": "", "models": [], "typed": False, "error": ""}
_clients = {}
_bad = {}                                    # model name -> time until which it is tried last
last_used = {"role": "", "model": "", "fell_back": False}


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


def is_coder(model_id):
    lowered = (model_id or "").lower()
    return any(hint in lowered for hint in _CODER_HINTS)


def _guess_type(model_id):
    lowered = model_id.lower()
    if "embed" in lowered:
        return "embeddings"
    return "vlm" if any(hint in lowered for hint in _VISION_HINTS) else "llm"


def list_models(refresh=False):
    """Every model LM Studio knows, as dicts: id, type ('llm', 'vlm', 'embeddings'), loaded, tools (True/False/None
    when unknown), coder, context, quant. Remembered for a few seconds. [] when LM Studio can't be reached."""
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
            model_id = entry.get("id", "")
            models.append({
                "id": model_id,
                "type": entry.get("type") or _guess_type(model_id),
                "loaded": entry.get("state") == "loaded",
                "tools": ("tool_use" in capabilities) if isinstance(capabilities, list) else None,
                "coder": is_coder(model_id),
                "context": entry.get("loaded_context_length") or entry.get("max_context_length"),
                "quant": entry.get("quantization") or "",
            })
    except Exception:
        try:                                                  # the standard list: only has what is loaded
            for entry in _fetch(url + "/models").get("data", []):
                model_id = entry.get("id", "")
                models.append({"id": model_id, "type": _guess_type(model_id), "loaded": True, "tools": None,
                               "coder": is_coder(model_id), "context": None, "quant": ""})
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


# ------------------------------------------------------------------ roles and fallback chains

def mark_bad(model_id, seconds=BAD_SECONDS):
    _bad[model_id] = time.monotonic() + seconds


def _is_bad(model_id):
    return _bad.get(model_id, 0) > time.monotonic()


def _rank_general(models):
    """Conversation models first: ones that use tools, then text models before vision models."""
    return sorted(models, key=lambda m: (0 if m["tools"] else (1 if m["tools"] is None else 2),
                                         1 if m["type"] == "vlm" else 0))


def _dedupe(names):
    seen, out = set(), []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _chat_candidates(s):
    loaded = loaded_chat_models()
    general = [m["id"] for m in _rank_general([m for m in loaded if not m["coder"]])]
    coders = [m["id"] for m in loaded if m["coder"]]
    names = []
    if s["mode"] == "fixed" and s["model"]:
        names.append(s["model"])
    names += general + coders                                 # a coder is a last resort for chat, better than nothing
    names = _dedupe(names)
    return names if s["fallback"] else names[:1]


def _code_candidates(s):
    loaded = loaded_chat_models()
    names = []
    if s["code"] == "off":
        return _chat_candidates(s)
    pinned = s["code"] if s["code"] not in ("auto", "") else None
    if pinned and (any(m["id"] == pinned and m["loaded"] for m in loaded) or s["code_on_demand"]):
        names.append(pinned)                                  # a coder you pinned
    names += [m["id"] for m in loaded if m["coder"]]          # then any other loaded coder
    if not names and s["code_on_demand"]:                     # none loaded: let LM Studio load one if there is one
        names += [m["id"] for m in list_models() if m["coder"] and m["type"] != "embeddings"][:1]
    if s["fallback"] or not names:
        names += _chat_candidates(s)
    names = _dedupe(names)
    return names if s["fallback"] else names[:1]


def candidates(role="chat"):
    """The models to try for this role, best first. Models that just failed go last."""
    s = settings()
    if role == "vision":
        found = vision_model()
        names = [found] if found else []
    elif role == "code":
        names = _code_candidates(s)
    else:
        names = _chat_candidates(s)
    return [n for n in names if not _is_bad(n)] + [n for n in names if _is_bad(n)]


def chat_model():
    """The model name to put in a request for conversation."""
    names = candidates("chat")
    return names[0] if names else FALLBACK_NAME


def code_model():
    """The model that handles coding, or None when there is no model at all."""
    names = candidates("code")
    return names[0] if names else None


def has_coder():
    """True when a model made for code is available for the coding role."""
    first = code_model()
    return bool(first and is_coder(first))


def vision_model():
    """A model that can look at pictures, or None (then screen reading falls back to reading the text)."""
    s = settings()
    if s["vision"] == "off":
        return None
    if s["vision"] not in ("auto", ""):
        return s["vision"]
    seeing = [m for m in loaded_chat_models() if m["type"] == "vlm"]
    return seeing[0]["id"] if seeing else None


def _empty(response):
    try:
        message = response.choices[0].message
        return not (message.tool_calls or (message.content or "").strip())
    except Exception:
        return True


def complete(messages, role="chat", tools=None, force_tool=False, max_tokens=None, temperature=None, timeout=None):
    """Ask the best model for the role, falling back to the next one if it fails.
    Returns (response, model that answered). Raises LMStudioDown or AllModelsFailed."""
    names = candidates(role)
    if not names:
        if not reachable():
            raise LMStudioDown("LM Studio can't be reached")
        raise AllModelsFailed([(role, "no model is loaded")])
    failures = []
    for position, name in enumerate(names):
        kwargs = {"model": name, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature
        link = client().with_options(timeout=timeout) if timeout else client()
        try:
            try:
                if force_tool and tools:
                    response = link.chat.completions.create(tool_choice="required", **kwargs)
                else:
                    response = link.chat.completions.create(**kwargs)
            except (APITimeoutError, APIConnectionError):
                raise
            except Exception:
                if not (force_tool and tools):
                    raise
                response = link.chat.completions.create(**kwargs)      # this model can't be forced: ask normally
        except APITimeoutError:
            failures.append((name, "too slow"))
            mark_bad(name)
            continue
        except APIConnectionError as e:
            raise LMStudioDown(str(e)) from e
        except Exception as e:
            failures.append((name, e))
            mark_bad(name)
            continue
        if tools and _empty(response):
            failures.append((name, "empty answer"))
            mark_bad(name, 20)
            continue
        last_used.update(role=role, model=name, fell_back=position > 0)
        if position > 0:
            print(f"[model] {role}: {names[0]} failed ({failures[0][1]}), used {name} instead")
        return response, name
    raise AllModelsFailed(failures)


# ------------------------------------------------------------------ plain words about it

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


def roles():
    """Which model each role would use right now: {'chat': id|None, 'code': id|None, 'coder': bool, 'vision': id|None}."""
    chat = candidates("chat")
    code = candidates("code")
    return {"chat": chat[0] if chat else None, "code": code[0] if code else None,
            "coder": bool(code and is_coder(code[0])), "vision": vision_model()}


def status():
    """Facts for the status question and the model window."""
    chosen = chat_model()
    entry = next((m for m in list_models() if m["id"] == chosen), None)
    return {"reachable": reachable(), "chat": chosen if (entry or settings()["mode"] == "fixed") else None,
            "mode": settings()["mode"], "tools": entry["tools"] if entry else None,
            "vision": vision_model(), "loaded": [m["id"] for m in loaded_chat_models()], "roles": roles()}


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
    return False, seconds, ("It answers, but didn't use the tool. Jarvis needs a model that supports tool use "
                            "for conversation, but it is fine for coding.")