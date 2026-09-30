"""One-click start for the panel - what start.bat and the desktop shortcut run.

The first run creates the virtual environment (.venv), installs the Python packages and the Chromium
build Playwright drives - a few minutes - and puts a shortcut on the Windows desktop. Later runs only
install again when requirements.txt changes.
Then it starts the panel (run.py) and opens it in the browser. The window stays the panel's console: its
log lines show up there, Ctrl+C in it (or Stop in the panel) stops the panel, and the panel's Restart
button starts it again.

    python start.py [--host 127.0.0.1] [--port 8000] [--no-browser] [--reinstall] [--shortcut]

--shortcut puts a shortcut to start.bat on the Windows desktop. Only the standard library is used until
the packages are installed.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import __version__                                     # noqa: E402 - plain Python, no packages
from app.control import LAUNCHER_ENV, RESTART_CODE, browse_url   # noqa: E402

NAME = "Google Maps Lead Crawler"
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
INSTALLED = VENV / "installed-requirements.txt"   # requirements.txt as it was when last installed
ICON = ROOT / "app" / "static" / "icon.ico"
MIN_PYTHON = (3, 11)
WINDOWS = os.name == "nt"
STARTUP_S = 180                 # the browser opens once the panel answers - if it does within this

# never a proxy for the panel on this computer
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    os.chdir(ROOT)
    set_title(NAME)
    if sys.version_info < MIN_PYTHON:
        return fail(f"The panel needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer; this is "
                    f"{platform.python_version()} ({sys.executable}).\nInstall it from "
                    "https://www.python.org/downloads/ - tick \"Add python.exe to PATH\" - and start again.")
    if args.shortcut:
        return make_shortcut()
    if not in_venv():
        return relaunch_in_venv(argv)
    first = not INSTALLED.is_file()
    try:
        install(force=args.reinstall)
    except (subprocess.CalledProcessError, OSError):
        return fail("Installing failed - the messages above say why. Check the internet connection, "
                    "then start again.")
    if first and WINDOWS:
        make_shortcut()                                 # from now on: double-click it on the desktop
    return serve(args)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Set up (first time) and start the {NAME} panel")
    parser.add_argument("--host", help="the address to listen on (default: HOST in .env, 127.0.0.1)")
    parser.add_argument("--port", type=int, help="the port (default: PORT in .env, 8000)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the panel in the browser")
    parser.add_argument("--reinstall", action="store_true", help="install the packages and Chromium again")
    parser.add_argument("--shortcut", action="store_true", help="put a shortcut to start.bat on the desktop")
    return parser.parse_args(argv)


# ----------------------------------------------------------------------
# Setting up
# ----------------------------------------------------------------------
def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def in_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV.resolve()


def relaunch_in_venv(argv: list[str]) -> int:
    """Run this script again with the virtual environment's Python (making it first)."""
    python = venv_python()
    if not python.exists():
        print("Setting up for the first time - creating the virtual environment (.venv) ...", flush=True)
        try:
            subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        except (subprocess.CalledProcessError, OSError):
            return fail("Could not create the virtual environment - the messages above say why.")
    command = [str(python), str(ROOT / "start.py"), *argv]
    if not WINDOWS:
        os.execv(command[0], command)                  # replaces this process
    signal.signal(signal.SIGINT, signal.SIG_IGN)       # Ctrl+C is for the run we start to handle
    return subprocess.call(command)


def install(force: bool = False) -> None:
    """The packages and Chromium - when requirements.txt has changed since they were last installed."""
    wanted = REQUIREMENTS.read_text(encoding="utf-8")
    first = not INSTALLED.is_file()
    if not force and not first and INSTALLED.read_text(encoding="utf-8") == wanted:
        return
    print("Installing the Python packages" + (" - the first time takes a few minutes" if first else "") + " ...",
          flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                    *([] if first else ["--quiet"]), "-r", str(REQUIREMENTS)], check=True)
    print("Installing Chromium for Playwright ...", flush=True)
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    INSTALLED.write_text(wanted, encoding="utf-8")
    print("Everything is installed.\n", flush=True)


