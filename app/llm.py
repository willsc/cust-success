"""Provider-agnostic LLM access — one interface, many backends.

The bot talks only to `complete()`; every provider is a translation layer under
it: Claude (the official SDK), OpenAI, any OpenAI-compatible endpoint (Azure
OpenAI, vLLM, LM Studio, llama.cpp, Groq, Together, OpenRouter, DeepSeek,
Qwen/DashScope...), Google Gemini, and locally hosted open-source models served
by Ollama — Qwen3, Llama, Mistral, Phi and friends.

Conversation history stays in the Anthropic content-block shape, which is what
`db.get_conversation` already holds, and each provider converts to and from it.
So switching providers doesn't corrupt stored history, and nothing else in the
app has to know which model is answering.

Tool calling is the one capability that genuinely varies. Providers with native
tool/function calling use it; anything that doesn't — plenty of small local
models — falls back to a prompted protocol where the model writes its call as
JSON and we parse it back out. LLM_TOOL_MODE picks: `auto` tries native and
drops to prompted the first time an endpoint rejects tools.

Everything here is httpx (already a base dependency) plus the Claude SDK, so
adding a provider never means another install — which matters on the Windows
boxes this ships to, where a compiler is not a given.
"""
import json
import os
import re
import uuid
from dataclasses import dataclass, field

import httpx

from . import settings

# provider key -> how to reach it and what to show in Settings.
PROVIDERS = {
    "anthropic": {
        "label": "Claude (Anthropic)",
        "blurb": "Anthropic's API. Native tool use, the best results for this agent.",
        "default_model": "claude-opus-5",
        "default_base_url": "",
        "needs_key": True,
        "key_hint": "console.anthropic.com → API keys",
        "model_hint": "claude-opus-5",
        "native_tools": True,
    },
    "openai": {
        "label": "OpenAI",
        "blurb": "api.openai.com. Native function calling.",
        "default_model": "gpt-4o",
        "default_base_url": "https://api.openai.com/v1",
        "needs_key": True,
        "key_hint": "platform.openai.com → API keys",
        "model_hint": "gpt-4o",
        "native_tools": True,
    },
    "google": {
        "label": "Google Gemini",
        "blurb": "Gemini API (generativelanguage.googleapis.com). Native function calling.",
        "default_model": "gemini-2.5-pro",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "needs_key": True,
        "key_hint": "aistudio.google.com → API key",
        "model_hint": "gemini-2.5-pro",
        "native_tools": True,
    },
    "ollama": {
        "label": "Ollama (local open-source models)",
        "blurb": "Runs models on this machine — Qwen3, Llama, Mistral, Phi. "
                 "Install Ollama for Windows, `ollama pull qwen3:8b`, and leave the key blank.",
        "default_model": "qwen3:8b",
        "default_base_url": "http://localhost:11434",
        "needs_key": False,
        "key_hint": "Not needed for a local Ollama.",
        "model_hint": "qwen3:8b",
        "native_tools": True,
    },
    "openai_compatible": {
        "label": "OpenAI-compatible endpoint",
        "blurb": "Anything speaking the /chat/completions API: Azure OpenAI, vLLM, LM Studio, "
                 "llama.cpp, Groq, Together, OpenRouter, DeepSeek, Qwen/DashScope, a private gateway.",
        "default_model": "",
        "default_base_url": "",
        "needs_key": False,
        "key_hint": "Whatever that endpoint expects; sent as both `Authorization: Bearer` and `api-key`.",
        "model_hint": "Qwen/Qwen3-8B",
        "native_tools": True,
    },
}

DEFAULT_PROVIDER = "anthropic"
MAX_TOKENS = 8000

# Endpoints we've caught rejecting a tools payload this run — in `auto` mode we
# stop asking and use the prompted protocol. Keyed by provider+model+URL, so
# pointing at a model that does support tools tries natively again.
_prompted_only: set = set()


class LLMError(RuntimeError):
    """Anything the user needs to fix: bad key, unreachable endpoint, unknown model."""


class ToolsUnsupported(LLMError):
    """The endpoint took the request but won't accept tool definitions."""


