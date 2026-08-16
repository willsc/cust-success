"""Commit tickets back to HubSpot when it's connected; keep a local copy either way.

Order of business on every create and update:

  1. The ticket is already saved in data/app.db — that never depends on anything external.
  2. If an enabled HubSpot source has a token, the ticket is pushed to the HubSpot
     tickets object (created once, updated in place afterwards).
  3. Whatever happened above, the whole board is written out to a spreadsheet in
     data/exports/ — CSV always, XLSX as well when openpyxl is installed. That is
     the record of last resort when HubSpot isn't connected or the push fails.

Nothing here is allowed to raise into the caller: a HubSpot outage must not stop
the team raising tickets. Failures land in the ticket's sync_state/sync_error and
can be retried from the UI.
"""
import csv
import json

import httpx

from . import deps, hubspot, settings
from .config import EXPORT_DIR

BASE = hubspot.BASE
TIMEOUT = 30

CSV_COLUMNS = [
    "id", "title", "status", "priority", "queue", "request_type", "raised_by", "assignee",
    "customer", "customer_id", "created_by", "created_at", "updated_at",
    "response_due", "resolution_due", "waiting_on", "paused_since", "total_paused_hours",
    "first_response_at", "resolved_at", "sla_response_breached", "sla_resolution_breached",
    "hubspot_id", "sync_state", "description",
]

# Our priorities -> HubSpot's hs_ticket_priority options. HubSpot has no URGENT
# by default, so urgent rides along as HIGH and stays visible in the body text.
PRIORITY_MAP = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH", "urgent": "HIGH"}

_pipeline_cache: dict[str, dict] = {}


# ---------- where does this ticket go ----------

def mode() -> str:
    """auto (push on every change) | manual (only when asked) | off."""
    value = (settings.value("HUBSPOT_TICKET_SYNC") or "auto").strip().lower()
    return value if value in ("auto", "manual", "off") else "auto"


def hubspot_target() -> tuple[dict, dict] | None:
    """The enabled HubSpot source we should write to, if there is a usable one."""
    from . import datasources, db

    for row in db.list_datasources(enabled_only=True):
        if row["type"] != "hubspot":
            continue
        config = json.loads(row["config_json"] or "{}")
        if hubspot.configured(config):
            return row, config
    return None


def status() -> dict:
    """Where tickets are being committed right now — shown in the UI."""
    target = hubspot_target()
    return {
        "mode": mode(),
        "hubspot_connected": bool(target),
        "hubspot_source": target[0]["name"] if target else "",
        "exports": {"csv": "/api/tickets/export.csv",
                    "xlsx": "/api/tickets/export.xlsx" if deps.module_present("openpyxl") else ""},
    }


# ---------- HubSpot pipeline/stage plumbing ----------

def _parse_pairs(raw: str) -> dict[str, str]:
    """`open: 1` per line -> {"open": "1"}."""
    out = {}
    for line in (raw or "").splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and value.strip():
            out[key.strip()] = value.strip()
    return out


