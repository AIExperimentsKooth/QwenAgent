"""Stream/termination control: shared threading events and SIGINT handling.

These events are shared between the streaming LLM layer (``llm``), the
interactive main loop (``main``) and the OS signal handler, so they live
in their own module to avoid circular imports.
"""

import os
import signal
import sys
import threading

from . import config

_stream_stop = threading.Event()
_stream_active = threading.Event()
_sigint_count = 0


def handle_sigint(sig, frame):
    global _sigint_count
    _sigint_count += 1
    if _sigint_count >= 2:
        # Double CTRL-C: hard exit
        sys.stderr.write("\n  (force exit)\n")
        os._exit(1)
    if _stream_active.is_set():
        _stream_stop.set()
        sys.stderr.write(f"\n{config.GREY}  (cancelling stream...){config.RESET}\n")
    else:
        sys.stderr.write("\n  use /q to save and quit\n")


def setup_signal_handlers():
    """Install the SIGINT handler when the interactive session starts."""
    signal.signal(signal.SIGINT, handle_sigint)