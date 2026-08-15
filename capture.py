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
    interpret nothing — with one named exception: voice is captured downstream
    of the live-loop's transcription filters (filter_transcription and the
    prompt-echo check), because a mistranscription faebot never "heard" should
    not become a memory either. Whether a live-loop filter should double as a
    memory filter is an open question, filed to the recognition sitting.
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


def _capture_path() -> str:
    """Date-stamped file inside the capture dir (UTC, so multi-day is unambiguous)."""
    today = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
    return os.path.join(CAPTURE_DIR, f"twitch-{today}.jsonl")


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
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        with open(_capture_path(), "a", encoding="utf-8") as capture_file:
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
        logging.debug(
            f"capture_usernotice failed: {type(error).__name__}: {error}"
        )


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
