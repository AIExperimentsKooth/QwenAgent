"""Parsing the model's raw response into a structured tool call / answer.

Supports four formats, tried in order:
1. Pure JSON (``{"thought", "tool"/"answer", ...}``)
2. A JSON object extracted from prose
3. XML tool_call blocks (``<tool_call><function=NAME>...``)
4. Markdown-fenced JSON (`` ```json { ... } ``` ``)
"""

import json
import re


def parse_agent_response(text):
    """Return (thought, tool_name, params, answer) — exactly one of tool/answer set."""
    # ── Strategy 1: pure JSON ──────────────────────────────────────────
    try:
        obj = json.loads(text)
        thought = obj.get("thought", "")
        if "answer" in obj:
            return thought, None, None, str(obj["answer"])
        if "tool" in obj:
            params = obj.get("params")
            if params is None:
                # Model's chat template outputs flat keys, not nested params
                params = {k: v for k, v in obj.items() if k not in ("thought", "tool")}
            return thought, obj["tool"], params, None
    except json.JSONDecodeError:
        pass

    # ── Strategy 2: JSON block extracted from prose ────────────────────
    matches = re.findall(r"\{[^{}]*\}", text, re.DOTALL)
    for m in reversed(matches):
        try:
            obj = json.loads(m)
            thought = obj.get("thought", "")
            if "answer" in obj:
                return thought, None, None, str(obj["answer"])
            if "tool" in obj:
                params = obj.get("params")
                if params is None:
                    params = {k: v for k, v in obj.items() if k not in ("thought", "tool")}
                return thought, obj["tool"], params, None
        except json.JSONDecodeError:
            continue

    # ── Strategy 3: XML tool_call format ──────────────────────────────
    # Format: <tool_call><function=NAME><parameter=KEY>VAL</parameter></function></tool_call>
    tc_match = re.search(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL)
    if tc_match:
        inner = tc_match.group(1)
        # Extract function (after <function= or <function>)
        func_match = re.search(r"<function[=>]\s*(\S+?)\s*>", inner)
        if func_match:
            tool_name = func_match.group(1).strip()
            # Extract all parameters
            params = {}
            for p_match in re.finditer(
                r"<parameter[=>]\s*(\S+?)\s*>(.*?)</parameter>", inner, re.DOTALL
            ):
                key = p_match.group(1).strip()
                val = p_match.group(2).strip()
                params[key] = val
            # Everything before <tool_call> is the thought
            thought = text[:tc_match.start()].strip()
            return thought, tool_name, params, None

    # ── Strategy 4: markdown-fenced JSON ───────────────────────────────
    # ```json { ... } ```
    fence_match = re.search(
        r"```(?:json)?\s*\n?(\{.*?\})\n?\s*```", text, re.DOTALL
    )
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1))
            thought = obj.get("thought", "")
            if "answer" in obj:
                return thought, None, None, str(obj["answer"])
            if "tool" in obj:
                params = obj.get("params")
                if params is None:
                    params = {k: v for k, v in obj.items() if k not in ("thought", "tool")}
                return thought, obj["tool"], params, None
        except json.JSONDecodeError:
            pass

    return None, None, None, {
        "error": "Could not parse agent response as JSON or XML tool_call",
        "raw": text[:500],
    }