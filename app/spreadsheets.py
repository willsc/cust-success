"""Spreadsheet ingestion: uploaded CSV/XLSX files become SQLite tables the bot can query with SQL."""
import json
import os
import re
import sqlite3
from contextlib import contextmanager

from . import deps
from .config import SHEETS_DB, UPLOAD_DIR

MAX_RESULT_ROWS = 200


def _pandas():
    """Import pandas only when a file is actually being ingested.

    Querying already-loaded tables is plain sqlite3, so the app runs — and the
    bot answers — before the spreadsheet pack is installed.
    """
    deps.require("spreadsheets")
    import pandas as pd

    return pd


@contextmanager
def _connect():
    """Commit-on-success connection that always closes.

    Closing matters on Windows, where a lingering handle keeps the database
    file locked and later writes fail with "database is locked".
    """
    conn = sqlite3.connect(SHEETS_DB)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip()).strip("_").lower()
    if not name or name[0].isdigit():
        name = "t_" + name
    return name[:60]


def _safe_filename(filename: str) -> str:
    """Strip any directory component and characters Windows rejects in filenames."""
    name = os.path.basename(filename.replace("\\", "/")).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name.strip(". ") or "upload.csv"


def ingest_file(filename: str, content: bytes, datasource_id: int | None = None) -> list[dict]:
    """Save the upload and load each sheet into its own SQLite table. Returns table summaries."""
    from . import db  # local import keeps this module importable on its own

    pd = _pandas()
    path = UPLOAD_DIR / _safe_filename(filename)
    path.write_bytes(content)

    base = _safe_name(path.stem)
    frames = {}
    if path.suffix.lower() in (".xlsx", ".xls"):
        sheets = pd.read_excel(path, sheet_name=None)
        for sheet_name, df in sheets.items():
            table = base if len(sheets) == 1 else f"{base}_{_safe_name(sheet_name)}"
            frames[table] = df
    else:
        frames[base] = pd.read_csv(path)

    # Keep table names readable, but don't let one data source overwrite another's table.
    owners = {t["table_name"]: t["datasource_id"] for t in db.sheet_tables()}
    resolved = {}
    for table, df in frames.items():
        owner = owners.get(table)
        if owner is not None and datasource_id is not None and owner != datasource_id:
            table = f"{table}_ds{datasource_id}"
        resolved[table] = df

    loaded = []
    with _connect() as conn:
        for table, df in resolved.items():
            df.columns = [_safe_name(str(c)) or f"col_{i}" for i, c in enumerate(df.columns)]
            df.to_sql(table, conn, if_exists="replace", index=False)
            summary = {"table": table, "rows": len(df), "columns": list(df.columns)}
            loaded.append(summary)
            if datasource_id is not None:
                db.register_sheet_table(datasource_id, table, path.name, len(df),
                                        json.dumps(list(df.columns)))
    return loaded


def drop_table(table_name: str) -> None:
    """Remove one loaded spreadsheet table and its registry entry."""
    from . import db

    safe = _safe_name(table_name)
    with _connect() as conn:
        conn.execute(f'DROP TABLE IF EXISTS "{safe}"')
    db.drop_sheet_table(table_name)


def list_tables() -> list[dict]:
    with _connect() as conn:
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        out = []
        for t in tables:
            cols = [r["name"] for r in conn.execute(f'PRAGMA table_info("{t}")').fetchall()]
            count = conn.execute(f'SELECT COUNT(*) AS n FROM "{t}"').fetchone()["n"]
            out.append({"table": t, "rows": count, "columns": cols})
    return out


def run_sql(query: str) -> dict:
    """Run a read-only SELECT against the spreadsheet database."""
    stripped = query.strip().rstrip(";")
    if not re.match(r"(?is)^(select|with)\b", stripped):
        raise ValueError("Only SELECT (or WITH ... SELECT) queries are allowed.")
    if re.search(r"(?is)\b(insert|update|delete|drop|alter|create|attach|pragma|vacuum|replace)\b", stripped):
        raise ValueError("Query contains a disallowed keyword; only read-only SELECT queries are permitted.")
    if not SHEETS_DB.exists():
        raise ValueError("No spreadsheets have been uploaded yet.")
    # as_uri() percent-encodes and normalises separators, so this also works for
    # Windows paths like C:\... which are not valid inside a plain file: URI.
    conn = sqlite3.connect(f"{SHEETS_DB.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(stripped).fetchmany(MAX_RESULT_ROWS + 1)
    finally:
        conn.close()
    truncated = len(rows) > MAX_RESULT_ROWS
    rows = rows[:MAX_RESULT_ROWS]
    return {
        "columns": list(rows[0].keys()) if rows else [],
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }
