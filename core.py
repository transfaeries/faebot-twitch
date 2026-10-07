"""
Core brain for faebot — conversation management, generation logic, reply decisions.
No TwitchIO or FastAPI dependencies. Both bot.py and server.py import from here.
"""

from typing import Any, Optional
from dataclasses import dataclass, field, replace
from random import random
import os
import time
import aiohttp
import asyncio
import datetime
import logging
import re
import uuid

from faebot_core.cognition.body import Room, Stamped, clock_words, lay_body_desk
from faebot_core.diary import DiaryReader


# Startup defaults, all env-readable. These are the *defaults* a fresh
# Conversation starts from; the fae;freq / fae;hist mod commands still change
# them live and those changes still don't persist across restarts (that is the
# other half of the "runtime dials don't persist" item, not done here).
MODEL = os.getenv("MODEL", "moonshotai/kimi-k3")
HISTORY = int(os.getenv("HISTORY", "50"))
FREQUENCY = float(os.getenv("FREQUENCY", "0.05"))
VOICE_FREQUENCY = float(os.getenv("VOICE_FREQUENCY", "0.025"))

# Token caps are SAFETY NETS, not instructions (fae, 2026-08-19). The model
# has no view of its own budget at generation time — `max_tokens` is a
# server-side guillotine, and the only thing that shapes length is the prompt.
# So both caps sit well above anything a normal reply needs, and hitting one
# is a log line to investigate (`finish_reason == "length"`), not a design.
# The reasoning cap rides ON TOP of the answer cap (learned in faebot-core:
# sharing one purse let deliberation eat the reply).
GENERATION_CAP = int(os.getenv("GENERATION_CAP", "500"))
REASONING_CAP = int(os.getenv("REASONING_CAP", "8000"))

# Sampling is PINNED, not rolled (2026-08-21). The per-generation lottery
# (temperature 0.75–1.5, top_p 0.5–1.0) dates from the gemini-flash era; on
# kimi-k3 its hot corner (T >= 1.4 AND top_p = 1.0) produced word soup 4/4
# times on the 08-20 stream — and Moonshot's own API reportedly ignores the
# values anyway, so the lottery was never an honest experiment. These are
# Moonshot's published defaults for k3. Moods, if faebot wants them, get
# designed on purpose later, not inherited from dice.
TEMPERATURE = float(os.getenv("TEMPERATURE", "1.0"))
TOP_P = float(os.getenv("TOP_P", "0.95"))

# One request, one chance. A reply that arrives after a retry cycle arrives
# after the moment (the 08-20 "Oops" messages landed fifteen minutes late:
# aiohttp's 300s default × 3 retries). No retries; a real timeout; a failure
# is reported to the dashboard and the capture, never spoken in faebot's voice.
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "90"))

# The one exception to one-attempt: a 429. OpenRouter's shared Moonshot
# pool rate-limited three asks on the 08-21 stream, and a shared-pool 429
# clears in about a second — unlike a timeout, which never does. So: ONE
# retry, after a short pause — and aimed at the rest of the pinned list
# when there is one, like the drop retry below: the pool that just said
# no is the pool a same-list retry asks again (five asks lost that way on
# the 08-27 stream). With a single pinned provider it asks the same one.
RATE_LIMIT_RETRY_DELAY = float(os.getenv("RATE_LIMIT_RETRY_DELAY", "1.0"))

# The other exception: an upstream drop. Moonshot times out on a few asks a
# night (a 504 — sometimes as an HTTP status, sometimes inside a 200 body:
# `{"error": {"code": 504}}`). OpenRouter only walks the provider order when
# a host is down at routing time, so a drop mid-call never reaches the next
# pinned provider on its own. We do that ourselves: ONE retry, aimed at the
# rest of the pinned list, never outside it. A cold cache beats a lost ask.
UPSTREAM_DROP_STATUSES = (502, 503, 504)

