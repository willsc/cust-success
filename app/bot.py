"""The customer-success bot: a Claude tool-use agent over spreadsheets, HubSpot,
MS365 mail, the ticketing system, and report/presentation generation."""
import json

import anthropic

from . import config, datasources, db, reports

client = anthropic.Anthropic()

MAX_TURNS = 15

SYSTEM_PROMPT = """You are a customer-success assistant for a team of customer success managers.
The team configures its own data sources, so what you can reach changes over time. Sources may include
spreadsheets, SQL databases, the HubSpot CRM, a Microsoft 365 mailbox, and arbitrary REST APIs.
You can also manage support tickets and produce reports (HTML) and presentations (.pptx).

Guidelines:
- ALWAYS call list_data_sources first when a request touches customer data. It returns each source's id,
  type, the team's own description of what it holds, and its schema. Never assume a source exists.
- Each source's description is written by the team - trust it when deciding which source answers a question.
- Pass source_id when more than one source of a type exists; omit it when there is only one.
- For SQL sources, inspect the table/column list from list_data_sources, then write precise SELECT queries.
  Spreadsheets are SQLite; external databases use their own dialect (Postgres, MySQL, ...).
- Cite concrete numbers from the data; never invent customer data.
- Before sending an email reply, show the user the draft and get their confirmation in conversation.
  Only call reply_email after the user has approved the draft text.
- When asked for a report or presentation, gather the data first, then generate the artifact and
  share its link with the user.
- REST API sources are read-only unless the team enabled writes; check before attempting a write.
- If a source you need is missing or misconfigured, say so plainly and tell the user to add it on the
  Data Sources tab - do not guess at the data.
- Keep responses focused, brief, and concise. Lead with the answer.

The current user is: {user_name} ({user_email}).
"""

TOOLS = [
    {
        "name": "list_data_sources",
        "description": "List every data source the team has configured, with its id, name, type, the team's description of what it contains, and its schema (tables and columns for SQL sources, objects for HubSpot, base URL for APIs). Call this first whenever a request touches customer data.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "query_sql",
        "description": "Run a read-only SELECT against a SQL-queryable data source - either an uploaded spreadsheet set (SQLite dialect) or a connected SQL database (its own dialect). Use list_data_sources first for table and column names. Results are capped at 200 rows.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single SELECT (or WITH...SELECT) statement."},
                "source_id": {"type": "integer", "description": "Data source id. Omit only if exactly one SQL source exists."},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "hubspot_query",
        "description": "Search or list HubSpot CRM records. Returns record properties like name, email, amount, dealstage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "object_type": {"type": "string", "enum": ["contacts", "companies", "deals", "tickets"]},
                "search": {"type": "string", "description": "Free-text search; omit to list recent records."},
                "limit": {"type": "integer", "description": "Max records, default 20."},
                "source_id": {"type": "integer", "description": "HubSpot data source id; omit if only one exists."},
            },
            "required": ["object_type"],
        },
    },
    {
        "name": "search_email",
        "description": "List or search messages in the team's Microsoft 365 mailbox. Returns id, subject, sender, date, and a short preview for each message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Free-text search; omit to list the latest messages."},
                "folder": {"type": "string", "description": "Mail folder, default 'inbox'."},
                "limit": {"type": "integer", "description": "Max messages, default 10."},
                "source_id": {"type": "integer", "description": "Mailbox data source id; omit if only one exists."},
            },
        },
    },
    {
        "name": "read_email",
        "description": "Read the full body of one email by its id (from search_email).",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "source_id": {"type": "integer", "description": "Mailbox data source id; omit if only one exists."},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "reply_email",
        "description": "Send a reply to an email. This actually sends mail to the customer - only call it after the user has seen the draft text in this conversation and explicitly approved sending it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "body": {"type": "string", "description": "Plain-text reply body, exactly as approved by the user."},
                "source_id": {"type": "integer", "description": "Mailbox data source id; omit if only one exists."},
            },
            "required": ["message_id", "body"],
        },
    },
    {
        "name": "call_api",
        "description": "Call an endpoint on a configured REST API data source and get the JSON response. Use list_data_sources to see the base URL and what the team says the API provides. Read-only (GET) unless the source explicitly allows writes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the source's base URL, e.g. /customers/42."},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "Defaults to GET."},
                "params": {"type": "object", "description": "Query-string parameters."},
                "body": {"type": "object", "description": "JSON request body for write methods."},
                "source_id": {"type": "integer", "description": "API data source id; omit if only one exists."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_tickets",
        "description": "List support tickets in the internal ticketing system, optionally filtered by status or assignee.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "in_progress", "waiting", "closed"]},
                "assignee": {"type": "string"},
            },
        },
    },
    {
        "name": "create_ticket",
        "description": "Create a support ticket in the internal ticketing system.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "customer": {"type": "string", "description": "Customer/company the ticket is about."},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                "assignee": {"type": "string", "description": "Team member name; omit to leave unassigned."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_ticket",
        "description": "Update fields on an existing ticket (status, priority, assignee, title, description, customer).",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["open", "in_progress", "waiting", "closed"]},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                "assignee": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "customer": {"type": "string"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "comment_ticket",
        "description": "Add a comment to a ticket.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "body": {"type": "string"},
            },
            "required": ["ticket_id", "body"],
        },
    },
    {
        "name": "create_report",
        "description": "Generate an HTML report from data you have gathered. Provide well-structured HTML body content (h2 sections, p, table). Returns a URL the user can open. Use real figures from the data sources.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body_html": {"type": "string", "description": "HTML for the report body (no <html>/<head> wrapper)."},
            },
            "required": ["title", "body_html"],
        },
    },
    {
        "name": "create_presentation",
        "description": "Generate a PowerPoint (.pptx) presentation. Provide a slide list; a title slide is added automatically. Returns a download URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "slides": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "bullets": {"type": "array", "items": {"type": "string"}},
                            "notes": {"type": "string", "description": "Optional speaker notes."},
                        },
                        "required": ["title", "bullets"],
                    },
                },
            },
            "required": ["title", "slides"],
        },
    },
]