@dataclass
class Reply:
    """One model turn, in the canonical (Anthropic-shaped) form we persist."""
    content: list = field(default_factory=list)   # blocks to append as the assistant turn
    tool_calls: list = field(default_factory=list)  # [{"id","name","input"}]
    text: str = ""
    stop_reason: str = "end_turn"


# ---------- configuration ----------

def provider() -> str:
    key = (settings.value("LLM_PROVIDER") or "").strip().lower()
    return key if key in PROVIDERS else DEFAULT_PROVIDER


def spec() -> dict:
    return PROVIDERS[provider()]


def model() -> str:
    """Configured model, else the pre-multi-provider CLAUDE_MODEL, else the provider default."""
    name = settings.value("LLM_MODEL").strip()
    if name:
        return name
    if provider() == "anthropic":
        legacy = settings.value("CLAUDE_MODEL").strip()
        if legacy:
            return legacy
    return spec()["default_model"]


def base_url() -> str:
    return (settings.value("LLM_BASE_URL").strip() or spec()["default_base_url"]).rstrip("/")


def api_key() -> str:
    """The Claude key keeps its own field so existing installs carry on working."""
    if provider() == "anthropic":
        return settings.value("ANTHROPIC_API_KEY").strip() or settings.value("LLM_API_KEY").strip()
    return settings.value("LLM_API_KEY").strip()


def tool_mode() -> str:
    mode = (settings.value("LLM_TOOL_MODE") or "auto").strip().lower()
    return mode if mode in ("auto", "native", "prompted") else "auto"


def timeout() -> float:
    """Generous by default: an 8B model on a CPU-only laptop is not quick."""
    try:
        value = float(settings.value("LLM_TIMEOUT") or 600)
    except ValueError:
        return 600.0
    return max(30.0, min(value, 3600.0))


