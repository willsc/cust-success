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


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


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


def create_ticket(title: str, description: str = "", customer: str = "", priority: str = "medium",
                  assignee: str = "", created_by: str = "") -> dict:
    if priority not in TICKET_PRIORITIES:
        priority = "medium"
    ts = now()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO tickets (title, description, customer, priority, assignee, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, description, customer, priority, assignee, created_by, ts, ts),
        )
        ticket_id = cur.lastrowid
    return get_ticket(ticket_id)


def update_ticket(ticket_id: int, **fields) -> dict | None:
    allowed = {"title", "description", "customer", "status", "priority", "assignee"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "status" in updates and updates["status"] not in TICKET_STATUSES:
        raise ValueError(f"invalid status; must be one of {sorted(TICKET_STATUSES)}")
    if "priority" in updates and updates["priority"] not in TICKET_PRIORITIES:
        raise ValueError(f"invalid priority; must be one of {sorted(TICKET_PRIORITIES)}")
    if not updates:
        return get_ticket(ticket_id)
    sets = ", ".join(f"{k} = ?" for k in updates)
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE tickets SET {sets}, updated_at = ? WHERE id = ?",
            (*updates.values(), now(), ticket_id),
        )
        if cur.rowcount == 0:
            return None
    return get_ticket(ticket_id)


def get_ticket(ticket_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not row:
            return None
        comments = conn.execute(
            "SELECT id, author, body, created_at FROM ticket_comments WHERE ticket_id = ? ORDER BY id",
            (ticket_id,),
        ).fetchall()
    ticket = dict(row)
    ticket["comments"] = [dict(c) for c in comments]
    return ticket


def list_tickets(status: str | None = None, assignee: str | None = None) -> list[dict]:
    query = "SELECT * FROM tickets"
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if assignee:
        clauses.append("assignee = ?")
        params.append(assignee)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, updated_at DESC"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def add_comment(ticket_id: int, author: str, body: str) -> dict | None:
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not exists:
            return None
        conn.execute(
            "INSERT INTO ticket_comments (ticket_id, author, body, created_at) VALUES (?, ?, ?, ?)",
            (ticket_id, author, body, now()),
        )
        conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now(), ticket_id))
    return get_ticket(ticket_id)


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