def _execute_tool(name: str, args: dict, user: dict) -> str:
    author = user.get("name", "")
    if name == "list_data_sources":
        return json.dumps(datasources.catalog())
    if name == "query_sql":
        return json.dumps(datasources.query_sql(args["sql"], args.get("source_id")))
    if name == "hubspot_query":
        return json.dumps(datasources.hubspot_query(
            args["object_type"], args.get("search", ""), args.get("limit", 20), args.get("source_id")))
    if name == "search_email":
        return json.dumps(datasources.search_email(
            args.get("search", ""), args.get("folder", "inbox"), args.get("limit", 10), args.get("source_id")))
    if name == "read_email":
        return json.dumps(datasources.read_email(args["message_id"], args.get("source_id")))
    if name == "reply_email":
        return json.dumps(datasources.reply_email(args["message_id"], args["body"], args.get("source_id")))
    if name == "call_api":
        return json.dumps(datasources.call_api(
            args["path"], args.get("method", "GET"), args.get("params"), args.get("body"), args.get("source_id")))
    if name == "list_tickets":
        return json.dumps(db.list_tickets(args.get("status"), args.get("assignee")))
    if name == "create_ticket":
        return json.dumps(db.create_ticket(
            title=args["title"], description=args.get("description", ""), customer=args.get("customer", ""),
            priority=args.get("priority", "medium"), assignee=args.get("assignee", ""), created_by=author))
    if name == "update_ticket":
        ticket = db.update_ticket(args.pop("ticket_id"), **args)
        return json.dumps(ticket) if ticket else json.dumps({"error": "ticket not found"})
    if name == "comment_ticket":
        ticket = db.add_comment(args["ticket_id"], author, args["body"])
        return json.dumps(ticket) if ticket else json.dumps({"error": "ticket not found"})
    if name == "create_report":
        return json.dumps(reports.create_report(args["title"], args["body_html"], author))
    if name == "create_presentation":
        return json.dumps(reports.create_presentation(args["title"], args["slides"], author))
    raise ValueError(f"unknown tool {name!r}")


def _serialize_content(content) -> list:
    """Convert response content blocks to plain dicts we can persist and resend."""
    return [block.model_dump(exclude_none=True) for block in content]


def chat(user: dict, message: str) -> dict:
    """Run one user turn through the agent loop. Returns the reply, tool activity, and updated history."""
    history = json.loads(db.get_conversation(user["id"]))
    messages = history + [{"role": "user", "content": message}]
    system = SYSTEM_PROMPT.format(user_name=user["name"], user_email=user["email"])

    tool_events = []
    final_text = ""

    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=8000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "refusal":
            final_text = "I can't help with that request."
            messages.append({"role": "assistant", "content": final_text})
            break

        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": _serialize_content(response.content)})
            continue

        assistant_content = _serialize_content(response.content)
        messages.append({"role": "assistant", "content": assistant_content})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        text_parts = [b.text for b in response.content if b.type == "text"]
        if text_parts:
            final_text = "\n".join(text_parts)

        if response.stop_reason != "tool_use" or not tool_uses:
            break

        results = []
        for tool in tool_uses:
            try:
                output = _execute_tool(tool.name, dict(tool.input), user)
                is_error = False
            except Exception as exc:  # surface tool failures back to the model
                output = f"Error: {exc}"
                is_error = True
            tool_events.append({"tool": tool.name, "input": tool.input, "ok": not is_error})
            results.append({
                "type": "tool_result",
                "tool_use_id": tool.id,
                "content": output,
                "is_error": is_error,
            })
        messages.append({"role": "user", "content": results})
    else:
        final_text = final_text or "I hit the maximum number of steps for one request - ask me to continue."

    db.save_conversation(user["id"], json.dumps(messages))
    return {"reply": final_text, "tool_events": tool_events}
