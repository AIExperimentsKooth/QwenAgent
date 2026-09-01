"""Main interactive loop: startup, /commands, agent rounds, entry point.

The loop owns the conversation state (``messages``) and dispatches tool
calls to ``tools.TOOL_MAP``, with special handling for the interactive
tools in ``interactive`` and on-demand history compression.
"""

import json
import os
import queue
import select
import sys
import threading
from datetime import datetime

from . import (
    config,
    context,
    control,
    interactive,
    llm,
    parse,
    sessions,
    skills,
    tools,
)
from .prompt import SYSTEM_PROMPT


def startup_messages():
    """Build the initial conversation, resuming a session if the user picks one."""
    messages = sessions.pick_session()
    if messages is None:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Load skills — only summary goes into system prompt
        skills_list, skills_summary = skills.load_skills()
        if skills_summary:
            messages[0]["content"] += "\n\n" + skills_summary
            print(f"  {config.GREY}loaded {len(skills_list)} skill(s) from {config.SKILLS_DIR}{config.RESET}")
        print()
    else:
        if not any(m.get("role") == "system" for m in messages):
            messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        # Ensure skills summary is loaded even on resumed session
        skills_list, skills_summary = skills.load_skills()
        if skills_summary:
            sys_text = messages[0]["content"]
            if "Available Skills" not in sys_text:
                messages[0]["content"] += "\n\n" + skills_summary
                print(f"  {config.GREY}loaded {len(skills_list)} skill(s) from {config.SKILLS_DIR}{config.RESET}")

    return messages, skills_list


