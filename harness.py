#!/home/aiker/qwenAgent/.venv/bin/python3
"""Minimal tool-calling loop over an OpenAI-compatible endpoint.

Auto-detects the loaded model from /v1/models at startup.
Tools: read_file, write_file, patch (find/replace), shell.
System prompt instructs the agent to prefer patch over write_file."""

import json
import os
import queue
import select
import signal
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime
from openai import OpenAI

# ── ANSI colors ────────────────────────────────────────────────────────
GREY = "\033[90m"
GREEN = "\033[32m"
GOLD = "\033[33m"
RESET = "\033[0m"

# ── config ─────────────────────────────────────────────────────────────
BASE_URL = os.environ.get("QWEN_API_BASE", "http://100.64.2.163:1234/v1")
MODEL = os.environ.get("QWEN_MODEL", None)
API_KEY = os.environ.get("QWEN_API_KEY", "")
MAX_TOOL_ROUNDS = 30  # safety limit
SESSIONS_DIR = os.path.expanduser("~/qwenAgent/sessions")
CONTEXT_LENGTH = int(os.environ.get("QWEN_CONTEXT_LENGTH", "32768"))
COMPRESSION_MODEL = os.environ.get("QWEN_COMPRESSION_MODEL", None)
COMPRESSION_TRIGGER = float(os.environ.get("QWEN_COMPRESSION_TRIGGER", "0.75"))
SKILLS_DIR = os.path.expanduser(os.environ.get("QWEN_SKILLS_DIR", "~/qwenAgent/skills"))

client = OpenAI(base_url=BASE_URL, api_key=API_KEY or "not-needed", timeout=None, max_retries=0)


# ── stream control (threading + signal) ─────────────────────────────────

_stream_stop = threading.Event()
_stream_active = threading.Event()
_sigint_count = 0


def handle_sigint(sig, frame):
    global _sigint_count
    _sigint_count += 1
    if _sigint_count >= 2:
        # Double CTRL-C: hard exit
        sys.stderr.write("\n  (force exit)\n")
        os._exit(1)
    if _stream_active.is_set():
        _stream_stop.set()
        sys.stderr.write(f"\n{GREY}  (cancelling stream...){RESET}\n")
    else:
        sys.stderr.write("\n  use /q to save and quit\n")


signal.signal(signal.SIGINT, handle_sigint)


def save_and_exit(messages, name):
    """Save session and exit cleanly."""
    if len(messages) > 1:
        n = name or datetime.now().strftime("%Y%m%d_%H%M%S")
        save_session(n, messages)
        print(f"  saved as '{n}', bye.")
    os._exit(0)


# ── session persistence ─────────────────────────────────────────────────

SESSION_IGNORE_ROLES = {"system", "assistant"}
MAX_SESSION_DISPLAY = 1024


def session_path(name):
    return os.path.join(SESSIONS_DIR, f"{name}.json")


