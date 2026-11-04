"""Can an address's domain receive email (app/outreach/domains.py)."""
from __future__ import annotations

import asyncio

import httpx

from app.outreach.domains import RECORD_A, RECORD_AAAA, RECORD_MX, domain_problem


def lookup_of(answers: dict[int, dict]):
    async def lookup(_name: str, record: int) -> dict:
        answer = answers[record]
        if isinstance(answer, Exception):
            raise answer
        return answer
    return lookup


def problem(address: str, answers: dict[int, dict]) -> str:
    return asyncio.run(domain_problem(address, lookup_of(answers)))


def mx(*hosts: str) -> dict:
    return {"Status": 0, "Answer": [{"type": RECORD_MX, "data": host} for host in hosts]}


def test_a_domain_with_a_mail_server_is_fine():
    assert problem("a@klmroofing.com", {RECORD_MX: mx("1 ASPMX.L.GOOGLE.com.")}) == ""


def test_big_mail_providers_are_not_looked_up():
    assert problem("a@gmail.com", {}) == ""


def test_a_domain_that_does_not_exist():
    assert problem("a@nope.example", {RECORD_MX: {"Status": 3}}) == "the domain nope.example doesn't exist"


def test_a_domain_that_says_it_receives_no_email():
    assert "receives no email" in problem("a@x.example", {RECORD_MX: mx("0 .")})


def test_no_mail_server_but_an_address_can_still_receive():
    answers = {RECORD_MX: {"Status": 0}, RECORD_A: {"Status": 0, "Answer": [{"type": RECORD_A, "data": "1.2.3.4"}]}}
    assert problem("a@x.example", answers) == ""


def test_no_mail_server_and_no_address():
    answers = {RECORD_MX: {"Status": 0}, RECORD_A: {"Status": 0}, RECORD_AAAA: {"Status": 0}}
    assert problem("a@x.example", answers) == "the domain x.example has no mail server"


def test_a_failed_lookup_never_skips_a_business():
    assert problem("a@x.example", {RECORD_MX: httpx.ConnectError("no internet")}) == ""
    assert problem("a@x.example", {RECORD_MX: {"Status": 2}}) == ""                       # the DNS server failed
    answers = {RECORD_MX: {"Status": 0}, RECORD_A: httpx.ReadTimeout("slow")}
    assert problem("a@x.example", answers) == ""
