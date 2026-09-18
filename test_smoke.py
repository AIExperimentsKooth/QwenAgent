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


def test_parse_qwen_mixed_xml_json():
    # Qwen mixed form: <function NAME> followed by a raw JSON params object
    text = (
        'I should list the media dir\n'
        '<tool_call>\n'
        '<function shell>\n'
        '{\n'
        '  "command": "ls D:\\\\QwenAgent\\\\workspace\\\\MediaManager"\n'
        '}\n'
        '</function>\n'
        '</tool_call>'
    )
    thought, tool, params, answer = parse.parse_agent_response(text)
    assert tool == "shell"
    assert params == {"command": "ls D:\\QwenAgent\\workspace\\MediaManager"}
    assert "list" in thought and answer is None
    print("ok - parse qwen mixed xml+json")


def test_parse_qwen_mixed_xml_json_unclosed():
    # Model cut off mid-output: no </function>, no </tool_call>, no closing }
    text = 'Hmm, let me check\n<tool_call>\n<function shell>\n{"command": "ls"'
    thought, tool, params, answer = parse.parse_agent_response(text)
    assert tool == "shell"
    assert params == {"command": "ls"}
    print("ok - parse qwen mixed auto-close <>, {}")


def test_parse_qwen_mixed_nested_json():
    # Nested object, missing a closing brace, cut off by </function>
    text = '<tool_call><function shell>{"command": "python run.py", "env": {"mode": "prod"}</function>'
    thought, tool, params, answer = parse.parse_agent_response(text)
    assert tool == "shell"
    assert params == {"command": "python run.py", "env": {"mode": "prod"}}
    print("ok - parse qwen mixed nested json repair")


def test_parse_qwen_mixed_windows_path():
    # User's verbatim example: single-backslash Windows path (invalid JSON
    # escapes) — repaired by the fallback; note the JSON is also auto-closed
    bs = "\\"
    text = (
        '<tool_call>\n<function shell>\n{\n'
        '  "command": "ls D:' + bs + 'QwenAgent' + bs + 'workspace' + bs + 'MediaManager"\n'
        '}\n'
    )
    thought, tool, params, answer = parse.parse_agent_response(text)
    assert tool == "shell"
    assert params == {"command": "ls D:" + bs + "QwenAgent" + bs + "workspace" + bs + "MediaManager"}
    print("ok - parse qwen mixed windows path escape repair")


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


def test_system_diagnostics():
    from qwen_agent import prompt

    info = prompt.system_diagnostics()
    sysname = os.uname().sysname if hasattr(os, "uname") else ""
    expected_os = {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(sysname, sysname.lower())
    assert "## System" in info
    assert f"OS: {expected_os}" in info
    assert "Uptime: " in info and info.split("Uptime: ")[1].splitlines()[0] not in ("unknown", "")
    assert "Available RAM: " in info
    print("ok - system diagnostics block:\n" + info.replace("## ", "- "))


def test_startup_fresh_includes_diagnostics():
    from datetime import datetime

    from qwen_agent import main, skills

    with tempfile.TemporaryDirectory() as tmp:
        old = config.SESSIONS_DIR
        config.SESSIONS_DIR = tmp  # empty → pick_session() returns None → fresh start
        try:
            messages, _, session_file, session_title = main.startup_messages()
        finally:
            config.SESSIONS_DIR = old
            skills.SKILLS_CACHE = []
    assert messages[0]["role"] == "system"
    assert "## System" in messages[0]["content"]
    assert "OS: " in messages[0]["content"] and "Uptime: " in messages[0]["content"]
    assert session_title is None
    assert session_file == datetime.now().strftime("%Y%m%d_%H%M%S")
    print("ok - fresh startup includes system diagnostics")


def test_session_fixed_file_and_title():
    # A session maps to exactly ONE json file forever: saves keep the start
    # timestamp, update the title, and never create a new file.
    from qwen_agent import sessions

    with tempfile.TemporaryDirectory() as tmp:
        old = config.SESSIONS_DIR
        config.SESSIONS_DIR = tmp
        try:
            name = "20260101_091500"  # session started Jan 1, saved "now"
            sessions.save_session(name, [{"role": "user", "content": "first"}], title="Media manager")
            sessions.save_session(
                name,
                [{"role": "user", "content": "first"}, {"role": "assistant", "content": "done"}],
                title="Media manager v2",
            )
            files = os.listdir(tmp)
            assert files == ["20260101_091500.json"], f"expected exactly one file, got {files}"
            with open(os.path.join(tmp, files[0])) as f:
                data = json.load(f)
            assert data["title"] == "Media manager v2"
            assert data["messages"][-1] == {"role": "assistant", "content": "done"}

            # saving without a title keeps the existing title
            sessions.save_session(name, [{"role": "user", "content": "first"}])
            with open(os.path.join(tmp, files[0])) as f:
                assert json.load(f)["title"] == "Media manager v2"

            # a session started at an old date keeps that start stamp when saved now
            rn = "20250101_000000"
            with open(os.path.join(tmp, rn + ".json"), "w") as f:
                json.dump({"version": 1, "timestamp": "2025-01-01 00:00:00", "messages": []}, f)
            sessions.save_session(rn, [{"role": "user", "content": "resumed work"}])
            with open(os.path.join(tmp, rn + ".json")) as f:
                assert json.load(f)["timestamp"] == "2025-01-01 00:00:00"

            # list_sessions exposes the title for the resume dialog
            listing = sessions.list_sessions()
            assert ("20260101_091500", "Media manager v2") in [(n, t) for n, _, t, _ in listing]
        finally:
            config.SESSIONS_DIR = old
    print("ok - session fixed file + title")


def test_colors_enabled_on_posix():
    from qwen_agent import config

    if os.name == "nt":
        print("skipped - running on windows, styling intentionally off")
        return
    assert config.STYLING is True
    assert config.GREY and config.GREEN and config.GOLD and config.RED and config.RESET
    print("ok - colors enabled on posix")


def test_styling_disabled_on_windows():
    # Simulate a Windows host: every ANSI sequence must be gone from the
    # emitted output — colors blanked and context display escape-free.
    from unittest import mock

    import importlib

    from qwen_agent import config, context

    with mock.patch("os.name", "nt"):
        importlib.reload(config)
        try:
            assert config.STYLING is False
            assert config.GREY == config.GREEN == config.GOLD == config.RED == config.RESET == ""
            disp = context.context_display([{"role": "system", "content": "x"}])
            assert "\033" not in disp, disp
        finally:
            pass
    importlib.reload(config)  # restore real-platform constants after patch lifts
    assert config.STYLING == (os.name != "nt")
    print("ok - styling disabled on windows")


if __name__ == "__main__":
    test_parse_json_tool_call()
    test_parse_xml_tool_call()
    test_parse_qwen_mixed_xml_json()
    test_parse_qwen_mixed_xml_json_unclosed()
    test_parse_qwen_mixed_nested_json()
    test_parse_qwen_mixed_windows_path()
    test_parse_answer()
    test_parse_unparseable()
    test_context_display_and_compress_trigger()
    test_consolidate_system_msgs()
    test_sessions_roundtrip()
    test_skills_roundtrip()
    test_tools_dispatch()
    test_tool_map_complete()
    test_system_prompt_sane()
    test_system_diagnostics()
    test_startup_fresh_includes_diagnostics()
    test_session_fixed_file_and_title()
    test_colors_enabled_on_posix()
    test_styling_disabled_on_windows()
    print("\nALL SMOKE TESTS PASSED")