"""Tests for capture.py — the spike 01 capture tap.

The one property that matters: capture must never disturb the live bot.
Everything here is proof of that promise — disabled means no-op, failures
are swallowed, and a write that succeeds is faithful.
"""

import json

import datetime
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
    for path in sorted(capture_dir.glob("**/twitch-*.jsonl")):
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

    def test_record_faebot_pass_keeps_reason_and_reasoning(self, enabled):
        capture.record_faebot_pass(
            "transfaeries",
            "they're mid-conversation",
            generation_id="g1",
            reasoning="ember is talking to minou",
            elapsed=4.2,
        )
        (event,) = read_events(enabled)
        assert event["kind"] == "faebot_pass"
        assert event["reason"] == "they're mid-conversation"
        assert event["reasoning"] == "ember is talking to minou"
        assert event["elapsed"] == 4.2
        assert event["generation_id"] == "g1"

    def test_record_faebot_error_is_its_own_kind(self, enabled):
        capture.record_faebot_error(
            "transfaeries",
            "GenerationFailed: timed out",
            generation_id="g2",
            elapsed=90.2,
        )
        (event,) = read_events(enabled)
        assert event["kind"] == "faebot_error"
        assert event["error"] == "GenerationFailed: timed out"
        assert event["elapsed"] == 90.2

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
        # default=str stringifies most oddballs, so a genuinely unserialisable
        # field needs a circular reference (verified: json raises ValueError).
        ouroboros = {}
        ouroboros["self"] = ouroboros
        capture.record("chat", content=ouroboros)  # must not raise
        assert read_events(enabled) == []  # nothing written, no torn line


def test_captures_land_in_a_folder_per_month(enabled):
    """A file per UTC day, inside a folder per month."""
    capture.record("raw", line="PING")
    now = datetime.datetime.now(datetime.UTC)
    expected = (
        enabled / now.strftime("%Y-%m") / f"twitch-{now.strftime('%Y%m%d')}.jsonl"
    )
    assert expected.is_file()
    assert list(enabled.glob("twitch-*.jsonl")) == []  # nothing at the top level


def test_a_repeat_on_the_wire_is_its_own_kind(enabled):
    """The ear's stutter is kept as a fact about the wire, not as speech."""
    capture.record_hear_repeat("abc", "hello", {"heard": True, "why": None})
    (event,) = read_events(enabled)
    assert event["kind"] == "hear_repeat"
    assert event["utterance_id"] == "abc" and event["text"] == "hello"
    assert event["answer"] == {"heard": True, "why": None}


# ── the machinery's own rows: stream state, wake, restart ────────────


class TestMachineryRows:
    @pytest.mark.parametrize(
        "recorder, kind, extra",
        [
            (
                capture.record_stream_state,
                "stream_state",
                {"live": True, "title": "t", "game": "g", "started_at": None},
            ),
            (
                capture.record_wake,
                "wake",
                {"lines_read_back": 3, "stop_was_chosen": False},
            ),
            (capture.record_restart, "restart", {"how": "said", "said": None}),
        ],
    )
    def test_each_row_keeps_the_line_and_its_facts(
        self, enabled, recorder, kind, extra
    ):
        recorder("testchannel", "[the machinery] something happened", **extra)
        rows = read_events(enabled)
        assert len(rows) == 1
        row = rows[0]
        assert row["kind"] == kind
        assert row["channel"] == "testchannel"
        assert row["line"] == "[the machinery] something happened"
        for key, value in extra.items():
            assert row[key] == value

    def test_disabled_is_a_no_op(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        capture.record_wake("testchannel", "line", lines_read_back=0)
        assert list(tmp_path.iterdir()) == []

    def test_path_for_a_day(self, enabled):
        day = datetime.datetime(2026, 9, 26, tzinfo=datetime.UTC)
        assert capture.path_for(day).endswith("2026-09/twitch-20260926.jsonl")
