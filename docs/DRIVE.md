# Google Drive

The crawler keeps its files in your Google Drive, one folder per business type:

- **Searches:** each search's CSV;
- **Lead files:** lists a campaign can run from, including ones you put there yourself;
- **Campaign results:** each campaign's results.

Setting it up step by step: [PLAYBOOK.md §2](PLAYBOOK.md#2-google-drive).

## Folders

```
DRIVE_FOLDER/                              My Drive itself, a folder path in it, or a (shared) folder's URL
  Roofer/                                  the business type's name
    roofing_in_houston_no_website_email_29_09_2026.csv    a search, uploaded when it finishes
    my cleaned list.csv                    a lead file you put there (or a Google Sheet)
    Outreach/
      roofer_houston_a1b2c3.csv            a campaign's results, kept up to date
  Plumber/
    …
```

| `DRIVE_FOLDER` | The business-type folders go … |
| --- | --- |
| *(empty)* | straight into *My Drive* |
| `Leads`, `Leads/2026` | into that folder of My Drive (made if missing) |
| `https://drive.google.com/drive/folders/1AbC…` or the id `1AbC…` | into that folder, also one someone shared with you (you need edit access) |

Where each file goes:

- **Searches:** a search for a business type goes to that type's folder (its name). A search typed
  in under *Other* goes to a folder named after what was searched (`Dentist`).
- **Campaign results:** they go under `<business type>/Outreach/`, or `Other/Outreach/` for a
  campaign with the default emails.

Folders are made when missing. If you delete one in Drive, the next upload makes it again. A
renamed folder keeps getting the files until the server restarts.

## What is uploaded, when

| File | When | Content |
| --- | --- | --- |
| a search's CSV | when the search finishes, and again after an enrichment. Automatic with `DRIVE_AUTO_UPLOAD=true`; any time with **⇪ Save to Drive** in the results card | the **extended** export: the default 12 columns first, then Address, Category, Website Type, Rating, Place ID … ([DATA_MODEL.md](DATA_MODEL.md)). Same name as the download: `<search>_<filters>_DD_MM_YYYY.csv` |
| a campaign's results | while it runs: when something changed, at most every 5 minutes. Also **⇪ Save results to Drive** | one row per business: Business, Email, Status, Emails Sent, First / Last / Next Email, Answered, Answer, City, Phone, Rating, Reviews, Name On Maps, Place ID, Note, Website |

A file is **updated in place**, the same Drive file with a new version, not a new copy each time.
The first upload reuses a file of the same name in the folder, if there is one.

## Lead files

A campaign can run from a CSV file or a Google Sheet in a business type's folder. Choose one under
*Outreach › + New* once a business type is picked. The format is the crawler's own CSV:

| Column | Needed | Used for |
| --- | --- | --- |
| `Name` | **yes** | the business (`{Business}` without LLC / Inc.) |
| `Email` | **yes** | where the emails go; several rows of one business = several addresses |
| `City` | no | `{City}` |
| `Website` | no | a business with a website of its own is left out |
| `Phone`, `Review Count`, `Rating`, `Place ID`, `Rank`, `Category`, `Url` … | no | shown in the panel, `{Rating}` / `{Reviews}`, and recognising the same business across files |

The format rules:

- **Header row:** the first row names the columns. Upper or lower case doesn't matter, the order
  doesn't matter, and other columns are ignored.
- **Separators:** comma, semicolon or tab, e.g. as Excel saves it.
- **One business, several rows:** rows with the same `Place ID`, else the same `Rank`, else the same
  name and phone, are one business.
- **Google Sheets:** the first sheet is used.

A file without a `Name` or an `Email` column is refused with that reason. The same rules as for a
search apply: who is left out and why ([OUTREACH.md §2](OUTREACH.md#2-who-becomes-a-lead-appoutreachleadspy)),
and nobody on the contacted list is emailed again.

## Signing in

The crawler talks to the Drive REST API with **your own OAuth client** (type *Desktop app*,
`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`):

1. **Connect Google Drive** in the Setup box asks the server for Google's consent page. That uses
   PKCE and a one-time `state`, valid for 15 minutes.
2. You sign in and allow access. Google sends the browser back to this server,
   `http://127.0.0.1:<port>/?code=…`. That is why the panel must be opened at `127.0.0.1` or
   `localhost`.
3. The server exchanges the code for a **refresh token** and stores it in
   `data/google_drive.json`. The file is git-ignored; treat it like a password.
4. Access tokens are renewed automatically every hour. If Google refuses the refresh token, for
   example because it was revoked or the app stayed in *Testing* for more than 7 days, the file is
   deleted and the panel asks you to connect again.

The scope is **full Drive access** (`https://www.googleapis.com/auth/drive`). The narrower
*"files this app created"* scope can't write into folders you made or read lead files you put
there. *Disconnect* revokes the token at Google and deletes the file.

The code: `app/drive/client.py` handles the sign-in and the REST calls (plain httpx, no Google
libraries); `app/drive/sync.py` decides where files go and handles uploads, lead files and
results; `app/export/csv_import.py` reads lead files. Tests run against a fake Google
(`tests/test_drive.py`).

## Settings

| Variable | Default | Description |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` | *(empty)* | OAuth client ID (*Desktop app*) from the Google Cloud console |
| `GOOGLE_CLIENT_SECRET` | *(empty)* | its client secret |
| `DRIVE_FOLDER` | *(empty = My Drive)* | where the business-type folders go (table above) |
| `DRIVE_AUTO_UPLOAD` | `true` | upload each search when it finishes and campaign results while they run; `false`: only the *Save to Drive* buttons |

The routes (`/api/drive…`, `POST /api/jobs/{id}/drive`, `POST /api/campaigns/{id}/drive`) are in
[API.md](API.md#google-drive).
