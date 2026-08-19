"""Wiring the MCP servers into a desktop assistant, without anyone editing JSON.

The servers in `mcp_servers/` are launched by the *client* — Claude Desktop,
Claude Code — which means the client needs the absolute path of this machine's
Python and of the server scripts. Those differ between a source checkout, a venv
and the Windows installer's private runtime, which is exactly the kind of detail
this app's users should never have to work out.

So the config is generated here from `sys.executable` and this file's own
location, and can be written straight into Claude Desktop's config file. That
file belongs to another application, so writing to it is done carefully: it is
never created where Claude Desktop is not installed, never overwritten when it
holds JSON we cannot parse, always backed up first, and always merged — other
MCP servers the user has set up are left exactly as they were.
"""
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

from .config import BASE_DIR

SERVERS_DIR = BASE_DIR / "mcp_servers"

# key -> what it is, and the script that serves it.
SERVERS = {
    "ms365": {
        "label": "Microsoft 365 — Outlook mail & calendar",
        "script": "ms365_server.py",
        "blurb": "Read and search the team's shared mailboxes, and look at calendars.",
        "supports_writes": True,
        "writes_blurb": "Also allow replying to and sending mail from those mailboxes.",
    },
    "hubspot": {
        "label": "HubSpot CRM",
        "script": "hubspot_server.py",
        "blurb": "Look up contacts, companies, deals and tickets. Read-only.",
        "supports_writes": False,
        "writes_blurb": "",
    },
}


def interpreter() -> str:
    """The Python that should launch the servers — the one running this app."""
    return sys.executable or shutil.which("python") or "python"


def script_path(key: str) -> Path:
    return SERVERS_DIR / SERVERS[key]["script"]


def servers_present() -> bool:
    return all(script_path(k).exists() for k in SERVERS)


def entries(allow_writes: bool = False) -> dict:
    """The "mcpServers" block for this machine."""
    out = {}
    for key, spec in SERVERS.items():
        args = [str(script_path(key))]
        if allow_writes and spec["supports_writes"]:
            args.append("--allow-writes")
        entry = {"command": interpreter(), "args": args}
        # The servers find the database through this, so a non-default data
        # directory has to travel with the config or they would read a different one.
        data_dir = os.environ.get("CSHUB_DATA_DIR")
        if data_dir:
            entry["env"] = {"CSHUB_DATA_DIR": data_dir}
        out[key] = entry
    return out


def document(allow_writes: bool = False) -> dict:
    return {"mcpServers": entries(allow_writes)}


def document_json(allow_writes: bool = False) -> str:
    return json.dumps(document(allow_writes), indent=2)


# ---------- Claude Desktop's config file ----------

def desktop_config_path() -> Path:
    """Where Claude Desktop keeps its config on this platform."""
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "Claude" / "claude_desktop_config.json"


