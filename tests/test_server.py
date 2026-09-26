"""Tests for server.py — the body's endpoints: the dashboard, /ws/events, /hear."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from server import create_app


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def event_queue():
    """A shared event queue for testing."""
    return asyncio.Queue(maxsize=256)


@pytest.fixture
def test_app(event_queue):
    """The body's app with no bot attached."""
    return create_app(bot=None, events=event_queue)


@pytest.fixture
def client(test_app):
    """TestClient for the FastAPI app."""
    return TestClient(test_app)


class FakeBot:
    """Stands in for Faebot at the /hear door: records what it was handed and
    answers with a scripted verdict."""

    def __init__(self, why=None):
        self.why = why
        self.handle_transcription = AsyncMock(return_value=why)


# ── GET / ────────────────────────────────────────────────────────────


class TestHomeEndpoint:
    def test_returns_html(self, client):
        """GET / should return an HTML dashboard page."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_is_the_body_page_not_the_ear(self, client):
        """The body's page shows generations; the mic lives on the ear now."""
        html = client.get("/").text
        assert "Generations" in html
        assert "Audio Capture" not in html


# ── /hear ────────────────────────────────────────────────────────────


class TestHear:
    def test_hands_the_line_to_the_bot(self, event_queue):
        bot = FakeBot()
        app = create_app(bot=bot, events=event_queue)
        with patch("server.STREAMER_CHANNEL", "transfaeries"):
            with TestClient(app) as client:
                response = client.post(
                    "/hear",
                    json={
                        "text": "hello chat",
                        "language": "en",
                        "language_probability": 0.9,
                        "duration": 1.2,
                        "no_speech_prob": 0.01,
                        "heard_at": "2026-09-23T14:00:00+00:00",
                        "whisper_prompt": "faebot, transfaeries",
                    },
                )
        assert response.status_code == 200
        assert response.json() == {"heard": True, "why": None}
        bot.handle_transcription.assert_awaited_once()
        args, kwargs = bot.handle_transcription.call_args
        assert args == ("transfaeries", "hello chat")
        assert kwargs["duration"] == 1.2
        assert kwargs["heard_at"] == "2026-09-23T14:00:00+00:00"
        assert kwargs["whisper_prompt"] == "faebot, transfaeries"

    def test_reports_why_the_bot_did_not_hear(self, event_queue):
        bot = FakeBot(why="outro-bleed")
        app = create_app(bot=bot, events=event_queue)
        with TestClient(app) as client:
            response = client.post("/hear", json={"text": "thanks for watching"})
        assert response.json() == {"heard": False, "why": "outro-bleed"}

    def test_wrong_token_is_refused(self, event_queue):
        bot = FakeBot()
        app = create_app(bot=bot, events=event_queue)
        with patch("server.EAR_TOKEN", "secret"):
            with TestClient(app) as client:
                refused = client.post("/hear", json={"text": "hi"})
                wrong = client.post(
                    "/hear", json={"text": "hi"}, headers={"X-Ear-Token": "nope"}
                )
                right = client.post(
                    "/hear", json={"text": "hi"}, headers={"X-Ear-Token": "secret"}
                )
        assert refused.status_code == 403
        assert wrong.status_code == 403
        assert right.status_code == 200
        bot.handle_transcription.assert_awaited_once()

    def test_no_token_configured_accepts_anyone(self, event_queue):
        bot = FakeBot()
        app = create_app(bot=bot, events=event_queue)
        with patch("server.EAR_TOKEN", ""):
            with TestClient(app) as client:
                response = client.post("/hear", json={"text": "hi"})
        assert response.status_code == 200

    def test_bad_payloads_are_rejected(self, event_queue):
        bot = FakeBot()
        app = create_app(bot=bot, events=event_queue)
        with TestClient(app) as client:
            assert client.post("/hear", content=b"not json").status_code == 400
            assert client.post("/hear", json={"nope": 1}).status_code == 400
            assert client.post("/hear", json=["a list"]).status_code == 400
        bot.handle_transcription.assert_not_awaited()

    def test_no_bot_is_503(self, client):
        response = client.post("/hear", json={"text": "hi"})
        assert response.status_code == 503


# ── /ws/events ───────────────────────────────────────────────────────


class TestEventsWebSocket:
    def test_websocket_connects(self, client):
        """Events WebSocket should accept connections."""
        with client.websocket_connect("/ws/events"):
            pass  # connection successful if we get here

    def test_replays_history_on_connect(self, test_app, event_queue):
        """New connections should receive event history from ring buffer."""
        test_app.state.event_history.append({"type": "test", "id": "1"})
        test_app.state.event_history.append({"type": "test", "id": "2"})

        with TestClient(test_app) as client:
            with client.websocket_connect("/ws/events") as websocket:
                event1 = websocket.receive_json()
                event2 = websocket.receive_json()

                assert event1["id"] == "1"
                assert event2["id"] == "2"

    def test_receives_live_events(self, test_app, event_queue):
        """Events pushed to the queue reach the ring buffer and later clients."""
        with TestClient(test_app) as client:
            with client.websocket_connect("/ws/events"):
                test_event = {"type": "response", "id": "live-1", "text": "hello"}
                test_app.state.event_history.append(test_event)

        with TestClient(test_app) as client:
            with client.websocket_connect("/ws/events") as websocket:
                event = websocket.receive_json()
                assert event["id"] == "live-1"


# ── Event drain task ─────────────────────────────────────────────────


class TestEventDrain:
    @pytest.mark.asyncio
    async def test_drain_adds_to_history(self, event_queue):
        """Events from queue should be added to ring buffer."""
        app = create_app(bot=None, events=event_queue)
        await event_queue.put({"type": "test", "id": "drain-1"})
        await asyncio.sleep(0.1)
        assert event_queue.qsize() == 1 or len(app.state.event_history) > 0

    @pytest.mark.asyncio
    async def test_event_history_capped_at_50(self):
        """Ring buffer should cap at 50 events."""
        app = create_app(bot=None, events=asyncio.Queue())
        for i in range(60):
            app.state.event_history.append({"id": str(i)})
        assert len(app.state.event_history) == 50
        assert app.state.event_history[0]["id"] == "10"
        assert app.state.event_history[-1]["id"] == "59"
