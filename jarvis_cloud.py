"""Online models for Jarvis: Gemini, Claude, OpenAI, OpenRouter, Groq and others.

This file only knows how to TALK to a provider. jarvis_llm.py decides when to use one.

  - Most providers speak the OpenAI protocol, so one adapter covers Gemini, OpenAI, OpenRouter, Groq, Mistral,
    DeepSeek and any custom OpenAI-compatible address.
  - Claude uses Anthropic's own Messages protocol, so it has its own small adapter. Both give back the same shape
    of answer, so the rest of Jarvis can't tell the difference.
  - API keys are kept in Windows' password store when the 'keyring' package is installed, otherwise in the .env
    file. They are never printed, logged or put in data/model_settings.json.
  - A daily request limit per provider protects you from a surprise bill if something loops.
"""
import json
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
from openai import (APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, OpenAI,
                    PermissionDeniedError, RateLimitError)

import email_config

ROOT = Path(__file__).resolve().parent
USAGE_FILE = ROOT / "data" / "online_usage.json"
KEYRING_SERVICE = "jarvis-llm"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = 60
DEFAULT_DAILY_LIMIT = 200

# What the "Add online model" window offers. Model names change often, so these are only suggestions:
# type any model name your provider lists, or press "Fetch models".
PRESETS = {
    "gemini": {"label": "Google Gemini", "protocol": "openai",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "key_url": "https://aistudio.google.com/apikey",
               "models": ["gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-flash"],
               "note": "Has a free tier with daily limits. Create a key in Google AI Studio."},
    "anthropic": {"label": "Anthropic Claude", "protocol": "anthropic",
                  "base_url": "https://api.anthropic.com/v1",
                  "key_url": "https://console.anthropic.com/settings/keys",
                  "models": ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"],
                  "note": "Paid. Create a key in the Anthropic Console. Haiku is fast, Sonnet is better for code."},
    "openai": {"label": "OpenAI", "protocol": "openai", "base_url": "https://api.openai.com/v1",
               "key_url": "https://platform.openai.com/api-keys", "models": [],
               "note": "Paid. Create a key on the OpenAI platform, then press Fetch models."},
    "openrouter": {"label": "OpenRouter", "protocol": "openai", "base_url": "https://openrouter.ai/api/v1",
                   "key_url": "https://openrouter.ai/keys", "models": [],
                   "note": "One key for many models, including some free ones. Press Fetch models."},
    "groq": {"label": "Groq", "protocol": "openai", "base_url": "https://api.groq.com/openai/v1",
             "key_url": "https://console.groq.com/keys", "models": [],
             "note": "Very fast, with a free tier. Press Fetch models."},
    "mistral": {"label": "Mistral", "protocol": "openai", "base_url": "https://api.mistral.ai/v1",
                "key_url": "https://console.mistral.ai/api-keys", "models": [],
                "note": "Press Fetch models after adding your key."},
    "deepseek": {"label": "DeepSeek", "protocol": "openai", "base_url": "https://api.deepseek.com/v1",
                 "key_url": "https://platform.deepseek.com/api_keys", "models": [],
                 "note": "Press Fetch models after adding your key."},
    "custom": {"label": "Other (OpenAI-compatible)", "protocol": "openai", "base_url": "", "key_url": "",
               "models": [], "note": "Any service that speaks the OpenAI protocol. Enter its address below."},
}
ROLE_NAMES = {"chat": "conversation", "code": "coding", "vision": "pictures"}


class CloudError(Exception):
    """kind: auth, rate, timeout, network, server, request."""

    def __init__(self, kind, message, status=None):
        super().__init__(message)
        self.kind = kind
        self.status = status


# ------------------------------------------------------------------ API keys

def _env_name(provider_id):
    return "JARVIS_KEY_" + "".join(c if c.isalnum() else "_" for c in provider_id.upper())


def _keyring():
    try:
        import keyring
        return keyring
    except Exception:
        return None


def get_key(provider_id):
    store = _keyring()
    if store:
        try:
            found = store.get_password(KEYRING_SERVICE, provider_id)
            if found:
                return found.strip()
        except Exception:
            pass
    return email_config.read_env().get(_env_name(provider_id), "").strip()


def has_key(provider_id):
    return bool(get_key(provider_id))


def set_key(provider_id, key):
    """Returns where it went: 'keyring' or 'env'."""
    key = (key or "").strip()
    store = _keyring()
    if store:
        try:
            store.set_password(KEYRING_SERVICE, provider_id, key)
            email_config.write_env({}, remove=(_env_name(provider_id),))
            return "keyring"
        except Exception:
            pass
    email_config.write_env({_env_name(provider_id): key})
    return "env"


