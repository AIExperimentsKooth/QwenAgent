"""Skills system: discover ``.md`` skill files and expose them to the agent.

Skills live in ``config.SKILLS_DIR`` (default: ``skills/``). The loader
caches ``(name, description, content)`` tuples in ``SKILLS_CACHE`` and
produces a compact summary (names + descriptions only) for the system
prompt. Full content is injected on demand via ``get_skill_content``.
"""

import os

from . import config

SKILLS_CACHE = []  # list of (name, description, content)


def extract_description(body):
    """Extract description from YAML frontmatter, or use first line."""
    body = body.strip()
    if body.startswith("---"):
        end = body.find("---", 3)
        if end > 0:
            front = body[3:end].strip()
            for line in front.split("\n"):
                if line.startswith("description:"):
                    val = line[len("description:"):].strip().strip('"').strip("'")
                    return val
    # Fallback: first non-empty line
    first = [l.strip() for l in body.split("\n") if l.strip()]
    return first[0][:120] if first else "(no description)"


def load_skills():
    """Read all .md files from SKILLS_DIR.

    Returns (list_of_tuples, summary_text) where summary_text is
    a compact listing of skill names + descriptions only (no full content).
    """
    global SKILLS_CACHE
    SKILLS_CACHE = []
    os.makedirs(config.SKILLS_DIR, exist_ok=True)
    names = sorted(os.listdir(config.SKILLS_DIR))
    summary_lines = []
    for fname in names:
        if not fname.endswith(".md"):
            continue
        path = os.path.join(config.SKILLS_DIR, fname)
        try:
            with open(path) as f:
                body = f.read().strip()
            name = fname[:-3]
            desc = extract_description(body)
            SKILLS_CACHE.append((name, desc, body))
            summary_lines.append(f"  {name}: {desc}")
        except Exception as e:
            print(f"  {config.GREY}skill '{fname}' failed to load: {e}{config.RESET}")

    if summary_lines:
        summary = "## Available Skills\n" + "\n".join(summary_lines)
        summary += "\n\nUse `load_skill(name)` to load a skill's full content into the conversation."
    else:
        summary = ""
    return SKILLS_CACHE, summary


def get_skill_content(name):
    """Return the full content of a skill by name, or None."""
    for n, desc, body in SKILLS_CACHE:
        if n == name or n == name + ".md":
            return body
    return None