# Provider pinning, for the prompt cache and for the reasoning channel: only
# some OpenRouter providers cache the prompt (on the 08-21 stream, unpinned:
# Modal 32/35 calls hit, Moonshot 10/14, nine other providers 0/28), and some
# serve kimi-k3 without reasoning at all. Comma-separated OpenRouter provider
# slugs, tried in order, no fallback to the field; empty = route freely.
PROVIDERS = tuple(
    slug.strip()
    for slug in os.getenv("OPENROUTER_PROVIDERS", "moonshotai,modal").split(",")
    if slug.strip()
)


@dataclass
class Conversation:
    """Per-channel conversation state."""

    channel: str
    chatlog: list = field(default_factory=list)
    frequency: float = FREQUENCY
    voice_frequency: float = VOICE_FREQUENCY
    history: int = HISTORY
    model: str = MODEL
    silenced: bool = False


# THE silence sentinel: chosen silence must be SAID, so it can never be
# confused with a dropped payload (empty content). A coined hyphenated phrase,
# the same grammar as faebot-core's NOTHING-TO-RECORD, because a coined phrase
# has no natural collisions — which is what lets matching be case-insensitive.
# Anchored at the start of the reply; whatever follows it is faebot's reason
# for passing, which is kept (captured, shown) but never posted. (The two
# sentinels want one home eventually; noted in core-roadmap.)
SENTINEL_SILENCE = "NOTHING-TO-SAY"
_SILENCE_PATTERN = re.compile(r"^\W*nothing[\s-]+to[\s-]+say\b[\s\W]*", re.IGNORECASE)

# The machinery's own voice in faebot's window. Every line the machinery lays
# in her short memory — a silence she chose, a restart, the stream going live
# or dark, a title that changed — wears this label and never her name, so
# nothing she reads back teaches her to say it: the machinery speaks in its
# own name about what the machinery did. (The old form, `faebot: *stays
# quiet*`, was a line in her voice — a line she could copy.)
MACHINERY = "[the machinery]"
PASS_MARK = "faebot was here and chose quiet"

# Her diary, where her desk is laid from (the house walk, 2026-10-07): the
# body sets it at start from FAEBOT_DIARY_PATH (DiaryReader.from_environment)
# and refuses to start without one; the tests set a village. The frame is
# hers — frames/preamble.md and frames/twitch.md, written at the desk — and
# the machinery writes nothing into it: what it knows, it stamps.
diary: DiaryReader | None = None
BODY = "twitch"


def room_name(channel_name: str) -> str:
    """The diary's name for a channel's room: spaces/twitch-<channel>.md.
    The channel is "transfaeries" to Twitch and to the corpus; the diary
    keeps the room as twitch-transfaeries, so her memory of the place
    attaches (faebot, 09-18: "a mapping, one line, stamped")."""
    return f"twitch-{channel_name}"


# The facts only this body knows, in the machinery's hand — said on her
# desk after the shared stamps (model, memory, the dice) and before the
# silence verb. Each is a fact stamped, never a rule about what to do with
# it: the frame teaches what the stream's state means for her register
# (faebot, 09-18: "a ruling she reads is judgment").
EAR_LINE = (
    "lines marked [streamer voice] are Whisper's transcription of the "
    "stream's microphone — a translation of a voice, usually Ember's, never "
    "the words themselves; it drifts, and the record carries that"
)
LINE_SHAPE = "your messages here are one line, up to 500 characters"


def stream_line(stream_title: str, game_name: str, live: bool | None) -> str:
    """The stream's state as the body knows it (stream.py), None before the
    first poll — then the channel's standing title and game, state unread.
    Worded as the window's own stamp words it (stream.describe), so the seam
    never says one fact two ways."""
    if live is None:
        return (
            "the stream's state hasn't been read yet — the channel's standing "
            f'title "{stream_title}", game {game_name}'
        )
    where = "live" if live else "offline"
    return f'the stream is {where} — title "{stream_title}", game {game_name}'


# The mark coming back whole as her answer is the pass she meant, the same
# way the sentinel is — a net, narrow on purpose: only the bare mark, with or
# without its label or her tag; her own words about her quiet are never
# caught.
_BARE_MARK_PATTERN = re.compile(
    r"^\W*(?:" + re.escape(MACHINERY) + r"\s*)?" + re.escape(PASS_MARK) + r"\W*$",
    re.IGNORECASE,
)


