"""Command line interface - scrape without the web panel.

Examples
--------
    python cli.py "roofing companies near houston" --limit 50 --out houston.csv
    python cli.py "dentists in austin tx" --limit 20 --enrich --extended --headed
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.config import settings
from app.enrichment.website import enrich_many
from app.export.csv_export import rows_to_csv_bytes
from app.jobs import apply_contact_result, select_enrichment_targets
from app.models import PlaceRow
from app.scraper.maps import MapsScraper


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Scrape Google Maps results to CSV")
    p.add_argument("query", help='search text, e.g. "roofing companies near houston"')
    p.add_argument("--limit", "-n", type=int, default=20, help="number of places (default 20)")
    p.add_argument("--out", "-o", type=Path, default=None, help="CSV path (default: data/exports/<query>.csv)")
    p.add_argument("--enrich", action="store_true", help="look up emails/phones on each website")
    p.add_argument("--extended", action="store_true", help="include extra columns (rating, address, ...)")
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--quiet", "-q", action="store_true", help="less output")
    return p


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

    scraper = MapsScraper(settings, headless=not args.headed, on_progress=on_progress)
    rows: list[PlaceRow] = await scraper.scrape(args.query, args.limit)
    if scraper.stats.errors and not args.quiet:
        for err in scraper.stats.errors:
            print(f"warning: {err}", file=sys.stderr)

    if args.enrich and rows:
        targets = select_enrichment_targets(rows, row_ids=None, force=False)
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

    out = args.out or settings.exports_dir / (_slug(args.query) + ".csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(rows_to_csv_bytes(rows, extended=args.extended))
    print(f"wrote {len(rows)} row(s) to {out}", file=sys.stderr)
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
