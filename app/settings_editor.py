"""The panel's Settings tab: every setting with its group, label and help - read from and saved to .env.

Saving rewrites only the lines of the settings that changed (comments and the order of the lines stay;
a new .env starts as a copy of .env.example) and puts the new values into the running server's
``settings`` object, so they apply at once. The RESTART ones are only read when the server starts. A
setting that is also an environment variable of the computer can't be changed here: the environment
variable wins over .env.

tests/test_settings.py checks that every setting in app/config.py is listed in OPTIONS.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.config import PROJECT_ROOT, Settings
from app.outreach.schedule import WindowError, parse_days, parse_hours

TEMPLATE = PROJECT_ROOT / ".env.example"          # what a new .env starts from


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    help: str = ""
    advanced: bool = False                         # the panel shows it folded


@dataclass(frozen=True)
class Option:
    name: str
    group: str
    label: str
    help: str = ""
    placeholder: str = ""
    secret: bool = False             # never sent to the browser; leaving the box empty keeps it
    restart: bool = False            # only read when the server starts
    needed: str = ""                 # "email" / "drive": what can't work while it's empty
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None


GROUPS = (
    Group("mailbox", "Mailbox",
          "The email account campaigns send from and read replies in. Gmail or Google Workspace: turn on 2-Step "
          "Verification, then create an app password (Google Account › Security › App passwords) and use it as "
          "the password."),
    Group("sender", "You, in the emails",
          "The From line, and what {SenderName}, {SenderCompany} and {SenderAddress} fill in."),
    Group("sending", "When emails go out",
          "Campaigns only send inside this window, a few minutes apart, and never more than the daily limit."),
    Group("sites", "Preview websites",
          "Every business a campaign emails gets a preview website, and its first email waits until the website "
          "is online. Needs Node.js and a Cloudflare login - docs/SITES.md."),
    Group("drive", "Google Drive",
          "An OAuth client of type Desktop app from the Google Cloud console – docs/PLAYBOOK.md step 2 shows how. "
          "After saving, press Connect Google Drive in the Outreach tab."),
    Group("search", "Searching Google Maps"),
    Group("enrich", "Finding emails and phones"),
    Group("server", "Panel", "These only take effect after a restart."),
    Group("advanced", "Advanced scraping",
          "Timings and limits of the browser that reads Google Maps. The defaults work – change them to debug.",
          advanced=True),
)

OPTIONS = (
    # --- mailbox ---
    Option("SMTP_HOST", "mailbox", "Outgoing mail server (SMTP)", "Nothing can be sent without it",
           "smtp.gmail.com", needed="email"),
    Option("SMTP_PORT", "mailbox", "SMTP port", "587 (STARTTLS) or 465 (SSL)", minimum=1, maximum=65535),
    Option("IMAP_HOST", "mailbox", "Incoming mail server (IMAP)",
           "Replies and bounces are read here – without it nothing is sent", "imap.gmail.com", needed="email"),
    Option("IMAP_PORT", "mailbox", "IMAP port", "Usually 993", minimum=1, maximum=65535),
    Option("IMAP_FOLDER", "mailbox", "Folder replies arrive in", "Usually INBOX"),
    Option("MAIL_USERNAME", "mailbox", "Username", "Your mailbox login – usually the email address",
           "you@yourdomain.com", needed="email"),
    Option("MAIL_PASSWORD", "mailbox", "Password",
           "Gmail / Google Workspace: the 16-letter app password, not your normal password",
           secret=True, needed="email"),
    # --- sender ---
    Option("SENDER_NAME", "sender", "Your name", "The From name, and {SenderName}", "Your Name", needed="email"),
    Option("SENDER_EMAIL", "sender", "From address", "Empty: the username above", "you@yourdomain.com"),
    Option("SENDER_COMPANY", "sender", "Company", "{SenderCompany} – optional", "Your Company"),
    Option("SENDER_ADDRESS", "sender", "Postal address",
           "{SenderAddress}, in every email's footer – US law (CAN-SPAM) wants it", "Street, City, State ZIP",
           needed="email"),
    # --- sending ---
    Option("OUTREACH_AUTOSEND", "sending", "Send automatically",
           "Off: the server never sends on its own – campaigns wait"),
    Option("OUTREACH_TIMEZONE", "sending", "Time zone",
           "Your leads' time zone: America/Chicago (Houston, Dallas), America/New_York, America/Denver, "
           "America/Los_Angeles …", "America/Chicago"),
    Option("OUTREACH_DAYS", "sending", "Days", "e.g. mon-fri, or mon,wed,fri", "mon-fri"),
    Option("OUTREACH_HOURS", "sending", "Hours", "e.g. 8-12 = from 08:00 to 11:59", "8-12"),
    Option("OUTREACH_DAILY_LIMIT", "sending", "Emails per day", "All campaigns and follow-ups together", minimum=1),
    Option("OUTREACH_MIN_GAP_S", "sending", "Pause between two emails – at least (seconds)", minimum=0),
    Option("OUTREACH_MAX_GAP_S", "sending", "… and at most (seconds)", minimum=0),
    Option("OUTREACH_CHECK_MINUTES", "sending", "Look for replies every (minutes)",
           "In the send window – outside it once an hour", minimum=1),
    Option("OUTREACH_MIN_REVIEWS", "sending", "{Rating} and {Reviews} – at least this many reviews",
           "Fewer: the email uses the sentence without them", minimum=0),
    Option("OUTREACH_MIN_RATING", "sending", "… and at least this rating", minimum=0, maximum=5),
    Option("OUTREACH_TEST_COPY_TO", "sending", "Test copy to",
           "Your own addresses, comma-separated: each day's first email also goes to them, word for word, so "
           "you can see which tab it lands in (Primary, Promotions, Spam)", "you@gmail.com, you@yahoo.com"),
    Option("OUTREACH_UNSUBSCRIBE_HEADER", "sending", "Gmail's unsubscribe link",
           "A List-Unsubscribe header on campaign emails. Off: they read as one-to-one emails – the footer "
           "says to reply \"no\""),
    # --- sites ---
    Option("PREVIEW_SITES", "sites", "Build a website for each business",
           "Off: campaigns email without one – use emails without {Website}", restart=True),
    Option("SITES_URL", "sites", "Where the websites are online",
           "Each business's website is this address / its name", "https://sites.leadspilot.co", restart=True),
    Option("SITES_VISITS_KEY", "sites", "Website visits key",
           "The websites' VISITS_KEY secret – with it the panel shows who opened their website "
           "(python sites/deploy.py --visits-key sets both)", secret=True, restart=True),
    # --- drive ---
    Option("GOOGLE_CLIENT_ID", "drive", "Client ID", "", "1234567890-abc.apps.googleusercontent.com", needed="drive"),
    Option("GOOGLE_CLIENT_SECRET", "drive", "Client secret", "", "GOCSPX-…", secret=True, needed="drive"),
    Option("DRIVE_FOLDER", "drive", "Folder",
           "Where the business-type folders go: a folder name or path in My Drive, or a folder's link. "
           "Empty: My Drive itself", "Leads"),
    Option("DRIVE_AUTO_UPLOAD", "drive", "Upload automatically",
           "Each search's CSV when it finishes, and campaign results while they run"),
    # --- search ---
    Option("HEADLESS", "search", "Hide the browser", "Off: watch Chromium work through Google Maps (debugging)"),
    Option("REGION_CODE", "search", "Country bias", "Google's gl parameter, e.g. us – empty: none", "us"),
    Option("MAX_RESULTS", "search", "Most results per search", minimum=1),
    Option("MAX_SCANNED", "search", "Most listings read per search",
           "While looking for businesses that match the filters", minimum=1),
    Option("MAX_SECTIONS", "search", "Most map sections per search",
           "Whole-area searches: every section is one Google search", minimum=1),
    Option("DETAIL_CONCURRENCY", "search", "Place pages at once",
           "More is faster, and more likely to be blocked", minimum=1, maximum=10),
    Option("PROXY_SERVER", "search", "Proxy", "e.g. http://user:pass@host:3128 – empty: none",
           "http://host:3128", secret=True),
    # --- enrich ---
    Option("ENRICH_MAX_PAGES", "enrich", "Pages read per website", minimum=1),
    Option("ENRICH_CONCURRENCY", "enrich", "Websites at once", minimum=1),
    Option("ENRICH_BROWSER_FALLBACK", "enrich", "Open JavaScript-heavy sites in Chromium", "Slower, finds more"),
    Option("DEFAULT_REGION", "enrich", "Country of phone numbers",
           "For numbers without a country code, e.g. US", "US"),
    Option("WEB_SEARCH_ENGINE", "enrich", "Web search engine",
           "Finds websites a business doesn't list on Maps; the other engine is the fallback",
           choices=("duckduckgo", "bing")),
    Option("WEB_SEARCH_DELAY_S", "enrich", "Seconds between two web searches", minimum=0),
    # --- server ---
    Option("HOST", "server", "Address",
           "127.0.0.1: only this computer can open the panel. 0.0.0.0: every computer on your network can – "
           "the panel has no password", "127.0.0.1", restart=True),
    Option("PORT", "server", "Port", "The panel's address: http://127.0.0.1:<port>", restart=True,
           minimum=1, maximum=65535),
    Option("DATA_DIR", "server", "Data folder",
           "Searches, campaigns and the Google Drive sign-in are kept here – move them yourself when you "
           "change it", restart=True),
    Option("LOG_BUFFER_SIZE", "server", "Log lines kept for Live logs", restart=True, minimum=100),
    Option("KEEP_AWAKE", "server", "Keep the computer awake",
           "Windows doesn't go to sleep while the panel runs, so emails and the autopilot keep going – the "
           "screen still turns off", restart=True),
    # --- advanced ---
    Option("LOCALE", "advanced", "Google Maps language", "Keep en-US: the scraper reads the English page"),
    Option("USER_AGENT", "advanced", "Browser user agent"),
    Option("PAGE_TIMEOUT_MS", "advanced", "Page timeout (ms)", minimum=1000),
    Option("DETAIL_RETRIES", "advanced", "Retries per place page", minimum=0),
    Option("SCROLL_PAUSE_MS", "advanced", "Pause between two scrolls (ms)", minimum=0),
    Option("SCROLL_STALL_LIMIT", "advanced", "Scrolls without new results before stopping", minimum=1),
    Option("MIN_DELAY_MS", "advanced", "Random pause between pages – at least (ms)", minimum=0),
    Option("MAX_DELAY_MS", "advanced", "… and at most (ms)", minimum=0),
    Option("BLOCK_MEDIA", "advanced", "Skip images and fonts", "Pages load faster"),
    Option("ENRICH_TIMEOUT_S", "advanced", "Website request timeout (seconds)", minimum=1),
    Option("ENRICH_MAX_BYTES", "advanced", "Largest website page read (bytes)", minimum=10_000),
)
BY_NAME = {option.name: option for option in OPTIONS}

_ACTIVE_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_COMMENTED_LINE = re.compile(r"^\s*#\s*([A-Z][A-Z0-9_]*)\s*=")
_PLAIN_VALUE = re.compile(r"^[^\s'\"\\#]+(?: [^\s'\"\\#]+)*$")   # safe without quotes in .env
_COUNTRY = re.compile(r"^[A-Za-z]{2}$")


class SettingsUpdate(BaseModel):
    """PATCH /api/settings: the settings to change. null (or an empty text) puts one back to its default."""
    values: dict[str, Any]


class SettingsError(ValueError):
    """Settings that can't be saved: ``errors`` maps each one to what's wrong."""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        name, why = next(iter(errors.items()))
        label = BY_NAME[name].label if name in BY_NAME else ""
        if len(errors) > 1:
            super().__init__(f"{len(errors)} settings need fixing")
        else:
            super().__init__(f"{label}: {why}" if label else why)


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------
def kind(option: Option) -> str:
    """How the panel edits it: bool, int, float, choice, secret or text."""
    if option.secret:
        return "secret"
    if option.choices:
        return "choice"
    annotation = Settings.model_fields[option.name].annotation
    return {bool: "bool", int: "int", float: "float"}.get(annotation, "text")


def locked_by(name: str) -> str:
    """The environment variable that sets ``name`` on this computer ('' if none) - it wins over .env."""
    return next((key for key in os.environ if key.upper() == name), "")


def saved_names(env_file: Path) -> set[str]:
    """The settings .env sets."""
    if not env_file.is_file():
        return set()
    return {key.upper() for key in dotenv_values(env_file, interpolate=False)}


def load(env_file: Path) -> Settings:
    """The settings a server started now would have."""
    return Settings(_env_file=env_file)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def describe(live: Settings, env_file: Path) -> dict:
    """GET /api/settings: the groups and every setting - its value in use (never a secret's), whether .env
    sets it, and whether .env holds a value the server only uses after a restart."""
    saved = saved_names(env_file)
    try:
        fresh, file_error = load(env_file), ""
    except ValidationError as exc:        # .env edited by hand into something the server can't start with
        fresh, file_error = None, _validation_message(exc)
    fields = []
    for option in OPTIONS:
        value = getattr(live, option.name)
        default = Settings.model_fields[option.name].default
        field = {
            "name": option.name, "group": option.group, "label": option.label, "help": option.help,
            "kind": kind(option), "placeholder": option.placeholder, "choices": list(option.choices),
            "min": option.minimum, "max": option.maximum, "needed": option.needed, "restart": option.restart,
            "saved": option.name in saved, "locked": locked_by(option.name),
            "pending": fresh is not None and getattr(fresh, option.name) != value,
        }
        if option.secret:
            field.update(value=None, default=None, is_set=bool(value))
        elif field["kind"] == "bool":
            field.update(value=bool(value), default=bool(default))
        else:
            field.update(value=_text(value), default=_text(default))
        fields.append(field)
    return {
        "groups": [{"key": g.key, "title": g.title, "help": g.help, "advanced": g.advanced} for g in GROUPS],
        "fields": fields,
        "env_file": str(env_file),
        "file_error": file_error,
        "pending": [f["name"] for f in fields if f["pending"]],
    }


# ----------------------------------------------------------------------
# Saving
# ----------------------------------------------------------------------
def save(live: Settings, values: dict[str, Any], env_file: Path) -> tuple[list[str], list[str]]:
    """Write ``values`` to .env and apply them to ``live``.

    Returns the settings whose value changed and, of those, the ones that need a restart. Raises
    SettingsError (nothing written) when a value is no good.
    """
    errors: dict[str, str] = {}
    updates: dict[str, str | None] = {}
    for name, raw in values.items():
        option = BY_NAME.get(name)
        if option is None:
            errors[name] = "there is no such setting"
        elif key := locked_by(name):
            errors[name] = f"set by the environment variable {key} on this computer – change or remove it there"
        else:
            try:
                updates[name] = env_text(option, raw)
            except ValueError as exc:
                errors[name] = str(exc)
    if errors:
        raise SettingsError(errors)
    if not updates:
        return [], []
    before = env_file.read_bytes() if env_file.is_file() else None
    write_env(env_file, updates)
    try:
        fresh = load(env_file)
    except ValidationError as exc:        # another line of .env is broken: put the file back as it was
        if before is None:
            env_file.unlink(missing_ok=True)
        else:
            _write_atomic(env_file, before)
        raise SettingsError({"_file": f"the .env file has a value the server can't use – "
                                      f"{_validation_message(exc)}"}) from None
    changed, restart = [], []
    for name in updates:
        value = getattr(fresh, name)
        if value == getattr(live, name):
            continue
        changed.append(name)
        if BY_NAME[name].restart:
            restart.append(name)
        else:
            setattr(live, name, value)
    return changed, restart


def env_text(option: Option, raw: Any) -> str | None:
    """A value from the panel as .env text (None: back to the default). ValueError says what's wrong."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    annotation = Settings.model_fields[option.name].annotation
    if isinstance(raw, bool) and annotation is not bool:
        raise ValueError("expected a value, not on / off")
    text = (raw if isinstance(raw, str) else _text(raw)).strip()
    if "\n" in text or "\r" in text:
        raise ValueError("must be a single line")
    if "${" in text:
        raise ValueError("can't contain ${ – the .env file would read it as a variable")
    try:
        value = TypeAdapter(annotation).validate_python(text)
    except ValidationError:
        raise ValueError({int: "must be a whole number", float: "must be a number",
                          bool: "expected on or off"}.get(annotation, "not a valid value")) from None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if option.minimum is not None and value < option.minimum:
            raise ValueError(f"must be at least {option.minimum:g}")
        if option.maximum is not None and value > option.maximum:
            raise ValueError(f"must be at most {option.maximum:g}")
    _check(option, text)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if option.choices:
        return text.lower()
    return text


def _check(option: Option, text: str) -> None:
    name = option.name
    if option.choices and text.lower() not in option.choices:
        raise ValueError("choose " + " or ".join(option.choices))
    if name == "OUTREACH_TIMEZONE":
        try:
            ZoneInfo(text)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown time zone – write one like America/Chicago") from None
    elif name == "OUTREACH_DAYS":
        try:
            parse_days(text)
        except WindowError:
            raise ValueError("write days like mon-fri or mon,wed,fri") from None
    elif name == "OUTREACH_HOURS":
        try:
            parse_hours(text)
        except WindowError:
            raise ValueError("write the first and the last hour like 8-12 (0 to 24)") from None
    elif name == "SENDER_EMAIL" and "@" not in text:
        raise ValueError("must be an email address")
    elif name in ("REGION_CODE", "DEFAULT_REGION") and not _COUNTRY.match(text):
        raise ValueError("a two-letter country code, e.g. US")
    elif name == "PROXY_SERVER" and urlparse(text).scheme not in ("http", "https", "socks5"):
        raise ValueError("write it like http://host:3128 (or https:// / socks5://)")


def _validation_message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error.get("loc", ())) or "a value"
    return f"{where.upper()}: {error.get('msg', 'not valid')}"


