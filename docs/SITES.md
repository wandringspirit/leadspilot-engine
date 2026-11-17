# Preview websites

Every business a campaign emails gets a **preview website** at `https://sites.leadspilot.co/<slug>/`
(`SITES_URL`), made from its Google Maps details: its name, city, phone, rating, reviews link and the
towns around it. The first email says the website is built, so **a business's first email waits until
its website is online**. When the business wants to keep it, you take over (the *Keep this site* button
emails `piyush@leadspilot.co`).

```mermaid
flowchart LR
    S[Search] --> C[+ New campaign]
    C -->|sites/build.py| B["data/sites/&lt;slug&gt;/<br/>data/sites/sites.json"]
    B -->|sites/deploy.py + wrangler| W[Worker leadspilot-sites<br/>sites.leadspilot.co]
    W --> E[the business's first email goes out]
```

## 1. What a website is made of

| Part | Where | What |
| --- | --- | --- |
| design | `sites/designs/<design>/` | `index.html.j2`, `site.css`, `site.js` – the page, its look and animations (`bold`) |
| trade pack | `sites/trades/<trade>.json` | the words, services, photos, colours, FAQ and roof check of one trade; named after the business type (*Roofer* → `roofer.json`) |
| photos | `sites/assets/<trade>/` | resized once per size into `data/sites/.image-cache`, then copied into each site |
| town list | `sites/geo/places.tsv` | every US town with its location, from the Census Bureau (`sites/geo/make_places.py`) |
| the business | its search row | name, city, state, phone, email, rating, reviews, place id, location |

