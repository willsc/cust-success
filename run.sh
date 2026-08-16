#!/usr/bin/env bash
# Customer Success Hub — macOS / Linux launcher.
# First run creates .venv and installs the (small) base requirements; later runs
# start straight away, reinstalling only when requirements.txt has changed.
# Optional components — spreadsheets, SQL drivers, decks — are installed from
# the app's Sources tab, not here.
set -euo pipefail
cd "$(dirname "$0")"

VENV_PY=".venv/bin/python"
STAMP=".venv/.requirements-sha"

find_python() {
  for candidate in "${PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3 python; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      echo "$candidate"; return 0
    fi
  done
  return 1
}

if [ ! -x "$VENV_PY" ]; then
  PY="$(find_python)" || {
    echo "ERROR: Python 3.10 or newer was not found." >&2
    echo "Install it (macOS: brew install python3 · Debian/Ubuntu: sudo apt install python3 python3-venv)," >&2
    echo "then run ./run.sh again. To use a specific interpreter: PYTHON=/path/to/python ./run.sh" >&2
    exit 1
  }
  echo "Creating virtual environment with $PY..."
  "$PY" -m venv .venv || {
    echo "ERROR: could not create .venv. On Debian/Ubuntu install python3-venv first." >&2
    exit 1
  }
fi

# Reinstall when requirements.txt changes — otherwise skip pip entirely.
want="$("$VENV_PY" -c 'import hashlib;print(hashlib.sha256(open("requirements.txt","rb").read()).hexdigest())')"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$want" ]; then
  echo "Installing dependencies..."
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install --quiet -r requirements.txt
  echo "$want" > "$STAMP"
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8300}"
echo
echo "Customer Success Hub → http://localhost:$PORT"
echo "Press Ctrl+C to stop."
echo
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
