import csv
import io

from app.export.csv_export import DEFAULT_COLUMNS, EXTENDED_COLUMNS, column_headers, rows_to_csv, rows_to_csv_bytes
from app.models import PlaceRow

EXPECTED_HEADERS = ["Name", "City", "State", "Country", "Email", "Website", "Phone", "Phone Unformatted",
                    "Url", "Rank", "Review Count", "Description"]


def test_default_columns_match_spreadsheet_layout():
    assert column_headers() == EXPECTED_HEADERS


def test_extended_columns_extend_default():
    assert EXTENDED_COLUMNS[: len(DEFAULT_COLUMNS)] == DEFAULT_COLUMNS
    assert "Address" in column_headers(extended=True)
    assert "Rating" in column_headers(extended=True)


def test_every_column_maps_to_a_row_attribute():
    row = PlaceRow()
    for _, attr in EXTENDED_COLUMNS:
        assert hasattr(row, attr), attr


def test_rows_to_csv_orders_by_rank_and_escapes():
    rows = [
        PlaceRow(rank=2, name='B "Quoted", Inc', description="line1\nline2", review_count=5),
        PlaceRow(rank=1, name="A", email="a@a.com", phone="(817) 555-0100", phone_unformatted="+18175550100"),
    ]
    text = rows_to_csv(rows)
    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[0] == EXPECTED_HEADERS
    assert parsed[1][0] == "A" and parsed[1][4] == "a@a.com" and parsed[1][9] == "1"
    assert parsed[2][0] == 'B "Quoted", Inc' and parsed[2][11] == "line1\nline2" and parsed[2][10] == "5"


def test_none_values_become_empty_cells():
    text = rows_to_csv([PlaceRow(rank=1, name="X", review_count=None)])
    assert text.splitlines()[1].endswith(",1,,")


def test_csv_bytes_have_bom_for_excel():
    payload = rows_to_csv_bytes([PlaceRow(rank=1, name="Café")])
    assert payload.startswith(b"\xef\xbb\xbf")
    assert "Café" in payload.decode("utf-8-sig")
