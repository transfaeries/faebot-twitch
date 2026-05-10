"""
Twitch bot — thin TwitchIO wrapper. Event handlers and command routing.
All conversation management and generation logic lives in core.py.
"""

from twitchio.ext import commands
import os
import logging
import asyncio
import re
import uuid

import core
from commands import FaebotCommands


TWITCH_TOKEN = os.getenv("TWITCH_TOKEN", "")
INITIAL_CHANNELS = os.getenv("INITIAL_CHANNELS", "").split(",")
VOICE_ACTIVATION = os.getenv("VOICE_ACTIVATION", "faebot dearest").lower()


# set up logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


class Faebot(commands.Bot, FaebotCommands):
    def __init__(self, event_queue: asyncio.Queue | None = None):
        self.emotes: list = []
        self.event_queue = event_queue
        self.whisper_filter: list[str] = [
            "faebot.com",
        ]
        super().__init__(
            token=TWITCH_TOKEN,
            prefix=["fb;", "fae;"],
            initial_channels=INITIAL_CHANNELS,
        )

    async def event_ready(self):
        """We are logged in and ready to chat and use commands."""
        await self.fetch_emotes()
        logging.info(f"Logged in as | {self.nick}")
        logging.info(f"User id is | {self.user_id}")
        logging.info(f"Joined channels {INITIAL_CHANNELS}")

    async def fetch_emotes(self):
        """Fetch channel emotes for all joined channels from the Twitch API."""
        self.emotes = []
        for channel in self.connected_channels:
            try:
                users = await self.fetch_users(names=[channel.name])
                if users:
                    channel_emotes = await users[0].fetch_channel_emotes()
                    # Only include emotes faebot can actually use (tier 1 and follower)
                    # TODO: fetch emote usability programmatically (e.g. fetch_user_emotes with faebot's token)
                    # rather than assuming tier "1000" and type "follower" are always the right filter
                    available = [
                        emote.name
                        for emote in channel_emotes
                        if emote.tier == "1000" or emote.type == "follower"
                    ]
                    self.emotes.extend(available)
                    logging.info(
                        f"Fetched {len(available)}/{len(channel_emotes)} usable emotes from {channel.name}"
                    )
            except Exception as e:
                logging.warning(f"Failed to fetch emotes for {channel.name}: {e}")
        if not self.emotes:
            logging.warning("No emotes fetched from any channel")
        else:
            logging.info(f"Total emotes loaded: {self.emotes}")

    def filter_transcription(self, text: str) -> str | None:
        """Filter out known Whisper mistranscriptions. Returns None to skip entirely."""
        for banned in self.whisper_filter:
            if banned.lower() in text.lower():
                logging.debug(
                    f"Filtered banned string '{banned}' from transcription: {text}"
                )
                return None
        return text

    async def handle_transcription(self, channel_name: str, text: str):
        """Handle a voice transcription from the streamer."""
        filtered = self.filter_transcription(text)
        if filtered is None:
            return
        text = filtered

        conversation = core.ensure_conversation(channel_name)
        conversation.chatlog.append(f"[streamer voice] {channel_name}: {text}")
        logging.debug(f"Voice transcription added to {channel_name}: {text}")

        text_normalized = re.sub(r"[^\w\s]", "", text.lower())
        if VOICE_ACTIVATION in text_normalized:
            logging.info(f"Voice activation phrase detected, generating!")
            asyncio.create_task(
                self._generate_and_send(channel_name, trigger_type="voice")
            )
        elif "faebot" in text.lower():
            logging.info(
                f"faebot mentioned by streamer, boosting to chat frequency ({conversation.frequency})"
            )
            frequency = conversation.frequency
            if core.choose_to_reply(channel_name, frequency):
                asyncio.create_task(
                    self._generate_and_send(channel_name, trigger_type="voice")
                )
        else:
            frequency = conversation.voice_frequency
            if core.choose_to_reply(channel_name, frequency):
                asyncio.create_task(
                    self._generate_and_send(channel_name, trigger_type="voice")
                )

    async def _generate_and_send(self, channel_name: str, trigger_type: str = "chat"):
        """Fetch channel info, generate a response via core, and send it to chat.

        The dashboard's `response` event is emitted only after Twitch acknowledges
        the send. If Twitch rejects (e.g. rate limit), we emit an `error` event
        with the same generation_id so the card flips red instead of green.
        """
        channel = self.get_channel(channel_name)

        channel_info = await self.fetch_channel(channel_name)
        stream_title = channel_info.title if channel_info else "Unknown"
        game_name = channel_info.game_name if channel_info else "Unknown"

        generation_id = str(uuid.uuid4())
        try:
            response = await core.generate_response(
                channel_name=channel_name,
                stream_title=stream_title,
                game_name=game_name,
                emotes=self.emotes,
                events=self.event_queue,
                trigger_type=trigger_type,
                generation_id=generation_id,
            )
        except Exception as e:
            # core has already emitted an `error` event for this generation
            logging.error(f"Generation failed: {e}")
            try:
                await channel.send(
                    "Oops, something strange has happened. Please let the developer know!"
                )
            except Exception as fallback_e:
                logging.error(f"Failed to send fallback message too: {fallback_e}")
            return

        try:
            await channel.send(response)
        except Exception as e:
            logging.warning(
                f"Twitch IRC send failed (likely connection lost): "
                f"{type(e).__name__}: {e}"
            )
            core.put_event(
                self.event_queue,
                {
                    "type": "error",
                    "id": generation_id,
                    "channel": channel_name,
                    "error": f"twitch send failed: {type(e).__name__}: {e}",
                },
            )
            return

        # NOTE: this is optimistic. `channel.send` returning means TwitchIO
        # successfully transmitted the IRC PRIVMSG, NOT that Twitch delivered
        # it. Twitch can still reject the message via an async NOTICE
        # (msg_ratelimit, msg_duplicate, msg_slowmode, etc.) which we don't
        # currently catch — in those cases the card turns green here even
        # though the message never reached chat. Future work: wire up
        # event_notice and correlate to the most recent send per channel.
        # See ROADMAP "Twitch NOTICE handling".
        core.put_event(
            self.event_queue,
            {
                "type": "response",
                "id": generation_id,
                "channel": channel_name,
                "text": response,
            },
        )

    async def event_message(self, message):
        if message.echo:
            return

        logging.debug(f"received message: {message.author}: {message.content}")
        core.ensure_conversation(message.channel.name)

        if (
            message.content.startswith("!")
            or message.content.startswith("fb;")
            or message.content.startswith("fae;")
        ):
            return await self.handle_commands(message)

        display_name = core.aliases.get(message.author.name, message.author.name)
        core.conversations[message.channel.name].chatlog.append(
            f"{display_name}: {message.content}"
        )

        conversation = core.conversations[message.channel.name]
        if "faebot" in message.content.lower():
            logging.info(f"faebot mentioned by {display_name}, replying")
            frequency = 1.0
        else:
            frequency = conversation.frequency
        if core.choose_to_reply(message.channel.name, frequency):
            return asyncio.create_task(
                self._generate_and_send(message.channel.name, trigger_type="chat")
            )

    async def close(self):
        """Close the bot's resources gracefully."""
        await core.close_session()
        await super().close()


if __name__ == "__main__":
    if not TWITCH_TOKEN:
        logging.error("TWITCH_TOKEN not set. Did you forget to source secrets?\n")
    else:
        bot = Faebot()
        bot.run()
