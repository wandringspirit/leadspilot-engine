"""Stopping and restarting the server from the panel (its Stop and Restart buttons).

run.py hands its uvicorn server to ``control`` so the API can end it. start.py - the one-click launcher
behind start.bat and the desktop shortcut - runs run.py as a child process and starts it again when it
exits with RESTART_CODE: that is how Restart works, because settings like PORT are only read when a
process starts. Only the standard library is used here: start.py imports this before any package is
installed.
"""
from __future__ import annotations

import os
from typing import Protocol

RESTART_CODE = 75                          # run.py's exit code for "start me again" (uvicorn uses 3 for failures)
LAUNCHER_ENV = "LEAD_CRAWLER_LAUNCHER"     # start.py sets it to "1" for the server it runs


class _Server(Protocol):
    should_exit: bool


class ServerControl:
    def __init__(self) -> None:
        self.server: _Server | None = None     # set by run.py; None when started some other way
        self.exit_code = 0

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


def browse_url(host: str, port: int) -> str:
    """Where a browser on this computer opens a panel listening on host:port."""
    if host in ("", "0.0.0.0", "::", "localhost"):
        host = "127.0.0.1"
    elif ":" in host and not host.startswith("["):
        host = f"[{host}]"                         # an IPv6 address
    return f"http://{host}:{port}"


control = ServerControl()
