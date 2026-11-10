"""Put the preview websites online at sites.leadspilot.co/<slug>/ (a Cloudflare Worker, ``sites/worker``).

    python sites/deploy.py              # upload every site that is still up
    python sites/deploy.py --dry-run    # check everything, upload nothing
    python sites/deploy.py --prepare    # only make data/deploy/public (for: wrangler dev)
    python sites/deploy.py --login      # log in to Cloudflare for this project (opens the browser)
    python sites/deploy.py --visits-key # a new VISITS_KEY: the Worker's secret and .env's SITES_VISITS_KEY
    python sites/deploy.py --make-webhook # set the Make.com webhook that emails quote requests at once

The panel does this by itself after it builds a campaign's sites and after first emails go out
(app/outreach/sites.py). The sites come from ``sites/build.py`` (``data/sites/sites.json``). This copies
the ones that haven't ended into ``data/deploy/public/<slug>/``, with ``_status.json`` - each site's end
date, whether it is on hold, and where its quote requests go (``deliver_to``) - which the Worker reads on
every request and never serves. Files that didn't change aren't uploaded again.

How long a site stays online (``ends_at``), from the latest thing that happened: 30 days after the
business replied; 2 days after the last email, which links the site and says it comes down tomorrow;
14 days after the first email with the link (room for the follow-ups to be late); 21 days after a first
email without it; else 30 days after it was built. A site on hold - the business wants to keep it - stays
until the hold is lifted.

Once, before the first upload, log in to Cloudflare with the account that owns leadspilot.co (it opens
the browser):

    python sites/deploy.py --login

This project keeps its own Cloudflare login, in data/wrangler (``WRANGLER_HOME``), so logging in to
another Cloudflare account for another project doesn't stop the uploads here.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITES = ROOT.parent / "data" / "sites"
PUBLIC = ROOT.parent / "data" / "deploy" / "public"    # wrangler.jsonc's assets directory
WORKER = ROOT / "worker"
WRANGLER = WORKER / "node_modules" / "wrangler" / "bin" / "wrangler.js"
WRANGLER_HOME = ROOT.parent / "data" / "wrangler"       # this project's own Cloudflare login
REPLY_DAYS = 30
LAST_EMAIL_DAYS = 2
LINK_DAYS = 14
EMAIL_DAYS = 21
UNUSED_DAYS = 30


def ends_at(site: dict) -> datetime | None:
    """When a site goes offline; None while it is on hold."""
    if site.get("hold"):
        return None
    for key, days in (("replied_at", REPLY_DAYS), ("last_email_at", LAST_EMAIL_DAYS), ("link_sent_at", LINK_DAYS),
                      ("first_email_at", EMAIL_DAYS), ("built_at", UNUSED_DAYS)):
        if site.get(key):
            return datetime.fromisoformat(site[key]) + timedelta(days=days)
    return None


def _writable(func, path, _exc) -> None:
    """Windows won't delete a read-only file or folder (copytree copies the flag): clear it and retry."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove(path: Path) -> None:
    if not path.is_dir():
        _writable(os.unlink, path, None)
    elif sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_writable)
    else:
        shutil.rmtree(path, onerror=_writable)


