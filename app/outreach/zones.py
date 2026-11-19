"""Which time zone a business is in, so its emails go out in its own morning and the Calls tab suggests its
own 9-11am - whatever the business does.

From its Google Maps address first (the ZIP code catches the parts of a state on another clock: El Paso,
the Florida Panhandle, East Tennessee ...), else its state, else the town that was searched ("Tampa, FL").
'' when none of these says - the caller falls back to OUTREACH_TIMEZONE. Canada, Australia and New Zealand
by their province or state ("Perth, WA, Australia": the country is always written, as "WA" alone is
Washington). Pure functions (tests/test_zones.py).
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

#: outside the US: country -> province / state (as an address writes it) -> zone; "" = the country's main zone
ABROAD_ZONES = {
    "CA": {"BC": "America/Vancouver", "AB": "America/Edmonton", "SK": "America/Regina", "MB": "America/Winnipeg",
           "ON": "America/Toronto", "QC": "America/Toronto", "NB": "America/Moncton", "NS": "America/Halifax",
           "PE": "America/Halifax", "NL": "America/St_Johns", "YT": "America/Whitehorse",
           "NT": "America/Yellowknife", "NU": "America/Iqaluit", "": "America/Toronto"},
    "AU": {"NSW": "Australia/Sydney", "ACT": "Australia/Sydney", "VIC": "Australia/Melbourne",
           "TAS": "Australia/Hobart", "QLD": "Australia/Brisbane", "SA": "Australia/Adelaide",
           "NT": "Australia/Darwin", "WA": "Australia/Perth", "": "Australia/Sydney"},
    "NZ": {"": "Pacific/Auckland"},
}
COUNTRIES = {"canada": "CA", "australia": "AU", "new zealand": "NZ"}
REGION_CODES = {
    "CA": {"british columbia": "BC", "alberta": "AB", "saskatchewan": "SK", "manitoba": "MB", "ontario": "ON",
           "quebec": "QC", "québec": "QC", "new brunswick": "NB", "nova scotia": "NS",
           "prince edward island": "PE", "newfoundland and labrador": "NL", "yukon": "YT",
           "northwest territories": "NT", "nunavut": "NU"},
    "AU": {"new south wales": "NSW", "australian capital territory": "ACT", "victoria": "VIC", "tasmania": "TAS",
           "queensland": "QLD", "south australia": "SA", "northern territory": "NT", "western australia": "WA"},
    "NZ": {},
}

#: how the zones are called in the panel and the master sheet
ZONE_NAMES = {EASTERN: "Eastern", CENTRAL: "Central", MOUNTAIN: "Mountain", ARIZONA: "Arizona",
              PACIFIC: "Pacific", ALASKA: "Alaska", HAWAII: "Hawaii",
              "America/Toronto": "Eastern (Canada)", "America/Winnipeg": "Central (Canada)",
              "America/Regina": "Saskatchewan", "America/Edmonton": "Mountain (Canada)",
              "America/Vancouver": "Pacific (Canada)", "America/Halifax": "Atlantic", "America/Moncton": "Atlantic",
              "America/St_Johns": "Newfoundland", "Australia/Sydney": "Sydney", "Australia/Melbourne": "Melbourne",
              "Australia/Hobart": "Hobart", "Australia/Brisbane": "Brisbane", "Australia/Adelaide": "Adelaide",
              "Australia/Darwin": "Darwin", "Australia/Perth": "Perth", "Pacific/Auckland": "New Zealand"}

_ADDRESS = re.compile(r",\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b")
_TOWN = re.compile(r",\s*([A-Za-z]{2})\s*$")


_CA_ADDRESS = re.compile(r",\s*([A-Z]{2})\s+[A-Z]\d[A-Z]\s?\d[A-Z]\d\b")       # ", ON M5V 2T6"
_AU_ADDRESS = re.compile(r"\b(NSW|ACT|VIC|TAS|QLD|SA|NT|WA)\s+\d{4}\b")          # "Geelong VIC 3220"


def country_of(text: str) -> str:
    """'CA' / 'AU' / 'NZ' when an address or a place ends with that country's name, else ''."""
    last = (text or "").rsplit(",", 1)[-1].strip().lower()
    return COUNTRIES.get(last, "")


def without_country(place: str) -> str:
    """'Perth, WA, Australia' -> 'Perth, WA'; 'Hamilton, New Zealand' -> 'Hamilton'; US places as they are."""
    return place.rsplit(",", 1)[0].strip() if country_of(place) else place


def _abroad_zone(country: str, address: str, state: str, town: str) -> str:
    zones, names = ABROAD_ZONES[country], REGION_CODES[country]
    pattern = {"CA": _CA_ADDRESS, "AU": _AU_ADDRESS}.get(country)
    found = pattern.findall(address or "") if pattern else []
    candidates = [found[-1]] if found else []
    candidates.append(names.get((state or "").strip().lower(), (state or "").strip().upper()))
    parts = [p.strip() for p in (town or "").split(",")]
    if len(parts) == 3:                              # "Perth, WA, Australia"
        candidates.append(parts[1].upper())
    return next((zones[c] for c in candidates if c and c in zones), zones[""])


def zone_for(address: str = "", state: str = "", town: str = "") -> str:
    """The business's time zone (an IANA name such as 'America/Chicago'), or '' when nothing says."""
    country = country_of(address) or country_of(town)
    if country:
        return _abroad_zone(country, address, state, town)
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
