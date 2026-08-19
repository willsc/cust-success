"""Shared plumbing for the MCP servers: imports, config lookup, error text.

An MCP client launches these servers as bare subprocesses from an arbitrary
working directory, so the first job is putting the repo on sys.path; the second
is finding which data source to act on, which is re-read on every call so
changes made on the Sources tab take effect without restarting the client.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SDK_HINT = (
    "The MCP runtime is not installed. Install it into the same environment as this app:\n"
    "    pip install mcp\n"
    "or open the app's Sources tab and install the 'MCP runtime' component."
)


def sdk():
    """The MCPServer class, or a clear message on stderr and a non-zero exit.

    An MCP client shows a server's stderr when it fails to start, so this is the
    one place a missing dependency can actually be explained to someone.
    """
    try:
        from mcp.server import MCPServer
    except ImportError:
        print(SDK_HINT, file=sys.stderr)
        raise SystemExit(2)
    return MCPServer


def parse_args(description: str, env_var: str, writes: str = "") -> argparse.Namespace:
    """Standard server arguments. `writes` names what --allow-writes unlocks;
    servers that only read leave it out and the flag is not offered."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--source-id", type=int, default=_env_int(env_var),
        help=f"Data source id to use. Defaults to ${env_var}, then to the only "
             f"enabled source of this type.",
    )
    if writes:
        parser.add_argument(
            "--allow-writes", action="store_true",
            help=f"Also register the tools that {writes}. Off by default: the "
                 f"server is read-only until you ask for it.",
        )
    return parser.parse_args()


def _env_int(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _prepare_db() -> None:
    from app import db
    db.init()


def config_for(type_: str, source_id: int | None) -> dict:
    """The unmasked config of the data source these tools act on.

    Falls back to an empty config — meaning "use the shared MS_*/HUBSPOT_*
    settings, and demo data if those are blank too" — only when no source of this
    type exists at all. When several exist and none was named, the resulting
    error tells the caller to pass --source-id, which is more useful than a guess.
    """
    from app import datasources, db, settings

    settings.invalidate()   # pick up anything changed in the UI since the last call
    if source_id is None:
        existing = [r for r in db.list_datasources(enabled_only=True) if r["type"] == type_]
        if not existing:
            return {}
    _, config = datasources.resolve_config(type_, source_id)
    return config


def describe_error(exc: Exception) -> str:
    """Turn an HTTP failure into something a model can act on."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        detail = ""
        try:
            payload = exc.response.json()
            error = payload.get("error")
            if isinstance(error, dict):
                detail = error.get("message", "")
            detail = detail or payload.get("message") or ""
        except Exception:
            detail = exc.response.text[:400]
        hint = ""
        if status in (401, 403):
            hint = (" — check the app registration's application permissions have been "
                    "granted admin consent, and that the mailbox or record is in scope.")
        elif status == 404:
            hint = " — the mailbox, message or record id does not exist."
        elif status == 429:
            hint = " — rate limited by the API; retry in a moment."
        return f"{exc.request.url.host} returned HTTP {status}: {detail or 'no detail'}{hint}"
    if isinstance(exc, httpx.RequestError):
        return f"could not reach {exc.request.url.host}: {exc}"
    return str(exc)


def guard(fn):
    """Wrap a tool so failures arrive as readable text, not a stack trace."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            raise ValueError(describe_error(exc)) from exc

    return wrapper


def run(server) -> None:
    """Bring the local database up to date, then serve on stdio."""
    _prepare_db()
    server.run("stdio")