def delete_key(provider_id):
    store = _keyring()
    if store:
        try:
            store.delete_password(KEYRING_SERVICE, provider_id)
        except Exception:
            pass
    email_config.write_env({}, remove=(_env_name(provider_id),))


# ------------------------------------------------------------------ daily limits

def _read_usage():
    try:
        return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def usage_today(provider_id):
    """(requests, tokens) used today."""
    day = _read_usage().get(date.today().isoformat(), {}).get(provider_id, {})
    return day.get("requests", 0), day.get("tokens", 0)


def record_usage(provider_id, tokens=0):
    data = _read_usage()
    today = date.today().isoformat()
    entry = data.setdefault(today, {}).setdefault(provider_id, {"requests": 0, "tokens": 0})
    entry["requests"] += 1
    entry["tokens"] += int(tokens or 0)
    for old in sorted(data)[:-30]:                          # keep a month
        data.pop(old, None)
    try:
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        USAGE_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def under_limit(provider):
    limit = int(provider.get("daily_limit", DEFAULT_DAILY_LIMIT) or 0)
    return limit <= 0 or usage_today(provider["id"])[0] < limit


# ------------------------------------------------------------------ talking to providers

def _base_url(provider):
    preset = PRESETS.get(provider.get("kind"), PRESETS["custom"])
    return (provider.get("base_url") or preset["base_url"]).strip()


def _protocol(provider):
    return PRESETS.get(provider.get("kind"), PRESETS["custom"])["protocol"]


def _text_of(content):
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return content or ""


def _anthropic_content(content):
    """OpenAI-style message content -> Anthropic content blocks."""
    if not isinstance(content, list):
        return content or ""
    blocks = []
    for part in content:
        if part.get("type") == "text":
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if url.startswith("data:") and ";base64," in url:
                header, data = url.split(";base64,", 1)
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": header[5:], "data": data}})
            else:
                raise CloudError("request", "Claude needs the picture as embedded data")
    return blocks


def _anthropic_body(provider, messages, tools, force_tool, max_tokens, temperature):
    system, turns = [], []
    for message in messages:
        role = message.get("role")
        if role == "system":
            system.append(_text_of(message.get("content")))
        elif role in ("user", "assistant"):
            content = _anthropic_content(message.get("content"))
            if turns and turns[-1]["role"] == role:            # Claude wants the two sides to alternate
                previous = turns[-1]["content"]
                if isinstance(previous, str) and isinstance(content, str):
                    turns[-1]["content"] = previous + "\n\n" + content
                else:
                    to_blocks = lambda c: c if isinstance(c, list) else [{"type": "text", "text": c}]
                    turns[-1]["content"] = to_blocks(previous) + to_blocks(content)
            else:
                turns.append({"role": role, "content": content})
    if turns and turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": "(start)"})
    body = {"model": provider["model"], "max_tokens": max_tokens or 1024, "messages": turns}
    if system:
        body["system"] = "\n\n".join(system)
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                          "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
                         for t in tools]
        if force_tool:
            body["tool_choice"] = {"type": "any"}
    return body


def _anthropic_reply(data):
    """Anthropic's answer -> the same shape the OpenAI library gives."""
    text, calls = [], []
    for block in data.get("content", []):
        if block.get("type") == "text":
            text.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            calls.append(SimpleNamespace(id=block.get("id", ""), type="function", function=SimpleNamespace(
                name=block.get("name", ""), arguments=json.dumps(block.get("input", {})))))
    usage = data.get("usage", {})
    used = SimpleNamespace(prompt_tokens=usage.get("input_tokens", 0), completion_tokens=usage.get("output_tokens", 0),
                           total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
    message = SimpleNamespace(content="".join(text) or None, tool_calls=calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=data.get("stop_reason"))],
                           usage=used, model=data.get("model", ""))


def _status_error(status, message):
    if status in (401, 403):
        return CloudError("auth", message, status)
    if status in (429, 529):
        return CloudError("rate", message, status)
    if status >= 500:
        return CloudError("server", message, status)
    return CloudError("request", message, status)


def _anthropic_headers(key):
    return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}


def _chat_anthropic(provider, key, messages, tools, force_tool, max_tokens, temperature, timeout):
    body = _anthropic_body(provider, messages, tools, force_tool, max_tokens, temperature)
    try:
        reply = httpx.post(_base_url(provider).rstrip("/") + "/messages", headers=_anthropic_headers(key),
                           json=body, timeout=timeout)
    except httpx.TimeoutException as e:
        raise CloudError("timeout", "too slow") from e
    except httpx.RequestError as e:
        raise CloudError("network", "can't reach the internet") from e
    if reply.status_code >= 400:
        try:
            detail = reply.json().get("error", {}).get("message", reply.text)
        except ValueError:
            detail = reply.text
        raise _status_error(reply.status_code, str(detail)[:200])
    return _anthropic_reply(reply.json())


