"""
Core brain for faebot — conversation management, generation logic, reply decisions.
No TwitchIO or FastAPI dependencies. Both bot.py and server.py import from here.
"""

from typing import Optional
from dataclasses import dataclass, field
from random import randrange, random
import os
import aiohttp
import asyncio
import datetime
import logging
import re


MODEL = os.getenv("MODEL", "google/gemini-2.5-flash")


@dataclass
class Conversation:
    """Per-channel conversation state."""

    channel: str
    chatlog: list = field(default_factory=list)
    frequency: float = 0.1
    voice_frequency: float = 0.05
    history: int = 20
    model: str = MODEL
    silenced: bool = False


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


async def generate_response(
    channel_name: str,
    stream_title: str = "Unknown",
    game_name: str = "Unknown",
    emotes: list[str] | None = None,
) -> str:
    """Build prompt, call the API, return the response text.

    The caller is responsible for sending the response to chat
    and for fetching stream_title/game_name from TwitchIO.
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
        conversation.chatlog = conversation.chatlog[-conversation.history:]

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

    response = await generate(
        model=conversation.model,
        prompt=prompt,
        system_prompt=system_prompt,
        params=params,
    )
    response = fix_emote_spacing(response, emotes)
    logging.info(f"received response: {response}")
    if len(response) > 499:
        logging.debug("generated content exceeded 500 characters, trimming.")
        response = response[:499] + "\u2013"
    permalog(
        f"generated message:{response}\n------------------------------------------------------------\n\n"
    )

    conversation.chatlog.append(f"faebot: {response}")
    return response


async def generate(
    prompt: str = "",
    model: str = MODEL,
    system_prompt: str = "",
    params: dict | None = None,
) -> str:
    """Generate completions with the OpenRouter API."""

    if params is None:
        params = {"top_k": 75, "top_p": 1, "temperature": 0.7, "seed": 666}

    session = await get_session()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    max_retries = 3
    for attempt in range(max_retries):
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
                    "max_tokens": 150,
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
                    logging.error(
                        f"OpenRouter returned {response.status}: {body}"
                    )
                    return "I couldn't generate a response. Please try again."

                result = await response.json()

                if "choices" in result and len(result["choices"]) > 0:
                    reply = result["choices"][0]["message"]["content"]
                    return str(reply)
                else:
                    logging.error(
                        f"Unexpected response format from OpenRouter: {result}"
                    )
                    return "I couldn't generate a response. Please try again."

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
