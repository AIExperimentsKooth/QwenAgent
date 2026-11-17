"""Context management: token estimation, threshold checks and history compression.

Compression uses the configured LLM client to summarize old conversation
history into a ``[Compressed history]`` block merged into the system
prompt (many backends reject multiple system messages).
"""

from . import config


def estimate_tokens(messages):
    """Rough token estimate: ~4 chars per token for text."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4 + 100
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += len(part["text"]) // 4
            total += 100
    return total


def should_compress(messages):
    """Return True if estimated token usage exceeds the trigger threshold."""
    if config.COMPRESSION_TRIGGER >= 1.0:
        return False
    used = estimate_tokens(messages)
    limit = int(config.CONTEXT_LENGTH * config.COMPRESSION_TRIGGER)
    return used > limit


def context_display(messages):
    """Return a short human-readable estimate of current context usage.

    Good-faith estimate based on estimate_tokens() (roughly 4 chars/token).
    Shown to the user before each input prompt so they can see how full
    the context window is.
    """
    used = estimate_tokens(messages)
    pct = (used / config.CONTEXT_LENGTH * 100) if config.CONTEXT_LENGTH else 0
    # Color the percentage: green < 50%, gold < 75%, red >= 75%
    if pct >= 75:
        color = "\033[31m"  # red
    elif pct >= 50:
        color = config.GOLD
    else:
        color = config.GREEN
    return f"{config.GREY}  [context ~{used:,}/{config.CONTEXT_LENGTH:,} tokens ({color}{pct:.0f}%{config.RESET}{config.GREY})]{config.RESET}"


def pick_compression_model():
    """Return the model to use for compression (user-configured or fallback)."""
    return config.COMPRESSION_MODEL or config.MODEL


def consolidate_system_msgs(messages):
    """Merge all system messages into one at the start.

    Many GGUF/llama.cpp backends reject multiple system messages or
    system messages appearing after user/assistant turns. This ensures
    exactly one system message at position 0.
    """
    sys_parts = []
    non_sys = []
    for m in messages:
        if m["role"] == "system":
            sys_parts.append(m["content"])
        else:
            non_sys.append(m)
    if len(sys_parts) <= 1:
        return messages
    merged = "\n\n".join(sys_parts)
    return [{"role": "system", "content": merged}] + non_sys


def compress_history(messages):
    """Compress the oldest conversation history into a summary.

    Merges the summary into the main system prompt rather than adding
    a separate system message (many backends enforce one system msg).
    """
    msgs = consolidate_system_msgs(messages)
    if len(msgs) < 4:
        return msgs

    c_model = pick_compression_model()
    print(f"{config.GREY}  compressing history ({c_model})...{config.RESET}", flush=True)

    system_msg = msgs[0] if msgs[0]["role"] == "system" else None
    non_system = [m for m in msgs if m["role"] != "system"]

    if len(non_system) < 2:
        return msgs

    keep_count = min(4, len(non_system))
    to_compress = non_system[:-keep_count]
    to_keep = non_system[-keep_count:]

    # Nothing to compress — return as-is
    if not to_compress:
        return msgs

    compressed_text = ""
    for m in to_compress:
        compressed_text += f"[{m['role']}]\n{m.get('content', '')}\n\n"

    # Use a single user message — no system message — to avoid template
    # issues with the compression model.
    summary_prompt = [
        {"role": "user", "content": f"Summarize this conversation history concisely. Keep all key facts, decisions, and context needed to continue the task. Output only the summary, no preamble.\n\n{compressed_text}"},
    ]

    try:
        summary = config.chat(
            summary_prompt,
            model=c_model,
            temperature=0.1,
            max_tokens=1024,
        ).strip()
        print(f"{config.GREY}  compressed {len(to_compress)} messages -> 1 summary{config.RESET}")
    except Exception as e:
        print(f"{config.GREY}  compression failed ({e}), keeping history{config.RESET}")
        return msgs

    # Merge summary into the existing system prompt, replacing any
    # previous [Compressed history] section so repeated compressions
    # don't keep appending and growing the system prompt indefinitely.
    base = system_msg["content"] if system_msg else ""
    # Strip any prior compressed-history block
    if "[Compressed history]" in base:
        base = base.split("[Compressed history]")[0].rstrip()
    new_sys = f"{base}\n\n[Compressed history]\n{summary}" if base else f"[Compressed history]\n{summary}"

    result = [{"role": "system", "content": new_sys}]
    result.extend(to_keep)
    return result