"""Application settings — API keys and shared credentials, edited in the UI.

Every value resolves the same way: a value saved in the UI wins, then the
environment (including anything loaded from a `.env` file), then the default
below. So `.env` still works for people who like it, but nobody has to open a
text editor and restart the server to change a key.

The model provider fields (LLM_*) decide which LLM answers questions; `llm.py`
reads them. ANTHROPIC_API_KEY and CLAUDE_MODEL are kept from before this app
supported more than Claude, so upgrades keep working untouched.

Values live in the `settings` table of data/app.db, next to the per-source
credentials the Sources tab already stores. Secrets never travel back to the
browser: the API returns MASK for anything marked secret, and MASK coming back
in means "leave it alone" (an empty string means "clear it and fall back to the
environment").
"""
import os

from . import db

MASK = "••••••••"

# group -> what the group is for; rendered as sections in the Settings dialog.
GROUPS = {
    "model": {
        "label": "Model provider",
        "blurb": "Which LLM answers questions. Claude, OpenAI, Gemini, any OpenAI-compatible "
                 "endpoint, or an open-source model running on this machine under Ollama. "
                 "Until one is configured the console can't answer anything.",
    },
    "shared": {
        "label": "Shared credentials",
        "blurb": "Optional fallbacks used when a data source leaves the matching field blank. "
                 "Setting them per-source on the Sources tab is usually clearer.",
    },
}

# Kept in step with llm.PROVIDERS by hand — settings can't import llm (llm imports
# settings), and these are only the labels the picker shows.
PROVIDER_OPTIONS = [
    {"value": "anthropic", "label": "Claude (Anthropic)"},
    {"value": "openai", "label": "OpenAI"},
    {"value": "google", "label": "Google Gemini"},
    {"value": "ollama", "label": "Ollama — local open-source models"},
    {"value": "openai_compatible", "label": "OpenAI-compatible endpoint"},
]

FIELDS = [
    {
        "key": "LLM_PROVIDER", "group": "model", "label": "Provider",
        "kind": "select", "options": PROVIDER_OPTIONS, "default": "anthropic",
        "help": "Pick Ollama to run an open-source model (Qwen3, Llama, Mistral) on this machine "
                "with no API key and no data leaving it. Pick OpenAI-compatible for Azure OpenAI, "
                "vLLM, LM Studio, llama.cpp, Groq, Together, OpenRouter, DeepSeek or a private gateway.",
    },
    {
        "key": "LLM_MODEL", "group": "model", "label": "Model",
        "kind": "text", "placeholder": "claude-opus-5 · gpt-4o · gemini-2.5-pro · qwen3:8b",
        "help": "Leave blank for the provider's default. Use List models to ask the endpoint "
                "what it actually serves.",
    },
    {
        "key": "ANTHROPIC_API_KEY", "group": "model", "label": "Claude API key",
        "kind": "password", "secret": True, "placeholder": "sk-ant-…",
        "help": "Only used when the provider is Claude. console.anthropic.com → API keys. Leave blank "
                "to fall back to the ANTHROPIC_API_KEY environment variable or an `ant auth login` session.",
    },
    {
        "key": "LLM_API_KEY", "group": "model", "label": "API key (other providers)",
        "kind": "password", "secret": True,
        "help": "The key for OpenAI, Gemini or your own endpoint. Leave blank for a local Ollama — "
                "it doesn't use one.",
    },
    {
        "key": "LLM_BASE_URL", "group": "model", "label": "Base URL",
        "kind": "text", "placeholder": "http://localhost:11434",
        "help": "Only needed for Ollama (default http://localhost:11434) and OpenAI-compatible "
                "endpoints — e.g. http://localhost:8000/v1 for vLLM, or the full Azure OpenAI "
                "…/openai/deployments/<deployment>/chat/completions?api-version=… URL.",
    },
    {
        "key": "LLM_TOOL_MODE", "group": "model", "label": "Tool calling",
        "kind": "select", "options": ["auto", "native", "prompted"], "default": "auto",
        "help": "The bot works by calling tools. auto — use the endpoint's native tool calling and "
                "fall back to the prompted JSON protocol if it refuses. native — require it. "
                "prompted — always use the JSON protocol, for small local models with no tool support.",
    },
    {
        "key": "LLM_TIMEOUT", "group": "model", "label": "Request timeout (seconds)",
        "kind": "text", "default": "600", "placeholder": "600",
        "help": "A small model on a CPU-only machine can take minutes to answer. Raise this if "
                "local replies are being cut off.",
    },
    {
        "key": "HUBSPOT_TOKEN", "group": "shared", "label": "HubSpot private app token",
        "kind": "password", "secret": True,
        "help": "Used by any HubSpot source with no token of its own.",
    },
    {
        "key": "HUBSPOT_TICKET_SYNC", "group": "shared", "label": "Commit tickets to HubSpot",
        "kind": "select", "options": ["auto", "manual", "off"], "default": "auto",
        "help": "auto — push every create and update as it happens. manual — only when you press "
                "Push on a ticket. off — never. Tickets are always kept locally and exported to "
                "data/exports/tickets.csv regardless.",
    },
    {
        "key": "MS_TENANT_ID", "group": "shared", "label": "Microsoft tenant ID", "kind": "text",
    },
    {
        "key": "MS_CLIENT_ID", "group": "shared", "label": "Microsoft client ID", "kind": "text",
    },
    {
        "key": "MS_CLIENT_SECRET", "group": "shared", "label": "Microsoft client secret",
        "kind": "password", "secret": True,
        "help": "Entra ID app registration with Mail.Read and Mail.Send application permissions.",
    },
    {
        "key": "MS_MAILBOX", "group": "shared", "label": "Default mailbox",
        "kind": "text", "placeholder": "success@yourcompany.com",
        "help": "Used whenever a request doesn't name a mailbox.",
    },
    {
        "key": "MS_MAILBOXES", "group": "shared", "label": "Additional mailboxes",
        "kind": "textarea", "placeholder": "renewals@yourcompany.com, escalations@yourcompany.com",
        "help": "Comma-separated (or one per line). Listing any here turns the set into an "
                "allowlist: only these and the default mailbox can be read. Leave blank and "
                "every mailbox the app registration can reach is available.",
    },
]

