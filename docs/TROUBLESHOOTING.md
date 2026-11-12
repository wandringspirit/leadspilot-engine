# Troubleshooting

## Installation

| Problem | Fix |
| --- | --- |
| Double-clicking `start.bat` says *Python 3.11 or newer is needed* | Install it from <https://www.python.org/downloads/>, tick *Add python.exe to PATH*, then double-click `start.bat` again. |
| *Installing failed* in the start window | usually no internet connection, or a proxy; the lines above it say which package. Fix that, then start again; `start.bat --reinstall` installs everything once more. |
| The desktop shortcut is missing, or does nothing after the folder moved | `start.bat --shortcut` (from a terminal in the project folder) makes it again. |
| *Restart* (or Stop) during a search: the log says *Shutting down* but the panel never comes back | fixed in 1.18.2: the search's browser kept the old process alive. Close the panel's window and start it again with the shortcut; the search shows *interrupted* - run it again. |
| *The websites couldn't be uploaded: PermissionError: [WinError 5] Access is denied* | fixed in 1.12.0: a read-only folder in `data/deploy/public` couldn't be emptied. Restart the panel. |
| `playwright: command not found` / `Executable doesn't exist at …chrome-win…` | Run `python -m playwright install chromium` inside the activated virtual environment (or `start.bat --reinstall`). |
| Linux: Chromium fails to start with missing `libnss3` / `libatk` … | `python -m playwright install-deps chromium` (needs sudo) |
| `ModuleNotFoundError: No module named 'app'` when running `cli.py` | Run from the project root (`cd leadspilot-engine`) with the venv activated. |
| `RuntimeError: no running event loop` | Routes in `app/main.py` must be `async def` (see DEVELOPMENT.md §6). |

## Scraping

