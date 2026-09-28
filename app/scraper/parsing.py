"""Pure helper functions that turn raw scraped strings into clean fields.

Nothing in here touches the network or a browser, which keeps it easy to unit
test (see tests/test_parsing.py).
"""
from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, parse_qsl, quote_plus, unquote, urlencode, urlsplit, urlunsplit

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------
US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico", "VI": "U.S. Virgin Islands", "GU": "Guam",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}

CA_PROVINCES: dict[str, str] = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador", "NS": "Nova Scotia",
    "NT": "Northwest Territories", "NU": "Nunavut", "ON": "Ontario",
    "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan", "YT": "Yukon",
}

AU_STATES: dict[str, str] = {
    "NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland",
    "WA": "Western Australia", "SA": "South Australia", "TAS": "Tasmania",
    "ACT": "Australian Capital Territory", "NT": "Northern Territory",
}

# Country display name (as Google prints it) -> ISO-3166 alpha-2
COUNTRY_CODES: dict[str, str] = {
    "united states": "US", "usa": "US", "u.s.": "US", "us": "US",
    "canada": "CA", "united kingdom": "GB", "uk": "GB", "great britain": "GB",
    "australia": "AU", "new zealand": "NZ", "ireland": "IE", "india": "IN",
    "germany": "DE", "france": "FR", "spain": "ES", "italy": "IT",
    "netherlands": "NL", "belgium": "BE", "switzerland": "CH", "austria": "AT",
    "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI",
    "portugal": "PT", "poland": "PL", "czechia": "CZ", "czech republic": "CZ",
    "mexico": "MX", "brazil": "BR", "argentina": "AR", "chile": "CL",
    "colombia": "CO", "south africa": "ZA", "nigeria": "NG", "kenya": "KE",
    "united arab emirates": "AE", "uae": "AE", "saudi arabia": "SA",
    "singapore": "SG", "malaysia": "MY", "philippines": "PH", "indonesia": "ID",
    "thailand": "TH", "vietnam": "VN", "japan": "JP", "south korea": "KR",
    "china": "CN", "hong kong": "HK", "taiwan": "TW", "pakistan": "PK",
    "bangladesh": "BD", "sri lanka": "LK", "turkey": "TR", "israel": "IL",
    "egypt": "EG", "greece": "GR", "hungary": "HU", "romania": "RO",
    "ukraine": "UA", "russia": "RU",
}

# Pages hosted on someone else's platform. A business whose Google "website" is one
# of these (a Facebook page, an Instagram profile, a Yelp listing, a booking page, a
# retired Google business.site page, ...) has no website of its own. A URL matches
# when its host is one of these domains or a sub-domain of one.
PROFILE_DOMAINS: frozenset[str] = frozenset({
    # social networks & messaging
    "facebook.com", "fb.com", "fb.me", "instagram.com", "twitter.com", "x.com", "tiktok.com",
    "linkedin.com", "youtube.com", "youtu.be", "pinterest.com", "snapchat.com", "threads.net",
    "tumblr.com", "nextdoor.com", "vk.com", "wa.me", "whatsapp.com", "m.me", "t.me", "telegram.me",
    # link-in-bio pages
    "linktr.ee", "linkin.bio", "beacons.ai", "bio.link", "lnk.bio", "campsite.bio", "taplink.cc", "solo.to",
    # directories, review sites & marketplaces
    "yelp.com", "yellowpages.com", "tripadvisor.com", "houzz.com", "angi.com", "angieslist.com",
    "homeadvisor.com", "thumbtack.com", "bbb.org", "porch.com", "bark.com", "manta.com", "etsy.com",
    "justdial.com", "sulekha.com", "indiamart.com", "booking.com", "airbnb.com",
    # booking & online-ordering platforms
    "booksy.com", "fresha.com", "vagaro.com", "styleseat.com", "schedulicity.com", "setmore.com",
    "calendly.com", "acuityscheduling.com", "squareup.com", "glossgenius.com", "mindbodyonline.com",
    "opentable.com", "resy.com", "toasttab.com", "doordash.com", "ubereats.com", "grubhub.com",
    "order.online", "zomato.com", "swiggy.com",
    # Google's own pages (business.site / negocio.site are the retired Business Profile websites)
    "business.site", "negocio.site", "g.page", "business.google.com", "maps.google.com", "maps.app.goo.gl",
})

