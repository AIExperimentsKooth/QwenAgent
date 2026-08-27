#!/home/aiker/qwenAgent/.venv/bin/python3
"""Minimal tool-calling loop over an OpenAI-compatible endpoint.

Auto-detects the loaded model from /v1/models at startup.
Tools: read_file, write_file, patch (find/replace), shell.
System prompt instructs the agent to prefer patch over write_file."""

import json
import os
import subprocess
import sys
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

client = OpenAI(base_url=BASE_URL, api_key=API_KEY or "not-needed", timeout=None, max_retries=0)


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

# ── system prompt ──────────────────────────────────────────────────────

# ── compression ─────────────────────────────────────────────────────────

def estimate_tokens(messages):
    """Rough token estimate: ~4 chars per token for text."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4 + 100  # overhead per message
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


def compress_history(messages):
    """Compress the oldest conversation history into a summary message.

    Removes all non-system messages up to the last user request, sends them
    to the compression model, and inserts a single 'system' message with the
    compressed summary in their place. Keeps the system prompt and the most
    recent user+assistant exchange untouched.
    """
    if len(messages) < 4:
        return messages  # nothing meaningful to compress

    c_model = pick_compression_model()
    print(f"{GREY}  compressing history ({c_model})...{RESET}", flush=True)

    # Find the system prompt(s) — keep them
    system_msgs = [m for m in messages if m["role"] == "system"]
    non_system = [m for m in messages if m["role"] != "system"]

    if len(non_system) < 2:
        return messages

    # Keep the last user+assistant exchange (2 messages at minimum)
    keep_count = min(4, len(non_system))
    to_compress = non_system[:-keep_count]
    to_keep = non_system[-keep_count:]

    # Build a compression prompt from the messages to compress
    compressed_text = ""
    for m in to_compress:
        role = m["role"]
        content = m.get("content", "")
        compressed_text += f"[{role}]\n{content}\n\n"

    summary_prompt = [
        {"role": "system", "content": "You are a conversation summarizer. Compress the following agent conversation history into a concise summary. Keep all key facts, decisions, and context needed to continue the task. Output only the summary, no preamble."},
        {"role": "user", "content": f"Summarize this conversation history:\n\n{compressed_text}"},
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
        return messages

    # Rebuild: system prompts + summary + recent messages
    result = list(system_msgs)
    result.append({"role": "system", "content": f"[Compressed history]\n{summary}"})
    result.extend(to_keep)

    return result


# ── system prompt ──────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an autonomous agent with access to five tools. You think, then act, then observe. You continue until the user's task is fully done.

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

## Protocol

Your response must be valid JSON in one of two shapes:

### Tool-call shape
```json
{
  "thought": "I need to read the config file first to understand the structure.",
  "tool": "read_file",
  "params": {"path": "/path/to/file"}
}
```

### Final-answer shape
```json
{
  "thought": "The task is done — I verified the file exists and the config is correct.",
  "answer": "All done. The config was updated and tests pass."
}
```

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
}


# ── LLM call ───────────────────────────────────────────────────────────

def llm_chat(messages, label=""):
    """Stream model output to terminal, then clear it and return the full text."""
    import shutil
    cols = shutil.get_terminal_size().columns

    header = f"  {label}" if label else ""
    print(header)

    stream = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.1,
        max_tokens=2048,
        stream=True,
    )

    buffer = ""
    for chunk in stream:
        token = chunk.choices[0].delta.content or ""
        buffer += token
        print(f"{GREY}{token}{RESET}", end="", flush=True)

    if not buffer.endswith("\n"):
        print()  # land on a clean line

    # Calculate how many terminal rows the streaming output consumed
    def terminal_rows(text):
        rows = 0
        for line in text.split("\n"):
            rows += max(1, (len(line) + cols - 1) // cols)
        return rows

    rows_to_clear = 1 + terminal_rows(buffer)  # header + content

    # Move up and clear each row
    for _ in range(rows_to_clear):
        sys.stdout.write("\033[2K")   # clear entire line
        sys.stdout.write("\033[A")    # cursor up
    sys.stdout.write("\033[2K")       # clear the row cursor is now on
    sys.stdout.flush()

    return buffer.strip()


# ── parse agent response ────────────────────────────────────────────────

def parse_agent_response(text):
    """Return (thought, tool_name, params, answer) — exactly one of tool/answer set."""
    # Try to find and parse a JSON block
    # First, try the whole text
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract a JSON block from markdown fences or braces
        import re
        matches = re.findall(r"\{[^{}]*\}", text, re.DOTALL)
        for m in reversed(matches):  # try largest first
            try:
                obj = json.loads(m)
                break
            except json.JSONDecodeError:
                continue
        else:
            return None, None, None, {"error": f"Could not parse JSON from agent response", "raw": text[:500]}

    thought = obj.get("thought", "")
    if "answer" in obj:
        return thought, None, None, str(obj["answer"])
    if "tool" in obj and "params" in obj:
        return thought, obj["tool"], obj["params"], None
    return None, None, None, {"error": f"Response has neither 'answer' nor 'tool'/'params'", "raw": text[:500]}


# ── main loop ──────────────────────────────────────────────────────────

def main():
    global MODEL
    MODEL = detect_model()

    # Check for saved sessions on startup
    messages = pick_session()
    if messages is None:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        print()
    else:
        # If resumed session has no system prompt, prepend one
        if not any(m.get("role") == "system" for m in messages):
            messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    print(f"Agent harness — model: {MODEL}")
    print(f"Endpoint: {BASE_URL}")
    print("Commands: /q to quit+suspend, /save <name>, /list, /del <name>, /compress, /config\n")

    session_name = None
    auto_save = False

    while True:
        try:
            user = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        # ── built-in commands ──────────────────────────────────────────

        if user.lower() in ("/q", "/quit", "/exit"):
            if len(messages) > 1:
                name = session_name or datetime.now().strftime("%Y%m%d_%H%M%S")
                save_session(name, messages)
                print(f"  saved as '{name}', bye.")
            break

        if user.lower() == "/list":
            sessions = list_sessions()
            if not sessions:
                print("  no saved sessions")
            else:
                for i, (name, ts, _) in enumerate(sessions):
                    print(f"  [{i}] {name}  ({ts})")
            continue

        if user.lower().startswith("/del "):
            target = user[5:].strip()
            if os.path.isfile(session_path(target)):
                delete_session(target)
                print(f"  deleted '{target}'")
            else:
                print(f"  session '{target}' not found")
            continue

        if user.lower().startswith("/save"):
            parts = user.split(None, 1)
            name = parts[1].strip() if len(parts) > 1 else session_name or datetime.now().strftime("%Y%m%d_%H%M%S")
            session_name = name
            save_session(name, messages)
            print(f"  saved as '{name}'")
            continue

        if user.lower().startswith("/compress"):
            before = len(messages)
            messages = compress_history(messages)
            after = len(messages)
            print(f"  {GOLD}compressed: {before} -> {after} messages{RESET}")
            continue

        if user.lower().startswith("/config"):
            used = estimate_tokens(messages)
            limit = int(CONTEXT_LENGTH * COMPRESSION_TRIGGER)
            print(f"  model: {MODEL}")
            print(f"  context: {CONTEXT_LENGTH} tokens")
            print(f"  compression model: {pick_compression_model()}")
            print(f"  compression trigger: {int(COMPRESSION_TRIGGER*100)}% ({limit} tokens)")
            print(f"  estimated tokens in session: {used}")
            print(f"  messages: {len(messages)}")
            print(f"  endpoint: {BASE_URL}")
            continue

        messages.append({"role": "user", "content": user})

        for _round in range(1, MAX_TOOL_ROUNDS + 1):
            raw = llm_chat(messages, label=f"round {_round}")
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
                messages.append({"role": "user", "content": f"Your response was not parseable. Respond with valid JSON: either {{\"thought\": \"...\", \"tool\": \"...\", \"params\": {{...}}}} or {{\"thought\": \"...\", \"answer\": \"...\"}}. Raw: {json.dumps(err)}"})
                continue

            if tool_name not in TOOL_MAP:
                print(f"  unknown tool: {tool_name}")
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Tool '{tool_name}' is not available. Available tools: {list(TOOL_MAP.keys())}. Choose one."})
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
            raw = llm_chat(messages)
            thought, _, _, answer = parse_agent_response(raw)
            print(f"{GOLD}  final: {answer or raw[:500]}{RESET}")

        print()


if __name__ == "__main__":
    main()