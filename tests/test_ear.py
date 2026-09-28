"""Tests for ear.py — the wire to the body (BodyLink) and the utterance shape.

The audio WebSocket itself (VAD + Whisper) stays untested here: it needs the
models. What's tested is everything the split added — delivery, retry, the
spool, and draining in order.
"""

import json
import pytest
import pytest_asyncio
from aioresponses import aioresponses

import ear


HEAR = "http://body.test/hear"


@pytest.fixture
def spool(tmp_path):
    return tmp_path / "spool.jsonl"


@pytest.fixture
def link(spool):
    return ear.BodyLink(body_url="http://body.test", token="t", spool=spool, timeout=1)


@pytest_asyncio.fixture
async def closed(link):
    yield
    await link.close()


def line(text):
    return ear.utterance(text, "en", 0.9, 1.0, 0.01)


class TestUtterance:
    def test_carries_whisper_meta_and_a_clock(self):
        heard = line("hello")
        assert heard["text"] == "hello"
        assert heard["language"] == "en"
        assert heard["duration"] == 1.0
        assert heard["no_speech_prob"] == 0.01
        assert heard["heard_at"].endswith("+00:00")


class TestDeliver:
    @pytest.mark.asyncio
    async def test_delivers_and_returns_the_bodys_answer(self, link, closed):
        with aioresponses() as mocked:
            mocked.post(HEAR, payload={"heard": True, "why": None})
            answer = await link.deliver(line("hello"))
        assert answer == {"heard": True, "why": None}
        assert not link.spool.exists()

    @pytest.mark.asyncio
    async def test_sends_the_token(self, link, closed):
        with aioresponses() as mocked:
            mocked.post(HEAR, payload={"heard": True, "why": None})
            await link.deliver(line("hello"))
            request = list(mocked.requests.values())[0][0]
        assert request.kwargs["headers"]["X-Ear-Token"] == "t"

    @pytest.mark.asyncio
    async def test_retries_once_then_spools(self, link, closed, monkeypatch):
        async def no_sleep(_):
            return None

        monkeypatch.setattr(ear.asyncio, "sleep", no_sleep)
        with aioresponses() as mocked:
            mocked.post(HEAR, status=503)
            mocked.post(HEAR, status=503)
            answer = await link.deliver(line("hello"))
        assert answer is None
        spooled = [json.loads(row) for row in link.spool.read_text().splitlines()]
        assert [row["text"] for row in spooled] == ["hello"]

    @pytest.mark.asyncio
    async def test_second_try_succeeds(self, link, closed, monkeypatch):
        async def no_sleep(_):
            return None

        monkeypatch.setattr(ear.asyncio, "sleep", no_sleep)
        with aioresponses() as mocked:
            mocked.post(HEAR, status=503)
            mocked.post(HEAR, payload={"heard": True, "why": None})
            answer = await link.deliver(line("hello"))
        assert answer == {"heard": True, "why": None}
        assert not link.spool.exists()


