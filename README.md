# Leads Pilot Engine

Search Google Maps for a type of business in a city (for example *plumbers* in *Austin, TX*),
keep only the businesses you want — e.g. **no website of their own, but a phone or an email** —
look up missing **emails / phone numbers on each business website**, export everything as a
spreadsheet-ready **CSV** (and to **Google Drive**, a folder per business type), and **email the
businesses** a first email and follow-ups from your own mailbox — from a web panel or the command
line.

**Setting it all up?** Follow the [playbook](docs/PLAYBOOK.md): mailbox → Google Drive → business
types → search → campaign, in that order.

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
- **Websites Google Maps doesn't show** – a listing only shows the website its owner entered, so
  a *No website* search also checks each business's email domain and the links on its Facebook /
  booking page, and – if you tick it – runs a web search for its name and phone number. A
  business whose own website turns up (verified by its phone number on that site) is left out.
- **Whole-area search** – a single Google Maps search lists only a city's ~120 most prominent
  businesses (nearly all with websites). Ticked by default, *Search the whole area* splits the map
  into sections, searches each one and splits busy sections further, so smaller businesses –
  the ones without a website – show up too. Several places can be searched at once
  (`Katy, TX; Pasadena, TX`); duplicates are dropped.
- **Honest short results** – ask for 200 when a city has 35: you get the 35 and a note saying
  that is every listing Google Maps has there (or that Google's ~120-per-search cap was hit, so
  there may be more).
- **A clear message when Google blocks you** – if Google answers with its "unusual traffic" check
  (a captcha) instead of Google Maps, the search stops at once (continuing would only prolong the
  block), keeps what it found, and the job shows as **blocked** with a red banner saying what
  happened and what to do.
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
- **Business types** – *Roofer*, *Plumber*, *HVAC* … (add your own): one choice in the search form
  sets what Google Maps is searched for, the Google Drive folder, and the emails a campaign for
  these businesses gets.
- **Email outreach** – campaigns from one or more searches or from a lead file in Drive: a first
  email and up to 4 follow-ups on the days you choose, per business type, edited in the panel
  with a live preview. Sent from your own mailbox (SMTP) in a send window, a few minutes apart,
  with a daily limit; any **reply, bounce or unsubscribe stops the follow-ups** (IMAP). Businesses
  with a website of their own, only a BBB / placeholder address, or emailed before are left out
  automatically. Nothing is sent before you press *Start*. See [docs/OUTREACH.md](docs/OUTREACH.md).
- **Preview websites** – every business a campaign emails gets its own website at
  `sites.leadspilot.co/<business>/`, and its first email waits until the website is online. See
  [docs/SITES.md](docs/SITES.md).
- **Autopilot** – searches the next place on a list and starts its campaign whenever the
  businesses to email run low; it stops itself on bounces or a Google block. A **master sheet** in
  Google Drive shows every campaign and everything that happened. See [docs/AUTOPILOT.md](docs/AUTOPILOT.md).
- **Google Drive** – each search's CSV lands in `<your folder>/<business type>/`, campaigns can
  run from a CSV / Google Sheet there, and each campaign's results are kept next to them. See
  [docs/DRIVE.md](docs/DRIVE.md).
- **CLI** for scripted or scheduled runs (`python cli.py dentists "Austin, TX" --website without -n 50`).
- **JSON API** (FastAPI, OpenAPI docs at `/docs`) so the crawler can be driven by other tools.
- Jobs are persisted on disk, survive restarts, can be cancelled, and partial results are kept.

## Quick start

