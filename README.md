# Customer Success Hub

A customer-success bot + ticketing system with a multi-user web UI.

**The bot can:**
- Query any data source the team configures — see below
- Create, update, and comment on **support tickets**
- Read and **reply to Microsoft 365 mail** (drafts are approved by you in chat before sending)
- Generate **reports** (HTML) and **presentations** (.pptx) from the data

It runs on whichever model you point it at — Claude, OpenAI, Gemini, any OpenAI-compatible endpoint, or an open-source model running on your own machine under Ollama (see [Choosing a model](#choosing-a-model)).

**The UI:** Chat, a drag-and-drop ticket board shared by the team, a Data Sources manager, and a Reports library. Light and dark themes; works on phones.

## Tickets

Every ticket is collected with the fields that make routing and SLA reporting possible, rather than "email several mailboxes and hope":

| Field | Type | What it does |
|---|---|---|
| **Owning team / queue** | Picklist — 13 queues, Finance through Exec | Routing |
| **Request type** | Picklist, **dependent on the queue** | Taxonomy. Picking a queue narrows the list |
| **Raised by (CSM)** | User | Distinct from the owner/assignee — drives the notify-back step |
| **Customer ID** | Text | Join key to the tracker (`customer_id_uk_public`) |
| **Response due / Resolution due** | Datetime, stored | The SLA clock, computed on UK business hours |
| **Waiting on** | Picklist — Customer / Internal team / Third party | Pauses the clock |
| **Paused since / Total paused hours** | Datetime, number | Pause accounting |
| **SLA breached (response / resolution)** | Boolean ×2 | Breach reporting, stored so SQL and the bot can filter on it |

Queue, request type, raised-by and customer ID are **required** at creation — by the form, the API and the bot's tool schema alike.

### Where tickets end up

Every ticket is written to the local board first — that never depends on anything external. Then:

- **HubSpot connected** (an enabled HubSpot source with a token): the ticket is committed to the HubSpot *tickets* object — created on first push, updated in place on every later change, including status moves on the board. The card shows `in HubSpot #<id>`.
- **Not connected** (or the push fails): the ticket stays in `data/app.db` and the whole board is written to a spreadsheet — `data/exports/tickets.csv`, plus `tickets.xlsx` when the spreadsheet component is installed. The board header says which of the two is in play and links to both files.

The export is rewritten on every create, update and comment, so it's a live mirror rather than a stale dump — it includes the routing fields, the SLA columns and the sync state.

**Field mapping.** Subject, body and priority map onto HubSpot's native properties (HubSpot has no *urgent*, so urgent rides as High and says so in the body). Status maps onto the pipeline's stages — discovered from your portal automatically, or pinned with **Status → pipeline stage** on the source. Routing fields go to custom HubSpot properties if you map them under **Field → HubSpot property**; anything unmapped is written into the ticket body instead, so nothing is silently dropped.

**Control it** in Settings → *Commit tickets to HubSpot*: `auto` (push as things change, the default), `manual` (only the **Push to HubSpot** button on a ticket), or `off`. A failed push never blocks ticketing — the ticket saves, the card shows `sync failed` with HubSpot's message, and the button retries.

### The SLA clock

There's no native primitive for "8 business hours from now, UK time", so `app/sla.py` is it. Due dates are computed from the ticket's priority in **business hours only** — Mon–Fri, 09:00–17:00 Europe/London, skipping bank holidays, BST included:

| Priority | Response | Resolution |
|---|---|---|
| Urgent | 1h | 8h |
| High | 4h | 16h |
| Medium | 8h | 40h |
| Low | 16h | 80h |

Setting **Waiting on** stops the clock; clearing it books the wait into `total_paused_hours` and pushes both open deadlines out by exactly that much, so customer silence can't burn the SLA. Changing priority or queue retargets the deadlines; editing a due date by hand takes it off the clock.

Breach flags are recomputed on every read and stored, so a report can just ask for `sla_resolution_breached = 1`. Response is met by the first comment from someone other than the raiser, or by the ticket leaving `open`; resolution is met when it's closed.

**Bank holidays** come in all three published calendars (England & Wales, Scotland, Northern Ireland) — they genuinely differ, and 2026 has a Scotland-only World Cup holiday no formula would predict. Each queue picks one in `tickets.QUEUE_CALENDAR`. The built-in list covers 2024–2027 and matches gov.uk exactly; **Settings → SLA calendar → Refresh from gov.uk** pulls the live list (2019–2028) so future substitute days stay right.

### Two placeholders to settle in Phase 0

Both are a single edit each, in `app/tickets.py`:

- `QUEUE_REQUEST_TYPES` — which of the nine request types each queue accepts. What's shipped is a plausible default, not an agreed taxonomy.
- `QUEUE_CALENDAR` — every queue currently follows England & Wales.

SLA targets live in `sla.TARGETS`, with `sla.QUEUE_TARGETS` for per-queue overrides.

## Data sources

Everything the bot can reach is configured in the UI — nothing is hard-coded. Add, edit, test, disable, or delete sources on the **Data Sources** tab.

| Type | What it connects to |
|---|---|
| 📊 **Spreadsheets** | Drop CSV/Excel files onto the card; each sheet becomes a SQL table |
| 🧡 **HubSpot CRM** | Contacts, companies, deals, tickets — plus any custom properties you name |
| 📧 **Microsoft 365 Mail** | Shared mailboxes to read and reply from, plus calendars, via Microsoft Graph |
| 🗄️ **SQL Database** | PostgreSQL, MySQL, SQL Server or SQLite. Read-only |
| 🔌 **REST API** | Any JSON HTTP API — Zendesk, Jira, Stripe, an internal service. Bearer / API-key / basic auth. Read-only unless you allow writes |

You can add **several of the same type** (two spreadsheet sets, a prod and a staging database) and the bot picks between them.

### Connecting HubSpot and Microsoft 365

Both are connected by **signing in**, not by pasting a secret. The source card shows a **Connect** button; the user signs in on Microsoft's or HubSpot's own page and comes back connected, and the card then says who as. Nobody types a password into this app, and no client secret has to be handed round a team.

- **Microsoft 365** uses the device-code flow: a short code to enter at `microsoft.com/devicelogin`. No redirect URL to register, so it behaves the same on localhost, a LAN address or behind the Windows service.
- **HubSpot** uses the standard redirect flow, because HubSpot offers nothing else. The redirect URL to register is shown in the source's dialog, already matching the address you reached the app on.

Each still needs a **one-time app registration** by whoever set the app up — that is what OAuth requires, and it is the only part that cannot be removed:

| | What to register once |
|---|---|
| Microsoft 365 | An Entra ID app registration with **Allow public client flows** on, and *delegated* Graph permissions: `Mail.Read`, `Mail.Send`, `Calendars.Read`, plus the `.Shared` variants to reach shared mailboxes. Paste the **Application (client) ID** — there is no secret |
| HubSpot | A HubSpot app; paste its client ID and secret, and add the redirect URL the dialog shows to the app's Auth tab |

A signed-in user reaches **their own** mailbox and calendar, plus shared mailboxes they already have access to. That is usually what a CSM wants, and it is a good deal narrower than the alternative.

The older path is still there, under **Advanced** in the source's dialog: an app registration with *application* permissions and a client secret, or a HubSpot private-app token. Existing installs keep working untouched. Application permissions reach **every** mailbox in the tenant, which is why the mailbox allowlist matters more on that path — see [MCP servers](#several-mailboxes-not-one).

### Pulling the data in, so it can be aggregated

Connected sources answer questions one record at a time, over the network. That is the wrong shape for the questions a customer success team actually asks — pipeline by stage, mail volume per account, this quarter against last — and none of it can be joined to a spreadsheet.

So each HubSpot and mailbox source has a **Sync** button. It pulls the records into the same local SQLite store the uploaded spreadsheets live in:

| Source | Tables it creates |
|---|---|
| HubSpot CRM | `hubspot_contacts`, `hubspot_companies`, `hubspot_deals`, `hubspot_tickets` |
| Microsoft 365 Mail | `mail_messages` — one row per message header, across every mailbox the source can reach |

The card then shows what came in and when. Because it is all one database, the bot can total, group and **join across services** in a single query — a synced deal list against mail volume against an uploaded usage export:

```sql
SELECT c.company, COUNT(m.id) AS emails, d.dealname, d.amount
FROM hubspot_contacts c
LEFT JOIN mail_messages m  ON m.from_address = c.email
LEFT JOIN hubspot_deals  d ON d.dealname LIKE c.company || '%'
GROUP BY c.company ORDER BY emails DESC
```

Just ask the console for the aggregate in plain English — it writes the SQL itself.

Things worth knowing:

- A sync **replaces** each table rather than merging. These are thousands of rows, not millions; a full pull is easy to reason about, and a half-updated table is worse than a slightly stale one.
- Figures come from the last sync, not from this second. The bot is told to quote `synced_at` alongside an aggregate so stale numbers are not mistaken for live ones; use the live tools when the answer must be current.
- Mail sync stores **headers only** — subject, sender, date, preview. Bodies are large and rarely what an aggregate needs; the bot can still fetch one on demand.
- A source that has not been connected yet syncs its demo data, which is a fair way to see the shape of it before wiring anything up.
- Ceilings of 5,000 records per CRM object and 1,000 messages per mailbox keep one sync from running away.

**The description field matters.** Each source has a "what's in it" box that the bot reads to decide when to use that source — so write it for the bot: *"Monthly product usage exports per customer: seats, logins, feature adoption."* Better descriptions mean better answers.

Other things worth knowing:
- **Test** checks credentials before you rely on them; **Schema** shows exactly which tables, columns, or objects the bot can see.
- Secrets are stored server-side and never sent back to the browser — the UI shows `••••••••` and leaves the stored value alone unless you type a new one.
- Sources you haven't configured fall back to realistic demo data, so the app is usable before any credentials exist.
- SQL sources are restricted to single `SELECT`/`WITH` statements; write and DDL keywords are rejected.

### Adding a new source type

Add an entry to `TYPES` in `app/datasources.py` (label, icon, and its config fields) and a connector module with `test_connection()` plus whatever query function it needs. The UI builds its form from that declaration — no frontend changes required.

## Quick start

### Windows 11 — installer

Run **`CustomerSuccessHub-<version>-setup.exe`** and click through it. Nothing else is needed: Python comes bundled, and the app installs per-user so **no administrator rights are required**.

- Installs to `%LOCALAPPDATA%\Programs\Customer Success Hub`; data (databases, uploads, ticket exports) goes in `%LOCALAPPDATA%\CustomerSuccessHub`, and the wizard lets you change the port, the data folder, and whether it listens beyond this machine.
- Tick **Run as a Windows service** and it registers *Customer Success Hub* in `services.msc`, starting with the machine and restarting itself if it dies. That single step asks for administrator; everything else doesn't.
- Start Menu gets *Customer Success Hub* (opens the browser, starting the server first if needed), *Run in this window*, and a shortcut to the data folder.
- Uninstall from Apps & Features. It removes the service and firewall rule and **leaves your data folder alone** — reinstalling over the top upgrades in place and keeps everything.

The bundled runtime has its own pip, so the Components panel still installs spreadsheets, SQL drivers and decks on demand.

Don't have the installer? Build it: [`installer/README.md`](installer/README.md), or run the **Windows installer** workflow in the Actions tab. It's unsigned, so SmartScreen warns on first run — *More info → Run anyway*, or sign it with your own certificate.

### Windows 11 — one command, from source

```powershell
powershell -ExecutionPolicy Bypass -File installer\install.ps1
```

Fetches a private Python, installs everything, makes shortcuts, connects Claude Desktop to the [MCP servers](#mcp-servers--the-same-connectors-outside-this-app) and opens the app — no administrator, no prerequisites, not even Python. `-Uninstall` removes it again, leaving your data folder alone. See [installer/README.md](installer/README.md) for every option.

**To have it run in the background as a Windows service**, add `-Service`:

```powershell
powershell -ExecutionPolicy Bypass -File installer\install.ps1 -Service
```

It then starts with the machine (delayed auto-start), restarts itself if it dies, and writes rolling logs. This is the one step that asks for administrator, and it elevates just that step. Add `-Network` as well to let colleagues reach it — that opens the firewall port too. Manage it afterwards with `service.ps1 -Action status|start|stop|restart`, or from `services.msc` as **Customer Success Hub**.

### Windows 11 — from source

For development, or if you'd rather not install: get [Python 3.10+](https://www.python.org/downloads/) — tick **"Add python.exe to PATH"** — then **double-click `run.bat`**.

Or from PowerShell:

```powershell
.\run.ps1
```

The first run creates a virtual environment and installs a small base of dependencies (well under a minute); later runs start immediately, and only reinstall when `requirements.txt` actually changes. Then open <http://localhost:8300>.

If PowerShell refuses to run the script, allow local scripts once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

If you copied this folder from a Mac or Linux machine rather than cloning it, delete the `.venv` folder first — the launcher will rebuild it for Windows.

### macOS / Linux

```bash
chmod +x run.sh
./run.sh                # http://localhost:8300
```

### Optional components — installed from the UI

The base install is only the web server, the Claude SDK, `httpx` (which is all the other model providers need) and the MCP runtime. The heavier pieces are installed **from the Sources tab, one click, when you first need them** — no terminal, no restart, and a machine that never touches spreadsheets never downloads pandas.

| Component | Installs | Needed for |
|---|---|---|
| Spreadsheet engine | `pandas`, `openpyxl` | Uploading CSV/Excel files |
| SQL toolkit | `sqlalchemy` | Any SQL Database source (SQLite works with this alone) |
| PostgreSQL driver | `psycopg2-binary` | `postgresql://` connection URLs |
| MySQL driver | `pymysql` | `mysql://` connection URLs |
| Presentation builder | `python-pptx` | Bot-generated `.pptx` decks |

A **Components** panel appears at the top of the Sources tab listing anything missing, with an Install button and a live pip log. Data source cards that need a component say so and offer to install it; picking such a type from **Connect** starts the download while you fill in the form. Progress survives a page reload — the install runs server-side.

Two ways to skip all of that:

- Preinstall everything: `.venv/bin/pip install -r requirements-optional.txt` (Windows: `.venv\Scripts\pip install -r requirements-optional.txt`). Useful for offline machines or a prebuilt image.
- Turn one-click installs off entirely: set `DISABLE_UI_INSTALL=1`. The panel then shows the `pip install` command to run by hand instead.

Components are declared in `app/deps.py` — the API takes a component key, never a package name, so nothing typed in the browser reaches pip.

Sign in with any name + email — each teammate gets their own login and chat history; the ticket board is shared.

By default the server binds to `127.0.0.1` on Windows (this machine only). To let colleagues on the network reach it, set `HOST=0.0.0.0` and allow the port through Windows Defender Firewall:

```powershell
$env:HOST="0.0.0.0"; .\run.ps1
```

## Configuration — keys and credentials

**No files to edit.** Click the **⚙ gear** in the top bar and fill in the Settings dialog; values are saved on the server and take effect on the next message — no restart. If no Claude key is set, the console says so on sign-in and offers the dialog straight away.

| Setting | Purpose |
|---|---|
| **Provider** | Which LLM answers — Claude, OpenAI, Gemini, Ollama, or any OpenAI-compatible endpoint. See [Choosing a model](#choosing-a-model) |
| **Model** | Blank uses the provider's default (`claude-opus-5`, `gpt-4o`, `gemini-2.5-pro`, `qwen3:8b`). **List models** asks the endpoint what it actually serves |
| **Claude API key** | Used when the provider is Claude. [console.anthropic.com](https://console.anthropic.com) → API keys |
| **API key (other providers)** | OpenAI, Gemini, or your own endpoint. A local Ollama needs none |
| **Base URL** | Only for Ollama and OpenAI-compatible endpoints |
| **Tool calling** | `auto` / `native` / `prompted` — see [Choosing a model](#choosing-a-model) |
| **Request timeout** | Seconds to wait for a reply; raise it for slow local models |
| **HubSpot private app token** | Fallback for HubSpot sources that use the older token path rather than signing in |
| **Commit tickets to HubSpot** | `auto` / `manual` / `off` — see [Where tickets end up](#where-tickets-end-up) |
| **Microsoft tenant / client ID / client secret / default mailbox** | Fallback Entra ID app registration for the older client-secret path (Application permissions `Mail.Read` + `Mail.Send`, admin-consented; `Calendars.Read` for calendar tools). Signing in on the Sources tab needs none of this beyond the client ID |
| **Additional mailboxes** | Further mailboxes the bot and MCP server may read. Filling it in makes it an allowlist — see [MCP servers](#several-mailboxes-not-one) |

**Test model connection** in that dialog does a one-token round trip, so you know the provider, key and model work before anyone asks a question.

Each field shows where its current value comes from — *set here*, *from environment*, or *default*. Secrets are never sent back to the browser: you see `••••••••`, and leaving that alone keeps the stored value. Clearing a box removes the override and falls back to the environment again.

Data source credentials (per-source HubSpot tokens, database URLs, REST API keys) live on the **Sources** tab instead — a per-source value always beats the shared fallback above.

<details>
<summary>Prefer environment variables?</summary>

Still supported, and useful for automated deployments. Copy `.env.example` to `.env` in this folder (`cp .env.example .env`, or `Copy-Item .env.example .env` in PowerShell) and fill in `LLM_PROVIDER`, `LLM_MODEL`, `ANTHROPIC_API_KEY` / `LLM_API_KEY`, `LLM_BASE_URL`, `HUBSPOT_TOKEN`, `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_MAILBOX`, `MS_MAILBOXES`. Real environment variables work too, as does an `ant auth login` session for the Claude key.

Resolution order for every setting: **UI value → environment/`.env` → default.**
</details>

Settings are stored in `data/app.db` (gitignored) in plain text, like the per-source credentials the app already keeps there — that file is as sensitive as the keys in it. Anyone signed in can view field names and change values; they can never read a stored secret back.

## MCP servers — the same connectors, outside this app

The Microsoft 365 and HubSpot connectors are also published as **MCP servers**, so Claude Code, Claude Desktop or any other MCP client can read the team's mailboxes and CRM directly. They are separate processes in `mcp_servers/`, sharing this app's configuration: whatever you set up on the Sources tab is what they use.

| Server | Tools |
|---|---|
| **`ms365`** — Outlook mail & calendar | `list_mailboxes`, `list_folders`, `search_mail`, `read_mail`, `list_calendar_events`, `check_availability` — plus `reply_to_mail` and `send_mail` under `--allow-writes` |
| **`hubspot`** — CRM (read-only) | `list_object_types`, `search_records`, `get_record`, `list_associated_records` |

### Several mailboxes, not one

A customer success team rarely has one inbox. Every mail and calendar tool takes an optional `mailbox` argument; leave it out and the source's **default mailbox** is used, or call `list_mailboxes` to see what is reachable.

Which mailboxes *are* reachable depends on one field — **Additional mailboxes** on the Microsoft 365 source (or `MS_MAILBOXES`):

- **Left blank** — any mailbox the Entra ID app registration can see. Client-credentials `Mail.Read` is tenant-wide by default, so this means every mailbox in the tenant. Scope it in Entra with an [application access policy](https://learn.microsoft.com/en-us/graph/auth-limit-mailbox-access).
- **Filled in** — an allowlist. Only the addresses you list, plus the default mailbox, can be read; anything else is refused before a request reaches Graph.

The same applies to the bot inside the app, which gained a `list_mailboxes` tool and a `mailbox` argument on `search_email` / `read_email` / `reply_email`.

### Connecting a client

These servers are entirely optional — the app itself does not need them, and most people will not want them. They exist for driving the same data from an assistant.

**Claude Code** opened in this folder needs nothing: the repo ships a `.mcp.json` and picks both servers up read-only. On Windows change `.venv/bin/python` to `.venv\Scripts\python.exe`.

For any other client, point it at the script with an absolute path (the servers do not care what directory they start in):

```bash
claude mcp add ms365 -- /path/to/cust-success/.venv/bin/python /path/to/cust-success/mcp_servers/ms365_server.py
claude mcp add hubspot -- /path/to/cust-success/.venv/bin/python /path/to/cust-success/mcp_servers/hubspot_server.py
```

Both take `--source-id N` to pick a particular data source when the team has more than one of a type (`CSHUB_MS365_SOURCE_ID` / `CSHUB_HUBSPOT_SOURCE_ID` do the same). With no flag they use the only enabled source of that type, and fall back to the shared `MS_*` / `HUBSPOT_TOKEN` settings — and then to demo data — if none exists. Configuration is re-read on every call, so editing a source on the Sources tab takes effect without restarting the client.

### Sending mail is opt-in

The mail server registers only reading tools by default: a client connected to it cannot email anyone. Add `--allow-writes` to also register `reply_to_mail` and `send_mail`, which are marked as destructive so a client can prompt before calling them.

```bash
claude mcp add ms365 -- /path/to/.venv/bin/python /path/to/mcp_servers/ms365_server.py --allow-writes
```

The HubSpot server has no write tools at all.

### Nothing to install

The servers' runtime (the `mcp` package) is part of the **base install**, not an optional component, so it is already there however the app arrived:

- **Windows installer** — baked into the bundled runtime, and `mcp_servers/` ships in the payload. Works offline.
- **From source** — `run.sh` / `run.ps1` / `run.bat` reinstall whenever `requirements.txt` changes, so the next launch after an update picks it up.
- **Neither** — if a client is ever pointed at an environment that somehow lacks it, the server installs it into its own interpreter on first launch, reports progress on stderr, and carries on. A client that gave up waiting during that install connects normally on its next attempt.

That last fallback refuses to act in two cases, and says so instead: when `DISABLE_UI_INSTALL=1` is set, and when the interpreter is a shared or system Python rather than this app's own — nothing pips into a system interpreter uninvited.

It is a base dependency precisely because an MCP client launches these servers itself and cannot stop halfway to install something, and "run `pip install`" is not an instruction this app's users should ever have to act on.

## Choosing a model

The bot is provider-agnostic: `app/llm.py` translates between the app and whatever you point it at. Switch providers in **Settings → Model provider**; it takes effect on the next message, and conversation history survives the switch.

| Provider | Set | Notes |
|---|---|---|
| **Claude (Anthropic)** | Claude API key | The default, and the best results here — this agent leans hard on tool use |
| **OpenAI** | API key, model (`gpt-4o`, …) | Native function calling |
| **Google Gemini** | API key, model (`gemini-2.5-pro`, …) | Native function calling |
| **Ollama** | Base URL (default `http://localhost:11434`), model (`qwen3:8b`, …) | Open-source models on your own machine. No key, no data leaving the box |
| **OpenAI-compatible** | Base URL, model, key if the endpoint wants one | Azure OpenAI, vLLM, LM Studio, llama.cpp, Groq, Together, OpenRouter, DeepSeek, Qwen/DashScope, a private gateway |

Nothing extra to install for any of them — the base install already has the Claude SDK and `httpx`.

### Running an open-source model locally (Windows)

Qwen3 8B is a good starting point: it fits on a 16 GB machine, and it supports tool calling, which this bot depends on.

1. Install **Ollama for Windows** from [ollama.com/download](https://ollama.com/download). It runs as a background service on `http://localhost:11434`.
2. Pull the model in PowerShell:
   ```powershell
   ollama pull qwen3:8b
   ```
3. Start the hub (`.\run.ps1`), open the **⚙ gear**, and set:
   - **Provider** → `Ollama — local open-source models`
   - **Model** → `qwen3:8b` (or press **List models** and pick from what's installed)
   - Leave both API keys blank
4. Press **Test model connection**. Once it answers, ask the bot something.

A GPU is not required, but on CPU alone expect answers to take a while — each question is several model turns, one per tool call. If replies get cut off, raise **Request timeout**.

Other local runtimes work through **OpenAI-compatible**: vLLM at `http://localhost:8000/v1`, LM Studio at `http://localhost:1234/v1`, `llama-server` at `http://localhost:8080/v1`. For **Azure OpenAI**, paste the whole deployment URL as the base URL (`https://<resource>.openai.azure.com/openai/deployments/<deployment>/chat/completions?api-version=2024-10-21`) and put the key in **API key (other providers)** — it is sent as both `Authorization: Bearer` and `api-key`.

### Tool calling, and what to expect from smaller models

Every answer this bot gives comes from tools — listing data sources, running SQL, reading mail, raising tickets. That makes tool-calling ability the thing that decides whether a model is usable here, more than its size.

**Tool calling** in Settings picks how the calls are made:

- **auto** (default) — use the endpoint's native tool calling, and fall back to the prompted protocol the first time an endpoint says it doesn't support tools.
- **native** — require native tool calling; fail loudly if it isn't there.
- **prompted** — always use the prompted protocol: the tools are described in the system prompt and the model replies with a JSON block, which the app parses back into a real tool call. This is what makes models with no tool-calling support usable at all.

Practical guidance:

- **7–8B and up, tool-trained** (Qwen3, Llama 3.1, Mistral, Command-R) — works. Qwen3's `<think>` reasoning is stripped out of what you see.
- **Below ~7B, or not tool-trained** — try `prompted`. Expect it to need more nudging and to sometimes pick the wrong source; keep questions specific.
- **Any local model** — the bot's guardrails (approve email drafts before sending, never invent customer data) are system-prompt instructions. A frontier model follows them reliably; a small local one is less dependable, so watch the first few email and ticket actions before trusting it unattended.

## Example prompts

- "What data sources can you see?"
- "Which customers in the usage spreadsheet dropped more than 20% last month?"
- "Cross-reference open deals in HubSpot with health scores from the usage data."
- "Check the inbox for anything urgent and create tickets for real issues."
- "Draft a reply to Maya's renewal email." (the bot shows the draft; it only sends after you approve)
- "Build a QBR presentation for Acme Retail from HubSpot and the usage data."
- "Create a weekly report of open tickets by priority."
- "Which tickets have breached their resolution SLA this month, by queue?"
- "Raise a DSAR for Acme against customer 8842 — Data Protection queue, urgent."

## Architecture

```
run.bat / run.ps1  Windows launchers (from source)
run.sh             macOS / Linux launcher
installer/         Windows installer: bundled runtime, service, Inno Setup script
requirements.txt   Base install (small); requirements-optional.txt has the rest
app/
  main.py          FastAPI: auth, chat, tickets, data sources, artifacts, static UI
  bot.py           Agent loop + tool definitions (provider-independent)
  llm.py           Model providers: Claude, OpenAI, Gemini, Ollama, OpenAI-compatible
  datasources.py   Source registry: types, config, secret masking, query dispatch
  tickets.py       Ticket taxonomy: queues, dependent request types, validation
  ticketsync.py    Commits tickets to HubSpot; writes the local CSV/XLSX mirror
  sla.py           UK business-hours clock: due dates, pause accounting, bank holidays
  settings.py      API keys + shared credentials: UI value > environment > default
  deps.py          Optional component packs + the pip installer the UI drives
  db.py            SQLite: users, tickets, conversations, sources, settings, artifacts
  spreadsheets.py  CSV/XLSX -> SQLite tables
  sqlsource.py     External SQL databases (SQLAlchemy, read-only)
  restsource.py    Generic REST API connector
  oauth.py         Signing in to Microsoft (device code) and HubSpot (redirect);
                   token storage and refresh, so nobody handles a secret
  ingest.py        Syncs HubSpot records and mailbox headers into the local SQLite
                   store, so they can be counted, grouped and joined
  hubspot.py       HubSpot CRM client (demo fallback)
  ms365.py         Microsoft Graph mail + calendar client, multi-mailbox (demo fallback)
  reports.py       HTML report + python-pptx presentation generation
  static/          Web UI (vanilla JS, no build step)
mcp_servers/       MCP servers exposing the connectors to any MCP client (shipped by the installer too)
  ms365_server.py  Outlook mail + calendar over stdio; sending is opt-in
  hubspot_server.py  HubSpot CRM over stdio, read-only
  _common.py       Source lookup, error text, the shared argument parser
.mcp.json          Registers both servers for Claude Code opened in this folder
data/              SQLite DBs, uploads, generated artifacts, ticket exports (gitignored)
                   — set CSHUB_DATA_DIR to put this somewhere else, as the installer does
```

Notes:
- Auth is lightweight (name + email → bearer token) — intended for a trusted internal team behind your network/VPN, not the public internet. There are no roles: everyone who can sign in can edit settings and data sources.
- Installing components needs a signed-in user and only accepts the component keys in `app/deps.py`; set `DISABLE_UI_INSTALL=1` to switch it off on a locked-down box.
- Spreadsheet SQL is enforced read-only; email replies require explicit user approval in chat before the bot calls the send tool.
- OAuth tokens are stored server-side in `data/app.db` and never sent to the browser: the UI is told who is signed in, not what with. Editing a source cannot overwrite or clear them.
- The MCP servers are read-only unless started with `--allow-writes`, and honour the mailbox allowlist before any request reaches Graph. They are as trusted as the client you connect them to — an MCP client with the mail server attached can read every mailbox the source permits.