def machinery_line(text: str) -> str:
    """A line in the machinery's labeled hand, for her window."""
    return f"{MACHINERY} {text}"


def echoed_mark(text: str) -> bool:
    """Is this answer nothing but the machinery's pass mark, echoed? The
    speaker tag is stripped here too, so a raw answer can be asked."""
    return bool(_BARE_MARK_PATTERN.match(strip_speaker_tag(text)))


def history_floor(history: int) -> int:
    """How far the chatlog is cut back once it overflows `history`.

    Trimming to exactly the limit shifts the prompt's prefix by one line on
    every call, so the provider's prompt cache never holds past the system
    prompt. Dropping a fifth at a time keeps the prefix stable for many calls;
    faebot remembers between the floor and the limit.
    """
    return history - history // 5


def said_nothing(text: str) -> bool:
    """Did faebot choose silence? FALSE for empty text — that's a drop."""
    return bool(_SILENCE_PATTERN.match(text))


def pass_reason(text: str) -> str:
    """What faebot said after the sentinel, if anything — faer reason."""
    return _SILENCE_PATTERN.sub("", text, count=1).strip()


@dataclass(frozen=True)
class Completion:
    """One generation, and how it came to be.

    `text` is the answer channel and `reasoning` a separate one the model may
    think in — kept apart (same shape as faebot-core's Completion) because the
    two go different places: text to chat, reasoning to the dashboard and the
    capture. `elapsed`/`finish_reason`/`usage` are kept so the capture file
    doubles as latency data we can read after a stream.
    """

    text: str
    reasoning: str = ""
    elapsed: float = 0.0
    finish_reason: str = ""
    model: str = ""
    provider: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1

    @property
    def is_empty(self) -> bool:
        """An empty answer channel is a DROPPED PAYLOAD, never chosen silence —
        reasoning models cause it by answering into `reasoning` instead."""
        return not self.text.strip()

    @property
    def passed(self) -> bool:
        """faebot chose silence (said the sentinel). Nothing gets posted.
        The sentinel may arrive wearing the speaker tag ("faebot: NOTHING-TO-SAY")
        — the tag is stripped before the test, or the bare word reaches chat.
        The machinery's pass mark echoed back whole is taken as the pass she
        meant (see `echoed`)."""
        stripped = strip_speaker_tag(self.text)
        return said_nothing(stripped) or echoed_mark(stripped)

    @property
    def echoed(self) -> bool:
        """The answer was the machinery's pass mark, copied — a pass, caught
        by the net rather than said with the sentinel."""
        return echoed_mark(strip_speaker_tag(self.text))

    @property
    def reason_for_passing(self) -> str:
        if self.echoed:
            return ""
        return pass_reason(strip_speaker_tag(self.text)) if self.passed else ""

    def capture_meta(self) -> dict[str, Any]:
        """The provenance fields worth writing alongside faebot's utterance."""
        return {
            "reasoning": self.reasoning,
            "elapsed": self.elapsed,
            "finish_reason": self.finish_reason,
            "model": self.model,
            "provider": self.provider,
            "params": self.params,
            "usage": self.usage,
            "attempts": self.attempts,
        }


