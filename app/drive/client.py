"""Google Drive over its REST API: signing in and the calls the crawler needs.

Signing in uses Google's OAuth flow for installed apps with PKCE: the panel
opens Google's consent page, Google sends the browser back to this server
(``http://127.0.0.1:<port>/?code=…``) and the code is exchanged for a refresh
token, kept in ``data/google_drive.json``. The OAuth client is yours - a
"Desktop app" client from the Google Cloud console (``GOOGLE_CLIENT_ID`` /
``GOOGLE_CLIENT_SECRET``). The scope is full Drive access: the crawler has to
write into folders you made and read lead files you put there yourself.

No Google libraries: plain httpx calls, tested with a fake transport
(tests/test_drive.py).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import time
from datetime import datetime, timezone
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive"
FOLDER_TYPE = "application/vnd.google-apps.folder"
SHEET_TYPE = "application/vnd.google-apps.spreadsheet"
TIMEOUT_S = 30
PENDING_S = 15 * 60                       # how long a sign-in link stays valid
_FILE_FIELDS = "id,name,mimeType,modifiedTime,size,webViewLink,parents"
_ID = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_FOLDER_URL = re.compile(r"/folders/([A-Za-z0-9_-]{20,})")


class DriveError(Exception):
    """A Drive call failed; the message says what Google answered (``status``: its HTTP status)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class DriveAuthError(DriveError):
    """Not signed in (any more): connect Google Drive again in the panel."""


@dataclass
class Token:
    refresh_token: str
    access_token: str = ""
    expires_at: float = 0.0
    email: str = ""


def folder_id_from(text: str) -> str:
    """A folder's id from its URL (…/folders/<id>) or the id itself; '' for a folder name / path."""
    text = text.strip()
    m = _FOLDER_URL.search(text)
    if m:
        return m.group(1)
    return text if _ID.match(text) and "/" not in text and " " not in text else ""


