"""Pull emails and phone numbers out of HTML / text.

Pure functions only - unit tested in tests/test_extractors.py.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlparse

import phonenumbers
from bs4 import BeautifulSoup

from app.scraper.parsing import unwrap_redirect, website_domain

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")
# "name [at] domain [dot] com", "name (at) domain.com", "name AT domain DOT com"
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|\s+at\s+)\s*"
    r"([A-Za-z0-9\-]+(?:(?:\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\s+dot\s+)\s*|\.)[A-Za-z0-9\-]+)+)",
    re.I,
)
_DOT_TOKEN_RE = re.compile(r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\s+dot\s+)\s*", re.I)

# Asset file names that match the email pattern ("logo@2x.png"). None of these
# extensions is a real top-level domain, so no deliverable address is lost.
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".ico", ".pdf")
ROLE_PRIORITY = ("info", "contact", "hello", "office", "sales", "admin", "support",
                 "service", "estimates", "quotes", "inquiries", "enquiries", "mail")
CONTACT_KEYWORDS = ("contact", "about", "team", "staff", "reach", "quote", "estimate",
                    "support", "kontakt", "impressum", "get-in-touch", "connect")
STANDARD_PATHS = ("/contact", "/contact-us", "/contactus", "/contact.html", "/contact.php",
                  "/about", "/about-us", "/aboutus", "/about.html", "/get-a-quote", "/quote",
                  "/free-estimate", "/request-a-quote")


@dataclass
class EmailHit:
    email: str
    source: str            # URL where it was found
    same_domain: bool = False
    score: int = 0


@dataclass
class ExtractResult:
    emails: list[EmailHit] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)   # E.164
    contact_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)   # links to other sites (redirects unwrapped)


# "website":"https:\/\/example.com\/" in a page's embedded JSON (a Facebook page's
# website, an Instagram profile's "external_url", ...)
_JSON_WEBSITE_RE = re.compile(
    r'"(?:website|websites|website_url|external_url)"\s*:\s*\[?\s*"((?:https?:)?\\?/\\?/[^"\s]+)"', re.I)


# --------------------------------------------------------------------------
# Emails
# --------------------------------------------------------------------------
def decode_cfemail(encoded: str) -> str:
    """Decode Cloudflare's `data-cfemail` obfuscation."""
    try:
        key = int(encoded[:2], 16)
        return "".join(chr(int(encoded[i:i + 2], 16) ^ key) for i in range(2, len(encoded), 2))
    except (ValueError, IndexError):
        return ""


def clean_email(raw: str) -> str:
    """Normalise a candidate address; '' when it is not a syntactically valid email.

    There is deliberately no junk / placeholder filtering: every valid address is
    kept and `score_email` puts the site's own addresses first.
    """
    email = unquote((raw or "").strip()).strip(".,;:<>()[]{}\"'")
    email = email.split("?", 1)[0]          # mailto:foo@bar.com?subject=..
    email = email.lower()
    if email.startswith("mailto:"):
        email = email[7:]
    if not EMAIL_RE.fullmatch(email):
        return ""
    if email.endswith(IMAGE_EXTENSIONS):
        return ""
    local, _, domain = email.partition("@")
    if len(local) > 64 or len(domain) > 253 or ".." in email:
        return ""
    return email


def deobfuscate(text: str) -> list[str]:
    found: list[str] = []
    for m in OBFUSCATED_RE.finditer(text or ""):
        domain = _DOT_TOKEN_RE.sub(".", m.group(2))
        domain = re.sub(r"\s+", "", domain)
        candidate = clean_email(f"{m.group(1)}@{domain}")
        if candidate:
            found.append(candidate)
    return found


def emails_in_text(text: str) -> list[str]:
    """Addresses written in plain text (incl. `[at]` / `[dot]` spellings), in order of appearance."""
    found: list[str] = []
    for candidate in [m.group(0) for m in EMAIL_RE.finditer(text or "")] + deobfuscate(text):
        email = clean_email(candidate)
        if email and email not in found:
            found.append(email)
    return found


def score_email(email: str, site_domain: str) -> tuple[bool, int]:
    domain = email.split("@", 1)[1]
    same = bool(site_domain) and (domain == site_domain or domain.endswith("." + site_domain)
                                  or site_domain.endswith("." + domain))
    score = 0
    if same:
        score += 100
    local = email.split("@", 1)[0]
    for i, role in enumerate(ROLE_PRIORITY):
        if local == role or local.startswith(role):
            score += 50 - i
            break
    if local.startswith(("noreply", "no-reply", "donotreply", "no_reply")):
        score -= 200
    if any(local.startswith(p) for p in ("privacy", "legal", "abuse", "dmca", "webmaster", "unsubscribe")):
        score -= 60
    if domain.endswith(("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com")):
        score += 10  # small businesses often use free mail; keep but below own-domain
    return same, score


