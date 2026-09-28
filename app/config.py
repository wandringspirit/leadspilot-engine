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

    # --- Storage ----------------------------------------------------------
    DATA_DIR: Path = PROJECT_ROOT / "data"
    LOG_BUFFER_SIZE: int = 2000           # log lines kept in memory for the panel's Live logs

    # --- Google Maps scraping --------------------------------------------
    HEADLESS: bool = True                 # set False to watch the browser work
    LOCALE: str = "en-US"                 # forces English UI so selectors match
    REGION_CODE: str = ""                 # optional Google "gl" param, e.g. "us" - biases results
    MAX_RESULTS: int = 500                # hard cap for a single job
    MAX_SCANNED: int = 1000               # listings examined per job while looking for filter matches
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

    @property
    def jobs_dir(self) -> Path:
        return self.DATA_DIR / "jobs"

    @property
    def exports_dir(self) -> Path:
        return self.DATA_DIR / "exports"


settings = Settings()
settings.jobs_dir.mkdir(parents=True, exist_ok=True)
settings.exports_dir.mkdir(parents=True, exist_ok=True)
