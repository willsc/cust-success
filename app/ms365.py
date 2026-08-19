"""Microsoft 365 mail and calendar via Microsoft Graph (client-credentials app registration).

One app registration can reach every mailbox in the tenant, so each call names the
mailbox it wants. `mailbox` defaults to the source's primary address; a source can
also declare an allowlist (the `mailboxes` field), and when it does, anything
outside that list is refused here rather than at Graph.

Falls back to an in-memory demo tenant when MS_* env vars are not configured, so
the bot's and the MCP server's mail tools work end to end without credentials.
"""
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import settings

GRAPH = "https://graph.microsoft.com/v1.0"

_token_cache: dict = {}  # cache_key -> {"token": ..., "expires": ...}

# Demo tenant: two mailboxes, so multi-mailbox tools have something to show
# before anyone has pasted a client secret in.
DEMO_MAILBOXES = {
    "success@example.com": [
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
    ],
    "renewals@example.com": [
        {
            "id": "demo-3",
            "subject": "Nordwind AB - purchase order for Q4",
            "from": "sara@nordwind.example",
            "receivedDateTime": "2026-08-16T11:02:00Z",
            "isRead": True,
            "bodyPreview": "Attaching the signed PO for the Q4 expansion. Please confirm receipt...",
            "body": "Hi,\n\nAttaching the signed PO for the Q4 expansion. Please confirm receipt so we can get the seats provisioned before the end of the month.\n\nBest,\nSara Lindqvist",
        },
    ],
}

DEMO_FOLDERS = ["inbox", "sentitems", "archive"]

DEMO_EVENTS = {
    "success@example.com": [
        {"id": "evt-1", "subject": "Acme Retail - renewal review",
         "start": "2026-08-20T13:00:00Z", "end": "2026-08-20T14:00:00Z",
         "organizer": "success@example.com", "location": "Teams",
         "attendees": ["maya.chen@acmeretail.example"], "isAllDay": False},
        {"id": "evt-2", "subject": "Bright Labs - escalation standup",
         "start": "2026-08-21T08:30:00Z", "end": "2026-08-21T09:00:00Z",
         "organizer": "success@example.com", "location": "Teams",
         "attendees": ["t.okafor@brightlabs.example"], "isAllDay": False},
    ],
    "renewals@example.com": [
        {"id": "evt-3", "subject": "Q4 pipeline review",
         "start": "2026-08-20T15:00:00Z", "end": "2026-08-20T16:00:00Z",
         "organizer": "renewals@example.com", "location": "Room 2",
         "attendees": [], "isAllDay": False},
    ],
}

DEMO_SENT: list[dict] = []

MESSAGE_FIELDS = "id,subject,from,toRecipients,receivedDateTime,isRead,hasAttachments,bodyPreview"
EVENT_FIELDS = "id,subject,start,end,organizer,location,attendees,isAllDay,isCancelled"


def _split(raw: str) -> list[str]:
    """Split a comma/newline separated list field into clean entries."""
    return [part.strip() for chunk in (raw or "").splitlines()
            for part in chunk.split(",") if part.strip()]


def _settings(source_config: dict | None = None) -> dict:
    """Per-source credentials, falling back to the shared MS_* values from Settings/environment."""
    cfg = source_config or {}
    return {
        "tenant_id": cfg.get("tenant_id") or settings.value("MS_TENANT_ID"),
        "client_id": cfg.get("client_id") or settings.value("MS_CLIENT_ID"),
        "client_secret": cfg.get("client_secret") or settings.value("MS_CLIENT_SECRET"),
        "mailbox": cfg.get("mailbox") or settings.value("MS_MAILBOX"),
        "mailboxes": cfg.get("mailboxes") or settings.value("MS_MAILBOXES"),
    }


def configured(source_config: dict | None = None) -> bool:
    s = _settings(source_config)
    return all([s["tenant_id"], s["client_id"], s["client_secret"], s["mailbox"]])


# ---------- mailbox selection ----------

def allowlist(source_config: dict | None = None) -> list[str]:
    """The mailboxes this source may touch: primary first, then the extras field.

    An empty list means "not restricted here" — the app registration's own grant
    (and any Exchange application access policy on it) is then the only limit.
    """
    s = _settings(source_config)
    entries = ([s["mailbox"]] if s["mailbox"] else []) + _split(s["mailboxes"])
    return list(dict.fromkeys(e.strip().lower() for e in entries if e.strip()))


def restricted(source_config: dict | None = None) -> bool:
    """True when the source declares extra mailboxes, making the allowlist binding."""
    return bool(_split(_settings(source_config)["mailboxes"]))


