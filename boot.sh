#!/bin/bash
# Boot script for Qwen3.8-27B Agentic Harness
# Activates the venv and starts harness.py

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[boot] activating venv at $DIR/.venv"
source "$DIR/.venv/bin/activate"

echo "[boot] starting harness.py"
exec python3 "$DIR/harness.py" "$@"