"""Interactive (main-thread) tools and system-prompt rebuilding.

These tools are NOT dispatched through ``TOOL_MAP`` — they need access to
the live conversation state (``msgs``) and must run on the main thread
because they block on user input or mutate the system prompt.
"""

import json
import os

from . import config, skills


# ── ask_user ───────────────────────────────────────────────────────────

def tool_ask_user_impl(question, choices=None):
    """Display a question to the user and return their response.

    Called synchronously from the main loop — not threaded.
    """
    if choices:
        print(f"\n{config.GOLD}  ? {question}{config.RESET}")
        for i, c in enumerate(choices):
            print(f"    [{i}] {c}")
        print(f"    [t] type your own answer")
        while True:
            try:
                sel = input(f"{config.GOLD}  > {config.RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                return json.dumps({"response": None, "cancelled": True})
            if sel.lower() == "t":
                try:
                    free = input(f"{config.GOLD}  type your answer: {config.RESET}").strip()
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
            ans = input(f"\n{config.GOLD}  ? {question}{config.RESET}\n{config.GOLD}  > {config.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            return json.dumps({"response": None, "cancelled": True})
        return json.dumps({"response": ans})


# ── skill tools ────────────────────────────────────────────────────────

def reload_skills_into_system(msgs):
    """Rebuild messages[0] system content with fresh skill summary (not full content)."""
    skills_list, skills_summary = skills.load_skills()
    # Preserve any already-injected skill content (kept in the conversation
    # as user messages by load_skill) — but strip old skill-summary block
    # from previous reloads by rebuilding from a clean base.
    existing = msgs[0]["content"]
    # Remove an old "## Available Skills" section if present
    base = existing.split("\n## Available Skills")[0].rstrip()
    msgs[0]["content"] = base + ("\n\n" + skills_summary if skills_summary else "")
    return len(skills_list)


def tool_write_skill_impl(name, content, msgs):
    """Write a skill file and auto-reload. Called from main thread."""
    if not name.endswith(".md"):
        fname = name + ".md"
    else:
        fname = name
    path = os.path.join(config.SKILLS_DIR, fname)
    os.makedirs(config.SKILLS_DIR, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    count = reload_skills_into_system(msgs)
    return json.dumps({
        "path": path,
        "bytes": len(content),
        "skills_loaded": count,
        "status": "ok",
    })


def tool_load_skill_impl(name, msgs):
    """Load a skill's full content into the conversation as a user message.

    The skill summary stays in the system prompt; full content is injected
    on demand so the context window isn't flooded with every skill.
    """
    body = skills.get_skill_content(name)
    if body is None:
        return json.dumps({"error": f"Skill '{name}' not found. Available: {[n for n, _, _ in skills.SKILLS_CACHE]}"})
    # Inject skill content as a user message so the agent sees it
    msgs.append({"role": "user", "content": f"[Skill loaded: {name}]\n{body}"})
    return json.dumps({
        "skill": name,
        "bytes": len(body),
        "injected": True,
        "status": "ok",
    })