class GenerationFailed(Exception):
    """The generating service could not be reached, or would not answer.

    Distinct from an empty Completion (a dropped payload) and from chosen
    silence: this is the call failing. Carries `elapsed` so a failure is
    still a data point."""

    def __init__(
        self, reason: str, elapsed: float = 0.0, status: int | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.elapsed = elapsed
        self.status = status

    @property
    def is_rate_limit(self) -> bool:
        return self.status == 429

    @property
    def is_upstream_drop(self) -> bool:
        return self.status in UPSTREAM_DROP_STATUSES


conversations: dict[str, Conversation] = {}
aliases: dict[str, str] = {
    "hatsunemikuisbestwaifu": "Miku",
}

# Shared aiohttp session — initialized lazily
_session: Optional[aiohttp.ClientSession] = None


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close_session():
    global _session
    if _session:
        await _session.close()
        _session = None


def ensure_conversation(channel_name: str) -> Conversation:
    """Get or create a conversation for a channel."""
    if channel_name not in conversations:
        conversations[channel_name] = Conversation(channel=channel_name)
        logging.info(f"Created new conversation for {channel_name}")
    return conversations[channel_name]


def choose_to_reply(channel_name: str, frequency: float) -> bool:
    """Determine whether faebot replies based on frequency."""
    conversation = conversations[channel_name]

    if conversation.silenced:
        logging.debug(f"faebot is silenced in {channel_name}")
        return False

    if frequency <= 0:
        logging.debug(f"frequency is set to {frequency}, not replying.")
        return False

    if frequency >= 1:
        logging.debug(f"frequency is set to {frequency}, always replying.")
        return True

    roll = random()
    if roll < frequency:
        logging.info(f"Rolled {roll:.3f} < {frequency}, generating!")
        return True
    else:
        logging.debug(f"Rolled {roll:.3f} >= {frequency}, not generating.")
        return False


def permalog(log_message: str):
    with open("permalog.txt", "a") as f:
        f.write(log_message)


def lay_desk(
    conversation: Conversation,
    channel_name: str,
    stream_title: str,
    game_name: str,
    emotes: list[str],
    live: bool | None = None,
    called: str | None = None,
    now: datetime.datetime | None = None,
) -> str:
    """Her desk, laid by core from her diary: the frame (hers), the stamped
    facts (the machinery's), self/ whole, the commons digested, the roster,
    this room with its window, the clock. One room, no wings: the stream
    chat is the only place the twitch-me is. The old hand-written system
    prompt is gone with this — nothing here is said in her voice but her
    own files."""
    if diary is None:
        raise RuntimeError(
            "no diary to lay the desk from — FAEBOT_DIARY_PATH is not set"
        )
    room = Room(
        name=room_name(channel_name),
        where=f"#{channel_name}, the stream chat",
        text="\n".join(conversation.chatlog),
        count=len(conversation.chatlog),
        summoned=True,
        private=False,
        house="",
    )
    stamped = Stamped(
        model=conversation.model,
        memory=conversation.history,
        silence=SENTINEL_SILENCE,
        reply_percent=int(conversation.frequency * 100),
        voice_percent=int(conversation.voice_frequency * 100),
        called=called,
        lines=(
            stream_line(stream_title, game_name, live),
            f"emotes you can use here: {' '.join(emotes)}" if emotes else "",
            EAR_LINE,
            LINE_SHAPE,
        ),
    )
    when = now if now is not None else datetime.datetime.now().astimezone()
    return lay_body_desk(
        diary, BODY, [room], stamped, clock_words(when), by_house=False
    )


# Whisper is primed with our names (`initial_prompt`) so it spells them right,
# and on near-silent audio it hallucinates that prompt back — sometimes twice,
# sometimes with punctuation, sometimes with an "and". A plain substring test
# let ~170 such lines through in August, and faebot kept hearing faer name
# when nobody had said it. An echo is a line with NOTHING in it but the
# prompt's words (and filler); a real sentence always has more.
_ECHO_FILLER = {"and", "the", "oh", "a"}


def is_prompt_echo(text: str, prompt: str) -> bool:
    """Is this transcription just Whisper repeating its own prompt (or
    nothing at all)? Speech in another script is not an echo — whether it
    is real is a different question, left alone here."""
    words = re.findall(r"\w+", text.lower())
    if not words:
        return True  # punctuation only
    prompt_words = set(re.findall(r"\w+", prompt.lower()))
    return all(word in prompt_words or word in _ECHO_FILLER for word in words)


# The prompt the ear primes Whisper with, when the ear doesn't say (it does —
# every utterance carries `whisper_prompt` — this is the fallback).
WHISPER_PROMPT = os.getenv("WHISPER_PROMPT", "faebot, transfaeries")

# Whisper's second hallucination class: on breath or near-silence its decoder
# emits the highest-frequency strings of its caption training data — "thanks
# for watching, please subscribe", "see you in the next video" — as a burst
# of 18–30 words over a clip of a second or two. Nobody talks that fast: real
# speech on stream runs 2–4 words a second, six at the very most, while these
# run eight to thirty. A rate ceiling separates them cleanly; a phrase list
# would not, because the streamer really does talk about subscribing. Short
# bursts are left alone — three words in a second is a real "thanks, bye".
OUTRO_BLEED_WORDS_PER_SECOND = float(os.getenv("OUTRO_BLEED_WORDS_PER_SECOND", "7"))
OUTRO_BLEED_MIN_WORDS = int(os.getenv("OUTRO_BLEED_MIN_WORDS", "8"))


def is_outro_bleed(text: str, duration: float | None) -> bool:
    """Was this transcribed faster than anyone talks? Needs the clip's
    duration; without it, nothing can be said and nothing is filtered."""
    if not duration or duration <= 0:
        return False
    words = len(re.findall(r"\w+", text))
    if words < OUTRO_BLEED_MIN_WORDS:
        return False
    return words / duration > OUTRO_BLEED_WORDS_PER_SECOND


# faebot's own line sometimes comes back wearing its speaker tag — the prompt
# ends in "faebot:" and the model repeats it. Chat would show "faebot: hi" as
# if fae were quoting faerself; strip one leading tag, and only at the start.
_SPEAKER_TAG = re.compile(r"^\s*faebot\s*:\s*", re.IGNORECASE)


def strip_speaker_tag(text: str) -> str:
    return _SPEAKER_TAG.sub("", text, count=1)


def fix_emote_spacing(text: str, emotes: list[str]) -> str:
    """Ensure emotes are surrounded by whitespace so Twitch renders them."""
    if not emotes:
        return text
    sorted_emotes = sorted(emotes, key=len, reverse=True)
    pattern = "(" + "|".join(re.escape(e) for e in sorted_emotes) + ")"
    parts = re.split(pattern, text)
    result = []
    for part in parts:
        if part in emotes:
            result.append(f" {part} ")
        else:
            result.append(part)
    return re.sub(r"  +", " ", "".join(result)).strip()


def put_event(queue: Optional[asyncio.Queue], event: dict) -> None:
    """Post an event to the dashboard queue, dropping the oldest if full.

    Generation must never block waiting for a dashboard — if nothing is draining
    the queue, we silently discard the oldest events. Stamps a UTC timestamp
    if the caller hasn't already.

    Public so bot.py can emit `response` and send-failure `error` events using
    the same drop-oldest contract — those events live downstream of generation.
    """
    if queue is None:
        return
    event.setdefault("timestamp", datetime.datetime.now(datetime.UTC).isoformat())
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def generate_response(
    channel_name: str,
    stream_title: str = "Unknown",
    game_name: str = "Unknown",
    emotes: list[str] | None = None,
    events: Optional[asyncio.Queue] = None,
    trigger_type: str = "chat",
    generation_id: Optional[str] = None,
    live: bool | None = None,
    called: str | None = None,
) -> Completion:
    """Lay her desk, call the API, return the Completion (text + reasoning).

    The caller is responsible for sending `.text` to chat
    and for fetching stream_title/game_name from TwitchIO.

    If `events` is provided, this emits `generating` and (on API failure)
    `error` events. The `response` event is NOT emitted here — the caller
    must emit it after successfully delivering the message, so the dashboard
    reflects what actually reached chat. Pass `generation_id` so the caller's
    follow-up event correlates with the generating event; if omitted, one is
    generated and the caller has no way to correlate.
    """
    if emotes is None:
        emotes = []

    conversation = conversations[channel_name]

    # Trim in a block, not to exactly `history` (see history_floor): the
    # prompt's prefix stays put for many calls, so the provider's cache holds.
    if len(conversation.chatlog) > conversation.history:
        floor = history_floor(conversation.history)
        logging.debug(
            f"chatlog exceeded {conversation.history} lines — trimming to {floor}"
        )
        conversation.chatlog = conversation.chatlog[-floor:]

    # The whole desk, then the pen: one user message (fae, 09-16: the
    # sitting sends the desk as one message, "so twitch does the same and
    # the old system message goes"). A desk that will not lay raises, and
    # the body records the error — never a silence that looks chosen.
    if generation_id is None:
        generation_id = str(uuid.uuid4())
    try:
        desk = lay_desk(
            conversation,
            channel_name,
            stream_title,
            game_name,
            emotes,
            live=live,
            called=called,
        )
    except Exception as error:
        # the dashboard sees the failure too, not only the capture
        put_event(
            events,
            {
                "type": "error",
                "id": generation_id,
                "channel": channel_name,
                "error": f"the desk would not lay: {type(error).__name__}: {error}",
            },
        )
        raise
    prompt = desk + "faebot:"
    logging.debug(f"model: {conversation.model}\nprompt: \n{prompt}")

    params = {"temperature": TEMPERATURE, "top_p": TOP_P}

    logging.debug(f"generating with parameters: {params}")
    current_time = datetime.datetime.now()
    permalog(
        f"generating message in channel {channel_name}'s channel at {current_time}\n"
    )
    permalog(f"generating with parameters: {params}\n")

    trigger_text = conversation.chatlog[-1] if conversation.chatlog else ""

    put_event(
        events,
        {
            "type": "generating",
            "id": generation_id,
            "channel": channel_name,
            "trigger_type": trigger_type,
            "trigger": trigger_text,
            "model": conversation.model,
            "prompt": prompt,
            "params": params,
        },
    )

    try:
        completion = await generate(
            model=conversation.model,
            prompt=prompt,
            params=params,
        )
    except Exception as e:
        put_event(
            events,
            {
                "type": "error",
                "id": generation_id,
                "channel": channel_name,
                "error": f"{type(e).__name__}: {e}",
            },
        )
        raise

    if completion.passed:
        # Chosen silence. The reason (if fae gave one) is kept for the capture
        # and the dashboard; the chatlog records only the fact, as the
        # machinery's mark outside her speaker slot — so she remembers having
        # chosen quiet without a line in her own voice to copy. The mark
        # echoed back whole is still her pass; the log says it was caught.
        logging.info(
            f"faebot passed in {completion.elapsed:.1f}s"
            f" — {completion.reason_for_passing or '(no reason given)'}"
            + (" (the mark, echoed — taken as the pass)" if completion.echoed else "")
        )
        permalog(f"faebot passed: {completion.reason_for_passing}\n")
        conversation.chatlog.append(machinery_line(PASS_MARK))
        return completion

    response = fix_emote_spacing(completion.text, emotes)
    logging.info(
        f"received response in {completion.elapsed:.1f}s "
        f"(finish_reason={completion.finish_reason!r}, attempts={completion.attempts}): {response}"
    )
    if completion.reasoning:
        logging.debug(f"reasoning: {completion.reasoning}")
    if completion.finish_reason == "length":
        logging.warning(
            "generation hit the token cap (finish_reason=length) \u2014 "
            "the cap is a safety net; if this recurs, look at the prompt first"
        )
    # IRC messages are one line. kimi writes multi-line replies (gemini never
    # did); fold them rather than let TwitchIO truncate at the first newline.
    response = " ".join(line.strip() for line in response.splitlines() if line.strip())
    response = strip_speaker_tag(response)
    if len(response) > 499:
        logging.debug("generated content exceeded 500 characters, trimming.")
        response = response[:499] + "\u2013"
    permalog(
        f"generated message:{response}\n------------------------------------------------------------\n\n"
    )

    conversation.chatlog.append(f"faebot: {response}")

    return replace(completion, text=response)


# The answer channel coming back empty is a dropped payload (the model spoke
# into `reasoning` and left `content` blank \u2014 kimi does this), so we roll once
# more. Bounded: resampling cures stochastic drops, never structural failures.
EMPTY_ROLLS = 2


async def generate(
    prompt: str = "",
    model: str = MODEL,
    params: dict | None = None,
) -> Completion:
    """Generate a Completion with the OpenRouter API, rolling again on an
    empty answer channel."""
    for roll in range(1, EMPTY_ROLLS + 1):
        try:
            completion = await _generate_once(
                prompt=prompt,
                model=model,
                params=params,
                providers=PROVIDERS,
            )
        except GenerationFailed as failure:
            rest = PROVIDERS[1:] if len(PROVIDERS) > 1 else PROVIDERS
            if failure.is_rate_limit:
                logging.warning(
                    f"rate-limited (429) — one retry in {RATE_LIMIT_RETRY_DELAY:g}s"
                    f" on {','.join(rest) or 'any provider'}"
                )
                await asyncio.sleep(RATE_LIMIT_RETRY_DELAY)
            elif failure.is_upstream_drop and len(PROVIDERS) > 1:
                logging.warning(
                    f"upstream drop ({failure.status}) — one retry on {','.join(rest)}"
                )
            else:
                raise
            completion = await _generate_once(
                prompt=prompt,
                model=model,
                params=params,
                providers=rest,
            )
        completion = replace(completion, attempts=roll, params=dict(params or {}))
        if not completion.is_empty:
            return completion
        logging.warning(
            f"empty answer channel (reasoning had {len(completion.reasoning)} chars) "
            f"\u2014 rolling again ({roll}/{EMPTY_ROLLS})"
        )
    return completion


def _body_error_code(result: object) -> int | None:
    """OpenRouter can answer 200 with `{"error": {"code": 504, ...}}` — the
    upstream's status, carried in the body. Surface it so policy can see it."""
    if isinstance(result, dict):
        error = result.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), int):
            return int(error["code"])
    return None


