"""FastAPI app: REST API for auth, chat, tickets, uploads, artifacts + static UI."""
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import bot, datasources, db, deps, settings, sla, spreadsheets, ticketsync, tickets
from .config import ARTIFACT_DIR, BASE_DIR, EXPORT_DIR

app = FastAPI(title="Customer Success Bot")
db.init()
datasources.seed_defaults()


# ---------- auth ----------

class LoginRequest(BaseModel):
    name: str
    email: str


def current_user(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    user = db.user_by_token(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return user


@app.post("/api/login")
def login(body: LoginRequest):
    name, email = body.name.strip(), body.email.strip().lower()
    if not name or "@" not in email:
        raise HTTPException(status_code=400, detail="Provide a name and a valid email")
    return db.login(name, email)


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return {"id": user["id"], "name": user["name"], "email": user["email"]}


@app.get("/api/users")
def users(user: dict = Depends(current_user)):
    return db.list_users()


# ---------- chat ----------

class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat")
def chat(body: ChatRequest, user: dict = Depends(current_user)):
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Empty message")
    try:
        return bot.chat(user, body.message.strip())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Bot error: {exc}")


@app.post("/api/chat/reset")
def chat_reset(user: dict = Depends(current_user)):
    db.clear_conversation(user["id"])
    return {"ok": True}


# ---------- tickets ----------

class TicketCreate(BaseModel):
    title: str
    description: str = ""
    customer: str = ""
    priority: str = "medium"
    assignee: str = ""
    # Collection fields: routing, the join key, and who to notify back
    queue: str = ""
    request_type: str = ""
    raised_by: str = ""
    customer_id: str = ""
    waiting_on: str = ""
    response_due: str = ""
    resolution_due: str = ""


class TicketUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    customer: str | None = None
    status: str | None = None
    priority: str | None = None
    assignee: str | None = None
    queue: str | None = None
    request_type: str | None = None
    raised_by: str | None = None
    customer_id: str | None = None
    waiting_on: str | None = None
    response_due: str | None = None
    resolution_due: str | None = None


class CommentCreate(BaseModel):
    body: str


@app.get("/api/ticket-fields")
def ticket_fields(user: dict = Depends(current_user)):
    """Queues, their dependent request types, waiting-on options and the SLA targets."""
    return tickets.field_catalog()


@app.post("/api/ticket-fields/refresh-holidays")
def ticket_refresh_holidays(user: dict = Depends(current_user)):
    """Re-pull the published bank holidays so the SLA clock stays right in future years."""
    return sla.refresh_from_govuk()


@app.get("/api/tickets")
def ticket_list(status: str | None = None, assignee: str | None = None, queue: str | None = None,
                breached: bool | None = None, user: dict = Depends(current_user)):
    return db.list_tickets(status, assignee, queue, breached)


@app.post("/api/tickets")
def ticket_create(body: TicketCreate, user: dict = Depends(current_user)):
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")
    try:
        return db.create_ticket(
            body.title.strip(), body.description, body.customer, body.priority, body.assignee,
            created_by=user["name"], queue=body.queue, request_type=body.request_type,
            raised_by=body.raised_by or user["name"], customer_id=body.customer_id,
            waiting_on=body.waiting_on, response_due=body.response_due,
            resolution_due=body.resolution_due)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/tickets/export.csv")
def ticket_export_csv(user: dict = Depends(current_user)):
    """The local spreadsheet — every ticket, refreshed on each change."""
    ticketsync.write_exports()
    path = EXPORT_DIR / "tickets.csv"
    return FileResponse(path, media_type="text/csv", filename="tickets.csv")


@app.get("/api/tickets/export.xlsx")
def ticket_export_xlsx(user: dict = Depends(current_user)):
    if not deps.module_present("openpyxl"):
        raise HTTPException(status_code=503,
                            detail="Excel export needs the spreadsheet component — install it on the Sources tab.")
    ticketsync.write_exports()
    path = EXPORT_DIR / "tickets.xlsx"
    return FileResponse(path, filename="tickets.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/tickets/sync-status")
def ticket_sync_status(user: dict = Depends(current_user)):
    return ticketsync.status()


@app.post("/api/tickets/{ticket_id}/sync")
def ticket_sync(ticket_id: int, user: dict = Depends(current_user)):
    """Push one ticket to HubSpot now — used for retries and for manual mode."""
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    result = ticketsync.sync(ticket, force=True)
    return {**result, "ticket": db.get_ticket(ticket_id)}


@app.get("/api/tickets/{ticket_id}")
def ticket_get(ticket_id: int, user: dict = Depends(current_user)):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@app.patch("/api/tickets/{ticket_id}")
def ticket_update(ticket_id: int, body: TicketUpdate, user: dict = Depends(current_user)):
    try:
        ticket = db.update_ticket(ticket_id, **body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@app.post("/api/tickets/{ticket_id}/comments")
def ticket_comment(ticket_id: int, body: CommentCreate, user: dict = Depends(current_user)):
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="Empty comment")
    ticket = db.add_comment(ticket_id, user["name"], body.body.strip())
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


# ---------- data sources ----------

class DataSourceCreate(BaseModel):
    name: str
    type: str
    description: str = ""
    config: dict = {}


class DataSourceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict | None = None
    enabled: bool | None = None


@app.get("/api/datasource-types")
def datasource_types(user: dict = Depends(current_user)):
    return datasources.type_catalog()


@app.get("/api/datasources")
def datasource_list(user: dict = Depends(current_user)):
    return datasources.list_sources()


@app.post("/api/datasources")
def datasource_create(body: DataSourceCreate, user: dict = Depends(current_user)):
    try:
        return datasources.create(body.name, body.type, body.description, body.config, user["name"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/datasources/{ds_id}")
def datasource_update(ds_id: int, body: DataSourceUpdate, user: dict = Depends(current_user)):
    source = datasources.update(ds_id, body.name, body.description, body.config, body.enabled)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    return source


@app.delete("/api/datasources/{ds_id}")
def datasource_delete(ds_id: int, user: dict = Depends(current_user)):
    if not datasources.delete(ds_id):
        raise HTTPException(status_code=404, detail="Data source not found")
    return {"ok": True}


@app.get("/api/datasources/{ds_id}/schema")
def datasource_schema(ds_id: int, user: dict = Depends(current_user)):
    schema = datasources.describe_source(ds_id)
    if not schema:
        raise HTTPException(status_code=404, detail="Data source not found")
    return schema


@app.post("/api/datasources/{ds_id}/test")
def datasource_test(ds_id: int, user: dict = Depends(current_user)):
    try:
        return datasources.test(ds_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/datasources/{ds_id}/upload")
async def datasource_upload(ds_id: int, file: UploadFile = File(...), user: dict = Depends(current_user)):
    source = datasources.get_source(ds_id)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    if source["type"] != "spreadsheet":
        raise HTTPException(status_code=400, detail="This data source does not accept file uploads")
    suffix = (file.filename or "").lower().rsplit(".", 1)[-1]
    if suffix not in ("csv", "xlsx", "xls"):
        raise HTTPException(status_code=400, detail="Only .csv, .xlsx and .xls files are supported")
    content = await file.read()
    try:
        tables = spreadsheets.ingest_file(file.filename, content, datasource_id=ds_id)
    except deps.MissingDependency as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {exc}")
    return {"loaded": tables}


@app.delete("/api/datasources/{ds_id}/tables/{table_name}")
def datasource_drop_table(ds_id: int, table_name: str, user: dict = Depends(current_user)):
    owned = {t["table_name"] for t in db.sheet_tables(ds_id)}
    if table_name not in owned:
        raise HTTPException(status_code=404, detail="Table not found on this data source")
    spreadsheets.drop_table(table_name)
    return {"ok": True}


# ---------- settings: API keys and shared credentials ----------

class SettingsUpdate(BaseModel):
    values: dict[str, str]


@app.get("/api/settings")
def settings_get(user: dict = Depends(current_user)):
    return settings.public()


@app.patch("/api/settings")
def settings_patch(body: SettingsUpdate, user: dict = Depends(current_user)):
    try:
        return settings.save(body.values, updated_by=user["name"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/settings/test-claude")
def settings_test_claude(user: dict = Depends(current_user)):
    """Round-trips the configured key and model so you know before you rely on it."""
    return bot.test_connection()


# ---------- optional components ----------

class InstallRequest(BaseModel):
    keys: list[str]


@app.get("/api/setup")
def setup_status(user: dict = Depends(current_user)):
    """Which optional packs are installed, and any install currently running."""
    return deps.overview()


@app.post("/api/setup/install")
def setup_install(body: InstallRequest, user: dict = Depends(current_user)):
    try:
        return deps.install(body.keys)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:  # another install already running
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/api/setup/jobs/{job_id}")
def setup_job(job_id: int, user: dict = Depends(current_user)):
    job = deps.job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="No such install job")
    return job


# ---------- artifacts ----------

@app.get("/api/artifacts")
def artifacts(user: dict = Depends(current_user)):
    return db.list_artifacts()


@app.get("/artifacts/{filename}")
def artifact_file(filename: str):
    path = (ARTIFACT_DIR / filename).resolve()
    if not path.is_file() or path.parent != ARTIFACT_DIR.resolve():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path)


# ---------- static UI ----------

class NoCacheStatic(StaticFiles):
    """Serve the UI with revalidation forced.

    Without this the browser keeps serving the previously cached CSS/JS from its
    own memory cache, so an updated UI silently doesn't appear until a hard
    refresh. ETags still make the revalidation request cheap (304, no body).
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app.mount("/", NoCacheStatic(directory=BASE_DIR / "app" / "static", html=True), name="static")
