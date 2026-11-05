"""Email templates: placeholders, optional parts and the default emails.

A template is plain text with placeholders in braces - ``{Business}``,
``{City}`` ... (see ``PLACEHOLDERS``) - and optional parts in double brackets:

* ``[[ in {City}]]`` is left out when a placeholder inside it has no value;
* ``[[A||B]]`` gives ``A`` when every placeholder in ``A`` has a value, ``B`` otherwise.

``{Rating}`` and ``{Reviews}`` only have a value for a business with at least
``OUTREACH_MIN_REVIEWS`` reviews and ``OUTREACH_MIN_RATING`` stars, so the
default first email mentions the stars only when they help. ``{Website}`` is the
business's preview website (docs/SITES.md); ``SITE_STEPS`` are the emails of a
business type that has a website design - they send the link.

Pure functions (tests/test_outreach.py).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from app.config import Settings
    from app.outreach.models import Campaign, Lead

PLACEHOLDERS: dict[str, str] = {
    "Business": "the business name, without LLC / Inc. (editable per lead)",
    "City": "the place you searched, e.g. Houston",
    "Trade": "what the campaign calls these businesses, e.g. roofers",
    "Rating": "Google rating, e.g. 4.8 - empty with few reviews or a low rating",
    "Reviews": "number of Google reviews - empty with few reviews or a low rating",
    "Website": "its preview website, e.g. https://sites.leadspilot.co/acme/ - empty without one",
    "SenderName": "your name (SENDER_NAME)",
    "SenderCompany": "your company (SENDER_COMPANY)",
    "SenderAddress": "your postal address (SENDER_ADDRESS)",
}

DEFAULT_STEPS: list[dict] = [
    {
        "day": 0,
        "subject": "website for {Business}",
        "body": (
            "Hi there,\n\n"
            "I came across {Business} on Google Maps while looking at {Trade}[[ in {City}]]. "
            "[[{Rating} stars from {Reviews} reviews, but there's no website on your listing"
            "||There's no website on your Google listing]], so people who find you there have nowhere "
            "to see your work or ask for a quote.\n\n"
            "So I went ahead and built you one. It's free and you're under no obligation.\n\n"
            "Would it be okay if I send you the link?\n\n"
            "{SenderName}"
        ),
    },
    {
        "day": 3,
        "subject": "",
        "body": (
            "Hi there,\n\n"
            "Just bringing this back up in case it got buried. The site for {Business} is ready. "
            "Want me to send the link?\n\n"
            "{SenderName}"
        ),
    },
    {
        "day": 7,
        "subject": "",
        "body": (
            "Hi there,\n\n"
            "Last note from me. If a website isn't a priority right now, no problem. I'll keep the "
            "preview up for another two weeks; reply \"link\" any time before then and I'll send it over.\n\n"
            "{SenderName}"
        ),
    },
]

# For businesses that get a preview website: the link in the first email. The website comes down a
# couple of days after the last email (sites/deploy.py: ends_at) - which says so.
SITE_STEPS: list[dict] = [
    {
        "day": 0,
        "subject": "{Business} website",
        "body": (
            "Hi,\n\n"
            "I made a website for {Business}:\n"
            "{Website}\n\n"
            "It has your Google reviews and the towns you cover.\n\n"
            "If you like it, it's yours to keep at no cost. If not, no problem at all.\n\n"
            "{SenderName}[[\n{SenderCompany}]]"
        ),
    },
    {
        "day": 3,
        "subject": "",
        "body": (
            "Hi, just checking you saw the site I made for {Business}: {Website}\n\n"
            "Happy to change anything: photos, services, phone number.\n\n"
            "{SenderName}[[\n{SenderCompany}]]"
        ),
    },
    {
        "day": 6,
        "subject": "",
        "body": (
            "Hi, the preview of {Business}'s website comes down tomorrow. If you'd like to keep it, "
            "just reply \"yes\".\n\n"
            "{Website}\n\n"
            "{SenderName}[[\n{SenderCompany}]]"
        ),
    },
]

# version B of SITE_STEPS, to test against it: no link in the first email - it asks first; the second
# email sends the link (docs/OUTREACH.md, "Testing two versions")
SITE_STEPS_ASK_FIRST: list[dict] = [
    {
        "day": 0,
        "subject": "{Business} website",
        "body": (
            "Hi,\n\n"
            "I came across {Business} on Google Maps. [[{Rating} stars from {Reviews} reviews, but there's no "
            "website on your listing||There's no website on your Google listing]], so I made one for you. It has "
            "your Google reviews and the towns you cover, and it's yours to keep at no cost.\n\n"
            "Can I send you the link?\n\n"
            "{SenderName}[[\n{SenderCompany}]]"
        ),
    },
    {
        "day": 3,
        "subject": "",
        "body": (
            "Hi, in case it's easier to just look, here's the website I made for {Business}: {Website}\n\n"
            "Happy to change anything: photos, services, phone number.\n\n"
            "{SenderName}[[\n{SenderCompany}]]"
        ),
    },
    {
        "day": 6,
        "subject": "",
        "body": (
            "Hi, the preview of {Business}'s website comes down tomorrow. If you'd like to keep it, "
            "just reply \"yes\".\n\n"
            "{Website}\n\n"
            "{SenderName}[[\n{SenderCompany}]]"
        ),
    },
]

SITE_FOOTER = "{SenderAddress}\nReply \"no\" and I won't write again."

DEFAULT_FOOTER = (
    "[[{SenderCompany}\n]]{SenderAddress}\n"
    "If you'd rather not hear from me, just reply \"no\" and I won't email again."
)

# The business types a fresh install starts with: (name - also the Google Drive folder name,
# Google Maps search, {Trade}). Each gets the default emails; edit them per type in the panel.
DEFAULT_BUSINESS_TYPES: list[tuple[str, str, str]] = [
    ("Roofer", "roofing", "roofers"),
    ("Plumber", "plumber", "plumbers"),
    ("HVAC", "hvac contractor", "HVAC companies"),
    ("Electrician", "electrician", "electricians"),
    ("Tree service", "tree service", "tree services"),
    ("Fence contractor", "fence contractor", "fence contractors"),
    ("Foundation repair", "foundation repair", "foundation repair companies"),
    ("Concrete contractor", "concrete contractor", "concrete contractors"),
    ("Painter", "painter", "painters"),
    ("Handyman", "handyman", "handymen"),
    ("Landscaper", "landscaper", "landscapers"),
    ("Pressure washing", "pressure washing service", "pressure washing companies"),
    ("Garage door repair", "garage door repair", "garage door companies"),
    ("Pool service", "pool cleaning service", "pool services"),
    ("Pest control", "pest control service", "pest control companies"),
    ("Junk removal", "junk removal service", "junk removal companies"),
    ("Mover", "moving company", "moving companies"),
]

_FIELD = re.compile(r"\{([A-Za-z]+)\}")
_BLOCK = re.compile(r"\[\[(.*?)\]\]", re.S)
_TOKEN = re.compile(r"\[\[(?P<block>.*?)\]\]|\{(?P<field>[A-Za-z]+)\}", re.S)
_BLANK_LINES = re.compile(r"\n{3,}")
_LEGAL_SUFFIX = re.compile(
    r"[\s,]+(?:l\.?\s?l\.?\s?c|p\.?\s?l\.?\s?l\.?\s?c|inc(?:orporated)?|ltd|limited|corp(?:oration)?)\.?$", re.I)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def render(text: str, values: Mapping[str, str]) -> str:
    """Fill a template. Unknown placeholders stay as they are (``template_errors`` reports them)."""
    def fill(part: str) -> str:
        return _FIELD.sub(lambda m: str(values.get(m.group(1)) or "") if m.group(1) in PLACEHOLDERS
                          else m.group(0), part)

    def token(m: re.Match) -> str:
        name = m.group("field")
        if name is not None:
            return str(values.get(name) or "") if name in PLACEHOLDERS else m.group(0)
        first, _, fallback = m.group("block").partition("||")
        wanted = [n for n in _FIELD.findall(first) if n in PLACEHOLDERS]
        return fill(first) if all(values.get(n) for n in wanted) else fill(fallback)

    return tidy(_TOKEN.sub(token, text or ""))


def tidy(text: str) -> str:
    """Trailing spaces off every line, at most one blank line in a row, no blank lines around."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip("\n")


