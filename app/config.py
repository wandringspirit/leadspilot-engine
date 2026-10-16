"""Runtime configuration.

Every setting can be overridden with an environment variable of the same name
(or via a `.env` file in the project root). See `.env.example` and
docs/DEVELOPMENT.md for the full list.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    # --- Server -----------------------------------------------------------
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    KEEP_AWAKE: bool = True               # Windows doesn't sleep while the panel runs (emails, autopilot)

    # --- Storage ----------------------------------------------------------
    DATA_DIR: Path = PROJECT_ROOT / "data"
    LOG_BUFFER_SIZE: int = 2000           # log lines kept in memory for the panel's Live logs

    # --- Google Maps scraping --------------------------------------------
    HEADLESS: bool = True                 # set False to watch the browser work
    LOCALE: str = "en-US"                 # forces English UI so selectors match
    REGION_CODE: str = ""                 # optional Google "gl" param, e.g. "us" - biases results
    MAX_RESULTS: int = 500                # hard cap for a single job
    MAX_SCANNED: int = 5000               # distinct listings read per job while looking for matches
    MAX_SECTIONS: int = 200               # map sections searched per job ("whole area" search)
    DETAIL_CONCURRENCY: int = 3           # parallel tabs used for place pages
    SCROLL_PAUSE_MS: int = 1200           # wait between feed scrolls
    SCROLL_STALL_LIMIT: int = 6           # scrolls without new results -> stop
    PAGE_TIMEOUT_MS: int = 30000          # navigation timeout for Maps pages
    DETAIL_RETRIES: int = 2               # retries per place page
    MIN_DELAY_MS: int = 250               # random delay between page visits
    MAX_DELAY_MS: int = 900
    PROXY_SERVER: str | None = None       # e.g. "http://user:pass@host:port"
    BLOCK_MEDIA: bool = True              # skip images/fonts/media for speed
    USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )

    # --- Website enrichment (email / phone discovery) --------------------
    ENRICH_TIMEOUT_S: float = 15.0        # per HTTP request
    ENRICH_MAX_PAGES: int = 6             # pages fetched per website
    ENRICH_CONCURRENCY: int = 4           # websites processed in parallel
    ENRICH_BROWSER_FALLBACK: bool = True  # render JS-heavy sites with Chromium
    ENRICH_MAX_BYTES: int = 2_000_000     # ignore pages larger than this
    DEFAULT_REGION: str = "US"            # phone parsing region when unknown

    # --- Web search for websites Google Maps does not list ----------------
    WEB_SEARCH_ENGINE: str = "duckduckgo" # duckduckgo | bing - the other one is the fallback (never Google)
    WEB_SEARCH_DELAY_S: float = 3.0       # at least this long between two web searches

    # --- Email outreach (docs/OUTREACH.md) ----------------------------------
    SMTP_HOST: str = ""                   # e.g. smtp.gmail.com - empty: nothing can be sent
    SMTP_PORT: int = 587                  # 587 (STARTTLS) or 465 (SSL)
    IMAP_HOST: str = ""                   # e.g. imap.gmail.com - needed to notice replies
    IMAP_PORT: int = 993
    IMAP_FOLDER: str = "INBOX"            # where replies arrive
    MAIL_USERNAME: str = ""               # the mailbox login (SMTP and IMAP), usually the address
    MAIL_PASSWORD: str = ""               # Gmail / Google Workspace: an app password
    SENDER_EMAIL: str = ""                # From address; empty: MAIL_USERNAME
    SENDER_NAME: str = ""                 # From name and {SenderName}
    SENDER_COMPANY: str = ""              # {SenderCompany}
    SENDER_ADDRESS: str = ""              # {SenderAddress}: your postal address (US law: in every email)
    OUTREACH_AUTOSEND: bool = True        # false: the server never sends on its own
    OUTREACH_TIMEZONE: str = "America/Chicago"  # time zone of the send window (your leads')
    OUTREACH_DAYS: str = "mon-fri"        # days emails go out
    OUTREACH_HOURS: str = "8-12"          # hours emails go out, e.g. 8-12 = 08:00 to 11:59
    OUTREACH_DAILY_LIMIT: int = 30        # emails per day, all campaigns and follow-ups together
    OUTREACH_MIN_GAP_S: int = 90          # random pause between two emails: at least ...
    OUTREACH_MAX_GAP_S: int = 240         # ... and at most this many seconds
    OUTREACH_CHECK_MINUTES: int = 10      # how often the mailbox is checked for replies and bounces
    OUTREACH_MIN_REVIEWS: int = 10        # {Rating} / {Reviews} need at least this many reviews ...
    OUTREACH_MIN_RATING: float = 4.0      # ... and at least this rating
    OUTREACH_UNSUBSCRIBE_HEADER: bool = False   # a List-Unsubscribe header (Gmail's unsubscribe link) on
                                          # campaign emails - off: one-to-one emails got filed as mailings

    # --- Preview websites (docs/SITES.md) ------------------------------------
    PREVIEW_SITES: bool = True            # each business a campaign emails gets a preview website first
    SITES_URL: str = "https://sites.leadspilot.co"   # where they are online: <SITES_URL>/<slug>/

    # --- Google Drive (docs/DRIVE.md) ----------------------------------------
    GOOGLE_CLIENT_ID: str = ""            # OAuth client of type "Desktop app" from the Google Cloud console
    GOOGLE_CLIENT_SECRET: str = ""
    DRIVE_FOLDER: str = ""                # where the business-type folders go: a folder name / path in My
                                          # Drive ("Leads"), a folder's URL or id; empty = My Drive itself
    DRIVE_AUTO_UPLOAD: bool = True        # upload each search's CSV when it finishes

    @property
    def jobs_dir(self) -> Path:
        return self.DATA_DIR / "jobs"

    @property
    def exports_dir(self) -> Path:
        return self.DATA_DIR / "exports"

    @property
    def campaigns_dir(self) -> Path:
        return self.DATA_DIR / "campaigns"


settings = Settings()
settings.jobs_dir.mkdir(parents=True, exist_ok=True)
settings.exports_dir.mkdir(parents=True, exist_ok=True)
