"""
Chat commands for faebot — extracted as a mixin so bot.py stays thin.
Faebot inherits from this class to gain all fb;/fae; commands.
"""

from twitchio.ext import commands
from functools import wraps
from typing import Awaitable, Callable
import os
import logging

import core


ADMIN = os.getenv("ADMIN", "").split(",")


def requires_mod(command):
    @wraps(command)
    async def mod_command(self, ctx: commands.Context):
        if ctx.author.is_mod or ctx.author.name in ADMIN:
            return await command(self, ctx)
        return await ctx.send("you must be a mod or an admin to use this command")

    return mod_command


class FaebotCommands:
    """Mixin providing all fb;/fae; chat commands."""

    # Provided by twitchio's Bot when this mixin is composed into Faebot;
    # declared here so mypy knows the mixin may call them.
    join_channels: Callable[[list[str]], Awaitable[None]]
    part_channels: Callable[[list[str]], Awaitable[None]]

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
            "and I'll always reply to messages with my name on them.For mod commands use 'fae;mods'"
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
            "Here are the commands mods can use with faebot. | fae;freq to set the frequency of responses. | fae;hist to set message history length.| "
            "fae;silence to silence faebot entirely. | fae;clear to clear faebot's memory. | fae;part to have faebot leave the channel."
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
        Usage: fae;freq [chat_freq] [voice_freq]
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
            "The system prompt is auto-generated each reply from current channel info (game, title, emotes)."
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
        logging.info(f"faebot silent status toggled to {conversation.silenced}")
        return await ctx.send(reply)

    # --- commands for admins ---

    @commands.command()
    async def join(self, ctx: commands.Context, user: str | None) -> None:
        """Invite faebot to join a channel."""
        if ctx.author.name not in ADMIN:
            return await ctx.send("sorry you need to be an admin to use that command")
        if not user:
            return await ctx.reply("usage: join <channel>")

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
