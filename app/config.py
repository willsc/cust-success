"""Paths and process-level configuration.

API keys and credentials are NOT here — they live in `settings.py`, which reads
them from the UI first and falls back to these environment variables, so they
can be changed without editing a file or restarting.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
ARTIFACT_DIR = DATA_DIR / "artifacts"
EXPORT_DIR = DATA_DIR / "exports"     # local ticket spreadsheet, written on every change

for d in (DATA_DIR, UPLOAD_DIR, ARTIFACT_DIR, EXPORT_DIR):
    d.mkdir(parents=True, exist_ok=True)


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

APP_DB = DATA_DIR / "app.db"        # users, tickets, conversations
SHEETS_DB = DATA_DIR / "sheets.db"  # uploaded spreadsheet data

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8300"))