def default_session_name():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def main():
    # Ensure the default workspace directory exists and switch into it
    os.makedirs(config.WORKSPACE_DIR, exist_ok=True)
    os.chdir(config.WORKSPACE_DIR)

    control.setup_signal_handlers()

    config.MODEL = config.detect_model()

    # Check for saved sessions on startup
    messages, skills_list = startup_messages()

    print(f"Agent harness — model: {config.MODEL}")
    print(f"Endpoint: {config.BASE_URL}")
    print("Commands: /q, /save <name>, /list, /del <name>, /compress, /config, /skills, /reload-skills, /help")
    print(f"Skills dir: {config.SKILLS_DIR} ({len(skills_list)} loaded)\n")

    # List-wrapped so nested handle_cmd can rebind it
    session_name = [None]

    def handle_cmd(cmd):
        """Process a /command. Returns 'exit', 'continue', or None (not a command)."""
        c = cmd.lower()

        if c in ("/q", "/quit", "/exit"):
            if len(messages) > 1:
                name = session_name[0] or default_session_name()
                sessions.save_session(name, messages)
                print(f"  saved as '{name}', bye.")
            return "exit"

        if c == "/list":
            s = sessions.list_sessions()
            if not s:
                print("  no saved sessions")
            else:
                for i, (nm, ts, _) in enumerate(s):
                    print(f"  [{i}] {nm}  ({ts})")
            return "continue"

        if c.startswith("/del "):
            target = cmd[5:].strip()
            if os.path.isfile(sessions.session_path(target)):
                sessions.delete_session(target)
                print(f"  deleted '{target}'")
            else:
                print(f"  session '{target}' not found")
            return "continue"

        if c.startswith("/save"):
            parts = cmd.split(None, 1)
            name = parts[1].strip() if len(parts) > 1 else session_name[0] or default_session_name()
            session_name[0] = name
            sessions.save_session(name, messages)
            print(f"  saved as '{name}'")
            return "continue"

        if c.startswith("/compress"):
            before = len(messages)
            new_msgs = context.compress_history(messages)
            messages.clear()
            messages.extend(new_msgs)
            after = len(messages)
            print(f"  {config.GOLD}compressed: {before} -> {after} messages{config.RESET}")
            return "continue"

        if c.startswith("/config"):
            used = context.estimate_tokens(messages)
            limit = int(config.CONTEXT_LENGTH * config.COMPRESSION_TRIGGER)
            print(f"  model: {config.MODEL}")
            print(f"  context: {config.CONTEXT_LENGTH} tokens")
            print(f"  compression model: {context.pick_compression_model()}")
            print(f"  compression trigger: {int(config.COMPRESSION_TRIGGER*100)}% ({limit} tokens)")
            print(f"  estimated tokens: {used}")
            print(f"  messages: {len(messages)}")
            print(f"  endpoint: {config.BASE_URL}")
            return "continue"

        if c == "/skills":
            if not skills.SKILLS_CACHE:
                print("  no skills loaded")
            else:
                print(f"  Available skills ({len(skills.SKILLS_CACHE)}):")
                for i, (name, desc, _) in enumerate(skills.SKILLS_CACHE):
                    print(f"  [{i}] {config.GREEN}{name}{config.RESET}: {desc[:100]}")
                print(f"  [n] Cancel")
                try:
                    sel = input("  Inject a skill [n]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    return "continue"
                if sel.lower() in ("", "n"):
                    return "continue"
                try:
                    idx = int(sel)
                    if 0 <= idx < len(skills.SKILLS_CACHE):
                        name = skills.SKILLS_CACHE[idx][0]
                        body = skills.get_skill_content(name)
                        if body:
                            messages.append({"role": "user", "content": f"[Skill loaded: {name}]\n{body}"})
                            print(f"  {config.GOLD}injected skill: {name}{config.RESET}")
                except (ValueError, IndexError):
                    pass
            return "continue"

        if c == "/reload-skills":
            count = interactive.reload_skills_into_system(messages)
            print(f"  {config.GOLD}reloaded {count} skill(s){config.RESET}")
            return "continue"

        if c == "/help":
            print("  Commands: /q, /save <name>, /list, /del <name>, /compress, /config, /skills, /reload-skills, /help")
            print("  Tools: read_file, write_file, patch, shell, browser, ask_user, write_skill, load_skill")
            return "continue"

        return None  # not a command

    while True:
        # Display current context estimate before prompting
        sys.stdout.write(context.context_display(messages))
        sys.stdout.flush()
        try:
            user = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            if len(messages) > 1:
                name = session_name[0] or default_session_name()
                sessions.save_session(name, messages)
                print(f"  saved as '{name}', bye.")
            break

        result = handle_cmd(user)
        if result == "exit":
            break
        if result == "continue":
            continue
        # None → not a command, treat as user request

        messages.append({"role": "user", "content": user})

        # ── run agent rounds (with interruptible streaming) ────────────

        round_interrupted = False
        control._stream_stop.clear()

        for _round in range(1, config.MAX_TOOL_ROUNDS + 1):
            if round_interrupted:
                break

            # Start streaming in a background thread
            result_q = queue.Queue()

            def stream_worker(msgs, rnd, q):
                raw_text, was_cancelled = llm.llm_chat(msgs, label=f"round {rnd}")
                q.put((raw_text, was_cancelled))

            t = threading.Thread(target=stream_worker, args=(messages, _round, result_q), daemon=True)
            t.start()

            # Poll for user input while the model streams
            user_during_stream = None
            while t.is_alive():
                if control._sigint_count > 0:
                    control._stream_stop.set()
                    t.join(timeout=2)
                    # Save and return to prompt instead of exiting
                    if len(messages) > 1:
                        name = session_name[0] or default_session_name()
                        sessions.save_session(name, messages)
                        print(f"  saved as '{name}', back to prompt.")
                    control._sigint_count = 0
                    round_interrupted = True
                    break

                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                if ready:
                    line = sys.stdin.readline().strip()
                    # If it was just a stray newline from terminal, skip
                    if not line and not user_during_stream:
                        continue
                    user_during_stream = line
                    control._stream_stop.set()
                    t.join(timeout=2)
                    break

            # Collect the result
            try:
                raw, cancelled = result_q.get_nowait()
            except queue.Empty:
                raw, cancelled = "", False

            if cancelled and user_during_stream is not None:
                # User typed something mid-stream
                if user_during_stream.lower().startswith("/"):
                    # Process the command, then break back to prompt
                    round_interrupted = True
                    cmd_result = handle_cmd(user_during_stream)
                    if cmd_result == "exit":
                        return  # already saved inside handle_cmd
                    break
                else:
                    # New user request — inject as a fresh user message and
                    # let the next round continue with it
                    messages.append({"role": "user", "content": user_during_stream})
                    continue

            # ── normal post-stream processing (unchanged) ──────────────

            thought, tool_name, params, answer = parse.parse_agent_response(raw)

            if thought:
                print(f"{config.GREY}  thought: {thought[:300]}{config.RESET}")

            if answer is not None and isinstance(answer, str):
                print(f"\n{config.GOLD}  answer: {answer}{config.RESET}")
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": "If the task is complete, say so in your answer. If more work remains, continue with another tool call."})
                break

            if tool_name is None or params is None:
                err = answer or {"error": "Agent response format unrecognised"}
                print(f"{config.RED}  parse error: {err}{config.RESET}")
                messages.append({"role": "assistant", "content": raw})
                # Check if the response was truncated mid-JSON (no closing brace)
                stripped = raw.strip()
                if stripped and not stripped.endswith("}"):
                    messages.append({"role": "user", "content": "Your response was cut off. Continue from where you left off — finish the JSON."})
                else:
                    messages.append({"role": "user", "content": "Your response was not parseable. Try again."})
                continue

            if tool_name not in tools.TOOL_MAP:
                print(f"{config.RED}  unknown tool: {tool_name}{config.RESET}")
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Tool '{tool_name}' is not available. Available tools: {list(tools.TOOL_MAP.keys())}. Choose one."})
                continue

            # ask_user is interactive — handle from main thread
            if tool_name == "ask_user":
                print(f"{config.GREEN}  tool: ask_user({json.dumps(params)[:200]}){config.RESET}", flush=True)
                result = interactive.tool_ask_user_impl(**params)
                print(f"{config.GREEN}  result: {result[:500]}{config.RESET}", flush=True)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Tool result:\n{result}\n\nContinue. If the task is fully done, respond with an 'answer'. Otherwise make the next tool call."})
                if context.should_compress(messages):
                    messages = context.compress_history(messages)
                continue

            # write_skill — writes a .md file and reloads into system prompt
            if tool_name == "write_skill":
                print(f"{config.GREEN}  tool: write_skill({json.dumps(params)[:200]}){config.RESET}", flush=True)
                result = interactive.tool_write_skill_impl(**params, msgs=messages)
                print(f"{config.GREEN}  result: {result[:500]}{config.RESET}", flush=True)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Tool result:\n{result}\n\nContinue. If the task is fully done, respond with an 'answer'. Otherwise make the next tool call."})
                if context.should_compress(messages):
                    messages = context.compress_history(messages)
                continue

            # load_skill — injects full skill content into the conversation
            if tool_name == "load_skill":
                print(f"{config.GREEN}  tool: load_skill({json.dumps(params)[:200]}){config.RESET}", flush=True)
                result = interactive.tool_load_skill_impl(**params, msgs=messages)
                print(f"{config.GREEN}  result: {result[:500]}{config.RESET}", flush=True)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Tool result:\n{result}\n\nContinue. If the task is fully done, respond with an 'answer'. Otherwise make the next tool call."})
                continue

            print(f"{config.GREEN}  tool: {tool_name}({json.dumps(params)[:200]}){config.RESET}", flush=True)

            try:
                result = tools.TOOL_MAP[tool_name](**params)
            except Exception as e:
                result = json.dumps({"error": str(e)})

            # Color the result red if it contains an error
            if '"error"' in result:
                print(f"{config.RED}  result: {result[:500]}{config.RESET}", flush=True)
            else:
                print(f"{config.GREEN}  result: {result[:500]}{config.RESET}", flush=True)

            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"Tool result:\n{result}\n\nContinue. If the task is fully done, respond with an 'answer'. Otherwise make the next tool call."})

            # Auto-compress if context threshold exceeded
            if context.should_compress(messages):
                messages = context.compress_history(messages)
        else:
            print("  (max rounds reached)")
            messages.append({"role": "system", "content": "You have reached the maximum number of tool rounds. If the task is complete, give your final answer. If not, state what remains."})
            raw, _ = llm.llm_chat(messages)
            thought, _, _, answer = parse.parse_agent_response(raw)
            print(f"{config.GOLD}  final: {answer or raw[:500]}{config.RESET}")

        print()


if __name__ == "__main__":
    main()