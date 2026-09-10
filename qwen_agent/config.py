"""Configuration: ANSI colors, repo-relative paths, environment, LLM client.

All runtime settings are read from environment variables with sensible
defaults. Paths default to locations inside the repository (see
``repo_path``) — no absolute host paths.
"""

import json
import os
import urllib.request

from openai import OpenAI

# ── ANSI colors ────────────────────────────────────────────────────────
def _enable_windows_ansi():
    """Enable ANSI escape processing in the Windows console (stdout + stderr).

    Classic cmd.exe renders ``\033[..m`` color codes as garbage unless VT
    processing is turned on. Windows Terminal and conemu already enable it,
    so this is a no-op for them — and a silent no-op whenever the handles
    are not consoles (e.g. output piped to a file).
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        for std_handle in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            h = kernel32.GetStdHandle(std_handle)
            if not h:
                continue
            mode = kernel32.GetConsoleMode(h)
            if not mode:
                continue  # not a console (redirected) or error
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(h, mode | 0x0004)
    except Exception:
        pass  # never break startup over console cosmetics


_enable_windows_ansi()

GREY = "\033[90m"
GREEN = "\033[32m"
GOLD = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"

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


def detect_model():
    """Query /v1/models and use whatever is loaded. Falls back to QWEN_MODEL env var."""
    if MODEL:
        return MODEL
    try:
        req = urllib.request.Request(BASE_URL + "/models")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        models = data.get("data", [])
        if models:
            detected = models[0]["id"]
            print(f"  detected model: {detected}")
            return detected
    except Exception as e:
        print(f"  model detection failed ({e}), fallback to env or hardcoded")
    return "qwen3.8-27b-obliterated@iq4_xs"