"""Tests for capture.py — the spike 01 capture tap.

The one property that matters: capture must never disturb the live bot.
Everything here is proof of that promise — disabled means no-op, failures
are swallowed, and a write that succeeds is faithful.
"""

import json

import pytest

import capture


@pytest.fixture(autouse=True)
def restore_capture_dir(monkeypatch):
    """Every test controls capture.CAPTURE_DIR explicitly; restore after."""
    monkeypatch.setattr(capture, "CAPTURE_DIR", "")


@pytest.fixture
def enabled(tmp_path, monkeypatch):
    """Capture pointed at a real temp directory."""
    monkeypatch.setattr(capture, "CAPTURE_DIR", str(tmp_path))
    return tmp_path


def read_events(capture_dir):
    """All captured events across files, in order."""
    events = []
    for path in sorted(capture_dir.glob("twitch-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            events.append(json.loads(line))
    return events


class TestDisabledIsNoOp:
    def test_record_writes_nothing(self, tmp_path, monkeypatch):
        # Disabled = CAPTURE_DIR empty; even with a writable cwd, nothing lands.
        monkeypatch.setattr(capture, "CAPTURE_DIR", "")
        monkeypatch.chdir(tmp_path)
        capture.record("chat", content="hello")
        assert list(tmp_path.iterdir()) == []

    def test_record_chat_writes_nothing(self):
        message = type(
            "Message", (), {"author": None, "channel": None, "content": "hi"}
        )()
        capture.record_chat(message)  # CAPTURE_DIR is "" — must not raise
        assert capture.is_enabled() is False


class TestFaithfulRecording:
    def test_record_chat_round_trip(self, enabled):
        author = type("Author", (), {"name": "kat", "display_name": "Kat", "id": 1})()
        channel = type("Channel", (), {"name": "transfaeries"})()
        message = type(
            "Message",
            (),
            {
                "author": author,
                "channel": channel,
                "content": "hello faebot",
                "id": "msg-1",
                "timestamp": "2026-08-11T00:00:00Z",
                "echo": False,
                "tags": {"bits": "100"},
            },
        )()
        capture.record_chat(message)

        (event,) = read_events(enabled)
        assert event["kind"] == "chat"
        assert event["author"] == "kat"
        assert event["channel"] == "transfaeries"
        assert event["content"] == "hello faebot"
        assert event["tags"] == {"bits": "100"}
        assert "captured_at" in event

    def test_record_voice_keeps_whisper_meta(self, enabled):
        capture.record_voice(
            "transfaeries", "chat is being lovely", language="en", duration=2.5
        )
        (event,) = read_events(enabled)
        assert event["kind"] == "voice"
        assert event["text"] == "chat is being lovely"
        assert event["language"] == "en"
        assert event["duration"] == 2.5

    def test_raw_skips_only_keepalives(self, enabled):
        capture.record_raw(
            "PING :tmi.twitch.tv\r\n:ronni!ronni@ronni.tmi.twitch.tv JOIN #dallas\r\nPONG"
        )
        (event,) = read_events(enabled)
        assert event["kind"] == "raw"
        assert "JOIN" in event["line"]

    def test_appends_never_truncates(self, enabled):
        capture.record("chat", content="first")
        capture.record("chat", content="second")
        assert [e["content"] for e in read_events(enabled)] == ["first", "second"]


class TestNeverBreaksTheBot:
    def test_unwritable_dir_is_swallowed(self, tmp_path, monkeypatch):
        # A regular file where a directory is needed: every write will fail.
        blocker = tmp_path / "blocker"
        blocker.touch()
        monkeypatch.setattr(capture, "CAPTURE_DIR", str(blocker / "impossible"))
        capture.record("chat", content="must not raise")

    def test_broken_message_object_is_swallowed(self, enabled):
        class ExplodesOnTouch:
            def __getattr__(self, name):
                raise RuntimeError("TwitchIO did something weird")

        capture.record_chat(ExplodesOnTouch())
        assert read_events(enabled) == []

    def test_unserialisable_field_is_swallowed(self, enabled):
        capture.record("raw", line=object())  # json.dumps(default=str) saves this…
        capture.record("chat", content=lambda: None)  # …but a callable in a field
        # Either way: nothing raised, and whatever landed is a string.
        for event in read_events(enabled):
            for value in event.values():
                assert isinstance(value, str)
