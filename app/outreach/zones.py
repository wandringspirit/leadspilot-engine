"""Which time zone a business is in, so its emails go out in its own morning and the Calls tab suggests its
own 9-11am - whatever the business does.

From its Google Maps address first (the ZIP code catches the parts of a state on another clock: El Paso,
the Florida Panhandle, East Tennessee ...), else its state, else the town that was searched ("Tampa, FL").
'' when none of these says - the caller falls back to OUTREACH_TIMEZONE. US only for now. Pure functions
(tests/test_zones.py).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.outreach.models import Lead

EASTERN, CENTRAL, MOUNTAIN, ARIZONA, PACIFIC = ("America/New_York", "America/Chicago", "America/Denver",
                                                "America/Phoenix", "America/Los_Angeles")
ALASKA, HAWAII = "America/Anchorage", "Pacific/Honolulu"

#: the zone most of each state is in
STATE_ZONES = {
    **dict.fromkeys(["CT", "DE", "DC", "FL", "GA", "IN", "KY", "ME", "MD", "MA", "MI", "NH", "NJ", "NY", "NC",
                     "OH", "PA", "RI", "SC", "VT", "VA", "WV"], EASTERN),
    **dict.fromkeys(["AL", "AR", "IL", "IA", "KS", "LA", "MN", "MS", "MO", "NE", "ND", "OK", "SD", "TN", "TX",
                     "WI"], CENTRAL),
    **dict.fromkeys(["CO", "ID", "MT", "NM", "UT", "WY"], MOUNTAIN),
    "AZ": ARIZONA,                                   # no daylight saving
    **dict.fromkeys(["CA", "NV", "OR", "WA"], PACIFIC),
    "AK": ALASKA, "HI": HAWAII,
}

STATE_CODES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}

#: first 3 digits of a ZIP code in a part of a state that keeps another clock than the rest of it
ZIP_ZONES = {
    "798": MOUNTAIN, "799": MOUNTAIN, "885": MOUNTAIN,          # El Paso and Hudspeth, TX
    "324": CENTRAL, "325": CENTRAL,                             # Florida Panhandle: Panama City, Pensacola
    "373": EASTERN, "374": EASTERN, "376": EASTERN, "377": EASTERN, "378": EASTERN, "379": EASTERN,  # East TN
    "420": CENTRAL, "421": CENTRAL, "422": CENTRAL, "423": CENTRAL, "424": CENTRAL,  # Western KY
    "463": CENTRAL, "464": CENTRAL, "476": CENTRAL, "477": CENTRAL,  # Indiana: Gary/Hammond, Evansville
    "693": MOUNTAIN,                                            # Nebraska Panhandle: Scottsbluff, Alliance
    "577": MOUNTAIN,                                            # Western South Dakota: Rapid City
    "586": MOUNTAIN,                                            # Western North Dakota: Dickinson
    "835": PACIFIC, "838": PACIFIC,                             # North Idaho: Lewiston, Coeur d'Alene
    "979": MOUNTAIN,                                            # Eastern Oregon: Ontario
}

#: searched towns ("City, ST", lower case) on another clock than their state - used when there's no address
TOWN_ZONES = {
    "el paso, tx": MOUNTAIN,
    **dict.fromkeys(["pensacola, fl", "panama city, fl", "destin, fl", "fort walton beach, fl", "crestview, fl",
                     "navarre, fl", "milton, fl", "niceville, fl", "panama city beach, fl"], CENTRAL),
    **dict.fromkeys(["knoxville, tn", "chattanooga, tn", "johnson city, tn", "kingsport, tn", "bristol, tn",
                     "cleveland, tn", "maryville, tn", "morristown, tn", "oak ridge, tn"], EASTERN),
    **dict.fromkeys(["bowling green, ky", "paducah, ky", "owensboro, ky", "hopkinsville, ky"], CENTRAL),
    **dict.fromkeys(["gary, in", "hammond, in", "evansville, in", "merrillville, in"], CENTRAL),
    **dict.fromkeys(["iron mountain, mi", "menominee, mi"], CENTRAL),
    **dict.fromkeys(["rapid city, sd", "dickinson, nd", "scottsbluff, ne"], MOUNTAIN),
    **dict.fromkeys(["coeur d'alene, id", "lewiston, id", "moscow, id", "post falls, id"], PACIFIC),
}

#: how the zones are called in the panel and the master sheet
ZONE_NAMES = {EASTERN: "Eastern", CENTRAL: "Central", MOUNTAIN: "Mountain", ARIZONA: "Arizona",
              PACIFIC: "Pacific", ALASKA: "Alaska", HAWAII: "Hawaii"}

_ADDRESS = re.compile(r",\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b")
_TOWN = re.compile(r",\s*([A-Za-z]{2})\s*$")


def zone_for(address: str = "", state: str = "", town: str = "") -> str:
    """The business's time zone (an IANA name such as 'America/Chicago'), or '' when nothing says."""
    found = _ADDRESS.findall(address or "")
    if found:
        code, zip_code = found[-1]
        if code in STATE_ZONES:
            return ZIP_ZONES.get(zip_code[:3]) or STATE_ZONES[code]
    town_key = " ".join((town or "").lower().split())
    if town_key in TOWN_ZONES:
        return TOWN_ZONES[town_key]
    code = (state or "").strip()
    code = STATE_CODES.get(code.lower(), code.upper())
    if code in STATE_ZONES:
        return STATE_ZONES[code]
    match = _TOWN.search(town or "")
    if match and match.group(1).upper() in STATE_ZONES:
        return STATE_ZONES[match.group(1).upper()]
    return ""


def lead_zone(lead: "Lead", fallback: str) -> str:
    """A lead's time zone: the one worked out when it was made, else its town's, else ``fallback``
    (OUTREACH_TIMEZONE)."""
    return lead.time_zone or zone_for(town=lead.city) or fallback.strip()


def zone_name(zone: str) -> str:
    """'America/New_York' -> 'Eastern'; any other zone as it is."""
    return ZONE_NAMES.get(zone, zone)
