#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || { printf 'error: %s not found\n' "$PYTHON" >&2; exit 1; }
command -v uv >/dev/null || { printf 'error: uv is required to install browser-harness\n' >&2; exit 1; }

# uv installs tools in ~/.local/bin on this platform; make that location
# available for both this script's verification and boot.sh.
export PATH="${HOME}/.local/bin:${PATH}"

if [[ ! -d .venv ]]; then
  "$PYTHON" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --upgrade openai
uv tool install --python 3.12 --upgrade --force browser-harness

mkdir -p sessions workspace toolbin
[[ -f skills/browser-harness.SKILL.md ]] || { printf 'error: bundled browser skill is missing\n' >&2; exit 1; }
chmod +x boot.sh install.sh update.sh

.venv/bin/python -c 'import openai; print("openai", openai.__version__)'
command -v browser-harness >/dev/null || { printf 'error: browser-harness was not installed\n' >&2; exit 1; }
printf 'Installation complete. Start with ./boot.sh\n'
