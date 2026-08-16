"""SQLite persistence for users, tickets, and chat conversations."""
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import APP_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    customer TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',        -- open | in_progress | waiting | closed
    priority TEXT NOT NULL DEFAULT 'medium',    -- low | medium | high | urgent
    assignee TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- Routing + SLA columns are added by _migrate() so existing databases pick them up too.

CREATE TABLE IF NOT EXISTS ticket_comments (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    messages_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sheet_tables (
    id INTEGER PRIMARY KEY,
    datasource_id INTEGER NOT NULL REFERENCES datasources(id) ON DELETE CASCADE,
    table_name TEXT NOT NULL UNIQUE,
    source_file TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    columns_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_by TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,           -- report | presentation
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    created_by TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    """Commit-on-success connection that always closes.

    `with sqlite3.connect(...)` only manages the transaction, not the handle.
    Leaking handles keeps the database file locked on Windows, so later writes
    fail with "database is locked" — hence the explicit close here.
    """
    conn = sqlite3.connect(APP_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# Columns added after the first release. Kept here so an existing data/app.db
# picks them up on boot instead of needing a hand-written migration.
ADDED_COLUMNS = {
    "tickets": [
        ("queue", "TEXT DEFAULT ''"),                       # owning team / queue — routing
        ("request_type", "TEXT DEFAULT ''"),                # dependent on queue
        ("raised_by", "TEXT DEFAULT ''"),                   # the CSM, distinct from assignee
        ("customer_id", "TEXT DEFAULT ''"),                 # join key to the tracker
        ("response_due", "TEXT DEFAULT ''"),                # UTC ISO, business-hours clock
        ("resolution_due", "TEXT DEFAULT ''"),
        ("waiting_on", "TEXT DEFAULT ''"),                  # Customer | Internal team | Third party
        ("paused_since", "TEXT DEFAULT ''"),
        ("total_paused_hours", "REAL NOT NULL DEFAULT 0"),
        ("sla_response_breached", "INTEGER NOT NULL DEFAULT 0"),
        ("sla_resolution_breached", "INTEGER NOT NULL DEFAULT 0"),
        ("first_response_at", "TEXT DEFAULT ''"),           # drives the response breach flag
        ("resolved_at", "TEXT DEFAULT ''"),
        ("hubspot_id", "TEXT DEFAULT ''"),                  # the ticket's id in HubSpot, once pushed
        ("sync_state", "TEXT DEFAULT ''"),                  # hubspot | local | error | off
        ("sync_error", "TEXT DEFAULT ''"),
        ("synced_at", "TEXT DEFAULT ''"),
    ],
}


def _migrate(conn) -> None:
    for table, columns in ADDED_COLUMNS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


# ---------- users ----------

def login(name: str, email: str) -> dict:
    """Create the user if new, otherwise refresh their token."""
    token = secrets.token_hex(16)
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            conn.execute("UPDATE users SET name = ?, token = ? WHERE id = ?", (name, token, row["id"]))
            user_id = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO users (name, email, token, created_at) VALUES (?, ?, ?, ?)",
                (name, email, token, now()),
            )
            user_id = cur.lastrowid
    return {"id": user_id, "name": name, "email": email, "token": token}


def user_by_token(token: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE token = ?", (token,)).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT id, name, email FROM users ORDER BY name").fetchall()
    return [dict(r) for r in rows]


# ---------- tickets ----------

TICKET_STATUSES = {"open", "in_progress", "waiting", "closed"}
TICKET_PRIORITIES = {"low", "medium", "high", "urgent"}


TICKET_FIELDS = {"title", "description", "customer", "status", "priority", "assignee",
                 "queue", "request_type", "raised_by", "customer_id",
                 "response_due", "resolution_due", "waiting_on"}


def create_ticket(title: str, description: str = "", customer: str = "", priority: str = "medium",
                  assignee: str = "", created_by: str = "", queue: str = "", request_type: str = "",
                  raised_by: str = "", customer_id: str = "", waiting_on: str = "",
                  response_due: str = "", resolution_due: str = "") -> dict:
    from . import sla, tickets

    if priority not in TICKET_PRIORITIES:
        priority = "medium"
    values = {"queue": queue, "request_type": request_type, "raised_by": raised_by,
              "customer_id": customer_id, "waiting_on": waiting_on}
    tickets.validate(values, creating=True)

    ts = now()
    calendar = tickets.calendar_for(queue)
    if not (response_due and resolution_due):
        computed = sla.due_dates(priority, queue, calendar, start=ts)
        response_due = response_due or computed[0]
        resolution_due = resolution_due or computed[1]

    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO tickets (title, description, customer, priority, assignee, created_by,
                                    queue, request_type, raised_by, customer_id, waiting_on,
                                    response_due, resolution_due, paused_since, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, description, customer, priority, assignee, created_by,
             queue, request_type, raised_by, customer_id, waiting_on,
             response_due, resolution_due, ts if waiting_on else "", ts, ts),
        )
        ticket_id = cur.lastrowid
    return _commit_ticket(get_ticket(ticket_id))


def set_sync_state(ticket_id: int, state: str, hubspot_id: str = "", error: str = "") -> None:
    """Record where a ticket has been committed. Deliberately does not re-trigger a sync."""
    with connect() as conn:
        conn.execute(
            """UPDATE tickets SET sync_state = ?, hubspot_id = ?, sync_error = ?, synced_at = ?
               WHERE id = ?""",
            (state, hubspot_id or "", error or "", now(), ticket_id),
        )


def _commit_ticket(ticket: dict | None, force: bool = False) -> dict | None:
    """Push to HubSpot / write the local spreadsheet, then return the ticket as stored."""
    if not ticket:
        return ticket
    from . import ticketsync

    ticketsync.sync(ticket, force=force)
    return get_ticket(ticket["id"])


def _apply_sla_transitions(row: dict, updates: dict) -> dict:
    """Pause/resume the clock and stamp response/resolution times. Returns extra column updates."""
    from . import sla, tickets

    extra: dict = {}
    ts = now()
    calendar = tickets.calendar_for(updates.get("queue", row["queue"]))

    # ---- waiting_on drives the pause clock
    if "waiting_on" in updates and updates["waiting_on"] != row["waiting_on"]:
        was_paused, now_paused = bool(row["waiting_on"]), bool(updates["waiting_on"])
        if now_paused and not was_paused:
            extra["paused_since"] = ts
        elif was_paused and not now_paused:
            paused_for = sla.business_hours_between(row["paused_since"] or ts, ts, calendar)
            extra["paused_since"] = ""
            extra["total_paused_hours"] = round((row["total_paused_hours"] or 0) + paused_for, 3)
            # The clock stopped while we waited, so push the still-open deadlines out to match.
            for column, done_column in (("response_due", "first_response_at"),
                                        ("resolution_due", "resolved_at")):
                if row[column] and not row[done_column] and column not in updates:
                    extra[column] = sla.add_business_hours(row[column], paused_for, calendar)

    # ---- priority/queue changes retarget the clock, unless the caller set dates explicitly
    retarget = any(k in updates and updates[k] != row[k] for k in ("priority", "queue"))
    if retarget and "response_due" not in updates and "resolution_due" not in updates:
        priority = updates.get("priority", row["priority"])
        queue = updates.get("queue", row["queue"])
        response_due, resolution_due = sla.due_dates(
            priority, queue, tickets.calendar_for(queue), start=row["created_at"])
        paused = extra.get("total_paused_hours", row["total_paused_hours"] or 0)
        extra["response_due"] = sla.add_business_hours(response_due, paused, calendar)
        extra["resolution_due"] = sla.add_business_hours(resolution_due, paused, calendar)

    # ---- status stamps
    new_status = updates.get("status", row["status"])
    if new_status != row["status"]:
        if new_status != "open" and not row["first_response_at"]:
            extra["first_response_at"] = ts        # someone picked it up
        if new_status == "closed":
            extra["resolved_at"] = ts
        elif row["status"] == "closed":
            extra["resolved_at"] = ""              # reopened
    return extra


def _breach_flags(row: dict) -> dict:
    """Breach booleans for reporting. A paused ticket can't newly breach, but a late one stays late."""
    ts = now()
    paused = bool(row.get("paused_since"))
    flags = {}
    for due_col, done_col, flag in (
        ("response_due", "first_response_at", "sla_response_breached"),
        ("resolution_due", "resolved_at", "sla_resolution_breached"),
    ):
        due, done = row.get(due_col) or "", row.get(done_col) or ""
        if not due:
            flags[flag] = 0
        elif done:
            flags[flag] = int(done > due)
        else:
            flags[flag] = int(not paused and ts > due)
    return flags


def _with_sla_state(row: dict) -> dict:
    """Read-only clock detail the board uses for its badges."""
    from . import sla, tickets

    calendar = tickets.calendar_for(row.get("queue", ""))
    ts = now()
    state = {"paused": bool(row.get("paused_since")), "calendar": calendar}
    for key, due_col, done_col in (("response", "response_due", "first_response_at"),
                                   ("resolution", "resolution_due", "resolved_at")):
        due, done = row.get(due_col) or "", row.get(done_col) or ""
        if not due:
            state[f"{key}_remaining_hours"] = None
            continue
        # Business hours of slack: positive means time left (or met early), negative means late.
        against = done or ts
        state[f"{key}_remaining_hours"] = round(
            sla.business_hours_between(against, due, calendar) if against <= due
            else -sla.business_hours_between(due, against, calendar), 2)
        state[f"{key}_met"] = bool(done) and done <= due
    row["sla"] = state
    return row


def _refresh_breaches(conn, rows: list[dict]) -> list[dict]:
    """Recompute the stored breach flags so reports can query them straight from SQL."""
    for row in rows:
        flags = _breach_flags(row)
        if any(row.get(k) != v for k, v in flags.items()):
            row.update(flags)
            conn.execute(
                "UPDATE tickets SET sla_response_breached = ?, sla_resolution_breached = ? WHERE id = ?",
                (flags["sla_response_breached"], flags["sla_resolution_breached"], row["id"]),
            )
    return rows


def update_ticket(ticket_id: int, **fields) -> dict | None:
    from . import tickets

    updates = {k: v for k, v in fields.items() if k in TICKET_FIELDS and v is not None}
    if "status" in updates and updates["status"] not in TICKET_STATUSES:
        raise ValueError(f"invalid status; must be one of {sorted(TICKET_STATUSES)}")
    if "priority" in updates and updates["priority"] not in TICKET_PRIORITIES:
        raise ValueError(f"invalid priority; must be one of {sorted(TICKET_PRIORITIES)}")

    with connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not row:
            return None
        row = dict(row)
        if not updates:
            return get_ticket(ticket_id)

        tickets.validate({**row, **updates}, creating=False)
        updates.update(_apply_sla_transitions(row, updates))
        updates.update(_breach_flags({**row, **updates}))

        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE tickets SET {sets}, updated_at = ? WHERE id = ?",
                     (*updates.values(), now(), ticket_id))
    return _commit_ticket(get_ticket(ticket_id))


