"""Put the preview websites online at sites.leadspilot.co/<slug>/ (a Cloudflare Worker, ``sites/worker``).

    python sites/deploy.py              # upload every site that is still up
    python sites/deploy.py --dry-run    # check everything, upload nothing
    python sites/deploy.py --prepare    # only make data/deploy/public (for: wrangler dev)

The panel does this by itself after it builds a campaign's sites and after first emails go out
(app/outreach/sites.py). The sites come from ``sites/build.py`` (``data/sites/sites.json``). This copies
the ones that haven't ended into ``data/deploy/public/<slug>/``, with ``_status.json`` - each site's end
date and whether it is on hold - which the Worker reads on every request. Files that didn't change
aren't uploaded again.

How long a site stays online (``ends_at``): 7 days after its link was sent; else 21 days after the first
email (the last email promises the preview for two more weeks after day 7); else 30 days after it was
built. A site on hold - the business wants to keep it - stays until the hold is lifted.

Once, before the first upload, log in to Cloudflare (it opens the browser), in sites/worker:

    npx wrangler login
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITES = ROOT.parent / "data" / "sites"
PUBLIC = ROOT.parent / "data" / "deploy" / "public"    # wrangler.jsonc's assets directory
WORKER = ROOT / "worker"
WRANGLER = WORKER / "node_modules" / "wrangler" / "bin" / "wrangler.js"
LINK_DAYS = 7
EMAIL_DAYS = 21
UNUSED_DAYS = 30


def ends_at(site: dict) -> datetime | None:
    """When a site goes offline; None while it is on hold."""
    if site.get("hold"):
        return None
    for key, days in (("link_sent_at", LINK_DAYS), ("first_email_at", EMAIL_DAYS), ("built_at", UNUSED_DAYS)):
        if site.get(key):
            return datetime.fromisoformat(site[key]) + timedelta(days=days)
    return None


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
        shutil.rmtree(old) if old.is_dir() else old.unlink()
    for slug in live:
        shutil.copytree(sites_dir / slug, public / slug)
    status = {slug: {"expires": ends[slug].isoformat(timespec="seconds") if ends[slug] else None,
                     "hold": bool(manifest[slug].get("hold"))} for slug in live}
    (public / "_status.json").write_text(json.dumps(status, indent=1) + "\n", encoding="utf-8")
    return live


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="check everything, upload nothing")
    mode.add_argument("--prepare", action="store_true", help="only make data/deploy/public")
    args = ap.parse_args()
    slugs = prepare()
    print(f"{len(slugs)} site(s) ready in {PUBLIC}")
    if args.prepare:
        return 0
    try:
        command = wrangler_command(dry_run=args.dry_run)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    result = subprocess.run(command, cwd=WORKER)
    if result.returncode == 0 and not args.dry_run:
        mark_published(slugs)
        print("Online: " + ", ".join(f"https://sites.leadspilot.co/{slug}/" for slug in slugs[:3])
              + (" ..." if len(slugs) > 3 else ""))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