def _backup_path(path: Path) -> Path:
    """A backup name that never overwrites an earlier one.

    Two clicks inside the same second would otherwise land on the same filename
    and quietly destroy the very copy being kept for safety.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}.backup-{stamp}-{counter}{path.suffix}")
        counter += 1
    return candidate


def _read_existing(path: Path) -> tuple[dict | None, str]:
    """(config, error). A None config with no error simply means "no file yet"."""
    if not path.exists():
        return None, ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"Could not read {path}: {exc}"
    if not text.strip():
        return {}, ""
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, (f"{path.name} is not valid JSON ({exc.msg}, line {exc.lineno}), so it will not be "
                      f"changed — fixing it by hand is safer than guessing what was meant.")
    if not isinstance(loaded, dict):
        return None, f"{path.name} does not contain a JSON object, so it will not be changed."
    return loaded, ""


def status() -> dict:
    """Everything the UI needs to explain the situation and offer the button."""
    path = desktop_config_path()
    existing, error = _read_existing(path)
    installed = existing.get("mcpServers", {}) if isinstance(existing, dict) else {}
    wanted = entries(False)
    wanted_writes = entries(True)

    connected = {}
    for key, spec in SERVERS.items():
        current = installed.get(key)
        connected[key] = {
            "present": bool(current),
            # Matching either shape counts as connected; the difference is only
            # whether sending mail was enabled.
            "current": current,
            "matches": current in (wanted[key], wanted_writes[key]),
            # Only meaningful where the two shapes actually differ — a read-only
            # server matches both, and must not be reported as write-enabled.
            "allow_writes": (bool(current) and spec["supports_writes"]
                             and current == wanted_writes[key]),
        }

    other = sorted(k for k in installed if k not in SERVERS)
    return {
        "servers": [{"key": k, **{f: v for f, v in SERVERS[k].items() if f != "script"},
                     "script": str(script_path(k)), **connected[k]}
                    for k in SERVERS],
        "servers_present": servers_present(),
        "interpreter": interpreter(),
        "config_path": str(path),
        "config_exists": path.exists(),
        "app_dir_exists": path.parent.exists(),
        "read_error": error,
        "other_servers": other,
        "all_connected": all(c["matches"] for c in connected.values()),
        "config_json": document_json(False),
        "config_json_writes": document_json(True),
        "platform": platform.system(),
    }


def connect(allow_writes: bool = False) -> dict:
    """Merge our two servers into Claude Desktop's config file.

    Raises ValueError with something the user can act on rather than writing
    anything doubtful.
    """
    if not servers_present():
        missing = [SERVERS[k]["script"] for k in SERVERS if not script_path(k).exists()]
        raise ValueError(f"The server scripts are missing from this install: {', '.join(missing)}")

    path = desktop_config_path()
    if not path.parent.exists():
        raise ValueError(
            f"Claude Desktop doesn't look installed on this machine — {path.parent} doesn't exist. "
            f"Install it first, or copy the configuration shown here into whichever assistant you use."
        )

    existing, error = _read_existing(path)
    if error:
        raise ValueError(error)
    config = existing if existing is not None else {}

    backup = ""
    if path.exists():
        backup = str(_backup_path(path))
        try:
            shutil.copy2(path, backup)
        except OSError as exc:
            raise ValueError(f"Could not back up the existing config: {exc}") from exc

    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    kept = sorted(k for k in servers if k not in SERVERS)
    servers.update(entries(allow_writes))
    config["mcpServers"] = servers

    try:
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Could not write {path}: {exc}") from exc

    return {
        "ok": True,
        "config_path": str(path),
        "backup": backup,
        "allow_writes": allow_writes,
        "kept": kept,
        "message": ("Connected. Quit Claude Desktop and open it again — it reads this file at startup."),
    }


def disconnect() -> dict:
    """Remove just our two entries again, leaving everything else alone."""
    path = desktop_config_path()
    existing, error = _read_existing(path)
    if error:
        raise ValueError(error)
    if existing is None:
        return {"ok": True, "removed": [], "config_path": str(path),
                "message": "Nothing to remove — there is no Claude Desktop config."}

    servers = existing.get("mcpServers")
    if not isinstance(servers, dict):
        return {"ok": True, "removed": [], "config_path": str(path),
                "message": "Nothing to remove."}

    removed = [k for k in SERVERS if k in servers]
    if not removed:
        return {"ok": True, "removed": [], "config_path": str(path),
                "message": "Nothing to remove."}

    backup = str(_backup_path(path))
    try:
        shutil.copy2(path, backup)
        for key in removed:
            servers.pop(key, None)
        existing["mcpServers"] = servers
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Could not update {path}: {exc}") from exc

    return {"ok": True, "removed": removed, "backup": backup, "config_path": str(path),
            "message": "Disconnected. Restart Claude Desktop for it to take effect."}


# ---------- command line ----------
#
# So an installer script — which has no browser and no session — can do the same
# thing the Sources tab's button does:
#
#     python -m app.mcpsetup connect --allow-writes
#     python -m app.mcpsetup status

def _cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m app.mcpsetup",
        description="Show or change how a desktop assistant reaches this app's MCP servers.")
    parser.add_argument("action", nargs="?", default="status",
                        choices=["status", "print", "connect", "disconnect"],
                        help="status: what is wired up now. print: the config JSON. "
                             "connect/disconnect: change Claude Desktop's config file.")
    parser.add_argument("--allow-writes", action="store_true",
                        help="Let the assistant reply to and send mail as well as read it.")
    args = parser.parse_args(argv)

    if args.action == "print":
        print(document_json(args.allow_writes))
        return 0

    if args.action == "status":
        state = status()
        print(f"Python      : {state['interpreter']}")
        print(f"Config file : {state['config_path']}"
              f"{'' if state['config_exists'] else '  (does not exist yet)'}")
        if state["read_error"]:
            print(f"Problem     : {state['read_error']}")
        for server in state["servers"]:
            mark = "connected" if server["matches"] else (
                "needs updating" if server["present"] else "not connected")
            extra = " (may send mail)" if server["allow_writes"] else ""
            print(f"  {server['key']:<8} {mark}{extra}")
        if state["other_servers"]:
            print(f"Other servers configured there: {', '.join(state['other_servers'])}")
        return 0

    try:
        result = connect(args.allow_writes) if args.action == "connect" else disconnect()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(result["message"])
    if result.get("backup"):
        print(f"Previous config saved as {result['backup']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
