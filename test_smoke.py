"""Behavioral sanity checks for the refactored qwen_agent package.

Run inside the repo root — standard library only, no pytest or venv needed:

    python test_smoke.py
    python -m pytest -q   (optional, if pytest is installed)
"""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

    for name in ("read_file", "write_file", "patch", "shell", "ask_user", "write_skill", "load_skill"):
        assert name in tools.TOOL_MAP
    assert ("browser" in tools.TOOL_MAP) == config.BROWSER_ENABLED
    print("ok - tool map complete")


def test_system_prompt_sane():
    assert "read_file" in SYSTEM_PROMPT and "load_skill" in SYSTEM_PROMPT
    assert "## Protocol" in SYSTEM_PROMPT
    print("ok - system prompt")


def test_prompt_matches_tools():
    # The prompt must only advertise tools the dispatcher actually has —
    # the count and the browser section track the environment.
    from qwen_agent import prompt, tools

    names = prompt.tool_names()
    assert set(names) == set(tools.TOOL_MAP), (names, list(tools.TOOL_MAP))
    assert f"access to {len(names)} tools" in SYSTEM_PROMPT
    assert ("browser(code)" in SYSTEM_PROMPT) == config.BROWSER_ENABLED
    print(f"ok - prompt advertises {len(names)} tools")


def test_browser_skill_hidden_without_cli():
    from qwen_agent import skills

    with tempfile.TemporaryDirectory() as tmp:
        old = config.SKILLS_DIR
        config.SKILLS_DIR = tmp
        try:
            # The bundled file is named browser-harness.SKILL.md — cover both
            # that and a plain browser-harness.md.
            for fname in ("browser-harness.SKILL.md", "browser-harness.md"):
                with open(os.path.join(tmp, fname), "w", encoding="utf-8") as f:
                    f.write("---\ndescription: browser skill\n---\n# Browser")
            cache, summary = skills.load_skills()
            if config.BROWSER_ENABLED:
                assert sorted(n for n, _, _ in cache) == ["browser-harness", "browser-harness.SKILL"]
            else:
                assert cache == [] and summary == ""
        finally:
            config.SKILLS_DIR = old
            skills.SKILLS_CACHE = []
    print("ok - optional browser skill gated on browser-harness")


def test_browser_tool_optional():
    from qwen_agent import config as cfg, tools

    if cfg.BROWSER_ENABLED:
        print("ok - browser tool enabled (browser-harness present)")
        return

    # Absent CLI: the tool must be unadvertised and report cleanly.
    assert "browser" not in tools.TOOL_MAP
    payload = json.loads(tools.tool_browser("print(1)"))
    assert "unavailable" in payload["error"], payload

    # Simulated install: the configured command is executed with the code
    # on stdin and its output is returned.
    old_cmd, old_enabled = cfg.BROWSER_CMD, cfg.BROWSER_ENABLED
    cfg.BROWSER_CMD, cfg.BROWSER_ENABLED = sys.executable, True
    try:
        payload = json.loads(tools.tool_browser('print("browser ok")'))
    finally:
        cfg.BROWSER_CMD, cfg.BROWSER_ENABLED = old_cmd, old_enabled
    assert payload["exit_code"] == 0 and "browser ok" in payload["output"], payload
    print("ok - optional browser tool degrades and runs when configured")


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


# ── stdlib LLM client (no SDK) ─────────────────────────────────────────

class _StubEndpoint(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible endpoint serving /v1/chat/completions."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if body.get("model") == "missing":
            payload = b'{"error": "model not loaded"}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for frame in (
                    b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
                    b": keep-alive\n\n",
                    b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
                    b"data: [DONE]\n\n",
                ):
                    self.wfile.write(frame)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError):
                pass  # the client closed mid-stream (cancellation test)
            return
        payload = json.dumps({"choices": [{"message": {"content": "hi there"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def test_stdlib_llm_client():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubEndpoint)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    old = config.BASE_URL
    config.BASE_URL = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        assert config.chat([{"role": "user", "content": "hi"}]) == "hi there"
        assert "".join(config.stream_chat([{"role": "user", "content": "hi"}])) == "Hello"

        # A cancelled stream must release its socket without hanging.
        gen = config.stream_chat([{"role": "user", "content": "hi"}])
        assert next(gen) == "Hel"
        gen.close()

        try:
            config.chat([{"role": "user", "content": "hi"}], model="missing")
        except RuntimeError as e:
            assert "HTTP 404" in str(e) and "model not loaded" in str(e), e
        else:
            raise AssertionError("expected an HTTP error")
    finally:
        config.BASE_URL = old
        server.shutdown()
        server.server_close()
    print("ok - stdlib chat + streaming SSE client")


def test_llm_client_unreachable_message():
    old = config.BASE_URL
    config.BASE_URL = "http://127.0.0.1:1/v1"
    try:
        try:
            config.chat([{"role": "user", "content": "hi"}])
        except RuntimeError as e:
            assert "cannot reach" in str(e), e
        else:
            raise AssertionError("expected a connection error")
    finally:
        config.BASE_URL = old
    print("ok - unreachable endpoint error is readable")


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
    test_prompt_matches_tools()
    test_browser_skill_hidden_without_cli()
    test_browser_tool_optional()
    test_stdlib_llm_client()
    test_llm_client_unreachable_message()
    print("\nALL SMOKE TESTS PASSED")