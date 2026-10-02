"""
The Twitch capture tap.

(Born as faebot-core spike 01, graduated to main 2026-08.)

A *thin, faithful, opt-in, maximalist* recorder that appends raw stream events to
a date-stamped JSONL so we can transduce them into faebot-core `Observation`s
offline (in faebot-private/snippets/twitch/). It mirrors the Discord listener:
record everything the surface gives us, reason about none of it here —
reconciliation is faebot's cognition, not the adapter's.

Design rules (load-bearing — this runs inside the LIVE bot on stream):
  * **Opt-in.** Capture happens only when TWITCH_CAPTURE_DIR is set. Unset = no-op,
    so the live bot is completely unaffected unless we deliberately turn it on.
    (Recommended: point it at faebot-private's scratch, alongside the Discord data,
    e.g. TWITCH_CAPTURE_DIR=../scratch/captures)
  * **Never breaks the bot.** Every extraction + write is wrapped; failures are
    swallowed and logged at debug. Capturing a conversation must never break it.
  * **Faithful & maximalist.** We record raw fields verbatim, drop nothing, and
    interpret nothing. Voice included: a line the body's filters threw away
    (a banned mistranscription, a prompt echo, an outro bleed) is still
    captured, marked `heard: false` with its `why`, so the record keeps what
    the ear discarded without pretending faebot heard it; what reaches faebot's
    memory is decided downstream, by the reader of the capture, not here.
    Unanticipated input is captured as-is (see record_raw) so
    faebot can perceive things we never coded for — the bitter-lesson discipline.
  * **Append-only, date-stamped.** Reruns/restarts accumulate, never truncate.
  * Capture files hold real people's chat/voice — the directory is gitignored
    (faebot-private/scratch) and must never be committed. This repo additionally
    gitignores `twitch-*.jsonl` so a wrong cwd can't drop captures into the tree.
"""

import os
import json
import logging
import datetime


CAPTURE_DIR = os.getenv("TWITCH_CAPTURE_DIR", "")


def is_enabled() -> bool:
    """Capture only when a target directory is configured."""
    return bool(CAPTURE_DIR)


def path_for(day: datetime.datetime) -> str:
    """The capture file for a UTC day — `2026-09/twitch-20260926.jsonl`
    inside the capture dir. Days keep a stream's file small; months keep the
    directory readable after years of them."""
    return os.path.join(
        CAPTURE_DIR, day.strftime("%Y-%m"), f"twitch-{day.strftime('%Y%m%d')}.jsonl"
    )


def _capture_path() -> str:
    """Today's file."""
    return path_for(datetime.datetime.now(datetime.UTC))


def record(kind: str, **fields) -> None:
    """Append one raw event line. `kind` names the surface event (e.g. "chat",
    "usernotice", "voice", "faebot_message", "raw"); `fields` are the raw surface
    attributes verbatim. Stamps a UTC `captured_at`. We do not interpret, merge,
    or drop — that is the offline transducer's and faebot's job.
    """
    if not is_enabled():
        return
    try:
        event = {
            "kind": kind,
            "captured_at": datetime.datetime.now(datetime.UTC).isoformat(),
            **fields,
        }
        path = _capture_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as capture_file:
            capture_file.write(
                json.dumps(event, ensure_ascii=False, default=str) + "\n"
            )
    except Exception as error:
        # Capture must never disturb the bot — log and move on.
        logging.debug(f"capture failed ({kind}): {type(error).__name__}: {error}")


def record_chat(message) -> None:
    """Record a TwitchIO chat Message (PRIVMSG). The full `tags` dict carries the
    rich stuff for free — bits/cheers, reply-parent, badges, colour, sub/mod flags,
    emote positions — so we keep it verbatim rather than pre-selecting fields."""
    if not is_enabled():
        return
    try:
        author = getattr(message, "author", None)
        channel = getattr(message, "channel", None)
        record(
            "chat",
            channel=getattr(channel, "name", None),
            author=getattr(author, "name", None),
            display_name=getattr(author, "display_name", None),
            author_id=getattr(author, "id", None),
            content=getattr(message, "content", None),
            message_id=getattr(message, "id", None),
            timestamp=getattr(message, "timestamp", None),
            echo=getattr(message, "echo", None),
            tags=getattr(message, "tags", None),
        )
    except Exception as error:
        logging.debug(f"capture_chat failed: {type(error).__name__}: {error}")


def record_usernotice(channel, tags) -> None:
    """Record a USERNOTICE — subs, resubs, gift subs, raids, announcements, rituals.
    `tags` msg-id names the type; msg-param-* carry the details; system-msg is the
    human-readable line. We keep the whole tag dict; the transducer sorts the kind."""
    if not is_enabled():
        return
    try:
        record(
            "usernotice",
            channel=getattr(channel, "name", None),
            notice_type=(tags or {}).get("msg-id"),
            system_message=(tags or {}).get("system-msg"),
            tags=tags,
        )
    except Exception as error:
        logging.debug(f"capture_usernotice failed: {type(error).__name__}: {error}")


