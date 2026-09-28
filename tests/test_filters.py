import pytest

from app.filters import ContactFilter, SearchFilters, WebsiteFilter
from app.models import PlaceRow
from app.scraper.parsing import website_type

NO_SITE = SearchFilters(website="without", contact="phone_or_email")


@pytest.mark.parametrize("url, expected", [
    ("", "none"),
    ("https://www.acmeroofing.com/", "own"),
    ("acmeroofing.com", "own"),
    ("https://www.facebook.com/joesplumbing", "profile"),
    ("https://m.facebook.com/joesplumbing", "profile"),
    ("https://instagram.com/joes.cuts", "profile"),
    ("https://joes-plumbing.business.site/", "profile"),
    ("https://www.yelp.com/biz/joes-plumbing-austin", "profile"),
    ("https://wa.me/15125550100", "profile"),
    ("https://sites.google.com/view/joesplumbing", "own"),     # a real (if simple) website
    ("https://joesplumbing.wixsite.com/home", "own"),
    ("https://inbox.com/", "own"),                              # "x.com" must match on a dot boundary
    ("https://myfb.com/", "own"),
])
def test_website_type(url, expected):
    assert website_type(url) == expected


def test_default_filters_are_inactive_and_keep_everything():
    f = SearchFilters()
    assert not f.active
    assert f.check([PlaceRow(rank=1)]) == ""
    assert f.label == "all businesses" and f.slug() == ""


def test_without_website_filter():
    assert NO_SITE.active
    assert NO_SITE.website_reason("") == ""
    assert NO_SITE.website_reason(None) == ""                   # unknown -> must look at the place
    assert NO_SITE.website_reason("https://acme.com") == "has a website"
    assert NO_SITE.website_reason("https://facebook.com/acme") == ""  # a Facebook page is not a website


def test_profiles_can_count_as_websites():
    f = SearchFilters(website="without", profiles_as_no_website=False)
    assert f.website_reason("https://facebook.com/acme") == "has a website"
    assert f.website_reason("") == ""
    with_site = SearchFilters(website="with")
    assert with_site.website_reason("https://facebook.com/acme") == "no website"
    assert with_site.website_reason("") == "no website"
    assert with_site.website_reason("https://acme.com") == ""


@pytest.mark.parametrize("contact, phone, email, reason", [
    ("any", False, False, ""),
    ("phone", True, False, ""),
    ("phone", False, True, "no phone"),
    ("email", False, True, ""),
    ("email", True, False, "no email"),
    ("phone_or_email", True, False, ""),
    ("phone_or_email", False, True, ""),
    ("phone_or_email", False, False, "no phone or email"),
    ("phone_and_email", True, True, ""),
    ("phone_and_email", True, False, "no email"),
    ("phone_and_email", False, False, "no phone or email"),
])
def test_contact_requirements(contact, phone, email, reason):
    assert SearchFilters(contact=contact).contact_reason(phone, email) == reason


def test_check_uses_every_row_of_the_place():
    rows = [PlaceRow(rank=3, phone=""), PlaceRow(rank=3, email="info@joes.com")]
    assert NO_SITE.check(rows) == ""
    assert NO_SITE.check([PlaceRow(rank=3)]) == "no phone or email"
    assert NO_SITE.check([PlaceRow(rank=3, website="https://joes.com", phone="1")]) == "has a website"
    assert NO_SITE.check([]) == "no data"


def test_email_would_help_only_when_an_email_can_still_decide():
    profile_no_phone = [PlaceRow(rank=1, website="https://facebook.com/joes")]
    assert NO_SITE.email_would_help(profile_no_phone)
    # already matches through the phone - nothing to look up
    assert not NO_SITE.email_would_help([PlaceRow(rank=1, website="https://facebook.com/joes", phone="1")])
    # no page to search
    assert not NO_SITE.email_would_help([PlaceRow(rank=1)])
    # an email cannot fix a website mismatch
    assert not NO_SITE.email_would_help([PlaceRow(rank=1, website="https://joes.com")])
    # phone requirement: an email never helps
    assert not SearchFilters(contact="phone").email_would_help(profile_no_phone)
    both = SearchFilters(contact="phone_and_email")
    assert both.email_would_help([PlaceRow(rank=1, website="https://joes.com", phone="1")])
    assert not both.email_would_help([PlaceRow(rank=1, website="https://joes.com")])  # phone missing too


def test_labels_and_slugs():
    assert NO_SITE.label == "no website · phone or email"
    assert NO_SITE.slug() == "no_website_phone_or_email"
    f = SearchFilters(website=WebsiteFilter.WITH, contact=ContactFilter.EMAIL)
    assert f.label == "has a website · has an email" and f.slug() == "with_website_email"


def test_filters_parse_from_json_values():
    f = SearchFilters.model_validate({"website": "without", "contact": "phone"})
    assert f.website is WebsiteFilter.WITHOUT and f.contact is ContactFilter.PHONE
    with pytest.raises(ValueError):
        SearchFilters.model_validate({"website": "sometimes"})
