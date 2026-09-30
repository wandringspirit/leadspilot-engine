"""Google Drive: each search's CSV in a folder per business type, campaigns that run from a file
in that folder, and every campaign's results next to them.

* ``client.py`` sign-in (OAuth for installed apps, the panel's server receives the answer) and
  the few Drive REST calls needed, over httpx;
* ``sync.py``   where things go (``DRIVE_FOLDER`` / <business type> / …), uploads, reading lead
  files, keeping campaign results up to date.

See docs/DRIVE.md.
"""
