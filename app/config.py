"""Paths and process-level configuration.

API keys and credentials are NOT here — they live in `settings.py`, which reads
them from the UI first and falls back to these environment variables, so they
can be changed without editing a file or restarting.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# Where the databases, uploads and generated files live. Normally alongside the
# code; the Windows installer points this at %LOCALAPPDATA% instead, because
# nothing under Program Files or a service account's profile is a sane place to
# write a database.
DATA_DIR = Path(os.environ.get("CSHUB_DATA_DIR") or (BASE_DIR / "data")).expanduser()
UPLOAD_DIR = DATA_DIR / "uploads"
ARTIFACT_DIR = DATA_DIR / "artifacts"
EXPORT_DIR = DATA_DIR / "exports"     # local ticket spreadsheet, written on every change

for d in (DATA_DIR, UPLOAD_DIR, ARTIFACT_DIR, EXPORT_DIR):
    d.mkdir(parents=True, exist_ok=True)

APP_DB = DATA_DIR / "app.db"        # users, tickets, conversations
SHEETS_DB = DATA_DIR / "sheets.db"  # uploaded spreadsheet data

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8300"))
