"""Which businesses of a job become outreach leads - and why the others don't.

One lead per business (a place can have several rows, one per email). A
business is left out when it

* has a website of its own (the emails say it has none);
* has no email, or only addresses that aren't its own - a BBB staff address
  (``…@bbbemail.org``), a placeholder (``name@example.com``) ...;
* was emailed before, or is a lead of another campaign (``taken``);
* is already a lead of this campaign (found by two of the selected searches,
  or two businesses sharing an address).

The first usable address of a business is the one emailed. Pure functions
(tests/test_outreach.py).
"""
from __future__ import annotations

from typing import Iterable, Mapping

from app.enrichment.sitefinder import is_free_mail, is_third_party
from app.models import Job, PlaceRow, split_places
from app.outreach.models import Lead, LeftOut
from app.outreach.templates import display_name
from app.scraper.parsing import website_domain

# Addresses in page templates, examples and error trackers - nobody reads them.
PLACEHOLDER_DOMAINS = frozenset({
    "example.com", "example.org", "example.net", "domain.com", "yourdomain.com", "yoursite.com",
    "email.address", "sentry.io", "wixpress.com", "sentry-next.wixpress.com",
})


def email_problem(email: str) -> str:
    """Why an address is no good for outreach ('' when it is fine)."""
    local, _, domain = (email or "").strip().lower().rpartition("@")
    if not local or "." not in domain:
        return f"not an email address ({email})"
    if any(domain == d or domain.endswith("." + d) for d in PLACEHOLDER_DOMAINS):
        return f"placeholder address ({email})"
    if is_free_mail(domain):
        return ""
    if is_third_party(f"https://{domain}/"):
        return f"address of {domain}, not of the business ({email})"
    return ""


def search_city(job: Job, row: PlaceRow) -> str:
    """{City}: the place that was searched - the one the business is in when a job searched several."""
    places = split_places(job.place)
    if len(places) == 1:
        return places[0]
    city = row.city.strip().lower()
    for place in places:
        if city and place.lower().startswith(city):
            return place
    return row.city or (places[0] if places else "")


def leads_from_jobs(jobs: Iterable[Job], *, taken: Mapping[str, str]) -> tuple[list[Lead], list[LeftOut]]:
    """Leads for a campaign from its jobs' rows, and the businesses left out with the reason.

    ``taken`` maps lower-case addresses and place ids already in use (emailed before, or in
    another campaign) to the reason shown for them.
    """
    leads: list[Lead] = []
    left_out: list[LeftOut] = []
    places: dict[str, str] = {}      # place key -> name of the lead (duplicates across searches)
    addresses: dict[str, str] = {}   # address -> name of the lead that has it
    for job in jobs:
        for rows in _places(job.rows):
            first = rows[0]
            key = first.place_id or f"{job.id}#{first.rank}"
            if key in places:
                left_out.append(LeftOut(name=first.name, reason="found by two of the selected searches"))
                continue
            places[key] = first.name
            if first.website_type == "own":
                left_out.append(LeftOut(name=first.name, reason=f"has a website ({website_domain(first.website)})"))
                continue
            emails = _unique(r.email for r in rows)
            if not emails:
                left_out.append(LeftOut(name=first.name, reason="no email"))
                continue
            usable = [e for e in emails if not email_problem(e)]
            if not usable:
                left_out.append(LeftOut(name=first.name, email=emails[0],
                                        reason="no address of its own: " + email_problem(emails[0])))
                continue
            reason = taken.get(first.place_id) if first.place_id else None
            reason = reason or next((taken[e.lower()] for e in usable if e.lower() in taken), "")
            if reason:
                left_out.append(LeftOut(name=first.name, email=usable[0], reason=reason))
                continue
            shared = next((addresses[e.lower()] for e in usable if e.lower() in addresses), "")
            if shared:
                left_out.append(LeftOut(name=first.name, email=usable[0],
                                        reason=f"same email as {shared}, already a lead"))
                continue
            for e in usable:
                addresses[e.lower()] = first.name
            leads.append(Lead(
                job_id=job.id, place_id=first.place_id, name=first.name, business=display_name(first.name),
                email=usable[0], other_emails=usable[1:], city=search_city(job, first),
                rating=first.rating, reviews=first.review_count, phone=first.phone,
                category=first.category, maps_url=first.url or first.maps_url,
            ))
    return leads, left_out


def _places(rows: list[PlaceRow]) -> list[list[PlaceRow]]:
    """Rows grouped by place (same rank), in order."""
    groups: dict[int, list[PlaceRow]] = {}
    for row in rows:
        groups.setdefault(row.rank, []).append(row)
    return list(groups.values())


def _unique(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        value = (value or "").strip()
        if value and value.lower() not in (v.lower() for v in out):
            out.append(value)
    return out
