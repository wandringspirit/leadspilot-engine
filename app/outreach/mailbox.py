"""Your mailbox: SMTP to send the emails, IMAP to notice replies and bounces.

The blocking stdlib clients (``smtplib``, ``imaplib``) run in a worker thread.
Building a message and reading what arrived - who it is from, whether it is a
reply, an auto-reply, an unsubscribe or a bounce - are pure functions
(tests/test_outreach.py). The inbox is opened read-only: nothing is marked as
read, moved or deleted.
"""
from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import logging
import re
import smtplib
import socket
import ssl
from dataclasses import dataclass, field
from datetime import date, datetime
from email.message import EmailMessage, Message
from email.utils import formataddr, formatdate, make_msgid, parseaddr, parsedate_to_datetime
from typing import Mapping, Protocol

from app.config import Settings
from app.outreach.schedule import MONTHS

log = logging.getLogger(__name__)

TIMEOUT_S = 30
_MESSAGE_ID = re.compile(r"<[^<>\s]+>")
_GMAIL_APP_PASSWORD = re.compile(r"^[a-z]{4} [a-z]{4} [a-z]{4} [a-z]{4}$")
_BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail-daemon")
_BOUNCE_SUBJECTS = ("undeliverable", "undelivered mail", "delivery status notification", "mail delivery failed",
                    "delivery failure", "returned mail", "failure notice", "delivery has failed")
_DELAY_WORDS = ("(delay)", "delayed", "delivery delay", "will retry", "warning: message")
_AUTO_SUBJECTS = ("automatic reply", "auto reply", "auto-reply", "autoreply", "auto response", "autoresponse",
                  "out of office", "out of the office", "away from the office", "on vacation")
_UNSUBSCRIBE_SUBJECTS = {"unsubscribe", "stop", "remove", "remove me", "opt out", "optout"}


class MailError(Exception):
    """Sending or checking failed; worth another try later."""


class MailAuthError(MailError):
    """The mailbox refused the login."""


class MailRejected(MailError):
    """The server refused to send this email (spam filter, sending limit …) - stop sending."""


class RecipientRefused(MailError):
    """The server refused the recipient's address."""


class MailboxClient(Protocol):
    async def send(self, message: EmailMessage) -> None: ...
    async def fetch(self, since: date, limit: int = 500) -> list["MailboxMessage"]: ...


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def sender_address(cfg: Settings) -> str:
    return (cfg.SENDER_EMAIL or cfg.MAIL_USERNAME).strip()


def sender_header(cfg: Settings) -> str:
    """'Piyush Gaur <piyush@example.com>' (the From header)."""
    address = sender_address(cfg)
    return formataddr((cfg.SENDER_NAME.strip(), address)) if address else ""


def mail_password(cfg: Settings) -> str:
    """The password as the server wants it: Gmail app passwords are shown in groups ('abcd efgh …')."""
    password = cfg.MAIL_PASSWORD
    if "gmail" in cfg.SMTP_HOST.lower() and _GMAIL_APP_PASSWORD.match(password.strip()):
        return password.replace(" ", "")
    return password


def smtp_problems(cfg: Settings) -> list[str]:
    """What keeps the mailbox from sending ([] when it can)."""
    problems = []
    if not cfg.SMTP_HOST.strip():
        problems.append("SMTP_HOST is not set (e.g. smtp.gmail.com)")
    if not cfg.MAIL_USERNAME.strip() or not cfg.MAIL_PASSWORD:
        problems.append("MAIL_USERNAME / MAIL_PASSWORD are not set (Gmail: your address and an app password)")
    user = cfg.MAIL_USERNAME.strip()
    if user and "@" not in user and "google" in f"{cfg.SMTP_HOST} {cfg.IMAP_HOST}".replace("gmail", "google"):
        problems.append(f"MAIL_USERNAME {user!r} is not an email address - Gmail logs in with the account's own "
                        "address (not a send-as alias, not an app password's name)")
    address = sender_address(cfg)
    if address and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address):
        problems.append(f"SENDER_EMAIL {address!r} is not an email address")
    return problems


