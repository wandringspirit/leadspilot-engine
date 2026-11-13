"""The address check before a first email: the DNS check, then Reoon (app/outreach/verify.py)."""
import asyncio

import httpx

from app.config import Settings
from app.outreach.verify import AddressCheck


def checker(answers=None, key="k-123", domain=""):
    asked = []

    async def fetch(address, api_key):
        asked.append((address, api_key))
        answer = (answers or {}).get(address, {"status": "safe"})
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def domain_check(address):
        return domain

    cfg = Settings(_env_file=None, EMAIL_VERIFY_KEY=key)
    return AddressCheck(cfg, fetch=fetch, domain_check=domain_check), asked


def run(check, address):
    return asyncio.run(check(address))


def test_mailboxes_that_dont_exist_are_skipped():
    check, asked = checker({"gone@gmail.com": {"status": "invalid"}, "off@x.com": {"status": "disabled"},
                            "full@x.com": {"status": "inbox_full"}})
    assert run(check, "gone@gmail.com") == "the mailbox doesn't exist (checked with Reoon)"
    assert run(check, "off@x.com") == "the mailbox has been shut down (checked with Reoon)"
    assert run(check, "full@x.com") == "the mailbox is full (checked with Reoon)"
    assert asked[0] == ("gone@gmail.com", "k-123")
    assert check.status() | {"last_at": ""} == {"on": True, "checked": 3, "skipped": 3, "last_at": "", "error": "",
                                                "error_at": ""}


def test_good_role_catch_all_and_unknown_addresses_are_emailed():
    check, _ = checker({"a@x.com": {"status": "safe"}, "info@x.com": {"status": "role_account"},
                        "b@any.com": {"status": "catch_all"}, "c@slow.com": {"status": "unknown"}})
    assert [run(check, a) for a in ("a@x.com", "info@x.com", "b@any.com", "c@slow.com")] == ["", "", "", ""]
    assert check.status()["skipped"] == 0


def test_a_bad_domain_is_skipped_without_asking_the_verifier():
    check, asked = checker(domain="the domain nope.example doesn't exist")
    assert run(check, "x@nope.example") == "the domain nope.example doesn't exist"
    assert asked == []


def test_without_a_key_only_the_domain_is_checked():
    check, asked = checker(key="")
    assert run(check, "a@x.com") == "" and asked == [] and check.status()["on"] is False


def test_when_the_verifier_fails_the_address_is_emailed_and_the_problem_shown():
    check, _ = checker({"a@x.com": {"status": "error", "reason": "Not enough credits"},
                        "b@x.com": httpx.ReadTimeout("slow"), "c@x.com": {"status": "what?"}})
    assert run(check, "a@x.com") == ""
    assert check.status()["error"] == "Not enough credits"
    assert run(check, "b@x.com") == "" and check.status()["error"] == "ReadTimeout"
    assert run(check, "c@x.com") == "" and "unexpected answer" in check.status()["error"]
    assert run(check, "d@x.com") == "" and check.status()["error"] == ""      # it answers again
    assert check.status()["checked"] == 1
