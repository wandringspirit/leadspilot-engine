-- Quote form requests waiting to be emailed to the business (src/index.js saves them; the panel emails and
-- deletes them: app/outreach/site_requests.py). Nothing stays here once it has been delivered.
CREATE TABLE requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,                 -- ISO time, UTC
  slug TEXT NOT NULL,               -- the site: sites.leadspilot.co/<slug>/
  who TEXT NOT NULL DEFAULT '',     -- a hash of the visitor's IP address (never the address itself)
  org TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL DEFAULT '',
  ua TEXT NOT NULL DEFAULT '',
  copy INTEGER NOT NULL DEFAULT 0,  -- 1: sent from a test copy of an email (?copy)
  data TEXT NOT NULL                -- JSON: problem, address, when, name, phone, email
);
