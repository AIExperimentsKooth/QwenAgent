#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT_DIR"

GH="${GH_BIN:-gh}"
command -v "$GH" >/dev/null || { printf 'error: gh is required\n' >&2; exit 1; }
"$GH" auth status >/dev/null

# Use gh's authenticated git credential helper, then fast-forward safely.
"$GH" auth setup-git
BRANCH="$(git branch --show-current)"
[[ -n "$BRANCH" ]] || { printf 'error: detached HEAD; check out a branch first\n' >&2; exit 1; }
git pull --ff-only origin "$BRANCH"

./install.sh
printf 'Update complete.\n'

# Relative project paths are intentional: .venv remains $ROOT_DIR/.venv.
: