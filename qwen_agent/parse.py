"""Parsing the model's raw response into a structured tool call / answer.

Supports five formats, tried in order:
1. Pure JSON (``{"thought", "tool"/"answer", ...}``)
2. A JSON object extracted from prose
3. XML tool_call blocks — legacy ``<tool_call><function=NAME><parameter=KEY>VAL</parameter>``
   and Qwen's mixed XML + JSON: ``<tool_call><function NAME>{ "param": "value" }</function></tool_call>``,
   where the JSON object following the ``<function>`` tag is parsed directly as the tool params.
   Unterminated ``<tool_call>`` / ``<function>`` / ``{ `` blocks are auto-closed.
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
    # Legacy:  <tool_call><function=NAME><parameter=KEY>VAL</parameter></function></tool_call>
    # Qwen mixed XML+JSON:
    #     <tool_call>
    #     <function shell>
    #     { "command": "ls D:\\QwenAgent\\workspace\\MediaManager" }
    #     </function>
    #     </tool_call>
    #   The raw JSON object after the <function> tag is parsed as the params.
    # Unterminated blocks are auto-closed: a missing </function>, </tool_call>
    # or a missing trailing '}' on the JSON object are tolerated (the parser
    # finishes them rather than failing).
    tc_start = re.search(r"<tool_call\s*>", text, re.IGNORECASE)
    if tc_start:
        inner_start = tc_start.end()
        tc_close = re.search(r"</tool_call\s*>", text[inner_start:], re.IGNORECASE)
        if tc_close:
            inner = text[inner_start:inner_start + tc_close.start()]
        else:
            inner = text[inner_start:]  # auto-close <tool_call>

        func_match = re.search(r"<function\b[^>]*>", inner, re.DOTALL | re.IGNORECASE)
        if func_match:
            tool_name = _function_name(func_match, inner)
            if tool_name:
                body = inner[func_match.end():]

                # Legacy format: <parameter=KEY>VAL</parameter> pairs
                params = {}
                for p_match in re.finditer(
                    r"<parameter[=>]\s*(\S+?)\s*>(.*?)</parameter>", body, re.DOTALL
                ):
                    params[p_match.group(1).strip()] = p_match.group(2).strip()

                # Qwen mixed format: the JSON object after <function> IS the params
                if not params:
                    json_text = _extract_json_object(body)
                    if json_text is not None:
                        parsed = None
                        try:
                            parsed = json.loads(json_text)
                        except json.JSONDecodeError:
                            # fallback: repair invalid escapes (e.g. \Q in
                            # Windows paths) — only reached when strict parse failed
                            try:
                                parsed = json.loads(_fix_invalid_escapes(json_text))
                            except json.JSONDecodeError:
                                parsed = None
                        if isinstance(parsed, dict):
                            params = parsed

                thought = text[:tc_start.start()].strip()
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


def _function_name(func_match, inner):
    """Extract the tool name from a ``<function ...>`` opening tag.

    Accepts ``<function=NAME>``, ``<function NAME>`` and ``<function>NAME</function>``.
    Returns None when no name can be found.
    """
    tag = func_match.group(0)
    m = re.search(r"[A-Za-z0-9_\-]+", tag[len("<function"):])
    if m:
        return m.group(0)
    m = re.search(r"<function>\s*([A-Za-z0-9_\-]+)\s*</function>", inner, re.DOTALL)
    return m.group(1) if m else None


def _extract_json_object(text):
    """Extract the first balanced ``{...}`` object from text (string-aware).

    Repairs an unterminated object — missing closing brace(s) are appended,
    and scanning stops at a stray closing XML tag (``</function>``,
    ``</tool_call>``) so truncated responses still parse.
    Returns None if no ``{`` is present.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escaped = False
    i = start
    while i < len(text):
        c = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
            elif depth > 0 and c == "<" and text.startswith("</", i):
                # Unterminated object cut off by a closing XML tag
                break
        i += 1
    # End of input (or cut off by a closing tag): auto-close the object
    return text[start:i] + "}" * depth


def _fix_invalid_escapes(s):
    """Repair invalid JSON string escapes (e.g. ``\\Q`` in Windows paths).

    Only ever called on text that already failed ``json.loads``. Valid JSON
    escapes (``\\ " / b f n r t u``) are left untouched, so well-formed JSON
    passes through unchanged.
    """
    valid = '"\\/bfnrtu'
    return re.sub(
        r"\\(.)",
        lambda m: "\\\\" + m.group(1) if m.group(1) not in valid else m.group(0),
        s,
    )