"""External SQL database data source (PostgreSQL / MySQL / SQL Server / SQLite) via SQLAlchemy.

Queries are restricted to read-only SELECT statements.
"""
import re

from sqlalchemy import create_engine, inspect, text

MAX_RESULT_ROWS = 200

WRITE_KEYWORDS = re.compile(
    r"(?is)\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|attach|"
    r"replace|merge|call|exec|execute|vacuum|copy|into\s+outfile)\b"
)

DRIVER_HINTS = {
    "postgresql": "postgresql+psycopg2://user:password@host:5432/dbname",
    "mysql": "mysql+pymysql://user:password@host:3306/dbname",
    "sqlite": "sqlite:////absolute/path/to/file.db",
    "mssql": "mssql+pyodbc://user:password@host/dbname?driver=ODBC+Driver+18+for+SQL+Server",
}


def _engine(config: dict):
    url = (config.get("url") or "").strip()
    if not url:
        raise ValueError("This SQL data source has no connection URL configured.")
    return create_engine(url, pool_pre_ping=True, connect_args={})


def guard(sql: str) -> str:
    stripped = sql.strip().rstrip(";")
    if not re.match(r"(?is)^(select|with)\b", stripped):
        raise ValueError("Only SELECT (or WITH ... SELECT) queries are allowed on this data source.")
    if WRITE_KEYWORDS.search(stripped):
        raise ValueError("Query contains a write/DDL keyword; this data source is read-only.")
    if ";" in stripped:
        raise ValueError("Only a single statement is allowed per query.")
    return stripped


def test_connection(config: dict) -> dict:
    engine = _engine(config)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        insp = inspect(engine)
        schema = config.get("schema") or None
        tables = insp.get_table_names(schema=schema)
        return {"ok": True, "message": f"Connected. {len(tables)} table(s) visible.",
                "tables": sorted(tables)[:100]}
    finally:
        engine.dispose()


def describe(config: dict) -> list[dict]:
    """Table + column listing so the bot knows what it can query."""
    engine = _engine(config)
    try:
        insp = inspect(engine)
        schema = config.get("schema") or None
        allow = {t.strip() for t in (config.get("tables") or "").split(",") if t.strip()}
        out = []
        for table in insp.get_table_names(schema=schema):
            if allow and table not in allow:
                continue
            cols = [c["name"] for c in insp.get_columns(table, schema=schema)]
            out.append({"table": table, "columns": cols})
        return out
    finally:
        engine.dispose()


def run_sql(config: dict, sql: str) -> dict:
    statement = guard(sql)
    engine = _engine(config)
    try:
        with engine.connect() as conn:
            result = conn.execute(text(statement))
            rows = result.fetchmany(MAX_RESULT_ROWS + 1)
            columns = list(result.keys())
    finally:
        engine.dispose()
    truncated = len(rows) > MAX_RESULT_ROWS
    rows = rows[:MAX_RESULT_ROWS]
    return {
        "columns": columns,
        "rows": [[_jsonable(v) for v in row] for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