_STATE_ZIP_US = re.compile(r"^([A-Z]{2})\s+(\d{5})(?:-\d{4})?$")
_PROVINCE_POSTAL_CA = re.compile(r"^([A-Z]{2})\s+([A-Z]\d[A-Z]\s?\d[A-Z]\d)$", re.I)
_STATE_POSTCODE_AU = re.compile(r"^(?:(.*?)\s+)?([A-Z]{2,3})\s+(\d{4})$")  # "Sydney NSW 2000"
_TRAILING_POSTCODE = re.compile(r"\s+[A-Z0-9][A-Z0-9 -]{2,9}$", re.I)  # "London SW1A 2AA"
TRACKING_PREFIXES = ("utm_", "mc_")
TRACKING_PARAMS = {"gclid", "fbclid", "msclkid", "ref", "y_source", "yclid", "dclid"}
_NARROW_NBSP = chr(0x202F)
_NBSP = chr(0x00A0)


def country_to_code(value: str) -> str:
    """'United States' -> 'US'. Already-a-code values pass through."""
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return COUNTRY_CODES.get(v.lower(), v)


def expand_region(code: str, country: str) -> str:
    """'TX' + 'US' -> 'Texas'. Unknown codes are returned unchanged."""
    code = (code or "").strip()
    table = {"US": US_STATES, "CA": CA_PROVINCES, "AU": AU_STATES}.get((country or "").upper(), {})
    return table.get(code.upper(), code)


def parse_address(address: str) -> dict[str, str]:
    """Best-effort split of a Google formatted address into parts.

    Google prints addresses as comma separated components, ending with the
    country, e.g. ``"123 Main St, Arlington, TX 76010, United States"``.
    Returns keys: street, city, state, postal_code, country (ISO-2).
    """
    out = {"street": "", "city": "", "state": "", "postal_code": "", "country": ""}
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    if not parts:
        return out

    # Country is always last when Google knows it.
    if len(parts) >= 2:
        code = country_to_code(parts[-1])
        if code != parts[-1] or (len(parts[-1]) == 2 and parts[-1].isalpha()):
            out["country"] = code
            parts = parts[:-1]
    country = out["country"]

    if not parts:
        return out

    tail = parts[-1]
    if (m := _STATE_ZIP_US.match(tail)) and country in ("", "US"):
        out["country"] = out["country"] or "US"
        out["state"], out["postal_code"] = expand_region(m.group(1), "US"), m.group(2)
        parts = parts[:-1]
    elif (m := _PROVINCE_POSTAL_CA.match(tail)) and country in ("", "CA"):
        out["country"] = out["country"] or "CA"
        out["state"], out["postal_code"] = expand_region(m.group(1).upper(), "CA"), m.group(2).upper()
        parts = parts[:-1]
    elif (m := _STATE_POSTCODE_AU.match(tail)) and country in ("", "AU") and m.group(2) in AU_STATES:
        out["country"] = out["country"] or "AU"
        out["state"], out["postal_code"] = expand_region(m.group(2), "AU"), m.group(3)
        parts = parts[:-1]
        if m.group(1):
            out["city"] = m.group(1).strip()
            out["street"] = ", ".join(parts)
            return out
    elif country and country not in ("US", "CA", "AU"):
        # e.g. "London SW1A 2AA" or "Berlin 10115" -> city + postcode in one token
        m2 = _TRAILING_POSTCODE.search(tail)
        if m2 and any(ch.isdigit() for ch in m2.group(0)):
            out["postal_code"] = m2.group(0).strip()
            tail = tail[: m2.start()].strip()
        out["city"] = tail
        out["street"] = ", ".join(parts[:-1])
        return out

    if parts:
        out["city"] = parts[-1]
        out["street"] = ", ".join(parts[:-1])
    return out


