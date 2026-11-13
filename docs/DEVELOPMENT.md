# Development guide

## 1. Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows PowerShell  (bash: source .venv/bin/activate)
pip install -r requirements.txt
playwright install chromium
python run.py --reload              # auto-restarts on code changes, panel at http://127.0.0.1:8000
```

`start.bat` / `start.py` (the one-click start in the README) do the same setup by themselves, then
run `run.py` as a child process: the panel's Restart button makes `run.py` exit with code 75
(`app/control.py`) and `start.py` starts it again; its Stop button and Ctrl+C in the window end
both. `run.py` logs no HTTP requests unless started with `--access-log` (the panel asks every few
seconds).

`.claude/launch.json` contains a `panel` configuration for editors / agents that can start
dev servers from it.

Python 3.11+ is required (the code uses `X | Y` unions and `match`-free 3.11 idioms).
Windows, macOS and Linux are supported; on Linux run `playwright install-deps chromium` once
for the browser's system libraries.

## 2. Project layout

```
app/
  __init__.py          version string
  config.py            Settings (pydantic-settings) – see reference below
  settings_editor.py   the Settings tab: every setting's group, label and help (OPTIONS), .env read / write
  control.py           the panel's Stop / Restart (run.py hands over its uvicorn server), browse_url()
  models.py            PlaceRow, Job, JobSummary, Progress, request bodies
  filters.py           SearchFilters - the website / contact rules (pure)
  main.py              FastAPI routes + static panel
  jobs.py              JobManager (background tasks, persistence, enrichment application),
                       PlaceFilter (applies filters while scraping), result_notice()
  logbuffer.py         in-memory ring buffer behind the panel's Live logs (+ job id tagging)
  remote.py            RemoteSearcher - ROLE=searcher: runs the cloud server's searches (docs/CLOUD.md)
  scraper/
    maps.py            MapsScraper (Playwright) + build_row(); Search, several searches per job
    area.py            map sections for whole-area searches (pure geometry)
    records.py         Google record decoder (index map IDX)
    parsing.py         address / phone / url helpers, reference tables
  enrichment/
    website.py         WebsiteEnricher, enrich_many()
    extractors.py      extract_from_html(), clean_email(), scoring, candidate pages
    sitefinder.py      SiteFinder - websites a Google Maps listing does not show
    websearch.py       WebSearch - DuckDuckGo / Bing result pages (never Google)
  export/
    csv_export.py      DEFAULT_COLUMNS, EXTENDED_COLUMNS, rows_to_csv(), job_filename()
    csv_import.py      rows_from_csv() - a lead file (the crawler's CSV format) back into rows
  outreach/            email campaigns (docs/OUTREACH.md)
    models.py          BusinessType, Campaign, Lead, Step, request bodies
    templates.py       placeholders, [[optional parts]], compose(), the default emails / business types
    leads.py           which businesses of a job become leads, and why the others don't
    schedule.py        SendWindow - days, hours, time zone
    mailbox.py         SMTP / IMAP (worker thread), build_message(), parse_headers(), classify()
    manager.py         OutreachManager - persistence, the API's operations, the send loop, results_csv()
    sites.py           SiteService - a campaign's preview websites: built, uploaded, first emails wait for them
  drive/               Google Drive (docs/DRIVE.md)
    client.py          DriveClient - OAuth sign-in (PKCE, loopback) and the REST calls, over httpx
    sync.py            DriveSync - folders per business type, uploads, lead files, campaign results
  static/
    index.html, app.js, styles.css   the panel (Search tab, Stop, the stopped / restarting screen)
    outreach.js                       the Outreach tab
    settings.js                       the Settings tab
    icon.ico                          the desktop shortcut's icon
sites/                 preview websites (docs/SITES.md)
  render.py            one business's website: design + trade pack + its details
  build.py             a search's / campaign's websites, data/sites/sites.json
  deploy.py            online at sites.leadspilot.co (wrangler), how long each stays up
  designs/, trades/    page templates; words, photos and colours per trade
  assets/, geo/        the trades' photos; the US town list
  worker/              the Cloudflare Worker that serves them (wrangler.jsonc, src/index.js)
cli.py                 CLI entry point
run.py                 uvicorn launcher
start.bat, start.py    one-click start: .venv, packages, Chromium, desktop shortcut, then run.py (restarts it)
tests/                 pytest suite
docs/                  this documentation
data/                  runtime files (git-ignored)
```

Dependency direction: `main → jobs → (scraper, enrichment, export) → models/config`;
`main → outreach → (enrichment helpers, models)` and `main → drive → (export, outreach)`.
`scraper` and `enrichment` never import from `jobs`, `outreach`, `drive` or `main`, so both can be
used stand-alone (that is what `cli.py` does). `JobManager` knows nothing about Drive: `main.py`
hooks the upload in through `JobManager.on_finished`.

## 3. Running the pieces by hand

```python
import asyncio
from app.scraper.maps import MapsScraper
from app.enrichment.website import WebsiteEnricher

async def demo():
    rows = await MapsScraper(headless=False).scrape("coffee shops in austin tx", 5)
    async with WebsiteEnricher() as enricher:
        result = await enricher.find_contacts(rows[0].website)
    print(rows[0].name, result.email_addresses, result.phones)

asyncio.run(demo())
```

`MapsScraper` accepts `on_progress(phase, message, current, total)` and `on_row(row)`
callbacks (sync or async). To keep only some places, pass a selector – usually `PlaceFilter`:

```python
from app.filters import SearchFilters
from app.jobs import PlaceFilter

leads = SearchFilters(website="without", contact="phone_or_email")
rows = await MapsScraper().scrape("plumbers in Austin, TX", 20, selector=PlaceFilter(leads))
# the whole map of Austin, section by section:
rows = await MapsScraper().scrape(Search("plumbers", area="Austin, TX"), 20, selector=PlaceFilter(leads))
```

`limit` then counts kept places, and `scraper.stats` tells how many listings were read, how many
were skipped and why, how many searches / map sections ran, and why the scan stopped
(`stats.stopped`). `Search` comes from `app.scraper.maps`.

## 4. Configuration reference

Settings are read from environment variables, then from `.env` in the project root
(`app/config.py`). The panel's Settings tab shows and changes all of them: it writes `.env` and
applies new values at once, except `HOST`, `PORT`, `DATA_DIR`, `LOG_BUFFER_SIZE`, `KEEP_AWAKE`, `ROLE` and the
preview-website settings, which are only read when the server starts. A setting that is also an environment variable can't be changed there.
`tests/test_docs.py` checks that every setting is listed here, `tests/test_settings.py` that every
one is in the Settings tab (`OPTIONS` in `app/settings_editor.py`).

### Server

| Variable | Default | Description |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | bind address for `run.py` |
| `PORT` | `8000` | port for `run.py` |
| `DATA_DIR` | `<project>/data` | where `jobs/` and `exports/` live |
| `LOG_BUFFER_SIZE` | `2000` | log lines kept in memory for the panel's Live logs / `GET /api/logs` |
| `KEEP_AWAKE` | `true` | Windows doesn't go to sleep while the panel runs, so scheduled emails and the autopilot keep going (the screen still turns off) |

### Google Maps scraping

| Variable | Default | Description |
| --- | --- | --- |
| `HEADLESS` | `true` | run Chromium without a window |
| `LOCALE` | `en-US` | browser locale; the UI is additionally forced to English with `hl=en` |
| `REGION_CODE` | *(empty)* | Google `gl` parameter (`us`, `gb`, …) to bias results to a country |
| `MAX_RESULTS` | `500` | hard cap for `limit` |
| `MAX_SCANNED` | `5000` | most distinct listings read per job while looking for matches (a single Google search stops at about 120; a whole-area search reads many more) |
| `MAX_SECTIONS` | `200` | most map sections searched per job by a whole-area search (each one is a Google search; a big city can need more) |
| `DETAIL_CONCURRENCY` | `3` | tabs used in parallel for place pages |
| `SCROLL_PAUSE_MS` | `1200` | pause after each feed scroll (plus up to 400 ms jitter) |
| `SCROLL_STALL_LIMIT` | `6` | consecutive scrolls without new results before giving up |
| `PAGE_TIMEOUT_MS` | `30000` | navigation / selector timeout |
| `DETAIL_RETRIES` | `2` | retries per place page |
| `MIN_DELAY_MS` / `MAX_DELAY_MS` | `250` / `900` | random pause between place pages per tab |
| `PROXY_SERVER` | *(empty)* | proxy for the browser, e.g. `http://user:pass@host:3128` |
| `BLOCK_MEDIA` | `true` | abort images / fonts / media / map tiles for speed |
| `USER_AGENT` | desktop Chrome UA | user agent for the browser and the enrichment HTTP client |

### Website enrichment

| Variable | Default | Description |
| --- | --- | --- |
| `ENRICH_TIMEOUT_S` | `15` | per request / navigation timeout |
| `ENRICH_MAX_PAGES` | `6` | extra pages fetched per website |
| `ENRICH_CONCURRENCY` | `4` | websites processed in parallel |
| `ENRICH_BROWSER_FALLBACK` | `true` | render JS-only or bot-blocking sites with Chromium |
| `ENRICH_MAX_BYTES` | `2000000` | truncate larger pages |
| `DEFAULT_REGION` | `US` | phone parsing region when a row has no country |
| `WEB_SEARCH_ENGINE` | `duckduckgo` | engine asked first when a *No website* search also searches the web: `duckduckgo` or `bing` (the other is the fallback; Google is never used) |
| `WEB_SEARCH_DELAY_S` | `3.0` | minimum pause between two web searches |

### Email outreach ([OUTREACH.md](OUTREACH.md))

| Variable | Default | Description |
| --- | --- | --- |
| `SMTP_HOST` | *(empty)* | outgoing mail server, e.g. `smtp.gmail.com`; empty: nothing can be sent |
| `SMTP_PORT` | `587` | `587` (STARTTLS) or `465` (SSL); a server without encryption is refused unless it is `localhost` |
| `IMAP_HOST` | *(empty)* | incoming mail server, e.g. `imap.gmail.com` – needed to notice replies; without it nothing is sent |
| `IMAP_PORT` | `993` | IMAP over SSL |
| `IMAP_FOLDER` | `INBOX` | where replies arrive (opened read-only) |
| `MAIL_USERNAME` | *(empty)* | the mailbox login for SMTP and IMAP, usually the address |
| `MAIL_PASSWORD` | *(empty)* | its password – for Gmail / Google Workspace an app password (spaces are dropped) |
| `SENDER_EMAIL` | *(empty = `MAIL_USERNAME`)* | the From address |
| `SENDER_NAME` | *(empty)* | the From name and `{SenderName}` – required to start a campaign |
| `SENDER_COMPANY` | *(empty)* | `{SenderCompany}` |
| `SENDER_ADDRESS` | *(empty)* | `{SenderAddress}`, your postal address – required (CAN-SPAM), it goes in every footer |
| `OUTREACH_AUTOSEND` | `true` | `false`: the server never sends on its own and checks replies only on request (the tests set it) |
| `OUTREACH_TIMEZONE` | `America/Chicago` | time zone of the send window – your leads' (needs the `tzdata` package on Windows) |
| `OUTREACH_DAYS` | `mon-fri` | days emails go out: `mon-fri`, `mon,wed,fri`, `mon-sat` … |
| `OUTREACH_HOURS` | `8-12` | hours emails go out: `8-12` = 08:00 until 11:59 |
| `OUTREACH_DAILY_LIMIT` | `30` | emails per day, all campaigns and follow-ups together |
| `OUTREACH_MIN_GAP_S` / `OUTREACH_MAX_GAP_S` | `90` / `240` | random pause between two emails |
| `OUTREACH_CHECK_MINUTES` | `10` | how often the inbox is checked in the send window (hourly outside it; always right before an email) |
| `OUTREACH_MIN_REVIEWS` / `OUTREACH_MIN_RATING` | `10` / `4.0` | `{Rating}` and `{Reviews}` only have a value for a business with this many reviews and stars |
| `OUTREACH_TEST_COPY_TO` | *(empty)* | your own addresses, comma-separated: each day's first email also goes to them, word for word, so you can see which tab it lands in ([OUTREACH.md](OUTREACH.md#6-what-is-sent)) |
| `ALERT_TO` | *(empty)* | where emails to you go – problems, the daily summary, replies, *Google Drive disconnected* ([OUTREACH.md §8](OUTREACH.md#8-emails-to-you-about-the-panel-appoutreachalertspy)) – comma-separated; empty: the From address |
| `EMAIL_VERIFY_KEY` | *(empty)* | a [Reoon](https://www.reoon.com/email-verifier/) API key: each address is checked before its first email and one whose mailbox doesn't exist is skipped ([OUTREACH.md §3](OUTREACH.md#3-templates-appoutreachtemplatespy)); empty: only its domain is checked |
| `OUTREACH_UNSUBSCRIBE_HEADER` | `false` | `true`: campaign emails carry a `List-Unsubscribe` header (Gmail's unsubscribe link); off, they read as one-to-one emails and the footer says to reply "no" |

### Preview websites ([SITES.md](SITES.md))

| Variable | Default | Description |
| --- | --- | --- |
| `PREVIEW_SITES` | `true` | every business a campaign emails gets a preview website, and its first email waits until it is online (`false`: neither; the tests set it) |
| `SITES_URL` | `https://sites.leadspilot.co` | where the websites are online: `<SITES_URL>/<slug>/` |
| `SITES_VISITS_KEY` | *(empty)* | the Worker's `VISITS_KEY` secret: with it the panel fetches who opened which website (`python sites/deploy.py --visits-key` sets both, [SITES.md](SITES.md#5-who-opened-their-website)) |

### Google Drive ([DRIVE.md](DRIVE.md))

| Variable | Default | Description |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` | *(empty)* | OAuth client ID of type *Desktop app* from the Google Cloud console |
| `GOOGLE_CLIENT_SECRET` | *(empty)* | its client secret |
| `DRIVE_FOLDER` | *(empty = My Drive)* | where the business-type folders go: a folder name / path in My Drive, or a folder's URL / id |
| `DRIVE_AUTO_UPLOAD` | `true` | upload each search when it finishes and campaign results while they run (`false`: only the *Save to Drive* buttons; the tests set it) |

### Cloud server ([CLOUD.md](CLOUD.md))

| Variable | Default | Description |
| --- | --- | --- |
| `ROLE` | `all` | `all`: this computer searches and sends. `sender`: the cloud server – it sends, and its searches wait for a search computer. `searcher`: only runs the sender's searches; no email, reply check, autopilot or website upload runs here (after a restart) |
| `CLOUD_URL` | *(empty)* | searcher: the sender's panel, e.g. `https://panel.leadspilot.co` |
| `SEARCHER_TOKEN` | *(empty)* | the same long random text on both computers; the sender only hands its searches to a request carrying it (`Authorization: Bearer …`) |
| `CLOUD_ACCESS_ID` | *(empty)* | searcher: a Cloudflare Access service token's client ID (`CF-Access-Client-Id`) |
| `CLOUD_ACCESS_SECRET` | *(empty)* | … and its secret (`CF-Access-Client-Secret`) |
| `TRUSTED_HOSTS` | *(empty)* | sender: comma-separated names its panel is opened at through Cloudflare Access; Settings, Stop and Restart work there as on `127.0.0.1` |

## 5. Tests

```bash
python -m pytest -q            # everything (offline, ~1 s)
python -m pytest -q tests/test_extractors.py -k obfusc
```

| File | Covers |
| --- | --- |
| `test_parsing.py` | address splitting, phone formatting, review/rating parsing, URL cleaning |
| `test_records.py` | Google record decoding (synthetic records), index-map documentation check |
| `test_build_row.py` | source precedence in `build_row()` |
| `test_filters.py` | website classification (own site vs profile page), every website / contact rule, labels, `search_web` only with *no website* |
| `test_scan.py` | the scraper's feed loop on an in-memory feed: rounds, pre-filtering, replacements, stop reasons, stats |
| `test_blocked.py` | Google's "unusual traffic" check: recognising the page (URL, status, wording, IP address), and the scrape stopping at once – no further searches, sections or place pages – with what it found |
| `test_area.py` | map-section geometry: world pixels, the view in a Google Maps URL, section URLs, quarters |
| `test_area_search.py` | whole-area and multi-place searches against a simulated Google that knows where businesses are: splitting full sections, finding businesses outside the top 120, de-duplication, limits, zoom-out guard, failures |
| `test_extractors.py` | email/phone extraction (no junk filtering), obfuscation, listing-text emails, contact-link discovery, links to other sites |
| `test_website.py` | enrichment fetch strategy: profile pages read alone, own sites crawled |
| `test_sitefinder.py` | finding a website a listing does not show: email domains, profile-page links, web search results, directory / parked / look-alike sites rejected, one fetch per page per job |
| `test_websearch.py` | redirect unwrapping, DuckDuckGo / Bing result parsing, engine fallback and switch-off on captchas (fake network) |
| `test_jobs.py` | enrichment target selection, the duplicate-row rule, `PlaceFilter` (incl. the unlisted-website check and its setup), listing emails, short-result notices |
| `test_csv_export.py` | column layout, escaping, BOM |
| `test_cli.py` | `cli.py` end to end on an in-memory feed: business + place, filter flags, short-result note, a business left out for its unlisted website, a Google block (error line, CSV, exit code 3) |
| `test_api.py` | every route through FastAPI's `TestClient`, business + place and filtered jobs run with a fake scraper, enricher and web search (a business left out for its unlisted website, web search switched off), a job Google blocked, notices, persistence (incl. pre-1.2 jobs), log tagging |
| `test_logbuffer.py` | ring buffer semantics: cursor, limit/truncation, level filter, tracebacks, job-id tagging across asyncio tasks |
| `test_outreach.py` | templates (placeholders, optional parts, business names), who becomes a lead, the send window (DST, weekends), building / reading emails (threading, replies, auto-replies, bounces, unsubscribes), the send loop on a fake mailbox and clock: pauses, daily limit, follow-ups, replies stopping them, auth errors pausing campaigns, nobody emailed twice, business types |
| `test_outreach_api.py` | the outreach and Drive routes through `TestClient`: business types, campaigns from searches and from a Drive file, start / pause / delete, test emails, connecting Drive (Google's redirect), uploads |
| `test_sites.py` | preview websites: who gets one (searched city and state, out-of-area businesses, supply shops, low ratings), how long each stays online, what gets uploaded, a campaign's first emails waiting for their websites, a trade without a design, upload errors |
| `test_drive.py` | lead files (the crawler's CSV and hand-made ones), sign-in and token refresh, folders per business type, uploads that update in place, shared folders – against a fake Google (`httpx.MockTransport`) |
| `test_settings.py` | the Settings tab: every setting listed, values written to `.env` and read back exactly (quotes, `#`, backslashes), the rest of the file kept, validation, environment variables winning, a broken `.env`; the settings / stop / restart routes and who may call them; `start.py` installing only when `requirements.txt` changed and finding a running panel |
| `test_remote.py` | two computers ([CLOUD.md](CLOUD.md)): a sender's searches waiting, handed out once, progress, results, kept across a restart, given up after silence (and the autopilot searching the place again), cancelled; the remote routes and their token; Settings at a `TRUSTED_HOSTS` name; a search computer running a search against the real routes (`httpx.ASGITransport`) and saying what's wrong |
| `test_docs.py` | documentation consistency (see §7) |

The suite never sends an email or touches Google Drive: `tests/conftest.py` sets
`OUTREACH_AUTOSEND=false` and `DRIVE_AUTO_UPLOAD=false` before the app is imported, so the
server's loops don't run with the mailbox / Drive from your `.env`, and the outreach tests use a
fake mailbox, a fake clock and a fake Google.

Live checks against Google are deliberately not part of the suite (they are slow and
non-deterministic). Use the CLI for a manual smoke test:

```bash
python cli.py "roofing companies" "Arlington, TX" --limit 5 --headed
python cli.py plumbers "Austin, TX" --website without --contact phone_or_email --limit 5
```

## 6. Common changes

### Add an output column

1. Add the field to `PlaceRow` (`app/models.py`).
2. Fill it in `build_row()` (`app/scraper/maps.py`) and/or in `apply_contact_result()`.
3. Add `("Header", "field")` to `DEFAULT_COLUMNS` or `EXTENDED_COLUMNS`
   (`app/export/csv_export.py`).
4. Add a `<th>` and a cell in `renderRows()` (`app/static/index.html`, `app/static/app.js`)
   if it should be visible in the panel.
5. Document it in `docs/DATA_MODEL.md` – `test_docs.py` fails until you do.

### Treat another platform as "no website"

Add its domain to `PROFILE_DOMAINS` in `app/scraper/parsing.py` (sub-domains match
automatically), add a case to `test_website_type` in `tests/test_filters.py`, and mention it in
[DATA_MODEL.md §9](DATA_MODEL.md#9-search-filters) if it is a new kind of platform.

### Repair a broken selector / record index

See [SCRAPING.md §6](SCRAPING.md#6-when-google-changes-its-layout). Update `IDX` in
`records.py` or the selectors in `maps.py`, then the table in `SCRAPING.md` and, if the
behaviour changed, `CHANGELOG.md`.

### Add an API route

Add it to `app/main.py` **as `async def`** (the job manager schedules asyncio tasks, which
requires the event loop of the request), document it in `docs/API.md`, add a test in
`tests/test_api.py`.

### Add a setting

Add the field to `Settings`, an entry to `.env.example`, a row to §4 above, and an `Option` (its
group, label and help) to `OPTIONS` in `app/settings_editor.py` – `test_settings.py` fails until
you do. If it is only read at start-up, mark it `restart=True`; if an object keeps its own copy,
refresh that in `_apply_settings()` (`app/main.py`).

## 7. Keeping docs in sync

Documentation drift is caught by `tests/test_docs.py`:

| Check | Fails when |
| --- | --- |
| required docs exist | a file in `docs/` is deleted or renamed |
| `DATA_MODEL.md` lists every CSV column | a column is added to `csv_export.py` |
| `API.md` lists every `/api` route | a route is added to `main.py` |
| `DEVELOPMENT.md` lists every setting | a field is added to `Settings` |
| the Settings tab lists every setting (`test_settings.py`) | a field is added to `Settings` without an `Option` |
| `SCRAPING.md` lists every record index | `IDX` in `records.py` changes |
| `CHANGELOG.md` mentions `app.__version__` | the version is bumped without a changelog entry |
| architecture docs contain Mermaid diagrams | diagrams are removed |

Conventions:

- Diagrams are Mermaid code blocks (rendered by GitHub, GitLab, VS Code, Obsidian…). Keep
  them next to the prose they explain.
- Each document starts with what it covers and links to related pages.
- Bump `app/__init__.py::__version__` and add a `CHANGELOG.md` entry for user-visible changes.

## 8. Code style

- Python 3.11, type hints everywhere, `from __future__ import annotations`.
- Pure functions for anything parse-like (easy to test); I/O lives in the scraper / enricher
  classes.
- Log with the module logger (`log = logging.getLogger(__name__)`); no prints outside `cli.py`.
  Everything logged at INFO or above shows up in the panel's **Live logs**, so write lines a
  user can act on ("Feed: 40 of 100 place(s) found", "Enriched #3 Acme: info@acme.com") and
  keep per-request noise at DEBUG. Lines emitted inside a job task are tagged with the job
  id automatically (`app/logbuffer.py::current_job_id`).
- The panel is intentionally dependency-free vanilla JS; keep it that way unless a real need
  appears.