def get_ticket(ticket_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not row:
            return None
        ticket = _refresh_breaches(conn, [dict(row)])[0]
        comments = conn.execute(
            "SELECT id, author, body, created_at FROM ticket_comments WHERE ticket_id = ? ORDER BY id",
            (ticket_id,),
        ).fetchall()
    ticket["comments"] = [dict(c) for c in comments]
    return _with_sla_state(ticket)


def list_tickets(status: str | None = None, assignee: str | None = None,
                 queue: str | None = None, breached: bool | None = None) -> list[dict]:
    query = "SELECT * FROM tickets"
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if assignee:
        clauses.append("assignee = ?")
        params.append(assignee)
    if queue:
        clauses.append("queue = ?")
        params.append(queue)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, updated_at DESC"
    with connect() as conn:
        rows = _refresh_breaches(conn, [dict(r) for r in conn.execute(query, params).fetchall()])
    tickets_out = [_with_sla_state(r) for r in rows]
    if breached:
        tickets_out = [t for t in tickets_out
                       if t["sla_response_breached"] or t["sla_resolution_breached"]]
    return tickets_out


def add_comment(ticket_id: int, author: str, body: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not row:
            return None
        row = dict(row)
        conn.execute(
            "INSERT INTO ticket_comments (ticket_id, author, body, created_at) VALUES (?, ?, ?, ?)",
            (ticket_id, author, body, now()),
        )
        # First reply from anyone other than the CSM who raised it stops the response clock.
        extra = {}
        if not row["first_response_at"] and author != (row["raised_by"] or row["created_by"]):
            extra["first_response_at"] = now()
        extra.update(_breach_flags({**row, **extra}))
        sets = ", ".join(f"{k} = ?" for k in extra)
        conn.execute(f"UPDATE tickets SET {sets}, updated_at = ? WHERE id = ?",
                     (*extra.values(), now(), ticket_id))
    return _commit_ticket(get_ticket(ticket_id))


# ---------- conversations ----------

def get_conversation(user_id: int) -> str:
    with connect() as conn:
        row = conn.execute("SELECT messages_json FROM conversations WHERE user_id = ?", (user_id,)).fetchone()
    return row["messages_json"] if row else "[]"


def save_conversation(user_id: int, messages_json: str) -> None:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE conversations SET messages_json = ?, updated_at = ? WHERE user_id = ?",
            (messages_json, now(), user_id),
        )
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO conversations (user_id, messages_json, updated_at) VALUES (?, ?, ?)",
                (user_id, messages_json, now()),
            )


