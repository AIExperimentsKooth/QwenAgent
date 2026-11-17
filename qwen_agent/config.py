"""Configuration: ANSI colors, repo-relative paths, environment, LLM client.

All runtime settings are read from environment variables with sensible
defaults. Paths default to locations inside the repository (see
``repo_path``) — no absolute host paths.

The LLM client speaks the OpenAI-compatible ``/chat/completions`` protocol
over plain ``urllib`` (server-sent events for streaming), so the harness has
no third-party Python dependencies at all.
"""

import json
import os
import shutil
import urllib.error
import urllib.request

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


BASE_URL = os.environ.get("QWEN_API_BASE", "http://localhost:1234/v1")
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

# ── optional browser automation ────────────────────────────────────────
# The browser tool shells out to the browser-harness CLI (CDP over Chrome).
# It is entirely optional: when the CLI is not installed the tool and its
# bundled skill are hidden from the agent, and nothing else changes.
# Point QWEN_BROWSER_CMD at a non-PATH install to enable it explicitly.
BROWSER_CMD = os.environ.get("QWEN_BROWSER_CMD") or shutil.which("browser-harness")
BROWSER_ENABLED = bool(BROWSER_CMD)

# ── LLM client ─────────────────────────────────────────────────────────
def _endpoint(path):
    return BASE_URL.rstrip("/") + path


def _request(path, payload, accept="application/json"):
    """POST a JSON payload and return the open response object.

    ``timeout=None`` blocks as long as the model needs to answer (local
    GGUF backends can take minutes), matching the old SDK client.
    """
    headers = {"Content-Type": "application/json", "Accept": accept}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    req = urllib.request.Request(
        _endpoint(path),
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        return urllib.request.urlopen(req, timeout=None)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace").strip()
        except Exception:
            pass
        raise RuntimeError(
            f"HTTP {e.code} from {_endpoint(path)}: {detail[:500] or e.reason}"
        ) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach {_endpoint(path)}: {e.reason}") from None


def _payload(messages, model, temperature, max_tokens, stream):
    payload = {
        "model": model or MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    return payload


def _chat_error(data):
    """Build a readable error for a response whose shape we do not recognise."""
    url = _endpoint("/chat/completions")
    return RuntimeError(f"unexpected response from {url}: {json.dumps(data)[:500]}")


def chat(messages, model=None, temperature=0.1, max_tokens=None):
    """Non-streaming completion. Returns the assistant message content."""
    payload = _payload(messages, model, temperature, max_tokens, stream=False)
    with _request("/chat/completions", payload) as resp:
        body = resp.read().decode("utf-8", "replace")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError(f"endpoint returned non-JSON: {body[:500]}") from None
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise _chat_error(data) from None


def stream_chat(messages, model=None, temperature=0.1, max_tokens=None):
    """Yield assistant content deltas from a streaming completion.

    Parses server-sent events directly, so no SDK is involved. Closing the
    returned generator (``gen.close()``) releases the socket immediately,
    which is what the main loop relies on when a stream is cancelled.
    """
    payload = _payload(messages, model, temperature, max_tokens, stream=True)
    resp = _request("/chat/completions", payload, accept="text/event-stream")
    try:
        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue  # blank separator or keep-alive comment
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue  # tolerate partial/garbled keep-alive frames
            for choice in chunk.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    yield piece
    finally:
        resp.close()


def detect_model():
    """Query /v1/models and use whatever is loaded. Falls back to QWEN_MODEL env var."""
    if MODEL:
        return MODEL
    try:
        req = urllib.request.Request(_endpoint("/models"))
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        models = data.get("data", [])
        if models:
            detected = models[0]["id"]
            print(f"  detected model: {detected}")
            return detected
    except Exception as e:
        print(f"  model detection failed ({e})")
        print(f"  is a server running at {BASE_URL}? set QWEN_API_BASE to point elsewhere")
    return os.environ.get("QWEN_MODEL") or "local-model"