def resolve_mailbox(mailbox: str = "", source_config: dict | None = None) -> str:
    """The mailbox a call should act on, enforcing the source's allowlist."""
    allowed = allowlist(source_config)
    if not configured(source_config):
        allowed = allowed or list(DEMO_MAILBOXES)

    if not mailbox:
        if not allowed:
            raise ValueError(
                "No mailbox is configured for this source. Set the mailbox address on the "
                "Sources tab (or MS_MAILBOX in the environment)."
            )
        return allowed[0]

    wanted = mailbox.strip().lower()
    if restricted(source_config) and wanted not in allowed:
        raise ValueError(
            f"Mailbox {mailbox!r} is not on this source's allowed list "
            f"({', '.join(allowed)}). Add it on the Sources tab to permit it."
        )
    return wanted


def list_mailboxes(source_config: dict | None = None) -> dict:
    """Mailboxes available to this source.

    A declared allowlist is authoritative. Without one, ask Graph for the tenant's
    mail-enabled users — which needs User.Read.All, so a refusal there is reported
    rather than raised, leaving the configured mailbox as the answer.
    """
    if not configured(source_config):
        boxes = list(dict.fromkeys(allowlist(source_config) + list(DEMO_MAILBOXES)))
        return {"source": "demo (Microsoft 365 not configured)", "restricted": restricted(source_config),
                "mailboxes": [{"address": b, "displayName": b.split("@")[0].title()} for b in boxes]}

    allowed = allowlist(source_config)
    if restricted(source_config):
        return {"source": "ms365", "restricted": True,
                "mailboxes": [{"address": a, "displayName": ""} for a in allowed]}

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f"{GRAPH}/users", headers=_headers(source_config),
                params={"$top": 100, "$select": "mail,displayName,userPrincipalName",
                        "$filter": "accountEnabled eq true"},
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return {
                "source": "ms365", "restricted": False,
                "mailboxes": [{"address": a, "displayName": ""} for a in allowed],
                "note": "Directory listing needs the User.Read.All application permission; "
                        "showing the configured mailbox only. Any mailbox address can still "
                        "be passed explicitly.",
            }
        raise

    users = [
        {"address": (u.get("mail") or u.get("userPrincipalName") or "").lower(),
         "displayName": u.get("displayName", "")}
        for u in resp.json().get("value", [])
    ]
    return {"source": "ms365", "restricted": False,
            "mailboxes": [u for u in users if u["address"]]}


def test_connection(source_config: dict) -> dict:
    if not configured(source_config):
        return {"ok": False, "message": "Credentials incomplete — the bot will use the demo inbox."}
    boxes = allowlist(source_config)
    try:
        for box in boxes:
            list_messages(limit=1, mailbox=box, source_config=source_config)
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    if len(boxes) > 1:
        return {"ok": True, "message": f"Connected to {len(boxes)} mailboxes: {', '.join(boxes)}."}
    return {"ok": True, "message": f"Connected to {boxes[0]}."}


# ---------- auth ----------

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


def _demo_messages(mailbox: str, requested: str = "") -> list[dict]:
    """Demo mail for a mailbox.

    A box the demo tenant has never heard of reads as empty when the caller asked
    for it by name; when it is only the configured default (someone set MS_MAILBOX
    but no secret) the demo inbox stands in, so the tools still demonstrate.
    """
    if mailbox in DEMO_MAILBOXES:
        return DEMO_MAILBOXES[mailbox]
    if requested:
        return []
    return next(iter(DEMO_MAILBOXES.values()))


def _demo_events(mailbox: str, requested: str = "") -> list[dict]:
    if mailbox in DEMO_EVENTS:
        return DEMO_EVENTS[mailbox]
    if requested:
        return []
    return next(iter(DEMO_EVENTS.values()))


def _summary(m: dict) -> dict:
    sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
    return {
        "id": m.get("id"), "subject": m.get("subject"), "from": sender,
        "to": [((r.get("emailAddress") or {}).get("address", ""))
               for r in (m.get("toRecipients") or [])],
        "receivedDateTime": m.get("receivedDateTime"), "isRead": m.get("isRead"),
        "hasAttachments": m.get("hasAttachments"), "bodyPreview": m.get("bodyPreview"),
    }


# ---------- mail ----------

def list_folders(mailbox: str = "", source_config: dict | None = None) -> dict:
    """Mail folders in a mailbox, so a caller can scope a search to one."""
    box = resolve_mailbox(mailbox, source_config)
    if not configured(source_config):
        return {"source": "demo (Microsoft 365 not configured)", "mailbox": box,
                "folders": [{"id": f, "displayName": f} for f in DEMO_FOLDERS]}

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{GRAPH}/users/{box}/mailFolders", headers=_headers(source_config),
            params={"$top": 100, "$select": "id,displayName,totalItemCount,unreadItemCount"},
        )
    resp.raise_for_status()
    folders = [
        {"id": f.get("id"), "displayName": f.get("displayName"),
         "total": f.get("totalItemCount"), "unread": f.get("unreadItemCount")}
        for f in resp.json().get("value", [])
    ]
    return {"source": "ms365", "mailbox": box, "folders": folders}