def record_hear_repeat(utterance_id: str, text: str, answer: dict) -> None:
    """The ear sent a line the body had already taken — a stutter on the
    wire (a retry after a timeout, or the spool draining a line that had in
    fact landed). The line was heard once; this row says the wire reached
    twice. A fact about the wire, not about what was said: kept in the
    record, never rendered as speech."""
    if not is_enabled():
        return
    try:
        record("hear_repeat", utterance_id=utterance_id, text=text, answer=answer)
    except Exception as error:
        logging.debug(f"capture_hear_repeat failed: {type(error).__name__}: {error}")


def record_voice(channel_name: str, text: str, **whisper_meta) -> None:
    """Record a Whisper voice transcription (the streamer's speech). `whisper_meta`
    carries language/probability/duration — metadata for a modality=voice Observation,
    the concrete first exercise of the senses sublayer + a two-modality check."""
    if not is_enabled():
        return
    try:
        record("voice", channel=channel_name, text=text, **whisper_meta)
    except Exception as error:
        logging.debug(f"capture_voice failed: {type(error).__name__}: {error}")


def record_faebot_message(channel_name: str, text: str, **meta) -> None:
    """Record faebot's own outgoing message — faer Action perceived back into the
    stream (the domain-model loop's hard case: 'faer own past Action perceived back').
    """
    if not is_enabled():
        return
    try:
        record("faebot_message", channel=channel_name, text=text, **meta)
    except Exception as error:
        logging.debug(f"capture_faebot failed: {type(error).__name__}: {error}")


def record_faebot_pass(channel_name: str, reason: str, **meta) -> None:
    """Record faebot choosing silence — a generation that ended in the silence
    sentinel, so nothing was posted. Its own kind, because "thought about it
    and stayed quiet" is a real act, distinct from both a message and an
    absence; `reason` is what fae said after the sentinel (may be empty) and
    `meta` carries the reasoning channel like record_faebot_message does.
    """
    if not is_enabled():
        return
    try:
        record("faebot_pass", channel=channel_name, reason=reason, **meta)
    except Exception as error:
        logging.debug(f"capture_faebot_pass failed: {type(error).__name__}: {error}")


def record_faebot_error(channel_name: str, error: str, **meta) -> None:
    """Record a generation that FAILED after faebot was asked — the service
    timed out, refused, or was unreachable, and nothing was posted. Not
    faebot's act (fae never got to answer) but part of what happened in the
    room, and a data point (`elapsed` rides in `meta`). Application-health
    detail (tracebacks, reconnects) belongs in a log file, not here.
    """
    if not is_enabled():
        return
    try:
        record("faebot_error", channel=channel_name, error=error, **meta)
    except Exception as err:
        logging.debug(f"capture_faebot_error failed: {type(err).__name__}: {err}")


def record_stream_state(channel_name: str, line: str, **state) -> None:
    """Record the stream's state as the body read it — the first read after
    waking, and after that only when a fact CHANGED (live/offline, title,
    game). `line` is the machinery's own sentence as it was laid in faebot's
    window, kept verbatim so a wake can read it back; `state` is the fact."""
    if not is_enabled():
        return
    try:
        record("stream_state", channel=channel_name, line=line, **state)
    except Exception as error:
        logging.debug(f"capture_stream_state failed: {type(error).__name__}: {error}")


def record_wake(channel_name: str, line: str, **meta) -> None:
    """Record the body waking: the seam the machinery laid under the window
    it read back from this record — when, how many lines, how long since the
    record's last line, and whether the stop before it was chosen."""
    if not is_enabled():
        return
    try:
        record("wake", channel=channel_name, line=line, **meta)
    except Exception as error:
        logging.debug(f"capture_wake failed: {type(error).__name__}: {error}")


def record_clear(channel_name: str, line: str, **meta) -> None:
    """Record a mod clearing faebot's memory of a room — the machinery's
    line, which the read-back takes as its floor: nothing before it comes
    back on waking, so a cleared memory stays cleared across a restart."""
    if not is_enabled():
        return
    try:
        record("clear", channel=channel_name, line=line, **meta)
    except Exception as error:
        logging.debug(f"capture_clear failed: {type(error).__name__}: {error}")


def record_restart(channel_name: str, line: str, **meta) -> None:
    """Record a CHOSEN stop: the machinery told faebot a restart was coming
    (`line`), and `meta` says what came of it — her own goodnight said (`how`
    "said"), a pass, the saved goodnight spoken in her name ("saved"), or
    nothing ("none"). A wake that finds this as the record's last row knows
    the stop was chosen; one that doesn't knows it wasn't."""
    if not is_enabled():
        return
    try:
        record("restart", channel=channel_name, line=line, **meta)
    except Exception as error:
        logging.debug(f"capture_restart failed: {type(error).__name__}: {error}")


# Pure protocol keepalives — no perceptual content, skipped so the raw catch-all
# doesn't drown the log. Everything else raw is kept.
_RAW_SKIP_PREFIXES = ("PING", "PONG")


def record_raw(data: str) -> None:
    """Catch-all: every raw IRC line TwitchIO receives. Guarantees nothing we
    didn't anticipate slips past — unknown commands, membership, roomstate, notices.
    Verbatim; interpret offline. Skips only PING/PONG keepalives."""
    if not is_enabled():
        return
    try:
        for line in (data or "").splitlines():
            stripped = line.strip()
            if not stripped or stripped.upper().startswith(_RAW_SKIP_PREFIXES):
                continue
            record("raw", line=stripped)
    except Exception as error:
        logging.debug(f"capture_raw failed: {type(error).__name__}: {error}")