# ----------------------------------------------------------------------
# Running the panel
# ----------------------------------------------------------------------
def serve(args: argparse.Namespace) -> int:
    """Start the panel and start it again when its Restart button asks; returns when it has stopped."""
    from pydantic import ValidationError

    from app.config import Settings
    browser = not args.no_browser
    while True:
        try:
            cfg = Settings()
        except ValidationError as exc:
            return fail(f"The settings file has a value the panel can't use:\n{exc}\n\n"
                        f"Fix it in {ROOT / '.env'} (Notepad opens it) and start again.", edit=True)
        host, port = args.host or cfg.HOST, args.port or cfg.PORT
        url = browse_url(host, port)
        set_title(f"{NAME} - {url}")
        if panel_version(url):
            if "server" not in (panel_config(url) or {}):
                return fail(f"An older panel is running at {url} - one started before the one-click start "
                            "existed, which can't be stopped from here.\nClose its window (or press Ctrl+C in "
                            "it), then start again.")
            print(f"The panel is already running at {url} - opening it.", flush=True)
            if browser:
                webbrowser.open(url)
            return 0
        if port_taken(url):
            return fail(f"Another program uses port {port}. Close it, or give the panel another port: "
                        f"PORT=8001 in {ROOT / '.env'}, or start it with: start.bat --port 8001", edit=True)
        code = run_panel(host, port, url, open_browser=browser)
        browser = False                                 # an open page reconnects by itself
        if code == RESTART_CODE:
            print("\nRestarting the panel ...\n", flush=True)
            try:
                install()
            except (subprocess.CalledProcessError, OSError):
                return fail("Installing failed - the messages above say why.")
            continue
        if code == 0:
            print("\nThe panel is stopped.", flush=True)
            return 0
        print(f"\nThe panel stopped unexpectedly (exit code {code}) - the messages above say why.", flush=True)
        if not WINDOWS:
            return code
        key = ask("Press R to start it again, E to open the settings file (.env), or any other key to close.")
        if key == "e":
            edit_env()
            key = ask("Press R to start the panel again, or any other key to close.")
        if key != "r":
            return code


def run_panel(host: str, port: int, url: str, *, open_browser: bool) -> int:
    """Run run.py until it exits; returns its exit code."""
    command = [sys.executable, str(ROOT / "run.py"), "--host", host, "--port", str(port)]
    env = {**os.environ, LAUNCHER_ENV: "1", "PYTHONUNBUFFERED": "1"}
    # its own process group on Windows: Ctrl+C is for this script to pass on (see ctrl_c_as_key)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
    child = subprocess.Popen(command, cwd=ROOT, env=env, creationflags=flags)
    if not WINDOWS:
        signal.signal(signal.SIGINT, signal.SIG_IGN)   # the terminal sends Ctrl+C to the panel as well
    ready, started, stopping = False, time.monotonic(), False
    try:
        with ctrl_c_as_key() as pressed:
            while child.poll() is None:
                if not ready and time.monotonic() - started < STARTUP_S and panel_version(url):
                    ready = True
                    announce(url, open_browser)
                    if open_browser:
                        webbrowser.open(url)
                if pressed():
                    if stopping:
                        print("Stopping right away.", flush=True)
                        child.kill()
                    else:
                        stopping = True
                        print("\nStopping the panel ... (Ctrl+C again to stop it right away)", flush=True)
                        if not request_stop(url):
                            child.terminate()
                time.sleep(0.25)
    except KeyboardInterrupt:                           # Ctrl+C as a signal (no console to read keys from)
        if not request_stop(url):
            child.terminate()
    finally:
        if not WINDOWS:
            signal.signal(signal.SIGINT, signal.default_int_handler)
    return child.wait()


def announce(url: str, opened: bool) -> None:
    line = "-" * 72
    print(f"\n{line}\n  {NAME} {__version__} is running at {url}"
          f"{' - opened in your browser' if opened else ''}\n"
          "  Keep this window open while campaigns are sending.\n"
          "  To stop: Ctrl+C in this window, or the Stop button in the panel.\n"
          f"{line}\n", flush=True)


