"""Tests for window.py — her window read back from the record, with the seam."""

import datetime
import json
import os
from unittest.mock import patch

import pytest

import capture
import core
import window


NOW = datetime.datetime(2026, 10, 1, 2, 10, tzinfo=datetime.UTC)


def row(kind, minutes_before=5, **fields):
    at = (NOW - datetime.timedelta(minutes=minutes_before)).isoformat()
    return {"kind": kind, "captured_at": at, "channel": "testchannel", **fields}


@pytest.fixture
def record(tmp_path):
    """A capture dir with today's file; returns a writer for rows."""
    with patch.object(capture, "CAPTURE_DIR", str(tmp_path)):
        path = capture.path_for(NOW)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        def write(*rows, day=NOW):
            target = capture.path_for(day)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "a", encoding="utf-8") as capture_file:
                for item in rows:
                    capture_file.write(json.dumps(item) + "\n")

        yield write


class TestWindowLine:
    def test_chat_renders_like_the_live_path(self):
        assert window.window_line(row("chat", author="ava", content="hi")) == "ava: hi"

    def test_chat_uses_the_alias_map(self):
        assert (
            window.window_line(
                row("chat", author="hatsunemikuisbestwaifu", content="o7")
            )
            == "Miku: o7"
        )

    @pytest.mark.parametrize("content", ["!hello", "fb;ping", "fae;freq 0.1"])
    def test_commands_never_reach_the_window(self, content):
        assert window.window_line(row("chat", author="mod", content=content)) is None

    def test_her_echo_is_skipped_her_send_point_row_is_kept(self):
        assert window.window_line(row("chat", echo=True, content="hi")) is None
        assert window.window_line(row("faebot_message", text="hi")) == "faebot: hi"

    def test_voice_heard_and_unheard(self):
        assert (
            window.window_line(row("voice", text="hello chat"))
            == "[streamer voice] testchannel: hello chat"
        )
        assert (
            window.window_line(
                row("voice", text="faebot", heard=False, why="prompt-echo")
            )
            is None
        )

    def test_a_pass_is_the_machinery_mark_not_her_voice(self):
        line = window.window_line(row("faebot_pass", reason="nothing to add"))
        assert line == core.machinery_line(core.PASS_MARK)
        assert "faebot:" not in line

    def test_machinery_lines_come_back_verbatim(self):
        text = core.machinery_line("the stream ended at 01:00 UTC")
        assert window.window_line(row("stream_state", line=text)) == text

    @pytest.mark.parametrize(
        "kind", ["raw", "usernotice", "hear_repeat", "faebot_error"]
    )
    def test_the_rest_of_the_record_was_never_in_her_window(self, kind):
        assert window.window_line(row(kind, line="x", text="x")) is None


class TestReadBack:
    def test_depth_is_the_live_windows_not_more(self, record):
        record(*[row("chat", 60 - i, author="a", content=str(i)) for i in range(60)])
        lines, last = window.read_back("testchannel", 50, NOW)
        assert len(lines) == 50
        assert lines[-1] == "a: 59"  # the newest line is last
        assert lines[0] == "a: 10"

    def test_reads_yesterday_too(self, record):
        yesterday = NOW - datetime.timedelta(days=1)
        record(
            {
                "kind": "chat",
                "captured_at": yesterday.isoformat(),
                "channel": "testchannel",
                "author": "late",
                "content": "night",
            },
            day=yesterday,
        )
        record(row("chat", 1, author="early", content="morning"))
        lines, _ = window.read_back("testchannel", 50, NOW)
        assert lines == ["late: night", "early: morning"]

    def test_other_channels_and_torn_lines_are_skipped(self, record, tmp_path):
        record(
            {
                "kind": "chat",
                "captured_at": NOW.isoformat(),
                "channel": "elsewhere",
                "author": "x",
                "content": "y",
            }
        )
        with open(capture.path_for(NOW), "a") as capture_file:
            capture_file.write(
                '{"kind": "chat", "captured_at": "2026-10-01T02:0\n'
            )  # a crash mid-write
        record(row("chat", 1, author="a", content="whole"))
        lines, last = window.read_back("testchannel", 50, NOW)
        assert lines == ["a: whole"]
        assert last["content"] == "whole"

    def test_nothing_to_read(self, record):
        assert window.read_back("testchannel", 50, NOW) == ([], None)


class TestSeam:
    def test_nothing_in_the_record(self):
        line, chosen = window.seam(0, None, NOW)
        assert line.startswith(core.MACHINERY)
        assert "nothing in the record" in line
        assert chosen is None

    def test_a_chosen_restart_is_named_as_one(self):
        last = row("restart", 3, line="…", how="said")
        line, chosen = window.seam(12, last, NOW)
        assert chosen is True
        assert "a chosen restart" in line
        assert "12 lines" in line
        assert "02:07 UTC, 3 minutes ago" in line

    def test_a_stop_that_was_not_chosen_gets_its_clock(self):
        last = row("chat", 150, author="a", content="…")
        line, chosen = window.seam(50, last, NOW)
        assert chosen is False
        assert "wasn't chosen" in line
        assert "2 hours ago" in line
        assert "what happened between isn't in it" in line


class TestRestore:
    def test_window_is_lines_then_seam_and_the_wake_is_recorded(self, record):
        record(
            row("chat", 2, author="a", content="hi"), row("faebot_pass", 1, reason="")
        )
        with patch("window.capture.record_wake") as record_wake:
            lines = window.restore("testchannel", 50, NOW)
        assert lines[:2] == ["a: hi", core.machinery_line(core.PASS_MARK)]
        assert lines[2].startswith(core.MACHINERY) and "wasn't chosen" in lines[2]
        record_wake.assert_called_once()
        args, kwargs = record_wake.call_args
        assert args[0] == "testchannel" and args[1] == lines[2]
        assert kwargs["lines_read_back"] == 2
        assert kwargs["stop_was_chosen"] is False

    def test_a_broken_read_still_wakes(self, record):
        with patch("window.read_back", side_effect=OSError("disk")):
            lines = window.restore("testchannel", 50, NOW)
        assert len(lines) == 1 and "nothing in the record" in lines[0]
