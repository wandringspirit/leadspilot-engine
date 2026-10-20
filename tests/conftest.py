"""Shared fixtures."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# The app's email loop, Drive uploads and website uploads must never run in the tests: they would use
# the real mailbox from .env, a Google Drive connected in data/ and the Cloudflare login. (Set before
# app.config is imported; environment variables win over .env.)
os.environ["OUTREACH_AUTOSEND"] = "false"
os.environ["DRIVE_AUTO_UPLOAD"] = "false"
os.environ["PREVIEW_SITES"] = "false"      # no websites built or uploaded to Cloudflare (tests/test_sites.py)
# ... and app.main's objects never see the real data/: the autopilot there (switched on) would start real
# Google Maps searches, and its campaigns, activity log and Drive sign-in are the real ones
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="leadspilot-tests-")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_record(**overrides) -> list:
    """Build a synthetic Google place record with the indexes we rely on."""
    rec: list = [None] * 260
    rec[11] = overrides.get("name", "Acme Roofing")
    rec[39] = overrides.get("address", "123 Main St, Arlington, TX 76010, United States")
    rec[2] = ["123 Main St", "Arlington, TX 76010", "United States"]
    rec[7] = [overrides.get("website", "https://www.acmeroofing.com/?utm_source=gmb"), "acmeroofing.com"]
    rec[178] = [["+1 817-555-0100", [["(817) 555-0100", 1]], None, overrides.get("phone", "+18175550100")]]
    rec[4] = [None, None, None, ["https://search.google.com/local/reviews?placeid=x", "126 reviews"],
              None, None, None, overrides.get("rating", 4.8), overrides.get("review_count", 126)]
    rec[78] = overrides.get("place_id", "ChIJTestPlaceId123456789")
    rec[10] = "0x864e62bbfe4e964d:0x75a60aab8b28895"
    rec[13] = ["Roofing contractor", "General contractor"]
    rec[9] = [None, None, 32.68, -97.10]
    rec[183] = [None, ["East Arlington", "123 Main St", "123 Main St", "Arlington", "76010", "Texas", "US"]]
    if "description" in overrides:
        rec[154] = [[overrides["description"]]]
    return rec


def rpc_body(*records: list) -> str:
    """Wrap records the way a /search?tbm=map response does (nested deep)."""
    payload = [None] * 70
    payload[0] = ["query", [[None] * 15]]
    payload[64] = [[None, r] for r in records]
    return ")]}'\n" + json.dumps(payload)


@pytest.fixture
def record() -> list:
    return make_record(description="Established in 2009. Full service roofing.")


@pytest.fixture
def sample_html_state(record) -> str:
    inner = ")]}'\n" + json.dumps([None, None, None, None, None, None, record])
    state = [None, None, None, [None, None, None, None, None, inner]]
    return ("<html><head><script>window.APP_INITIALIZATION_STATE="
            + json.dumps(state) + ";window.APP_FLAGS=[1];</script></head><body></body></html>")
