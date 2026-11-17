# Qwen3.8-27B Agentic Harness

Minimal tool-calling loop over any OpenAI-compatible endpoint. Designed for running Qwen-style models locally, with live streaming, session persistence, context compression, and skills.

**No third-party Python packages.** The harness uses only the Python standard library — the OpenAI-compatible HTTP/SSE client is built in — so it installs and runs on Termux, Linux, macOS and Windows with nothing but Python 3.10+.

## Quick Start

```bash
./install.sh
./boot.sh
```

**Termux** (Android):

```bash
pkg install python git
./install.sh
./boot.sh
```

**Windows** (from the repo folder, in cmd or PowerShell):

```
install.bat
start.bat
```

To update the checkout and re-run the installer:

```bash
./update.sh
```

On Windows (native cmd/PowerShell, no Git Bash needed): `update.cmd`

On startup the harness auto-detects whatever model is loaded at `http://localhost:1234/v1` (no API key required). Point `QWEN_API_BASE` at your server if it lives elsewhere — on Termux the model usually runs on another machine:

```bash
QWEN_API_BASE=http://192.168.1.20:1234/v1 ./boot.sh
```

On startup you can resume a previous session or start fresh.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `QWEN_API_BASE` | `http://localhost:1234/v1` | OpenAI-compatible endpoint |
| `QWEN_MODEL` | auto-detect | Override model selection |
| `QWEN_API_KEY` | *(none)* | API key if required |
| `QWEN_CONTEXT_LENGTH` | `32768` | Model's max context (tokens) |
| `QWEN_COMPRESSION_MODEL` | *(same as MODEL)* | Separate model for compression |
| `QWEN_COMPRESSION_TRIGGER` | `0.75` | Compress when context is this % full |
| `QWEN_MAX_TOKENS` | `16384` | Max tokens per response |
| `QWEN_SKILLS_DIR` | `skills` | Directory for .md skill files (relative to the repository) |
| `QWEN_SESSIONS_DIR` | `sessions` | Where saved sessions live |
| `QWEN_WORKSPACE_DIR` | `workspace` | Working directory for the agent |
| `QWEN_BROWSER_CMD` | `browser-harness` on PATH | CLI backing the optional browser tool |

```bash
QWEN_API_BASE=http://other-host:1234/v1 ./boot.sh
```

## Tools

The agent has 7 tools at its disposal:

1. **read_file(path, [offset], [limit])** — read text files
2. **write_file(path, content)** — create/overwrite files (patch preferred for edits)
3. **patch(path, old_string, new_string)** — find-and-replace edits
4. **shell(command)** — run shell commands
5. **ask_user(question, [choices])** — asks you for input or a decision mid-task
6. **write_skill(name, content)** — saves a reusable skill file, immediately loaded
7. **load_skill(name)** — injects a skill's full content into the conversation

Optional: **browser(code)** — browser automation via [browser-harness](https://github.com/browser-use/browser-harness) (CDP over Chrome). It appears as an eighth tool only when the `browser-harness` CLI is on `PATH` (or `QWEN_BROWSER_CMD` is set). Without it, everything else works unchanged and the bundled browser skill is hidden.

## Features

### Session Persistence

- `/save <name>` — save current session to `sessions/<name>.json`
- `/list` — show saved sessions
- `/del <name>` — delete a saved session
- `/q` — auto-save and quit
- On startup, you can pick a saved session to resume

### Streaming

Model output streams to the terminal in real-time (grey text). On completion the stream is cleared and only the structured thought + tool call + result remains visible. Interrupt mid-stream with Ctrl-C or by typing a command.

### Interruptible Streaming

Type a `/command` or a new message *while* the model is still generating — the stream cancels immediately and your input is processed.

### Context Compression

When the estimated token count exceeds `CONTEXT_LENGTH × COMPRESSION_TRIGGER`, older conversation history is automatically summarized by the compression model into a single message. Manual compression: `/compress`.

### Skills System

Drop `.md` files into `skills/`. They're loaded into the system prompt wrapped as `<skill>...</skill>` blocks. The agent can create its own skills with `write_skill`. Commands: `/skills` (list), `/reload-skills` (re-read from disk).

### Colors

- Grey: streaming output and thoughts
- Green: tool calls and results
- Gold: final answers

### Signal Handling

- Ctrl-C during stream: cancels generation, saves session
- Ctrl-C at prompt: reminds you to use `/q`
- Double Ctrl-C anywhere: immediate force-exit

## Requirements

- Python 3.10+ — that's it. No `pip install` step; only standard-library modules are used (`urllib`, `json`, `subprocess`, `threading`, `signal`, …).
- Optional only: the `browser-harness` CLI for the browser tool. Nothing in the harness installs or requires it — it is detected on `PATH`.

Termux notes:

- `pkg install python` provides everything needed. No compiler, `rust`, or wheels are involved.
- `.venv` is optional. `install.sh` does not create one; `boot.sh` and `start.bat` use it automatically if it exists (e.g. `.venv/bin/python`), otherwise the system `python3`.
- For a model hosted on your PC, run the server on `0.0.0.0` (LM Studio: *Serve on Local Network*) and set `QWEN_API_BASE=http://<pc-lan-ip>:1234/v1`. `localhost` on the phone is the phone itself.

Windows notes:

- Run `install.bat` instead of `./install.sh`, then `start.bat` instead of `./boot.sh`.
- `install.bat` requires Python 3.10+ on PATH; there are no other install steps.
- The bat scripts are plain ASCII; run them from cmd. PowerShell works too: `.\install.bat`.
- Both bat scripts `pause` before the window closes (on success and on error), so messages stay readable when launched by double-click.

## Architecture

The harness is split into small single-purpose modules in the `qwen_agent/`
package so individual agents can work on one component at a time:

| Module | Responsibility |
|---|---|
| `qwen_agent/config.py` | ANSI colors, repo-relative paths, env config, stdlib LLM client, model detection |
| `qwen_agent/control.py` | Shared stream-cancellation events + SIGINT handling |
| `qwen_agent/prompt.py` | The system prompt (tool list built from what is available) |
| `qwen_agent/sessions.py` | Session save/list/load/delete/resume |
| `qwen_agent/skills.py` | Skill discovery, cache, content lookup |
| `qwen_agent/context.py` | Token estimation + history compression |
| `qwen_agent/parse.py` | Parsing raw model output into tool calls / answers |
| `qwen_agent/tools.py` | Dispatchable tools (read/write/patch/shell, optional browser) |
| `qwen_agent/interactive.py` | Main-thread tools (ask_user, write_skill, load_skill) |
| `qwen_agent/llm.py` | Streaming LLM chat with cancellation |
| `qwen_agent/main.py` | Interactive main loop + `/` commands |

`harness.py` is a thin launcher over `qwen_agent.main`. Run smoke tests with
`python test_smoke.py` (no pytest needed).

## Configuration

Use `/config` to display current settings, estimated token usage, and message count.

## Skills Directory

Skills are loaded from the repository's `skills/` directory (override with `QWEN_SKILLS_DIR`). The bundled `browser-harness.SKILL.md` comes from the upstream browser-harness project and is only advertised when that CLI is installed.