def template_errors(text: str) -> list[str]:
    """What is wrong with a template ([] when it is fine)."""
    errors: list[str] = []
    for name in sorted({n for n in _FIELD.findall(text or "") if n not in PLACEHOLDERS}):
        errors.append(f"unknown placeholder {{{name}}} - use " + ", ".join(f"{{{p}}}" for p in PLACEHOLDERS))
    if any("[[" in m.group(1) for m in _BLOCK.finditer(text or "")):
        errors.append("optional parts [[ … ]] can't be inside each other")
    elif "[[" in _BLOCK.sub("", text or "") or "]]" in _BLOCK.sub("", text or ""):
        errors.append("an optional part is not closed - every [[ needs a ]]")
    return errors


def required_fields(text: str) -> set[str]:
    """Placeholders used outside optional parts: without a value the text reads wrong."""
    return {n for n in _FIELD.findall(_BLOCK.sub("", text or "")) if n in PLACEHOLDERS}


def used_fields(text: str) -> set[str]:
    return {n for n in _FIELD.findall(text or "") if n in PLACEHOLDERS}


# --------------------------------------------------------------------------
# Values of a lead
# --------------------------------------------------------------------------
def display_name(name: str) -> str:
    """'High Caliber Roofing LLC.' -> 'High Caliber Roofing' (how a business name reads in an email)."""
    cleaned = " ".join((name or "").split())
    while True:
        shorter, found = _LEGAL_SUFFIX.subn("", cleaned)
        shorter = shorter.rstrip(" ,.-")
        if not found or not shorter:
            return cleaned
        cleaned = shorter


