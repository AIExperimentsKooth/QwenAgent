"""Dispatchable tool implementations: file tools, shell and browser.

Each tool takes plain arguments and returns a JSON string. ``TOOL_MAP``
maps tool names to callables for the main loop's dispatcher. The
interactive tools (ask_user / write_skill / load_skill) live in
``interactive`` because they need access to the conversation state.
"""

import json
import os
import subprocess

from . import config

# ── file tools ─────────────────────────────────────────────────────────

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


# ── shell tool ─────────────────────────────────────────────────────────

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


# ── browser tool (browser-harness CLI) ─────────────────────────────────

BH_WORKSPACE = config.repo_path("QWEN_TOOLBIN_DIR", "toolbin")


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


# ── registries ─────────────────────────────────────────────────────────

TOOL_MAP = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "patch": tool_patch,
    "shell": tool_shell,
    "browser": tool_browser,
    "ask_user": None,  # handled specially in main loop — not dispatched through here
    "write_skill": None,  # handled specially — reloads system prompt
    "load_skill": None,  # handled specially — injects skill content into chat
}

TOOL_ARGS = {
    "ask_user": {
        "question": {"type": "string", "description": "Question to ask the user"},
        "choices": {"type": "list", "description": "Optional list of options (2-4). Omit for free-text input."},
    },
    "write_skill": {
        "name": {"type": "string", "description": "Skill name (without .md suffix)"},
        "content": {"type": "string", "description": "Full markdown content of the skill file"},
    },
    "load_skill": {
        "name": {"type": "string", "description": "Skill name to load (without .md)"},
    },
}