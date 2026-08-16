"""Optional dependency packs — declared here, installed from the UI on demand.

The base install (requirements.txt) is deliberately small: the web server, the
Claude SDK and httpx. Everything heavier is a *pack* declared below — pandas for
spreadsheets, SQLAlchemy plus a driver for databases, python-pptx for decks — so
the first run is quick and a machine that never touches spreadsheets never
compiles pandas.

Modules that need a pack call `require()` right before they import it, which
turns a raw ImportError into an instruction the UI can act on. The Sources tab
reads `overview()` and installs a pack with `install()`, which shells out to
`pip` in a background thread and streams the log back to the browser.

Only the pack keys below can ever be installed — the API takes a key, never a
package name, so nothing the browser sends reaches pip's command line.
"""
import importlib
import importlib.util
import os
import subprocess
import sys
import threading
import time
from itertools import count

# key -> what it unlocks and what pip needs to install for it.
PACKS = {
    "spreadsheets": {
        "label": "Spreadsheet engine",
        "blurb": "Reads uploaded CSV and Excel files into tables the bot can query with SQL.",
        "packages": ["pandas", "openpyxl"],
        "modules": ["pandas", "openpyxl"],
        "size": "~45 MB",
    },
    "sql": {
        "label": "SQL toolkit",
        "blurb": "Connects to external databases (SQLAlchemy). SQLite works with this alone.",
        "packages": ["sqlalchemy"],
        "modules": ["sqlalchemy"],
        "size": "~10 MB",
    },
    "sql_postgres": {
        "label": "PostgreSQL driver",
        "blurb": "Needed for postgresql:// connection URLs.",
        "packages": ["psycopg2-binary"],
        "modules": ["psycopg2"],
        "size": "~3 MB",
    },
    "sql_mysql": {
        "label": "MySQL driver",
        "blurb": "Needed for mysql:// connection URLs.",
        "packages": ["pymysql"],
        "modules": ["pymysql"],
        "size": "~1 MB",
    },
    "decks": {
        "label": "Presentation builder",
        "blurb": "Lets the bot generate .pptx decks on the Output tab.",
        "packages": ["python-pptx"],
        "modules": ["pptx"],
        "size": "~10 MB",
    },
}

# Data source type -> packs it needs. `required` must be present for the type to
# work at all; `optional` are drivers only some connection URLs need.
TYPE_PACKS = {
    "spreadsheet": {"required": ["spreadsheets"], "optional": []},
    "sql_database": {"required": ["sql"], "optional": ["sql_postgres", "sql_mysql"]},
    "hubspot": {"required": [], "optional": []},
    "ms365_mail": {"required": [], "optional": []},
    "rest_api": {"required": [], "optional": []},
}

# SQLAlchemy URL prefix -> driver pack, so a missing driver names its own fix.
URL_PACKS = {
    "postgresql": "sql_postgres",
    "postgres": "sql_postgres",
    "mysql": "sql_mysql",
    "mariadb": "sql_mysql",
}

LOG_LINES = 300


def install_enabled() -> bool:
    """One-click installs can be switched off on a locked-down server."""
    return os.getenv("DISABLE_UI_INSTALL", "").strip().lower() not in ("1", "true", "yes")


class MissingDependency(RuntimeError):
    """Raised instead of ImportError so the caller can offer to install the pack."""

    def __init__(self, pack: str):
        self.pack = pack
        label = PACKS[pack]["label"]
        super().__init__(
            f"{label} is not installed yet. Open the Sources tab and install it "
            f"from the Components panel (one click, no restart)."
        )


# ---------- status ----------

