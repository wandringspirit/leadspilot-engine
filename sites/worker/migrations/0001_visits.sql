-- Visits to the preview websites (src/index.js writes them, the panel reads them: app/outreach/visits.py)
CREATE TABLE visits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,                 -- ISO time, UTC
  slug TEXT NOT NULL,               -- the site: sites.leadspilot.co/<slug>/
  kind TEXT NOT NULL,               -- view (page loaded) | seen (open 3 seconds) | call | email (tapped) | ended
  who TEXT NOT NULL DEFAULT '',     -- a hash of the visitor's IP address (never the address itself)
  org TEXT NOT NULL DEFAULT '',     -- the visitor's network, e.g. "Comcast Cable" or "Microsoft Corporation"
  country TEXT NOT NULL DEFAULT '',
  city TEXT NOT NULL DEFAULT '',
  ua TEXT NOT NULL DEFAULT '',      -- the browser's User-Agent, cut to 200 characters
  copy INTEGER NOT NULL DEFAULT 0   -- 1: opened from a test copy of an email (?copy), not by the business
);
CREATE INDEX visits_slug ON visits (slug);
