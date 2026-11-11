"""Start the web panel: ``python run.py`` then open http://127.0.0.1:8000.

start.bat (start.py) does this for you, after setting everything up, and opens the browser. The panel's
Stop button ends this process; its Restart button makes it exit with app.control.RESTART_CODE, and
start.py starts it again.
"""
from __future__ import annotations

import argparse
import sys

import uvicorn

from app.config import settings
from app.control import control


def main() -> int:
    parser = argparse.ArgumentParser(description="Leads Pilot Engine web panel")
    parser.add_argument("--host", default=settings.HOST)
    parser.add_argument("--port", type=int, default=settings.PORT)
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes (development)")
    parser.add_argument("--access-log", action="store_true",
                        help="log every HTTP request too (the panel asks every few seconds: many lines)")
    args = parser.parse_args()
    if args.reload:
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=True, log_level="info",
                    access_log=args.access_log, proxy_headers=False)
        return 0

    from app.main import app

    # proxy_headers=False: the request's client stays the one that connected. With uvicorn's default, a
    # Cloudflare Tunnel's X-Forwarded-For (it connects from 127.0.0.1) made it the visitor's address, and
    # Settings, Stop and Restart refused the cloud panel at TRUSTED_HOSTS (main._this_computer).
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="info",
                                           access_log=args.access_log, proxy_headers=False))
    control.server = server                    # the panel's Stop / Restart buttons
    control.owns_process = True                # ... and nothing left over keeps a stopped panel running
    try:
        server.run()                           # exits the process with 3 when it can't start (port taken ...)
    except KeyboardInterrupt:                  # uvicorn raises Ctrl+C again once it has shut down cleanly
        pass
    return control.exit_code


if __name__ == "__main__":
    sys.exit(main())
