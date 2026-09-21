# Google Maps Lead Crawler

Search Google Maps for a place or keyword (for example `roofing companies near houston`),
collect the top *N* listings, look up missing **emails / phone numbers on each business
website**, and export everything as a spreadsheet-ready **CSV** — from a web panel or the
command line.

```
Name | City | State | Country | Email | Website | Phone | Phone Unformatted | Url | Rank | Review Count | Description
```

## Features

- **Web panel** – enter a query and the number of results, watch progress live, browse the
  rows in a table, download / copy the CSV, revisit previous jobs from the history list.
- **Google Maps scraping** with a real Chromium browser (Playwright): scrolls the results feed
  until the requested count is reached, then opens every listing to collect name, address
  (split into city / state / country), website, phone (formatted + E.164), rating, review
  count, category, coordinates, place id and the business description.
- **Email & phone enrichment** – a *Find* button per row (and one for the whole table) crawls
  the business website (homepage + contact/about pages, JS-rendered sites via Chromium) and
  fills the empty `Email` / `Phone` cells. When several emails are found the row is
  **duplicated once per email**, exactly like a spreadsheet lead list.
- **CSV export** in the column layout above (UTF-8 with BOM, opens cleanly in Excel / Google
  Sheets) plus an *extended* export with the extra fields.
- **Live logs** – a terminal-style section in the panel shows what the scraper and the email
  finder are doing as it happens (filter by level or by the current job, pause, clear, save).
- **CLI** for scripted or scheduled runs (`python cli.py "dentists in austin" --limit 50 --enrich`).
- **JSON API** (FastAPI, OpenAPI docs at `/docs`) so the crawler can be driven by other tools.
- Jobs are persisted on disk, survive restarts, can be cancelled, and partial results are kept.

## Quick start

Requirements: **Python 3.11+**, ~400 MB disk for Chromium.

1. Create and activate a virtual environment:

```bash
python -m venv .venv
```

```powershell
.venv\Scripts\Activate.ps1
```

(macOS / Linux: `source .venv/bin/activate`)

2. Install the Python dependencies and the Chromium build that Playwright drives:

```bash
pip install -r requirements.txt
```

```bash
python -m playwright install chromium
```

3. Start the panel and open <http://127.0.0.1:8000>:

```bash
python run.py
```

## Using the panel

1. **City / keyword** – anything you would type into Google Maps: `roofing companies near
   houston`, `dentists in austin tx`, `coffee shops downtown seattle`.
2. **Number of results** – how many places you want (1 – 500). Google stops paging at roughly
   120 results for a single query; split broad searches by area for more.
3. Optionally tick **Find emails & phones on websites after scraping** to run enrichment
   automatically, or press **✉ Find missing emails / phones** (whole table) / the row-level
   **✉ Find** button afterwards. Rows that gained extra emails appear indented (`↳`) under the
   original with the same rank.
4. **⬇ Download CSV** gives the 12-column file shown above; **Extended CSV** adds address,
   category, rating, place id, coordinates and where each email was found.

The **History** list keeps every job (stored as JSON under `data/jobs/`); click one to
reload its rows, `✕` deletes it.

**Live logs** (bottom of the page) streams the server log as it happens – which page is being
opened, how many results the feed has, what each website lookup found, and every warning or
error with its traceback. Use *Level* to show only warnings/errors, *This job only* to hide
lines from other jobs, *Pause* to stop the view from moving, *Clear* to empty it, and *Save* to
download the visible lines as a text file. Scrolling up pauses auto-scroll; scrolling back to
the bottom resumes it. The section can be collapsed with the ▾ button.

## Command line

```bash
python cli.py "roofing companies near houston" --limit 50 --enrich --out houston.csv
```

