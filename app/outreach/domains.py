"""Can an address's domain receive email at all? Asked once, just before a business's first email.

Bounces hurt the mailbox's reputation, and the autopilot stops after a few, so an address whose domain
doesn't exist or has no mail server isn't tried (docs/OUTREACH.md). This can't see whether the mailbox itself
exists: only sending can tell.

The answer comes from Cloudflare's DNS over HTTPS instead of the computer's own DNS, because home routers
remember "not found" answers. When the lookup fails - no internet, a timeout - the address counts as fine:
only a clear answer skips a business.

Pure apart from the lookup, which tests replace (tests/test_domains.py).
"""
from __future__ import annotations

from typing import Awaitable, Callable

import httpx

DOH_URL = "https://cloudflare-dns.com/dns-query"
TIMEOUT_S = 8
NXDOMAIN = 3            # DNS status: the name doesn't exist
RECORD_A, RECORD_MX, RECORD_AAAA = 1, 15, 28
# they receive email: no need to ask
KNOWN_DOMAINS = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
                 "icloud.com", "me.com", "aol.com", "att.net", "comcast.net", "sbcglobal.net", "verizon.net"}

Lookup = Callable[[str, int], Awaitable[dict]]


async def _lookup(name: str, record: int) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        response = await client.get(DOH_URL, params={"name": name, "type": record},
                                    headers={"Accept": "application/dns-json"})
        response.raise_for_status()
        return response.json()


def _records(answer: dict, record: int) -> list[str]:
    return [str(item.get("data", "")) for item in answer.get("Answer") or [] if item.get("type") == record]


async def domain_problem(address: str, lookup: Lookup = _lookup) -> str:
    """Why ``address``'s domain can't receive email - '' when it can, or when that can't be told."""
    domain = address.rpartition("@")[2].strip().lower().rstrip(".")
    if not domain or domain in KNOWN_DOMAINS:
        return ""
    try:
        answer = await lookup(domain, RECORD_MX)
        if answer.get("Status") == NXDOMAIN:
            return f"the domain {domain} doesn't exist"
        if answer.get("Status") != 0:
            return ""
        servers = _records(answer, RECORD_MX)
        if servers:
            hosts = [server.split()[-1].rstrip(".") for server in servers if server.split()]
            return "" if any(hosts) else f"the domain {domain} says it receives no email"
        # no mail server named: mail goes to the domain's own address, if it has one
        for record in (RECORD_A, RECORD_AAAA):
            answer = await lookup(domain, record)
            if answer.get("Status") != 0 or _records(answer, record):
                return ""
    except (httpx.HTTPError, ValueError, AttributeError):
        return ""
    return f"the domain {domain} has no mail server"