def imap_problems(cfg: Settings) -> list[str]:
    """What keeps the mailbox from being checked for replies ([] when it can)."""
    problems = []
    if not cfg.IMAP_HOST.strip():
        problems.append("IMAP_HOST is not set (e.g. imap.gmail.com) - without it replies can't be noticed, "
                        "so nothing is sent")
    if not cfg.MAIL_USERNAME.strip() or not cfg.MAIL_PASSWORD:
        problems.append("MAIL_USERNAME / MAIL_PASSWORD are not set (Gmail: your address and an app password)")
    return problems


def mailbox_problems(cfg: Settings) -> list[str]:
    """What keeps a campaign from running: sending, noticing replies, the address the law asks for."""
    problems = smtp_problems(cfg)
    problems += [p for p in imap_problems(cfg) if p not in problems]
    if not cfg.SENDER_ADDRESS.strip():
        problems.append("SENDER_ADDRESS is not set - US law (CAN-SPAM) wants your postal address in every email")
    if not cfg.SENDER_NAME.strip():
        problems.append("SENDER_NAME is not set - the name your emails are signed with")
    return problems


# --------------------------------------------------------------------------
# Building a message
# --------------------------------------------------------------------------
def new_message_id(sender: str) -> str:
    """'<…@yourdomain.com>' - on the sender's domain, like a mail program would make it."""
    domain = sender.rpartition("@")[2] or "localhost"
    return make_msgid(domain=domain)


def build_message(*, sender: str, to: str, subject: str, body: str, message_id: str,
                  earlier_ids: list[str] | None = None, unsubscribe: str = "") -> EmailMessage:
    """A plain-text email. ``earlier_ids`` (the Message-IDs sent to this lead before) make it a
    reply in the same thread; ``unsubscribe`` is the address a List-Unsubscribe click writes to."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = message_id
    if earlier_ids:
        message["In-Reply-To"] = earlier_ids[-1]
        message["References"] = " ".join(earlier_ids)
    if unsubscribe:
        message["List-Unsubscribe"] = f"<mailto:{unsubscribe}?subject=unsubscribe>"
    message.set_content(body)
    return message


# --------------------------------------------------------------------------
# Reading what arrived
# --------------------------------------------------------------------------
@dataclass
class MailboxMessage:
    """The parts of an incoming email that matter here."""
    sender: str = ""               # lower-case address
    subject: str = ""
    received_at: datetime | None = None
    message_id: str = ""
    refs: set[str] = field(default_factory=set)       # In-Reply-To + References
    auto: bool = False             # an automatic reply (out of office …)
    bounce: bool = False           # a delivery failure report
    failed: list[str] = field(default_factory=list)   # addresses a bounce names (X-Failed-Recipients)
    text: str = ""                 # start of the body - only fetched for bounces


def parse_headers(raw: bytes, text: str = "") -> MailboxMessage:
    """Read the headers of an incoming email (as IMAP returns them)."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = _address(_header(msg, "From"))
    subject = " ".join(_header(msg, "Subject").split())
    received = None
    try:
        received = parsedate_to_datetime(_header(msg, "Date")) if _header(msg, "Date") else None
    except (TypeError, ValueError, IndexError):
        received = None
    refs = set(_MESSAGE_ID.findall(_header(msg, "In-Reply-To") + " " + _header(msg, "References")))
    auto_submitted = _header(msg, "Auto-Submitted").lower()
    lowered = subject.lower()
    auto = ((auto_submitted not in ("", "no")) or bool(_header(msg, "X-Autoreply") or _header(msg, "X-Autorespond"))
            or _header(msg, "Precedence").lower() == "auto_reply" or lowered.startswith(_AUTO_SUBJECTS))
    local = sender.partition("@")[0]
    content_type = _header(msg, "Content-Type").lower().replace(" ", "").replace('"', "")
    bounce = (local.startswith(_BOUNCE_SENDERS) or "report-type=delivery-status" in content_type
              or (not sender and lowered.startswith(_BOUNCE_SUBJECTS)))
    if any(word in lowered for word in _DELAY_WORDS):
        bounce = False             # "still trying to deliver" - not a failure (yet)
    failed = [a.strip().lower() for a in _header(msg, "X-Failed-Recipients").split(",") if "@" in a]
    return MailboxMessage(sender=sender, subject=subject, received_at=received,
                          message_id=_first_id(_header(msg, "Message-ID")), refs=refs,
                          auto=auto and not bounce, bounce=bounce, failed=failed, text=text)


