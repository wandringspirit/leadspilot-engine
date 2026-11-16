"""Which time zone a business is in (app/outreach/zones.py)."""
from __future__ import annotations

import pytest

from app.outreach.models import Lead
from app.outreach.zones import lead_zone, zone_for, zone_name


@pytest.mark.parametrize("address, state, town, expected", [
    ("4409 E 39th St, Kansas City, MO 64128, United States", "Missouri", "Kansas City, MO", "America/Chicago"),
    ("608 Peterson St, Raleigh, NC 27610, United States", "", "", "America/New_York"),
    ("9 Main St, Overland Park, KS 66212, United States", "Kansas", "Kansas City, MO", "America/Chicago"),
    ("100 N Oregon St, El Paso, TX 79901, United States", "Texas", "Midland, TX", "America/Denver"),
    ("1 Palafox Pl, Pensacola, FL 32502", "", "Tampa, FL", "America/Chicago"),
    ("400 Main St, Knoxville, TN 37902", "", "", "America/New_York"),
    ("1 Main St, Nashville, TN 37201", "", "", "America/Chicago"),
    ("2 W Washington St, Phoenix, AZ 85003", "", "", "America/Phoenix"),
    ("", "California", "", "America/Los_Angeles"),
    ("", "", "El Paso, TX", "America/Denver"),
    ("", "", "Tampa, FL", "America/New_York"),
    ("", "", "Denver, co", "America/Denver"),
    ("", "", "Houston", ""),
    ("10 Downing St, London SW1A 2AA, United Kingdom", "", "", ""),
])
def test_zone_for(address, state, town, expected):
    assert zone_for(address, state, town) == expected


def test_lead_zone_and_names():
    assert lead_zone(Lead(email="a@b.co", city="Tampa, FL"), "America/Chicago") == "America/New_York"
    assert lead_zone(Lead(email="a@b.co", city="Tampa, FL", time_zone="America/Denver"), "") == "America/Denver"
    assert lead_zone(Lead(email="a@b.co", city="Houston"), "America/Chicago") == "America/Chicago"
    assert zone_name("America/New_York") == "Eastern" and zone_name("Europe/London") == "Europe/London"
