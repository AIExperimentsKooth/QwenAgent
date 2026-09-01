"""Behavioral sanity checks for the refactored qwen_agent package.

Run inside the repo root with the project venv activated:

    .venv/bin/python -m pytest -q  (requires pytest)
    .venv/bin/python test_smoke.py  (no pytest needed)
"""

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from qwen_agent import parse  # noqa: E402
from qwen_agent import context  # noqa: E402
from qwen_agent import config  # noqa: E402
from qwen_agent.prompt import SYSTEM_PROMPT  # noqa: E402


def test_parse_json_tool_call():
    text = '{"thought": "check file", "tool": "read_file", "params": {"path": "/tmp/x"}}'
    thought, tool, params, answer = parse.parse_agent_response(text)
    assert tool == "read_file" and params == {"path": "/tmp/x"} and answer is None
    print("ok - parse json tool call")


def test_parse_xml_tool_call():
    text = 'I should look\n<tool_call><function=read_file><parameter=path>/etc/hostname</parameter></function></tool_call>'
    thought, tool, params, answer = parse.parse_agent_response(text)
    assert tool == "read_file" and params == {"path": "/etc/hostname"}
    assert "look" in thought
    print("ok - parse xml tool call")


def test_parse_answer():
    thought, tool, params, answer = parse.parse_agent_response('{"thought": "done", "answer": "All done."}')
    assert tool is None and answer == "All done."
    print("ok - parse answer")


def test_parse_unparseable():
    thought, tool, params, answer = parse.parse_agent_response("garbage")
    assert tool is None and isinstance(answer, dict) and "error" in answer
    print("ok - parse unparseable")


def test_context_display_and_compress_trigger():
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    display = context.context_display(msgs)
    assert "context" in display and "tokens" in display
    assert context.should_compress(msgs) is False
    print("ok - context display + trigger")


def test_consolidate_system_msgs():
    msgs = [
        {"role": "system", "content": "A"},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "B"},
    ]
    out = context.consolidate_system_msgs(msgs)
    assert out[0]["role"] == "system"
    assert out[0]["content"] == "A\n\nB"
    assert len(out) == 2
    print("ok - consolidate system msgs")


def test_sessions_roundtrip():
    from qwen_agent import sessions

    with tempfile.TemporaryDirectory() as tmp:
        old = config.SESSIONS_DIR
        config.SESSIONS_DIR = tmp
        try:
            sessions.save_session("t1", [{"role": "user", "content": "hello"}])
            loaded = sessions.load_session("t1")
            assert loaded and loaded[0]["content"] == "hello"
            listing = sessions.list_sessions()
            assert listing[0][0] == "t1"
            sessions.delete_session("t1")
            assert sessions.load_session("t1") is None
        finally:
            config.SESSIONS_DIR = old
    print("ok - sessions roundtrip")


def test_skills_roundtrip():
    from qwen_agent import skills

    with tempfile.TemporaryDirectory() as tmp:
        old = config.SKILLS_DIR
        config.SKILLS_DIR = tmp
        try:
            with open(os.path.join(tmp, "demo.md"), "w") as f:
                f.write("---\ndescription: a demo skill\n---\n# Demo\nBody.")
            cache, summary = skills.load_skills()
            assert len(cache) == 1 and cache[0][0] == "demo"
            assert "demo: a demo skill" in summary
            assert "Body." in skills.get_skill_content("demo")
        finally:
            config.SKILLS_DIR = old
            skills.SKILLS_CACHE = []
    print("ok - skills roundtrip")


def test_tools_dispatch():
    from qwen_agent import tools

    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "a.txt")
        r = json.loads(tools.tool_write_file(f, "hello world"))
        assert r["bytes_written"] == 11 and r["status"] == "ok"
        r = json.loads(tools.tool_read_file(f))
        assert r["content"] == "hello world" and r["total_lines"] == 1
        r = json.loads(tools.tool_patch(f, "hello", "goodbye"))
        assert r["replacements"] == 1
        assert json.loads(tools.tool_read_file(f))["content"] == "goodbye world"
        r = json.loads(tools.tool_patch(f, "not-there", "x"))
        assert "error" in r
    print("ok - tools dispatch")


def test_tool_map_complete():
    from qwen_agent import tools

    for name in ("read_file", "write_file", "patch", "shell", "browser", "ask_user", "write_skill", "load_skill"):
        assert name in tools.TOOL_MAP
    print("ok - tool map complete")


def test_system_prompt_sane():
    assert "read_file" in SYSTEM_PROMPT and "browser" in SYSTEM_PROMPT and "load_skill" in SYSTEM_PROMPT
    assert "## Protocol" in SYSTEM_PROMPT
    print("ok - system prompt")


if __name__ == "__main__":
    test_parse_json_tool_call()
    test_parse_xml_tool_call()
    test_parse_answer()
    test_parse_unparseable()
    test_context_display_and_compress_trigger()
    test_consolidate_system_msgs()
    test_sessions_roundtrip()
    test_skills_roundtrip()
    test_tools_dispatch()
    test_tool_map_complete()
    test_system_prompt_sane()
    print("\nALL SMOKE TESTS PASSED")