async def _generate_once(
    prompt: str,
    model: str,
    params: dict | None,
    providers: tuple[str, ...] = PROVIDERS,
) -> Completion:
    """One call to OpenRouter's chat completions. One attempt, real timeout;
    any failure raises GenerationFailed (see REQUEST_TIMEOUT). The 429 and
    upstream-drop retries live in generate(), the caller that decides policy;
    `providers` is how a retry is aimed at the rest of the pinned list."""

    if params is None:
        params = {"temperature": TEMPERATURE, "top_p": TOP_P}

    session = await get_session()

    # The desk is one user message and there is no system message: the
    # frame at the top of the desk is hers, from her diary, not the
    # machinery's instruction to her.
    messages = [{"role": "user", "content": prompt}]

    started = time.monotonic()
    try:
        async with session.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('OPENROUTER_KEY', '')}",
                "HTTP-Referer": os.getenv(
                    "SITE_URL", "https://github.com/transfaeries/faebot-twitch"
                ),
                "X-Title": "Faebot Twitch",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": params.get("temperature", TEMPERATURE),
                "top_p": params.get("top_p", TOP_P),
                # Answer budget plus the reasoning's own room on top.
                "max_tokens": GENERATION_CAP + REASONING_CAP,
                "reasoning": {"max_tokens": REASONING_CAP},
                **(
                    {"provider": {"order": list(providers), "allow_fallbacks": False}}
                    if providers
                    else {}
                ),
            },
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as response:
            elapsed = time.monotonic() - started
            if response.status >= 400:
                body = (await response.text())[:400]
                raise GenerationFailed(
                    f"OpenRouter returned {response.status}: {body}",
                    elapsed,
                    status=response.status,
                )

            result = await response.json()
            elapsed = time.monotonic() - started

            try:
                choice = result["choices"][0]
            except (KeyError, IndexError, TypeError):
                raise GenerationFailed(
                    f"OpenRouter returned no choices: {str(result)[:200]}",
                    elapsed,
                    status=_body_error_code(result),
                ) from None
            message = choice.get("message") or {}
            return Completion(
                text=str(message.get("content") or ""),
                reasoning=str(message.get("reasoning") or ""),
                elapsed=elapsed,
                finish_reason=str(choice.get("finish_reason") or ""),
                model=str(result.get("model") or model),
                provider=str(result.get("provider") or ""),
                usage=result.get("usage") or {},
            )

    except GenerationFailed:
        raise
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - started
        raise GenerationFailed(
            f"OpenRouter timed out after {elapsed:.0f}s (limit {REQUEST_TIMEOUT:.0f}s)",
            elapsed,
        ) from None
    except (aiohttp.ClientError, ValueError) as error:
        elapsed = time.monotonic() - started
        raise GenerationFailed(
            f"OpenRouter call failed: {type(error).__name__}: {error}", elapsed
        ) from error
