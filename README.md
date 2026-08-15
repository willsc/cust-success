# Customer Success Hub

A customer-success bot + ticketing system with a multi-user web UI.

**The bot (Claude Opus 5) can:**
- Query any data source the team configures — see below
- Create, update, and comment on **support tickets**
- Read and **reply to Microsoft 365 mail** (drafts are approved by you in chat before sending)
- Generate **reports** (HTML) and **presentations** (.pptx) from the data

**The UI:** Chat, a drag-and-drop ticket board shared by the team, a Data Sources manager, and a Reports library. Light and dark themes; works on phones.

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

### Windows 11

Install [Python 3.10+](https://www.python.org/downloads/) — tick **"Add python.exe to PATH"** during setup — then **double-click `run.bat`**.

Or from PowerShell:

```powershell
.\run.ps1
```

The first run creates a virtual environment and installs dependencies (a minute or two); later runs start immediately. Then open <http://localhost:8300>.

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

Sign in with any name + email — each teammate gets their own login and chat history; the ticket board is shared.

By default the server binds to `127.0.0.1` on Windows (this machine only). To let colleagues on the network reach it, set `HOST=0.0.0.0` and allow the port through Windows Defender Firewall:

```powershell
$env:HOST="0.0.0.0"; .\run.ps1
```

## Configuration

Copy `.env.example` to `.env` in this folder:

```powershell
Copy-Item .env.example .env    # Windows PowerShell
```
```bash
cp .env.example .env           # macOS / Linux
```

Then fill in:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Required for the chat bot (or use `ant auth login`) |
| `HUBSPOT_TOKEN` | Optional default HubSpot token (you can also set it per-source in the UI) |
| `MS_TENANT_ID` / `MS_CLIENT_ID` / `MS_CLIENT_SECRET` / `MS_MAILBOX` | Optional default Entra ID app registration (Application permissions `Mail.Read` + `Mail.Send`, admin-consented) |

Only `ANTHROPIC_API_KEY` really belongs here — every data source can be configured in the UI instead, which is the easier path. Values set in the UI take precedence over these.

## Example prompts

- "What data sources can you see?"
- "Which customers in the usage spreadsheet dropped more than 20% last month?"
- "Cross-reference open deals in HubSpot with health scores from the usage data."
- "Check the inbox for anything urgent and create tickets for real issues."
- "Draft a reply to Maya's renewal email." (the bot shows the draft; it only sends after you approve)
- "Build a QBR presentation for Acme Retail from HubSpot and the usage data."
- "Create a weekly report of open tickets by priority."

## Architecture

```
run.bat / run.ps1  Windows launchers
run.sh             macOS / Linux launcher
app/
  main.py          FastAPI: auth, chat, tickets, data sources, artifacts, static UI
  bot.py           Claude Opus 5 agent loop + tool definitions
  datasources.py   Source registry: types, config, secret masking, query dispatch
  db.py            SQLite: users, tickets, conversations, sources, artifacts
  spreadsheets.py  CSV/XLSX -> SQLite tables
  sqlsource.py     External SQL databases (SQLAlchemy, read-only)
  restsource.py    Generic REST API connector
  hubspot.py       HubSpot CRM client (demo fallback)
  ms365.py         Microsoft Graph mail client (demo fallback)
  reports.py       HTML report + python-pptx presentation generation
  static/          Web UI (vanilla JS, no build step)
data/              SQLite DBs, uploads, generated artifacts (gitignored)
```

Notes:
- Auth is lightweight (name + email → bearer token) — intended for a trusted internal team behind your network/VPN, not the public internet.
- Spreadsheet SQL is enforced read-only; email replies require explicit user approval in chat before the bot calls the send tool.
