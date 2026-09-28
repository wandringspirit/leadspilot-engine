# Google Maps Lead Crawler

Search Google Maps for a type of business in a city (for example *plumbers* in *Austin, TX*),
keep only the businesses you want — e.g. **no website of their own, but a phone or an email** —
look up missing **emails / phone numbers on each business website**, and export everything as a
spreadsheet-ready **CSV** — from a web panel or the command line.

```
Name | City | State | Country | Email | Website | Phone | Phone Unformatted | Url | Rank | Review Count | Description
```

## Features

- **Web panel** – enter a business type, a city and the number of results, watch progress live,
  browse the rows in a table, download / copy the CSV, revisit previous jobs from the history list.
- **Filters** – *Website*: any / **no website** / has website; *Must have*: phone, email,
  **phone or email**, phone and email. A Facebook, Instagram, Yelp, booking or retired
  `business.site` page does not count as a website (switchable). The number of results counts
  *matching* businesses: the crawler keeps going down Google's list until it has that many, and
  never opens the page of a listing the results already show has a website.
- **Whole-area search** – a single Google Maps search lists only a city's ~120 most prominent
  businesses (nearly all with websites). Ticked by default, *Search the whole area* splits the map
  into sections, searches each one and splits busy sections further, so smaller businesses –
  the ones without a website – show up too. Several places can be searched at once
  (`Katy, TX; Pasadena, TX`); duplicates are dropped.
- **Honest short results** – ask for 200 when a city has 35: you get the 35 and a note saying
  that is every listing Google Maps has there (or that Google's ~120-per-search cap was hit, so
  there may be more).
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
- **CLI** for scripted or scheduled runs (`python cli.py dentists "Austin, TX" --website without -n 50`).
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

1. **Business type** – what you are looking for: `plumbers`, `hair salon`, `dentist`.
2. **City / area** – where: `Austin, TX`, `Manchester`, `downtown Seattle`; several places
   separated by `;`. The panel shows the search it will run.
3. **Search the whole area, not just Google's top ~120** (ticked by default, remembered) – searches
   the place's map section by section instead of once. Slower – every map section is one more
   Google search, and a big city can need dozens – but it reaches the smaller businesses a
   city-wide search never lists. Untick it for a quick look at the top results.
4. **Number of results** – how many *matching* businesses you want (1 – 500). The search stops as
   soon as it has that many.
5. **Filters** (remembered for next time):
   - **Website** – *Any*, **No website** (only businesses without a website of their own) or
     *Has website*. By default a Facebook / Instagram / Yelp / booking page doesn't count as a
     website; untick the box under the buttons to count them.
   - **Must have** – *Phone or email* keeps businesses you can reach; *Phone*, *Email* and
     *Phone and email* are stricter. Google Maps never shows emails, so an email comes from the
     listing's description or, while scanning, from the business's website / social page.
6. Optionally tick **Afterwards, look for emails & phones on websites / social pages** to run
   enrichment automatically, or press **✉ Find missing emails / phones** (whole table) / the
   row-level **✉ Find** button afterwards. Rows that gained extra emails appear indented (`↳`)
   under the original with the same rank.
7. If fewer businesses exist than you asked for, a note above the table says so — e.g. *"Only 35
   businesses found for “plumbers” in Austin, TX — that's every listing Google Maps has for this
   search (you asked for 200)."*
8. **⬇ Download CSV** gives the 12-column file shown above; **Extended CSV** adds address,
   category, website type, rating, place id, coordinates and where each email was found.

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
python cli.py roofing "Houston, TX" --whole-area --website without --contact phone_or_email --limit 50
python cli.py plumbers "Katy, TX; Pasadena, TX" --limit 100
python cli.py "roofing companies near houston" --limit 50 --enrich --out houston.csv
```

The first argument is the business type, the second the place – several separated by `;` (leave
it out to pass a whole search as one argument).

| Option | Meaning |
| --- | --- |
| `--limit / -n` | number of matching places (default 20) |
| `--whole-area` | search each place section by section, not just Google's top ~120 (slower, finds more) |
| `--website` | `any` (default), `without` (no website of their own) or `with` |
| `--contact` | `any` (default), `phone`, `email`, `phone_or_email` or `phone_and_email` |
| `--profiles-are-websites` | count a Facebook / Instagram / Yelp / booking page as a website |
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
    U[Panel / CLI] -->|business, place,<br/>limit, filters| J[Job manager]
    J --> S[Maps scraper<br/>Playwright + Chromium]
    S -->|scroll feed, open places<br/>until enough match| G[(Google Maps)]
    S -->|matching rows| J
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
  filters.py           search filters (website / contact rules)
  config.py            settings (env / .env)
  scraper/
    maps.py            Playwright Google Maps scraper
    area.py            map sections for whole-area searches
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
- Google Maps lists at most about 120 businesses per search, most prominent first. The
  whole-area search gets past that by searching map sections; it relies on Google keeping a
  zoomed-in search to the area on screen (see [docs/SCRAPING.md](docs/SCRAPING.md)).
- Google Maps does not show email addresses. For businesses without a website an email can only
  come from their listing's description or their social / booking page, so most of them will
  have a phone but no email.
- Emails are collected from public business websites. Make sure your outreach complies with
  the laws that apply to you (CAN-SPAM, GDPR, PECR, …) and with Google's terms of service.