| Option | Meaning |
| --- | --- |
| `--limit / -n` | number of places (default 20) |
| `--enrich` | look up emails / phones on every website that is missing one |
| `--extended` | include the extra columns |
| `--headed` | show the browser window while scraping |
| `--out / -o` | CSV path (default `data/exports/<query>.csv`) |
| `--quiet / -q` | less output |

## Configuration

Copy `.env.example` to `.env` and adjust. The most useful switches:

| Variable | Default | Purpose |
| --- | --- | --- |
| `HEADLESS` | `true` | set `false` to watch Chromium work |
| `DETAIL_CONCURRENCY` | `3` | browser tabs used in parallel for place pages |
| `MAX_RESULTS` | `500` | hard cap per job |
| `REGION_CODE` | *(empty)* | Google `gl` parameter, e.g. `us`, to bias results to a country |
| `PROXY_SERVER` | *(empty)* | `http://user:pass@host:port` for all Maps traffic |
| `ENRICH_MAX_PAGES` | `6` | pages fetched per website when looking for contacts |
| `ENRICH_BROWSER_FALLBACK` | `true` | render JS-only / bot-blocking sites with Chromium |

Every setting is listed in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#configuration-reference).

## How it works (short version)

```mermaid
flowchart LR
    U[Panel / CLI] -->|query, limit| J[Job manager]
    J --> S[Maps scraper<br/>Playwright + Chromium]
    S -->|scroll feed, open places| G[(Google Maps)]
    S -->|rows| J
    J -->|websites with<br/>missing email/phone| E[Website enricher<br/>httpx + Chromium fallback]
    E -->|emails, phones| J
    J --> C[CSV export]
    J --> D[(data/jobs/*.json)]
```

The full design, diagrams and the data-flow are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Project layout

```
app/
  main.py              FastAPI app: JSON API + serves the panel
  jobs.py              background job manager, enrichment application, persistence
  logbuffer.py         ring buffer behind the Live logs section
  models.py            PlaceRow / Job pydantic models
  config.py            settings (env / .env)
  scraper/
    maps.py            Playwright Google Maps scraper
    records.py         decoder for Google's embedded place records
    parsing.py         address / phone / url normalisation helpers
  enrichment/
    website.py         website fetching strategy (httpx -> Chromium fallback)
    extractors.py      email / phone extraction & filtering
  export/csv_export.py CSV column layout and writer
  static/              the web panel (index.html, app.js, styles.css)
cli.py                 command line entry point
run.py                 starts the web server
tests/                 pytest suite (unit, API, docs-consistency)
docs/                  documentation (see below)
data/                  runtime data: jobs/, exports/ (git-ignored)
```

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/README.md](docs/README.md) | documentation index |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | components, data flow, job state machine, design decisions (with diagrams) |
| [docs/SCRAPING.md](docs/SCRAPING.md) | how the Google Maps scraping works, selectors, record index map, anti-bot notes |
| [docs/ENRICHMENT.md](docs/ENRICHMENT.md) | how emails / phones are discovered on websites and the duplicate-row rule |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | every output column, CSV format, parsing rules |
| [docs/API.md](docs/API.md) | REST API reference with examples |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | dev setup, configuration reference, testing, extending, keeping docs in sync |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | common problems and fixes |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | release history |

## Tests

```bash
python -m pytest -q
```

The suite is fully offline (no browser, no network). It includes documentation-consistency
tests that fail when a column, API route or setting is added without updating the docs.

## Limitations & responsible use

- Google Maps has no official scraping API; this tool automates a normal browser session.
  Layout or data changes on Google's side can break extraction — see
  [docs/SCRAPING.md](docs/SCRAPING.md) for how the fallbacks are layered and how to repair them.
- Keep volumes reasonable (a few hundred results per session, small delays are built in) and
  use a proxy if you run large batches. Heavy use can trigger Google's bot detection.
- Emails are collected from public business websites. Make sure your outreach complies with
  the laws that apply to you (CAN-SPAM, GDPR, PECR, …) and with Google's terms of service.
