"""
The stream's state, stamped as a fact.

The body polls Twitch for whether the channel is live, its title and its
game. The first read after waking is laid in faebot's window once; after
that a line is laid only when a fact CHANGES — the stream going live, the
stream ending, a title or game that moved. Each line is in the machinery's
own hand (core.MACHINERY), goes to the capture so the diary can see it, and
to the dashboard. The state also feeds her system prompt (the title and game
it already carried, now with live/offline).

That is all it does. Offline is not a rule about her: the stamp tells her
the state and the choice of what to say, or not, stays hers.
"""

import asyncio
import datetime
import logging
import os
from dataclasses import asdict, dataclass

import capture
import core


POLL_SECONDS = float(os.getenv("STREAM_POLL_SECONDS", "60"))


@dataclass(frozen=True)
class StreamState:
    live: bool
    title: str
    game: str
    started_at: str | None = None


def describe(state: StreamState) -> str:
    where = "live" if state.live else "offline"
    return f'{where} — title "{state.title}", game {state.game}'


def change_lines(
    before: StreamState | None, after: StreamState, now: datetime.datetime
) -> list[str]:
    """What the machinery says about a read: the whole state on the first
    read, and after that one line per fact that changed — nothing when
    nothing did. A fact that moved without saying so is half a fact."""
    when = now.strftime("%H:%M UTC")
    if before is None:
        return [f"the stream is {describe(after)} (read at {when})"]
    lines: list[str] = []
    if before.live != after.live:
        if after.live:
            lines.append(
                f'the stream went live at {when} — title "{after.title}", game {after.game}'
            )
        else:
            since = (
                f" (it had been live since {_clock(before.started_at)})"
                if before.started_at
                else ""
            )
            lines.append(f"the stream ended at {when}{since}")
        return lines
    if before.title != after.title:
        lines.append(
            f'the stream title changed at {when}: "{before.title}" → "{after.title}"'
        )
    if before.game != after.game:
        lines.append(f"the game changed at {when}: {before.game} → {after.game}")
    return lines


def _clock(stamp: str | None) -> str:
    try:
        return datetime.datetime.fromisoformat(stamp or "").strftime("%H:%M UTC")
    except ValueError:
        return "an unknown time"


async def read_state(bot, channel: str) -> StreamState:
    """One read from Twitch: the live stream if there is one (title, game,
    when it started), else the channel's standing title and game."""
    streams = await bot.fetch_streams(user_logins=[channel])
    if streams:
        stream = streams[0]
        started = getattr(stream, "started_at", None)
        return StreamState(
            live=True,
            title=stream.title or "Unknown",
            game=stream.game_name or "Unknown",
            started_at=started.isoformat() if started else None,
        )
    info = await bot.fetch_channel(channel)
    return StreamState(
        live=False,
        title=(info.title if info else None) or "Unknown",
        game=(info.game_name if info else None) or "Unknown",
    )


class StreamWatch:
    """The body's knowledge of each channel's stream, kept current by a
    poll. `state[channel]` is None until the first read lands."""

    def __init__(self) -> None:
        self.state: dict[str, StreamState | None] = {}

    def note(
        self,
        channel: str,
        after: StreamState,
        events: asyncio.Queue | None,
        now: datetime.datetime | None = None,
    ) -> list[str]:
        """Take a read: lay what changed in her window, the record and the
        dashboard. Returns the machinery's lines (empty when nothing moved)."""
        now = now or datetime.datetime.now(datetime.UTC)
        before = self.state.get(channel)
        lines = [core.machinery_line(text) for text in change_lines(before, after, now)]
        self.state[channel] = after
        if not lines:
            return lines
        conversation = core.ensure_conversation(channel)
        for line in lines:
            conversation.chatlog.append(line)
            capture.record_stream_state(channel, line, **asdict(after))
            core.put_event(
                events,
                {
                    "type": "stream_state",
                    "channel": channel,
                    "text": line,
                    **asdict(after),
                },
            )
            logging.info(line)
        return lines

    async def watch(
        self,
        bot,
        channel: str,
        events: asyncio.Queue | None,
        interval: float = POLL_SECONDS,
    ) -> None:
        """Poll until cancelled. A failed read is logged and tried again next
        time — the watch never takes the body down, and never invents a state."""
        while True:
            try:
                self.note(channel, await read_state(bot, channel), events)
            except asyncio.CancelledError:
                return
            except Exception as error:
                logging.warning(
                    f"stream state read failed for {channel}: {type(error).__name__}: {error}"
                )
            await asyncio.sleep(interval)
