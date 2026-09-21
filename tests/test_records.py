from app.scraper.records import (
    IDX,
    dig,
    find_place_records,
    is_place_response,
    is_search_response,
    parse_rpc_json,
    record_to_dict,
    records_from_html,
    records_from_rpc,
)
from tests.conftest import make_record, rpc_body


def test_dig_is_safe():
    assert dig([1, [2, 3]], 1, 1) == 3
    assert dig([1, [2, 3]], 1, 5) is None
    assert dig("not a list", 0) is None
    assert dig(None, 0, 0) is None


def test_parse_rpc_json_strips_prefix():
    assert parse_rpc_json(")]}'\n[1, 2]") == [1, 2]
    assert parse_rpc_json("[1]") == [1]
    assert parse_rpc_json(")]}'\nnot json") is None
    assert parse_rpc_json("") is None


def test_record_to_dict_maps_fields(record):
    data = record_to_dict(record)
    assert data["name"] == "Acme Roofing"
    assert data["website"].startswith("https://www.acmeroofing.com/")
    assert data["phone"] == "+18175550100"
    assert data["rating"] == 4.8
    assert data["review_count"] == 126
    assert data["place_id"] == "ChIJTestPlaceId123456789"
    assert data["category"] == "Roofing contractor"
    assert data["categories"] == ["Roofing contractor", "General contractor"]
    assert data["city"] == "Arlington"
    assert data["state"] == "Texas"
    assert data["country"] == "US"
    assert data["postal_code"] == "76010"
    assert data["latitude"] == 32.68 and data["longitude"] == -97.10
    assert data["description"] == "Established in 2009. Full service roofing."


def test_record_to_dict_tolerates_missing_indexes():
    data = record_to_dict([None] * 11 + ["Only a name"])
    assert data["name"] == "Only a name"
    assert data["website"] == "" and data["phone"] == ""
    assert data["rating"] is None and data["review_count"] is None
    assert data["description"] == ""


def test_records_from_rpc_finds_every_record_in_document_order():
    body = rpc_body(make_record(name="First", place_id="ChIJ1"), make_record(name="Second", place_id="ChIJ2"))
    recs = records_from_rpc(body)
    assert [r["name"] for r in recs] == ["First", "Second"]
    assert [r["place_id"] for r in recs] == ["ChIJ1", "ChIJ2"]


def test_find_place_records_ignores_non_records():
    data = [["a", "b"], [[None] * 50], 5, None]
    assert find_place_records(data) == []


def test_records_from_html(sample_html_state):
    recs = records_from_html(sample_html_state)
    assert len(recs) == 1
    assert recs[0]["name"] == "Acme Roofing"
    assert records_from_html("<html></html>") == []


def test_url_classifiers():
    assert is_search_response("https://www.google.com/search?tbm=map&authuser=0&q=x")
    assert not is_search_response("https://www.google.com/maps/preview/place?x=1")
    assert is_place_response("https://www.google.com/maps/preview/place?authuser=0")
    assert not is_place_response("https://www.google.com/search?tbm=map")


def test_index_map_is_documented():
    """docs/SCRAPING.md must list every field of the index map."""
    from pathlib import Path

    doc = Path(__file__).resolve().parent.parent / "docs" / "SCRAPING.md"
    text = doc.read_text(encoding="utf-8")
    for key, path in IDX.items():
        rendered = "".join(f"[{i}]" for i in path)
        assert rendered in text, f"index {rendered} ({key}) is not documented in docs/SCRAPING.md"
