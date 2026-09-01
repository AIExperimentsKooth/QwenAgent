#!/bin/bash
# Boot script for Qwen3.8-27B Agentic Harness
# Activates the venv and starts harness.py

DIR="$(cd "$(dirname "$0")" && pwd)"

export PATH="${HOME}/.local/bin:${PATH}"

if [[ ! -x "$DIR/.venv/bin/python" ]]; then
  printf 'error: .venv is missing; run ./install.sh first\n' >&2
  exit 1
fi

printf '[boot] activating venv at %s/.venv\n' "$DIR"
# shellcheck disable=SC1091
source "$DIR/.venv/bin/activate"

echo "[boot] starting harness.py"
exec python3 "$DIR/harness.py" "$@"