def configured() -> bool:
    """Enough is set that a request has a fair chance of working."""
    if not spec()["needs_key"]:
        return bool(base_url() or provider() == "ollama")
    if provider() == "anthropic":
        return bool(api_key() or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return bool(api_key())


def describe() -> dict:
    """What the UI shows next to the provider picker."""
    return {
        "provider": provider(),
        "label": spec()["label"],
        "model": model(),
        "base_url": base_url(),
        "tool_mode": tool_mode(),
        "ready": configured(),
    }


def catalog() -> list:
    return [{"key": k, **{f: v for f, v in p.items() if f != "native_tools"}} for k, p in PROVIDERS.items()]


# ---------- public entry points ----------

def _endpoint() -> tuple:
    """What "this endpoint" means for the tool-support memo above."""
    return (provider(), model(), base_url())


def complete(system: str, messages: list, tools: list, max_tokens: int = MAX_TOKENS) -> Reply:
    """One model turn. Raises LLMError with something the user can act on."""
    mode = tool_mode()
    native = _NATIVE[provider()]

    if not tools:
        return native(system, messages, None, max_tokens)

    if mode == "native" or (mode == "auto" and _endpoint() not in _prompted_only):
        try:
            return native(system, messages, tools, max_tokens)
        except ToolsUnsupported:
            if mode == "native":
                raise
            _prompted_only.add(_endpoint())   # don't pay for the rejection twice

    return _prompted_complete(native, system, messages, tools, max_tokens)


def test_connection() -> dict:
    """Cheapest round trip there is, so Settings can verify before anyone relies on it."""
    name, target = provider(), model()
    if not target:
        return {"ok": False, "message": "No model set. Enter the model name your endpoint serves."}
    if not configured():
        return {"ok": False, "message": _missing_config()}
    try:
        reply = _NATIVE[name](
            "Reply with the single word OK.",
            [{"role": "user", "content": "ping"}], None, 16)
    except LLMError as exc:
        return {"ok": False, "message": str(exc)[:400]}
    except Exception as exc:  # network stack, DNS, TLS
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"[:400]}
    answered = (reply.text or "").strip() or "(no text)"
    return {"ok": True,
            "message": f"{PROVIDERS[name]['label']} answered — {target} is reachable. Said: {answered[:60]}"}


def list_models() -> dict:
    """Best-effort model list, so the Model box can suggest instead of guess."""
    name = provider()
    try:
        if name == "ollama":
            data = _get(f"{base_url()}/api/tags", {})
            names = [m.get("name", "") for m in data.get("models", [])]
        elif name == "google":
            data = _get(f"{base_url()}/models", {}, params={"key": api_key()})
            names = [m.get("name", "").removeprefix("models/") for m in data.get("models", [])]
        elif name == "anthropic":
            names = [m.id for m in _anthropic_client().models.list(limit=40)]
        else:
            data = _get(f"{base_url()}/models", _openai_headers())
            names = [m.get("id", "") for m in data.get("data", [])]
    except LLMError as exc:
        return {"ok": False, "provider": name, "models": [], "message": str(exc)[:300]}
    except Exception as exc:
        return {"ok": False, "provider": name, "models": [],
                "message": f"{type(exc).__name__}: {exc}"[:300]}
    names = sorted({n for n in names if n})
    return {"ok": True, "provider": name, "models": names,
            "message": f"{len(names)} model(s) available." if names else "The endpoint listed no models."}


def _missing_config() -> str:
    p = spec()
    if p["needs_key"]:
        return (f"No API key configured for {p['label']}. Open Settings (the gear in the top bar) "
                f"and add one — {p['key_hint']}")
    return (f"{p['label']} needs a base URL. Open Settings (the gear in the top bar) and point it at "
            f"your endpoint.")


def auth_hint() -> str:
    """What to tell the user when the provider turns the request down."""
    p = spec()
    if not configured():
        return _missing_config()
    return (f"{p['label']} rejected the request. Check the key, model name and base URL in Settings "
            f"(the gear in the top bar).")


# ---------- shared HTTP ----------

def _post(url: str, headers: dict, payload: dict) -> dict:
    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=timeout())
    except httpx.TimeoutException:
        raise LLMError(f"{spec()['label']} timed out after {int(timeout())}s. Local models can be slow — "
                       f"raise the request timeout in Settings, or use a smaller model.")
    except httpx.RequestError as exc:
        raise LLMError(f"Can't reach {url}: {exc}. Is the endpoint running?")
    if response.status_code >= 400:
        detail = _error_detail(response)
        if _looks_like_tools_rejected(response.status_code, detail):
            raise ToolsUnsupported(detail)
        raise LLMError(f"{spec()['label']} returned {response.status_code}: {detail}")
    return response.json()


def _get(url: str, headers: dict, params: dict | None = None) -> dict:
    try:
        response = httpx.get(url, headers=headers, params=params, timeout=min(timeout(), 60))
    except httpx.RequestError as exc:
        raise LLMError(f"Can't reach {url}: {exc}")
    if response.status_code >= 400:
        raise LLMError(f"{response.status_code}: {_error_detail(response)}")
    return response.json()


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    for path in (("error", "message"), ("error",), ("message",), ("detail",)):
        value = body
        for step in path:
            value = value.get(step) if isinstance(value, dict) else None
        if isinstance(value, str) and value:
            return value[:300]
    return json.dumps(body)[:300]


def _looks_like_tools_rejected(status: int, detail: str) -> bool:
    """Tell 'this model has no tool support' apart from 'your key is wrong'."""
    if status not in (400, 404, 422, 500, 501):
        return False
    text = detail.lower()
    return ("tool" in text or "function" in text) and any(
        w in text for w in ("not support", "unsupported", "does not", "unknown", "invalid", "unrecognized"))


THINK_TAGS = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)


def _clean(text: str) -> str:
    """Reasoning models (Qwen3 and friends) narrate inside <think> tags. Not for the user."""
    return THINK_TAGS.sub("", text or "").strip()


