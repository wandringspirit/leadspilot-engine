from app.scraper.maps import PlaceRef, build_row, place_id_from_href
from app.scraper.records import record_to_dict
from tests.conftest import make_record

HREF = ("https://www.google.com/maps/place/Acme+Roofing/data=!4m7!3m6!1s0x1:0x2!8m2!3d32.68!4d-97.10"
        "!16s%2Fg%2F11abc!19sChIJFromHref123?authuser=0&hl=en&rclk=1")


def test_place_id_from_href():
    assert place_id_from_href(HREF) == "ChIJFromHref123"
    assert place_id_from_href("https://example.com") == ""


def test_build_row_prefers_records_over_dom():
    ref = PlaceRef(rank=3, href=HREF, name="Card Name", card_reviews="(9)", card_rating="3.5")
    dom = {"name": "DOM Name", "address": "1 Dom St, Dallas, TX 75201, United States", "website": "https://dom.com",
           "phoneRaw": "+12145550100", "reviews": "42 reviews", "rating": "4.1", "category": "Dom cat",
           "description": "dom description"}
    full = record_to_dict(make_record(description="From  the\nbusiness"))
    row = build_row(ref, dom, full, {})
    assert row.rank == 3
    assert row.name == "Acme Roofing"
    assert row.city == "Arlington" and row.state == "Texas" and row.country == "US"
    assert row.website == "https://www.acmeroofing.com/"          # utm stripped
    assert row.phone == "(817) 555-0100" and row.phone_unformatted == "+18175550100"
    assert row.phone_source == "maps"
    assert row.review_count == 126 and row.rating == 4.8
    assert row.description == "From the business"                    # whitespace collapsed
    assert row.place_id == "ChIJTestPlaceId123456789"
    assert row.url == "https://www.google.com/maps/search/?api=1&query=Acme+Roofing&query_place_id=ChIJTestPlaceId123456789"
    assert row.maps_url == HREF.split("?")[0]
    assert row.latitude == 32.68


def test_build_row_falls_back_to_dom_and_card():
    ref = PlaceRef(rank=1, href=HREF, name="Card Name", card_reviews="(9)", card_rating="3.5")
    dom = {"name": "DOM Name", "address": "1 Dom St, Dallas, TX 75201, United States", "website": "dom.com",
           "phone": "(214) 555-0100", "category": "Roofer"}
    row = build_row(ref, dom, {}, {})
    assert row.name == "DOM Name"
    assert row.city == "Dallas" and row.state == "Texas" and row.country == "US"
    assert row.website == "https://dom.com"
    assert row.phone_unformatted == "+12145550100"
    assert row.review_count == 9 and row.rating == 3.5                # from the result card
    assert row.place_id == "ChIJFromHref123"                          # from the link
    assert row.latitude == 32.68 and row.longitude == -97.10          # from the link


def test_build_row_search_record_fills_when_full_record_missing():
    ref = PlaceRef(rank=1, href=HREF)
    search_rec = record_to_dict(make_record(name="Search Rec"))
    search_rec["review_count"] = None
    row = build_row(ref, {}, {}, search_rec)
    assert row.name == "Search Rec"
    assert row.review_count is None
    assert row.city == "Arlington"


def test_build_row_with_nothing_but_a_link():
    ref = PlaceRef(rank=7, href=HREF, name="Only Card")
    row = build_row(ref, {}, {}, {})
    assert row.name == "Only Card" and row.rank == 7
    assert row.phone == "" and row.city == "" and row.description == ""
    assert row.url.startswith("https://www.google.com/maps/search/?api=1&query=Only+Card")
