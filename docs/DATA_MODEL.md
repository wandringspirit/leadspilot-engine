# Data model & CSV format

The single source of truth for a result row is `PlaceRow` in `app/models.py`; the CSV
column order lives in `app/export/csv_export.py`. `tests/test_docs.py` fails if a column
is added there without being documented here.

## 1. Default CSV columns

These match the spreadsheet layout the tool was designed for, in this order:

| # | Column | `PlaceRow` field | Type | Source / rule |
| --- | --- | --- | --- | --- |
| 1 | `Name` | `name` | text | business name as shown on Google Maps |
| 2 | `City` | `city` | text | Google's structured address component; fallback: parsed from the address. Empty for service-area businesses without an address. |
| 3 | `State` | `state` | text | full name (`Texas`), expanded from the 2-letter code for US / CA / AU |
| 4 | `Country` | `country` | text | ISO-3166 alpha-2 (`US`, `GB`, `CA`) |
| 5 | `Email` | `email` | text | **only from website enrichment**; one row per email |
| 6 | `Website` | `website` | url | normalised (`https://` added, tracking parameters and fragments removed) |
| 7 | `Phone` | `phone` | text | national format, e.g. `(817) 606-8303` (from Maps, or from the website when Maps has none) |
| 8 | `Phone Unformatted` | `phone_unformatted` | text | E.164, e.g. `+18176068303` (empty when the number could not be validated) |
| 9 | `Url` | `url` | url | `https://www.google.com/maps/search/?api=1&query=<name>&query_place_id=<place id>` – opens the listing in Google Maps |
| 10 | `Rank` | `rank` | int | 1-based position in the Google Maps results for the query. Duplicated (multi-email) rows share the rank. |
| 11 | `Review Count` | `review_count` | int | number of Google reviews (empty if unknown) |
| 12 | `Description` | `description` | text | the business's own "From the business" description on Google Maps; whitespace collapsed to one line. Empty when the business has none. |

## 2. Extended CSV columns (`?extended=true` / `--extended`)

Appended after the default columns:

