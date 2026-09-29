# Changelog

All notable changes to this project are documented here. The version lives in
`app/__init__.py`; `tests/test_docs.py` requires an entry for the current version.

## 1.5.2 – 2026-09-29

### Changed
- CSV download names from the panel end with the date the search was run (`DD_MM_YYYY`), e.g.
  `roofing_in_houston_no_website_email_29_09_2026.csv`, so downloads of the same search from different
  days are easy to tell apart.

## 1.5.1 – 2026-09-28

### Changed
- `MAX_SECTIONS` default raised from 100 to 200 map sections per job. A whole-area search of a big
  city (Houston) ran into the cap after about an hour with fewer matches than asked for; a job
  can now search twice as many sections (and take about twice as long).
- The log warning and the short-result notice for the cap now say where to change it (the `.env`
  file, then restart the server).

## 1.5.0 – 2026-09-28

### Added
- **A clear error when Google blocks the scraper.** When Google answers with its "unusual traffic"
  check (the `google.com/sorry/` captcha page, HTTP 429, or the harder *"We're sorry … automated
  queries"* page) instead of Google Maps, the job now ends with the new status **`blocked`** and an
  error that says what happened, which IP address Google named, how many businesses were found
  before, and what to do (wait at least 30 minutes; smaller searches; lower `DETAIL_CONCURRENCY`;
  `PROXY_SERVER`). The panel shows it as a red banner above the results, a red *blocked* pill and
  toast, and *blocked · 12 of 50 found* in the history; the CLI prints it as an `error:` line and
  exits with code 3 after writing the CSV. Job stats gain `blocked_ip`; `stopped` can be `blocked`.

### Changed
- On a block the scrape **stops at once and keeps what it found**. Before, the job failed after the
  page timeout with *"results feed did not appear (blocked, captcha or layout change?)"*; a
  whole-area search tried more map sections (splitting the blocked ones) before failing; and a
  blocked place page was retried and then kept with only the results-list data. Now nothing more
  is requested: no retries, no further searches or sections, the other place-page tabs are stopped.
  The block is recognised as soon as the page opens, not after the 30-second wait.
- A results list that stops loading because Google answers its requests with HTTP 429 counts as
  blocked (the listings already collected are still opened).
- Enrichment still runs on a blocked job's rows (it reads the businesses' own websites).

## 1.4.0 – 2026-09-28

### Added
- **Websites Google Maps doesn't show.** A listing only shows the website its owner entered, so a
  *No website* search could return businesses that do have a site (the listing shows nothing or a
  booking page; a Google search finds `roofing4texas.com`). Every place that passes a *No website*
  search is now checked before it is kept:
  - **email domain** – an address on a domain of its own (not gmail & co.) whose site shows the
    business's phone number or is named after the business;
  - **profile page** – a link on its Facebook / BBB / booking page to a site that shows its phone
    number;
  - **web search** (optional: *Also search the web for a website they don't list on Maps* in the
    panel, `search_web` in the API, `--search-web` in the CLI) – its name and city, then its phone
    number, on DuckDuckGo with Bing as the fallback (never Google, so the Maps scraping IP is not
    put at risk); a result counts when its site shows the business's phone number.

  A site belongs to the business when its home page (or first contact pages) shows the phone
  number from Maps among at most 5 numbers, so directory pages about the business don't count.
  A business with such a site is skipped as *has a website not listed on Maps*; the log names the
  site and the check that found it.
- Job stats gain `unlisted_websites` (count per check) and `web_searches`; the panel shows a
  *web search* chip on the job and the number of web searches in the results summary. Web search
  switches itself off for the rest of a job when both engines refuse (captcha / rate limit) or
  three searches in a row reach neither, and the job notice says so.
- Settings `WEB_SEARCH_ENGINE` (`duckduckgo` / `bing`, default `duckduckgo`) and
  `WEB_SEARCH_DELAY_S` (default 3 s between web searches).
- The page extractor lists a page's links to other sites (websites named in the page's data
  first; Facebook / Yelp / Google / DuckDuckGo / Bing redirect links unwrapped).

### Changed
- In a *No website* search the profile page of every place that would be kept is read (for its
  links), and the emails / phone it shows are written into the rows; the automatic enrichment
  does not fetch it again.

## 1.3.0 – 2026-09-28

### Added
- **Search the whole area** (on by default in the panel; `whole_area` in the API, `--whole-area` in
  the CLI). A single Google Maps search only lists a city's ~120 most prominent businesses – nearly
  all with websites – so the crawler now splits the map Google shows for the place into sections,
  searches each one, and splits every section whose list is still full into four smaller ones
  (down to ~2 km), until the lists run out. Smaller, less prominent businesses – typically the
  ones without a website – show up this way. Places found by several sections are kept once.
- **Several places in one search**: separate them with `;` (`Katy, TX; Pasadena, TX`); each place
  is searched in turn (or section by section with *whole area*), duplicates are dropped.
- `MAX_SECTIONS` setting (default 100 map sections per job); job stats gain `searches`, `sections`,
  `full_lists` and `dense`; new stop reasons `searched_all` and `section_limit`, each with its own
  short-result notice (*"Searched all of Houston, TX for “roofing” (37 map sections): 812 businesses
  listed, 35 of them match your filters …"*).
- The panel shows the running section in the progress line, a *whole area* chip on the job and in
  the history, and the number of map sections in the results summary.

### Changed
- `MAX_SCANNED` default raised from 1000 to 5000 (a whole-area search reads many more listings;
  most are skipped without opening their page).
- With several searches or a whole-area search, `Rank` is the order in which places were found.
- A search that fails inside a multi-search job is logged and skipped (a failed map section is
  searched again as four smaller ones); three failures in a row still fail the job.
- The notice for a search that hit Google's ~120 cap now suggests searching the whole area.

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