def lead_values(campaign: "Campaign", lead: "Lead", cfg: "Settings") -> dict[str, str]:
    stars = (lead.rating is not None and lead.reviews is not None
             and lead.reviews >= cfg.OUTREACH_MIN_REVIEWS and lead.rating >= cfg.OUTREACH_MIN_RATING)
    return {
        "Business": lead.business,
        "City": lead.city,
        "Trade": campaign.trade.strip(),
        "Rating": f"{lead.rating:.1f}" if stars else "",
        "Reviews": str(lead.reviews) if stars else "",
        "Website": f"{cfg.SITES_URL.rstrip('/')}/{lead.site}/" if lead.site else "",
        "SenderName": cfg.SENDER_NAME.strip(),
        "SenderCompany": cfg.SENDER_COMPANY.strip(),
        "SenderAddress": cfg.SENDER_ADDRESS.strip(),
    }


def compose(campaign: "Campaign", lead: "Lead", step: int, cfg: "Settings") -> tuple[str, str]:
    """(subject, body) of email ``step`` (1 = the first email) for a lead, footer included.

    A follow-up without a subject of its own answers the first email: 'Re: <first subject>'.
    """
    values = lead_values(campaign, lead, cfg)
    steps = campaign.steps_of(lead)
    template = steps[step - 1]
    first_subject = _one_line(render(steps[0].subject, values))
    subject = _one_line(render(template.subject, values)) if template.subject.strip() else ""
    if not subject:
        subject = first_subject if step == 1 else f"Re: {first_subject}"
    body = render(template.body, values)
    footer = render(campaign.footer, values)
    return subject, (f"{body}\n\n{footer}" if footer else body)


def links_site(campaign: "Campaign", lead: "Lead", step: int) -> bool:
    """Email ``step`` (1 = the first) to ``lead`` or one before it had the ``{Website}`` link."""
    texts = [campaign.footer] + [t for s in campaign.steps_of(lead)[:step] for t in (s.subject, s.body)]
    return any("Website" in used_fields(t) for t in texts)


def missing_fields(campaign: "Campaign", lead: "Lead", cfg: "Settings") -> list[str]:
    """Placeholders the lead's emails need (used outside optional parts) that have no value."""
    values = lead_values(campaign, lead, cfg)
    steps = campaign.steps_of(lead) if lead.variant else campaign.all_steps()
    texts = [campaign.footer] + [t for s in steps for t in (s.subject, s.body)]
    needed = set().union(*(required_fields(t) for t in texts))
    return sorted(n for n in needed if not values.get(n))


def _one_line(text: str) -> str:
    return " ".join(text.split())