| Column | `PlaceRow` field | Type | Meaning |
| --- | --- | --- | --- |
| `Address` | `address` | text | full formatted address as printed by Google |
| `Category` | `category` | text | primary category, e.g. `Roofing contractor` |
| `Rating` | `rating` | float | average star rating (0–5) |
| `Place ID` | `place_id` | text | Google place id (`ChIJ…`) – stable key for de-duplication across jobs |
| `Maps URL` | `maps_url` | url | the `/maps/place/…` page that was visited |
| `Latitude` | `latitude` | float | |
| `Longitude` | `longitude` | float | |
| `Email Source` | `email_source` | url | page where the email in this row was found |
| `Phone Source` | `phone_source` | text | `maps` or `website` |
| `Enrichment Status` | `enrichment_status` | text | `done`, `not_found`, `no_website`, `error`, `pending` or empty (see [ENRICHMENT.md](ENRICHMENT.md#5-row-status-values)) |

Internal fields that are **not exported**: `row_id` (unique per row, used by the panel and
the enrichment API), `enrichment_note` (human-readable detail, shown as a tooltip in the
panel and returned by the API).

## 3. CSV encoding details

- UTF-8 with a byte-order mark (`EF BB BF`) so Microsoft Excel detects the encoding; Google
  Sheets, LibreOffice and pandas (`encoding="utf-8-sig"`) handle it transparently.
- `\r\n` line endings, comma delimiter, minimal quoting (fields containing commas, quotes or
  newlines are wrapped in double quotes; quotes are doubled).
- Rows are sorted by `Rank`, then by insertion order, so duplicated rows stay together.
- `None` values become empty cells; floats are printed without trailing zeros (`4.8`).
- The download file name is derived from the query
  (`roofing_companies_near_houston.csv`, `…_extended.csv`).

## 4. Address parsing rules (`parse_address`)

Used when Google's structured components are unavailable. Google prints addresses as
comma-separated parts ending with the country:

| Input | street | city | state | postal | country |
| --- | --- | --- | --- | --- | --- |
| `123 Main St, Arlington, TX 76010, United States` | 123 Main St | Arlington | Texas | 76010 | US |
| `100 Queen St W, Toronto, ON M5H 2N2, Canada` | 100 Queen St W | Toronto | Ontario | M5H 2N2 | CA |
| `1 Macquarie St, Sydney NSW 2000, Australia` | 1 Macquarie St | Sydney | New South Wales | 2000 | AU |
| `10 Downing St, London SW1A 2AA, United Kingdom` | 10 Downing St | London | *(empty)* | SW1A 2AA | GB |
| `Corpus Christi, TX 78401` | *(empty)* | Corpus Christi | Texas | 78401 | US |

Country names are mapped to ISO codes with a built-in table (`COUNTRY_CODES`); unknown
names are kept verbatim.

## 5. Phone formatting rules (`format_phone`)

- Parsed with the `phonenumbers` library; numbers starting with `+` are taken as-is,
  others are interpreted in the row's country (fallback `DEFAULT_REGION`).
- `Phone` = national format of that region (`(817) 606-8303` for US, `020 7946 0958` for GB).
- `Phone Unformatted` = E.164 (`+18176068303`).
- Numbers that fail validation keep the raw text in `Phone` and leave
  `Phone Unformatted` empty, so nothing is silently dropped.

## 6. Job model (API)

```jsonc
{
  "id": "job_5f0c2a1b9d3e",
  "query": "roofing companies near houston",
  "limit": 50,
  "enrich": false,
  "status": "completed",            // queued | running | enriching | completed | failed | cancelled | interrupted
  "progress": { "phase": "done", "message": "Done - 52 rows", "current": 50, "total": 50, "percent": 100.0 },
  "rows": [ { /* PlaceRow */ } ],
  "error": null,
  "created_at": "2026-09-20T15:04:28.123Z",
  "updated_at": "2026-09-20T15:06:01.456Z",
  "finished_at": "2026-09-20T15:06:01.456Z",
  "stats": { "links_found": 50, "scraped": 50, "failed": 0, "reached_end": false, "errors": [] }
}
```

`progress.phase` is one of `queued`, `collecting` (scrolling the feed), `details` (visiting
place pages), `enriching`, `done`.

## 7. Log entry (API)

`GET /api/logs` returns lines from the in-memory log buffer:

```jsonc
{
  "seq": 1533,                          // monotonically increasing, use as `since` cursor
  "ts": "2026-09-21T06:09:19.412Z",     // UTC
  "level": "WARNING",                   // DEBUG | INFO | WARNING | ERROR | CRITICAL
  "logger": "app.jobs",                 // python logger name (module)
  "message": "Enrichment failed for #2 Legends Roofing: ...",   // tracebacks are appended, multi-line
  "job_id": "job_5f0c2a1b9d3e"          // null for lines outside a job (start-up, API errors)
}
```

## 8. Entity relationships

```mermaid
classDiagram
    class Job {
        +str id
        +str query
        +int limit
        +bool enrich
        +JobStatus status
        +Progress progress
        +list~PlaceRow~ rows
        +dict stats
        +datetime created_at
        +datetime finished_at
    }
    class PlaceRow {
        +str row_id
        +int rank
        +str name
        +str city
        +str state
        +str country
        +str email
        +str website
        +str phone
        +str phone_unformatted
        +str url
        +int review_count
        +str description
        +str address
        +str category
        +float rating
        +str place_id
        +str maps_url
        +float latitude
        +float longitude
        +str email_source
        +str phone_source
        +str enrichment_status
        +str enrichment_note
        +clone_with_email(email, source)
    }
    class Progress {
        +str phase
        +str message
        +int current
        +int total
        +float percent
    }
    Job "1" *-- "many" PlaceRow : rows (same rank = same place)
    Job "1" *-- "1" Progress
```
