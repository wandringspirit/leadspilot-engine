import asyncio
import logging

import pytest

from app.logbuffer import LogBuffer, current_job_id


@pytest.fixture
def buf():
    buffer = LogBuffer(capacity=50)
    logger = logging.getLogger("test.logbuffer")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(buffer)
    yield buffer, logger
    logger.removeHandler(buffer)


def test_records_are_stored_with_increasing_seq(buf):
    buffer, logger = buf
    logger.info("first %s", "one")
    logger.warning("second")
    data = buffer.entries()
    assert [e["message"] for e in data["entries"]] == ["first one", "second"]
    assert [e["seq"] for e in data["entries"]] == [1, 2]
    assert data["entries"][0]["level"] == "INFO" and data["entries"][1]["level"] == "WARNING"
    assert data["entries"][0]["logger"] == "test.logbuffer"
    assert data["last_seq"] == 2 and data["next"] == 2


def test_since_cursor_returns_only_new_lines(buf):
    buffer, logger = buf
    for i in range(5):
        logger.info("line %d", i)
    first = buffer.entries()
    assert len(first["entries"]) == 5
    assert buffer.entries(since=first["next"])["entries"] == []
    logger.info("line 5")
    data = buffer.entries(since=first["next"])
    assert [e["message"] for e in data["entries"]] == ["line 5"]
    assert data["next"] == 6


def test_limit_and_truncated_flag(buf):
    buffer, logger = buf
    for i in range(10):
        logger.info("line %d", i)
    page1 = buffer.entries(since=0, limit=4)
    assert [e["seq"] for e in page1["entries"]] == [1, 2, 3, 4]
    assert page1["truncated"] is True and page1["next"] == 4
    page2 = buffer.entries(since=page1["next"], limit=100)
    assert [e["seq"] for e in page2["entries"]] == list(range(5, 11))
    assert page2["truncated"] is False and page2["next"] == 10
    tail = buffer.entries(limit=3)  # initial load -> newest lines
    assert [e["seq"] for e in tail["entries"]] == [8, 9, 10]


def test_ring_buffer_drops_oldest(buf):
    buffer, logger = buf
    for i in range(60):
        logger.info("line %d", i)
    data = buffer.entries(since=0, limit=1000)
    assert len(data["entries"]) == 50
    assert data["first_seq"] == 11 and data["last_seq"] == 60


def test_level_filter_skips_ahead(buf):
    buffer, logger = buf
    logger.info("info")
    logger.warning("warn")
    logger.error("boom")
    data = buffer.entries(since=0, min_level=logging.WARNING)
    assert [e["level"] for e in data["entries"]] == ["WARNING", "ERROR"]
    assert data["next"] == 3


def test_exceptions_include_traceback(buf):
    buffer, logger = buf
    try:
        raise ValueError("bad thing")
    except ValueError:
        logger.exception("failed")
    entry = buffer.entries()["entries"][0]
    assert entry["level"] == "ERROR"
    assert "failed" in entry["message"] and "ValueError: bad thing" in entry["message"]


def test_excluded_loggers_are_ignored():
    buffer = LogBuffer(capacity=10)
    noisy = logging.getLogger("uvicorn.access")
    noisy.addHandler(buffer)
    try:
        noisy.info("GET / 200")
    finally:
        noisy.removeHandler(buffer)
    assert buffer.entries()["entries"] == []


def test_job_id_tag_follows_asyncio_tasks(buf):
    buffer, logger = buf

    async def job(job_id: str):
        current_job_id.set(job_id)
        logger.info("in %s", job_id)

        async def child():
            logger.info("child of %s", job_id)

        await asyncio.create_task(child())

    async def main():
        logger.info("untagged")
        await asyncio.gather(job("job_a"), job("job_b"))

    asyncio.run(main())
    entries = buffer.entries()["entries"]
    tags = {e["message"]: e["job_id"] for e in entries}
    assert tags["untagged"] is None
    assert tags["in job_a"] == "job_a" and tags["child of job_a"] == "job_a"
    assert tags["in job_b"] == "job_b" and tags["child of job_b"] == "job_b"
    only_a = buffer.entries(job_id="job_a")["entries"]
    assert {e["message"] for e in only_a} == {"in job_a", "child of job_a"}
