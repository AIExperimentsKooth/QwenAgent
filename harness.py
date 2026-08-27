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
from openai import OpenAI

# ── ANSI colors ────────────────────────────────────────────────────────
GREY = "\033[90m"
GREEN = "\033[32m"
GOLD = "\033[33m"
RESET = "\033[0m"

# ── config ─────────────────────────────────────────────────────────────
BASE_URL = os.environ.get("QWEN_API_BASE", "http://100.64.2.163:1234/v1")
MODEL = os.environ.get("QWEN_MODEL", None)  # optional override; None = auto-detect
API_KEY = os.environ.get("QWEN_API_KEY", "")  # the endpoint doesn't need one
MAX_TOOL_ROUNDS = 12  # safety limit

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

# ── system prompt ──────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an autonomous agent with access to four tools. You think, then act, then observe. You continue until the user's task is fully done.

## Tools

### 1. read_file(path, [offset=1], [limit=2000])
Read a text file. Lines are 1-indexed. Returns content and total line count.

### 2. write_file(path, content)
**Write entire content to a file, overwriting anything that was there.** Only use this when you are *creating* a new file or when patch cannot express the change (e.g. wholesale replacement). For any edit to an existing file, **always prefer patch**.

### 3. patch(path, old_string, new_string)
Find `old_string` in the file and replace it with `new_string`. **This is the preferred way to edit existing files.** Use patch over write_file whenever you are making a targeted change, not a full rewrite.

### 4. shell(command)
Run any shell command. The working directory persists between calls. Use this for creating directories, listing files, running scripts, git operations, builds, etc.

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


TOOL_MAP = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "patch": tool_patch,
    "shell": tool_shell,
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

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    print(f"Agent harness — model: {MODEL}")
    print(f"Endpoint: {BASE_URL}")
    print("Type your request, or /q to quit.\n")

    while True:
        try:
            user = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if user.lower() in ("/q", "/quit", "/exit"):
            break

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
        else:
            print("  (max rounds reached)")
            messages.append({"role": "system", "content": "You have reached the maximum number of tool rounds. If the task is complete, give your final answer. If not, state what remains."})
            raw = llm_chat(messages)
            thought, _, _, answer = parse_agent_response(raw)
            print(f"{GOLD}  final: {answer or raw[:500]}{RESET}")

        print()


if __name__ == "__main__":
    main()