"""Data source registry: user-configurable connections the bot can query.

Each data source is a row in the `datasources` table with a type, a free-text
description (which the bot reads to decide when to use it), and a JSON config
whose shape is declared by TYPES below. The UI renders its form from that
declaration, so adding a new source type only means adding an entry here plus a
connector module.
"""
import json

from . import db, deps, hubspot, ms365, restsource, spreadsheets, sqlsource

# Field spec drives both the UI form and secret masking.
# kind: text | password | textarea | select | checkbox | number
TYPES = {
    "spreadsheet": {
        "label": "Spreadsheets",
        "icon": "📊",
        "blurb": "Upload CSV or Excel files. Each sheet becomes a table the bot queries with SQL.",
        "fields": [],
        "uploadable": True,
    },
    "hubspot": {
        "label": "HubSpot CRM",
        "icon": "🧡",
        "blurb": "Contacts, companies, deals and tickets from HubSpot.",
        "fields": [
            {"name": "token", "label": "Private app token", "kind": "password",
             "help": "HubSpot → Settings → Integrations → Private Apps. Leave blank to use demo data."},
            {"name": "contacts_properties", "label": "Extra contact properties", "kind": "text",
             "placeholder": "custom_tier, csm_owner", "help": "Comma-separated custom properties to fetch."},
            {"name": "companies_properties", "label": "Extra company properties", "kind": "text",
             "placeholder": "renewal_date, arr"},
            {"name": "deals_properties", "label": "Extra deal properties", "kind": "text"},
            {"name": "tickets_properties", "label": "Extra ticket properties", "kind": "text"},
            {"name": "ticket_pipeline", "label": "Ticket pipeline (for tickets we push)", "kind": "text",
             "placeholder": "0 or Support Pipeline",
             "help": "Leave blank to use the portal's first ticket pipeline."},
            {"name": "ticket_stage_map", "label": "Status → pipeline stage", "kind": "textarea",
             "placeholder": "open: 1\nin_progress: 2\nwaiting: 3\nclosed: 4",
             "help": "One per line. Leave blank to map onto the pipeline's stages in order."},
            {"name": "ticket_property_map", "label": "Field → HubSpot property", "kind": "textarea",
             "placeholder": "queue: custom_owning_team\ncustomer_id: customer_id_uk_public",
             "help": "Send our routing fields to custom HubSpot properties. Anything unmapped is "
                     "written into the ticket body instead, so nothing is lost."},
        ],
    },
    "ms365_mail": {
        "label": "Microsoft 365 Mail",
        "icon": "📧",
        "blurb": "Read and reply to a shared mailbox via Microsoft Graph.",
        "fields": [
            {"name": "mailbox", "label": "Mailbox address", "kind": "text",
             "placeholder": "success@yourcompany.com"},
            {"name": "tenant_id", "label": "Tenant ID", "kind": "text"},
            {"name": "client_id", "label": "Client ID", "kind": "text"},
            {"name": "client_secret", "label": "Client secret", "kind": "password",
             "help": "Entra ID app registration with Mail.Read and Mail.Send application permissions."},
        ],
    },
    "sql_database": {
        "label": "SQL Database",
        "icon": "🗄️",
        "blurb": "Query PostgreSQL, MySQL, SQL Server or SQLite directly. Read-only.",
        "fields": [
            {"name": "url", "label": "Connection URL", "kind": "text",
             "placeholder": "postgresql+psycopg2://user:password@host:5432/dbname",
             "help": "MySQL: mysql+pymysql://…  ·  SQLite: sqlite:////path/to/file.db", "secret": True},
            {"name": "schema", "label": "Schema", "kind": "text", "placeholder": "public"},
            {"name": "tables", "label": "Limit to tables", "kind": "text",
             "placeholder": "customers, subscriptions",
             "help": "Comma-separated. Leave blank to expose every table."},
        ],
    },
    "rest_api": {
        "label": "REST API",
        "icon": "🔌",
        "blurb": "Any JSON HTTP API — Zendesk, Jira, Stripe, an internal service.",
        "fields": [
            {"name": "base_url", "label": "Base URL", "kind": "text",
             "placeholder": "https://api.example.com/v1/"},
            {"name": "auth_type", "label": "Authentication", "kind": "select",
             "options": ["none", "bearer", "header", "basic"]},
            {"name": "token", "label": "Token / API key", "kind": "password",
             "help": "Used for Bearer and custom-header auth."},
            {"name": "header_name", "label": "Header name", "kind": "text", "placeholder": "X-API-Key",
             "help": "Only for custom-header auth."},
            {"name": "username", "label": "Username", "kind": "text", "help": "Only for basic auth."},
            {"name": "password", "label": "Password", "kind": "password", "help": "Only for basic auth."},
            {"name": "extra_headers", "label": "Extra headers", "kind": "textarea",
             "placeholder": "Accept: application/json\nX-Account: 42", "help": "One per line, Name: value."},
            {"name": "test_path", "label": "Test path", "kind": "text", "placeholder": "/ping"},
            {"name": "timeout", "label": "Timeout (seconds)", "kind": "number", "placeholder": "30"},
            {"name": "allow_writes", "label": "Allow write methods (POST/PUT/DELETE)", "kind": "checkbox",
             "help": "Off by default — the bot can only read."},
        ],
    },
}