BY_KEY = {f["key"]: f for f in FIELDS}

_cache: dict[str, str] | None = None


def _stored() -> dict[str, str]:
    global _cache
    if _cache is None:
        _cache = db.all_settings()
    return _cache


def invalidate() -> None:
    global _cache
    _cache = None


def value(key: str) -> str:
    """UI value, else environment, else declared default."""
    stored = _stored().get(key)
    if stored:
        return stored
    return os.environ.get(key) or BY_KEY.get(key, {}).get("default", "")


def source_of(key: str) -> str:
    """Where the effective value came from: ui | env | default | unset."""
    if _stored().get(key):
        return "ui"
    if os.environ.get(key):
        return "env"
    return "default" if BY_KEY.get(key, {}).get("default") else "unset"


def llm_ready() -> bool:
    """The configured provider has what it needs — the bot has a fair chance of working."""
    from . import llm   # imported here: llm reads this module at import time

    return llm.configured()


def public() -> dict:
    """Settings shaped for the UI, with secrets masked."""
    meta = db.settings_meta()
    fields = []
    for field in FIELDS:
        key = field["key"]
        raw = value(key)
        origin = source_of(key)
        fields.append({
            **{k: v for k, v in field.items() if k != "secret"},
            "value": (MASK if field.get("secret") and raw else "" if field.get("secret") else raw),
            "configured": bool(raw),
            "source": origin,
            "env_present": bool(os.environ.get(key)),
            "updated_by": meta.get(key, {}).get("updated_by", ""),
            "updated_at": meta.get(key, {}).get("updated_at", ""),
        })
    from . import llm   # imported here: llm reads this module at import time

    return {
        "groups": [{"key": k, **v} for k, v in GROUPS.items()],
        "fields": fields,
        "llm_ready": llm.configured(),
        "llm": llm.describe(),
        "providers": llm.catalog(),
    }


def save(values: dict, updated_by: str = "") -> dict:
    """Apply UI edits. MASK means unchanged; "" clears the override."""
    unknown = [k for k in values if k not in BY_KEY]
    if unknown:
        raise ValueError(f"Unknown setting: {', '.join(sorted(unknown))}")

    for key, new in values.items():
        field = BY_KEY[key]
        new = (new or "").strip()
        if field.get("secret") and new == MASK:
            continue
        if new:
            db.set_setting(key, new, updated_by)
        else:
            db.delete_setting(key)   # fall back to the environment again
    invalidate()
    return public()