def env_line_value(value: str) -> str:
    """How a value is written in .env so python-dotenv reads it back exactly."""
    if _PLAIN_VALUE.match(value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def write_env(env_file: Path, updates: dict[str, str | None]) -> None:
    """Set (or, for None, remove) settings in .env, keeping every other line as it is.

    A setting that isn't in the file yet goes under its commented example line (``# NAME=…``), else at the
    end. A new .env starts as a copy of .env.example.
    """
    if env_file.is_file():
        lines = env_file.read_text(encoding="utf-8-sig").splitlines()
    elif TEMPLATE.is_file() and env_file.parent.resolve() == TEMPLATE.parent.resolve():
        lines = TEMPLATE.read_text(encoding="utf-8-sig").splitlines()
    else:
        lines = []
    for name, value in updates.items():
        active = [i for i, line in enumerate(lines)
                  if (m := _ACTIVE_LINE.match(line)) and m.group(1).upper() == name]
        if value is None:
            for i in reversed(active):
                del lines[i]
            continue
        line = f"{name}={env_line_value(value)}"
        if active:
            lines[active[0]] = line
            for i in reversed(active[1:]):
                del lines[i]
            continue
        example = next((i for i, text in enumerate(lines)
                        if (m := _COMMENTED_LINE.match(text)) and m.group(1) == name), None)
        if example is None:
            lines.append(line)
        else:
            lines.insert(example + 1, line)
    _write_atomic(env_file, ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8"))


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=".env")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