def _chat_openai(provider, key, messages, tools, force_tool, max_tokens, temperature, timeout):
    client = OpenAI(base_url=_base_url(provider), api_key=key, timeout=timeout)
    kwargs = {"model": provider["model"], "messages": messages}
    if tools:
        kwargs["tools"] = tools
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature
    try:
        try:
            if force_tool and tools:
                return client.chat.completions.create(tool_choice="required", **kwargs)
            return client.chat.completions.create(**kwargs)
        except (AuthenticationError, PermissionDeniedError, RateLimitError, APITimeoutError, APIConnectionError):
            raise
        except APIStatusError:
            if not (force_tool and tools):
                raise
            return client.chat.completions.create(**kwargs)          # this provider can't force a tool: ask normally
    except (AuthenticationError, PermissionDeniedError) as e:
        raise CloudError("auth", str(e)[:200], 401) from e
    except RateLimitError as e:
        raise CloudError("rate", str(e)[:200], 429) from e
    except APITimeoutError as e:
        raise CloudError("timeout", "too slow") from e
    except APIConnectionError as e:
        raise CloudError("network", "can't reach the internet") from e
    except APIStatusError as e:
        raise _status_error(getattr(e, "status_code", 400), str(e)[:200]) from e


def chat(provider, messages, tools=None, force_tool=False, max_tokens=None, temperature=None, timeout=None):
    """One request to an online provider. Returns an answer shaped like the OpenAI library's. Raises CloudError."""
    key = get_key(provider["id"])
    if not key:
        raise CloudError("auth", "no API key saved")
    if not provider.get("model"):
        raise CloudError("request", "no model name set")
    timeout = timeout or DEFAULT_TIMEOUT
    if _protocol(provider) == "anthropic":
        response = _chat_anthropic(provider, key, messages, tools, force_tool, max_tokens, temperature, timeout)
    else:
        response = _chat_openai(provider, key, messages, tools, force_tool, max_tokens, temperature, timeout)
    tokens = getattr(getattr(response, "usage", None), "total_tokens", 0) or 0
    record_usage(provider["id"], tokens)
    return response


# ------------------------------------------------------------------ for the settings window

def list_models(provider, key=None):
    """The model names this provider offers (needs a working key). Raises CloudError."""
    key = key or get_key(provider.get("id", ""))
    if not key:
        raise CloudError("auth", "enter the API key first")
    base = _base_url(provider).rstrip("/")
    headers = _anthropic_headers(key) if _protocol(provider) == "anthropic" else {"Authorization": f"Bearer {key}"}
    try:
        reply = httpx.get(base + "/models", headers=headers, timeout=20)
    except httpx.TimeoutException as e:
        raise CloudError("timeout", "too slow") from e
    except httpx.RequestError as e:
        raise CloudError("network", "can't reach the internet") from e
    if reply.status_code >= 400:
        raise _status_error(reply.status_code, reply.text[:160])
    names = []
    for entry in reply.json().get("data", []):
        name = str(entry.get("id", "")).removeprefix("models/")
        if name and "embed" not in name.lower():
            names.append(name)
    return sorted(names)


_TEST_TOOL = {"type": "function", "function": {
    "name": "get_number", "description": "Returns the number the user asked for.",
    "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}}}


def test_provider(provider, key=None):
    """Tries the provider with a tiny request. Returns (ok, message). Uses your key, so it counts as one request."""
    provider = dict(provider)
    provider.setdefault("id", "test")
    started = time.monotonic()
    try:
        if key:                                                         # test a key that isn't saved yet
            saved = get_key(provider["id"])
            set_key(provider["id"], key)
        try:
            reply = chat(provider, [{"role": "user", "content": "Use the get_number tool with n=7."}],
                         tools=[_TEST_TOOL], max_tokens=80, temperature=0)
        finally:
            if key and not saved:
                delete_key(provider["id"])
            elif key and saved != key:
                set_key(provider["id"], saved)
    except CloudError as e:
        reasons = {"auth": "The key was refused. Check it, and that billing is on if the provider needs it.",
                   "rate": "Reached the provider's limit. For a free key, try again later.",
                   "timeout": "It took too long to answer.", "network": "Can't reach the internet.",
                   "server": "The provider has a problem right now.",
                   "request": "The provider rejected the request. Check the model name."}
        return False, f"{reasons.get(e.kind, 'Failed.')} ({str(e)[:100]})"
    seconds = time.monotonic() - started
    if reply.choices[0].message.tool_calls:
        return True, f"Works, and it can use tools. Answered in {seconds:.1f} seconds."
    return True, f"Works ({seconds:.1f} seconds), but it didn't use the test tool, so it may be weak at actions."