"""Session persistence: save, list, load, delete and pick sessions.

Session files are JSON documents stored in ``config.SESSIONS_DIR``
(default: ``sessions/`` inside the repository).
"""

import json
import os
from datetime import datetime

from . import config

SESSION_IGNORE_ROLES = {"system", "assistant"}
MAX_SESSION_DISPLAY = 1024


def session_path(name):
    return os.path.join(config.SESSIONS_DIR, f"{name}.json")


def list_sessions():
    """Return sorted list of (name, timestamp, preview)."""
    os.makedirs(config.SESSIONS_DIR, exist_ok=True)
    sessions = []
    for fname in sorted(os.listdir(config.SESSIONS_DIR), reverse=True):
        if not fname.endswith(".json"):
            continue
        name = fname[:-5]
        path = os.path.join(config.SESSIONS_DIR, fname)
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
    os.makedirs(config.SESSIONS_DIR, exist_ok=True)
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
        "model": config.MODEL,
        "endpoint": config.BASE_URL,
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
        print(f"      {config.GOLD}{short}{config.RESET}")
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