def list_messages(search: str = "", folder: str = "inbox", limit: int = 10,
                  source_config: dict | None = None, mailbox: str = "",
                  unread_only: bool = False) -> dict:
    """List or search messages in one mailbox.

    folder is a well-known name ('inbox', 'sentitems', ...) or a folder id from
    list_folders; pass 'all' to search the whole mailbox instead of one folder.
    """
    limit = max(1, min(int(limit), 50))
    box = resolve_mailbox(mailbox, source_config)

    if not configured(source_config):
        items = _demo_messages(box, mailbox)
        if search:
            s = search.lower()
            items = [m for m in items if s in (m["subject"] + m["from"] + m["body"]).lower()]
        if unread_only:
            items = [m for m in items if not m["isRead"]]
        summaries = [{k: m[k] for k in
                      ("id", "subject", "from", "receivedDateTime", "isRead", "bodyPreview")}
                     for m in items[:limit]]
        return {"source": "demo (Microsoft 365 not configured)", "mailbox": box,
                "folder": folder, "messages": summaries}

    scope = (f"{GRAPH}/users/{box}/messages" if folder in ("", "all")
             else f"{GRAPH}/users/{box}/mailFolders/{folder}/messages")
    params: dict = {"$top": limit, "$select": MESSAGE_FIELDS}
    if search:
        # Graph rejects $search combined with $orderby or $filter, so a search
        # gives up relevance-vs-recency ordering and the unread filter.
        params["$search"] = f'"{search}"'
    else:
        params["$orderby"] = "receivedDateTime desc"
        if unread_only:
            params["$filter"] = "isRead eq false"

    with httpx.Client(timeout=30) as client:
        resp = client.get(scope, headers=_headers(source_config), params=params)
    resp.raise_for_status()
    messages = [_summary(m) for m in resp.json().get("value", [])]
    if search and unread_only:
        messages = [m for m in messages if m.get("isRead") is False]
    return {"source": "ms365", "mailbox": box, "folder": folder or "all", "messages": messages}


def read_message(message_id: str, source_config: dict | None = None, mailbox: str = "") -> dict:
    box = resolve_mailbox(mailbox, source_config)
    if not configured(source_config):
        for m in _demo_messages(box, mailbox):
            if m["id"] == message_id:
                return {"source": "demo", "mailbox": box, **m}
        raise ValueError(f"message {message_id!r} not found in demo mailbox {box}")

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{GRAPH}/users/{box}/messages/{message_id}",
            headers=_headers(source_config),
            params={"$select": f"{MESSAGE_FIELDS},body,ccRecipients,conversationId"},
        )
    resp.raise_for_status()
    m = resp.json()
    return {
        "source": "ms365", "mailbox": box, **_summary(m),
        "cc": [((r.get("emailAddress") or {}).get("address", ""))
               for r in (m.get("ccRecipients") or [])],
        "conversationId": m.get("conversationId"),
        "body": (m.get("body") or {}).get("content", ""),
    }


def reply_message(message_id: str, comment: str, source_config: dict | None = None,
                  mailbox: str = "", reply_all: bool = False) -> dict:
    """Send a reply to the given message (Graph 'reply' / 'replyAll' action)."""
    box = resolve_mailbox(mailbox, source_config)
    if not configured(source_config):
        original = next((m for m in _demo_messages(box, mailbox) if m["id"] == message_id), None)
        if not original:
            raise ValueError(f"message {message_id!r} not found in demo mailbox {box}")
        DEMO_SENT.append({"mailbox": box, "in_reply_to": message_id,
                          "to": original["from"], "body": comment})
        return {"source": "demo", "mailbox": box,
                "status": "sent (demo mode - no real email was sent)",
                "to": original["from"], "in_reply_to": original["subject"]}

    action = "replyAll" if reply_all else "reply"
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{GRAPH}/users/{box}/messages/{message_id}/{action}",
            headers=_headers(source_config),
            json={"comment": comment},
        )
    resp.raise_for_status()
    return {"source": "ms365", "mailbox": box, "status": "sent", "action": action}


