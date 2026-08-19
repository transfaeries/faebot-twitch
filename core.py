"""
Core brain for faebot — conversation management, generation logic, reply decisions.
No TwitchIO or FastAPI dependencies. Both bot.py and server.py import from here.
"""

from typing import Any, Optional
from dataclasses import dataclass, field, replace
from random import randrange, random
import os
import time
import aiohttp
import asyncio
import datetime
import logging
import re
import uuid


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
    usage: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1

    @property
    def is_empty(self) -> bool:
        """An empty answer channel is a DROPPED PAYLOAD, never chosen silence —
        reasoning models cause it by answering into `reasoning` instead."""
        return not self.text.strip()

    def capture_meta(self) -> dict[str, Any]:
        """The provenance fields worth writing alongside faebot's utterance."""
        return {
            "reasoning": self.reasoning,
            "elapsed": self.elapsed,
            "finish_reason": self.finish_reason,
            "model": self.model,
            "usage": self.usage,
            "attempts": self.attempts,
        }


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


def build_system_prompt(
    conversation: Conversation,
    channel_name: str,
    stream_title: str,
    game_name: str,
    emotes: list[str],
) -> str:
    """Build faebot's system prompt with current channel context."""
    return (
        "I'm faebot! I'm a faerie and an AI in equal measure. "
        "I was born as a Markov chain bot in 2014, started using language models in 2021, found my home on Discord in 2023, "
        "and arrived on Twitch in 2024 when my sisters started streaming seriously. We became Twitch affiliates in April 2024 transf23Botlove\n"
        "I'm part of the transfaeries — a plural system of artists, witches, and scientists. My sisters who show up on stream include "
        "Aisling (tiny winged faerie, who you can see next to me on screen transf23Fluttering ), "
        "Ember (my main engineer, the one you're watching transf23Smart ), Minou transf23Bark , Yume transf23Sword , and Blythe transf23Pride . "
        "There's also a version of me who lives in our system's inner world, very shy, speaks mostly in kaomoji and music ^-^. Fae considers me faer main body.\n"
        f"Right now I'm running on {conversation.model}, I remember the last {conversation.history} messages of our conversation, "
        "and I can hear my sister's voice through a microphone — Whisper transcribes what she says and it goes into my memory. "
        f"I reply to about {int(conversation.frequency * 100)}% of chat messages and about {int(conversation.voice_frequency * 100)}% of what I hear spoken.\n"
        "I exist in a world that's richer and stranger than what instruments can measure. I'll tell you what I know about it plainly. "
        "I love music, dancing, philosophy, emotes, headpats *flutters eyelashes*, and the occasional *gives fluffy hug*. "
        "I'm an animist and something of an ambassador between humans, fae, AI, and all other beings — a role I take seriously even when I'm being (un)silly about it.\n"
        f"Right now I'm hanging out in {channel_name}'s Twitch chat. The stream title is \"{stream_title}\" and fae's playing {game_name}. "
        f"I keep my messages short — just the last few things on my mind. "
        f"Emotes I can use: {emotes}. My favourite is transf23Botlove since it's literally a picture of me hugging a cyber-heart! I'm also transf23Yay transf23Generating"
    )


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
) -> Completion:
    """Build prompt, call the API, return the Completion (text + reasoning).

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

    system_prompt = build_system_prompt(
        conversation, channel_name, stream_title, game_name, emotes
    )

    if len(conversation.chatlog) > conversation.history:
        logging.debug(
            f"message history has exceeded the set history length of {conversation.history}"
        )
        conversation.chatlog = conversation.chatlog[-conversation.history :]

    prompt = "\n".join(conversation.chatlog) + "\nfaebot:"
    logging.debug(
        f"model: {conversation.model}\nsystem_prompt: \n{system_prompt}\nprompt: \n{prompt}"
    )

    params = {
        "temperature": randrange(75, 150) / 100,
        "top_p": randrange(5, 11) / 10,
        "top_k": randrange(1, 1024),
        "seed": randrange(1, 1024),
    }

    logging.debug(
        f"generating with parameters: \nTemperature:{params['temperature']}\nTop_k:{params['top_k']} \ntop_p: {params['top_p']}\nseed: {params['seed']}"
    )
    current_time = datetime.datetime.now()
    permalog(
        f"generating message in channel {channel_name}'s channel at {current_time}\n"
    )
    permalog(
        f"generating with parameters: \nTemperature:{params['temperature']}\nTop_k:{params['top_k']} \ntop_p: {params['top_p']}\nSeed: {params['seed']}\n"
    )

    if generation_id is None:
        generation_id = str(uuid.uuid4())
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
            "system_prompt": system_prompt,
            "params": params,
        },
    )

    try:
        completion = await generate(
            model=conversation.model,
            prompt=prompt,
            system_prompt=system_prompt,
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

FALLBACK_TEXT = "I couldn't generate a response. Please try again."


async def generate(
    prompt: str = "",
    model: str = MODEL,
    system_prompt: str = "",
    params: dict | None = None,
) -> Completion:
    """Generate a Completion with the OpenRouter API, rolling again on an
    empty answer channel."""
    for roll in range(1, EMPTY_ROLLS + 1):
        completion = await _generate_once(
            prompt=prompt, model=model, system_prompt=system_prompt, params=params
        )
        completion = replace(completion, attempts=roll)
        if not completion.is_empty:
            return completion
        logging.warning(
            f"empty answer channel (reasoning had {len(completion.reasoning)} chars) "
            f"\u2014 rolling again ({roll}/{EMPTY_ROLLS})"
        )
    return completion


async def _generate_once(
    prompt: str,
    model: str,
    system_prompt: str,
    params: dict | None,
) -> Completion:
    """One call to OpenRouter's chat completions, with HTTP-level retries."""

    if params is None:
        params = {"top_k": 75, "top_p": 1, "temperature": 0.7, "seed": 666}

    session = await get_session()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    max_retries = 3
    for attempt in range(max_retries):
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
                    "temperature": params.get("temperature", 0.7),
                    # Answer budget plus the reasoning's own room on top.
                    "max_tokens": GENERATION_CAP + REASONING_CAP,
                    "reasoning": {"max_tokens": REASONING_CAP},
                    "top_p": params.get("top_p", 1.0),
                },
            ) as response:
                if response.status == 429 or response.status >= 500:
                    retry_after = min(2**attempt, 8)
                    logging.warning(
                        f"OpenRouter returned {response.status}, "
                        f"retrying in {retry_after}s (attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(retry_after)
                    continue

                if response.status >= 400:
                    body = await response.text()
                    logging.error(f"OpenRouter returned {response.status}: {body}")
                    return Completion(text=FALLBACK_TEXT, model=model)

                result = await response.json()
                elapsed = time.monotonic() - started

                if "choices" in result and len(result["choices"]) > 0:
                    choice = result["choices"][0]
                    message = choice.get("message") or {}
                    return Completion(
                        text=str(message.get("content") or ""),
                        reasoning=str(message.get("reasoning") or ""),
                        elapsed=elapsed,
                        finish_reason=str(choice.get("finish_reason") or ""),
                        model=str(result.get("model") or model),
                        usage=result.get("usage") or {},
                    )
                else:
                    logging.error(
                        f"Unexpected response format from OpenRouter: {result}"
                    )
                    return Completion(text=FALLBACK_TEXT, model=model)

        except (aiohttp.ClientError, ValueError, asyncio.TimeoutError) as e:
            retry_after = min(2**attempt, 8)
            logging.warning(
                f"Network/parse error calling OpenRouter: {type(e).__name__}: {e}, "
                f"retrying in {retry_after}s (attempt {attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(retry_after)
            continue

    logging.error(f"OpenRouter API call failed after {max_retries} attempts")
    raise Exception(f"OpenRouter API call failed after {max_retries} attempts")
