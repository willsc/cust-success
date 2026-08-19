"""Pulling HubSpot and mailbox data into the local store, so it can be aggregated.

The bot can already query these services live, one question at a time. That is no
good for the things a customer success team actually asks - totals by account,
this quarter against last, mail volume per customer joined to the renewal tracker
- because each answer needs one record at a time over the network, and nothing can
be joined to a spreadsheet.

So: sync. Each source pulls its records into tables in the same SQLite file the
uploaded spreadsheets live in, which makes all of it one queryable set. A synced
HubSpot deal list and an uploaded usage export can be joined in a single SELECT.

Tables are replaced wholesale on each sync rather than merged. These are small
(thousands of rows, not millions), a full pull is simple to reason about, and a
half-updated table is worse than a slightly stale one. Everything is stored as
text: SQLite does not mind, and the alternative is guessing types from an API
whose fields differ per portal.

Nothing here needs pandas - it is plain sqlite3 - so syncing works on a machine
that has never installed the spreadsheet component.
"""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx

from . import db, hubspot, ms365
from .config import SHEETS_DB

# Per-object ceilings, so one sync cannot run away with a large portal.
MAX_RECORDS = 5000
MAX_MESSAGES = 1000
PAGE = 100


@contextmanager
def _connect():
    conn = sqlite3.connect(SHEETS_DB)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", str(name).strip()).strip("_").lower()
    if not name or name[0].isdigit():
        name = "t_" + name
    return name[:60]


def _flatten(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _write_table(table: str, rows: list[dict], columns: list[str] | None = None) -> dict:
    """Replace `table` with these rows.

    `columns` fixes the shape up front, so a page where every record happens to
    omit an optional field does not silently produce a narrower table. Anything
    extra a record carries is appended. `keys` reads the records; `cols` is the
    same list scrubbed into SQL identifiers - they stay index-aligned.
    """
    keys = list(columns or [])
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    if not keys:
        keys = ["id"]
    cols = [_safe_name(k) for k in keys]

    with _connect() as conn:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        conn.execute(f'CREATE TABLE "{table}" ({", ".join(f'"{c}" TEXT' for c in cols)})')
        if rows:
            placeholders = ", ".join("?" for _ in cols)
            conn.executemany(
                f'INSERT INTO "{table}" VALUES ({placeholders})',
                [[_flatten(row.get(k)) for k in keys] for row in rows],
            )
    return {"table": table, "rows": len(rows), "columns": cols}


def _register(source_id: int, summary: dict, label: str) -> None:
    db.register_sheet_table(source_id, summary["table"], label,
                            summary["rows"], json.dumps(summary["columns"]))


# ---------- HubSpot ----------

def _hubspot_page(object_type: str, config: dict, after: str | None) -> tuple[list[dict], str | None]:
    props = hubspot.properties_for(object_type, config)
    params = {"limit": PAGE, "properties": ",".join(props)}
    if after:
        params["after"] = after
    with httpx.Client(timeout=60) as client:
        resp = client.get(
            f"{hubspot.BASE}/crm/v3/objects/{object_type}",
            headers={"Authorization": f"Bearer {hubspot._token(config)}"},
            params=params,
        )
    resp.raise_for_status()
    payload = resp.json()
    records = [{"id": r.get("id"), **(r.get("properties") or {})} for r in payload.get("results", [])]
    return records, (((payload.get("paging") or {}).get("next") or {}).get("after"))


def sync_hubspot(source_id: int, config: dict) -> dict:
    """Every CRM object type this source exposes, into hubspot_<type> tables."""
    tables = []
    for object_type in hubspot.object_types():
        columns = ["id"] + hubspot.properties_for(object_type, config)
        if not hubspot.configured(config):
            records = list(hubspot.DEMO_DATA[object_type])
        else:
            records, after = [], None
            while len(records) < MAX_RECORDS:
                page, after = _hubspot_page(object_type, config, after)
                records.extend(page)
                if not after or not page:
                    break
            records = records[:MAX_RECORDS]
        summary = _write_table(f"hubspot_{object_type}", records, columns)
        _register(source_id, summary, "HubSpot sync")
        tables.append(summary)
    return {"tables": tables, "live": hubspot.configured(config)}


# ---------- Microsoft 365 mail ----------

MAIL_COLUMNS = ["mailbox", "id", "conversation_id", "subject", "from_address", "to_addresses",
                "received", "is_read", "has_attachments", "preview"]


def _mail_page(mailbox: str, config: dict, url: str | None) -> tuple[list[dict], str | None]:
    headers = ms365._headers(config)
    with httpx.Client(timeout=60) as client:
        if url:
            resp = client.get(url, headers=headers)
        else:
            resp = client.get(
                f"{ms365.GRAPH}/users/{mailbox}/messages", headers=headers,
                params={"$top": PAGE, "$orderby": "receivedDateTime desc",
                        "$select": ms365.MESSAGE_FIELDS + ",conversationId"},
            )
    resp.raise_for_status()
    payload = resp.json()
    rows = []
    for m in payload.get("value", []):
        summary = ms365._summary(m)
        rows.append({
            "mailbox": mailbox, "id": summary["id"], "conversation_id": m.get("conversationId", ""),
            "subject": summary["subject"], "from_address": summary["from"],
            "to_addresses": ", ".join(summary.get("to") or []),
            "received": summary["receivedDateTime"], "is_read": summary["isRead"],
            "has_attachments": summary.get("hasAttachments"), "preview": summary["bodyPreview"],
        })
    return rows, payload.get("@odata.nextLink")


def sync_mail(source_id: int, config: dict) -> dict:
    """Message headers from every mailbox this source can reach, into one table.

    Headers only - subject, sender, date, preview. Bodies are large, rarely what
    an aggregate needs, and the bot can still fetch one on demand with read_email.
    """
    mailboxes = ms365.allowlist(config) or list(ms365.DEMO_MAILBOXES)
    rows = []
    for mailbox in mailboxes:
        if not ms365.configured(config):
            for m in ms365._demo_messages(mailbox, mailbox):
                rows.append({
                    "mailbox": mailbox, "id": m["id"], "conversation_id": "",
                    "subject": m["subject"], "from_address": m["from"], "to_addresses": "",
                    "received": m["receivedDateTime"], "is_read": m["isRead"],
                    "has_attachments": False, "preview": m["bodyPreview"],
                })
            continue
        url, got = None, 0
        while got < MAX_MESSAGES:
            page, url = _mail_page(mailbox, config, url)
            rows.extend(page)
            got += len(page)
            if not url or not page:
                break

    summary = _write_table("mail_messages", rows, MAIL_COLUMNS)
    _register(source_id, summary, "Mailbox sync")
    return {"tables": [summary], "live": ms365.configured(config), "mailboxes": mailboxes}


# ---------- entry point ----------

SYNCERS = {"hubspot": sync_hubspot, "ms365_mail": sync_mail}


def can_sync(type_: str) -> bool:
    return type_ in SYNCERS


def sync(source_id: int, type_: str, config: dict) -> dict:
    if not can_sync(type_):
        raise ValueError(f"{type_} sources are not synced - they are queried directly.")
    result = SYNCERS[type_](source_id, config)
    result["synced_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result["total_rows"] = sum(t["rows"] for t in result["tables"])
    return result