def _result_text(content) -> str:
    """A tool_result's content, flattened to the string every non-Anthropic API wants."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return json.dumps(content)


def _blocks(reply_text: str, calls: list) -> list:
    """Canonical assistant content: text first, then any tool_use blocks."""
    blocks = []
    if reply_text:
        blocks.append({"type": "text", "text": reply_text})
    for call in calls:
        blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["input"]})
    return blocks


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------- Claude ----------

_anthropic_cache: tuple | None = None


def _anthropic_client():
    """Rebuilt when Settings changes the key. With no key we still hand back a
    default client: the SDK can pick up an `ant auth login` session or
    ANTHROPIC_AUTH_TOKEN on its own."""
    global _anthropic_cache
    import anthropic

    key = api_key()
    url = base_url()
    if _anthropic_cache is None or _anthropic_cache[0] != (key, url):
        kwargs = {}
        if key:
            kwargs["api_key"] = key
        if url:
            kwargs["base_url"] = url
        _anthropic_cache = ((key, url), anthropic.Anthropic(**kwargs))
    return _anthropic_cache[1]


def _anthropic_complete(system: str, messages: list, tools: list | None, max_tokens: int) -> Reply:
    import anthropic

    kwargs = {
        "model": model(),
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    try:
        response = _anthropic_client().messages.create(**kwargs)
    except anthropic.NotFoundError:
        raise LLMError(f"No such model: {model()}.")
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise LLMError(auth_hint())
    except TypeError as exc:  # the SDK's shape when it resolves no credentials at all
        if "authentication" in str(exc).lower():
            raise LLMError(auth_hint())
        raise
    except anthropic.APIError as exc:
        raise LLMError(f"Claude: {exc}"[:400])

    content = [block.model_dump(exclude_none=True) for block in response.content]
    calls = [{"id": b.id, "name": b.name, "input": dict(b.input)}
             for b in response.content if b.type == "tool_use"]
    text = "\n".join(b.text for b in response.content if b.type == "text")
    return Reply(content=content, tool_calls=calls, text=text,
                 stop_reason=response.stop_reason or "end_turn")


# ---------- OpenAI and every OpenAI-compatible endpoint ----------

def _openai_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    key = api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["api-key"] = key   # Azure OpenAI wants this one
    return headers


def _openai_url() -> str:
    """Honour a base URL that already names the endpoint — that's how Azure's
    /openai/deployments/<name>/chat/completions?api-version=... gets in."""
    url = base_url()
    if not url:
        raise LLMError("No base URL set for the OpenAI-compatible endpoint. Add one in Settings.")
    return url if "/chat/completions" in url else f"{url}/chat/completions"


def _to_openai_messages(system: str, messages: list, native_tools: bool) -> list:
    out: list = [{"role": "system", "content": system}]
    for message in messages:
        role, content = message.get("role", "user"), message.get("content", "")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if role == "assistant":
            text = "\n".join(b["text"] for b in content if b.get("type") == "text")
            calls = [b for b in content if b.get("type") == "tool_use"]
            if calls and native_tools:
                out.append({
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [{"id": b["id"], "type": "function",
                                    "function": {"name": b["name"],
                                                 "arguments": json.dumps(b.get("input") or {})}}
                                   for b in calls],
                })
            else:
                # Prompted mode: replay the call as the JSON the model itself wrote.
                parts = [text] + [json.dumps({"tool": b["name"], "input": b.get("input") or {}})
                                  for b in calls]
                out.append({"role": "assistant", "content": "\n".join(p for p in parts if p)})
            continue

        texts = []
        for block in content:
            if block.get("type") == "tool_result":
                body = _result_text(block.get("content"))
                if native_tools:
                    out.append({"role": "tool", "tool_call_id": block.get("tool_use_id", ""),
                                "content": body})
                else:
                    texts.append(f"Tool result:\n{body}")
            elif block.get("type") == "text":
                texts.append(block["text"])
        if texts:
            out.append({"role": "user", "content": "\n\n".join(texts)})
    return out


def _openai_tools(tools: list) -> list:
    return [{"type": "function",
             "function": {"name": t["name"], "description": t.get("description", ""),
                          "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
            for t in tools]


def _openai_complete(system: str, messages: list, tools: list | None, max_tokens: int) -> Reply:
    payload = {
        "model": model(),
        "messages": _to_openai_messages(system, messages, native_tools=bool(tools)),
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        payload["tools"] = _openai_tools(tools)
        payload["tool_choice"] = "auto"

    data = _post(_openai_url(), _openai_headers(), payload)
    choices = data.get("choices") or []
    if not choices:
        raise LLMError(f"{spec()['label']} returned no choices: {json.dumps(data)[:200]}")
    message = choices[0].get("message") or {}
    text = _clean(message.get("content") or "")
    calls = [{"id": c.get("id") or f"call_{uuid.uuid4().hex[:12]}",
              "name": (c.get("function") or {}).get("name", ""),
              "input": _parse_args((c.get("function") or {}).get("arguments"))}
             for c in (message.get("tool_calls") or [])]
    stop = "tool_use" if calls else (choices[0].get("finish_reason") or "end_turn")
    return Reply(content=_blocks(text, calls), tool_calls=calls, text=text, stop_reason=stop)


# ---------- Ollama (local open-source models) ----------

def _ollama_complete(system: str, messages: list, tools: list | None, max_tokens: int) -> Reply:
    payload = {
        "model": model(),
        "messages": _to_ollama_messages(system, messages, native_tools=bool(tools)),
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if tools:
        payload["tools"] = _openai_tools(tools)   # Ollama uses the OpenAI function shape

    url = base_url() or PROVIDERS["ollama"]["default_base_url"]
    data = _post(f"{url}/api/chat", {"Content-Type": "application/json"}, payload)
    message = data.get("message") or {}
    text = _clean(message.get("content") or "")
    calls = [{"id": f"call_{uuid.uuid4().hex[:12]}",
              "name": (c.get("function") or {}).get("name", ""),
              "input": _parse_args((c.get("function") or {}).get("arguments"))}
             for c in (message.get("tool_calls") or [])]
    stop = "tool_use" if calls else (data.get("done_reason") or "end_turn")
    return Reply(content=_blocks(text, calls), tool_calls=calls, text=text, stop_reason=stop)


def _to_ollama_messages(system: str, messages: list, native_tools: bool) -> list:
    """Same shape as OpenAI, minus tool_call_ids — Ollama matches results by order."""
    out = []
    for message in _to_openai_messages(system, messages, native_tools):
        if message.get("role") == "tool":
            out.append({"role": "tool", "content": message.get("content") or ""})
            continue
        converted = {"role": message["role"], "content": message.get("content") or ""}
        if message.get("tool_calls"):
            converted["tool_calls"] = [
                {"function": {"name": c["function"]["name"],
                              "arguments": _parse_args(c["function"]["arguments"])}}
                for c in message["tool_calls"]]
        out.append(converted)
    return out


# ---------- Google Gemini ----------

GEMINI_SCHEMA_KEYS = {"type", "description", "enum", "properties", "required", "items", "nullable"}


def _gemini_schema(schema: dict) -> dict:
    """Gemini takes an OpenAPI subset and rejects the rest of JSON Schema."""
    if not isinstance(schema, dict):
        return {"type": "string"}
    out = {k: v for k, v in schema.items() if k in GEMINI_SCHEMA_KEYS}
    if "properties" in out:
        out["properties"] = {k: _gemini_schema(v) for k, v in out["properties"].items()}
    if "items" in out:
        out["items"] = _gemini_schema(out["items"])
    out.setdefault("type", "object" if "properties" in out else "string")
    return out


def _google_complete(system: str, messages: list, tools: list | None, max_tokens: int) -> Reply:
    contents, names = [], {}
    for message in messages:
        role = "model" if message.get("role") == "assistant" else "user"
        content = message.get("content", "")
        if isinstance(content, str):
            contents.append({"role": role, "parts": [{"text": content}]})
            continue
        parts = []
        for block in content:
            kind = block.get("type")
            if kind == "text":
                parts.append({"text": block["text"]})
            elif kind == "tool_use":
                names[block["id"]] = block["name"]
                parts.append({"functionCall": {"name": block["name"], "args": block.get("input") or {}}})
            elif kind == "tool_result":
                parts.append({"functionResponse": {
                    "name": names.get(block.get("tool_use_id"), "tool"),
                    "response": {"result": _result_text(block.get("content"))}}})
        if parts:
            contents.append({"role": role, "parts": parts})

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if tools:
        payload["tools"] = [{"functionDeclarations": [
            {"name": t["name"], "description": t.get("description", ""),
             "parameters": _gemini_schema(t.get("input_schema") or {})} for t in tools]}]

    key = api_key()
    url = f"{base_url()}/models/{model()}:generateContent"
    data = _post(f"{url}?key={key}", {"Content-Type": "application/json"}, payload)

    candidates = data.get("candidates") or []
    if not candidates:
        raise LLMError(f"Gemini returned no candidates: {json.dumps(data)[:200]}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = _clean("\n".join(p["text"] for p in parts if "text" in p))
    calls = [{"id": f"call_{uuid.uuid4().hex[:12]}",
              "name": p["functionCall"].get("name", ""),
              "input": p["functionCall"].get("args") or {}}
             for p in parts if "functionCall" in p]
    stop = "tool_use" if calls else (candidates[0].get("finishReason") or "end_turn")
    return Reply(content=_blocks(text, calls), tool_calls=calls, text=text, stop_reason=stop)


_NATIVE = {
    "anthropic": _anthropic_complete,
    "openai": _openai_complete,
    "openai_compatible": _openai_complete,
    "ollama": _ollama_complete,
    "google": _google_complete,
}


# ---------- prompted tool calling, for models without native tool use ----------

PROMPTED_PROTOCOL = """