def module_present(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def missing_modules(key: str) -> list[str]:
    return [m for m in PACKS[key]["modules"] if not module_present(m)]


def is_installed(key: str) -> bool:
    return not missing_modules(key)


def require(key: str) -> None:
    """Call before importing a pack's modules."""
    if not is_installed(key):
        raise MissingDependency(key)


def pack_for_url(url: str) -> str | None:
    """The driver pack an SQLAlchemy URL needs, if any."""
    scheme = (url or "").split(":", 1)[0].split("+", 1)[0].strip().lower()
    return URL_PACKS.get(scheme)


def _pack_status(key: str) -> dict:
    spec = PACKS[key]
    return {
        "key": key,
        "label": spec["label"],
        "blurb": spec["blurb"],
        "packages": spec["packages"],
        "size": spec.get("size", ""),
        "installed": is_installed(key),
    }


def type_status(type_: str) -> dict:
    entry = TYPE_PACKS.get(type_, {"required": [], "optional": []})
    required = [_pack_status(k) for k in entry["required"]]
    return {
        "required": required,
        "optional": [_pack_status(k) for k in entry["optional"]],
        "ready": all(p["installed"] for p in required),
    }


def overview() -> dict:
    """Everything the Sources tab needs to render install prompts."""
    return {
        "install_enabled": install_enabled(),
        "in_venv": sys.prefix != sys.base_prefix,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "packs": [_pack_status(k) for k in PACKS],
        "types": {t: type_status(t) for t in TYPE_PACKS},
        "job": _public_job(_current_job()),
    }


# ---------- install jobs ----------

_jobs: dict[int, dict] = {}
_job_ids = count(1)
_lock = threading.Lock()


def _current_job() -> dict | None:
    running = [j for j in _jobs.values() if j["state"] == "running"]
    if running:
        return running[-1]
    return max(_jobs.values(), key=lambda j: j["id"], default=None)


def _public_job(job: dict | None) -> dict | None:
    if not job:
        return None
    return {
        "id": job["id"], "keys": job["keys"], "labels": job["labels"],
        "packages": job["packages"], "state": job["state"],
        "log": job["log"][-40:], "error": job["error"],
        "started_at": job["started_at"], "finished_at": job["finished_at"],
    }


def job(job_id: int) -> dict | None:
    return _public_job(_jobs.get(job_id))


def install(keys: list[str]) -> dict:
    """Start a pip install for the named packs. Raises ValueError on bad input."""
    if not install_enabled():
        raise ValueError("Installing from the UI is disabled on this server (DISABLE_UI_INSTALL).")

    unknown = [k for k in keys if k not in PACKS]
    if unknown:
        raise ValueError(f"Unknown component: {', '.join(unknown)}")
    keys = [k for k in dict.fromkeys(keys)]
    if not keys:
        raise ValueError("Nothing to install.")

    with _lock:
        running = next((j for j in _jobs.values() if j["state"] == "running"), None)
        if running:
            raise RuntimeError(f"Already installing {', '.join(running['labels'])} — wait for it to finish.")

        packages = [pkg for k in keys for pkg in PACKS[k]["packages"]]
        job_ = {
            "id": next(_job_ids), "keys": keys, "labels": [PACKS[k]["label"] for k in keys],
            "packages": packages, "state": "running", "log": [], "error": "",
            "started_at": time.time(), "finished_at": None,
        }
        _jobs[job_["id"]] = job_

    threading.Thread(target=_run, args=(job_,), daemon=True).start()
    return _public_job(job_)


def _log(job_: dict, line: str) -> None:
    job_["log"].append(line.rstrip())
    del job_["log"][:-LOG_LINES]


def _run(job_: dict) -> None:
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *job_["packages"]]
    _log(job_, "$ " + " ".join(cmd))
    try:
        env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, env=env)
        for line in proc.stdout:
            _log(job_, line)
        code = proc.wait()
    except Exception as exc:  # pip missing, python gone, permissions
        job_.update(state="failed", error=str(exc), finished_at=time.time())
        _log(job_, f"ERROR: {exc}")
        return

    # A fresh install lands in a directory the interpreter already has on
    # sys.path, so the running server can import it once the caches are dropped.
    importlib.invalidate_caches()
    still_missing = [m for k in job_["keys"] for m in missing_modules(k)]

    if code != 0:
        job_.update(state="failed", error=f"pip exited with code {code}.")
    elif still_missing:
        job_.update(state="failed", error=f"pip finished but {', '.join(still_missing)} still won't import.")
    else:
        job_.update(state="done")
    job_["finished_at"] = time.time()
