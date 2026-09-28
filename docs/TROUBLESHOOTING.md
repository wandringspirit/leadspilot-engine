# Troubleshooting

## Installation

| Problem | Fix |
| --- | --- |
| `playwright: command not found` / `Executable doesn't exist at …chrome-win…` | Run `python -m playwright install chromium` inside the activated virtual environment. |
| Linux: Chromium fails to start with missing `libnss3` / `libatk` … | `python -m playwright install-deps chromium` (needs sudo) |
| `ModuleNotFoundError: No module named 'app'` when running `cli.py` | Run from the project root (`cd google-maps-crawler`) with the venv activated. |
| `RuntimeError: no running event loop` | Routes in `app/main.py` must be `async def` (see DEVELOPMENT.md §6). |

## Scraping

| Symptom | Cause | What to do |
| --- | --- | --- |
| Job fails with *"Google Maps results feed did not appear (blocked, captcha or layout change?)"* | Google served a captcha / "unusual traffic" page, a consent page we could not dismiss, or the layout changed | Run the job with **Show the browser window** (or `HEADLESS=false`) and look. Captcha → wait 15–30 min, lower `DETAIL_CONCURRENCY`, use `PROXY_SERVER`. Layout change → [SCRAPING.md §6](SCRAPING.md#6-when-google-changes-its-layout). |
| Fewer results than requested, notice *"Only N businesses found … that's every listing"* (`stats.stopped = end_of_list`) | Google's list for that search really ended | Nothing is wrong – that is all Google Maps lists. Try a related business type or a nearby area. |
| Fewer results, notice *"Google Maps stopped listing … after 120 results"* | Google shows at most ~120 listings per search | Tick *Search the whole area* (`--whole-area`), or search several narrower places (`Katy, TX; Pasadena, TX`). |
| Whole-area search takes a long time | every map section is a separate Google search; busy areas are split into many sections | Expected. Lower the number of results, search a smaller place, or lower `MAX_SECTIONS`. Progress shows the current section; cancelling keeps what was found. |
| Notice *"Stopped after 100 map sections (MAX_SECTIONS setting)"* | the area needed more sections than allowed | Raise `MAX_SECTIONS` or search a smaller place. |
| Whole-area search never splits (log: *section 1 … N listing(s)* with no *splitting*) although results are few | Google shows the same ~120 whatever the zoom, or its layout changed | Check a section URL from the log with `--headed`; see SCRAPING.md §6. |
| Fewer results, notice *"stopped loading after N listings"* (`stopped = stalled`) | Feed stopped loading (slow network, throttling) | Retry; increase `SCROLL_PAUSE_MS` / `SCROLL_STALL_LIMIT`. |
| A *No website* search finds only a few businesses | most businesses in that category have a website; only the ~120 listings Google shows per search can be checked | Check the subtitle (`… listings scanned (skipped: …)`); search more areas. Untick *Facebook, Instagram … don't count as a website* only if you want to exclude businesses that just have a social page. |
| A filtered search is slow | *Must have: Email* (or *Phone and email*, or *Phone or email* for places without a phone) searches each candidate's website / social page while scanning; *No website* checks each candidate's email domain and social page for a website of its own (and runs web searches when ticked) | Expected; use *Phone or email* or *Phone* for speed, or lower `ENRICH_MAX_PAGES`. Untick *Also search the web …* for the fastest *No website* search. |
| A *No website* result has a website when you Google it | the listing on Google Maps doesn't show it, and none of the checks found it: its email is gmail & co., its social page doesn't link it (or showed a login page), the web search wasn't ticked or didn't find it, or the site doesn't show the phone number from Maps (or only renders with JavaScript) | Tick *Also search the web for a website they don't list on Maps* (`--search-web`). The log line *has a website Google Maps does not list: …* shows each business that was left out and how its site was found. See [ENRICHMENT.md §8](ENRICHMENT.md#8-websites-google-maps-does-not-list). |
| Skipped reason *has a website not listed on Maps* for a business you think has none | its email domain, a link on its social page or a web search result led to a site that shows its phone number | The log line *#N … has a website Google Maps does not list: <url> (found by …)* names the site; open it to check. |
| Notice *"Web search stopped after N searches …"* | DuckDuckGo and Bing both answered with a captcha / rate-limit page | The rest of the job still checks email domains and social pages. Raise `WEB_SEARCH_DELAY_S`, run smaller jobs, or wait before the next one. |
| Notice *"Web search did not work (the search engines could not be reached)"* | three searches in a row reached neither DuckDuckGo nor Bing – no internet, a firewall or proxy blocking them | Check that `html.duckduckgo.com` / `www.bing.com` open from that machine; the log shows the error of each attempt. |
| Results are from the wrong country / city | Google localises by IP when the query has no place | Put the place in the query (`… near houston`) or set `REGION_CODE`. |
| `City`/`State`/`Country` empty but `Address` empty too | Service-area business without a public address | Expected – nothing to parse. |
| Descriptions empty for every row | `/maps/preview/place` XHR not captured or index `[154]` moved | Check with `--headed`; see SCRAPING.md §3. |
| Rows with `enrichment_note: details page failed: …` | place page timed out repeatedly | re-run; increase `PAGE_TIMEOUT_MS`; check the network. |
| Very slow (10 s+ per place) | media not blocked, slow proxy, low CPU | keep `BLOCK_MEDIA=true`, lower `DETAIL_CONCURRENCY` on weak machines (contention makes it slower, not faster). |
| Browser window opens but stays blank on Windows | another process holds the Playwright browser profile | close stray `chrome.exe` processes started from `ms-playwright`. |

## Enrichment

| Symptom | Cause | What to do |
| --- | --- | --- |
| Row shows `error` with *HTTP 403* / *Server disconnected* | site blocks non-browser clients; if the Chromium fallback also failed the site is unreachable or has a hard bot wall | open the site manually; make sure `ENRICH_BROWSER_FALLBACK=true`. |
| `not_found` although the site clearly shows an email | email is an image, is injected after a user action, or lives on a page not in the candidate list | add the page's path to `STANDARD_PATHS` / a keyword to `CONTACT_KEYWORDS` in `extractors.py`. |
| Unwanted addresses (`you@example.com`, `…@sentry.io`, a plugin vendor) as extra rows | there is no junk-email filtering, by design – every valid address found is kept | They sort after the site's own addresses; delete them in the spreadsheet. The extended CSV's `Email Source` shows the page each came from. |
| A Facebook / Instagram page is marked `not_found` | the platform showed a login page instead of the business's details | Open the page yourself; nothing else to fetch there. |
| Phone found on the website differs from Maps | Maps phone is kept when present; website phones only fill empty cells | use the extended CSV (`Phone Source`) to see where it came from. |
| Enrichment button disabled | job still running, or every row with a website already has email + phone | wait, or use the API with `force: true`. |

## Panel

| Symptom | Fix |
| --- | --- |
| "API offline" badge | the server is not running or runs on another port; start `python run.py` and open the printed URL. |
| Old layout / buttons after upgrading | hard-refresh (Ctrl+F5). Assets are version-stamped and sent with `no-cache`, but a stale tab from before the upgrade keeps its scripts. |
| Live logs stay empty / stop updating | the view is *Paused* or collapsed, or the server restarted (the buffer lives in memory – it starts empty). Press *Resume*, expand the section, or reload the page. |
| Live logs show "older lines dropped" | more than `LOG_BUFFER_SIZE` lines arrived between two polls; raise the setting or use *Save* regularly on very large jobs. |
| Job shows `interrupted` | the server was restarted while it was running; the rows collected so far are kept – start a new job or enrich the partial result. |
| Copy CSV does nothing | the clipboard API needs a secure context (`http://localhost` is fine; a plain LAN IP is not) – use Download instead. |

## Where to look

- **Live logs** section at the bottom of the panel: the same lines as the server console, filterable by
  level and by job, with tracebacks for failures. `GET /api/logs?level=WARNING` gives them as JSON.
- Server log: the terminal running `run.py` (INFO level; every job failure logs a stack trace).
- Job files: `data/jobs/job_*.json` – contain the rows, progress, `stats.errors`.
- Playwright debugging: set `PWDEBUG=1` to open the inspector, or `DEBUG=pw:api` for verbose
  protocol logs.