| Symptom | Cause | What to do |
| --- | --- | --- |
| Job status **blocked**, banner *"Google Maps blocked this search"* (CLI: `error: Google Maps blocked the search …`, exit code 3) | too many searches from this IP address: Google answered with its "unusual traffic" check (a captcha) instead of Google Maps. The search stopped at once; what was found before is kept | Wait at least 30 minutes before searching again – searching straight away keeps the block going. Then run smaller searches (fewer results, a smaller area – a whole-area search runs many searches), lower `DETAIL_CONCURRENCY` / raise `MIN_DELAY_MS` and `MAX_DELAY_MS`, or set `PROXY_SERVER` to search from another IP address. The rows found can still be downloaded and enriched. |
| Job fails with *"Google Maps results feed did not appear (blocked, captcha or layout change?)"* | a page we don't recognise: a consent page we could not dismiss, a new kind of block page, or the layout changed (Google's usual captcha is reported as **blocked**, above) | Run the job with **Show the browser window** (or `HEADLESS=false`) and look. Layout change → [SCRAPING.md §6](SCRAPING.md#6-when-google-changes-its-layout). |
| Fewer results than requested, notice *"Only N businesses found … that's every listing"* (`stats.stopped = end_of_list`) | Google's list for that search really ended | Nothing is wrong – that is all Google Maps lists. Try a related business type or a nearby area. |
| Fewer results, notice *"Google Maps stopped listing … after 120 results"* | Google shows at most ~120 listings per search | Tick *Search the whole area* (`--whole-area`), or search several narrower places (`Katy, TX; Pasadena, TX`). |
| Whole-area search takes a long time | every map section is a separate Google search; busy areas are split into many sections | Expected. Lower the number of results, search a smaller place, or lower `MAX_SECTIONS`. Progress shows the current section; cancelling keeps what was found. |
| Notice *"Stopped after 200 map sections (MAX_SECTIONS setting)"* | the area needed more sections than allowed – every section is a Google search, and a big city can need thousands | Raise *Settings › Searching Google Maps › Most map sections per search* (more sections take longer and make a Google block more likely), or search smaller places – several suburbs separated by `;` get down to the small sections, where businesses without a website show up, much sooner. |
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

## Email outreach

| Symptom | Cause | What to do |
| --- | --- | --- |
| *Can't start yet: …* | something is missing – the message lists all of it (mailbox settings, `SENDER_ADDRESS`, `{Trade}`, a business without `{City}` …) | fix each item; settings in the Settings tab (*Fill in ›* in the Setup box takes you there), the rest in the campaign's emails / list |
| Campaign **paused** by itself: *"the mailbox refused the login (535 …)"* | wrong `MAIL_USERNAME` / `MAIL_PASSWORD`; Gmail and Google Workspace refuse your normal password | make an **app password** (Google Account › Security › 2-Step Verification › App passwords), put it in `MAIL_PASSWORD`, restart, *Resume* |
| Paused: *"the mail server refused the email: 550 5.7.x …"* / *"… sending limit …"* | the provider's spam filter or its daily sending limit | wait a day, lower `OUTREACH_DAILY_LIMIT`, check the email for spammy words / links; a new mailbox needs warming up |
| Nothing goes out, no error | outside the send window (Mailbox box: *Send window … open now*?), the daily limit is reached (*Today N of N*), the campaign isn't *running*, or `OUTREACH_AUTOSEND=false` | the Mailbox box shows the next email's time; the window is in `OUTREACH_TIMEZONE` – convert it to yours. The server has to run during the window |
| Mailbox box: *Replies checked … – failed* and nothing is sent | IMAP doesn't work (host, login, IMAP switched off in the mailbox) – without it replies can't be noticed, so no email goes out | check `IMAP_HOST` / `IMAP_PORT`, turn IMAP on in the mailbox settings; *↻ Check replies* shows the exact error |
| *can't find the mail server smtp.… – check SMTP_HOST* | typo in the host name, or no internet | fix `SMTP_HOST` / `IMAP_HOST` (e.g. `smtp.gmail.com`, `imap.gmail.com`) |
| A reply isn't recognised | it came from an address the business wasn't emailed at *and* doesn't answer one of the emails (a new email instead of *Reply*), or it landed in spam / another folder | *Leave out* the business by hand; set `IMAP_FOLDER` if replies are filtered into another folder |
| A business got the follow-up although it replied | the reply arrived after the last check (at most 2 minutes before each email) | rare by design; answer them – the follow-ups stop with the next check |
| *unknown time zone 'America/Chicago' … pip install tzdata* | Windows has no time-zone database | `pip install -r requirements.txt` (it includes `tzdata`) |
| A business is missing from a campaign | it was left out – *N businesses left out – why* under the list says why (website, BBB / placeholder address, emailed before, in another campaign) | expected; a business emailed before is never emailed again (`data/contacted.json`) |
| *✉ Send me this* says *Save first* | the test email is made from the saved text | press *Save*, then send the test |

## Google Drive

| Symptom | Cause | What to do |
| --- | --- | --- |
| Setup: *GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set* | no OAuth client yet | make one ([PLAYBOOK.md §2](PLAYBOOK.md#2-google-drive)), paste both into *Settings › Google Drive*, Save |
| *open the panel at http://127.0.0.1:8000 to connect Google Drive* | the panel was opened by another address (a LAN IP) – Google only sends the sign-in back to this computer | open <http://127.0.0.1:8000> on the computer running the server, connect there |
| Google shows *Error 400: redirect_uri_mismatch* | the OAuth client is not of type **Desktop app** | make a *Desktop app* client and use its ID / secret |
| Google shows *access_denied* / *app not verified* and stops | your account isn't a test user of the app, or you cancelled | add your account under *Audience › Test users*, or publish the app; on *"Google hasn't verified this app"* click *Advanced › Go to …* |
| *Google refused the sign-in (it expired or was revoked) – connect Google Drive again*, or the email *[Leads Pilot] Google Drive disconnected*, every week | the app is still in *Testing*: Google ends its sign-ins after 7 days | *Publish app* (In production) in the consent-screen settings, then connect again |
| *DRIVE_FOLDER: the folder … can't be opened* | the URL / id is wrong, or the account has no access to it | open the folder in Drive with the connected account, copy its URL again; you need edit access to upload |
| A search wasn't uploaded | Drive wasn't connected when it finished, `DRIVE_AUTO_UPLOAD=false`, or Google refused (the log says why) | *⇪ Save to Drive* in the results card |
| *the file has no Name and no Email column* | the lead file isn't in the crawler's CSV format | give it a header row with at least `Name` and `Email` ([DRIVE.md](DRIVE.md#lead-files)) |
| Files go to a new folder instead of your existing *Roofer* | `DRIVE_FOLDER` points somewhere else, or the business type's name differs from the folder's | set `DRIVE_FOLDER` to the folder that contains *Roofer* (empty = My Drive), or rename the business type |

## Panel

| Symptom | Fix |
| --- | --- |
| *The panel isn't running* over the page, or the "API offline" badge | the panel was stopped or its window closed. Double-click the desktop shortcut (or `start.bat`); the page reconnects by itself. `data/logs/panel.log` says when and how it stopped; `start.bat --weekday-start` starts it on weekdays by itself. |
| Settings tab: *Can't load the settings: Not Found* | the panel running is older than the page – started before an update. Stop it (close its window, or Ctrl+C in it) and start it again with the desktop shortcut. |
| Double-clicking the shortcut only opens the browser: *already running* | a panel runs already (its window may be minimised) – that is the one you get. To start fresh, stop it first (*⏻ Stop*). |
| *Another program uses port 8000* in the start window | something else listens on the port. Close it, or press E and set another `PORT` (or `start.bat --port 8001` once). |
| A setting can't be changed: *Set by the environment variable …* | that variable is set in Windows (*Edit environment variables for your account*) and wins over the Settings tab. Remove it there, then restart the panel. |
| *Saved – used after a restart* | the Panel group's settings (address, port, data folder, log size) are only read at start: press *Restart now*. |
| The panel froze while you selected text in its window | Windows pauses a program while text is being selected in its console window. Press Esc. (Started with `start.bat` / the shortcut, clicking the window doesn't select, so this can't happen.) |
| Old layout / buttons after upgrading | hard-refresh (Ctrl+F5). Assets are version-stamped and sent with `no-cache`, but a stale tab from before the upgrade keeps its scripts. |
| Live logs stay empty / stop updating | the view is *Paused* or collapsed, or the server restarted (the buffer lives in memory – it starts empty). Press *Resume*, expand the section, or reload the page. |
| Live logs show "older lines dropped" | more than `LOG_BUFFER_SIZE` lines arrived between two polls; raise the setting or use *Save* regularly on very large jobs. |
| Job shows `interrupted` | the server was restarted while it was running; the rows collected so far are kept – start a new job or enrich the partial result. |
| Job shows `blocked` | Google showed its "unusual traffic" check – see the red banner above the results and *Scraping* above. |
| Copy CSV does nothing | the clipboard API needs a secure context (`http://localhost` is fine; a plain LAN IP is not) – use Download instead. |

## Cloud server

The laptop (`ROLE=searcher`) says what's wrong in `GET /api/remote` and once in its log ([CLOUD.md](CLOUD.md)).

| Symptom | Cause | Fix |
| --- | --- | --- |
| *"CLOUD_URL is empty"* / *"SEARCHER_TOKEN is empty"* | the laptop's cloud settings aren't filled in | Settings › Cloud server |
| *"it asks for a login - set CLOUD_ACCESS_ID …"* | Cloudflare Access sent the laptop to its login page | create a service token in Cloudflare Zero Trust, allow it in the panel's Access application, put its ID and secret in the laptop's settings |
| *"it refused the SEARCHER_TOKEN"* (`401`) | the token differs between the two computers | paste the same one on both |
| *"it doesn't hand out searches"* (`404`) | the cloud server isn't `ROLE=sender`, or has no `SEARCHER_TOKEN`, or was not restarted after setting `ROLE` | fix its settings, restart it |
| *"can't be reached"* | no internet, the cloud server or its tunnel is down | `systemctl status leadspilot cloudflared` on the server |
| Searches stay *Waiting for the search computer* | the laptop is off, asleep, or its panel isn't `ROLE=searcher` | open the laptop; its panel starts with Windows |
| A search ends *"The search computer stopped reporting"* | the laptop slept or went offline mid-search for 20 minutes | nothing: the autopilot searches the place again (three failures in a row: it moves on) |
| The laptop logs *"the cloud server didn't take …"* | the search was cancelled there or given up on before the result arrived | nothing – it was searched again |
| The cloud's Google Drive stays disconnected after connecting on the laptop | the laptop's panel isn't running or can't reach the cloud, or one of them is older than 1.19.5 | start the laptop's panel; its log says *Google Drive sign-in (…) handed to the cloud server*; update both |
| The cloud panel's *Connect Google Drive* says *connect Google Drive in the laptop's panel* | Google only sends the sign-in back to `127.0.0.1` | connect on the laptop: it hands the sign-in over within a minute |
| Settings / Stop / Restart answer `403` in the cloud panel | its name isn't in `TRUSTED_HOSTS`, or the panel trusts forwarded headers (before 1.19.4) | add it (only a name behind Cloudflare Access); update the server (`git push cloud main`) |

## Where to look

- **Live logs** section at the bottom of the panel: the same lines as the server console, filterable by
  level and by job, with tracebacks for failures. `GET /api/logs?level=WARNING` gives them as JSON.
- Server log: the window the desktop shortcut / `start.bat` opened, or the terminal running `run.py`;
  also `data/logs/panel.log` (kept after the panel stops, with how it stopped)
  (INFO level; every job failure logs a stack trace).
- Job files: `data/jobs/job_*.json` – contain the rows, progress, `stats.errors`.
- Playwright debugging: set `PWDEBUG=1` to open the inspector, or `DEBUG=pw:api` for verbose
  protocol logs.
