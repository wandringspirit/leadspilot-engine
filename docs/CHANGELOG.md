# Changelog

All notable changes to this project are documented here. The version lives in
`app/__init__.py`; `tests/test_docs.py` requires an entry for the current version.

## 1.23.0 – 2026-11-12

### Added
- **A daily backup in Google Drive** (`app/drive/backup.py`): after 03:00 the campaigns, the contacted list,
  business types, the autopilot, the activity log and the searches go into `Backups/leadspilot-backup-YYYY-MM-DD.zip`;
  the newest 7 are kept, older ones go to Drive's trash. Never the sign-ins or the preview websites. A *Backup* row
  in the master sheet's Status tab and the daily summary; restoring is in [CLOUD.md §10](CLOUD.md#10-a-new-cloud-server).

## 1.22.0 – 2026-11-12

### Added
- **The laptop watches the cloud server**: when it can't reach it for 30 minutes it emails you (*Cloud server
  not reachable*, with what to check), once, and *Cloud server reachable again* when it can. With the cloud
  server's *Laptop not searching* alert, each computer watches the other.

## 1.21.0 – 2026-11-12

### Added
- **A Status tab, first in the master sheet**: when it was written (an old time means the panel isn't
  running), sending and the next email, today's and yesterday's emails and what came back, campaigns, the
  autopilot, the laptop's last check-in, Google Drive, the last day's problems. Rows that need a look start
  with ⚠ and are red. Also `GET /api/master/status.csv`, and `Leads Pilot - status.csv` while the Sheets API is
  off. The daily summary email is built from the same rows.

## 1.20.1 – 2026-11-12

### Fixed
- **The laptop wrote its old copy over the master sheet.** As the search computer (`ROLE=searcher`) it still
  ran the Google Drive loop, so each time its panel started it saved its campaigns and activity log – a
  snapshot from before the move – to the master sheet, until the cloud server saved it again. Only the
  sender keeps the master sheet and the campaign results in Drive now; the laptop still uploads its
  searches' CSV files.

## 1.20.0 – 2026-11-12