def prepare(sites_dir: Path = SITES, public: Path = PUBLIC, now: datetime | None = None) -> list[str]:
    """Fill ``public`` with the sites that are still up; returns their slugs."""
    path = sites_dir / "sites.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    now = now or datetime.now(timezone.utc)
    ends = {slug: ends_at(site) for slug, site in manifest.items()}
    live = sorted(slug for slug, site in manifest.items()
                  if (site.get("hold") or (ends[slug] and ends[slug] > now)) and (sites_dir / slug).is_dir())
    public.mkdir(parents=True, exist_ok=True)
    for old in public.iterdir():                    # emptied, not removed: wrangler dev may be watching it
        _remove(old)
    for slug in live:
        shutil.copytree(sites_dir / slug, public / slug)
    contacted = _contacted(sites_dir.parent / "contacted.json")
    status = {slug: {"expires": ends[slug].isoformat(timespec="seconds") if ends[slug] else None,
                     "hold": bool(manifest[slug].get("hold")),
                     "business": manifest[slug].get("business") or "",
                     "to": deliver_to(manifest[slug], contacted)} for slug in live}
    (public / "_status.json").write_text(json.dumps(status, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return live


#: what data/contacted.json says about a business that must not get an email (app/outreach/site_requests.py)
NOT_EMAILED = {"unsubscribed", "bounced", "failed"}


def deliver_to(site: dict, contacted: dict) -> str:
    """Where the Worker sends the site's quote requests straight away (through the Make webhook): the
    business's address, or "" when it must not be emailed - then the request waits for the panel, which
    sends it to you."""
    address = (site.get("email") or "").strip().lower()
    return "" if not address or (contacted.get(address) or {}).get("status") in NOT_EMAILED else address


def _contacted(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def wrangler_command(*, dry_run: bool = False) -> list[str]:
    """The command that uploads data/deploy/public (run it in WORKER)."""
    if not WRANGLER.is_file():
        raise FileNotFoundError("Cloudflare's wrangler isn't installed - run  npm install  in sites/worker")
    return ["node", str(WRANGLER), "deploy", "--config", str(WORKER / "wrangler.jsonc"),
            *(["--dry-run"] if dry_run else [])]


def mark_published(slugs: list[str], sites_dir: Path = SITES, now: datetime | None = None) -> None:
    """Write down when each of ``slugs`` first went online (``published_at``)."""
    path = sites_dir / "sites.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    for slug in slugs:
        if slug in manifest and not manifest[slug].get("published_at"):
            manifest[slug]["published_at"] = stamp
    partial = path.with_suffix(".json.part")
    partial.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    partial.replace(path)


def wrangler_env() -> dict[str, str]:
    """The environment wrangler runs in: with this project's own login (``WRANGLER_HOME``)."""
    WRANGLER_HOME.mkdir(parents=True, exist_ok=True)
    return {**os.environ, "XDG_CONFIG_HOME": str(WRANGLER_HOME)}


def login() -> int:
    """Log in to Cloudflare for this project (opens the browser) - with the account that owns leadspilot.co."""
    if not WRANGLER.is_file():
        print("Cloudflare's wrangler isn't installed - run  npm install  in sites/worker", file=sys.stderr)
        return 1
    print("Log in with the Cloudflare account that owns leadspilot.co (leadpilot01@gmail.com).")
    result = subprocess.run(["node", str(WRANGLER), "login"], cwd=WORKER, env=wrangler_env())
    if result.returncode == 0:
        subprocess.run(["node", str(WRANGLER), "whoami"], cwd=WORKER, env=wrangler_env())
    return result.returncode


def visits_key() -> int:
    """A new random VISITS_KEY: the Worker hashes visitors' IP addresses with it and only shows the visits
    to whoever has it - set as the Worker's secret, and as SITES_VISITS_KEY in .env for the panel. It is
    never printed."""
    import secrets
    sys.path.insert(0, str(ROOT.parent))
    from app import settings_editor
    from app.config import PROJECT_ROOT, Settings
    if not WRANGLER.is_file():
        print("Cloudflare's wrangler isn't installed - run  npm install  in sites/worker", file=sys.stderr)
        return 1
    key = secrets.token_urlsafe(32)
    result = subprocess.run(["node", str(WRANGLER), "secret", "put", "VISITS_KEY", "--config",
                             str(WORKER / "wrangler.jsonc")], cwd=WORKER, env=wrangler_env(), input=key, text=True,
                            capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print("The Worker's secret couldn't be set:\n" + (result.stdout + result.stderr).strip()[-800:],
              file=sys.stderr)
        return result.returncode
    settings_editor.save(Settings(), {"SITES_VISITS_KEY": key}, PROJECT_ROOT / ".env")
    print("VISITS_KEY is set on the Worker and in .env (SITES_VISITS_KEY) - restart the panel.")
    return 0


def make_webhook() -> int:
    """Set (or, with nothing typed, remove) the Worker's MAKE_WEBHOOK secret: the Make.com webhook that emails
    a website's quote requests to its business straight away. It is asked for here and never printed or kept
    in a file - whoever has it can make the scenario send email."""
    import getpass
    if not WRANGLER.is_file():
        print("Cloudflare's wrangler isn't installed - run  npm install  in sites/worker", file=sys.stderr)
        return 1
    print("Paste the Make.com webhook address (it stays hidden) and press Enter.\n"
          "Its scenario must end with a Webhook response whose body is: sent\n"
          "Press Enter without typing anything to remove the webhook: requests then wait for the panel.")
    url = getpass.getpass("Webhook: ").strip()
    if url and (not url.startswith("https://hook.") or " " in url):
        print("That isn't a Make.com webhook address (https://hook.….make.com/…) - nothing was changed.", file=sys.stderr)
        return 1
    command = ["node", str(WRANGLER), "secret", "put" if url else "delete", "MAKE_WEBHOOK", "--config",
               str(WORKER / "wrangler.jsonc")]
    result = subprocess.run(command, cwd=WORKER, env=wrangler_env(), input=url or "y\n", text=True,
                            capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print("The Worker's secret couldn't be changed:\n" + (result.stdout + result.stderr).strip()[-800:],
              file=sys.stderr)
        return result.returncode
    print("MAKE_WEBHOOK is set on the Worker: real quote requests now go straight to the business." if url else
          "MAKE_WEBHOOK is removed: quote requests wait for the panel again.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--make-webhook", action="store_true",
                      help="set or remove the Make.com webhook that emails quote requests to the business at once")
    mode.add_argument("--dry-run", action="store_true", help="check everything, upload nothing")
    mode.add_argument("--prepare", action="store_true", help="only make data/deploy/public")
    mode.add_argument("--login", action="store_true", help="log in to Cloudflare for this project")
    mode.add_argument("--visits-key", action="store_true",
                      help="make a new VISITS_KEY (who opened which website): the Worker's secret and .env's "
                           "SITES_VISITS_KEY")
    args = ap.parse_args()
    if args.login:
        return login()
    if args.visits_key:
        return visits_key()
    if args.make_webhook:
        return make_webhook()
    slugs = prepare()
    print(f"{len(slugs)} site(s) ready in {PUBLIC}")
    if args.prepare:
        return 0
    try:
        command = wrangler_command(dry_run=args.dry_run)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    result = subprocess.run(command, cwd=WORKER, env=wrangler_env())
    if result.returncode == 0 and not args.dry_run:
        mark_published(slugs)
        print("Online: " + ", ".join(f"https://sites.leadspilot.co/{slug}/" for slug in slugs[:3])
              + (" ..." if len(slugs) > 3 else ""))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