def _quote(value: str) -> str:
    """A string inside a Drive query."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


class DriveClient:
    def __init__(self, cfg: Settings, *, transport: httpx.AsyncBaseTransport | None = None,
                 token_file: Path | None = None, clock=time.time) -> None:
        self.on_lost: Callable[[], None] | None = None   # Google ended the sign-in (main.py emails you)
        self.cfg = cfg
        self.token_file = token_file or cfg.DATA_DIR / "google_drive.json"
        self.lost_file = self.token_file.with_name(self.token_file.stem + "_lost.json")
        self._transport = transport
        self._clock = clock
        self._pending: dict[str, tuple[str, str, float]] = {}   # state -> (code verifier, redirect uri, when)
        self.token: Token | None = self._load()
        self.lost_at: str = self._load_lost()      # when Google ended the sign-in ('' = it didn't) - the panel's banner

    # ------------------------------------------------------------------
    # Sign-in
    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.cfg.GOOGLE_CLIENT_ID.strip() and self.cfg.GOOGLE_CLIENT_SECRET.strip())

    @property
    def connected(self) -> bool:
        return self.token is not None and bool(self.token.refresh_token)

    @property
    def email(self) -> str:
        return self.token.email if self.token else ""

    def auth_url(self, redirect_uri: str) -> str:
        """Google's consent page; it sends the browser back to ``redirect_uri`` with a code."""
        if not self.configured:
            raise DriveError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set")
        now = self._clock()
        self._pending = {s: p for s, p in self._pending.items() if now - p[2] < PENDING_S}
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self._pending[state] = (verifier, redirect_uri, now)
        return AUTH_URL + "?" + urlencode({
            "client_id": self.cfg.GOOGLE_CLIENT_ID.strip(), "redirect_uri": redirect_uri, "response_type": "code",
            "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })

    def is_pending(self, state: str) -> bool:
        return state in self._pending

    def cancel_sign_in(self, state: str) -> None:
        self._pending.pop(state, None)

    async def finish_sign_in(self, code: str, state: str) -> str:
        """Exchange the code Google sent back for tokens; returns the Google account's email."""
        pending = self._pending.pop(state, None)
        if pending is None or self._clock() - pending[2] >= PENDING_S:
            raise DriveAuthError("the sign-in link has expired - press Connect Google Drive again")
        verifier, redirect_uri, _ = pending
        data = await self._token_call({
            "code": code, "client_id": self.cfg.GOOGLE_CLIENT_ID.strip(),
            "client_secret": self.cfg.GOOGLE_CLIENT_SECRET.strip(), "redirect_uri": redirect_uri,
            "grant_type": "authorization_code", "code_verifier": verifier,
        })
        if not data.get("refresh_token"):
            raise DriveAuthError("Google sent no refresh token - remove the app under Google Account › Security › "
                                 "Third-party access, then connect again")
        if SCOPE not in data.get("scope", SCOPE).split():
            raise DriveAuthError("Drive access was not granted - connect again and tick the Google Drive box")
        self.token = Token(refresh_token=data["refresh_token"], access_token=data.get("access_token", ""),
                           expires_at=self._clock() + float(data.get("expires_in", 3600)))
        about = (await self.request("GET", f"{API}/about", params={"fields": "user(emailAddress)"})).json()
        self.token.email = about.get("user", {}).get("emailAddress", "")
        self._save()
        self._set_lost("")
        log.info("Google Drive connected (%s)", self.token.email or "account without an email")
        return self.token.email

    def _load_lost(self) -> str:
        try:
            return str(json.loads(self.lost_file.read_text(encoding="utf-8")).get("at", ""))
        except (OSError, ValueError, AttributeError):
            return ""

    def _set_lost(self, at: str) -> None:
        self.lost_at = at
        with suppress(OSError):
            if at:
                self.lost_file.write_text(json.dumps({"at": at}), encoding="utf-8")
            else:
                self.lost_file.unlink(missing_ok=True)

    async def disconnect(self) -> None:
        """Forget the sign-in, and tell Google to revoke it (best effort)."""
        token, self.token = self.token, None
        with suppress(OSError):
            self.token_file.unlink(missing_ok=True)
        self._set_lost("")                          # you did it: no banner
        if token and token.refresh_token:
            with suppress(httpx.HTTPError):
                async with self._http() as http:
                    await http.post(REVOKE_URL, data={"token": token.refresh_token})
        log.info("Google Drive disconnected")

    async def _access_token(self, *, force: bool = False) -> str:
        if not self.connected:
            raise DriveAuthError("Google Drive is not connected")
        token = self.token
        assert token is not None
        if force or not token.access_token or self._clock() >= token.expires_at - 60:
            data = await self._token_call({
                "client_id": self.cfg.GOOGLE_CLIENT_ID.strip(), "client_secret": self.cfg.GOOGLE_CLIENT_SECRET.strip(),
                "refresh_token": token.refresh_token, "grant_type": "refresh_token",
            })
            token.access_token = data.get("access_token", "")
            token.expires_at = self._clock() + float(data.get("expires_in", 3600))
            self._save()
        return token.access_token

    async def _token_call(self, form: dict) -> dict:
        try:
            async with self._http() as http:
                response = await http.post(TOKEN_URL, data=form)
        except httpx.HTTPError as exc:
            raise DriveError(f"could not reach Google ({exc.__class__.__name__})") from exc
        data = _json(response)
        if response.status_code >= 400:
            error = data.get("error", "")
            if error == "invalid_grant":
                if form.get("grant_type") == "refresh_token":
                    self.token = None
                    with suppress(OSError):
                        self.token_file.unlink(missing_ok=True)
                    log.warning("Google Drive: Google ended the sign-in (expired or revoked) - connect it again")
                    self._set_lost(datetime.now(timezone.utc).isoformat(timespec="seconds"))
                    if self.on_lost is not None:
                        try:
                            self.on_lost()
                        except Exception:  # noqa: BLE001 - telling you must not break the Drive call's error
                            log.exception("Google Drive: could not report the lost sign-in")
                raise DriveAuthError("Google refused the sign-in (it expired or was revoked) - connect Google Drive "
                                     "again")
            raise DriveError(f"Google refused the sign-in: {error} {data.get('error_description', '')}".strip())
        return data

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------
    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT_S)

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """A Drive call with the access token; refreshed once when Google says it expired."""
        for attempt in (1, 2):
            token = await self._access_token(force=attempt == 2)
            headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
            try:
                async with self._http() as http:
                    response = await http.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                raise DriveError(f"could not reach Google Drive ({exc.__class__.__name__})") from exc
            if response.status_code == 401 and attempt == 1:
                kwargs["headers"] = {k: v for k, v in headers.items() if k != "Authorization"}
                continue
            if response.status_code >= 400:
                raise DriveError(_error_text(response), response.status_code)
            return response
        raise DriveAuthError("Google Drive refused the sign-in - connect it again")  # pragma: no cover

    # ------------------------------------------------------------------
    # Drive calls
    # ------------------------------------------------------------------
    async def get_file(self, file_id: str) -> dict:
        response = await self.request("GET", f"{API}/files/{file_id}",
                                      params={"fields": _FILE_FIELDS, "supportsAllDrives": "true"})
        return response.json()

    async def find(self, parent_id: str, name: str, *, folder: bool) -> dict | None:
        kind = f"mimeType = {_quote(FOLDER_TYPE)}" if folder else f"mimeType != {_quote(FOLDER_TYPE)}"
        query = f"{_quote(parent_id)} in parents and name = {_quote(name)} and {kind} and trashed = false"
        files = await self._list(query, order="createdTime")
        return files[0] if files else None

    async def folder(self, parent_id: str, name: str) -> dict:
        """The folder ``name`` in ``parent_id`` - made when it isn't there yet."""
        found = await self.find(parent_id, name, folder=True)
        if found:
            return found
        response = await self.request("POST", f"{API}/files",
                                      params={"fields": _FILE_FIELDS, "supportsAllDrives": "true"},
                                      json={"name": name, "mimeType": FOLDER_TYPE, "parents": [parent_id]})
        log.info("Google Drive: made the folder %r", name)
        return response.json()

    async def files(self, folder_id: str) -> list[dict]:
        """The files (not folders) in a folder, newest first."""
        query = f"{_quote(folder_id)} in parents and mimeType != {_quote(FOLDER_TYPE)} and trashed = false"
        return await self._list(query, order="modifiedTime desc")

    async def _list(self, query: str, *, order: str) -> list[dict]:
        files: list[dict] = []
        page = ""
        while True:
            params = {"q": query, "fields": f"nextPageToken,files({_FILE_FIELDS})", "pageSize": "200",
                      "orderBy": order, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if page:
                params["pageToken"] = page
            data = (await self.request("GET", f"{API}/files", params=params)).json()
            files += data.get("files", [])
            page = data.get("nextPageToken", "")
            if not page or len(files) >= 1000:
                return files

    async def upload(self, folder_id: str, name: str, content: bytes, *, mime: str = "text/csv",
                     file_id: str = "") -> dict:
        """Write a file: replace the content of ``file_id`` (or of the file with that name in the
        folder), else make a new one."""
        if not file_id:
            existing = await self.find(folder_id, name, folder=False)
            file_id = existing["id"] if existing else ""
        params = {"fields": _FILE_FIELDS, "supportsAllDrives": "true"}
        if file_id:
            try:
                response = await self.request("PATCH", f"{UPLOAD_API}/files/{file_id}",
                                              params={**params, "uploadType": "media"}, content=content,
                                              headers={"Content-Type": mime})
                return response.json()
            except DriveError as exc:
                if exc.status != 404:
                    raise                                 # 404: deleted in Drive - make a new one below
        boundary = "lead-crawler-" + secrets.token_hex(12)
        metadata = json.dumps({"name": name, "parents": [folder_id], "mimeType": mime}).encode("utf-8")
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + metadata
                + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode() + content
                + f"\r\n--{boundary}--\r\n".encode())
        response = await self.request("POST", f"{UPLOAD_API}/files", params={**params, "uploadType": "multipart"},
                                      content=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"})
        return response.json()

    async def download(self, file: dict) -> bytes:
        """A file's content; a Google Sheet comes as CSV (its first sheet)."""
        if file.get("mimeType") == SHEET_TYPE:
            response = await self.request("GET", f"{API}/files/{file['id']}/export", params={"mimeType": "text/csv"})
        else:
            response = await self.request("GET", f"{API}/files/{file['id']}",
                                          params={"alt": "media", "supportsAllDrives": "true"})
        return response.content

    # ------------------------------------------------------------------
    # The token file
    # ------------------------------------------------------------------
    def _load(self) -> Token | None:
        if not self.token_file.is_file():
            return None
        try:
            return Token(**json.loads(self.token_file.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:
            log.warning("unreadable %s (%s) - connect Google Drive again", self.token_file, exc)
            return None

    def _save(self) -> None:
        if self.token is None:
            return
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.token_file.parent, prefix=".tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(self.token)))
            os.replace(tmp, self.token_file)
        except Exception:
            with suppress(OSError):
                os.unlink(tmp)
            raise


def _json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error_text(response: httpx.Response) -> str:
    """'Google Drive: 403 insufficientPermissions - …' from an error answer."""
    data = _json(response).get("error", {})
    message = data.get("message", "") if isinstance(data, dict) else str(data)
    reason = ""
    if isinstance(data, dict) and data.get("errors"):
        reason = data["errors"][0].get("reason", "")
    return " ".join(f"Google Drive: {response.status_code} {reason} - {message or response.reason_phrase}".split())