def _pipeline(config: dict) -> dict:
    """Pipeline id + our status -> stage id, discovered once per token/pipeline."""
    token = hubspot._token(config)
    wanted = (config.get("ticket_pipeline") or "").strip()
    cache_key = f"{token[-8:]}:{wanted}"
    if cache_key in _pipeline_cache:
        return _pipeline_cache[cache_key]

    resp = httpx.get(f"{BASE}/crm/v3/pipelines/tickets",
                     headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
    resp.raise_for_status()
    pipelines = resp.json().get("results") or []
    if not pipelines:
        raise RuntimeError("This HubSpot portal has no ticket pipelines.")

    pipeline = next((p for p in pipelines if wanted in (p.get("id"), p.get("label"))), pipelines[0])
    stages = sorted(pipeline.get("stages") or [], key=lambda s: s.get("displayOrder", 0))
    if not stages:
        raise RuntimeError(f"HubSpot pipeline {pipeline.get('label')!r} has no stages.")

    closed = [s for s in stages if (s.get("metadata") or {}).get("ticketState") == "CLOSED"]
    open_stages = [s for s in stages if s not in closed] or stages
    pick = lambda seq, i: seq[min(i, len(seq) - 1)]["id"]
    resolved = {
        "id": pipeline["id"],
        "label": pipeline.get("label", ""),
        "stages": {
            "open": pick(open_stages, 0),
            "in_progress": pick(open_stages, 1),
            "waiting": pick(open_stages, 2),
            "closed": (closed or stages)[-1]["id"],
        },
    }
    _pipeline_cache[cache_key] = resolved
    return resolved


def invalidate_pipelines() -> None:
    _pipeline_cache.clear()


# ---------- pushing ----------

def _body_text(ticket: dict, mapped: set[str]) -> str:
    """The description plus every routing/SLA field that has no HubSpot property to live in."""
    labels = [
        ("queue", "Owning team"), ("request_type", "Request type"), ("raised_by", "Raised by (CSM)"),
        ("customer_id", "Customer ID"), ("response_due", "Response due"),
        ("resolution_due", "Resolution due"), ("waiting_on", "Waiting on"),
        ("total_paused_hours", "Paused (business hours)"),
    ]
    lines = [f"{label}: {ticket.get(key)}" for key, label in labels
             if key not in mapped and ticket.get(key)]
    if ticket.get("priority") == "urgent":
        lines.append("Priority: URGENT (HubSpot caps at High)")
    for flag, label in (("sla_response_breached", "Response SLA breached"),
                        ("sla_resolution_breached", "Resolution SLA breached")):
        if ticket.get(flag):
            lines.append(f"{label}: yes")

    body = ticket.get("description") or ""
    if lines:
        body = (body + "\n\n" if body else "") + "--- Routing & SLA (Customer Success Hub) ---\n" \
            + "\n".join(lines)
    return body


def _properties(ticket: dict, config: dict) -> dict:
    custom = _parse_pairs(config.get("ticket_property_map", ""))
    props = {
        "subject": ticket["title"],
        "hs_ticket_priority": PRIORITY_MAP.get(ticket.get("priority", ""), "MEDIUM"),
    }
    for field, hs_property in custom.items():
        value = ticket.get(field)
        if value not in (None, ""):
            props[hs_property] = str(value)
    props["content"] = _body_text(ticket, set(custom))

    stage_overrides = _parse_pairs(config.get("ticket_stage_map", ""))
    if stage_overrides.get(ticket["status"]):
        props["hs_pipeline_stage"] = stage_overrides[ticket["status"]]
        if config.get("ticket_pipeline"):
            props["hs_pipeline"] = config["ticket_pipeline"].strip()
    else:
        pipeline = _pipeline(config)
        props["hs_pipeline"] = pipeline["id"]
        props["hs_pipeline_stage"] = pipeline["stages"].get(ticket["status"],
                                                            pipeline["stages"]["open"])
    return props


def push(ticket: dict) -> dict:
    """Create or update this ticket in HubSpot. Returns {ok, hubspot_id, message}."""
    target = hubspot_target()
    if not target:
        return {"ok": False, "message": "No enabled HubSpot source with a token."}
    _, config = target

    headers = {"Authorization": f"Bearer {hubspot._token(config)}",
               "Content-Type": "application/json"}
    try:
        properties = _properties(ticket, config)
        payload = {"properties": properties}
        existing = (ticket.get("hubspot_id") or "").strip()
        with httpx.Client(timeout=TIMEOUT) as client:
            if existing:
                resp = client.patch(f"{BASE}/crm/v3/objects/tickets/{existing}",
                                    headers=headers, json=payload)
                if resp.status_code == 404:      # deleted in HubSpot — recreate it
                    existing = ""
                    resp = client.post(f"{BASE}/crm/v3/objects/tickets",
                                       headers=headers, json=payload)
            else:
                resp = client.post(f"{BASE}/crm/v3/objects/tickets",
                                   headers=headers, json=payload)
        if resp.status_code >= 400:
            return {"ok": False, "message": _hubspot_error(resp)}
        hubspot_id = str(resp.json().get("id") or existing)
        return {"ok": True, "hubspot_id": hubspot_id,
                "message": f"{'Updated' if existing else 'Created'} HubSpot ticket {hubspot_id}."}
    except Exception as exc:
        return {"ok": False, "message": str(exc)[:300]}


def _hubspot_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        detail = body.get("message") or json.dumps(body)[:200]
    except Exception:
        detail = resp.text[:200]
    return f"HubSpot {resp.status_code}: {detail}"


# ---------- local spreadsheet ----------

def _rows() -> list[dict]:
    from . import db

    return db.list_tickets()


def write_exports() -> dict:
    """Mirror every ticket into data/exports/. CSV always; XLSX when openpyxl is around."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    tickets = _rows()
    written = []

    csv_path = EXPORT_DIR / "tickets.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for ticket in tickets:
            writer.writerow({c: ticket.get(c, "") for c in CSV_COLUMNS})
    written.append(csv_path.name)

    if deps.module_present("openpyxl"):
        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        sheet.title = "Tickets"
        sheet.append(CSV_COLUMNS)
        for ticket in tickets:
            sheet.append([_cell(ticket.get(c, "")) for c in CSV_COLUMNS])
        sheet.freeze_panes = "A2"
        for i, column in enumerate(CSV_COLUMNS, start=1):
            sheet.column_dimensions[sheet.cell(row=1, column=i).column_letter].width = \
                min(max(len(column) + 2, 12), 40)
        xlsx_path = EXPORT_DIR / "tickets.xlsx"
        book.save(xlsx_path)
        written.append(xlsx_path.name)

    return {"files": written, "rows": len(tickets), "dir": str(EXPORT_DIR)}


def _cell(value):
    return value if isinstance(value, (str, int, float)) or value is None else str(value)


# ---------- the thing db.py calls ----------

def sync(ticket: dict, force: bool = False) -> dict:
    """Commit one ticket: HubSpot if connected, local spreadsheet either way.

    Never raises — the ticket is already saved by the time we get here.
    """
    from . import db

    result = {"state": "local", "message": "", "hubspot_id": ticket.get("hubspot_id", "")}
    try:
        current = mode()
        if current == "off" and not force:
            result["state"] = "off"
            result["message"] = "Ticket sync is switched off in Settings."
        elif current == "manual" and not force:
            result["state"] = ticket.get("sync_state") or "local"
            result["message"] = "Manual mode — push from the ticket when you're ready."
        elif not hubspot_target():
            result["message"] = "No HubSpot connected — kept in the local board and spreadsheet."
        else:
            pushed = push(ticket)
            if pushed["ok"]:
                result.update(state="hubspot", hubspot_id=pushed["hubspot_id"],
                              message=pushed["message"])
            else:
                result.update(state="error", message=pushed["message"])

        db.set_sync_state(ticket["id"], result["state"], result["hubspot_id"],
                          "" if result["state"] in ("hubspot", "local", "off") else result["message"])
    except Exception as exc:  # belt and braces: sync must never break ticketing
        result.update(state="error", message=str(exc)[:300])

    try:
        write_exports()
    except Exception as exc:
        result["export_error"] = str(exc)[:200]
    return result