def extract_from_html(html: str, page_url: str, site_url: str) -> ExtractResult:
    """Find emails, phones and candidate contact pages in one HTML document."""
    result = ExtractResult()
    if not html:
        return result
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        if tag.name == "script" and tag.get("type") == "application/ld+json":
            continue
        tag.decompose()

    emails: set[str] = set()
    phones: set[str] = set()

    # mailto: / tel: links
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        lower = href.lower()
        if lower.startswith("mailto:"):
            for part in href[7:].split("?", 1)[0].split(","):
                e = clean_email(part)
                if e:
                    emails.add(e)
        elif lower.startswith("tel:"):
            phones.add(href[4:])

    # Cloudflare obfuscation
    for el in soup.select("[data-cfemail]"):
        e = clean_email(decode_cfemail(el.get("data-cfemail", "")))
        if e:
            emails.add(e)

    # JSON-LD (LocalBusiness / Organization)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for e, p in _walk_jsonld(data):
            if e:
                ce = clean_email(e)
                if ce:
                    emails.add(ce)
            if p:
                phones.add(p)

    # Visible text + raw attribute text (catches emails inside data attributes)
    text = soup.get_text(" ")
    for m in EMAIL_RE.finditer(text):
        e = clean_email(m.group(0))
        if e:
            emails.add(e)
    for m in EMAIL_RE.finditer(html):
        e = clean_email(m.group(0))
        if e:
            emails.add(e)
    emails.update(deobfuscate(text))

    # Phones from visible text
    phones.update(_phones_in_text(text))

    site_domain = website_domain(site_url)
    for e in sorted(emails):
        same, score = score_email(e, site_domain)
        result.emails.append(EmailHit(email=e, source=page_url, same_domain=same, score=score))
    result.emails.sort(key=lambda h: (-h.score, h.email))
    result.phones = _normalise_phones(phones)

    # Candidate internal links that look like contact/about pages
    base_domain = website_domain(page_url) or site_domain
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(page_url, href).split("#", 1)[0]
        if website_domain(absolute) != base_domain:
            continue
        label = f"{href} {a.get_text(' ', strip=True)}".lower()
        if any(k in label for k in CONTACT_KEYWORDS) and absolute not in seen:
            seen.add(absolute)
            result.contact_links.append(absolute)

    # Links to other sites - on a profile page, the business's own website is one of them.
    # Websites named in the page's data come first, then the links in document order.
    candidates = [m.group(1).replace("\\/", "/") for m in _JSON_WEBSITE_RE.finditer(html)]
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            candidates += list(_jsonld_urls(json.loads(script.string or "")))
        except (json.JSONDecodeError, TypeError):
            continue
    candidates += [urljoin(page_url, a["href"].strip()) for a in soup.find_all("a", href=True)
                   if not a["href"].strip().startswith(("mailto:", "tel:", "javascript:", "#"))]
    for link in candidates:
        link = unwrap_redirect("https:" + link if link.startswith("//") else link).split("#", 1)[0]
        domain = website_domain(link)
        if link.startswith(("http://", "https://")) and domain and domain != base_domain \
                and link not in result.external_links:
            result.external_links.append(link)
    return result


def _jsonld_urls(node, depth: int = 0):
    """url / sameAs / website values of JSON-LD objects."""
    if depth > 6:
        return
    if isinstance(node, dict):
        for key in ("url", "sameAs", "website"):
            value = node.get(key)
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, str) and item.startswith(("http", "//")):
                    yield item
        for value in node.values():
            yield from _jsonld_urls(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _jsonld_urls(item, depth + 1)


def _walk_jsonld(node, depth: int = 0):
    if depth > 6:
        return
    if isinstance(node, dict):
        yield node.get("email") if isinstance(node.get("email"), str) else None, \
            node.get("telephone") if isinstance(node.get("telephone"), str) else None
        for value in node.values():
            yield from _walk_jsonld(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_jsonld(item, depth + 1)


# --------------------------------------------------------------------------
# Phones
# --------------------------------------------------------------------------
_REGION = "US"


def set_default_region(region: str) -> None:
    global _REGION
    _REGION = (region or "US").upper()


def _phones_in_text(text: str, region: str | None = None) -> set[str]:
    region = region or _REGION
    found: set[str] = set()
    try:
        for match in phonenumbers.PhoneNumberMatcher(text or "", region, leniency=phonenumbers.Leniency.VALID):
            found.add(phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164))
    except Exception:  # pragma: no cover - matcher can choke on odd input
        pass
    return found


def _normalise_phones(raw: set[str], region: str | None = None) -> list[str]:
    region = region or _REGION
    out: list[str] = []
    for value in raw:
        value = unquote(value).strip()
        try:
            num = phonenumbers.parse(value, None if value.startswith("+") else region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(num):
            e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
            if e164 not in out:
                out.append(e164)
    return out


def candidate_pages(site_url: str, discovered: list[str], max_pages: int) -> list[str]:
    """Order the pages worth fetching after the homepage."""
    parsed = urlparse(site_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    ordered: list[str] = []
    for link in discovered:
        if link not in ordered and link.rstrip("/") != site_url.rstrip("/"):
            ordered.append(link)
    for path in STANDARD_PATHS:
        url = root + path
        if url not in ordered:
            ordered.append(url)
    return ordered[:max_pages]