def panel_version(url: str) -> str | None:
    """The version of the panel answering at ``url`` - None when nothing (of ours) answers there."""
    try:
        with _OPENER.open(f"{url}/api/health", timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("version") if isinstance(data, dict) and data.get("status") == "ok" else None


def panel_config(url: str) -> dict | None:
    try:
        with _OPENER.open(f"{url}/api/config", timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def port_taken(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname, parts.port), timeout=1):
            return True
    except OSError:
        return False


def request_stop(url: str) -> bool:
    """What the panel's Stop button does: it finishes the email being sent, then exits."""
    request = urllib.request.Request(f"{url}/api/server/stop", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(request, timeout=5) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


# ----------------------------------------------------------------------
# The window
# ----------------------------------------------------------------------
_STD_INPUT_HANDLE = -10
_ENABLE_PROCESSED_INPUT = 0x0001     # Ctrl+C is a signal
_ENABLE_QUICK_EDIT_MODE = 0x0040     # a mouse click starts selecting text
_ENABLE_EXTENDED_FLAGS = 0x0080      # needed to change the one above


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]
    return kernel32


@contextmanager
def ctrl_c_as_key():
    """Windows: while the panel runs, read Ctrl+C as a key press instead of a signal.

    As a signal Ctrl+C also reaches cmd.exe running start.bat, which then asks "Terminate batch job
    (Y/N)?". And with QuickEdit on, a click in the window starts selecting text - and Windows pauses every
    program writing to a window while something is selected in it: the whole panel would hang, emails
    included. Yields a function that says whether Ctrl+C was pressed since it was last called.
    """
    if not WINDOWS:
        yield lambda: False
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = _kernel32()
    handle = kernel32.GetStdHandle(_STD_INPUT_HANDLE & 0xFFFFFFFF)
    mode = wintypes.DWORD()
    if not handle or not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        yield lambda: False                    # no console (input redirected): Ctrl+C stays a signal
        return
    kernel32.SetConsoleMode(handle, (mode.value | _ENABLE_EXTENDED_FLAGS)
                            & ~(_ENABLE_PROCESSED_INPUT | _ENABLE_QUICK_EDIT_MODE))

    def pressed() -> bool:
        hit = False
        while msvcrt.kbhit():
            hit = msvcrt.getwch() == "\x03" or hit
        return hit

    try:
        yield pressed
    finally:
        kernel32.SetConsoleMode(handle, mode.value)


def set_title(text: str) -> None:
    if WINDOWS:
        _kernel32().SetConsoleTitleW(text)


def ask(prompt: str) -> str:
    """Show ``prompt`` and return the key pressed, in lower case ('' when no key can be pressed)."""
    print(prompt, flush=True)
    if WINDOWS:
        import msvcrt
        try:
            key = msvcrt.getwch()
            if key in ("\x00", "\xe0"):                 # arrows, F-keys: a second code follows
                msvcrt.getwch()
                return ""
            return key.lower()
        except (KeyboardInterrupt, OSError):
            return ""
    try:
        return input().strip().lower()[:1]
    except (EOFError, KeyboardInterrupt):
        return ""


def fail(message: str, *, edit: bool = False) -> int:
    """Say what went wrong; on Windows keep the window open until a key is pressed."""
    print(f"\n{message}\n", flush=True)
    if WINDOWS:
        if edit and ask("Press E to open the settings file (.env), or any other key to close.") == "e":
            edit_env()
        elif not edit:
            ask("Press any key to close this window.")
    return 1


def edit_env() -> None:
    env = ROOT / ".env"
    if not env.exists() and (ROOT / ".env.example").is_file():
        env.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    if WINDOWS:
        subprocess.Popen(["notepad.exe", str(env)])
    else:
        print(f"The settings file: {env}")


def make_shortcut() -> int:
    """A desktop shortcut to start.bat, with the panel's icon. Through cmd.exe, so it can be pinned to the
    taskbar too (right-click > Pin to taskbar)."""
    if not WINDOWS:
        print("Desktop shortcuts are made on Windows. Elsewhere start the panel with: python3 start.py")
        return 1

    def ps(value: object) -> str:                    # a PowerShell '...' string
        return "'" + str(value).replace("'", "''") + "'"

    arguments = '/c ""' + str(ROOT / "start.bat") + '""'   # cmd.exe's quoting for a path with spaces
    script = "\n".join([
        "$shell = New-Object -ComObject WScript.Shell",
        "$path = Join-Path ([Environment]::GetFolderPath('Desktop')) " + ps(NAME + ".lnk"),
        "$link = $shell.CreateShortcut($path)",
        "$link.TargetPath = Join-Path $env:SystemRoot 'System32\\cmd.exe'",
        "$link.Arguments = " + ps(arguments),
        "$link.WorkingDirectory = " + ps(ROOT),
        "$link.IconLocation = " + ps(f"{ICON},0"),
        "$link.Description = " + ps(f"Start the {NAME} panel"),
        "$link.Save()",
        "Write-Output $path",
    ])
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-EncodedCommand", encoded], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Could not make the desktop shortcut: {exc}", flush=True)
        return 1
    if result.returncode != 0 or not result.stdout.strip():
        print(f"Could not make the desktop shortcut:\n{result.stderr.strip()}", flush=True)
        return 1
    print(f"Made the desktop shortcut {result.stdout.strip()} - double-click it to start the panel.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:                                   # noqa: BLE001 - show it, don't just close the window
        traceback.print_exc()
        sys.exit(fail("Something went wrong starting the panel - see above."))
