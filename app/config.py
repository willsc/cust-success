"""Central configuration, read from environment variables (.env supported)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
ARTIFACT_DIR = DATA_DIR / "artifacts"

for d in (DATA_DIR, UPLOAD_DIR, ARTIFACT_DIR):
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

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5")

HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")

MS_TENANT_ID = os.environ.get("MS_TENANT_ID", "")
MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.environ.get("MS_CLIENT_SECRET", "")
MS_MAILBOX = os.environ.get("MS_MAILBOX", "")  # shared/user mailbox address to operate on

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8300"))
