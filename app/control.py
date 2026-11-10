"""Stopping and restarting the server from the panel (its Stop and Restart buttons).

run.py hands its uvicorn server to ``control`` so the API can end it. start.py - the one-click launcher
behind start.bat and the desktop shortcut - runs run.py as a child process and starts it again when it
exits with RESTART_CODE: that is how Restart works, because settings like PORT are only read when a
process starts. Only the standard library is used here: start.py imports this before any package is
installed.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Protocol

RESTART_CODE = 75                          # run.py's exit code for "start me again" (uvicorn uses 3 for failures)
EXIT_GRACE_S = 10.0                        # how long the process may linger after the app has shut down
LAUNCHER_ENV = "LEAD_CRAWLER_LAUNCHER"     # start.py sets it to "1" for the server it runs


class _Server(Protocol):
    should_exit: bool


class ServerControl:
    def __init__(self) -> None:
        self.server: _Server | None = None     # set by run.py; None when started some other way
        self.exit_code = 0
        self.owns_process = False              # run.py: this server is the whole process (exit_if_stuck)

    @property
    def can_stop(self) -> bool:
        return self.server is not None

    @property
    def can_restart(self) -> bool:
        """Only start.py starts the server again after it exits."""
        return self.can_stop and os.environ.get(LAUNCHER_ENV) == "1"

    def stop(self, *, restart: bool = False) -> None:
        if self.server is None:
            raise RuntimeError("the server was not started with run.py")
        self.exit_code = RESTART_CODE if restart else 0
        self.server.should_exit = True             # uvicorn finishes the open requests, then shuts down

    def exit_if_stuck(self, after: float = EXIT_GRACE_S) -> None:
        """Called once the app has shut down: end the process ``after`` seconds later if it still runs.

        Something left over (a browser, a thread) must never keep a stopped panel alive - start.py only
        starts it again once this process has ended. Does nothing unless run.py started the server (tests,
        ``--reload``).
        """
        if not self.owns_process:
            return

        def force() -> None:
            log = logging.getLogger(__name__)
            log.warning("Still running %.0fs after shutting down - ending the process", after)
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except Exception:  # noqa: BLE001 - exiting anyway
                    pass
            os._exit(self.exit_code)

        timer = threading.Timer(after, force)
        timer.daemon = True                        # a process that ends by itself doesn't wait for it
        timer.start()


def browse_url(host: str, port: int) -> str:
    """Where a browser on this computer opens a panel listening on host:port."""
    if host in ("", "0.0.0.0", "::", "localhost"):
        host = "127.0.0.1"
    elif ":" in host and not host.startswith("["):
        host = f"[{host}]"                         # an IPv6 address
    return f"http://{host}:{port}"


def keep_awake(on: bool) -> bool:
    """Ask Windows not to sleep while this process runs (the screen may still turn off); ``on=False`` lets it
    sleep again, as does the process ending. Call it from the same thread both times. False elsewhere."""
    if os.name != "nt":
        return False
    import ctypes
    es_continuous, es_system_required = 0x80000000, 0x00000001
    flags = es_continuous | (es_system_required if on else 0)
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))


control = ServerControl()
