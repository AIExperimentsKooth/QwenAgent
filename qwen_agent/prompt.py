"""The static system prompt describing the agent's tools and protocol.

The tool section is assembled from the docs below so the prompt always
matches the tools that are actually available — notably the optional
browser tool, which only exists when browser-harness is installed.
"""

import platform
import time

from . import config

# (signature, body) per tool, in prompt order. The heading number is
# generated, so adding or hiding a tool renumbers the list automatically.
_TOOL_DOCS = {
    "read_file": (
        "read_file(path, [offset=1], [limit=2000])",
        "Read a text file. Lines are 1-indexed. Returns content and total line count.",
    ),
    "write_file": (
        "write_file(path, content)",
        "**Write entire content to a file, overwriting anything that was there.** Only use this when you are *creating* a new file or when patch cannot express the change (e.g. wholesale replacement). For any edit to an existing file, **always prefer patch**.",
    ),
    "patch": (
        "patch(path, old_string, new_string)",
        "Find `old_string` in the file and replace it with `new_string`. **This is the preferred way to edit existing files.** Use patch over write_file whenever you are making a targeted change, not a full rewrite.",
    ),
    "shell": (
        "shell(command)",
        "Run any shell command. The working directory persists between calls. Use this for creating directories, listing files, running scripts, git operations, builds, etc.",
    ),
    "browser": (
        "browser(code)",
        """**Browser automation via browser-harness.** Accepts multi-line Python code that runs inside the browser-harness environment. Pre-imported helpers:

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

Always wait_for_load() after navigation. After clicking, verify with a targeted js() or page_info() call. If a page is bot-protected or a direct fetch fails, escalate to the browser tool.""",
    ),
    "ask_user": (
        "ask_user(question, [choices])",
        """**Ask the user for input.** Use this when you need a decision, clarification, or information that only the user can provide.
- `question` (required): what to ask
- `choices` (optional): a list of 2-4 predefined options. When provided, the user can pick one or type a free-text answer.
- If you omit `choices`, the user types a free-form response.
- The result will be a JSON object with the user's `"response"` and, if a choice was picked, the `"choice_index"`.

When in doubt about a decision, ask the user rather than guessing.""",
    ),
    "write_skill": (
        "write_skill(name, content)",
        """**Write a reusable skill to the skills directory and register it as available.**
- `name` — skill name (without .md). The file is saved as `skills/<name>.md`.
- `content` — full markdown content. Include YAML frontmatter with `name:` and `description:` fields.
- The skill's name and description appear in the `## Available Skills` section of your system prompt.
- The full content is NOT loaded until you or the agent calls `load_skill(name)`.
- Use this to save workflows, helper patterns, or site-specific instructions you discover.""",
    ),
    "load_skill": (
        "load_skill(name)",
        """**Load a skill's full content into the conversation.**
- `name` — skill name (without .md) from the Available Skills list.
- The full skill content is injected as a user message so the agent can follow it.
- Only load skills you actually need for the current task — this saves context space.""",
    ),
}

_TOOL_ORDER = [
    "read_file",
    "write_file",
    "patch",
    "shell",
    "browser",
    "ask_user",
    "write_skill",
    "load_skill",
]


def tool_names():
    """Return the tool names the agent is told about, in prompt order."""
    names = []
    for name in _TOOL_ORDER:
        if name == "browser" and not config.BROWSER_ENABLED:
            continue  # optional: only offered when browser-harness is installed
        names.append(name)
    return names


def _tools_section():
    blocks = []
    for i, name in enumerate(tool_names(), 1):
        signature, body = _TOOL_DOCS[name]
        blocks.append(f"### {i}. {signature}\n{body}")
    return "\n\n".join(blocks)


_PROTOCOL = """## Protocol

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


def build_system_prompt():
    """Assemble the prompt for the tools available in this environment."""
    names = tool_names()
    return (
        f"You are an autonomous agent with access to {len(names)} tools. "
        "You think, then act, then observe. You continue until the user's task is fully done.\n\n"
        "## Tools\n\n"
        f"{_tools_section()}\n\n"
        f"{_PROTOCOL}"
    )


SYSTEM_PROMPT = build_system_prompt()


def system_diagnostics():
    """Probe basic system facts, appended to the system prompt on a new session.

    Reports OS family (windows / mac / linux), uptime and available RAM.
    Never raises — any probe failure degrades to "unknown" so startup is
    never blocked by an exotic environment.
    """
    sysname = platform.system()
    if sysname == "Windows":
        os_label = "windows"
    elif sysname == "Darwin":
        os_label = "mac"
    elif sysname == "Linux":
        os_label = "linux"
    else:
        os_label = (sysname or "unknown").lower()

    return (
        "## System\n"
        f"- OS: {os_label}\n"
        f"- Uptime: {_uptime_str(sysname)}\n"
        f"- Available RAM: {_available_ram_str(sysname)}"
    )


def _uptime_str(sysname):
    try:
        if sysname == "Linux":
            with open("/proc/uptime") as f:
                seconds = float(f.read().split()[0])
        elif sysname == "Windows":
            import ctypes
            seconds = ctypes.windll.kernel32.GetTickCount64() / 1000.0
        elif sysname == "Darwin":
            import re
            import subprocess
            out = subprocess.check_output(["sysctl", "-n", "kern.boottime"], text=True)
            boot_sec = int(re.search(r"sec = (\d+)", out).group(1))
            seconds = time.time() - boot_sec
        else:
            return "unknown"
    except Exception:
        return "unknown"

    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _available_ram_str(sysname):
    try:
        if sysname == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        mb = int(line.split()[1]) // 1024  # kB -> MB
                        break
                else:
                    return "unknown"
        elif sysname == "Windows":
            import ctypes

            class _MemStat(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MemStat()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            mb = stat.ullAvailPhys // (1024 * 1024)
        elif sysname == "Darwin":
            import subprocess
            page = int(subprocess.check_output(["sysctl", "-n", "hw.pagesize"], text=True))
            out = subprocess.check_output(["vm_stat"], text=True)
            pages = 0
            for line in out.splitlines():
                key, _, val = line.partition(":")
                key = key.strip()
                if key in ("Pages free", "Pages inactive", "Pages speculative"):
                    pages += int(val.strip().rstrip("."))
            mb = (pages * page) // (1024 * 1024)
        else:
            return "unknown"
    except Exception:
        return "unknown"

    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb} MB"
