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

MCP_PACKAGE = "mcp"

SDK_HINT = (
    f"The MCP runtime is missing and could not be installed automatically.\n"
    f"Install it into this app's environment by hand:\n"
    f"    {sys.executable} -m pip install {MCP_PACKAGE}"
)


def _note(message: str) -> None:
    """Say something to the operator.

    Always stderr: stdout is the JSON-RPC channel, and one stray byte on it
    breaks the protocol. An MCP client surfaces a server's stderr, so this is
    where a non-technical user actually sees what is happening.
    """
    print(message, file=sys.stderr, flush=True)


def _autoinstall() -> bool:
    """Install the MCP runtime into this interpreter. True if it is now importable.

    `mcp` is a base requirement, so the launchers and the Windows installer put
    it in place long before anything gets here. This is the last resort for an
    environment they never touched — someone who pointed a client straight at a
    fresh checkout — because "run pip install" is not an instruction this app's
    users can be expected to act on.
    """
    import importlib
    import subprocess

    from app import deps

    if not deps.install_enabled():
        _note("The MCP runtime is missing, and automatic installs are switched off "
              "here (DISABLE_UI_INSTALL).")
        return False
    if not deps.isolated_runtime():
        # Never pip into a system or shared interpreter uninvited.
        _note("The MCP runtime is missing, and this is a shared Python installation, "
              "so it will not be installed automatically.")
        return False

    _note(f"Installing the MCP runtime ({MCP_PACKAGE}) into {sys.executable} — "
          f"this happens once and needs internet access.")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
             "--no-input", MCP_PACKAGE],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _note(f"The install could not be started: {exc}")
        return False

    if proc.returncode != 0:
        _note(proc.stdout.strip()[-2000:])
        _note(f"pip exited with code {proc.returncode}.")
        return False

    importlib.invalidate_caches()
    _note("MCP runtime installed. Starting up.")
    return True


def sdk():
    """The MCPServer class, installing the runtime first if it is missing.

    A client that gave up waiting during the install will connect fine on its
    next attempt, since by then the package is there.
    """
    try:
        from mcp.server import MCPServer
        return MCPServer
    except ImportError:
        pass

    if _autoinstall():
        try:
            from mcp.server import MCPServer
            return MCPServer
        except ImportError:
            pass

    _note(SDK_HINT)
    raise SystemExit(2)


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
