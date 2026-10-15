"""The Settings tab (reading and writing .env), the panel's Stop / Restart, and the one-click launcher."""
from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

import app.main as main_module
import start
from app import settings_editor as editor
from app.config import Settings
from app.control import LAUNCHER_ENV, RESTART_CODE, browse_url, control
from app.outreach.manager import OutreachManager
from tests.test_outreach import FakeMailbox, make_settings


def fresh(env_file) -> Settings:
    return Settings(_env_file=env_file)


def field(view: dict, name: str) -> dict:
    return next(f for f in view["fields"] if f["name"] == name)


# --------------------------------------------------------------------------
# settings_editor
# --------------------------------------------------------------------------
def test_every_setting_is_on_the_settings_tab():
    names = [option.name for option in editor.OPTIONS]
    assert sorted(names) == sorted(Settings.model_fields) and len(names) == len(set(names))
    assert {option.group for option in editor.OPTIONS} == {group.key for group in editor.GROUPS}
    assert {option.name for option in editor.OPTIONS if option.restart} == {"HOST", "PORT", "DATA_DIR",
                                                                            "LOG_BUFFER_SIZE", "PREVIEW_SITES",
                                                                            "SITES_URL"}


@pytest.mark.parametrize("value", ["plain", "abcd efgh ijkl mnop", "1 Main St, Houston, TX 77002", "pa#ss", "a #b",
                                   "it's", 'say "hi"', "back\\slash\\", "C:\\Users\\me", " x", "two  spaces",
                                   "$HOME", "a\\'b"])
def test_values_read_back_exactly(value):
    assert dotenv_values(stream=io.StringIO(f"NAME={editor.env_line_value(value)}\n"))["NAME"] == value


def test_saving_keeps_the_rest_of_the_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# Mailbox\n# SMTP_HOST=smtp.gmail.com   # e.g.\nIMAP_HOST=old.example.com\nPORT=8000\n"
                        "port=8001\n\n# the end\n", encoding="utf-8")
    editor.write_env(env_file, {"SMTP_HOST": "smtp.gmail.com", "IMAP_HOST": "imap.gmail.com", "PORT": None,
                                "SENDER_NAME": "Pat O'Brien"})
    assert env_file.read_text(encoding="utf-8") == (
        "# Mailbox\n# SMTP_HOST=smtp.gmail.com   # e.g.\nSMTP_HOST=smtp.gmail.com\nIMAP_HOST=imap.gmail.com\n\n"
        "# the end\nSENDER_NAME='Pat O\\'Brien'\n")
    assert fresh(env_file).SENDER_NAME == "Pat O'Brien"


def test_a_new_env_file_starts_from_the_example(tmp_path, monkeypatch):
    template = tmp_path / ".env.example"
    template.write_text("# Server\nHOST=127.0.0.1\n# SMTP_HOST=smtp.gmail.com\n", encoding="utf-8")
    monkeypatch.setattr(editor, "TEMPLATE", template)
    editor.write_env(tmp_path / ".env", {"SMTP_HOST": "smtp.gmail.com"})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        "# Server\nHOST=127.0.0.1\n# SMTP_HOST=smtp.gmail.com\nSMTP_HOST=smtp.gmail.com\n")


def test_every_value_is_checked_and_nothing_is_written_when_one_is_wrong(tmp_path):
    env_file = tmp_path / ".env"
    with pytest.raises(editor.SettingsError) as err:
        editor.save(fresh(env_file), {
            "SMTP_PORT": "abc", "OUTREACH_TIMEZONE": "Mars/Base", "OUTREACH_DAYS": "someday", "OUTREACH_HOURS": "12-8",
            "WEB_SEARCH_ENGINE": "google", "SENDER_EMAIL": "nope", "OUTREACH_MIN_RATING": 7, "MAIL_PASSWORD": "a${b}",
            "NOPE": 1, "HEADLESS": "maybe", "REGION_CODE": "usa", "PROXY_SERVER": "host:3128", "SMTP_HOST": "a\nb",
            "IMAP_PORT": 0, "OUTREACH_DAILY_LIMIT": True,
        }, env_file)
    errors = err.value.errors
    assert set(errors) == {"SMTP_PORT", "OUTREACH_TIMEZONE", "OUTREACH_DAYS", "OUTREACH_HOURS", "WEB_SEARCH_ENGINE",
                           "SENDER_EMAIL", "OUTREACH_MIN_RATING", "MAIL_PASSWORD", "NOPE", "HEADLESS", "REGION_CODE",
                           "PROXY_SERVER", "SMTP_HOST", "IMAP_PORT", "OUTREACH_DAILY_LIMIT"}
    assert errors["SMTP_PORT"] == "must be a whole number" and errors["IMAP_PORT"] == "must be at least 1"
    assert errors["OUTREACH_MIN_RATING"] == "must be at most 5" and errors["WEB_SEARCH_ENGINE"] == "choose duckduckgo or bing"
    assert "America/Chicago" in errors["OUTREACH_TIMEZONE"] and errors["NOPE"] == "there is no such setting"
    assert not env_file.exists()
    assert str(err.value) == "15 settings need fixing"