def classify(message: MailboxMessage, *, addresses: Mapping[str, str], message_ids: Mapping[str, str],
             own_address: str = "") -> tuple[str, str] | None:
    """(lead key, kind) for an email that concerns a lead, else None.

    ``addresses`` / ``message_ids`` map the leads' addresses (lower case) and the Message-IDs of
    the emails sent to them to a lead key. kind: ``bounce``, ``unsubscribe``, ``auto_reply`` or
    ``reply``. A reply is recognised by its sender's address, or - sent from another address - by
    answering one of our emails.
    """
    if message.bounce:
        for address in message.failed:
            if address in addresses:
                return addresses[address], "bounce"
        text = message.text.lower()
        for msg_id, key in message_ids.items():
            if msg_id.lower() in text:
                return key, "bounce"
        for address, key in addresses.items():
            if address in text:
                return key, "bounce"
        return None
    if own_address and message.sender == own_address.lower():
        return None
    key = addresses.get(message.sender) or next((message_ids[i] for i in message.refs if i in message_ids), None)
    if key is None:
        return None
    subject = re.sub(r"^(re|aw|fwd?)\s*:\s*", "", message.subject.strip().lower()).strip(" .!")
    if subject in _UNSUBSCRIBE_SUBJECTS:
        return key, "unsubscribe"
    return key, "auto_reply" if message.auto else "reply"


def imap_date(day: date) -> str:
    """IMAP's date format (English month names whatever the computer's language): '29-Sep-2026'."""
    return f"{day.day:02d}-{MONTHS[day.month - 1]}-{day.year}"


def _header(msg: Message, name: str) -> str:
    try:
        value = msg.get(name)
    except Exception:  # noqa: BLE001 - a malformed header counts as missing
        return ""
    return str(value) if value is not None else ""


def _address(value: str) -> str:
    return parseaddr(value)[1].strip().lower()


def _first_id(value: str) -> str:
    m = _MESSAGE_ID.search(value or "")
    return m.group(0) if m else (value or "").strip()


