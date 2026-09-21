from app.enrichment.extractors import EmailHit
from app.enrichment.website import ContactResult
from app.jobs import apply_contact_result, group_by_place, select_enrichment_targets
from app.models import PlaceRow


def _row(rank: int, **kw) -> PlaceRow:
    base = dict(rank=rank, name=f"Place {rank}", website=f"https://p{rank}.com", phone="(817) 555-0100",
                phone_unformatted="+18175550100", country="US")
    base.update(kw)
    return PlaceRow(**base)


def _result(website: str, emails: list[str], phones: list[str] | None = None, error: str | None = None) -> ContactResult:
    return ContactResult(
        website=website,
        emails=[EmailHit(email=e, source=website + "contact", same_domain=True, score=100) for e in emails],
        phones=phones or [],
        pages_checked=[website],
        error=error,
    )


def test_group_by_place():
    rows = [_row(1), _row(2), _row(1)]
    groups = group_by_place(rows)
    assert sorted(groups) == [1, 2]
    assert len(groups[1]) == 2


def test_select_targets_only_missing_by_default():
    rows = [_row(1, email="a@p1.com"), _row(2), _row(3, phone="", phone_unformatted="", email="x@p3.com")]
    targets = select_enrichment_targets(rows, row_ids=None, force=False)
    assert sorted(targets) == [2, 3]


def test_select_targets_respects_row_ids_and_force():
    rows = [_row(1, email="a@p1.com"), _row(2)]
    assert select_enrichment_targets(rows, row_ids=[rows[0].row_id], force=False) == {}
    assert sorted(select_enrichment_targets(rows, row_ids=[rows[0].row_id], force=True)) == [1]


def test_apply_single_email_fills_row():
    rows = [_row(1), _row(2)]
    added = apply_contact_result(rows, 1, _result("https://p1.com/", ["info@p1.com"]))
    assert added == []
    assert rows[0].email == "info@p1.com"
    assert rows[0].email_source == "https://p1.com/contact"
    assert rows[0].enrichment_status == "done"
    assert rows[1].email == ""


def test_apply_multiple_emails_duplicates_rows_in_place():
    rows = [_row(1), _row(2)]
    added = apply_contact_result(rows, 1, _result("https://p1.com/", ["info@p1.com", "sales@p1.com", "hb@p1.com"]))
    assert len(added) == 2
    assert [r.email for r in rows] == ["info@p1.com", "sales@p1.com", "hb@p1.com", ""]
    assert [r.rank for r in rows] == [1, 1, 1, 2]
    assert len({r.row_id for r in rows}) == 4, "clones need fresh row ids"
    clone = rows[1]
    assert clone.name == rows[0].name and clone.website == rows[0].website and clone.phone == rows[0].phone
    assert all(r.enrichment_status == "done" for r in rows[:3])


def test_apply_skips_emails_already_present():
    rows = [_row(1, email="info@p1.com")]
    added = apply_contact_result(rows, 1, _result("https://p1.com/", ["info@p1.com", "new@p1.com"]))
    assert len(added) == 1 and added[0].email == "new@p1.com"
    assert [r.email for r in rows] == ["info@p1.com", "new@p1.com"]


def test_apply_fills_missing_phone_from_website():
    rows = [_row(1, phone="", phone_unformatted="")]
    apply_contact_result(rows, 1, _result("https://p1.com/", [], phones=["+18175550123"]))
    assert rows[0].phone == "(817) 555-0123"
    assert rows[0].phone_unformatted == "+18175550123"
    assert rows[0].phone_source == "website"
    assert rows[0].enrichment_status == "done"


def test_apply_not_found_and_error_statuses():
    rows = [_row(1), _row(2)]
    apply_contact_result(rows, 1, _result("https://p1.com/", []))
    assert rows[0].enrichment_status == "not_found"
    apply_contact_result(rows, 2, _result("https://p2.com/", [], error="HTTP 403"))
    assert rows[1].enrichment_status == "error"
    assert "403" in rows[1].enrichment_note


def test_apply_unknown_rank_is_noop():
    rows = [_row(1)]
    assert apply_contact_result(rows, 99, _result("https://x.com/", ["a@x.com"])) == []
    assert rows[0].email == ""
