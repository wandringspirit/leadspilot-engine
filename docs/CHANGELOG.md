# Changelog

All notable changes to this project are documented here. The version lives in
`app/__init__.py`; `tests/test_docs.py` requires an entry for the current version.

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