def list_sessions():
    """Return sorted list of (name, timestamp, preview)."""
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    sessions = []
    for fname in sorted(os.listdir(SESSIONS_DIR), reverse=True):
        if not fname.endswith(".json"):
            continue
        name = fname[:-5]
        path = os.path.join(SESSIONS_DIR, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            ts = data.get("timestamp", "?")
            msgs = data.get("messages", [])
            # Find the first user message after the system prompt
            preview = ""
            for m in msgs:
                if m.get("role") == "user" and m.get("content"):
                    preview = m["content"][:MAX_SESSION_DISPLAY]
                    break
            sessions.append((name, ts, preview))
        except Exception:
            sessions.append((name, "?", "(corrupt)"))
    return sessions


def save_session(name, messages):
    """Save current messages + metadata to sessions/{name}.json."""
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    # Strip tool results and parsing-noise — save only user/assistant turns for resume
    clean = [
        m for m in messages
        if m.get("role") in ("system", "user", "assistant")
    ]
    # Also strip the "If the task is complete" follow-ups the loop injects
    stripped = []
    for m in clean:
        if m["role"] == "user" and m["content"].startswith("Tool result:"):
            continue
        if m["role"] == "user" and m["content"].startswith("Your response was not parseable"):
            continue
        if m["role"] == "user" and m["content"].startswith("Tool '"):
            continue
        if m["role"] == "user" and m["content"].startswith("If the task is complete"):
            continue
        if m["role"] == "system" and "tool rounds" in m.get("content", ""):
            continue
        stripped.append(m)

    data = {
        "version": 1,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": MODEL,
        "endpoint": BASE_URL,
        "messages": stripped,
    }
    with open(session_path(name), "w") as f:
        json.dump(data, f, indent=2)
    return name


def load_session(name):
    """Load a saved session. Returns messages list or None."""
    path = session_path(name)
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        data = json.load(f)
    return data.get("messages", [])


def delete_session(name):
    path = session_path(name)
    if os.path.isfile(path):
        os.remove(path)


def pick_session():
    """Show saved sessions and let the user pick one to resume. Returns messages or None."""
    sessions = list_sessions()
    if not sessions:
        return None

    print(f"\n  Saved sessions ({len(sessions)}):")
    print(f"  {'':─>60}")
    for i, (name, ts, preview) in enumerate(sessions):
        short = preview[:128].replace("\n", " ") if preview else "(empty)"
        print(f"  [{i}] {name}  ({ts})")
        print(f"      {GOLD}{short}{RESET}")
    print(f"  [n] Start fresh")
    print()

    while True:
        try:
            choice = input("  Resume session [0]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if choice.lower() in ("", "n"):
            return None
        try:
            idx = int(choice)
            if 0 <= idx < len(sessions):
                msgs = load_session(sessions[idx][0])
                if msgs:
                    print(f"  resumed session: {sessions[idx][0]}")
                    return msgs
                print("  (session file corrupt, skipping)")
        except (ValueError, IndexError):
            print("  enter a number or 'n'")


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


# ── skills system ───────────────────────────────────────────────────────

SKILLS_CACHE = []   # list of (filename, content)


def load_skills():
    """Read all .md files from SKILLS_DIR and return (list, joined_text)."""
    global SKILLS_CACHE
    SKILLS_CACHE = []
    os.makedirs(SKILLS_DIR, exist_ok=True)
    names = sorted(os.listdir(SKILLS_DIR))
    blocks = []
    for fname in names:
        if not fname.endswith(".md"):
            continue
        path = os.path.join(SKILLS_DIR, fname)
        try:
            with open(path) as f:
                body = f.read().strip()
            SKILLS_CACHE.append((fname, body))
            blocks.append(f"<skill name=\"{fname[:-3]}\">\n{body}\n</skill>")
        except Exception as e:
            print(f"  {GREY}skill '{fname}' failed to load: {e}{RESET}")
    return SKILLS_CACHE, "\n\n".join(blocks)


# ── system prompt ──────────────────────────────────────────────────────

# ── compression ─────────────────────────────────────────────────────────

def estimate_tokens(messages):
    """Rough token estimate: ~4 chars per token for text."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4 + 100
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += len(part["text"]) // 4
            total += 100
    return total


def should_compress(messages):
    """Return True if estimated token usage exceeds the trigger threshold."""
    if COMPRESSION_TRIGGER >= 1.0:
        return False
    used = estimate_tokens(messages)
    limit = int(CONTEXT_LENGTH * COMPRESSION_TRIGGER)
    return used > limit


def pick_compression_model():
    """Return the model to use for compression (user-configured or fallback)."""
    return COMPRESSION_MODEL or MODEL


def consolidate_system_msgs(messages):
    """Merge all system messages into one at the start.

    Many GGUF/llama.cpp backends reject multiple system messages or
    system messages appearing after user/assistant turns. This ensures
    exactly one system message at position 0.
    """
    sys_parts = []
    non_sys = []
    for m in messages:
        if m["role"] == "system":
            sys_parts.append(m["content"])
        else:
            non_sys.append(m)
    if len(sys_parts) <= 1:
        return messages
    merged = "\n\n".join(sys_parts)
    return [{"role": "system", "content": merged}] + non_sys


def compress_history(messages):
    """Compress the oldest conversation history into a summary.

    Merges the summary into the main system prompt rather than adding
    a separate system message (many backends enforce one system msg).
    """
    msgs = consolidate_system_msgs(messages)
    if len(msgs) < 4:
        return msgs

    c_model = pick_compression_model()
    print(f"{GREY}  compressing history ({c_model})...{RESET}", flush=True)

    system_msg = msgs[0] if msgs[0]["role"] == "system" else None
    non_system = [m for m in msgs if m["role"] != "system"]

    if len(non_system) < 2:
        return msgs

    keep_count = min(4, len(non_system))
    to_compress = non_system[:-keep_count]
    to_keep = non_system[-keep_count:]

    compressed_text = ""
    for m in to_compress:
        compressed_text += f"[{m['role']}]\n{m.get('content', '')}\n\n"

    # Use a single user message — no system message — to avoid template
    # issues with the compression model.
    summary_prompt = [
        {"role": "user", "content": f"Summarize this conversation history concisely. Keep all key facts, decisions, and context needed to continue the task. Output only the summary, no preamble.\n\n{compressed_text}"},
    ]

    try:
        resp = client.chat.completions.create(
            model=c_model,
            messages=summary_prompt,
            temperature=0.1,
            max_tokens=1024,
        )
        summary = resp.choices[0].message.content.strip()
        print(f"{GREY}  compressed {len(to_compress)} messages -> 1 summary{RESET}")
    except Exception as e:
        print(f"{GREY}  compression failed ({e}), keeping history{RESET}")
        return msgs

    # Merge summary into the existing system prompt
    base = system_msg["content"] if system_msg else ""
    new_sys = f"{base}\n\n[Compressed history]\n{summary}" if base else f"[Compressed history]\n{summary}"

    result = [{"role": "system", "content": new_sys}]
    result.extend(to_keep)
    return result


# ── system prompt ──────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an autonomous agent with access to seven tools. You think, then act, then observe. You continue until the user's task is fully done.

## Tools

### 1. read_file(path, [offset=1], [limit=2000])
Read a text file. Lines are 1-indexed. Returns content and total line count.

### 2. write_file(path, content)
**Write entire content to a file, overwriting anything that was there.** Only use this when you are *creating* a new file or when patch cannot express the change (e.g. wholesale replacement). For any edit to an existing file, **always prefer patch**.

### 3. patch(path, old_string, new_string)
Find `old_string` in the file and replace it with `new_string`. **This is the preferred way to edit existing files.** Use patch over write_file whenever you are making a targeted change, not a full rewrite.

### 4. shell(command)
Run any shell command. The working directory persists between calls. Use this for creating directories, listing files, running scripts, git operations, builds, etc.

### 5. browser(code)
**Browser automation via browser-harness.** Accepts multi-line Python code that runs inside the browser-harness environment. Pre-imported helpers:

  new_tab(url)      — open URL in a new tab (use this for FIRST navigation)
  goto_url(url)     — navigate current tab
  page_info()       — get page URL, title, tab count
  js(expr)          — evaluate JS expression and return result
  fill_input(selector, text)
  click_at_xy(x, y) — click at viewport coordinates
  wait_for_load()   — wait after navigation
  capture_screenshot() — save a screenshot
  cdp("Domain.method", **kwargs) — raw Chrome DevTools Protocol
  ensure_real_tab() — recover from a stale/internal tab
  switch_tab(index) — switch to tab by 0-based index

To find elements: use cdp("Accessibility.getFullAXTree")["nodes"] for the full accessibility tree, then filter in Python. Get click coordinates with cdp("DOM.getBoxModel", backendNodeId=n).

Example — navigate and read:
browser('''
new_tab("https://example.com")
wait_for_load()
print(page_info())
''')

Always wait_for_load() after navigation. After clicking, verify with a targeted js() or page_info() call. If a page is bot-protected or a direct fetch fails, escalate to the browser tool.

### 6. ask_user(question, [choices])
**Ask the user for input.** Use this when you need a decision, clarification, or information that only the user can provide.
- `question` (required): what to ask
- `choices` (optional): a list of 2-4 predefined options. When provided, the user can pick one or type a free-text answer.
- If you omit `choices`, the user types a free-form response.
- The result will be a JSON object with the user's `"response"` and, if a choice was picked, the `"choice_index"`.

When in doubt about a decision, ask the user rather than guessing.

### 7. write_skill(name, content)
**Write a reusable skill to the skills directory and auto-load it into the system prompt.**
- `name` — skill name (without .md). The file is saved as `skills/<name>.md`.
- `content` — full markdown content. Include YAML frontmatter with `name:` and `description:` fields.
- Skills persist across sessions and are injected into the system prompt on every startup.
- Use this to save workflows, helper patterns, or site-specific instructions you discover.
- The skill is immediately available to all subsequent rounds after writing.

## Protocol

Your response must use **valid JSON** in one of these two shapes:

### Tool-call shape
```json
{
  "thought": "I need to read the config file first.",
  "tool": "read_file",
  "params": {"path": "/path/to/file"}
}
```

### Final-answer shape
```json
{
  "thought": "The task is done.",
  "answer": "All done. The config was updated."
}
```

Output ONLY clean JSON — do NOT wrap it in XML tags, markdown fences, or any other formatting. No `<tool_call>`, no `<function>`, no backticks. Just the JSON object on its own.

Rules:
- One tool call at a time. Wait for the result before the next.
- Always explain *why* you are calling the tool (in "thought").
- When the task is complete, return an "answer" — never a tool call.
- Prefer patch over write_file for editing existing files.
- If a tool errors, analyse the message and try a different approach.
- Do not guess file paths — list the directory with `shell("ls")` or `shell("find ...")` first.
- Verify your work after writing (e.g. `shell("python script.py")` or re-read the file)."""


# ── tool implementations ────────────────────────────────────────────────

def tool_read_file(path, offset=1, limit=2000):
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return json.dumps({"error": f"File not found: {path}"})
    with open(path, "r") as f:
        lines = f.readlines()
    total = len(lines)
    start = max(0, offset - 1)
    end = min(total, start + limit)
    content = "".join(lines[start:end])
    return json.dumps({"path": path, "total_lines": total, "offset": offset, "content": content})


def tool_write_file(path, content):
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    size = os.path.getsize(path)
    return json.dumps({"path": path, "bytes_written": size, "status": "ok"})


def tool_patch(path, old_string, new_string):
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return json.dumps({"error": f"File not found: {path}"})
    with open(path, "r") as f:
        text = f.read()
    count = text.count(old_string)
    if count == 0:
        return json.dumps({"error": f"old_string not found in {path}", "searched_for": old_string[:80]})
    text = text.replace(old_string, new_string, 1)
    with open(path, "w") as f:
        f.write(text)
    return json.dumps({"path": path, "replacements": 1, "status": "ok"})


def tool_shell(command):
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
        )
        out = result.stdout + result.stderr
        if len(out) > 8000:
            out = out[:8000] + f"\n\n... (truncated, full length {len(out)} chars)"
        return json.dumps({
            "exit_code": result.returncode,
            "output": out,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── browser-harness ─────────────────────────────────────────────────────

BH_WORKSPACE = os.path.expanduser("~/qwenAgent/toolbin")


def tool_browser(code):
    """Run Python code through browser-harness CLI (CDP browser automation).

    Accepts multi-line Python code using the pre-imported browser helpers:
      new_tab(url)   — open URL in new tab
      goto_url(url)  — navigate current tab
      page_info()    — get page state summary
      js(expr)       — evaluate JS expression
      fill_input(selector, text)
      click_at_xy(x, y)
      wait_for_load()
      cdp("Domain.method", **kwargs)
      capture_screenshot()
      ensure_real_tab()
      ... and others

    First call in a session typically starts with new_tab(url).
    Returns stdout from browser-harness.
    """
    os.makedirs(BH_WORKSPACE, exist_ok=True)
    env = os.environ.copy()
    env["BH_AGENT_WORKSPACE"] = BH_WORKSPACE
    try:
        result = subprocess.run(
            ["browser-harness"],
            input=code,
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
        )
        out = result.stdout + result.stderr
        if len(out) > 12000:
            out = out[:12000] + f"\n\n... (truncated, full length {len(out)} chars)"
        return json.dumps({
            "exit_code": result.returncode,
            "output": out,
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "browser-harness command timed out (300s)"})
    except FileNotFoundError:
        return json.dumps({"error": "browser-harness not installed. Run: uv tool install browser-harness"})
    except Exception as e:
        return json.dumps({"error": str(e)})


TOOL_MAP = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "patch": tool_patch,
    "shell": tool_shell,
    "browser": tool_browser,
    "ask_user": None,  # handled specially in main loop — not dispatched through here
    "write_skill": None,  # handled specially — reloads system prompt
}

# ── ask_user and write_skill (interactive — called from main thread) ───

TOOL_ARGS = {
    "ask_user": {
        "question": {"type": "string", "description": "Question to ask the user"},
        "choices": {"type": "list", "description": "Optional list of options (2-4). Omit for free-text input."},
    },
    "write_skill": {
        "name": {"type": "string", "description": "Skill name (without .md suffix)"},
        "content": {"type": "string", "description": "Full markdown content of the skill file"},
    },
}


def tool_ask_user_impl(question, choices=None):
    """Display a question to the user and return their response.

    Called synchronously from the main loop — not threaded.
    """
    if choices:
        print(f"\n{GOLD}  ? {question}{RESET}")
        for i, c in enumerate(choices):
            print(f"    [{i}] {c}")
        print(f"    [t] type your own answer")
        while True:
            try:
                sel = input(f"{GOLD}  > {RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                return json.dumps({"response": None, "cancelled": True})
            if sel.lower() == "t":
                try:
                    free = input(f"{GOLD}  type your answer: {RESET}").strip()
                except (EOFError, KeyboardInterrupt):
                    return json.dumps({"response": None, "cancelled": True})
                return json.dumps({"response": free, "choice": sel})
            try:
                idx = int(sel)
                if 0 <= idx < len(choices):
                    return json.dumps({"response": choices[idx], "choice_index": idx})
            except ValueError:
                pass
            print(f"  enter 0-{len(choices)-1}, 't' for custom, or Ctrl-C to cancel")
    else:
        try:
            ans = input(f"\n{GOLD}  ? {question}{RESET}\n{GOLD}  > {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            return json.dumps({"response": None, "cancelled": True})
        return json.dumps({"response": ans})


def reload_skills_into_system(msgs):
    """Rebuild messages[0] system content with fresh skill text."""
    skills_list, skills_text = load_skills()
    sys_text = SYSTEM_PROMPT
    if skills_text:
        sys_text += "\n\n" + skills_text
    msgs[0]["content"] = sys_text
    return len(skills_list)


def tool_write_skill_impl(name, content, msgs):
    """Write a skill file and auto-reload. Called from main thread."""
    if not name.endswith(".md"):
        fname = name + ".md"
    else:
        fname = name
    path = os.path.join(SKILLS_DIR, fname)
    os.makedirs(SKILLS_DIR, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    count = reload_skills_into_system(msgs)
    return json.dumps({
        "path": path,
        "bytes": len(content),
        "skills_loaded": count,
        "status": "ok",
    })


# ── LLM call ───────────────────────────────────────────────────────────

def llm_chat(messages, label=""):
    """Stream model output to terminal. Returns (text, cancelled)."""
    import shutil
    cols = shutil.get_terminal_size().columns

    header = f"  {label}" if label else ""
    if not _stream_stop.is_set():
        print(header)

    _stream_stop.clear()
    _stream_active.set()

    try:
        # Consolidate system messages — many GGUF backends reject
        # multiple system messages or mid-turn system messages.
        safe_msgs = consolidate_system_msgs(messages)
        stream = client.chat.completions.create(
            model=MODEL,
            messages=safe_msgs,
            temperature=0.1,
            max_tokens=2048,
            stream=True,
        )
    except Exception as e:
        _stream_active.clear()
        return f"  error: {e}", False

    parts = []
    try:
        for chunk in stream:
            if _stream_stop.is_set():
                try:
                    stream.close()
                except Exception:
                    pass
                break
            token = chunk.choices[0].delta.content or ""
            parts.append(token)
            sys.stdout.write(f"{GREY}{token}{RESET}")
            sys.stdout.flush()
    except Exception as e:
        _stream_active.clear()
        return f"  error: {e}", False

    _stream_active.clear()
    cancelled = _stream_stop.is_set()

    text = "".join(parts)
    if text and not text.endswith("\n"):
        print()

    if not cancelled:
        # Completed normally — clear the streaming output
        def terminal_rows(t):
            rows = 0
            for line in t.split("\n"):
                rows += max(1, (len(line) + cols - 1) // cols)
            return rows

        rows = 1 + terminal_rows(text)
        for _ in range(rows):
            sys.stdout.write("\033[2K\033[A")
        sys.stdout.write("\033[2K")
        sys.stdout.flush()
    else:
        # Cancelled — leave partial output visible
        sys.stdout.write(f"{GREY}  (cancelled){RESET}\n")
        sys.stdout.flush()

    return text.strip(), cancelled


# ── parse agent response ────────────────────────────────────────────────

def parse_agent_response(text):
    """Return (thought, tool_name, params, answer) — exactly one of tool/answer set.

    Supports two formats:
    1. JSON: {"thought": "...", "tool": "...", "params": {...}}
    2. XML: <tool_call><function=NAME><parameter=KEY>VAL</parameter></function></tool_call>
    """
    # ── Strategy 1: pure JSON ──────────────────────────────────────────
    try:
        obj = json.loads(text)
        thought = obj.get("thought", "")
        if "answer" in obj:
            return thought, None, None, str(obj["answer"])
        if "tool" in obj:
            params = obj.get("params")
            if params is None:
                # Model put params as top-level keys instead of nested
                params = {k: v for k, v in obj.items() if k not in ("thought", "tool")}
            return thought, obj["tool"], params, None
    except json.JSONDecodeError:
        pass

    # ── Strategy 2: JSON block extracted from prose ────────────────────
    import re
    matches = re.findall(r"\{[^{}]*\}", text, re.DOTALL)
    for m in reversed(matches):
        try:
            obj = json.loads(m)
            thought = obj.get("thought", "")
            if "answer" in obj:
                return thought, None, None, str(obj["answer"])
            if "tool" in obj:
                params = obj.get("params")
                if params is None:
                    params = {k: v for k, v in obj.items() if k not in ("thought", "tool")}
                return thought, obj["tool"], params, None
        except json.JSONDecodeError:
            continue

    # ── Strategy 3: XML tool_call format ──────────────────────────────
    # Format: <tool_call><function=NAME><parameter=KEY>VAL</parameter></function></tool_call>
    tc_match = re.search(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL)
    if tc_match:
        inner = tc_match.group(1)
        # Extract function (after <function= or <function>)
        func_match = re.search(r"<function[=>]\s*(\S+?)\s*>", inner)
        if func_match:
            tool_name = func_match.group(1).strip()
            # Extract all parameters
            params = {}
            for p_match in re.finditer(
                r"<parameter[=>]\s*(\S+?)\s*>(.*?)</parameter>", inner, re.DOTALL
            ):
                key = p_match.group(1).strip()
                val = p_match.group(2).strip()
                params[key] = val
            # Everything before <tool_call> is the thought
            thought = text[:tc_match.start()].strip()
            return thought, tool_name, params, None

    # ── Strategy 4: markdown-fenced JSON ───────────────────────────────
    # ```json { ... } ```
    fence_match = re.search(
        r"```(?:json)?\s*\n?(\{.*?\})\n?\s*```", text, re.DOTALL
    )
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1))
            thought = obj.get("thought", "")
            if "answer" in obj:
                return thought, None, None, str(obj["answer"])
            if "tool" in obj:
                params = obj.get("params")
                if params is None:
                    params = {k: v for k, v in obj.items() if k not in ("thought", "tool")}
                return thought, obj["tool"], params, None
        except json.JSONDecodeError:
            pass

    return None, None, None, {
        "error": "Could not parse agent response as JSON or XML tool_call",
        "raw": text[:500],
    }


# ── main loop ──────────────────────────────────────────────────────────

def main():
    global MODEL
    MODEL = detect_model()

    # Check for saved sessions on startup
    messages = pick_session()
    if messages is None:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Load skills and append to system prompt
        skills_list, skills_text = load_skills()
        if skills_text:
            messages[0]["content"] += "\n\n" + skills_text
            print(f"  {GREY}loaded {len(skills_list)} skill(s) from {SKILLS_DIR}{RESET}")
        print()
    else:
        if not any(m.get("role") == "system" for m in messages):
            messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        # Ensure skills are loaded even on resumed session
        skills_list, skills_text = load_skills()
        if skills_text:
            # Only append if not already present
            sys_text = messages[0]["content"]
            if "<skill" not in sys_text:
                messages[0]["content"] += "\n\n" + skills_text
                print(f"  {GREY}loaded {len(skills_list)} skill(s) from {SKILLS_DIR}{RESET}")

    print(f"Agent harness — model: {MODEL}")
    print(f"Endpoint: {BASE_URL}")
    print("Commands: /q, /save <name>, /list, /del <name>, /compress, /config, /skills, /reload-skills, /help")
    print(f"Skills dir: {SKILLS_DIR} ({len(skills_list)} loaded)\n")

    # List-wrapped so nested handle_cmd can rebind it
    session_name = [None]

    def handle_cmd(cmd):
        """Process a /command. Returns 'exit', 'continue', or None (not a command)."""
        c = cmd.lower()

        if c in ("/q", "/quit", "/exit"):
            if len(messages) > 1:
                name = session_name[0] or datetime.now().strftime("%Y%m%d_%H%M%S")
                save_session(name, messages)
                print(f"  saved as '{name}', bye.")
            return "exit"

        if c == "/list":
            s = list_sessions()
            if not s:
                print("  no saved sessions")
            else:
                for i, (nm, ts, _) in enumerate(s):
                    print(f"  [{i}] {nm}  ({ts})")
            return "continue"

        if c.startswith("/del "):
            target = cmd[5:].strip()
            if os.path.isfile(session_path(target)):
                delete_session(target)
                print(f"  deleted '{target}'")
            else:
                print(f"  session '{target}' not found")
            return "continue"

        if c.startswith("/save"):
            parts = cmd.split(None, 1)
            name = parts[1].strip() if len(parts) > 1 else session_name[0] or datetime.now().strftime("%Y%m%d_%H%M%S")
            session_name[0] = name
            save_session(name, messages)
            print(f"  saved as '{name}'")
            return "continue"

        if c.startswith("/compress"):
            before = len(messages)
            new_msgs = compress_history(messages)
            messages.clear()
            messages.extend(new_msgs)
            after = len(messages)
            print(f"  {GOLD}compressed: {before} -> {after} messages{RESET}")
            return "continue"

        if c.startswith("/config"):
            used = estimate_tokens(messages)
            limit = int(CONTEXT_LENGTH * COMPRESSION_TRIGGER)
            print(f"  model: {MODEL}")
            print(f"  context: {CONTEXT_LENGTH} tokens")
            print(f"  compression model: {pick_compression_model()}")
            print(f"  compression trigger: {int(COMPRESSION_TRIGGER*100)}% ({limit} tokens)")
            print(f"  estimated tokens: {used}")
            print(f"  messages: {len(messages)}")
            print(f"  endpoint: {BASE_URL}")
            return "continue"

        if c == "/skills":
            if not SKILLS_CACHE:
                print("  no skills loaded")
            else:
                for fname, body in SKILLS_CACHE:
                    print(f"  {GREEN}{fname}{RESET} ({len(body)} chars)")
            return "continue"

        if c == "/reload-skills":
            count = reload_skills_into_system(messages)
            print(f"  {GOLD}reloaded {count} skill(s){RESET}")
            return "continue"

        if c == "/help":
            print("  Commands: /q, /save <name>, /list, /del <name>, /compress, /config, /skills, /reload-skills, /help")
            print("  Tools: read_file, write_file, patch, shell, browser, ask_user, write_skill")
            return "continue"

        return None  # not a command

    while True:
        try:
            user = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            if len(messages) > 1:
                name = session_name[0] or datetime.now().strftime("%Y%m%d_%H%M%S")
                save_session(name, messages)
                print(f"  saved as '{name}', bye.")
            break

        result = handle_cmd(user)
        if result == "exit":
            break
        if result == "continue":
            continue
        # None → not a command, treat as user request

        messages.append({"role": "user", "content": user})

        # ── run agent rounds (with interruptible streaming) ────────────

        round_interrupted = False
        _stream_stop.clear()

        for _round in range(1, MAX_TOOL_ROUNDS + 1):
            if round_interrupted:
                break

            # Start streaming in a background thread
            result_q = queue.Queue()

            def stream_worker(msgs, rnd, q):
                raw_text, was_cancelled = llm_chat(msgs, label=f"round {rnd}")
                q.put((raw_text, was_cancelled))

            t = threading.Thread(target=stream_worker, args=(messages, _round, result_q), daemon=True)
            t.start()

            # Poll for user input while the model streams
            user_during_stream = None
            while t.is_alive():
                if _sigint_count > 0:
                    _stream_stop.set()
                    t.join(timeout=2)
                    save_and_exit(messages, session_name[0])
                    return  # unreachable but safe

                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                if ready:
                    line = sys.stdin.readline().strip()
                    # If it was just a stray newline from terminal, skip
                    if not line and not user_during_stream:
                        continue
                    user_during_stream = line
                    _stream_stop.set()
                    t.join(timeout=2)
                    break

            # Collect the result
            try:
                raw, cancelled = result_q.get_nowait()
            except queue.Empty:
                raw, cancelled = "", False

            if cancelled and user_during_stream is not None:
                # User typed something mid-stream
                if user_during_stream.lower().startswith("/"):
                    # Process the command, then break back to prompt
                    round_interrupted = True
                    cmd_result = handle_cmd(user_during_stream)
                    if cmd_result == "exit":
                        return  # already saved inside handle_cmd
                    break
                else:
                    # New user request — inject as a fresh user message and
                    # let the next round continue with it
                    messages.append({"role": "user", "content": user_during_stream})
                    continue

            # ── normal post-stream processing (unchanged) ──────────────

            thought, tool_name, params, answer = parse_agent_response(raw)

            if thought:
                print(f"{GREY}  thought: {thought[:300]}{RESET}")

            if answer is not None:
                print(f"\n{GOLD}  answer: {answer}{RESET}")
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": "If the task is complete, say so in your answer. If more work remains, continue with another tool call."})
                break

            if tool_name is None or params is None:
                err = answer or {"error": "Agent response format unrecognised"}
                print(f"  parse error: {err}")
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Your response was not parseable. Use JSON or XML format. Raw: {json.dumps(err)}"})
                continue

            if tool_name not in TOOL_MAP:
                print(f"  unknown tool: {tool_name}")
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Tool '{tool_name}' is not available. Available tools: {list(TOOL_MAP.keys())}. Choose one."})
                continue

            # ask_user is interactive — handle from main thread
            if tool_name == "ask_user":
                print(f"{GREEN}  tool: ask_user({json.dumps(params)[:200]}){RESET}", flush=True)
                result = tool_ask_user_impl(**params)
                print(f"{GREEN}  result: {result[:500]}{RESET}", flush=True)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Tool result:\n{result}\n\nContinue. If the task is fully done, respond with an 'answer'. Otherwise make the next tool call."})
                if should_compress(messages):
                    messages = compress_history(messages)
                continue

            # write_skill — writes a .md file and reloads into system prompt
            if tool_name == "write_skill":
                print(f"{GREEN}  tool: write_skill({json.dumps(params)[:200]}){RESET}", flush=True)
                result = tool_write_skill_impl(**params, msgs=messages)
                print(f"{GREEN}  result: {result[:500]}{RESET}", flush=True)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Tool result:\n{result}\n\nContinue. If the task is fully done, respond with an 'answer'. Otherwise make the next tool call."})
                if should_compress(messages):
                    messages = compress_history(messages)
                continue

            print(f"{GREEN}  tool: {tool_name}({json.dumps(params)[:200]}){RESET}", flush=True)

            try:
                result = TOOL_MAP[tool_name](**params)
            except Exception as e:
                result = json.dumps({"error": str(e)})

            print(f"{GREEN}  result: {result[:500]}{RESET}", flush=True)

            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"Tool result:\n{result}\n\nContinue. If the task is fully done, respond with an 'answer'. Otherwise make the next tool call."})

            # Auto-compress if context threshold exceeded
            if should_compress(messages):
                messages = compress_history(messages)
        else:
            print("  (max rounds reached)")
            messages.append({"role": "system", "content": "You have reached the maximum number of tool rounds. If the task is complete, give your final answer. If not, state what remains."})
            raw, _ = llm_chat(messages)
            thought, _, _, answer = parse_agent_response(raw)
            print(f"{GOLD}  final: {answer or raw[:500]}{RESET}")

        print()


if __name__ == "__main__":
    main()