# --------------------------------------------------------------------------
# The real mailbox
# --------------------------------------------------------------------------
class Mailbox:
    """SMTP + IMAP with the MAIL_* / SMTP_* / IMAP_* settings."""

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send, message)

    async def fetch(self, since: date, limit: int = 500) -> list[MailboxMessage]:
        return await asyncio.to_thread(self._fetch, since, limit)

    # -- SMTP ---------------------------------------------------------------
    def _send(self, message: EmailMessage) -> None:
        cfg = self.cfg
        host, port = cfg.SMTP_HOST.strip(), cfg.SMTP_PORT
        context = ssl.create_default_context()
        try:
            server = (smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_S, context=context) if port == 465
                      else smtplib.SMTP(host, port, timeout=TIMEOUT_S))
            with server:
                if port != 465:
                    server.ehlo()
                    if server.has_extn("starttls"):
                        server.starttls(context=context)
                        server.ehlo()
                    elif host not in ("localhost", "127.0.0.1", "::1"):
                        raise MailError(f"{host}:{port} offers no encrypted connection (STARTTLS) - your password "
                                        "would travel in the clear; use port 587 or 465")
                server.login(cfg.MAIL_USERNAME.strip(), mail_password(cfg))
                server.send_message(message)
        except smtplib.SMTPAuthenticationError as exc:
            raise MailAuthError(f"the mailbox refused the login ({_smtp_text(exc)}) - check MAIL_USERNAME / "
                                "MAIL_PASSWORD; Gmail and Google Workspace need an app password") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            reasons = "; ".join(_smtp_text(e) for e in exc.recipients.values())
            raise RecipientRefused(f"the address was refused: {reasons}") from exc
        except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
            raise MailRejected(f"the mail server refused the email: {_smtp_text(exc)}") from exc
        except MailError:
            raise
        except socket.gaierror as exc:
            raise MailError(f"can't find the mail server {host} - check SMTP_HOST (e.g. smtp.gmail.com)") from exc
        except (TimeoutError, ConnectionError) as exc:
            raise MailError(f"could not reach {host}:{port} ({exc.__class__.__name__}) - check SMTP_HOST / "
                            "SMTP_PORT and the internet connection") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"{exc.__class__.__name__}: {exc}") from exc

    # -- IMAP ---------------------------------------------------------------
    def _fetch(self, since: date, limit: int) -> list[MailboxMessage]:
        cfg = self.cfg
        try:
            imap = imaplib.IMAP4_SSL(cfg.IMAP_HOST.strip(), cfg.IMAP_PORT,
                                     ssl_context=ssl.create_default_context(), timeout=TIMEOUT_S)
        except socket.gaierror as exc:
            raise MailError(f"can't find the mail server {cfg.IMAP_HOST} - check IMAP_HOST (e.g. imap.gmail.com)") \
                from exc
        except (OSError, imaplib.IMAP4.error) as exc:
            raise MailError(f"could not reach {cfg.IMAP_HOST}:{cfg.IMAP_PORT} ({exc.__class__.__name__}) - check "
                            "IMAP_HOST / IMAP_PORT and the internet connection") from exc
        try:
            try:
                imap.login(cfg.MAIL_USERNAME.strip(), mail_password(cfg))
            except imaplib.IMAP4.error as exc:
                raise MailAuthError(f"the mailbox refused the IMAP login ({_imap_text(exc)}) - check MAIL_USERNAME "
                                    "/ MAIL_PASSWORD, and that IMAP is on for the mailbox") from exc
            status, _ = imap.select(_quote(cfg.IMAP_FOLDER), readonly=True)
            if status != "OK":
                raise MailError(f"the mailbox has no folder {cfg.IMAP_FOLDER!r} (IMAP_FOLDER)")
            status, data = imap.search(None, "SINCE", imap_date(since))
            if status != "OK":
                raise MailError("the mailbox refused the search for new emails")
            numbers = (data[0] or b"").split()[-limit:]
            if not numbers:
                return []
            messages: dict[bytes, MailboxMessage] = {}
            status, data = imap.fetch(b",".join(numbers).decode(), "(BODY.PEEK[HEADER])")
            for number, raw in _fetched(data):
                try:
                    messages[number] = parse_headers(raw)
                except Exception as exc:  # noqa: BLE001 - one odd email must not stop the check
                    log.debug("could not read email %s: %s", number, exc)
            for number, message in messages.items():
                if message.bounce and not message.failed:
                    status, data = imap.fetch(number.decode(), "(BODY.PEEK[TEXT]<0.30000>)")
                    message.text = b"".join(raw for _, raw in _fetched(data)).decode("utf-8", "replace")
            return list(messages.values())
        except (OSError, imaplib.IMAP4.error) as exc:
            raise MailError(f"reading the mailbox failed: {_imap_text(exc)}") from exc
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass


def _fetched(data: list) -> list[tuple[bytes, bytes]]:
    """imaplib's FETCH answer -> [(message number, bytes)]."""
    out = []
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            out.append((item[0].split()[0], item[1]))
    return out


def _quote(folder: str) -> str:
    folder = folder.strip() or "INBOX"
    return f'"{folder}"' if " " in folder and not folder.startswith('"') else folder


def _smtp_text(exc: Exception) -> str:
    if isinstance(exc, tuple) and len(exc) >= 2:                   # recipients: {address: (code, message)}
        code, text = exc[0], exc[1]
    else:
        code, text = getattr(exc, "smtp_code", ""), getattr(exc, "smtp_error", str(exc))
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return " ".join(f"{code} {text}".split())


def _imap_text(exc: Exception) -> str:
    text = str(exc)
    m = re.match(r"^b['\"](.*)['\"]$", text)
    return m.group(1) if m else text
