# Customer Success Hub

A customer-success bot + ticketing system with a multi-user web UI.

**The bot (Claude Opus 5) can:**
- Query any data source the team configures — see below
- Create, update, and comment on **support tickets**
- Read and **reply to Microsoft 365 mail** (drafts are approved by you in chat before sending)
- Generate **reports** (HTML) and **presentations** (.pptx) from the data

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
| 📧 **Microsoft 365 Mail** | A shared mailbox to read and reply from, via Microsoft Graph |
| 🗄️ **SQL Database** | PostgreSQL, MySQL, SQL Server or SQLite. Read-only |
| 🔌 **REST API** | Any JSON HTTP API — Zendesk, Jira, Stripe, an internal service. Bearer / API-key / basic auth. Read-only unless you allow writes |

You can add **several of the same type** (two spreadsheet sets, a prod and a staging database) and the bot picks between them.

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

The base install is only the web server and the Claude SDK. The heavier pieces are installed **from the Sources tab, one click, when you first need them** — no terminal, no restart, and a machine that never touches spreadsheets never downloads pandas.

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
| **Claude API key** | Required for the chat bot. [console.anthropic.com](https://console.anthropic.com) → API keys |
| **Model** | Defaults to `claude-opus-5` |
| **HubSpot private app token** | Fallback for HubSpot sources with no token of their own |
| **Commit tickets to HubSpot** | `auto` / `manual` / `off` — see [Where tickets end up](#where-tickets-end-up) |
| **Microsoft tenant / client ID / client secret / default mailbox** | Fallback Entra ID app registration (Application permissions `Mail.Read` + `Mail.Send`, admin-consented) |

**Test Claude key** in that dialog does a one-token round trip, so you know the key and model work before anyone asks a question.

Each field shows where its current value comes from — *set here*, *from environment*, or *default*. Secrets are never sent back to the browser: you see `••••••••`, and leaving that alone keeps the stored value. Clearing a box removes the override and falls back to the environment again.

Data source credentials (per-source HubSpot tokens, database URLs, REST API keys) live on the **Sources** tab instead — a per-source value always beats the shared fallback above.

<details>
<summary>Prefer environment variables?</summary>

Still supported, and useful for automated deployments. Copy `.env.example` to `.env` in this folder (`cp .env.example .env`, or `Copy-Item .env.example .env` in PowerShell) and fill in `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `HUBSPOT_TOKEN`, `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_MAILBOX`. Real environment variables work too, as does an `ant auth login` session for the Claude key.

Resolution order for every setting: **UI value → environment/`.env` → default.**
</details>

Settings are stored in `data/app.db` (gitignored) in plain text, like the per-source credentials the app already keeps there — that file is as sensitive as the keys in it. Anyone signed in can view field names and change values; they can never read a stored secret back.

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
  bot.py           Claude Opus 5 agent loop + tool definitions
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
  hubspot.py       HubSpot CRM client (demo fallback)
  ms365.py         Microsoft Graph mail client (demo fallback)
  reports.py       HTML report + python-pptx presentation generation
  static/          Web UI (vanilla JS, no build step)
data/              SQLite DBs, uploads, generated artifacts, ticket exports (gitignored)
                   — set CSHUB_DATA_DIR to put this somewhere else, as the installer does
```

Notes:
- Auth is lightweight (name + email → bearer token) — intended for a trusted internal team behind your network/VPN, not the public internet. There are no roles: everyone who can sign in can edit settings and data sources.
- Installing components needs a signed-in user and only accepts the component keys in `app/deps.py`; set `DISABLE_UI_INSTALL=1` to switch it off on a locked-down box.
- Spreadsheet SQL is enforced read-only; email replies require explicit user approval in chat before the bot calls the send tool.