# Calling tools
You have tools available. They are listed below as JSON, each with its name, what it
does, and the JSON Schema its input must satisfy.

To call a tool, reply with NOTHING but a single fenced JSON block:

```json
{{"tool": "<tool name>", "input": {{...}}}}
```

Rules:
- One tool per reply. Wait for the result before calling the next one.
- No prose, no explanation and no second block in a reply that calls a tool.
- The result comes back in the next message as "Tool result:". Read it, then either
  call another tool the same way or answer the user.
- When you are answering the user, write plain prose with no JSON block at all.

Tools:
{tools}
"""

FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _prompted_complete(native, system: str, messages: list, tools: list, max_tokens: int) -> Reply:
    catalogue = json.dumps(
        [{"name": t["name"], "description": t.get("description", ""),
          "input_schema": t.get("input_schema", {})} for t in tools], indent=2)
    reply = native(system + PROMPTED_PROTOCOL.format(tools=catalogue), messages, None, max_tokens)

    call = _extract_call(reply.text, {t["name"] for t in tools})
    if not call:
        return reply

    name, arguments, span = call
    remainder = (reply.text[:span[0]] + reply.text[span[1]:]).strip()
    calls = [{"id": f"call_{uuid.uuid4().hex[:12]}", "name": name, "input": arguments}]
    return Reply(content=_blocks(remainder, calls), tool_calls=calls,
                 text=remainder, stop_reason="tool_use")


def _extract_call(text: str, valid: set) -> tuple | None:
    """Find the model's tool call: a fenced block first, then any bare JSON object."""
    for match in FENCED.finditer(text or ""):
        parsed = _as_call(match.group(1), valid)
        if parsed:
            return parsed[0], parsed[1], match.span()
    for start in (m.start() for m in re.finditer(r"\{", text or "")):
        chunk = _balanced(text, start)
        if not chunk:
            continue
        parsed = _as_call(chunk, valid)
        if parsed:
            return parsed[0], parsed[1], (start, start + len(chunk))
    return None


def _as_call(chunk: str, valid: set) -> tuple | None:
    try:
        body = json.loads(chunk)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    name = body.get("tool") or body.get("name") or body.get("tool_name")
    if not isinstance(name, str) or name not in valid:
        return None
    arguments = body.get("input", body.get("arguments", body.get("parameters", {})))
    return name, arguments if isinstance(arguments, dict) else {}


def _balanced(text: str, start: int) -> str | None:
    """The JSON object starting at `start`, brace-matched outside of strings."""
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None