def test_an_environment_variable_wins_so_it_cant_be_changed_here(tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.from-windows.com")
    env_file = tmp_path / ".env"
    with pytest.raises(editor.SettingsError, match="environment variable SMTP_HOST"):
        editor.save(fresh(env_file), {"SMTP_HOST": "smtp.gmail.com"}, env_file)
    view = editor.describe(fresh(env_file), env_file)
    assert field(view, "SMTP_HOST")["locked"] == "SMTP_HOST"
    assert field(view, "OUTREACH_AUTOSEND")["locked"] == "OUTREACH_AUTOSEND"      # set by tests/conftest.py


def test_saving_writes_env_and_uses_the_values_at_once(tmp_path):
    env_file = tmp_path / ".env"
    live = fresh(env_file)
    changed, restart = editor.save(live, {
        "SMTP_HOST": " smtp.gmail.com ", "SMTP_PORT": 465, "PORT": "8123", "SENDER_ADDRESS": "1 Main St, Houston, TX",
        "MAIL_PASSWORD": "abcd efgh ijkl mnop", "HEADLESS": False, "IMAP_FOLDER": "INBOX", "WEB_SEARCH_ENGINE": "Bing",
    }, env_file)
    assert set(changed) == {"SMTP_HOST", "SMTP_PORT", "PORT", "SENDER_ADDRESS", "MAIL_PASSWORD", "HEADLESS",
                            "WEB_SEARCH_ENGINE"}                               # INBOX already was
    assert restart == ["PORT"]
    assert (live.SMTP_HOST, live.SMTP_PORT, live.HEADLESS, live.WEB_SEARCH_ENGINE) == ("smtp.gmail.com", 465, False,
                                                                                       "bing")
    assert live.PORT == 8000 and fresh(env_file).PORT == 8123                   # the port after a restart
    assert fresh(env_file).MAIL_PASSWORD == "abcd efgh ijkl mnop"
    view = editor.describe(live, env_file)
    assert view["pending"] == ["PORT"] and field(view, "PORT")["saved"]
    password = field(view, "MAIL_PASSWORD")
    assert password["value"] is None and password["is_set"] and password["kind"] == "secret"
    assert "abcd efgh" not in json.dumps(view)                                  # passwords never leave the server
    # back to the default: the line goes
    changed, _ = editor.save(live, {"SMTP_PORT": None, "SENDER_ADDRESS": ""}, env_file)
    assert set(changed) == {"SMTP_PORT", "SENDER_ADDRESS"} and live.SMTP_PORT == 587 and live.SENDER_ADDRESS == ""
    assert "SMTP_PORT" not in env_file.read_text(encoding="utf-8")


def test_a_broken_env_file_is_reported_and_not_made_worse(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PORT=eighty\n", encoding="utf-8")
    live = Settings(_env_file=None)
    assert "PORT" in editor.describe(live, env_file)["file_error"]
    with pytest.raises(editor.SettingsError, match="PORT"):
        editor.save(live, {"SMTP_HOST": "smtp.gmail.com"}, env_file)
    assert env_file.read_text(encoding="utf-8") == "PORT=eighty\n"
    editor.save(live, {"PORT": 8001}, env_file)                                  # fixing it works
    assert fresh(env_file).PORT == 8001


# --------------------------------------------------------------------------
# the routes
# --------------------------------------------------------------------------
class FakeServer:
    should_exit = False


@pytest.fixture
def panel(tmp_path, monkeypatch):
    live = Settings(_env_file=None)
    env_file = tmp_path / ".env"
    monkeypatch.setattr(main_module, "settings", live)
    monkeypatch.setattr(main_module, "ENV_FILE", env_file)
    monkeypatch.setattr(main_module, "outreach", OutreachManager(make_settings(tmp_path), mailbox=FakeMailbox()))
    monkeypatch.setattr(control, "server", None)
    monkeypatch.setattr(control, "exit_code", 0)
    client = TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    return client, live, env_file


def test_settings_routes(panel):
    client, live, env_file = panel
    view = client.get("/api/settings").json()
    assert len(view["fields"]) == len(editor.OPTIONS) and view["groups"][0]["key"] == "mailbox"
    assert view["server"] == {"can_stop": False, "can_restart": False, "url": "http://127.0.0.1:8000",
                              "env_file": str(env_file)}
    saved = client.patch("/api/settings", json={"values": {"SMTP_HOST": "smtp.gmail.com", "PORT": 8123}})
    assert saved.status_code == 200 and saved.json()["changed"] == ["SMTP_HOST", "PORT"]
    assert saved.json()["restart"] == ["PORT"] and saved.json()["pending"] == ["PORT"]
    assert live.SMTP_HOST == "smtp.gmail.com" and "SMTP_HOST=smtp.gmail.com" in env_file.read_text(encoding="utf-8")
    bad = client.patch("/api/settings", json={"values": {"SMTP_PORT": "x"}})
    assert bad.status_code == 400
    assert bad.json()["detail"] == {"message": "SMTP port: must be a whole number",
                                    "fields": {"SMTP_PORT": "must be a whole number"}}


def test_settings_stop_and_restart_only_from_this_computer(panel):
    client, *_ = panel
    evil = {"Origin": "https://evil.example"}
    assert client.get("/api/settings", headers=evil).status_code == 403
    assert client.patch("/api/settings", headers=evil, json={"values": {"PORT": 1}}).status_code == 403
    assert client.post("/api/server/stop", headers=evil).status_code == 403
    assert client.get("/api/settings", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
    rebound = TestClient(main_module.app, base_url="http://attacker.example:8000", client=("127.0.0.1", 50000))
    assert rebound.get("/api/settings").status_code == 403                       # a DNS name pointing here
    network = TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("192.168.1.20", 50000))
    assert network.get("/api/settings").status_code == 403                       # HOST=0.0.0.0, another computer


def test_stop_and_restart(panel, monkeypatch):
    client, *_ = panel
    assert client.post("/api/server/stop").status_code == 409                    # not started by run.py
    server = FakeServer()
    monkeypatch.setattr(control, "server", server)
    assert client.get("/api/config").json()["server"]["can_stop"] is True
    monkeypatch.delenv(LAUNCHER_ENV, raising=False)
    assert client.post("/api/server/restart").status_code == 409                 # only start.py starts it again
    monkeypatch.setenv(LAUNCHER_ENV, "1")
    restarted = client.post("/api/server/restart")
    assert restarted.json() == {"restarting": True, "url": "http://127.0.0.1:8000"}
    assert server.should_exit and control.exit_code == RESTART_CODE
    server.should_exit = False
    assert client.post("/api/server/stop").json() == {"stopping": True}
    assert server.should_exit and control.exit_code == 0


def test_browse_url():
    assert browse_url("0.0.0.0", 8000) == "http://127.0.0.1:8000"
    assert browse_url("localhost", 8001) == "http://127.0.0.1:8001"
    assert browse_url("192.168.1.5", 80) == "http://192.168.1.5:80"
    assert browse_url("::1", 8000) == "http://[::1]:8000"


# --------------------------------------------------------------------------
# start.py - the one-click launcher
# --------------------------------------------------------------------------
def test_the_launcher_installs_only_when_requirements_change(tmp_path, monkeypatch):
    calls = []

    class FakeSubprocess:
        CalledProcessError = RuntimeError

        @staticmethod
        def run(command, check):
            calls.append(command[2:4])

    monkeypatch.setattr(start, "subprocess", FakeSubprocess)
    monkeypatch.setattr(start, "REQUIREMENTS", tmp_path / "requirements.txt")
    monkeypatch.setattr(start, "INSTALLED", tmp_path / "installed.txt")
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    start.install()
    assert calls == [["pip", "install"], ["playwright", "install"]]
    start.install()
    assert len(calls) == 2                                                       # nothing changed
    (tmp_path / "requirements.txt").write_text("fastapi\nhttpx\n", encoding="utf-8")
    start.install()
    start.install(force=True)
    assert len(calls) == 6


def test_the_launcher_finds_a_running_panel_and_stops_it():
    class Panel(BaseHTTPRequestHandler):
        def do_GET(self):
            self._answer(b'{"status": "ok", "version": "9.9"}')

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))   # unread, Windows resets the connection
            self.server.stop_asked = self.path == "/api/server/stop"
            self._answer(b'{"stopping": true}')

        def _answer(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Panel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        assert start.panel_version(url) == "9.9" and start.port_taken(url)
        assert start.request_stop(url) and server.stop_asked
    finally:
        server.shutdown()
        server.server_close()
    assert start.panel_version(url) is None and not start.port_taken(url)
    with start.ctrl_c_as_key() as pressed:                                       # no key pressed in a test run
        assert pressed() is False