def send_message(to: list[str], subject: str, body: str, cc: list[str] | None = None,
                 source_config: dict | None = None, mailbox: str = "",
                 html: bool = False) -> dict:
    """Send a new message from a mailbox."""
    box = resolve_mailbox(mailbox, source_config)
    recipients = [a.strip() for a in (to or []) if a and a.strip()]
    if not recipients:
        raise ValueError("at least one recipient is required")

    if not configured(source_config):
        DEMO_SENT.append({"mailbox": box, "to": recipients, "subject": subject, "body": body})
        return {"source": "demo", "mailbox": box,
                "status": "sent (demo mode - no real email was sent)",
                "to": recipients, "subject": subject}

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML" if html else "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in recipients],
            "ccRecipients": [{"emailAddress": {"address": a.strip()}}
                             for a in (cc or []) if a and a.strip()],
        },
        "saveToSentItems": True,
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{GRAPH}/users/{box}/sendMail",
                           headers=_headers(source_config), json=payload)
    resp.raise_for_status()
    return {"source": "ms365", "mailbox": box, "status": "sent",
            "to": recipients, "subject": subject}


# ---------- calendar ----------

def _window(start: str = "", end: str = "", days: int = 7) -> tuple[str, str]:
    """Normalise a time window to ISO-8601 UTC, defaulting to the next `days` days."""
    now = datetime.now(timezone.utc)
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else now
    if end:
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    else:
        end_dt = start_dt + timedelta(days=max(1, int(days)))
    if end_dt <= start_dt:
        raise ValueError("end must be after start")
    return (start_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            end_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))


def list_events(mailbox: str = "", start: str = "", end: str = "", days: int = 7,
                limit: int = 25, source_config: dict | None = None) -> dict:
    """Calendar events for a mailbox in a time window (expanded recurrences)."""
    box = resolve_mailbox(mailbox, source_config)
    limit = max(1, min(int(limit), 100))
    window_start, window_end = _window(start, end, days)

    if not configured(source_config):
        events = [e for e in _demo_events(box, mailbox)
                  if window_start <= e["start"] <= window_end][:limit]
        return {"source": "demo (Microsoft 365 not configured)", "mailbox": box,
                "start": window_start, "end": window_end, "events": events}

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{GRAPH}/users/{box}/calendarView",
            headers={**_headers(source_config), "Prefer": 'outlook.timezone="UTC"'},
            params={"startDateTime": window_start, "endDateTime": window_end,
                    "$top": limit, "$orderby": "start/dateTime", "$select": EVENT_FIELDS},
        )
    resp.raise_for_status()
    events = []
    for e in resp.json().get("value", []):
        events.append({
            "id": e.get("id"), "subject": e.get("subject"),
            "start": (e.get("start") or {}).get("dateTime"),
            "end": (e.get("end") or {}).get("dateTime"),
            "organizer": (((e.get("organizer") or {}).get("emailAddress") or {}).get("address", "")),
            "location": ((e.get("location") or {}).get("displayName", "")),
            "attendees": [((a.get("emailAddress") or {}).get("address", ""))
                          for a in (e.get("attendees") or [])],
            "isAllDay": e.get("isAllDay"), "isCancelled": e.get("isCancelled"),
        })
    return {"source": "ms365", "mailbox": box, "start": window_start,
            "end": window_end, "events": events}


def free_busy(addresses: list[str], start: str = "", end: str = "", days: int = 1,
              interval: int = 30, source_config: dict | None = None) -> dict:
    """Free/busy for one or more addresses (Graph getSchedule).

    Addresses here are the people being *asked about*, so they are not held to
    the mailbox allowlist; the request itself is issued from an allowed mailbox.
    """
    schedules = [a.strip() for a in (addresses or []) if a and a.strip()]
    if not schedules:
        raise ValueError("at least one address is required")
    window_start, window_end = _window(start, end, days)
    box = resolve_mailbox("", source_config)

    if not configured(source_config):
        out = []
        for address in schedules:
            busy = [{"status": "busy", "start": e["start"], "end": e["end"], "subject": e["subject"]}
                    for e in DEMO_EVENTS.get(address.lower(), [])
                    if window_start <= e["start"] <= window_end]
            out.append({"address": address, "busy": busy})
        return {"source": "demo (Microsoft 365 not configured)", "start": window_start,
                "end": window_end, "schedules": out}

    payload = {
        "schedules": schedules,
        "startTime": {"dateTime": window_start, "timeZone": "UTC"},
        "endTime": {"dateTime": window_end, "timeZone": "UTC"},
        "availabilityViewInterval": max(5, min(int(interval), 1440)),
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{GRAPH}/users/{box}/calendar/getSchedule",
                           headers=_headers(source_config), json=payload)
    resp.raise_for_status()
    out = []
    for entry in resp.json().get("value", []):
        out.append({
            "address": entry.get("scheduleId"),
            "availabilityView": entry.get("availabilityView"),
            "busy": [{"status": item.get("status"),
                      "start": (item.get("start") or {}).get("dateTime"),
                      "end": (item.get("end") or {}).get("dateTime"),
                      "subject": item.get("subject", "")}
                     for item in (entry.get("scheduleItems") or [])],
            "error": (entry.get("error") or {}).get("message"),
        })
    return {"source": "ms365", "start": window_start, "end": window_end, "schedules": out}
