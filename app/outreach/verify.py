"""Is there a mailbox behind an address? Asked once, just before a business's first email (docs/OUTREACH.md).

Bounces hurt the mailbox's reputation: over about 2 % of the emails sent, Google starts filing them as spam,
and the autopilot stops at 2 bounces in 20. ``domains.domain_problem`` (free, DNS only) catches domains that
can't receive email; this asks an email verifier whether the mailbox itself exists. Checking that ourselves
would need port 25, which Oracle Cloud and home internet both block.

The verifier is Reoon (reoon.com, free: 20 checks a day, the panel needs at most one per first email) in
"power" mode, the one that asks the mail server. Its key is ``EMAIL_VERIFY_KEY``; without it only the DNS check
runs. Skipped: ``invalid``, ``disabled``, ``disposable``, ``spamtrap``, ``inbox_full``. Sent: ``safe``,
``role_account`` (info@ …), ``catch_all`` (the domain takes any name - can't tell) and ``unknown``. When the
verifier can't answer - no credits left, a timeout, a bad key - the address counts as fine (only a clear "no"
skips a business) and the problem shows in the master sheet's Status tab.

The key travels in the URL, so nothing here logs a URL or an httpx error's text.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

import httpx

from app.config import Settings
from app.outreach.domains import domain_problem

log = logging.getLogger(__name__)

VERIFY_URL = "https://emailverifier.reoon.com/api/v1/verify"
TIMEOUT_S = 120                 # power mode asks the mail server: "a few seconds to more than a minute"
SKIP = {
    "invalid": "the mailbox doesn't exist",
    "disabled": "the mailbox has been shut down",
    "disposable": "it is a throwaway address",
    "spamtrap": "it is a spam trap",
    "inbox_full": "the mailbox is full",
}
SEND = {"safe", "role_account", "catch_all", "unknown"}

Fetch = Callable[[str, str], Awaitable[dict]]


async def _fetch(address: str, key: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        response = await client.get(VERIFY_URL, params={"email": address, "key": key, "mode": "power"})
        if response.status_code >= 400:
            try:
                reason = str(response.json().get("reason") or "")
            except ValueError:
                reason = ""
            return {"status": "error", "reason": reason or f"HTTP {response.status_code}"}
        return response.json()


class AddressCheck:
    """``await check(address)`` - why not to email it ('' = go ahead); the manager's ``check_domain``."""

    def __init__(self, cfg: Settings, fetch: Fetch = _fetch, domain_check=domain_problem,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.cfg = cfg
        self.fetch = fetch
        self.domain_check = domain_check
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.checked = 0            # since the panel started
        self.skipped = 0
        self.last_at = ""
        self.last_error = ""        # the verifier's last failure, '' once it answers again
        self.last_error_at = ""

    @property
    def on(self) -> bool:
        return bool(self.cfg.EMAIL_VERIFY_KEY.strip())

    async def __call__(self, address: str) -> str:
        problem = await self.domain_check(address)
        if problem or not self.on:
            return problem
        now = self.clock().isoformat(timespec="seconds")
        try:
            answer = await self.fetch(address.strip(), self.cfg.EMAIL_VERIFY_KEY.strip())
        except (httpx.HTTPError, ValueError) as exc:
            return self._failed(type(exc).__name__, now)
        status = str(answer.get("status") or "").lower()
        if status == "error" or (status not in SKIP and status not in SEND):
            return self._failed(str(answer.get("reason") or f"unexpected answer {status!r}")[:200], now)
        self.checked += 1
        self.last_at, self.last_error, self.last_error_at = now, "", ""
        if status in SKIP:
            self.skipped += 1
            return f"{SKIP[status]} (checked with Reoon)"
        return ""

    def _failed(self, why: str, now: str) -> str:
        if why != self.last_error:
            log.warning("Email verifier: couldn't check an address (%s) - it is emailed anyway", why)
        self.last_error, self.last_error_at = why, now
        return ""

    def status(self) -> dict:
        return {"on": self.on, "checked": self.checked, "skipped": self.skipped, "last_at": self.last_at,
                "error": self.last_error, "error_at": self.last_error_at}
