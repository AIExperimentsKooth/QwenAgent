"""Configuration: ANSI colors, repo-relative paths, environment, LLM client.

All runtime settings are read from environment variables with sensible
defaults. Paths default to locations inside the repository (see
``repo_path``) — no absolute host paths.
"""

import json
import os
import urllib.request

from openai import OpenAI

# ── terminal styling ──────────────────────────────────────────────────
# Color codes never render properly under cmd.exe/PowerShell and only
# clutter captured logs — disable every ANSI sequence on Windows.
STYLING = os.name != "nt"

if STYLING:
    GREY = "\033[90m"
    GREEN = "\033[32m"
    GOLD = "\033[33m"
    RED = "\033[31m"
    RESET = "\033[0m"
else:
    GREY = GREEN = GOLD = RED = RESET = ""

# ── repo-relative paths ────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repo_path(env_name, default):
    """Return a path relative to the repository unless explicitly overridden."""
    value = os.environ.get(env_name) or default
    return value if os.path.isabs(value) else os.path.join(BASE_DIR, value)


BASE_URL = os.environ.get("QWEN_API_BASE", "http://100.64.2.163:1234/v1")
MODEL = os.environ.get("QWEN_MODEL", None)
API_KEY = os.environ.get("QWEN_API_KEY", "")
MAX_TOOL_ROUNDS = 30  # safety limit
SESSIONS_DIR = repo_path("QWEN_SESSIONS_DIR", "sessions")
CONTEXT_LENGTH = int(os.environ.get("QWEN_CONTEXT_LENGTH", "32768"))
COMPRESSION_MODEL = os.environ.get("QWEN_COMPRESSION_MODEL", None)
COMPRESSION_TRIGGER = float(os.environ.get("QWEN_COMPRESSION_TRIGGER", "0.75"))
MAX_TOKENS = int(os.environ.get("QWEN_MAX_TOKENS", "16384"))
SKILLS_DIR = repo_path("QWEN_SKILLS_DIR", "skills")
WORKSPACE_DIR = repo_path("QWEN_WORKSPACE_DIR", "workspace")

# ── LLM client ─────────────────────────────────────────────────────────
client = OpenAI(base_url=BASE_URL, api_key=API_KEY or "not-needed", timeout=None, max_retries=0)


FALLBACK_MODEL = "qwen3.8-27b-obliterated@iq4_xs"


def fetch_model_ids(timeout=30):
    """Return the ids of every model the backend reports as loaded (deduped).

    The order matches the backend's /v1/models response (first occurrence
    wins for duplicates/aliases)."""
    req = urllib.request.Request(BASE_URL + "/models")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    ids = []
    for m in data.get("data", []):
        mid = m.get("id") if isinstance(m, dict) else None
        if mid and mid not in ids:
            ids.append(mid)
    return ids


def detect_model():
    """Pick the model to chat with.

    Resolution order, never a silent guess with several models loaded:
      1. ``QWEN_MODEL`` env var, if set;
      2. exactly one loaded model -> auto-detect it;
      3. several loaded models -> list them and ask the user (Empty picks
         the first, shown clearly);
      4. none reachable / nothing loaded -> hardcoded fallback with a
         visible warning.
    """
    if MODEL:
        return MODEL
    try:
        loaded = fetch_model_ids()
    except Exception as e:
        print(f"  {RED}model detection failed ({e}); set QWEN_MODEL or check {BASE_URL}{RESET}")
        loaded = []
    if not loaded:
        print(f"  {RED}no models reported by {BASE_URL} — using hardcoded fallback model{RESET}")
        return FALLBACK_MODEL
    if len(loaded) == 1:
        print(f"  detected model: {loaded[0]}")
        return loaded[0]

    # Multiple models loaded — never route prompts at an arbitrary pick.
    print(f"  {GOLD}multiple models loaded ({len(loaded)}):{RESET}")
    for i, mid in enumerate(loaded):
        print(f"    [{i}] {mid}")
    while True:
        try:
            choice = input(f"  which model [0-{len(loaded) - 1}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"  no input — using: {loaded[0]}")
            return loaded[0]
        if choice == "":
            print(f"  using: {loaded[0]}")
            return loaded[0]
        try:
            idx = int(choice)
            if 0 <= idx < len(loaded):
                print(f"  using: {loaded[idx]}")
                return loaded[idx]
        except ValueError:
            pass
        print("  enter a number from the list above")