Requirements: **Python 3.11+** ([python.org](https://www.python.org/downloads/) – tick *Add python.exe
to PATH* while installing), ~400 MB disk for Chromium.

**Windows, one click:** double-click **`start.bat`** in the project folder. The first time it sets
everything up – a virtual environment, the Python packages and Chromium, a few minutes – and puts a
**Leads Pilot Engine** shortcut on your desktop. From then on double-click that shortcut: the
panel opens in your browser.

The window that opens with it is the panel's console: the log shows there, and it has to stay open
while campaigns are sending (minimise it). To stop the panel press **⏻ Stop** at the top right of the
panel, or **Ctrl+C** in that window. When `requirements.txt` changes (after an update), the next start
installs what's new by itself.

**macOS / Linux:** `python3 start.py` does the same, without the shortcut.

| `start.bat` / `start.py` option | What it does |
| --- | --- |
| `--port 8001` | use another port this time (for good: *Settings › Panel › Port*) |
| `--no-browser` | don't open the panel in the browser |
| `--reinstall` | install the packages and Chromium again |
| `--shortcut` | make the desktop shortcut again (e.g. after moving the folder) |
| `--autostart` | start the panel when you sign in to Windows (minimised, no browser); `--no-autostart` undoes it |

### Manual setup

The same by hand, on any system:

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

The panel has three tabs: **Search** (below), **Outreach** (email campaigns, business types and
their emails, the mailbox and Google Drive – see [the playbook](docs/PLAYBOOK.md)) and **Settings**
(every setting, explained – see [Configuration](#configuration)).

1. **Business type** – pick a saved type (*Roofer*, *Plumber* …): it fills in what to search for,
   and the search's campaign and Drive folder follow from it. *Other* lets you type any search
   (`hair salon`, `dentist`).
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
     website; untick the box under the buttons to count them. With *No website*, a business is
     also left out when its email address or social page leads to a website of its own that
     Google Maps doesn't show; tick **Also search the web for a website they don't list on Maps**
     to look each one up with a web search too (slower: a few seconds per business; uses
     DuckDuckGo / Bing, never Google).
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
   category, website type, rating, place id, coordinates and where each email was found. With
   Google Drive connected, the CSV is also uploaded to the business type's folder when the search
   finishes (**⇪ Save to Drive** uploads it again).
9. **✉ Email these businesses** starts a campaign for them in the *Outreach* tab: check who is on
   the list, check the emails (a live preview for each business), send yourself a test, *Start*.

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
python cli.py roofing "Houston, TX" --website without --contact phone --search-web --limit 20
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
| `--search-web` | with `--website without`: also search the web for a website a business doesn't list on Google Maps (slower) |
| `--enrich` | look up emails / phones on every website that is missing one |
| `--extended` | include the extra columns |
| `--headed` | show the browser window while scraping |
| `--out / -o` | CSV path (default `data/exports/<query>.csv`) |
| `--quiet / -q` | less output |

Exit codes: `0` done, `2` bad arguments, `3` Google blocked the search with its "unusual traffic"
check – the CSV still holds what was found before, and the error line says what to do.

## Configuration

Everything is in the panel's **Settings** tab, grouped and explained. Saving writes the `.env` file in
the project folder, and the panel uses the new values at once – except its own address, port, data
folder and log size, which need *Restart* (in the tab). You can also edit `.env` yourself
(`.env.example` shows the format) and restart; an environment variable of your computer wins over
both. The most useful switches:

| Variable | Default | Purpose |
| --- | --- | --- |
| `HEADLESS` | `true` | set `false` to watch Chromium work |
| `DETAIL_CONCURRENCY` | `3` | browser tabs used in parallel for place pages |
| `MAX_RESULTS` | `500` | hard cap per job |
| `REGION_CODE` | *(empty)* | Google `gl` parameter, e.g. `us`, to bias results to a country |
| `PROXY_SERVER` | *(empty)* | `http://user:pass@host:port` for all Maps traffic |
| `ENRICH_MAX_PAGES` | `6` | pages fetched per website when looking for contacts |
| `ENRICH_BROWSER_FALLBACK` | `true` | render JS-only / bot-blocking sites with Chromium |
| `WEB_SEARCH_DELAY_S` | `3.0` | pause between web searches (*Also search the web …*) |
| `SMTP_HOST` / `IMAP_HOST` / `MAIL_USERNAME` / `MAIL_PASSWORD` | *(empty)* | your mailbox, for email campaigns ([playbook §1](docs/PLAYBOOK.md#1-your-mailbox-emails-go-out-from-it-replies-come-back-to-it)) |
| `SENDER_NAME` / `SENDER_ADDRESS` | *(empty)* | who the emails are from; your postal address goes in every footer |
| `OUTREACH_HOURS` / `OUTREACH_DAILY_LIMIT` | `8-12` / `30` | when emails go out (in `OUTREACH_TIMEZONE`) and how many a day |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `DRIVE_FOLDER` | *(empty)* | Google Drive ([playbook §2](docs/PLAYBOOK.md#2-google-drive)) |

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
  settings_editor.py   the Settings tab: every setting's group and help, reading and writing .env
  control.py           the panel's Stop / Restart buttons
  scraper/
    maps.py            Playwright Google Maps scraper
    area.py            map sections for whole-area searches
    records.py         decoder for Google's embedded place records
    parsing.py         address / phone / url normalisation helpers
  enrichment/
    website.py         website fetching strategy (httpx -> Chromium fallback)
    extractors.py      email / phone extraction & filtering
    sitefinder.py      finds websites a Google Maps listing does not show
    websearch.py       DuckDuckGo / Bing web search (never Google)
  export/csv_export.py CSV column layout and writer (csv_import.py reads lead files back)
  outreach/            email campaigns: business types, templates, leads, send window, mailbox, send loop
  drive/               Google Drive: sign-in, folders per business type, uploads, lead files
  static/              the web panel (index.html, app.js, outreach.js, settings.js, styles.css, icon.ico)
cli.py                 command line entry point
start.bat, start.py    one-click start: sets up what's missing, starts the panel, opens the browser
run.py                 starts the web server
tests/                 pytest suite (unit, API, docs-consistency)
docs/                  documentation (see below)
data/                  runtime data: jobs/, exports/ (git-ignored)
```

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/PLAYBOOK.md](docs/PLAYBOOK.md) | **setting everything up, in order**: mailbox, Google Drive, business types, search, campaign, every day |
| [docs/README.md](docs/README.md) | documentation index |
| [docs/OUTREACH.md](docs/OUTREACH.md) | email campaigns: business types, templates, who gets emailed, when, replies and bounces |
| [docs/AUTOPILOT.md](docs/AUTOPILOT.md) | the autopilot (searches and campaigns by themselves) and the master sheet |
| [docs/SITES.md](docs/SITES.md) | preview websites: how each business's site is made, who gets one, how long it stays online |
| [docs/DRIVE.md](docs/DRIVE.md) | Google Drive: folders per business type, uploads, lead files, signing in |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | components, data flow, job state machine, design decisions (with diagrams) |
| [docs/SCRAPING.md](docs/SCRAPING.md) | how the Google Maps scraping works, selectors, record index map, anti-bot notes |
| [docs/ENRICHMENT.md](docs/ENRICHMENT.md) | how emails / phones are discovered on websites, the duplicate-row rule, and how websites a listing doesn't show are found |
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
  the laws that apply to you (CAN-SPAM, GDPR, PECR, CASL …) and with Google's terms of service. The
  campaigns put your postal address and an opt-out line in every email and never email an
  address twice after it answered, bounced or unsubscribed ([docs/OUTREACH.md](docs/OUTREACH.md#7-staying-on-the-right-side-of-the-rules)).
