# Autopilot and the master sheet

The autopilot finds businesses and starts their campaigns by itself. The master sheet in Google
Drive shows every campaign and everything that happened. Code: `app/outreach/autopilot.py`,
`app/outreach/activity.py`, `app/outreach/master.py`.

## 1. What the autopilot does

```mermaid
flowchart LR
    look["every minute: fewer than 10 businesses<br/>waiting for a first email?"] -->|yes| search["search the next place<br/>(whole area, no website, has an email)"]
    search --> done["search finished"]
    done --> campaign["campaign “Roofer · Katy, TX”<br/>a website for each business"]
    campaign --> start["started: the emails go out<br/>in the send window, under the daily limit"]
    look -->|no| look
```

It works through **a list of places** for **one business type** (Roofer). It starts with Houston's
suburbs, then Dallas–Fort Worth, San Antonio and Austin; you can edit the list. Each search looks
at the whole area for businesses **without a website that have an email**, like the search form
with *Whole area*, *No website* and *Has an email*. When the search finishes, the autopilot makes
the campaign with the business type's emails and starts it. Businesses that were emailed before,
or are in another campaign, are left out as usual ([OUTREACH.md](OUTREACH.md)). Each business gets
its website ([SITES.md](SITES.md)), and its first email waits until the website is online.

It searches only when the running campaigns are about to run out of businesses to email, so
websites aren't built weeks before anyone is emailed and Google Maps isn't searched more than
needed.

| Setting | Default | Meaning |
| --- | --- | --- |
| business type | Roofer | what it looks for, and the emails its campaigns use |
| places | 20 Texas places | searched in this order; *next* is the next one |
| `min_waiting` | 10 | search when fewer businesses than this wait for their first email (two days at 5 a day) |
| `searches_per_day` | 2 | at most this many searches a day, one at a time |
| `limit` | 60 | businesses wanted per search |

### When it holds back

- a search is running;
- the mailbox settings have a problem, or a campaign was paused because of the mailbox;
- enough businesses are waiting, or today's searches are used up.

The panel shows which one applies.

### When it stops itself

The autopilot switches itself off, says why, and writes it in the activity log when:

- **2 of the last 20 businesses emailed bounced.** It also pauses the running campaigns, because
  bounces hurt the mailbox's reputation. Check the addresses and the mailbox, then switch it on
  again; bounces from before that don't count.
- **Google blocked a search** (its *unusual traffic* check). Wait a day, then switch it on again.
- every place on the list has been searched, or its business type was deleted.

A search that didn't finish (the panel was closed during it) is searched again.

## 2. The activity log

Every event is a line in `data/activity.jsonl`:

- searches started, finished or blocked;
- campaigns made, started, paused or finished;
- every email sent or refused;
- every reply, automatic reply, bounce and unsubscribe;
- the autopilot switched on or off, or stopping itself.

The Outreach tab shows the newest, and `GET /api/activity` returns them.

## 3. The master sheet

Two files in `DRIVE_FOLDER` (Google Drive opens them as spreadsheets). They are updated within
about 5 minutes of a change while Drive is connected and `DRIVE_AUTO_UPLOAD` is on, and can be
downloaded from the Outreach tab:

| File | What's in it |
| --- | --- |
| `Leads Pilot - campaigns.csv` | an **All campaigns** row, then a row per campaign: Status, Created, Started, Businesses, Emailed, Waiting, In sequence, Replied, No reply, Bounced, Unsubscribed, Refused, Skipped, Left out, Emails sent, Last email, and a link to its results file |
| `Leads Pilot - activity.csv` | the activity log, newest first: When, What, Campaign, Business, Email, Detail |

Each campaign's own results file, with one row per business, stays in `<business type>/Outreach/`
([DRIVE.md](DRIVE.md)). Times are the computer's.

## 4. API

`GET /api/autopilot`, `PATCH /api/autopilot`, `GET /api/activity`,
`GET /api/master/campaigns.csv`, `GET /api/master/activity.csv` ([API.md](API.md#autopilot)).