A business type without a trade pack has **no website design yet**: a campaign for it can't start
(*there's no website design for HVAC yet …*). **Roofer** and **Plumber** have one. The Roofer design is
locked (git tag `roofer-design-v1`): everything the Plumber pack changes is an optional pack field whose
default is the Roofer's text, so a roofer's page comes out byte for byte the same.

The **Plumber** pack (`sites/trades/plumber.json`) has its own moving parts:

- `"hero_fx": "drips"` – water drops fall over the hero photo and the closing band, speed up and ring
  out where they land (instead of the Roofer's rain and lightning);
- an explorer drawn instead of photographed (`"svg"`): a house cut open at night with its cold and hot
  pipes and drains, water moving through every pipe, the shower running, a dripping kitchen faucet and
  the water heater's flame – `sites/assets/plumber/make_house.py` draws it, and the pins' x / y are
  percent of its view box;
- its own words where the Roofer's were built in: `schema_type`, `logo_icon` + `symbols` (icons),
  `nav_cta`, `form_title`, `mbar_cta`, `services_title`, `process_title`, `work_title`, the explorer's
  `part` / `go` / `legend`, and `quote_for` (which quote option each service picks).

Plumbers get the **Flow** design (`sites/designs/flow/`, "website B"): the Bold design with water running
through it –

- the headline's coloured line drawn in outline and filling with moving water; ripples under the mouse
  and where you tap on the hero photo; the line under the headline on frosted glass;
- **"What's happening at your place?"** (the pack's `helper`): four problems, each with what to do in the
  next five minutes, a safety note, *Call* and *Book a visit* (which picks the problem in the quote form);
- a **drip calculator** (`drip_calc`): drips a minute → gallons a year and showers' worth, from the EPA's
  WaterSense figures (a drip a second is over 3,000 gallons a year; a shower about 17 gallons), with a
  tap dripping into a jug at that pace;
- the rating as a tank of water filled to it, moving waves between the dark and light sections, the
  "how it works" line as a pipe with water in it; the header's links switch to the menu button below
  1240px instead of wrapping.

A pack can build on another: `"extends": "plumber.json"` starts from that pack and its own keys win
(`sites/render.py: load_pack`). `sites/trades/plumber-a.json` is the plumber page on the Bold design
("website A"), kept to compare – no business type is called "Plumber A", so nothing sends it.

Samples: `python sites/render.py sites/trades/plumber.json sites/samples/plumber-houston.json` (B), and
the same with `plumber-a.json` (A).

HVAC businesses get the **Air** design (`sites/designs/air/`, pack `sites/trades/hvac.json`): its own look
(icy-blue paper, Bricolage Grotesque headings) on the shared parts of Bold –

- a **thermostat** in the hero (the pack's `dial`): drag it round, use the arrow keys or the snowflake /
  flame buttons. Like a real one it cools or heats until the room reaches the setting (an *Inside now*
  reading on the photo counts there one degree at a time), then holds. The setting is the page's
  temperature (`--t`, 0 cool – 1 warm; `--temp` runs icy blue – mint – warm orange): the headline's
  coloured line, the arc, the air and the photo follow it – frost creeps in from the photo's edges when
  it's cold, and it fades into a warm living room (`dial.warm_photo`) when it's hot. Once, on arrival, it
  shows itself: a cold snap, a warm spell, back to 72°;
- air streaming across the hero and the closing band (`data-fx="air"`): sinking while it cools, rising
  while it heats, pushed aside by the mouse, a gust where you tap; snowflakes when it's cold, embers when
  it's hot;
- the **system check** drawing (`sites/assets/hvac/make_house.py` → `house-check.svg`): supply and return
  air moving in the ducts, puffs from the vents, the outdoor unit's fan turning, the furnace flame;
- **"What's your system doing?"** (`helper`, as the plumber's);
- **"Is your old AC costing you?"** (`energy_calc`): what cooling adds to the summer bill × (1 − old SEER
  / 16), the old SEER being the federal minimum when the system was put in (10 before 2006, 13 from 2006,
  14 in the South from 2015); the page says it's a rough estimate and how it's worked out. Two outdoor
  units side by side, the old one's fan spinning harder;
- the rating as a gauge whose needle swings to it.

Sample: `python sites/render.py sites/trades/hvac.json sites/samples/hvac-phoenix.json`.

Electricians get the **Spark** design (`sites/designs/spark/`, pack `sites/trades/electrician.json`), built
from Air with its own look (graphite and volt, Space Grotesk headings) –

- a **wall switch and a dimmer** in the hero (the pack's `switch`): the page arrives dark and the lights
  come on with one fluorescent flicker; flip the switch or slide the dimmer and the kitchen photo, the
  headline's neon line and the glow follow (`--b`, 0 off – 1). The photo's reading shows the watts its
  LED bulbs draw at that level;
- current running along circuit traces across the hero and the closing band (`data-fx="spark"`): near
  the mouse an arc jumps to the nearest trace, a tap throws sparks; off, the traces go dim;
- the **power check** drawing (`sites/assets/electrician/make_house.py` → `house-check.svg`): current from
  the pole through the meter (its disc turning) to the panel and out along the circuits, lights glowing,
  smoke alarms blinking, the EV charger charging a car;
- **"What's happening with your power?"** (`helper`): a tripped breaker, dead outlets, flickering lights,
  a burning smell or sparks – with when to stop and call 911 or the utility;
- **"What are your bulbs really costing?"** (`led_calc`): bulbs × watts × hours × 365 ÷ 1,000 × the rate
  the visitor sets, 60 W bulbs against 9 W LEDs (DOE: LEDs use at least 75% less energy), each with a
  meter turning at its own speed;
- the rating as a gauge, as Air's.

Sample: `python sites/render.py sites/trades/electrician.json sites/samples/electrician-denver.json`.

The accent colour, headline and hero photo are picked from the pack by a hash of the business's name
and city, so two roofers in one city don't get the same page.

## 2. Who gets a website (`sites/build.py`)

When a campaign is made, each of its businesses gets a website, or is left out with the reason:

- a business without a city on Google gets **the city it was searched in**, and that city's state
  (from `"Katy, TX"`, the businesses' own states, or the town of that name nearest to them);
- a business **more than 60 miles** from that city is left out – Google's results now and then reach
  far outside the searched place (a Toronto plumber in a Houston search);
- a **shop that sells to the trade** ("Roofing supply store", wholesalers …) is left out;
- a rating **under 4.0**, or from **fewer than 5 reviews**, stays off the page (the page shows it big);
- the service-area map shows the business's city and up to 5 bigger towns within 30 miles.

A business that already has a website (the same Google place or email) keeps its address.

## 3. How long a website is online (`sites/deploy.py`)

The latest of these decides:

| Since | Online for | Why |
| --- | --- | --- |
| it was built (no email yet) | 30 days | |
| a first email without the link | 21 days | older emails offered to send the link later |
| the first email with the link (`{Website}`) | 14 days | room for follow-ups that go out late (weekends, the daily limit) |
| the last email, when the emails link the site | 2 days | it says the preview "comes down tomorrow" |
| the business replied | 30 days | it stays up while you talk |
| on hold | until the hold is lifted | set by itself when the business replies, unless the reply says no; the *Put on hold* / *Lift the hold* button under its website link in a campaign does it by hand |

The Roofer emails (a business type with a design starts with them, `SITE_STEPS` in
`app/outreach/templates.py`) link the site in all three: day 0, day 3, and day 6 - *comes down
tomorrow*.

The Worker (`sites/worker/src/index.js`) reads each site's end date from `_status.json`, uploaded with
the sites (never served): an ended site shows *This preview has ended* (`410`); a site on hold shows
*Your new website is being set up* instead of the countdown. Every page is `noindex`.

## The quote form

One form, everything visible: name, phone, address or ZIP, email (optional), *What do you need?*, *How soon*
and *Anything we should know?* (optional). The **Quick start** chips above it (It's leaking, Storm or hail
damage, I need a new roof, Just want it checked) fill the *What do you need?* dropdown, and a service card
or *We'll check it for free* link elsewhere on the page picks one too.

It posts to the Worker (`POST /_q`). **A real request goes to the business straight away**, laptop on or
off: the Worker posts it to a Make.com webhook (the Worker's `MAKE_WEBHOOK` secret, set with
`python sites/deploy.py --make-webhook`), whose scenario emails it to the business and answers `sent`. The
address comes from `_status.json` (`to`, written by `sites/deploy.py`: empty when the business bounced,
unsubscribed or its address was refused). The webhook gets `to`, `business`, `subject`, `text`, `html`,
`reply_to` (the visitor's email), `site_url`, `at` and each field on its own.

The scenario: **Webhooks → Custom webhook**, then **Gmail → Send an email** (from the outreach mailbox; To
`to`, Subject `subject`, Content `html`, Reply-To `reply_to`), then **Webhooks → Webhook response** (status
200, body `sent`). Only `sent` counts: a scenario that is off only queues a request and answers *Accepted*,
and an error answers something else, so then the Worker keeps it in its database and the panel emails it
within a minute while it runs (`app/outreach/site_requests.py`) - a request is never lost. (Switching a
scenario back on sends what it queued: that one arrives twice.)

The visitor sees *We've got it* only once the request is saved; otherwise the form shows the business's
phone number. The panel's lead row shows *Website form sent*, without the visitor's details.

A request goes to **you**, with *[Not sent to ...]* in its subject, when it came from this computer, your
own country (`OWNER_COUNTRY` in `sites/worker/wrangler.jsonc`) or a test copy of an email, so you can try a
form: those never go to Make. It also goes to you when the business asked
not to be emailed or its address doesn't work. A visitor can send 5 requests an hour and a site can receive
20; a hidden field catches simple bots.

After changing the design, `python sites/build.py --rebuild` renders every site again without touching
their dates. A new database table needs `wrangler d1 migrations apply leadspilot-visits --remote`, run in
`sites/worker`.

## 4. Uploading

The panel uploads by itself 30 seconds after a change – a new campaign's websites, first emails that
went out – and again every 10 minutes while an upload fails. Only changed files are sent. The *Preview
websites* line of the Outreach tab's setup list shows how many are online and any upload error.

Set up once, on the computer the panel runs on:

1. install [Node.js](https://nodejs.org);
2. in `sites/worker`: `npm install`;
3. `python sites/deploy.py --login` – log in with the Cloudflare account that owns
   `leadspilot.co`. This project keeps its own login in `data/wrangler`, so logging in to another
   Cloudflare account elsewhere doesn't affect it. `wrangler.jsonc` names that account (`account_id`), so an upload under another
   login fails instead of going to the wrong account.

| Setting | Default | |
| --- | --- | --- |
| `PREVIEW_SITES` | `true` | `false`: campaigns get no websites and first emails don't wait (the tests set it) |
| `SITES_URL` | `https://sites.leadspilot.co` | where the websites are online |

## 5. Who opened their website

The Worker writes every visit into a small Cloudflare D1 database, `leadspilot-visits`
(`sites/worker/migrations/0001_visits.sql`):

| Kind | When |
| --- | --- |
| `view` | a page of the site was loaded |
| `seen` | it stayed open 3 seconds (a small script the Worker adds to every page) |
| `call` / `email` / `keep` | the phone number, the business's email or *keep my website* was tapped |
| `ended` | the site had ended and *This preview has ended* was shown |

Of the visitor it keeps their network (e.g. *Comcast Cable*), city and country, the browser's
User-Agent and a hash of the IP address made with the `VISITS_KEY` secret. The address itself is never
stored. The panel fetches the new visits every 5 minutes (`GET /_visits`, with `VISITS_KEY` as the
bearer token; `app/outreach/visits.py`), keeps them in `data/sites/visits.jsonl`, and sorts them:

- **you**: from the panel's own internet connection, or from your own country (the Worker tells the
  panel both). Your test visits, from this computer or your phone, aren't the business's;
- **copy**: opened from a test copy of an email (`?copy` on the link, `OUTREACH_TEST_COPY_TO`);
- **check**: a mail server or security scanner checking the link: a program's User-Agent, a network
  like Microsoft's or Google's, or a page load within 2 minutes of the email that didn't stay open;
- **visitor**: everyone else. Counted on the business: *Website opened* in the activity log (the first
  of each day, with where it came from) and *Tapped call* / *Tapped email* / *Asked to keep website*,
  and the columns *Website Opened*, *Last Visit*, *Website Visits*, *Tapped*, *Visited From* in the
  campaign's results file. The master sheet counts *Opened website* and *Tapped* per campaign.

Set up once (done for sites.leadspilot.co):

```bash
cd sites/worker
npx wrangler d1 create leadspilot-visits                       # its id goes into wrangler.jsonc
npx wrangler d1 migrations apply leadspilot-visits --remote
cd ../..
python sites/deploy.py --visits-key        # makes VISITS_KEY: the Worker's secret and .env's SITES_VISITS_KEY
python sites/deploy.py                     # uploads the Worker
```

Restart the panel after `--visits-key`. Without `SITES_VISITS_KEY` the panel doesn't fetch visits.

## 6. By hand

```bash
python sites/render.py sites/trades/roofer.json sites/samples/roofer-dallas.json   # one sample site
python sites/build.py job_5deb5c9d7bd1                                            # a search's websites
python sites/deploy.py --dry-run                                                  # check an upload
python sites/deploy.py                                                            # upload
```

`.claude/launch.json` has two preview servers: `sites` (the built sites, port 4173) and
`sites-worker` (the Worker with `wrangler dev`, port 8787, after `python sites/deploy.py --prepare`).

## 7. When something goes wrong

| Symptom | Cause | Fix |
| --- | --- | --- |
| *not logged in to Cloudflare* | this project has no Cloudflare login | `python sites/deploy.py --login` |
| *Node.js isn't installed* | `node` isn't on the PATH | install Node.js, restart the panel |
| first emails never go out, *once its website is online* | uploads fail | the setup list shows why |
| a new website address doesn't open on your PC | the router remembers it didn't exist yet | wait up to an hour, or try on mobile data |
| *the websites don't know this key* | `SITES_VISITS_KEY` isn't the Worker's `VISITS_KEY` | `python sites/deploy.py --visits-key`, restart the panel |
