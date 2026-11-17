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
    """Stream model output to terminal. Returns (text, cancelled, error).

    ``error`` is None on success. A transport-level failure (endpoint down,
    HTTP error) is reported once instead of being fed back to the model as
    an unparseable answer, so the loop returns straight to the prompt.
    """
    cols = shutil.get_terminal_size().columns

    header = f"  {label}" if label else ""
    if not control._stream_stop.is_set():
        print(header)

    control._stream_stop.clear()
    control._stream_active.set()

    # Consolidate system messages — many GGUF backends reject
    # multiple system messages or mid-turn system messages.
    safe_msgs = context.consolidate_system_msgs(messages)
    parts = []
    error = None
    stream = None
    try:
        stream = config.stream_chat(
            safe_msgs,
            model=config.MODEL,
            temperature=0.1,
            max_tokens=config.MAX_TOKENS,
        )
        for token in stream:
            if control._stream_stop.is_set():
                break
            parts.append(token)
            sys.stdout.write(f"{config.GREY}{token}{config.RESET}")
            sys.stdout.flush()
    except Exception as e:
        error = str(e) or e.__class__.__name__
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        control._stream_active.clear()

    cancelled = control._stream_stop.is_set()

    text = "".join(parts)
    if text and not text.endswith("\n"):
        print()

    if cancelled:
        # Cancelled — leave partial output visible
        sys.stdout.write(f"{config.GREY}  (cancelled){config.RESET}\n")
        sys.stdout.flush()
    elif error is None:
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

    return text.strip(), cancelled, error
