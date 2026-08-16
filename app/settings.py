"""Application settings — API keys and shared credentials, edited in the UI.

Every value resolves the same way: a value saved in the UI wins, then the
environment (including anything loaded from a `.env` file), then the default
below. So `.env` still works for people who like it, but nobody has to open a
text editor and restart the server to change a key.

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
    "claude": {
        "label": "Claude",
        "blurb": "Powers the chat bot. Without a key the console can't answer anything.",
    },
    "shared": {
        "label": "Shared credentials",
        "blurb": "Optional fallbacks used when a data source leaves the matching field blank. "
                 "Setting them per-source on the Sources tab is usually clearer.",
    },
}

FIELDS = [
    {
        "key": "ANTHROPIC_API_KEY", "group": "claude", "label": "Claude API key",
        "kind": "password", "secret": True, "placeholder": "sk-ant-…",
        "help": "console.anthropic.com → API keys. Leave blank to fall back to the "
                "ANTHROPIC_API_KEY environment variable or an `ant auth login` session.",
    },
    {
        "key": "CLAUDE_MODEL", "group": "claude", "label": "Model",
        "kind": "text", "default": "claude-opus-5", "placeholder": "claude-opus-5",
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


def claude_ready() -> bool:
    """A key is configured somewhere — the bot has a fair chance of working."""
    return bool(value("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


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
    return {
        "groups": [{"key": k, **v} for k, v in GROUPS.items()],
        "fields": fields,
        "claude_ready": claude_ready(),
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
