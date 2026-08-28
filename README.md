# Qwen3.8-27B Agentic Harness

Minimal tool-calling loop over any OpenAI-compatible endpoint. Designed for running Qwen-style models locally, with live streaming, session persistence, context compression, and skills.

## Quick Start

```bash
./boot.sh
```

Auto-detects whatever model is loaded at `http://100.64.2.163:1234/v1` (no API key required). On startup you can resume a previous session or start fresh.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `QWEN_API_BASE` | `http://100.64.2.163:1234/v1` | OpenAI-compatible endpoint |
| `QWEN_MODEL` | auto-detect | Override model selection |
| `QWEN_API_KEY` | *(none)* | API key if required |
| `QWEN_CONTEXT_LENGTH` | `32768` | Model's max context (tokens) |
| `QWEN_COMPRESSION_MODEL` | *(same as MODEL)* | Separate model for compression |
| `QWEN_COMPRESSION_TRIGGER` | `0.75` | Compress when context is this % full |
| `QWEN_SKILLS_DIR` | `~/qwenAgent/skills` | Directory for .md skill files |

```bash
QWEN_API_BASE=http://other-host:1234/v1 ./boot.sh
```

## Tools

The agent has 7 tools at its disposal:

1. **read_file(path, [offset], [limit])** — read text files
2. **write_file(path, content)** — create/overwrite files (patch preferred for edits)
3. **patch(path, old_string, new_string)** — find-and-replace edits
4. **shell(command)** — run shell commands
5. **browser(code)** — browser automation via browser-harness (CDP over Chrome)
6. **ask_user(question, [choices])** — asks you for input or a decision mid-task
7. **write_skill(name, content)** — saves a reusable skill file, immediately loaded

## Features

### Session Persistence

- `/save <name>` — save current session to `~/qwenAgent/sessions/<name>.json`
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

Drop `.md` files into `~/qwenAgent/skills/`. They're loaded into the system prompt wrapped as `<skill>...</skill>` blocks. The agent can create its own skills with `write_skill`. Commands: `/skills` (list), `/reload-skills` (re-read from disk).

### Colors

- Grey: streaming output and thoughts
- Green: tool calls and results
- Gold: final answers

### Signal Handling

- Ctrl-C during stream: cancels generation, saves session
- Ctrl-C at prompt: reminds you to use `/q`
- Double Ctrl-C anywhere: immediate force-exit

## Requirements

- Python 3.10+
- `openai` package (installed in `.venv/`)
- browser-harness CLI: `uv tool install --python 3.12 --upgrade --force browser-harness`

## Configuration

Use `/config` to display current settings, estimated token usage, and message count.

## Skills Directory

Skills are loaded from `~/qwenAgent/skills/`. The bundled default is `browser-harness.SKILL.md` from the upstream browser-harness project.