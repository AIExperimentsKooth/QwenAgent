#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT_DIR"

command -v git >/dev/null || { printf 'error: git is required\n' >&2; exit 1; }

BRANCH="$(git branch --show-current)"
[[ -n "$BRANCH" ]] || { printf 'error: detached HEAD; check out a branch first\n' >&2; exit 1; }
git pull --ff-only origin "$BRANCH"

./install.sh
printf 'Update complete.\n'
