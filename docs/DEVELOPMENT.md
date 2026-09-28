# Development guide

## 1. Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows PowerShell  (bash: source .venv/bin/activate)
pip install -r requirements.txt
playwright install chromium
python run.py --reload              # auto-restarts on code changes, panel at http://127.0.0.1:8000
```

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
  models.py            PlaceRow, Job, JobSummary, Progress, request bodies
  filters.py           SearchFilters - the website / contact rules (pure)
  main.py              FastAPI routes + static panel
  jobs.py              JobManager (background tasks, persistence, enrichment application),
                       PlaceFilter (applies filters while scraping), result_notice()
  logbuffer.py         in-memory ring buffer behind the panel's Live logs (+ job id tagging)
  scraper/
    maps.py            MapsScraper (Playwright) + build_row(); Search, several searches per job
    area.py            map sections for whole-area searches (pure geometry)
    records.py         Google record decoder (index map IDX)
    parsing.py         address / phone / url helpers, reference tables
  enrichment/
    website.py         WebsiteEnricher, enrich_many()
    extractors.py      extract_from_html(), clean_email(), scoring, candidate pages
  export/
    csv_export.py      DEFAULT_COLUMNS, EXTENDED_COLUMNS, rows_to_csv()
  static/
    index.html, app.js, styles.css   the panel
cli.py                 CLI entry point
run.py                 uvicorn launcher
tests/                 pytest suite
docs/                  this documentation
data/                  runtime files (git-ignored)
```

Dependency direction: `main → jobs → (scraper, enrichment, export) → models/config`.
`scraper` and `enrichment` never import from `jobs` or `main`, so both can be used
stand-alone (that is what `cli.py` does).

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
(`app/config.py`). `tests/test_docs.py` checks that every setting is listed here.

### Server

| Variable | Default | Description |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | bind address for `run.py` |
| `PORT` | `8000` | port for `run.py` |
| `DATA_DIR` | `<project>/data` | where `jobs/` and `exports/` live |
| `LOG_BUFFER_SIZE` | `2000` | log lines kept in memory for the panel's Live logs / `GET /api/logs` |

### Google Maps scraping

| Variable | Default | Description |
| --- | --- | --- |
| `HEADLESS` | `true` | run Chromium without a window |
| `LOCALE` | `en-US` | browser locale; the UI is additionally forced to English with `hl=en` |
| `REGION_CODE` | *(empty)* | Google `gl` parameter (`us`, `gb`, …) to bias results to a country |
| `MAX_RESULTS` | `500` | hard cap for `limit` |
| `MAX_SCANNED` | `5000` | most distinct listings read per job while looking for matches (a single Google search stops at about 120; a whole-area search reads many more) |
| `MAX_SECTIONS` | `100` | most map sections searched per job by a whole-area search |
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
| `test_filters.py` | website classification (own site vs profile page), every website / contact rule, labels |
| `test_scan.py` | the scraper's feed loop on an in-memory feed: rounds, pre-filtering, replacements, stop reasons, stats |
| `test_area.py` | map-section geometry: world pixels, the view in a Google Maps URL, section URLs, quarters |
| `test_area_search.py` | whole-area and multi-place searches against a simulated Google that knows where businesses are: splitting full sections, finding businesses outside the top 120, de-duplication, limits, zoom-out guard, failures |
| `test_extractors.py` | email/phone extraction (no junk filtering), obfuscation, listing-text emails, contact-link discovery |
| `test_website.py` | enrichment fetch strategy: profile pages read alone, own sites crawled |
| `test_jobs.py` | enrichment target selection, the duplicate-row rule, `PlaceFilter`, listing emails, short-result notices |
| `test_csv_export.py` | column layout, escaping, BOM |
| `test_cli.py` | `cli.py` end to end on an in-memory feed: business + place, filter flags, short-result note |
| `test_api.py` | every route through FastAPI's `TestClient`, business + place and filtered jobs run with a fake scraper and enricher, notices, persistence (incl. pre-1.2 jobs), log tagging |
| `test_logbuffer.py` | ring buffer semantics: cursor, limit/truncation, level filter, tracebacks, job-id tagging across asyncio tasks |
| `test_docs.py` | documentation consistency (see §7) |

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

Add the field to `Settings`, an entry to `.env.example`, and a row to §4 above.

## 7. Keeping docs in sync

Documentation drift is caught by `tests/test_docs.py`:

| Check | Fails when |
| --- | --- |
| required docs exist | a file in `docs/` is deleted or renamed |
| `DATA_MODEL.md` lists every CSV column | a column is added to `csv_export.py` |
| `API.md` lists every `/api` route | a route is added to `main.py` |
| `DEVELOPMENT.md` lists every setting | a field is added to `Settings` |
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
