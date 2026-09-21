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
| Fewer results than requested, `stats.reached_end = true` | Google's list really ended (it pages to ~120 results at most) | Split the search into narrower areas or categories. |
| Fewer results, `reached_end = false` | Feed stopped loading (slow network) | Increase `SCROLL_PAUSE_MS` / `SCROLL_STALL_LIMIT`. |
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
| Wrong email (e.g. a plugin vendor) | junk filter missed a pattern | add the domain to `JUNK_DOMAIN_PARTS`; report it so the default list improves. |
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