class TestSpool:
    @pytest.mark.asyncio
    async def test_drains_in_order_and_empties(self, link, closed):
        link._spool(line("one"))
        link._spool(line("two"))
        with aioresponses() as mocked:
            mocked.post(HEAR, payload={"heard": True, "why": None})
            mocked.post(HEAR, payload={"heard": True, "why": None})
            assert await link.drain() is True
            sent = [
                call.kwargs["json"]["text"]
                for call in list(mocked.requests.values())[0]
            ]
        assert sent == ["one", "two"]
        assert not link.spool.exists()

    @pytest.mark.asyncio
    async def test_drain_stops_at_the_first_failure_and_keeps_the_rest(
        self, link, closed
    ):
        link._spool(line("one"))
        link._spool(line("two"))
        link._spool(line("three"))
        with aioresponses() as mocked:
            mocked.post(HEAR, payload={"heard": True, "why": None})
            mocked.post(HEAR, status=503)
            assert await link.drain() is False
        left = [json.loads(row)["text"] for row in link.spool.read_text().splitlines()]
        assert left == ["two", "three"]

    @pytest.mark.asyncio
    async def test_a_new_line_waits_behind_the_spool(self, link, closed, monkeypatch):
        """With something spooled and the body still down, a fresh line is
        spooled after it — never delivered out of order."""

        async def no_sleep(_):
            return None

        monkeypatch.setattr(ear.asyncio, "sleep", no_sleep)
        link._spool(line("old"))
        with aioresponses() as mocked:
            mocked.post(HEAR, status=503)  # the drain's attempt at "old"
            answer = await link.deliver(line("new"))
        assert answer is None
        left = [json.loads(row)["text"] for row in link.spool.read_text().splitlines()]
        assert left == ["old", "new"]

    @pytest.mark.asyncio
    async def test_a_new_line_follows_a_successful_drain(self, link, closed):
        link._spool(line("old"))
        with aioresponses() as mocked:
            mocked.post(HEAR, payload={"heard": True, "why": None})
            mocked.post(HEAR, payload={"heard": True, "why": None})
            answer = await link.deliver(line("new"))
        assert answer == {"heard": True, "why": None}
        assert not link.spool.exists()

    @pytest.mark.asyncio
    async def test_drain_with_no_spool_is_true(self, link, closed):
        assert await link.drain() is True

    @pytest.mark.asyncio
    async def test_a_torn_line_is_set_aside_and_the_rest_drains(self, link, closed):
        """A crash mid-write leaves half a line; it must not wedge the spool."""
        link._spool(line("one"))
        with open(link.spool, "a", encoding="utf-8") as spool_file:
            spool_file.write('{"text": "tw')  # no newline, no close
        with open(link.spool, "a", encoding="utf-8") as spool_file:
            spool_file.write("\n")
        link._spool(line("three"))
        with aioresponses() as mocked:
            mocked.post(HEAR, payload={"heard": True, "why": None})
            mocked.post(HEAR, payload={"heard": True, "why": None})
            assert await link.drain() is True
            sent = [
                call.kwargs["json"]["text"]
                for call in list(mocked.requests.values())[0]
            ]
        assert sent == ["one", "three"]
        assert not link.spool.exists()
        aside = [json.loads(row) for row in link.set_aside.read_text().splitlines()]
        assert len(aside) == 1
        assert aside[0]["line"] == '{"text": "tw'
        assert "unreadable" in aside[0]["why"]

    @pytest.mark.asyncio
    async def test_a_rejected_payload_is_set_aside_not_retried(self, link, closed):
        """400: the body heard us and will never take this line — one
        attempt, no spool, the line kept beside it for a human."""
        with aioresponses() as mocked:
            mocked.post(HEAR, status=400)
            answer = await link.deliver(line("bad"))
            attempts = len(list(mocked.requests.values())[0])
        assert answer is None
        assert attempts == 1
        assert not link.spool.exists()
        aside = [json.loads(row) for row in link.set_aside.read_text().splitlines()]
        assert json.loads(aside[0]["line"])["text"] == "bad"

    @pytest.mark.asyncio
    async def test_a_refused_token_keeps_the_spool_and_waits(self, link, closed):
        """403: ours to fix, not the line's fault — spool it, and the drain
        stops at it without setting anything aside."""
        with aioresponses() as mocked:
            mocked.post(HEAR, status=403)
            assert await link.deliver(line("one")) is None
        assert [
            json.loads(row)["text"] for row in link.spool.read_text().splitlines()
        ] == ["one"]
        with aioresponses() as mocked:
            mocked.post(HEAR, status=403)
            assert await link.drain() is False
        assert [
            json.loads(row)["text"] for row in link.spool.read_text().splitlines()
        ] == ["one"]
        assert not link.set_aside.exists()


def test_every_utterance_carries_its_own_id():
    """The ear names each line once; the spool keeps the name, so a line
    delivered late is still recognisably the same line."""
    first, second = ear.utterance("a", "en", 0.9, 1.0, 0.01), ear.utterance(
        "a", "en", 0.9, 1.0, 0.01
    )
    assert first["utterance_id"] and second["utterance_id"]
    assert first["utterance_id"] != second["utterance_id"]