def format_phone(raw: str, region: str = "US") -> tuple[str, str]:
    """Return (national_format, e164). E.164 is empty when the number is invalid."""
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    region = (region or "US").upper()
    try:
        num = phonenumbers.parse(raw, None if raw.startswith("+") else region)
    except NumberParseException:
        return raw, ""
    if not phonenumbers.is_possible_number(num):
        return raw, ""
    return (
        phonenumbers.format_number(num, PhoneNumberFormat.NATIONAL),
        phonenumbers.format_number(num, PhoneNumberFormat.E164),
    )


def parse_review_count(text: str | int | None) -> int | None:
    """'(1,234)' / '1,234 reviews' / '1.2K reviews' -> int."""
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)):
        return int(text)
    t = str(text).replace(",", "").replace(_NARROW_NBSP, "").replace(_NBSP, "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM]?)", t)
    if not m:
        return None
    value = float(m.group(1))
    mult = {"k": 1_000, "m": 1_000_000}.get(m.group(2).lower(), 1)
    return int(value * mult)


def parse_rating(text: str | float | None) -> float | None:
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)):
        return float(text) if 0 <= float(text) <= 5 else None
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(text))
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return value if 0 <= value <= 5 else None


def build_maps_search_url(name: str, place_id: str = "", address: str = "") -> str:
    """Build the shareable `?api=1` Google Maps URL used in the CSV `Url` column."""
    query = name if not address else f"{name} {address}"
    url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(query.strip())}"
    if place_id:
        url += f"&query_place_id={place_id}"
    return url


def normalise_website(url: str) -> str:
    """Strip tracking junk and make sure the URL has a scheme."""
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    # Google sometimes wraps links: https://www.google.com/url?q=<real>&...
    m = re.match(r"^https?://(?:www\.)?google\.[a-z.]+/url\?(?:.*?&)?q=([^&]+)", url, re.I)
    if m:
        url = unquote(m.group(1))
    url = url.split("#", 1)[0]
    # drop tracking parameters (utm_source=gmb&utm_medium=organic ...)
    parts = urlsplit(url)
    if parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if not (k.lower().startswith(TRACKING_PREFIXES) or k.lower() in TRACKING_PARAMS)]
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))
    return url


def website_domain(url: str) -> str:
    """'https://www.example.com/path' -> 'example.com'."""
    m = re.match(r"^(?:https?://)?(?:www\.)?([^/:?#]+)", (url or "").strip(), re.I)
    return m.group(1).lower() if m else ""


# Hosts whose links wrap the real destination in a query parameter.
_REDIRECTORS = ("facebook.com", "instagram.com", "duckduckgo.com", "google.", "yelp.", "bing.com")
_REDIRECT_PARAMS = ("uddg", "u", "url", "q", "target", "dest", "destination", "redirect")


def unwrap_redirect(url: str) -> str:
    """The real destination of a redirect link, without requesting it.

    Handles l.facebook.com/l.php?u=..., duckduckgo.com/l/?uddg=...,
    google.com/url?q=..., yelp.com/biz_redir?url=... and Bing's
    bing.com/ck/a?...&u=a1<base64>. Other URLs are returned unchanged.
    """
    for _ in range(3):   # a wrapper can wrap another one
        parts = urlsplit(url)
        host = parts.netloc.lower()
        if not parts.query or not any(r in host for r in _REDIRECTORS):
            return url
        params = parse_qs(parts.query)
        if host.endswith("bing.com"):
            value = params.get("u", [""])[0]
            if not value.startswith("a1"):
                return url
            try:
                url = base64.urlsafe_b64decode(value[2:] + "=" * (-len(value[2:]) % 4)).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return url
            continue
        target = next((params[p][0] for p in _REDIRECT_PARAMS
                       if p in params and params[p][0].startswith(("http://", "https://", "//"))), "")
        if not target:
            return url
        url = "https:" + target if target.startswith("//") else target
    return url


def website_type(url: str) -> str:
    """Classify a listing's website: 'none', 'profile' (a page on a platform from
    `PROFILE_DOMAINS`, e.g. a Facebook page) or 'own' (the business's own site)."""
    host = website_domain(url)
    if not host:
        return "none"
    if any(host == domain or host.endswith("." + domain) for domain in PROFILE_DOMAINS):
        return "profile"
    return "own"
