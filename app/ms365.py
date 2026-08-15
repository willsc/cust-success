"""Microsoft 365 mail via Microsoft Graph (client-credentials app registration).

Falls back to an in-memory demo mailbox when MS_* env vars are not configured,
so the bot's mail tools work end to end without credentials.
"""
import time

import httpx

from . import config

GRAPH = "https://graph.microsoft.com/v1.0"

_token_cache: dict = {}  # cache_key -> {"token": ..., "expires": ...}

DEMO_MESSAGES = [
    {
        "id": "demo-1",
        "subject": "Renewal question - Acme Retail",
        "from": "maya.chen@acmeretail.example",
        "receivedDateTime": "2026-08-14T09:12:00Z",
        "isRead": False,
        "bodyPreview": "Hi team, our contract renews next month. Could you send over the updated pricing tiers?",
        "body": "Hi team,\n\nOur contract renews next month. Could you send over the updated pricing tiers and confirm whether the EU data residency option is included?\n\nThanks,\nMaya",
    },
    {
        "id": "demo-2",
        "subject": "Escalation: export bug blocking month-end close",
        "from": "t.okafor@brightlabs.example",
        "receivedDateTime": "2026-08-15T07:45:00Z",
        "isRead": False,
        "bodyPreview": "The CSV export truncation is now blocking our month-end reporting...",
        "body": "Hello,\n\nThe CSV export truncation issue is now blocking our month-end reporting. We need a fix or workaround by Friday.\n\nTom Okafor\nBright Labs",
    },
]
DEMO_SENT: list[dict] = []


def _settings(source_config: dict | None = None) -> dict:
    """Per-source credentials, falling back to the MS_* environment variables."""
    cfg = source_config or {}
    return {
        "tenant_id": cfg.get("tenant_id") or config.MS_TENANT_ID,
        "client_id": cfg.get("client_id") or config.MS_CLIENT_ID,
        "client_secret": cfg.get("client_secret") or config.MS_CLIENT_SECRET,
        "mailbox": cfg.get("mailbox") or config.MS_MAILBOX,
    }


def configured(source_config: dict | None = None) -> bool:
    s = _settings(source_config)
    return all([s["tenant_id"], s["client_id"], s["client_secret"], s["mailbox"]])


def test_connection(source_config: dict) -> dict:
    if not configured(source_config):
        return {"ok": False, "message": "Credentials incomplete — the bot will use the demo inbox."}
    try:
        result = list_messages(limit=1, source_config=source_config)
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": f"Connected to {_settings(source_config)['mailbox']}."}


def _token(source_config: dict | None = None) -> str:
    s = _settings(source_config)
    cache_key = f"{s['tenant_id']}:{s['client_id']}"
    cached = _token_cache.get(cache_key)
    if cached and time.time() < cached["expires"] - 60:
        return cached["token"]
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"https://login.microsoftonline.com/{s['tenant_id']}/oauth2/v2.0/token",
            data={
                "client_id": s["client_id"],
                "client_secret": s["client_secret"],
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
    resp.raise_for_status()
    data = resp.json()
    _token_cache[cache_key] = {
        "token": data["access_token"],
        "expires": time.time() + int(data.get("expires_in", 3600)),
    }
    return data["access_token"]


def _headers(source_config: dict | None = None) -> dict:
    return {"Authorization": f"Bearer {_token(source_config)}"}


def list_messages(search: str = "", folder: str = "inbox", limit: int = 10,
                  source_config: dict | None = None) -> dict:
    limit = max(1, min(int(limit), 50))
    if not configured(source_config):
        items = DEMO_MESSAGES
        if search:
            s = search.lower()
            items = [m for m in items if s in (m["subject"] + m["from"] + m["body"]).lower()]
        summaries = [{k: m[k] for k in ("id", "subject", "from", "receivedDateTime", "isRead", "bodyPreview")}
                     for m in items[:limit]]
        return {"source": "demo (Microsoft 365 not configured)", "messages": summaries}

    params = {"$top": limit, "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview"}
    if search:
        params["$search"] = f'"{search}"'
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{GRAPH}/users/{_settings(source_config)['mailbox']}/mailFolders/{folder}/messages",
            headers=_headers(source_config), params=params,
        )
    resp.raise_for_status()
    messages = []
    for m in resp.json().get("value", []):
        sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
        messages.append({
            "id": m.get("id"), "subject": m.get("subject"), "from": sender,
            "receivedDateTime": m.get("receivedDateTime"), "isRead": m.get("isRead"),
            "bodyPreview": m.get("bodyPreview"),
        })
    return {"source": "ms365", "messages": messages}


def read_message(message_id: str, source_config: dict | None = None) -> dict:
    if not configured(source_config):
        for m in DEMO_MESSAGES:
            if m["id"] == message_id:
                return {"source": "demo", **m}
        raise ValueError(f"message {message_id!r} not found in demo mailbox")

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{GRAPH}/users/{_settings(source_config)['mailbox']}/messages/{message_id}",
            headers=_headers(source_config),
            params={"$select": "id,subject,from,receivedDateTime,isRead,body"},
        )
    resp.raise_for_status()
    m = resp.json()
    sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
    return {
        "source": "ms365", "id": m.get("id"), "subject": m.get("subject"), "from": sender,
        "receivedDateTime": m.get("receivedDateTime"),
        "body": (m.get("body") or {}).get("content", ""),
    }


def reply_message(message_id: str, comment: str, source_config: dict | None = None) -> dict:
    """Send a reply to the given message (Graph 'reply' action)."""
    if not configured(source_config):
        original = next((m for m in DEMO_MESSAGES if m["id"] == message_id), None)
        if not original:
            raise ValueError(f"message {message_id!r} not found in demo mailbox")
        DEMO_SENT.append({"in_reply_to": message_id, "to": original["from"], "body": comment})
        return {"source": "demo", "status": "sent (demo mode - no real email was sent)",
                "to": original["from"], "in_reply_to": original["subject"]}

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{GRAPH}/users/{_settings(source_config)['mailbox']}/messages/{message_id}/reply",
            headers=_headers(source_config),
            json={"comment": comment},
        )
    resp.raise_for_status()
    return {"source": "ms365", "status": "sent"}
