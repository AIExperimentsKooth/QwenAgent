#!/usr/bin/env bash
# QwenAgent installer — Linux, macOS and Termux.
#
# The harness uses only the Python standard library, so there is nothing to
# install: this script checks the Python version, creates the runtime
# directories and verifies the package imports cleanly.
#
# The optional browser tool needs the browser-harness CLI; it is picked up
# automatically when already on PATH (nothing is installed for it here).
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT_DIR"

# Prefer python3, but fall back to python: on Windows "python3" can be an
# App Execution Alias stub that exists on PATH yet cannot run.
if [[ -n "${PYTHON:-}" ]]; then
  CANDIDATES=("$PYTHON")
else
  CANDIDATES=(python3 python)
fi

PYTHON=""
for candidate in "${CANDIDATES[@]}"; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c '' >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  printf 'error: Python 3.10+ not found (Termux: pkg install python)\n' >&2
  exit 1
fi

"$PYTHON" - <<'PY'
import sys

if sys.version_info < (3, 10):
    sys.exit(f"error: Python 3.10+ required, found {sys.version.split()[0]}")
PY

# Self-check: the package must import using the standard library only.
"$PYTHON" -c 'import qwen_agent.main' || {
  printf 'error: qwen_agent failed to import\n' >&2
  exit 1
}

# Match boot.sh, which makes the usual user tool directory available so a
# browser-harness installed there is detected.
export PATH="${HOME}/.local/bin:${PATH}"

mkdir -p sessions workspace
chmod +x boot.sh install.sh update.sh 2>/dev/null || true

printf 'Python %s — no third-party packages required.\n' \
  "$("$PYTHON" -c 'import platform; print(platform.python_version())')"
if command -v browser-harness >/dev/null 2>&1; then
  printf 'browser tool: enabled (browser-harness found)\n'
else
  printf 'browser tool: disabled (optional; install browser-harness to enable)\n'
fi
printf 'Installation complete. Start with ./boot.sh\n'
