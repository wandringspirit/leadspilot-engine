# Changelog

All notable changes to this project are documented here. The version lives in
`app/__init__.py`; `tests/test_docs.py` requires an entry for the current version.

## 1.2.0 – 2026-09-28

### Added
- **Business + place search**: the panel asks for a *Business type* (`plumbers`) and a *City / area*
  (`Austin, TX`) and searches Google Maps for `plumbers in Austin, TX`. The API takes `business` /
  `place` (a free-text `query` still works); the CLI takes them as two arguments.
- **Search filters**: *Website* (any / no website / has website) and *Must have* (phone, email, phone
  or email, phone and email). A Facebook, Instagram, Yelp, booking or retired `business.site` page
  does not count as a website (switchable). `filters` in `POST /api/scrape`, `--website` /
  `--contact` / `--profiles-are-websites` in the CLI.
- **The limit counts matching businesses**: a filtered search keeps scrolling Google's list until it
  has found that many matches, and skips opening the page of any listing the results feed already
  shows has a website. Ranks keep each business's position in Google's list.
- **Short-result notice**: when fewer businesses exist than requested, the job completes with what
  there is and says why – e.g. *"Only 35 businesses found for “plumbers” in Austin, TX — that's every
  listing Google Maps has for this search (you asked for 200)."* It distinguishes a complete list,
  Google's ~120-per-search cap, a stalled list and `MAX_SCANNED`. Shown as a banner above the results,
  in the job history (`35 of 200 found`), printed by the CLI and returned as `notice` / `found`.
- When a filter needs an email, the candidate's website or social page is searched while scanning;
  emails written in a listing's description are picked up for every search.
- `Website Type` column in the extended CSV (`none` / `own` / `profile`); rows with a profile page are
  tagged in the panel. `MAX_SCANNED` setting (default 1000). Job stats gain `matched`, `skipped`
  (by reason) and `stopped`.

### Changed
- Website enrichment reads only the profile page itself for Facebook / Yelp / booking … pages instead
  of crawling the platform's own `/contact`, `/about` pages.
- **No junk-email filtering**: every syntactically valid address found is kept (placeholder domains,
  `sentry.io`, `john@…` and similar are no longer dropped); scoring still lists the site's own
  addresses first. Only asset file names that look like addresses (`logo@2x.png`) are skipped.
- CSV download names include the filters (`plumbers_in_austin_tx_no_website_phone_or_email.csv`).
- Version bumped to 1.2.0.

## 1.1.0 – 2026-09-21

### Added
- **Live logs** section in the panel: streams the server log as it happens, with level filter,
  *This job only* filter, pause, clear, save-as-text, collapse (remembered), auto-scroll that
  pauses when you scroll up.
- `GET /api/logs` – ring-buffered log lines with a sequence cursor, level and job filters
  (`LOG_BUFFER_SIZE` setting, default 2000 lines).
- Log lines are tagged with the job that produced them (scraper worker tabs and enrichment
  fetches included).
- Far more informative log lines: feed progress, one line per scraped place, one line per
  website checked (emails / phones found, fallback to Chromium, failures), job summaries with
  timings.

### Changed
- Version bumped to 1.1.0.

## 1.0.0 – 2026-09-20

Initial release.

### Added
- Google Maps scraper (Playwright / Chromium): feed scrolling up to the requested count,
  per-place detail extraction from Google's XHR records with DOM fallback, single-result
  queries, EU consent handling, media blocking, proxy support.
- Output rows with Name, City, State, Country, Email, Website, Phone (national + E.164),
  Google Maps URL, Rank, Review Count, Description plus extended fields (address, category,
  rating, place id, coordinates, sources).
- Website enrichment: emails and phones from homepage / contact pages (mailto, tel, JSON-LD,
  Cloudflare obfuscation, `[at]`/`[dot]` patterns), junk filtering and scoring, Chromium
  fallback for JS-rendered or bot-protected sites, one output row per email address.
- Web panel: search form, live progress, results table with per-row and bulk *Find* buttons,
  filter, CSV download (default + extended), copy to clipboard, job history with delete.
- JSON API (FastAPI) with OpenAPI docs; jobs persisted as JSON and restored on restart;
  cancellation keeps partial rows.
- CLI (`cli.py`) for scripted runs.
- Test suite (unit, API, documentation consistency) and documentation with Mermaid diagrams.
