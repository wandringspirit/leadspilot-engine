"""Command line interface - scrape without the web panel.

Examples
--------
    python cli.py plumbers "Austin, TX" --limit 30
    python cli.py plumbers "Austin, TX" --website without --contact phone_or_email -n 50
    python cli.py roofing "Houston, TX" --whole-area --website without --contact phone_or_email -n 50
    python cli.py roofing "Houston, TX" --website without --contact phone --search-web -n 20
    python cli.py roofing "Katy, TX; Pasadena, TX; Pearland, TX" -n 100
    python cli.py "dentists in austin tx" --limit 20 --enrich --extended --headed
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from app.config import settings
from app.enrichment.website import enrich_many
from app.export.csv_export import rows_to_csv_bytes
from app.filters import ContactFilter, SearchFilters, WebsiteFilter
from app.jobs import (
    PlaceFilter,
    apply_contact_result,
    blocked_message,
    plan_searches,
    result_notice,
    select_enrichment_targets,
    stats_to_dict,
)
from app.models import Job, PlaceRow, build_query, split_places
from app.scraper.maps import MapsScraper


EXIT_BLOCKED = 3      # Google showed its "unusual traffic" check; the CSV has what was found before


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Find businesses on Google Maps and export them to CSV")
    p.add_argument("business", help='what to look for, e.g. "plumbers" (or a whole search: "roofers near houston")')
    p.add_argument("place", nargs="?", default="",
                   help='where to look, e.g. "Austin, TX" - several separated by ";"')
    p.add_argument("--whole-area", action="store_true",
                   help="search each place section by section, not just Google's top ~120 (slower, finds more)")
    p.add_argument("--limit", "-n", type=int, default=20, help="number of matching places (default 20)")
    p.add_argument("--website", choices=[f.value for f in WebsiteFilter], default="any",
                   help="keep only places with / without their own website (default: any)")
    p.add_argument("--contact", choices=[f.value for f in ContactFilter], default="any",
                   help="keep only places with this contact information (default: any)")
    p.add_argument("--profiles-are-websites", action="store_true",
                   help="count a Facebook / Instagram / Yelp / booking page as a website")
    p.add_argument("--search-web", action="store_true",
                   help="with --website without: also search the web for a website a business does not "
                        "list on Google Maps (slower)")
    p.add_argument("--out", "-o", type=Path, default=None, help="CSV path (default: data/exports/<search>.csv)")
    p.add_argument("--enrich", action="store_true", help="look up emails/phones on each website")
    p.add_argument("--extended", action="store_true", help="include extra columns (rating, address, ...)")
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--quiet", "-q", action="store_true", help="less output")
    return p


def filters_from_args(args: argparse.Namespace) -> SearchFilters:
    return SearchFilters(website=args.website, contact=args.contact,
                         profiles_as_no_website=not args.profiles_are_websites, search_web=args.search_web)


async def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is too chatty
    last = {"line": ""}

    def on_progress(phase: str, message: str, current: int, total: int) -> None:
        if args.quiet:
            return
        line = f"[{phase}] {message}"
        if line != last["line"]:
            print(line, file=sys.stderr)
            last["line"] = line

    query = build_query(args.business, args.place)
    if len(query) < 2:
        print('error: say what to look for, e.g. python cli.py plumbers "Austin, TX"', file=sys.stderr)
        return 2
    if args.whole_area and not split_places(args.place):
        print('error: --whole-area needs a place, e.g. python cli.py roofing "Houston, TX" --whole-area',
              file=sys.stderr)
        return 2
    if args.search_web and args.website != "without":
        print("warning: --search-web only applies with --website without - ignored", file=sys.stderr)
    job = Job(query=query, business=args.business.strip() if args.place.strip() else "", place=args.place.strip(),
              whole_area=args.whole_area,
              limit=max(1, min(args.limit, settings.MAX_RESULTS)), filters=filters_from_args(args))
    selector = PlaceFilter(job.filters, default_region=settings.DEFAULT_REGION)
    scraper = MapsScraper(settings, headless=not args.headed, on_progress=on_progress)
    async with AsyncExitStack() as stack:
        await selector.open(stack, settings)
        rows: list[PlaceRow] = await scraper.scrape(plan_searches(job), job.limit, selector=selector)
    job.stats = stats_to_dict(scraper.stats) | selector.stats()
    blocked = scraper.stats.stopped == "blocked"
    if scraper.stats.errors and not args.quiet:
        for err in scraper.stats.errors:
            print(f"warning: {err}", file=sys.stderr)

    # places already looked up while filtering are not fetched a second time
    fresh = [r.row_id for r in rows if not r.enrichment_status]
    if args.enrich and fresh:
        targets = select_enrichment_targets(rows, row_ids=fresh, force=False)
        websites = {str(rank): group[0].website for rank, group in targets.items() if group[0].website}
        for rank, group in targets.items():
            for r in group:
                r.enrichment_status = "pending" if group[0].website else "no_website"
        print(f"[enriching] checking {len(websites)} website(s)", file=sys.stderr)
        region = next((r.country for r in rows if r.country), settings.DEFAULT_REGION)

        def on_result(key: str, result) -> None:
            added = apply_contact_result(rows, int(key), result, default_region=settings.DEFAULT_REGION)
            if not args.quiet:
                emails = ", ".join(result.email_addresses) or "-"
                print(f"[enriching] {result.website}: emails={emails} phones={len(result.phones)}"
                      f"{' (+%d rows)' % len(added) if added else ''}", file=sys.stderr)

        await enrich_many(websites, cfg=settings, region=region, on_result=on_result)

    suffix = job.filters.slug()
    out = args.out or settings.exports_dir / (_slug(job.query) + (f"_{suffix}" if suffix else "") + ".csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(rows_to_csv_bytes(rows, extended=args.extended))
    found = scraper.stats.matched
    print(f"wrote {len(rows)} row(s) for {found} place(s) to {out}"
          + (f" ({job.filters.label}; {scraper.stats.links_found} listing(s) scanned)" if job.filters.active else ""),
          file=sys.stderr)
    unlisted = sum(selector.sites_found.values())
    if unlisted:
        how = ", ".join(f"{n} by {check}" for check, n in selector.sites_found.items())
        print(f"left out {unlisted} place(s) with a website Google Maps does not list ({how})", file=sys.stderr)
    for notice in (result_notice(job), selector.notice()):
        if notice:
            print(f"note: {notice}", file=sys.stderr)
    if blocked:
        print(f"error: {blocked_message(job)}", file=sys.stderr)
        return EXIT_BLOCKED
    return 0


def _slug(text: str) -> str:
    import re
    return (re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "results")[:60]


def main() -> None:
    args = build_parser().parse_args()
    try:
        code = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
