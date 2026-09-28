import base64

import httpx
import pytest

from app.config import Settings
from app.enrichment.websearch import MAX_FAILURES, WebSearch, parse_bing, parse_duckduckgo
from app.scraper.parsing import unwrap_redirect

SITE = "https://roofing4texas.com/"
BING_WRAPPED = ("https://www.bing.com/ck/a?!&&p=abc&u=a1"
                + base64.urlsafe_b64encode(SITE.encode()).decode().rstrip("=") + "&ntb=1")

DDG_HTML = """
<div id="links" class="results">
  <div class="result results_links results_links_deep result--ad">
    <h2 class="result__title"><a class="result__a" href="https://duckduckgo.com/y.js?ad_domain=ads.com&amp;u3=x">Ad</a></h2>
  </div>
  <div class="result results_links results_links_deep web-result">
    <h2 class="result__title"><a rel="nofollow" class="result__a"
        href="//duckduckgo.com/l/?uddg=https%3A%2F%2Froofing4texas.com%2F&amp;rut=abc">Roofing Solutions Enterprises</a></h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Froofing4texas.com%2F">(281) 932-4462</a>
  </div>
  <div class="result results_links results_links_deep web-result">
    <h2 class="result__title"><a class="result__a" href="https://www.yelp.com/biz/roofing-solutions-houston">Yelp</a></h2>
  </div>
</div>
"""

BING_HTML = f"""
<ol id="b_results">
  <li class="b_algo"><h2><a href="{BING_WRAPPED.replace('&', '&amp;')}">Roofing Solutions Enterprises</a></h2></li>
  <li class="b_algo"><h2><a href="https://www.bbb.org/us/tx/houston/profile/roofing">BBB</a></h2></li>
  <li class="b_ad"><h2><a href="https://ads.example.com/">Ad</a></h2></li>
</ol>
"""


@pytest.mark.parametrize("wrapped, real", [
    ("https://l.facebook.com/l.php?u=https%3A%2F%2Froofing4texas.com%2F&h=AT0", SITE),
    ("https://duckduckgo.com/l/?uddg=https%3A%2F%2Froofing4texas.com%2F&rut=1", SITE),
    ("https://www.google.com/url?q=https://roofing4texas.com/&sa=U", SITE),
    ("https://www.yelp.com/biz_redir?url=https%3A%2F%2Froofing4texas.com%2F&website_link_type=website", SITE),
    (BING_WRAPPED, SITE),
    (SITE, SITE),
    ("https://example.com/search?q=https://other.com/", "https://example.com/search?q=https://other.com/"),
])
def test_unwrap_redirect(wrapped, real):
    assert unwrap_redirect(wrapped) == real


def test_parse_duckduckgo_skips_ads_and_unwraps():
    assert parse_duckduckgo(DDG_HTML) == [SITE, "https://www.yelp.com/biz/roofing-solutions-houston"]
    assert parse_duckduckgo("<html>no results</html>") == []


def test_parse_bing_unwraps_base64_links():
    assert parse_bing(BING_HTML) == [SITE, "https://www.bbb.org/us/tx/houston/profile/roofing"]


def _search(pages: dict[str, tuple[int, str]]):
    """WebSearch over a fake network: pages maps host -> (status, body); requests are recorded."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.host)
        status, body = pages[request.url.host]
        return httpx.Response(status, text=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebSearch(Settings(WEB_SEARCH_DELAY_S=0), client=client), asked


async def test_duckduckgo_is_asked_first():
    ws, asked = _search({"html.duckduckgo.com": (200, DDG_HTML), "www.bing.com": (200, BING_HTML)})
    assert (await ws.search("Roofing Solutions Enterprise LLC Houston"))[0] == SITE
    assert asked == ["html.duckduckgo.com"] and ws.searches == 1


async def test_bing_is_the_fallback_and_a_refusing_engine_is_not_asked_again():
    ws, asked = _search({"html.duckduckgo.com": (202, "<p>anomaly</p>"), "www.bing.com": (200, BING_HTML)})
    assert (await ws.search("q1"))[0] == SITE
    assert (await ws.search("q2"))[0] == SITE
    assert asked == ["html.duckduckgo.com", "www.bing.com", "www.bing.com"]
    assert not ws.disabled


async def test_search_stops_when_every_engine_refuses():
    ws, asked = _search({"html.duckduckgo.com": (429, ""), "www.bing.com": (200, "<p>Please solve the CAPTCHA</p>")})
    assert await ws.search("q1") == []
    assert ws.disabled and ws.searches == 0
    assert await ws.search("q2") == [] and len(asked) == 2, "no more requests once switched off"


async def test_search_stops_when_no_engine_can_be_reached():
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.host)
        raise httpx.ConnectError("blocked", request=request)

    ws = WebSearch(Settings(WEB_SEARCH_DELAY_S=0), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    for _ in range(MAX_FAILURES - 1):
        assert await ws.search("q") == [] and not ws.disabled, "a network hiccup is not the end"
    assert await ws.search("q") == [] and ws.disabled == "the search engines could not be reached"
    assert await ws.search("q") == [] and len(asked) == 2 * MAX_FAILURES


async def test_only_failures_in_a_row_switch_web_search_off():
    ddg_answers = iter([False, False, True, False, False])     # DuckDuckGo reachable on the 3rd search only

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "html.duckduckgo.com" and next(ddg_answers):
            return httpx.Response(200, text=DDG_HTML)
        raise httpx.ConnectError("flaky", request=request)

    ws = WebSearch(Settings(WEB_SEARCH_DELAY_S=0), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    results = [await ws.search(f"q{i}") for i in range(5)]
    assert [bool(r) for r in results] == [False, False, True, False, False]
    assert not ws.disabled and ws.searches == 1


async def test_an_empty_result_page_is_not_a_refusal():
    ws, _ = _search({"html.duckduckgo.com": (200, "<html><div id='links'></div></html>"), "www.bing.com": (200, "")})
    assert await ws.search("very rare business") == [] and not ws.disabled and ws.searches == 1


async def test_bing_can_be_the_first_engine():
    ws, asked = _search({"html.duckduckgo.com": (200, DDG_HTML), "www.bing.com": (200, BING_HTML)})
    ws.cfg = Settings(WEB_SEARCH_DELAY_S=0, WEB_SEARCH_ENGINE="bing")
    await ws.search("q")
    assert asked == ["www.bing.com"]