def clear_conversation(user_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))


# ---------- datasources ----------

def create_datasource(name: str, type_: str, description: str = "", config_json: str = "{}",
                      created_by: str = "", enabled: bool = True) -> dict:
    ts = now()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO datasources (name, type, description, config_json, enabled, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, type_, description, config_json, int(enabled), created_by, ts, ts),
        )
        ds_id = cur.lastrowid
    return get_datasource(ds_id)


def update_datasource(ds_id: int, **fields) -> dict | None:
    allowed = {"name", "description", "config_json", "enabled"}
    updates = {k: (int(v) if k == "enabled" else v) for k, v in fields.items()
               if k in allowed and v is not None}
    if not updates:
        return get_datasource(ds_id)
    sets = ", ".join(f"{k} = ?" for k in updates)
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE datasources SET {sets}, updated_at = ? WHERE id = ?",
            (*updates.values(), now(), ds_id),
        )
        if cur.rowcount == 0:
            return None
    return get_datasource(ds_id)


def get_datasource(ds_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM datasources WHERE id = ?", (ds_id,)).fetchone()
    return dict(row) if row else None


def list_datasources(enabled_only: bool = False) -> list[dict]:
    query = "SELECT * FROM datasources"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY type, name"
    with connect() as conn:
        rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def delete_datasource(ds_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM datasources WHERE id = ?", (ds_id,))
        return cur.rowcount > 0


# ---------- spreadsheet table registry ----------

def register_sheet_table(datasource_id: int, table_name: str, source_file: str,
                         row_count: int, columns_json: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sheet_tables WHERE table_name = ?", (table_name,))
        conn.execute(
            """INSERT INTO sheet_tables (datasource_id, table_name, source_file, row_count, columns_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (datasource_id, table_name, source_file, row_count, columns_json, now()),
        )


def sheet_tables(datasource_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM sheet_tables"
    params: list = []
    if datasource_id is not None:
        query += " WHERE datasource_id = ?"
        params.append(datasource_id)
    query += " ORDER BY table_name"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def drop_sheet_table(table_name: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sheet_tables WHERE table_name = ?", (table_name,))


# ---------- settings ----------

def all_settings() -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def settings_meta() -> dict[str, dict]:
    """Who last changed each setting, and when."""
    with connect() as conn:
        rows = conn.execute("SELECT key, updated_by, updated_at FROM settings").fetchall()
    return {r["key"]: {"updated_by": r["updated_by"], "updated_at": r["updated_at"]} for r in rows}


def set_setting(key: str, value: str, updated_by: str = "") -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                   updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
            (key, value, updated_by, now()),
        )


def delete_setting(key: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


# ---------- artifacts ----------

def add_artifact(kind: str, title: str, filename: str, created_by: str = "") -> dict:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO artifacts (kind, title, filename, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (kind, title, filename, created_by, now()),
        )
        artifact_id = cur.lastrowid
    return {"id": artifact_id, "kind": kind, "title": title, "filename": filename,
            "created_by": created_by, "url": f"/artifacts/{filename}"}


def list_artifacts() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM artifacts ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["url"] = f"/artifacts/{d['filename']}"
        out.append(d)
    return out
