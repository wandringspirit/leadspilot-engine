"""Search filters: which businesses a job keeps.

A search can ask for businesses *with* or *without* a website and require a
way to reach them (a phone, an email, either or both). The rules live here as
pure functions so the scraper, the job manager and the CLI apply them the same
way (unit tested in tests/test_filters.py):

* `SearchFilters.website_reason()` runs twice: on the results feed, to skip
  listings that cannot match without opening their place page, and on the
  scraped place.
* `SearchFilters.check()` decides on the scraped place (all of its rows).
* `SearchFilters.email_would_help()` tells the job manager when looking for an
  email on the website / social page could still turn a reject into a match.

"Website" means the business's *own* site: a Facebook page, Yelp listing,
booking page, ... (see `PROFILE_DOMAINS` in app/scraper/parsing.py) counts as
no website unless ``profiles_as_no_website`` is switched off. With
``website=without`` a place that matches is also checked for a website Google
Maps does not list (its email domain, the links on its social page and, with
``search_web``, a web search - see app/enrichment/sitefinder.py); a place with
one is skipped as `UNLISTED_WEBSITE`.
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Sequence

from pydantic import BaseModel, Field, computed_field, model_validator

from app.scraper.parsing import website_type

if TYPE_CHECKING:
    from app.models import PlaceRow


class WebsiteFilter(str, Enum):
    ANY = "any"
    WITH = "with"          # only businesses that have their own website
    WITHOUT = "without"    # only businesses without one


class ContactFilter(str, Enum):
    ANY = "any"
    PHONE = "phone"
    EMAIL = "email"
    PHONE_OR_EMAIL = "phone_or_email"
    PHONE_AND_EMAIL = "phone_and_email"


# Requirements that an email can satisfy - only these ever trigger an email lookup.
EMAIL_CONTACTS = {ContactFilter.EMAIL, ContactFilter.PHONE_OR_EMAIL, ContactFilter.PHONE_AND_EMAIL}

# Skip reason of a place with a website that its Google Maps listing does not show.
UNLISTED_WEBSITE = "has a website not listed on Maps"

_WEBSITE_LABELS = {WebsiteFilter.WITH: "has a website", WebsiteFilter.WITHOUT: "no website"}
_CONTACT_LABELS = {
    ContactFilter.PHONE: "has a phone", ContactFilter.EMAIL: "has an email",
    ContactFilter.PHONE_OR_EMAIL: "phone or email", ContactFilter.PHONE_AND_EMAIL: "phone and email",
}


class SearchFilters(BaseModel):
    website: WebsiteFilter = Field(WebsiteFilter.ANY, description="any | with | without (own website)")
    contact: ContactFilter = Field(
        ContactFilter.ANY, description="any | phone | email | phone_or_email | phone_and_email")
    profiles_as_no_website: bool = Field(
        True, description="A Facebook / Instagram / Yelp / booking page does not count as a website")
    search_web: bool = Field(
        False, description="With website=without: also search the web for a website the business does not "
                           "list on Google Maps (slower)")

    @model_validator(mode="after")
    def _search_web_needs_no_website(self) -> "SearchFilters":
        if self.website is not WebsiteFilter.WITHOUT:
            self.search_web = False      # only a "no website" search looks for unlisted websites
        return self

    @property
    def active(self) -> bool:
        return self.website is not WebsiteFilter.ANY or self.contact is not ContactFilter.ANY

    # ---- website --------------------------------------------------------
    def has_website(self, url: str) -> bool:
        kind = website_type(url)
        return kind == "own" or (kind == "profile" and not self.profiles_as_no_website)

    def website_reason(self, url: str | None) -> str:
        """Why a place with this website cannot match ('' if it can; ``None`` = not known yet)."""
        if self.website is WebsiteFilter.ANY or url is None:
            return ""
        has = self.has_website(url)
        if self.website is WebsiteFilter.WITHOUT and has:
            return "has a website"
        if self.website is WebsiteFilter.WITH and not has:
            return "no website"
        return ""

    # ---- contact --------------------------------------------------------
    def contact_reason(self, has_phone: bool, has_email: bool) -> str:
        """Why a place with these contact details cannot match ('' if it can)."""
        need = self.contact
        if need is ContactFilter.ANY:
            return ""
        if need is ContactFilter.PHONE_OR_EMAIL:
            return "" if has_phone or has_email else "no phone or email"
        wanted = {
            ContactFilter.PHONE: (("phone", has_phone),),
            ContactFilter.EMAIL: (("email", has_email),),
            ContactFilter.PHONE_AND_EMAIL: (("phone", has_phone), ("email", has_email)),
        }[need]
        missing = [name for name, present in wanted if not present]
        return "no " + " or ".join(missing) if missing else ""

    # ---- whole place ----------------------------------------------------
    def check(self, rows: Sequence[PlaceRow]) -> str:
        """'' when the place (all of its rows) matches, otherwise the reason it does not."""
        if not rows:
            return "no data"
        return self.website_reason(rows[0].website) or self.contact_reason(
            any(r.phone for r in rows), any(r.email for r in rows))

    def email_would_help(self, rows: Sequence[PlaceRow]) -> bool:
        """True when only a missing email keeps the place out and there is a page to search for one."""
        if not rows or not rows[0].website or any(r.email for r in rows):
            return False
        if self.website_reason(rows[0].website):
            return False
        has_phone = any(r.phone for r in rows)
        return bool(self.contact_reason(has_phone, False)) and not self.contact_reason(has_phone, True)

    # ---- presentation ---------------------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def label(self) -> str:
        """Human readable summary, e.g. 'no website · phone or email' (sent to the panel)."""
        parts = [_WEBSITE_LABELS.get(self.website, ""), _CONTACT_LABELS.get(self.contact, "")]
        return " · ".join(p for p in parts if p) or "all businesses"

    def slug(self) -> str:
        """File-name suffix, e.g. 'no_website_phone_or_email' ('' without filters)."""
        parts = []
        if self.website is not WebsiteFilter.ANY:
            parts.append("no_website" if self.website is WebsiteFilter.WITHOUT else "with_website")
        if self.contact is not ContactFilter.ANY:
            parts.append(self.contact.value)
        return "_".join(parts)
