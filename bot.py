"""
Twitch bot — thin TwitchIO wrapper. Event handlers and command routing.
All conversation management and generation logic lives in core.py.
"""

from typing import Optional
from twitchio.ext import commands
from functools import wraps
import os
import aiohttp
import logging
import asyncio
import re

import core


TWITCH_TOKEN = os.getenv("TWITCH_TOKEN", "")
INITIAL_CHANNELS = os.getenv("INITIAL_CHANNELS", "").split(",")
ADMIN = os.getenv("ADMIN", "").split(",")


# set up logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


class Faebot(commands.Bot):
    def __init__(self):
        self.emotes: list = []
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

        if "faebot" in text.lower():
            logging.info(
                f"faebot mentioned by streamer, boosting to chat frequency ({conversation.frequency})"
            )
            frequency = conversation.frequency
        else:
            frequency = conversation.voice_frequency
        if core.choose_to_reply(channel_name, frequency):
            asyncio.create_task(self._generate_and_send(channel_name))

    async def _generate_and_send(self, channel_name: str):
        """Fetch channel info, generate a response via core, and send it to chat."""
        channel = self.get_channel(channel_name)

        channel_info = await self.fetch_channel(channel_name)
        stream_title = channel_info.title if channel_info else "Unknown"
        game_name = channel_info.game_name if channel_info else "Unknown"

        try:
            response = await core.generate_response(
                channel_name=channel_name,
                stream_title=stream_title,
                game_name=game_name,
                emotes=self.emotes,
            )
            await channel.send(response)
        except Exception as e:
            logging.error(
                f"Unknown error has occured, please contact the administrator. Error: {e}"
            )
            await channel.send(
                "Oops, something strange has happened. Please let the developer know!"
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
            return asyncio.create_task(self._generate_and_send(message.channel.name))

    async def close(self):
        """Close the bot's resources gracefully."""
        await core.close_session()
        await super().close()

    # --- commands for everyone ---

    @commands.command()
    async def hello(self, ctx: commands.Context):
        """Display the help message."""
        await ctx.reply(
            "Hello, my name is faebot, I'm an AI chatbot developed by the transfaeries. "
            "I'll chime in on the chat and reply every so often, and I'll always reply to messages with my name on them. For mod commands use 'fb;mods'"
        )

    @commands.command()
    async def help(self, ctx: commands.Context):
        """Display the help message."""
        await ctx.reply(
            "Hello, my name is faebot, I'm an AI chatbot developed by the transfaeries. I'll chime in on the chat and reply every so often, "
            "and I'll always reply to messages with my name on them.For mod commands use 'fb;mods'"
        )

    @commands.command()
    async def invite(self, ctx: commands.Context):
        """Invite Faebot to your channel."""
        await ctx.reply(
            "Thanks for the invitation, but you should ask the transfaeries first. Send faer a whisper!"
        )

    @commands.command()
    async def mods(self, ctx: commands.Context):
        """Display the mods command message."""
        await ctx.reply(
            "Here are the commands mods can use with faebot. | fb;freq to set the frequency of responses. | fb;hist to set message history length.| "
            "fb;silence to silence faebot entirely. | fb;clear to clear faebot's memory. | fb;part to have faebot leave the channel."
        )

    @commands.command()
    async def ping(self, ctx: commands.Context):
        """Ping the bot."""
        message = ctx.message.content
        message_tokens = message.split(" ")
        await ctx.reply(f'pong {" ".join(message_tokens[1:])}')

    @commands.command()
    async def alias(self, ctx: commands.Context):
        """Set or check your preferred alias."""
        arguments = ctx.message.content.split(" ")
        username = ctx.message.author.name

        if len(arguments) > 1:
            new_alias = " ".join(arguments[1:])
            core.aliases[username] = new_alias
            reply = f"Got it! From now on I'll think of you as {new_alias}"
            core.conversations[ctx.channel.name].chatlog.append(
                f"{username}: fae;alias {new_alias}"
            )
            core.conversations[ctx.channel.name].chatlog.append(f"faebot: {reply}")
            return await ctx.reply(reply)

        if username in core.aliases:
            return await ctx.reply(
                f"I currently know you as {core.aliases[username]}, should I call you something else?"
            )
        else:
            return await ctx.reply(
                "You haven't given me a different name to use. Use 'fae;alias <name>' to set one!"
            )

    # --- commands for mods ---

    def requires_mod(command: commands) -> commands:
        @wraps(command)
        async def mod_command(self, ctx: commands.Context):
            if ctx.author.is_mod or ctx.author.name in ADMIN:
                return await command(self, ctx)
            return await ctx.send("you must be a mod or an admin to use this command")

        return mod_command

    @commands.command()
    @requires_mod
    async def clear(self, ctx: commands.Context):
        """Clear faebot's memory."""
        core.conversations[ctx.channel.name].chatlog = []
        return await ctx.reply("message history has been cleared. faebot has forgotten")

    @commands.command()
    @requires_mod
    async def freq(self, ctx: commands.Context):
        """Check or change message frequency in this channel.
        Usage: fb;freq [chat_freq] [voice_freq]
        Frequency is 0-1 (e.g. 0.1 = 10% chance to reply)"""
        arguments = ctx.message.content.split(" ")
        conversation = core.conversations[ctx.channel.name]
        if len(arguments) > 1:
            try:
                new_freq = float(arguments[1])
                conversation.frequency = new_freq
                msg = f"Chat frequency set to {new_freq}"
                if len(arguments) > 2:
                    voice_freq = float(arguments[2])
                    conversation.voice_frequency = voice_freq
                    msg += f", voice frequency set to {voice_freq}"
                return await ctx.send(msg)
            except ValueError:
                return await ctx.send("Frequency must be a number between 0 and 1")

        return await ctx.send(
            f"Chat frequency: {conversation.frequency}, "
            f"Voice frequency: {conversation.voice_frequency}"
        )

    @commands.command()
    @requires_mod
    async def hist(self, ctx: commands.Context):
        """Check or change message history length in the channel."""
        arguments = ctx.message.content.split(" ")
        if len(arguments) > 1:
            if str(arguments[1]).isdigit():
                core.conversations[ctx.channel.name].history = int(arguments[1])
                return await ctx.send(
                    f"changed message history length in this channel to {core.conversations[ctx.channel.name].history}"
                )

        return await ctx.send(
            f"current message history length in this channel is {core.conversations[ctx.channel.name].history}"
        )

    @commands.command()
    @requires_mod
    async def part(self, ctx: commands.Context):
        """Ask faebot to leave the channel."""
        await ctx.reply("Oki, bye bye. *faebot has left the channel*")
        return await self.part_channels([ctx.channel.name])

    @commands.command()
    @requires_mod
    async def prompt(self, ctx: commands.Context):
        """Display the current system prompt."""
        return await ctx.send(
            "The system prompt is auto-generated each reply from current channel info (game, title, emotes). "
            "A custom prompt override is planned for a future update."
        )

    @commands.command()
    @requires_mod
    async def silence(self, ctx: commands.Context):
        """Ask faebot to be quiet."""
        conversation = core.conversations[ctx.channel.name]
        if conversation.silenced:
            reply = "Yay I can speak again!"
        else:
            reply = "oki, I'll be quiet. 🤐"
        conversation.silenced = not conversation.silenced
        logging.info(
            f"faebot silent status toggled to {conversation.silenced}"
        )
        return await ctx.send(reply)

    # --- commands for admins ---

    @commands.command()
    async def join(self, ctx: commands.Context, user: str | None) -> None:
        """Invite faebot to join a channel."""
        if ctx.author.name not in ADMIN:
            return await ctx.send("sorry you need to be an admin to use that command")

        await self.join_channels([user])
        logging.info(f"Joined new channel: {user}")
        return await ctx.reply(f"Joined new channel: {user}")

    @commands.command()
    async def model(self, ctx: commands.Context):
        """Check or change the model used to generate in the channel."""
        if ctx.author.name not in ADMIN:
            return await ctx.send("sorry you need to be an admin to use that command")
        arguments = ctx.message.content.split(" ")
        if len(arguments) > 1:
            core.conversations[ctx.channel.name].model = " ".join(arguments[1:])
            return await ctx.send(
                f"changed model in this channel to {core.conversations[ctx.channel.name].model}"
            )

        return await ctx.send(
            f"current model in this channel is {core.conversations[ctx.channel.name].model}"
        )


if __name__ == "__main__":
    if not TWITCH_TOKEN:
        logging.error("TWITCH_TOKEN not set. Did you forget to source secrets?\n")
    else:
        bot = Faebot()
        bot.run()
