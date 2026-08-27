# Qwen3.8-27B Agentic Harness

Minimal tool-calling loop over any OpenAI-compatible endpoint.

## Usage

```bash
./boot.sh
```

The harness connects to `http://100.64.2.163:1234/v1` and auto-detects whatever model is loaded (no API key required).

Override via environment variables:

| Variable | Default | Description |
|---|---|---|
| `QWEN_API_BASE` | `http://100.64.2.163:1234/v1` | OpenAI-compatible endpoint |
| `QWEN_MODEL` | auto-detect | Override model selection |
| `QWEN_API_KEY` | *(none)* | API key if required |

```bash
QWEN_API_BASE=http://other-host:1234/v1 ./boot.sh
```

## Tools

- **read_file(path, [offset=1], [limit=2000])** — read text files
- **write_file(path, content)** — create/overwrite files (use patch for editing)
- **patch(path, old_string, new_string)** — find-and-replace edits (preferred for existing files)
- **shell(command)** — run shell commands
- **browser(code)** — browser automation via browser-harness (CDP over Chrome)

## Loop

1. You type a task
2. The agent thinks and calls a tool (or gives a final answer)
3. Tool result is fed back to the agent
4. Repeats until the agent answers or the round limit (12) is hit

## Requirements

- Python 3.10+
- `openai` package (installed in `.venv/`)
- `browser-harness` CLI (install: `uv tool install --python 3.12 --upgrade --force browser-harness`)
