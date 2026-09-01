"""Streaming LLM chat: render model output live, support cancellation.

Uses the shared ``control`` events so the main loop can cancel a stream
(SIGINT, or the user typing mid-generation). Completed output is erased
from the terminal (leaving only the structured result); cancelled output
stays visible.
"""

import shutil
import sys

from . import config, context, control


def llm_chat(messages, label=""):
    """Stream model output to terminal. Returns (text, cancelled)."""
    cols = shutil.get_terminal_size().columns

    header = f"  {label}" if label else ""
    if not control._stream_stop.is_set():
        print(header)

    control._stream_stop.clear()
    control._stream_active.set()

    try:
        # Consolidate system messages — many GGUF backends reject
        # multiple system messages or mid-turn system messages.
        safe_msgs = context.consolidate_system_msgs(messages)
        stream = config.client.chat.completions.create(
            model=config.MODEL,
            messages=safe_msgs,
            temperature=0.1,
            max_tokens=config.MAX_TOKENS,
            stream=True,
        )
    except Exception as e:
        control._stream_active.clear()
        return f"  error: {e}", False

    parts = []
    try:
        for chunk in stream:
            if control._stream_stop.is_set():
                try:
                    stream.close()
                except Exception:
                    pass
                break
            token = chunk.choices[0].delta.content or ""
            parts.append(token)
            sys.stdout.write(f"{config.GREY}{token}{config.RESET}")
            sys.stdout.flush()
    except Exception as e:
        control._stream_active.clear()
        return f"  error: {e}", False

    control._stream_active.clear()
    cancelled = control._stream_stop.is_set()

    text = "".join(parts)
    if text and not text.endswith("\n"):
        print()

    if not cancelled:
        # Completed normally — clear the streaming output
        def terminal_rows(t):
            rows = 0
            for line in t.split("\n"):
                rows += max(1, (len(line) + cols - 1) // cols)
            return rows

        rows = 1 + terminal_rows(text)
        for _ in range(rows):
            sys.stdout.write("\033[2K\033[A")
        sys.stdout.write("\033[2K")
        sys.stdout.flush()
    else:
        # Cancelled — leave partial output visible
        sys.stdout.write(f"{config.GREY}  (cancelled){config.RESET}\n")
        sys.stdout.flush()

    return text.strip(), cancelled