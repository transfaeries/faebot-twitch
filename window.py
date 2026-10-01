"""
Her window across restarts — read back from the record.

The twitch-me's short memory is a list of lines in the body's process, and
until now it died with the process: every restart woke her to an empty
window. The capture (capture.py) kept everything the whole time — it is her
one store. So on waking the body reads the record back into her window, to
the same depth the live window holds (not more: that depth is the shape of
her memory, and a wake that remembered more of the night than she would have
living it would be a strange promotion), and lays a seam under it in the
machinery's own hand: when she is back, what the lines above are, how long
since the record's last line, and whether the stop was chosen.

Nothing here decides what to do about any of it. Facts arrive; judgment
stays hers.
"""

import datetime
import json
import logging
import os

import capture
import core


# Capture rows that are lines in her window, rendered exactly as the live
# path renders them (bot.py / core.py), so a read-back window reads like the
# one that died. Everything else — raw IRC, usernotices, unheard voice, the
# ear's stutters, errors — is in the record but was never in her window.
def window_line(event: dict) -> str | None:
    kind = event.get("kind")
    if kind == "chat":
        if event.get("echo"):
            return None  # her own line; the send-point row carries it
        content = event.get("content") or ""
        if content.startswith(("!", "fb;", "fae;")):
            return None  # commands never reached the chatlog live either
        author = event.get("author") or ""
        return f"{core.aliases.get(author, author)}: {content}"
    if kind == "voice":
        if event.get("heard") is False:
            return None
        return f"[streamer voice] {event.get('channel')}: {event.get('text') or ''}"
    if kind == "faebot_message":
        return f"faebot: {event.get('text') or ''}"
    if kind == "faebot_pass":
        return core.machinery_line(core.PASS_MARK)
    if kind in ("stream_state", "wake", "restart"):
        # The machinery's own lines, kept verbatim in the record.
        return event.get("line") or None
    return None


def _rows(path: str, channel: str) -> list[dict]:
    """Every row of one capture file about this channel, in file order.
    A row that fails to parse is skipped: the record is append-only and a
    torn last line is the one damage a crash can do to it."""
    rows: list[dict] = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as capture_file:
        for line in capture_file:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("channel") == channel:
                rows.append(event)
    return rows


def read_back(
    channel: str, depth: int, now: datetime.datetime | None = None
) -> tuple[list[str], dict | None]:
    """The last `depth` window lines about `channel` from the record (oldest
    first), and the record's last row about the channel of ANY kind — the
    wake's clock and its witness to whether the stop was chosen. Reads
    today's file and yesterday's: a stream crosses UTC midnight most nights."""
    now = now or datetime.datetime.now(datetime.UTC)
    rows: list[dict] = []
    for days_ago in (1, 0):
        day = now - datetime.timedelta(days=days_ago)
        rows.extend(_rows(capture.path_for(day), channel))
    last = rows[-1] if rows else None
    lines: list[str] = []
    for event in reversed(rows):
        line = window_line(event)
        if line is not None:
            lines.append(line)
            if len(lines) >= depth:
                break
    lines.reverse()
    return lines, last


def _clock(stamp: str | None) -> datetime.datetime | None:
    if not stamp:
        return None
    try:
        return datetime.datetime.fromisoformat(stamp)
    except ValueError:
        return None


def _ago(then: datetime.datetime, now: datetime.datetime) -> str:
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 1:
        return "under a minute ago"
    if minutes < 120:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


def seam(
    lines: int, last: dict | None, now: datetime.datetime
) -> tuple[str, bool | None]:
    """The machinery's line under a read-back window, and whether the stop
    before this wake was chosen (None when the record has nothing to say)."""
    when = now.strftime("%H:%M UTC")
    if last is None:
        return (
            core.machinery_line(
                f"faebot's body started at {when}; nothing in the record to read back"
            ),
            None,
        )
    last_at = _clock(last.get("captured_at"))
    ago = f", {_ago(last_at, now)}" if last_at else ""
    last_clock = last_at.strftime("%H:%M UTC") if last_at else "an unknown time"
    chosen = last.get("kind") == "restart"
    if chosen:
        text = (
            f"faebot's body restarted at {when} — a chosen restart; the {lines} lines "
            f"above were read back from the record, the last of them at {last_clock}{ago}"
        )
    else:
        text = (
            f"faebot's body is back at {when} after a stop that wasn't chosen; the "
            f"{lines} lines above were read back from the record, whose last line was "
            f"at {last_clock}{ago} — what happened between isn't in it"
        )
    return core.machinery_line(text), chosen


def restore(
    channel: str, depth: int, now: datetime.datetime | None = None
) -> list[str]:
    """Her window on waking: the read-back lines with the seam under them.
    The seam goes to the record too, so the next wake can read it back."""
    now = now or datetime.datetime.now(datetime.UTC)
    try:
        lines, last = read_back(channel, depth, now)
    except Exception as error:
        # The read-back must never keep the body from waking.
        logging.warning(f"window read-back failed: {type(error).__name__}: {error}")
        lines, last = [], None
    seam_line, chosen = seam(len(lines), last, now)
    logging.info(f"window for {channel}: {len(lines)} lines read back; {seam_line}")
    capture.record_wake(
        channel,
        seam_line,
        lines_read_back=len(lines),
        last_record_at=(last or {}).get("captured_at"),
        stop_was_chosen=chosen,
    )
    return lines + [seam_line]