MASK = "••••••••"


def _secret_fields(type_: str) -> set[str]:
    spec = TYPES.get(type_, {})
    return {f["name"] for f in spec.get("fields", [])
            if f.get("kind") == "password" or f.get("secret")}


def type_catalog() -> list[dict]:
    """Type declarations for the UI's 'Add data source' form."""
    return [{"type": key, "packs": deps.type_status(key), **{k: v for k, v in spec.items()}}
            for key, spec in TYPES.items()]


def _parse(row: dict) -> dict:
    try:
        return json.loads(row.get("config_json") or "{}")
    except json.JSONDecodeError:
        return {}


def _public(row: dict) -> dict:
    """Row shaped for the UI, with secrets masked."""
    config = _parse(row)
    secrets = _secret_fields(row["type"])
    masked = {k: (MASK if k in secrets and v else v) for k, v in config.items()}
    spec = TYPES.get(row["type"], {})
    out = {
        "id": row["id"], "name": row["name"], "type": row["type"],
        "type_label": spec.get("label", row["type"]), "icon": spec.get("icon", "🔗"),
        "description": row.get("description", ""), "enabled": bool(row["enabled"]),
        "created_by": row.get("created_by", ""), "updated_at": row.get("updated_at", ""),
        "config": masked,
    }
    if row["type"] == "spreadsheet":
        out["tables"] = [
            {"table": t["table_name"], "rows": t["row_count"],
             "columns": json.loads(t["columns_json"]), "file": t["source_file"]}
            for t in db.sheet_tables(row["id"])
        ]
    return out


def list_sources(enabled_only: bool = False) -> list[dict]:
    return [_public(r) for r in db.list_datasources(enabled_only)]


def get_source(ds_id: int) -> dict | None:
    row = db.get_datasource(ds_id)
    return _public(row) if row else None


def _raw(ds_id: int) -> tuple[dict, dict]:
    """(row, unmasked config) — internal use only, never returned to the UI."""
    row = db.get_datasource(ds_id)
    if not row:
        raise ValueError(f"Data source {ds_id} not found")
    return row, _parse(row)


def create(name: str, type_: str, description: str = "", config: dict | None = None,
           created_by: str = "") -> dict:
    if type_ not in TYPES:
        raise ValueError(f"Unknown data source type {type_!r}")
    row = db.create_datasource(name.strip() or TYPES[type_]["label"], type_, description,
                               json.dumps(config or {}), created_by)
    return _public(row)


def update(ds_id: int, name: str | None = None, description: str | None = None,
           config: dict | None = None, enabled: bool | None = None) -> dict | None:
    row = db.get_datasource(ds_id)
    if not row:
        return None
    config_json = None
    if config is not None:
        existing = _parse(row)
        secrets = _secret_fields(row["type"])
        merged = dict(existing)
        for key, value in config.items():
            # A masked secret coming back from the UI means "unchanged"
            if key in secrets and value == MASK:
                continue
            merged[key] = value
        config_json = json.dumps(merged)
    updated = db.update_datasource(ds_id, name=name, description=description,
                                   config_json=config_json, enabled=enabled)
    return _public(updated) if updated else None


def delete(ds_id: int) -> bool:
    row = db.get_datasource(ds_id)
    if row and row["type"] == "spreadsheet":
        for t in db.sheet_tables(ds_id):
            spreadsheets.drop_table(t["table_name"])
    return db.delete_datasource(ds_id)


def test(ds_id: int) -> dict:
    row, config = _raw(ds_id)
    type_ = row["type"]
    try:
        if type_ == "hubspot":
            return hubspot.test_connection(config)
        if type_ == "ms365_mail":
            return ms365.test_connection(config)
        if type_ == "sql_database":
            return sqlsource.test_connection(config)
        if type_ == "rest_api":
            return restsource.test_connection(config)
        if type_ == "spreadsheet":
            tables = db.sheet_tables(ds_id)
            return {"ok": bool(tables),
                    "message": f"{len(tables)} table(s) loaded." if tables else "No files uploaded yet."}
    except deps.MissingDependency as exc:
        return {"ok": False, "message": str(exc), "needs": exc.pack}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": False, "message": f"No connection test for type {type_}"}


# ---------- what the bot sees ----------