### Added
- **Emails to you about problems** (`app/outreach/alerts.py`, [OUTREACH.md §8](OUTREACH.md#8-emails-to-you-about-the-panel-appoutreachalertspy)):
  the autopilot stopped (also on too many bounces), Google blocked a search, a campaign paused (not by you),
  an email refused, a campaign not made or not started, and on the cloud server the laptop silent for 24
  hours – sent together, at most every 30 minutes, each with what to do.
- **A daily summary email** at 08:00: yesterday's emails and what came back, campaigns, the next email, the
  autopilot, the laptop, Google Drive, the last day's problems.
- `ActivityLog.listeners`: told about each new entry.

### Changed
- **Reply alerts** (*[Leads Pilot] Reply from …*) go to `ALERT_TO` when it's set, like the other emails to you.

## 1.19.5 – 2026-11-12

### Added
- **The laptop hands its Google Drive sign-in to the cloud server** (`POST /api/remote/drive`): the cloud
  can't connect Drive itself (Google only sends the sign-in back to `127.0.0.1`). Connect in the laptop's
  panel; within a minute the cloud has it. When Google ends the sign-in, the cloud emails you how to connect
  again and the laptop's panel shows its *Connect Google Drive* banner; the laptop doesn't email too, and
  a sign-in Google ended is never handed back.

### Fixed
- The tests read this computer's `ROLE` / `CLOUD_URL` from `.env`: on the laptop (`ROLE=searcher`) a test
  run started the searcher loop against the real cloud panel. `tests/conftest.py` now clears them.

## 1.19.4 – 2026-11-11

### Fixed
- **Settings, Stop and Restart in the cloud panel** (`https://panel.leadspilot.co`, `TRUSTED_HOSTS`) said *only
  the panel opened on this computer can do this*: uvicorn took the Cloudflare Tunnel's `X-Forwarded-For` as the
  visitor's address. The panel now ignores forwarded headers (`run.py`, `proxy_headers=False`).

## 1.19.3 – 2026-11-11

### Added
- **`ALERT_TO`** (Settings › You, in the emails › *Alerts to*): where emails about the panel itself go – the
  *Google Drive disconnected* alert – instead of the campaigns' From address. Comma-separated; empty: as before.

## 1.19.2 – 2026-11-11

### Added
- **A banner when Google Drive disconnects.** When Google ends the Drive sign-in, every tab of the panel shows a
  red *Google Drive is disconnected* banner with a *Connect Google Drive* button, and the browser tab's title
  starts with *⚠ Google Drive disconnected* - visible even when the panel is a background tab. It stays until
  you connect again (or press *Disconnect*), also across restarts; `GET /api/drive` has `lost_at`.

## 1.19.1 – 2026-11-11

### Added
- **An email when Google Drive disconnects.** When Google ends the Drive sign-in (it expired or was revoked -
  every 7 days while the Google app is in *Testing*), you get *[Leads Pilot] Google Drive disconnected* at the
  campaigns' From address, and the activity log says so. Before, only the panel's Setup list and the log
  showed it, and searches and campaign results quietly stopped uploading.

## 1.19.0 – 2026-11-11

### Added
- **Sending from a cloud server, searching on this computer** ([CLOUD.md](CLOUD.md)). A new setting `ROLE`:
  `all` (as before: one computer does everything), `sender` (the cloud server - it sends, checks replies,
  runs the autopilot and the websites, and its searches wait for the search computer) or `searcher` (the
  laptop - it only runs the cloud server's searches and never sends). Google blocks searches from cloud
  servers, so they stay on the laptop; campaigns keep sending while it's closed.
  - The search computer asks for a search every minute (`POST /api/remote/next`), runs it under the same id,
    reports progress every 30 s and sends the result back; the cloud makes the campaign as if it had searched.
  - A search whose search computer goes quiet for 20 minutes (asleep, offline) ends *interrupted* and the
    autopilot searches the place again; a cancelled search stops on the laptop at its next report.
  - The remote routes need `SEARCHER_TOKEN` (`Authorization: Bearer`); the laptop can also send a Cloudflare
    Access service token (`CLOUD_ACCESS_ID` / `CLOUD_ACCESS_SECRET`).
  - `TRUSTED_HOSTS`: Settings, Stop and Restart also work in the cloud panel opened through Cloudflare Access.
  - `GET /api/remote` says who searches and when the other computer was last heard from.

## 1.18.4 – 2026-11-11

### Changed
- **A place whose autopilot search fails is skipped after three failures in a row** instead of two.

## 1.18.3 – 2026-11-11

### Fixed
- **A search that fails with an error is searched again** instead of the autopilot quietly moving on to the
  next place - for instance when the browser didn't survive the laptop sleeping. Once: a place whose search
  fails twice in a row is skipped, so one bad place can't use up every day's searches.

## 1.18.2 – 2026-11-11

### Fixed
- **Restart (or Stop) during a search no longer hangs the panel.** The log said *Shutting down*, but the
  process never ended, so the panel didn't come back: when the server's event loop ended, Python cancelled
  Playwright's own connection to the browser together with the search, and the search then waited forever
  for its browser to close. Running searches and email lookups now stop first, while the browser still
  answers (a search stopped this way is saved as *interrupted*, as after any restart), and a process still
  running 10 seconds after the panel has shut down is ended anyway.

## 1.18.1 – 2026-11-10

### Added
- **Quote requests reach the business straight away, laptop on or off.** The Worker posts a real request
  from a preview website's form to a Make.com webhook, whose scenario emails it to the business
  (`python sites/deploy.py --make-webhook` sets the webhook). Tests, businesses that bounced or unsubscribed,
  and any request Make didn't confirm (`sent`) still wait for the panel, so none is lost. A bounce or
  unsubscribe uploads the sites again, so that business's requests stop going to it.
- **Day by day charts** in the Autopilot view: the emails sent each day (first emails and follow-ups, with the
  daily limit) and what came back (replies, websites opened, bounces, unsubscribes), for 2 weeks, 30 days or
  everything, with a table view and a CSV (`/api/master/daily`, `daily.csv`).

### Changed
- **The preview websites' quote form is one form, everything visible**, instead of three steps. *Quick
  start* chips (It's leaking, Storm or hail damage, I need a new roof, Just want it checked) fill its *What do
  you need?* dropdown, and there's a new optional *Anything we should know?* box.
- **A campaign whose search left every business out now says "nobody to email"** instead of "draft", in the
  panel and on the master sheet (grey, with how many were left out), and it has no Start button. A draft you
  are still setting up keeps saying "draft".

## 1.18.0 – 2026-11-06

### Added
- **An email to you for every reply**, subject *[Leads Pilot] Reply from {business}*, with what they wrote and
  their phone number. It goes to the outreach mailbox, and one that can't go out is tried again after the next
  check. Replies that were already noted don't get one.

### Fixed
- **Later replies were invisible.** After a business's first reply the panel stopped looking at its emails, so
  the campaign and the sheet's Activity tab never showed the rest of the conversation. A business that replied
  is now watched for 45 days after it last wrote.

## 1.17.2 – 2026-11-06

### Changed
- The master sheet's **Refused** column is now **Bad address**. It counts addresses that couldn't take an email,
  and "Refused" read as if the business had said no.

### Fixed
- The preview websites' **Keep this site** link: an `&` in the business's name cut the email's subject short.
  Every site was rebuilt and deployed.

## 1.17.1 – 2026-11-06

### Changed
- The roofer websites' insurance answer: it now says the insurance company can tell the visitor what's covered,
  and that the roofer documents what they find for the visitor to send on. Rebuilt and deployed to the online sites.

## 1.17.0 – 2026-11-05

### Added
- **The quote form on the preview websites works.** It only pretended before: it said *We've got it* and
  sent nothing. A request now waits in the Worker's database until the panel emails it to the business,
  and the form shows the phone number when a request can't be saved. It also asks for an email address
  (optional).
- **A reply puts the website on hold** (no end date, no countdown), unless the reply says no. A button
  under the website link in a campaign, and `PATCH /api/sites/{slug}`, do it by hand.
- `python sites/build.py --rebuild` renders every site again with the current design.

### Changed
- The website emails' layout: the "yours to keep" sentence has its own paragraph, the signature carries the
  company name, and the footer has the address and the opt-out on two lines. The words are the same.

## 1.16.1 – 2026-11-05

### Fixed
- Switching the autopilot on after it stopped itself did nothing: the button said *on*, but it stayed
  stopped until it was switched off and on. Switching it on now clears the stop and starts the bounce
  count afresh.

## 1.16.0 – 2026-11-04

### Added
- **Address check**: just before a business's first email, an address whose domain doesn't exist or
  has no mail server is skipped (*Address skipped* in the activity log), so it can't bounce. When the
  lookup fails, the email goes out as usual.

### Fixed
- A delivery report saying an email is delayed was logged as an *Automatic reply*, or could count as
  a reply and stop the follow-ups when it had no automatic-reply header. It is now *Delivery delayed*
  and the follow-ups continue.

## 1.15.0 – 2026-10-30

### Added
- **Testing two versions of the emails.** A campaign or business type can have a second version
  (`steps_b`): the businesses take turns at their first email between A and B and keep theirs for
  the follow-ups. The activity log says which version went out, and the results file has a
  **Version** column. `SITE_STEPS_ASK_FIRST` is a version of the website emails without the link
  in the first email (it asks first; the second email sends it).

## 1.14.0 – 2026-10-25

### Added
- `start.bat --weekday-start`: a Windows Task Scheduler task starts the panel at 18:00 Monday to Friday
  unless it is already running (or as soon as the computer is awake after that), so a closed window
  doesn't cost a day of emails; `--no-weekday-start` removes it.
- The panel's log is also written to `data/logs/panel.log` (rotated at 2 MB), with how the panel
  stopped: the window closed, a normal stop, or an exit code.

## 1.13.1 – 2026-10-23

### Changed
- The website emails' first email says the website is free to keep: *If you like it, it's yours to
  keep at no cost.* (without the word *free*, which spam filters look for).

## 1.13.0 – 2026-10-20

### Added
- **Who opened their website.** The website Worker records each visit in a Cloudflare D1 database:
  page loads, a page that stayed open, taps on the phone number, email or *keep my website*. It
  keeps the visitor's network, city and country, and a hash of the IP address, never the address.
  The panel fetches them every 5 minutes and leaves out your own visits (your connection or your
  country), test copies and mail servers checking the link. *Website opened* and taps go into the
  activity log; the results file and the master sheet count them. Needs `SITES_VISITS_KEY`
  (`python sites/deploy.py --visits-key`).
- **Test copy** (`OUTREACH_TEST_COPY_TO`): each day's first email also goes to your own addresses,
  word for word, so you can see which tab it lands in.
- The settings say so when the Gmail login (`MAIL_USERNAME`) isn't an email address.
- The project keeps its own Cloudflare login in `data/wrangler` (`python sites/deploy.py --login`):
  logging in to another Cloudflare account for another project no longer stops the website uploads.

### Fixed
- The tests could start real Google Maps searches: app.main's autopilot read the real `data/`. The
  tests now run with a temporary `DATA_DIR`.

## 1.12.0 – 2026-10-16

### Added
- `start.bat --autostart` starts the panel when you sign in to Windows (minimised, no browser), so
  the emails and the autopilot run every day; `--no-autostart` undoes it.
- `KEEP_AWAKE` (on by default): Windows doesn't go to sleep while the panel runs.

### Fixed
- The preview websites couldn't be uploaded again (*PermissionError: [WinError 5] Access is
  denied*): a site folder copied from the read-only design folder couldn't be removed. Emptying
  `data/deploy/public` now clears the read-only flag first.

## 1.11.0 – 2026-10-15

### Changed
- The master sheet is a **Google Sheet** with a Campaigns tab and an Activity tab. It has a purple
  header that stays at the top, filter buttons, and rows coloured by status or by what happened. It
  needs the Google Sheets API switched on in the Google Cloud project. Until then it is the two CSV
  files, and the panel switches over by itself.

## 1.10.0 – 2026-10-15

### Added
- **Autopilot** ([AUTOPILOT.md](AUTOPILOT.md)) finds businesses and starts their campaigns by itself.
  It works through a list of places (20 in Texas to start) for one business type. When fewer than 10
  businesses are waiting for a first email, it searches the next place and makes and starts that
  place's campaign. It searches at most twice a day. It stops itself, pausing the campaigns, when 2 of
  the last 20 businesses emailed bounced, and it also stops when Google blocks a search. Off until you
  switch it on in the Outreach tab.
- **Activity log**: every search, campaign, email, reply and bounce, in the Outreach tab and
  `GET /api/activity`.
- **Master sheet** in Google Drive: `Leads Pilot - campaigns.csv` has a row per campaign (emailed,
  waiting, replied, bounced, emails sent …), and `Leads Pilot - activity.csv` holds the activity log.

## 1.9.0 – 2026-10-15

### Added
- **`{Website}`** in emails: the business's own preview website. A business type with a website design
  (Roofer) starts with emails that send it: *I made a website for {Business}* (day 0), a check-in
  (day 3), and *the preview comes down tomorrow* (day 6).
- `OUTREACH_UNSUBSCRIBE_HEADER` (off): the `List-Unsubscribe` header on campaign emails. In inbox
  tests the same email went to Promotions or Spam with it and to the inbox without it; the footer says
  to reply "no".

### Changed
- How long a preview website stays online follows the emails: 14 days after the first email with the
  link, 2 days after the last email, 30 days after a reply ([SITES.md](SITES.md#3-how-long-a-website-is-online-sitesdeploypy)).

## 1.8.0 – 2026-10-15

### Added
- **Preview websites for campaigns** ([SITES.md](SITES.md)): every business a new campaign emails gets
  its own website at `sites.leadspilot.co/<business>/`, and **its first email waits until the website
  is online** – the email says it is built. The panel builds them when the campaign is made and uploads
  them to Cloudflare by itself (again every 10 minutes while an upload fails).
  - A business without a city on Google gets the searched city; one more than 60 miles from it, or a
    supply shop, is left out with the reason; a rating under 4.0 or from fewer than 5 reviews stays
    off the page.
  - A website stays online 30 days after it is built, 21 days after the first email, 7 days after its
    link is sent; on hold, until you lift it.
  - The Outreach tab shows each business's website and, in the setup list, how many are online and
    any upload error. The results CSV has a **Website** column.
  - A campaign for a business type without a website design can't start (today only Roofer has one).
  - New settings `PREVIEW_SITES` and `SITES_URL`; new packages Jinja2 and Pillow.

## 1.7.1 – 2026-10-20

### Fixed
- `jinja2` and `Pillow` are now in `requirements.txt`. The website generator (`sites/render.py`)
  needs both, so on a fresh install it stopped with *No module named 'jinja2'* (then *'PIL'*).
  `start.bat` installs them on its next run, since `requirements.txt` changed.

## 1.7.0 – 2026-10-15

### Added
- **Website generator** (`sites/`): `python sites/render.py <trade pack> <lead>` builds a
  business's preview website in `data/sites/<lead>` from a design, a trade pack (its services,
  photos, roof check, FAQ …) and the lead's details. The first trade is roofers. The "sites"
  preview server shows them at <http://127.0.0.1:4173>.

### Changed
- The project is now called **Leads Pilot Engine** (GitHub: `wandringspirit/leadspilot-engine`,
  formerly *Google Maps Lead Crawler*). The panel, its start window and the desktop shortcut carry
  the new name. A shortcut made before keeps the old name; `start.bat --shortcut` makes the new one.

## 1.6.0 – 2026-09-30

### Added
- **One-click start** (Windows): double-click `start.bat` – or, after its first run, the
  **desktop shortcut** it makes. The first run creates `.venv` and installs the packages and
  Chromium; later runs only install again when `requirements.txt` changes. It starts the panel,
  opens it in the browser and keeps its window as the panel's console. Elsewhere: `python3 start.py`.
- **⏻ Stop** at the top right of the panel (and Ctrl+C in the start window) stops the panel. An
  email that is on its way is finished and written down first, so it never goes out twice. A page
  left open shows *The panel is stopped* and reconnects by itself when the panel runs again.
- **Settings tab**: every setting in groups (Mailbox, You in the emails, When emails go out, Google
  Drive, Searching, Finding emails, Panel, Advanced), each with a line of help. *Save* writes
  `.env` – keeping its comments and the other lines – and the panel uses the new values at once;
  address, port, data folder and log size after **Restart** (a button, when started with
  `start.bat`). Passwords are never sent to the browser. *Use Gmail's servers* fills in Gmail's
  SMTP / IMAP. What's still needed to send emails is marked, and counted on the tab.
- The *Outreach* Setup box's **Fill in ›** buttons open the settings a step still needs.
- **Email outreach** (new *Outreach* tab, [OUTREACH.md](OUTREACH.md)).
  - **Campaigns:** made from one or more searches or from a lead file in Google Drive. Each has a
    first email and up to 4 follow-ups, on the days you choose.
  - **Sending:** from your own mailbox over SMTP, only in a send window (default Mon–Fri
    08:00–12:00 America/Chicago), a few minutes apart, at most `OUTREACH_DAILY_LIMIT` a day.
  - **Replies:** the inbox is read over IMAP before every email. A reply, an unsubscribe (the
    `List-Unsubscribe` header) or a bounce stops that business's follow-ups; an out-of-office
    reply doesn't.
  - **Who is left out:** businesses with a website of their own, with only a BBB / placeholder
    address, or emailed before. Each is listed with the reason, and nobody is ever emailed twice
    (`data/contacted.json`).
  - **Before Start:** nothing is sent. Pause any time. The campaigns pause themselves when the
    mailbox refuses the login or an email.
- **Business types** (Roofer, Plumber, HVAC … – add your own). Each holds what the search form
  types into Google Maps, `{Trade}`, the Google Drive folder name, and its own emails.
  - **The search form's Business type:** now a list of your types plus *Other*. A search remembers
    its type, and its campaign starts with that type's emails.
  - **The email editor:** every email is a block you can open and close – the text on the left,
    the email filled in for a real business on the right, updated while you type. A ✓ in the
    header shows it is filled in; **!** marks an unknown placeholder, an unclosed `[[ … ]]` or a
    follow-up day out of order.
  - **Placeholders:** `{Business}`, `{City}`, `{Trade}`, `{Rating}`, `{Reviews}`, `{Sender…}`, and
    optional parts `[[ … ]]` / `[[A||B]]`. **✉ Send me this** sends any email to yourself as a
    test.
- **Google Drive** ([DRIVE.md](DRIVE.md)).
  - **Searches:** each search's CSV is uploaded to `DRIVE_FOLDER/<business type>/` when it
    finishes (again after an enrichment), and **⇪ Save to Drive** uploads it on demand.
  - **Lead files:** campaigns can run from a CSV / Google Sheet in that folder, in the crawler's
    format with at least `Name` and `Email` columns.
  - **Campaign results:** kept in `<business type>/Outreach/` and updated while the campaign runs.
  - **Sign-in:** with your own OAuth client (*Desktop app*); Google's answer comes back to the
    panel.
- **✉ Email these businesses** in the results card starts a campaign from the search.
- **[PLAYBOOK.md](PLAYBOOK.md):** setting everything up in order – mailbox, Google Drive, business
  types, search, campaign, every day. The *Outreach* tab's **Setup** box ticks the steps off and
  says what is still missing.
- New settings: `SMTP_*`, `IMAP_*`, `MAIL_USERNAME` / `MAIL_PASSWORD`, `SENDER_*`,
  `OUTREACH_*`, `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, `DRIVE_FOLDER`, `DRIVE_AUTO_UPLOAD`.
  `tzdata` is now a requirement (time zones on Windows).
- `JobSummary` has `email_count`, `business_type_id`, `drive_path` and `drive_link`. The search
  request takes `business_type_id`.
- New routes: `GET` / `PATCH /api/settings`, `POST /api/server/stop`, `POST /api/server/restart`
  (only for the panel opened on this computer); `/api/config` has `server`.

### Changed
- `run.py` no longer logs every HTTP request (the panel asks every few seconds); `--access-log`
  turns it back on.
- The *MAX_SECTIONS* notice points to the Settings tab.

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
