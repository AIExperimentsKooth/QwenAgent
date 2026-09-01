#!/usr/bin/env python3
"""QwenAgent entry point.

Thin launcher — the implementation lives in the ``qwen_agent`` package,
split into small single-purpose modules so individual agents can work on
one component at a time:

    config.py      — colors, repo-relative paths, env config, LLM client
    control.py     — stream cancellation events + SIGINT handling
    prompt.py      — the static system prompt
    sessions.py    — save/list/load/delete/resume sessions
    skills.py      — skill discovery and content lookup
    context.py     — token estimation + history compression
    parse.py       — parsing raw model output into tool calls / answers
    tools.py       — dispatchable tools (read/write/patch/shell/browser)
    interactive.py — main-thread tools (ask_user, write_skill, load_skill)
    llm.py         — streaming LLM chat with cancellation
    main.py        — interactive main loop + /commands
"""

from qwen_agent.main import main

if __name__ == "__main__":
    main()