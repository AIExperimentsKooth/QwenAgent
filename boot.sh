#!/bin/bash
# Boot script for Qwen3.8-27B Agentic Harness
# Runs harness.py with the project venv when one exists, otherwise with the
# system Python (Termux, Linux, macOS). No third-party packages are required.

DIR="$(cd "$(dirname "$0")" && pwd)"

export PATH="${HOME}/.local/bin:${PATH}"

if [[ -x "$DIR/.venv/bin/python" ]]; then
  printf '[boot] using venv at %s/.venv\n' "$DIR"
  PY="$DIR/.venv/bin/python"
else
  # Prefer python3, but fall back to python: on Windows "python3" can be an
  # App Execution Alias stub that exists on PATH yet cannot run.
  if [[ -n "${PYTHON:-}" ]]; then
    PY="$PYTHON"
  else
    PY=""
    for candidate in python3 python; do
      if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c '' >/dev/null 2>&1; then
        PY="$candidate"
        break
      fi
    done
  fi
  if [[ -z "$PY" ]] || ! "$PY" -c '' >/dev/null 2>&1; then
    printf 'error: Python 3.10+ not found (Termux: pkg install python)\n' >&2
    exit 1
  fi
fi

echo "[boot] starting harness.py"
exec "$PY" "$DIR/harness.py" "$@"
