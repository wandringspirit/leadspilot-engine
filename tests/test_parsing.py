from app.scraper.parsing import (
    build_maps_search_url,
    country_to_code,
    expand_region,
    format_phone,
    normalise_website,
    parse_address,
    parse_rating,
    parse_review_count,
    website_domain,
)


def test_parse_us_address():
    parts = parse_address("3901 Arlington Highlands Blvd Ste 200, Arlington, TX 76018, United States")
    assert parts == {
        "street": "3901 Arlington Highlands Blvd Ste 200",
        "city": "Arlington",
        "state": "Texas",
        "postal_code": "76018",
        "country": "US",
    }


def test_parse_us_address_without_country():
    parts = parse_address("208 Willis Ave, Arlington, TX 76010")
    assert parts["city"] == "Arlington"
    assert parts["state"] == "Texas"
    assert parts["country"] == "US"


def test_parse_uk_address():
    parts = parse_address("10 Downing St, London SW1A 2AA, United Kingdom")
    assert parts["country"] == "GB"
    assert parts["city"] == "London"
    assert parts["postal_code"] == "SW1A 2AA"
    assert parts["street"] == "10 Downing St"


def test_parse_canadian_address():
    parts = parse_address("100 Queen St W, Toronto, ON M5H 2N2, Canada")
    assert parts["country"] == "CA"
    assert parts["state"] == "Ontario"
    assert parts["city"] == "Toronto"
    assert parts["postal_code"] == "M5H 2N2"


def test_parse_australian_address():
    parts = parse_address("1 Macquarie St, Sydney NSW 2000, Australia")
    assert parts["country"] == "AU"
    assert parts["state"] == "New South Wales"
    assert parts["postal_code"] == "2000"


def test_parse_empty_address():
    assert parse_address("") == {"street": "", "city": "", "state": "", "postal_code": "", "country": ""}


def test_country_to_code():
    assert country_to_code("United States") == "US"
    assert country_to_code("us") == "US"
    assert country_to_code("Narnia") == "Narnia"
    assert country_to_code("") == ""


def test_expand_region():
    assert expand_region("TX", "US") == "Texas"
    assert expand_region("Texas", "US") == "Texas"
    assert expand_region("ZZ", "US") == "ZZ"
    assert expand_region("QC", "CA") == "Quebec"


def test_format_phone_us():
    assert format_phone("+18176068303") == ("(817) 606-8303", "+18176068303")
    assert format_phone("(817) 606-8303", "US") == ("(817) 606-8303", "+18176068303")
    assert format_phone("817.606.8303", "US") == ("(817) 606-8303", "+18176068303")


def test_format_phone_invalid_keeps_raw():
    assert format_phone("call us", "US") == ("call us", "")
    assert format_phone("", "US") == ("", "")


def test_parse_review_count():
    assert parse_review_count("(1,234)") == 1234
    assert parse_review_count("126 reviews") == 126
    assert parse_review_count("1.2K reviews") == 1200
    assert parse_review_count(28) == 28
    assert parse_review_count("") is None
    assert parse_review_count(None) is None
    assert parse_review_count("no reviews") is None


def test_parse_rating():
    assert parse_rating("4.8") == 4.8
    assert parse_rating("4,8 stars") == 4.8
    assert parse_rating(5) == 5.0
    assert parse_rating("12") is None
    assert parse_rating("") is None


def test_build_maps_search_url():
    url = build_maps_search_url("Roof Experts, LLC", "ChIJabc")
    assert url == "https://www.google.com/maps/search/?api=1&query=Roof+Experts%2C+LLC&query_place_id=ChIJabc"
    assert build_maps_search_url("Acme") == "https://www.google.com/maps/search/?api=1&query=Acme"


def test_normalise_website():
    assert normalise_website("example.com") == "https://example.com"
    assert normalise_website("https://a.com/?utm_source=gmb&utm_medium=organic&id=1#x") == "https://a.com/?id=1"
    assert normalise_website("https://www.google.com/url?q=https%3A%2F%2Freal.com%2F&sa=D") == "https://real.com/"
    assert normalise_website("") == ""


def test_website_domain():
    assert website_domain("https://www.example.com/path?x=1") == "example.com"
    assert website_domain("sub.example.co.uk") == "sub.example.co.uk"
