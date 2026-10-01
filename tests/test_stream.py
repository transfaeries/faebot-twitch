"""Tests for stream.py — the stream's state as a stamped fact."""

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core
import stream


NOW = datetime.datetime(2026, 10, 1, 1, 0, tzinfo=datetime.UTC)
OFF = stream.StreamState(live=False, title="NightReign", game="Elden Ring Nightreign")
ON = stream.StreamState(
    live=True,
    title="NightReign",
    game="Elden Ring Nightreign",
    started_at="2026-10-01T00:30:00+00:00",
)


class TestChangeLines:
    def test_first_read_says_the_whole_state(self):
        lines = stream.change_lines(None, OFF, NOW)
        assert lines == [
            'the stream is offline — title "NightReign", game Elden Ring Nightreign (read at 01:00 UTC)'
        ]

    def test_nothing_changed_nothing_said(self):
        assert stream.change_lines(OFF, OFF, NOW) == []
        assert stream.change_lines(ON, ON, NOW) == []

    def test_going_live_is_one_line_with_the_facts(self):
        lines = stream.change_lines(OFF, ON, NOW)
        assert lines == [
            'the stream went live at 01:00 UTC — title "NightReign", game Elden Ring Nightreign'
        ]

    def test_ending_names_when_it_started(self):
        lines = stream.change_lines(ON, OFF, NOW)
        assert lines == [
            "the stream ended at 01:00 UTC (it had been live since 00:30 UTC)"
        ]

    def test_a_title_that_moved_says_so(self):
        after = stream.StreamState(
            live=True,
            title="Resident Evil October",
            game="Resident Evil",
            started_at=ON.started_at,
        )
        lines = stream.change_lines(ON, after, NOW)
        assert lines == [
            'the stream title changed at 01:00 UTC: "NightReign" → "Resident Evil October"',
            "the game changed at 01:00 UTC: Elden Ring Nightreign → Resident Evil",
        ]


class TestReadState:
    @pytest.mark.asyncio
    async def test_live_from_the_stream(self):
        bot = MagicMock()
        started = datetime.datetime(2026, 10, 1, 0, 30, tzinfo=datetime.UTC)
        bot.fetch_streams = AsyncMock(
            return_value=[MagicMock(title="t", game_name="g", started_at=started)]
        )
        state = await stream.read_state(bot, "testchannel")
        assert state == stream.StreamState(True, "t", "g", started.isoformat())
        bot.fetch_streams.assert_awaited_once_with(user_logins=["testchannel"])

    @pytest.mark.asyncio
    async def test_offline_from_the_channel(self):
        bot = MagicMock()
        bot.fetch_streams = AsyncMock(return_value=[])
        bot.fetch_channel = AsyncMock(
            return_value=MagicMock(title="standing", game_name="Just Chatting")
        )
        state = await stream.read_state(bot, "testchannel")
        assert state == stream.StreamState(False, "standing", "Just Chatting")


class TestStreamWatch:
    def test_note_lays_lines_in_the_window_record_and_dashboard(self):
        core.ensure_conversation("testchannel")
        watch = stream.StreamWatch()
        events: asyncio.Queue = asyncio.Queue()
        with patch("stream.capture.record_stream_state") as record:
            first = watch.note("testchannel", OFF, events, NOW)
            again = watch.note("testchannel", OFF, events, NOW)
            live = watch.note("testchannel", ON, events, NOW)
        assert len(first) == 1 and first[0].startswith(core.MACHINERY)
        assert again == []
        assert "went live" in live[0]
        assert core.conversations["testchannel"].chatlog == first + live
        assert record.call_count == 2
        assert events.qsize() == 2
        event = events.get_nowait()
        assert event["type"] == "stream_state" and event["live"] is False
        assert watch.state["testchannel"] == ON

    @pytest.mark.asyncio
    async def test_a_failed_read_is_logged_and_the_watch_goes_on(self):
        core.ensure_conversation("testchannel")
        watch = stream.StreamWatch()
        bot = MagicMock()
        bot.fetch_streams = AsyncMock(side_effect=[RuntimeError("twitch down"), []])
        bot.fetch_channel = AsyncMock(return_value=MagicMock(title="t", game_name="g"))
        task = asyncio.create_task(watch.watch(bot, "testchannel", None, interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert watch.state["testchannel"] == stream.StreamState(False, "t", "g")
