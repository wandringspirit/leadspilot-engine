from app.enrichment.extractors import (
    candidate_pages,
    clean_email,
    decode_cfemail,
    deobfuscate,
    emails_in_text,
    extract_from_html,
    score_email,
)


def test_clean_email_accepts_valid_and_normalises():
    assert clean_email("Info@Example-Roofing.com") == "info@example-roofing.com"
    assert clean_email("mailto:sales@acme.com?subject=hi") == "sales@acme.com"
    assert clean_email("<office@acme.com>") == "office@acme.com"


def test_clean_email_rejects_only_non_addresses():
    assert clean_email("logo@2x.png") == ""          # an asset file name, not an address
    assert clean_email("not-an-email") == ""
    assert clean_email("a..b@acme.com") == ""


def test_clean_email_does_no_junk_filtering():
    for email in ("user@example.com", "abc@sentry.io", "someone@yourdomain.com", "john@smithplumbing.com",
                  "info@thecleaningcompany.com", "sales@greatest.com", "support@facebook.com"):
        assert clean_email(email) == email


def test_emails_in_text():
    text = "Call or write to Info@JoesPlumbing.com, or joe [at] gmail [dot] com. Logo: logo@2x.png"
    assert emails_in_text(text) == ["info@joesplumbing.com", "joe@gmail.com"]
    assert emails_in_text("") == []


def test_decode_cfemail():
    # "info@acme.com" encoded with key 0x5a
    encoded = "5a" + "".join(f"{ord(c) ^ 0x5A:02x}" for c in "info@acme.com")
    assert decode_cfemail(encoded) == "info@acme.com"
    assert decode_cfemail("zz") == ""


def test_deobfuscate_variants():
    assert deobfuscate("Reach us at office [at] acme [dot] com today") == ["office@acme.com"]
    assert deobfuscate("sales (at) acme.com") == ["sales@acme.com"]
    assert deobfuscate("nothing here") == []


def test_score_prefers_same_domain_and_roles():
    same, s1 = score_email("info@acme.com", "acme.com")
    other, s2 = score_email("bob@gmail.com", "acme.com")
    _, s3 = score_email("noreply@acme.com", "acme.com")
    assert same and not other
    assert s1 > s2 > s3


def _cf(email: str, key: int = 0x5A) -> str:
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in email)


HTML = f"""
<html><head>
<script type="application/ld+json">{{"@type":"LocalBusiness","email":"hello@acme.com","telephone":"+1 817-555-0100"}}</script>
<script>var junk = "tracker@sentry.io";</script>
</head><body>
<a href="mailto:info@acme.com?subject=Quote">Email us</a>
<a href="tel:+18175550199">Call</a>
<span class="__cf_email__" data-cfemail="{_cf('contact@acme.com')}">[email protected]</span>
<p>Or write to office [at] acme [dot] com. Call (817) 555-0100.</p>
<a href="/contact-us">Contact</a>
<a href="/about">About the team</a>
<a href="https://facebook.com/acme">Facebook</a>
<a href="/gallery">Gallery</a>
<img src="/img/hero@2x.png">
</body></html>
"""


def test_extract_from_html_collects_everything():
    result = extract_from_html(HTML, "https://www.acme.com/", "https://www.acme.com/")
    emails = [h.email for h in result.emails]
    assert "info@acme.com" in emails
    assert "hello@acme.com" in emails
    assert "office@acme.com" in emails
    assert "contact@acme.com" in emails
    assert "tracker@sentry.io" in emails, "no junk filtering - it is kept, ranked below the site's own"
    assert emails.index("tracker@sentry.io") > emails.index("info@acme.com")
    assert "hero@2x.png" not in emails
    assert all(h.same_domain for h in result.emails if h.email.endswith("@acme.com"))
    assert result.emails[0].email in {"info@acme.com", "hello@acme.com", "office@acme.com"}
    assert "+18175550100" in result.phones
    assert "+18175550199" in result.phones
    assert "https://www.acme.com/contact-us" in result.contact_links
    assert "https://www.acme.com/about" in result.contact_links
    assert not any("facebook" in link or "gallery" in link for link in result.contact_links)


def test_extract_from_html_handles_empty():
    result = extract_from_html("", "https://x.com", "https://x.com")
    assert result.emails == [] and result.phones == [] and result.contact_links == []


def test_candidate_pages_orders_and_limits():
    pages = candidate_pages("https://www.acme.com/", ["https://www.acme.com/contact-us", "https://www.acme.com/"], 4)
    assert pages[0] == "https://www.acme.com/contact-us"
    assert "https://www.acme.com/contact" in pages
    assert len(pages) == 4
    assert "https://www.acme.com/" not in pages


PROFILE_HTML = r"""
<html><head>
<script>{"page":{"name":"Roofing Solutions","website":"https:\/\/roofing4texas.com\/","category":"Roofer"}}</script>
<script type="application/ld+json">{"@type":"LocalBusiness","url":"https://www.facebook.com/RoofingSolutions",
  "sameAs":["https://www.instagram.com/roofingsolutions/"]}</script>
</head><body>
  <a href="/RoofingSolutions/photos">Photos</a>
  <a href="https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.roofing4texas.com%2Fcontact%23form&amp;h=AT0">roofing4texas.com</a>
  <a href="https://roofing4texas.com/">duplicate</a>
  <a href="tel:+12819324462">Call</a>
</body></html>
"""


def test_extract_from_html_lists_links_to_other_sites():
    result = extract_from_html(PROFILE_HTML, "https://www.facebook.com/RoofingSolutions",
                               "https://www.facebook.com/RoofingSolutions")
    # the website named in the page data first, redirects unwrapped, the page's own site left out
    assert result.external_links == ["https://roofing4texas.com/", "https://www.instagram.com/roofingsolutions/",
                                     "https://www.roofing4texas.com/contact"]
    assert extract_from_html("<p>no links</p>", "https://a.com/", "https://a.com/").external_links == []