def _catalog_entry(row: dict) -> dict:
    config = _parse(row)
    entry = {
        "id": row["id"], "name": row["name"], "type": row["type"],
        "description": row.get("description", "") or TYPES.get(row["type"], {}).get("blurb", ""),
    }
    try:
        if row["type"] == "spreadsheet":
            entry["tables"] = [
                {"table": t["table_name"], "rows": t["row_count"], "columns": json.loads(t["columns_json"])}
                for t in db.sheet_tables(row["id"])
            ]
            entry["query_with"] = "query_sql"
        elif row["type"] == "sql_database":
            entry["tables"] = sqlsource.describe(config)
            entry["query_with"] = "query_sql"
        elif row["type"] == "hubspot":
            entry["objects"] = sorted(hubspot.DEFAULT_PROPERTIES)
            entry["live"] = hubspot.configured(config)
            entry["query_with"] = "hubspot_query"
        elif row["type"] == "ms365_mail":
            entry["mailbox"] = ms365._settings(config)["mailbox"] or "(demo inbox)"
            entry["live"] = ms365.configured(config)
            entry["query_with"] = "search_email / read_email / reply_email"
        elif row["type"] == "rest_api":
            entry["base_url"] = config.get("base_url", "")
            entry["writes_allowed"] = bool(config.get("allow_writes"))
            entry["query_with"] = "call_api"
    except Exception as exc:
        entry["error"] = f"could not inspect: {exc}"
    return entry


def catalog() -> list[dict]:
    """Enabled sources, with enough schema detail for the bot to query them."""
    return [_catalog_entry(row) for row in db.list_datasources(enabled_only=True)]


def describe_source(ds_id: int) -> dict | None:
    """Live schema for one source — used by the UI's schema inspector."""
    row = db.get_datasource(ds_id)
    return _catalog_entry(row) if row else None


def _resolve(ds_id: int | None, type_: str) -> tuple[dict, dict]:
    """Find the source to use: the one named, else the only enabled one of that type."""
    if ds_id is not None:
        row, config = _raw(int(ds_id))
        if row["type"] != type_:
            raise ValueError(f"Data source {ds_id} is a {row['type']}, not a {type_}")
        if not row["enabled"]:
            raise ValueError(f"Data source '{row['name']}' is disabled")
        return row, config
    candidates = [r for r in db.list_datasources(enabled_only=True) if r["type"] == type_]
    if not candidates:
        raise ValueError(
            f"No enabled '{type_}' data source is configured. "
            f"Add one on the Data Sources tab."
        )
    if len(candidates) > 1:
        names = ", ".join(f"{r['id']}={r['name']}" for r in candidates)
        raise ValueError(f"Several {type_} sources exist — pass source_id. Options: {names}")
    return candidates[0], _parse(candidates[0])


# ---------- query dispatch used by the bot ----------

def query_sql(sql: str, source_id: int | None = None) -> dict:
    """Route a SELECT to either the spreadsheet store or an external SQL database."""
    if source_id is not None:
        row, config = _raw(int(source_id))
        if not row["enabled"]:
            raise ValueError(f"Data source '{row['name']}' is disabled")
        if row["type"] == "spreadsheet":
            return spreadsheets.run_sql(sql)
        if row["type"] == "sql_database":
            return sqlsource.run_sql(config, sql)
        raise ValueError(f"Data source {source_id} ({row['type']}) cannot be queried with SQL")

    sql_sources = [r for r in db.list_datasources(enabled_only=True)
                   if r["type"] in ("spreadsheet", "sql_database")]
    if not sql_sources:
        raise ValueError("No SQL-queryable data source is configured yet.")
    if len(sql_sources) > 1:
        names = ", ".join(f"{r['id']}={r['name']}" for r in sql_sources)
        raise ValueError(f"Several SQL sources exist — pass source_id. Options: {names}")
    only = sql_sources[0]
    return (spreadsheets.run_sql(sql) if only["type"] == "spreadsheet"
            else sqlsource.run_sql(_parse(only), sql))


def hubspot_query(object_type: str, search: str = "", limit: int = 20,
                  source_id: int | None = None) -> dict:
    _, config = _resolve(source_id, "hubspot")
    return hubspot.query(object_type, search, limit, source_config=config)


def search_email(search: str = "", folder: str = "inbox", limit: int = 10,
                 source_id: int | None = None) -> dict:
    _, config = _resolve(source_id, "ms365_mail")
    return ms365.list_messages(search, folder, limit, source_config=config)


def read_email(message_id: str, source_id: int | None = None) -> dict:
    _, config = _resolve(source_id, "ms365_mail")
    return ms365.read_message(message_id, source_config=config)


def reply_email(message_id: str, body: str, source_id: int | None = None) -> dict:
    _, config = _resolve(source_id, "ms365_mail")
    return ms365.reply_message(message_id, body, source_config=config)


def call_api(path: str, method: str = "GET", params: dict | None = None,
             body: dict | None = None, source_id: int | None = None) -> dict:
    _, config = _resolve(source_id, "rest_api")
    return restsource.call(config, path, method, params, body)


# ---------- first-run seeding ----------

def seed_defaults() -> None:
    """Create starter sources on first run, pre-filled from any .env settings."""
    if db.list_datasources():
        return
    create("Spreadsheets", "spreadsheet",
           "Files the customer success team uploads: usage exports, renewal trackers, health scores.")
    create("HubSpot CRM", "hubspot",
           "Customer records: contacts, companies, open deals and CRM tickets.")
    create("Team Mailbox", "ms365_mail",
           "The shared customer success inbox — incoming customer email and replies.")
