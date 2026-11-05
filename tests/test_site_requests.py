"""The preview websites' quote form: replies that say no, the page, and emailing requests to the business
(app/outreach/mailbox.py, app/outreach/site_requests.py, sites/render.py)."""
from __future__ import annotations

import json

import pytest

from app.outreach.mailbox import MailError, RecipientRefused, declined, reply_text, sender_address
from app.outreach.site_requests import SiteRequests
from sites.build import rebuild_all, trade_pack
from sites.render import render_lead
from tests.test_outreach import make_manager


@pytest.mark.parametrize("text", ["No", "no.", "NO\n\nSent from my iPhone", "Nope!", "not interested, thanks",
                                  "No thanks", "We don\u2019t need a website", "please remove me",
                                  "We already have a website\n> Hi,"])
def test_replies_that_say_no(text):
    assert declined(text)


@pytest.mark.parametrize("text", ["", "I like it. What does it take to own this?", "No problem, send it over",
                                  "Yes\n\nOn Thu, Piyush wrote:\n> Reply no and I won't write again\n> not interested",
                                  "How much?"])
def test_replies_that_dont(text):
    assert not declined(text)


def test_the_text_of_a_reply():
    raw = (b"From: a@b.com\r\nSubject: Re: x\r\nContent-Type: multipart/alternative; boundary=b\r\n\r\n"
           b"--b\r\nContent-Type: text/plain\r\n\r\nNo thanks\r\n--b\r\nContent-Type: text/html\r\n\r\n<p>No</p>\r\n--b--\r\n")
    assert reply_text(raw).strip() == "No thanks"
    html = b"From: a@b.com\r\nContent-Type: text/html\r\n\r\n<style>p{}</style><p>Sounds good</p>"
    assert reply_text(html).strip() == "Sounds good"


def test_the_form_posts_to_the_worker_and_asks_for_an_email(tmp_path):
    _, pack = trade_pack("Roofer")
    lead = {"business": "Hill Roofing", "city": "Frisco", "state": "TX", "phone": "(940) 555-0101",
            "email": "hill@gmail.com", "rating": None, "reviews": None, "maps_url": "", "place_id": "",
            "service_area": ["Frisco"]}
    render_lead(pack, lead, tmp_path / "hill")
    html = (tmp_path / "hill" / "index.html").read_text(encoding="utf-8")
    assert 'data-endpoint="/_q"' in html and 'data-phone="(940) 555-0101"' in html
    assert 'name="email"' in html and 'name="fax"' in html
    assert "didn't go through" in (tmp_path / "hill" / "site.js").read_text(encoding="utf-8")


def test_rebuilding_names_what_it_cannot_rebuild(tmp_path):
    built, problems = rebuild_all({"gone": {"job_id": "job_nope"}}, tmp_path)
    assert built == [] and "is gone" in problems[0]


class Sites:
    def __init__(self):
        self.sites = {"hill": {"business": "Hill Roofing", "email": "hill@gmail.com"}}

    def manifest(self):
        return self.sites

    def url(self, slug):
        return f"https://sites.test/{slug}/"


class Visits:
    on = True

    def classify(self, row):
        return row.get("as", "visitor")


def request(number=1, **fields):
    data = {"problem": "Storm damage", "address": "75034", "when": "This week", "name": "Ann Lee",
            "phone": "(214) 555-0101", "email": "", **fields}
    return {"id": number, "slug": "hill", "who": "h", "org": "", "country": "US", "ua": "", "copy": 0,
            "data": json.dumps(data)}


def make(tmp_path, rows):
    manager, mailbox, *_ = make_manager(tmp_path)
    finished = []

    async def fetch():
        return rows

    async def finish(ids):
        finished.extend(ids)

    return SiteRequests(manager.cfg, manager, Sites(), Visits(), fetch=fetch, finish=finish), manager, mailbox, finished


async def test_a_request_is_emailed_to_the_business_and_deleted(tmp_path):
    requests, _, mailbox, finished = make(tmp_path, [request(email="ann@x.com")])
    assert await requests.sync() == 1
    sent = mailbox.sent[0]
    assert sent["To"] == "hill@gmail.com" and sent["Reply-To"] == "ann@x.com" and "Ann Lee" in sent["Subject"]
    assert "Phone: (214) 555-0101" in sent.get_content() and "https://sites.test/hill/" in sent.get_content()
    assert finished == [1]


@pytest.mark.parametrize("who", ["you", "copy"])
async def test_your_own_test_comes_to_you(tmp_path, who):
    requests, manager, mailbox, _ = make(tmp_path, [{**request(), "as": who}])
    await requests.sync()
    assert mailbox.sent[0]["To"] == sender_address(manager.cfg) and "[Not sent to Hill Roofing]" in mailbox.sent[0]["Subject"]


@pytest.mark.parametrize("status", ["unsubscribed", "bounced", "failed"])
async def test_a_business_that_cant_be_emailed_doesnt_get_it(tmp_path, status):
    requests, manager, mailbox, finished = make(tmp_path, [request()])
    manager.contacted["hill@gmail.com"] = {"status": status}
    await requests.sync()
    assert mailbox.sent[0]["To"] == sender_address(manager.cfg) and finished == [1]


async def test_a_refused_address_comes_to_you(tmp_path):
    requests, manager, mailbox, finished = make(tmp_path, [request()])
    real = mailbox.send
    calls = []

    async def send(message):
        calls.append(message["To"])
        if len(calls) == 1:
            raise RecipientRefused("no such address")
        await real(message)

    mailbox.send = send
    await requests.sync()
    assert calls == ["hill@gmail.com", sender_address(manager.cfg)] and finished == [1]


async def test_a_request_that_cant_be_sent_stays_and_is_never_sent_twice(tmp_path):
    rows = [request(1), request(2)]
    requests, _, mailbox, finished = make(tmp_path, rows)
    mailbox.send_error = MailError("the mail server is away")
    assert await requests.sync() == 0 and finished == []
    mailbox.send_error = None

    async def broken(_ids):
        raise ValueError("the Worker is away")

    good, requests.finish = requests.finish, broken
    assert await requests.sync() == 2 and len(mailbox.sent) == 2
    requests.finish = good
    assert await requests.sync() == 0 and len(mailbox.sent) == 2 and finished == [1, 2]
