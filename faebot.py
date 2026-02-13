from types import coroutine
from typing import Optional
from twitchio import InvalidContent
from twitchio.ext import commands
import os
import aiohttp
import logging
import asyncio
import datetime
from random import randrange
from dataclasses import dataclass, field
from functools import wraps
import signal


TWITCH_TOKEN = os.getenv("TWITCH_TOKEN", "")
INITIAL_CHANNELS = os.getenv("INITIAL_CHANNELS", "").split(",")
MODEL = os.getenv("MODEL", "google/gemini-2.5-flash")
ADMIN = os.getenv("ADMIN", "").split(",")


# set up logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


@dataclass
class Conversation:
    """for storing conversations"""

    channel: str
    chatlog: list = field(default_factory=list)  # dict[int, Message]
    conversants: list = field(default_factory=list)
    system_prompt: str = ""
    frequency: int = 10
    history: int = 20
    model: str = MODEL
    silenced: bool = False


class Faebot(commands.Bot):
    def __init__(self):
        # Initialise our Bot with our access token, prefix and a list of channels to join on boot...
        self.conversations: dict[str, Conversation] = {}
        self.aliases: dict[str, str] = {
            "hatsunemikuisbestwaifu": "Miku",
        }
        self.session: Optional[
            aiohttp.ClientSession
        ] = None  # Add session for HTTP requests
        self.emotes = list()  # Store emotes for each channel
        super().__init__(
            token=TWITCH_TOKEN,
            prefix=["fb;", "fae;"],
            initial_channels=INITIAL_CHANNELS,
        )
        signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(self.close()))

    async def event_ready(self):
        # We are logged in and ready to chat and use commands...
        self.session = aiohttp.ClientSession()  # Initialize HTTP session
        await self.fetch_emotes()
        logging.info(f"Logged in as | {self.nick}")
        logging.info(f"User id is | {self.user_id}")
        logging.info(f"Joined channels {INITIAL_CHANNELS}")

    async def fetch_emotes(self):
        """Fetch channel emotes for all joined channels from the Twitch API"""
        self.emotes = []
        for channel in self.connected_channels:
            try:
                users = await self.fetch_users(names=[channel.name])
                if users:
                    channel_emotes = await users[0].fetch_channel_emotes()
                    # Only include emotes faebot can actually use (tier 1 and follower)
                    available = [
                        emote.name
                        for emote in channel_emotes
                        if emote.tier == "1000"
                        or emote.type == "follower"
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

    def ensure_conversation(self, channel_name: str) -> Conversation:
        """Get or create a conversation for a channel."""
        if channel_name not in self.conversations:
            self.conversations[channel_name] = Conversation(
                channel=channel_name,
                system_prompt="",
            )
            logging.info(f"Created new conversation for {channel_name}")
        return self.conversations[channel_name]

    async def handle_transcription(self, channel_name: str, text: str):
        """Handle a voice transcription from the streamer."""
        conversation = self.ensure_conversation(channel_name)
        conversation.chatlog.append(f"[streamer voice] {channel_name}: {text}")
        logging.info(f"Voice transcription added to {channel_name}: {text}")

    async def event_message(self, message):
        # Messages with echo set to True are messages sent by the bot...
        # For now we just want to ignore them...
        if message.echo:
            return

        # Print the contents of our message to console...
        channel_info = await self.fetch_channel(message.channel.name)
        stream_title = channel_info.title if channel_info else "Unknown"
        game_name = channel_info.game_name if channel_info else "Unknown"
        logging.info(f"received message: {message.author}: {message.content}")
        logging.info(f"channel object {message.channel.name}")
        logging.info(f"channel title: {stream_title}")
        logging.info(f"channel category: {game_name}")
        self.ensure_conversation(message.channel.name)

        # command, execute command if appropriate otherwise return out
        # TODO: change if statement to use prefixes directly
        if (
            message.content.startswith("!")
            or message.content.startswith("fb;")
            or message.content.startswith("fae;")
        ):
            return await self.handle_commands(message)

        # log message
        # Use alias if available, otherwise use regular username
        display_name = self.aliases.get(message.author.name, message.author.name)
        self.conversations[message.channel.name].chatlog.append(
            f"{display_name}: {message.content}"
        )

        if self.choose_to_reply(message):
            return asyncio.create_task(self.generate_response(message))

    def choose_to_reply(self, message) -> bool:
        """determine whether faebot replies to a message or not"""

        if self.conversations[message.channel.name].silenced:
            logging.info(
                f"faebot is silenced in channel {message.channel.name} faebot won't reply!"
            )
            return False

        if "faebot" in message.content.lower():
            return True

        if self.conversations[message.channel.name].frequency == 1:
            logging.info(
                f"frequency set to {self.conversations[message.channel.name].frequency} in this channel generating on every message!"
            )
            return True

        if self.conversations[message.channel.name].frequency < 1:
            logging.info(
                f"frequency set to {self.conversations[message.channel.name].frequency}, that's fewer than one faebot will only reply to faer name!"
            )
            return False

        else:
            chance = randrange(self.conversations[message.channel.name].frequency)
            if chance == 0:
                logging.info(f"rolled a {chance} generating!")
                return True
            else:
                logging.info(f"rolled a {chance}, not generating!")
                return False

    def permalog(self, log_message):
        with open("permalog.txt", "a") as permalog:
            permalog.write(log_message)

    async def generate_response(self, message):
        """prompt the GenAI API for a message"""

        # Update system prompt with current channel info
        channel_info = await self.fetch_channel(message.channel.name)
        stream_title = channel_info.title if channel_info else "Unknown"
        game_name = channel_info.game_name if channel_info else "Unknown"

        self.conversations[message.channel.name].system_prompt = (
            f"I'm an AI chatbot called faebot. \n"
            f"I'm hanging out in {message.channel.name}'s chat on twitch where I enjoy talking with chatters about whatever the streamer, {message.channel.name}, is doing. "
            f"The streamer is playing {game_name} and the title is {stream_title}\n"
            f"I am friendly and talkative. I like to use the channel emotes to express myself they are {self.emotes},"
            f"my favourite is transf23Botlove since it's a picture of me! \n"
            "I make sure my messages are below the character limit of 500 characters. I prioritize replying to the last message and I never ask followup questions."
        )

        if (
            len(self.conversations[message.channel.name].chatlog)
            > self.conversations[message.channel.name].history
        ):
            logging.info(
                f"message history has exceeded the set history length of {self.conversations[message.channel.name].history}"
            )
            self.conversations[message.channel.name].chatlog = self.conversations[
                message.channel.name
            ].chatlog[
                len(self.conversations[message.channel.name].chatlog)
                - self.conversations[message.channel.name].history :
            ]

        prompt = (
            "\n".join(self.conversations[message.channel.name].chatlog) + "\nfaebot:"
        )
        logging.info(
            f"model: {self.conversations[message.channel.name].model}\nsystem_prompt: \n{self.conversations[message.channel.name].system_prompt}\nprompt: \n{prompt}"
        )

        params = {
            "temperature": randrange(75, 150) / 100,
            "top_p": randrange(5, 11) / 10,
            "top_k": randrange(1, 1024),
            "seed": randrange(1, 1024),
        }
        # params = {"temperature":1.5, "top_p":0.5, "top_k": 232}

        logging.info(
            f"generating with parameters: \nTemperature:{params['temperature']}\nTop_k:{params['top_k']} \ntop_p: {params['top_p']}\nseed: {params['seed']}"
        )
        current_time = datetime.datetime.now()
        self.permalog(
            f"generating message in channel {message.channel.name}'s channel at {current_time}\n"
        )
        self.permalog(
            f"generating with parameters: \nTemperature:{params['temperature']}\nTop_k:{params['top_k']} \ntop_p: {params['top_p']}\nSeed: {params['seed']}\n"
        )

        try:
            response = await self.generate(
                model=self.conversations[message.channel.name].model,
                prompt=prompt,
                author=message.author.display_name,
                system_prompt=self.conversations[message.channel.name].system_prompt,
                params=params,
            )
            logging.info(f"received response: {response}")
            self.permalog(
                f"generated message:{response}\n------------------------------------------------------------\n\n"
            )
            await message.channel.send(response)

        except InvalidContent:
            logging.info(
                "generated content exceeded 500 characters, trimming and posting."
            )
            response = response[0:499] + "–"
            await message.channel.send(response)

        except Exception as e:
            logging.info(
                f"Unknown error has occured, please contact the administrator. Error: {e}"
            )
            response = (
                "Oops, something strange has happened. Please let the developer know!"
            )
            await message.channel.send(response)

        self.conversations[message.channel.name].chatlog.append(f"faebot: {response}")
        return

    async def generate(
        self,
        prompt: str = "",
        author="",
        model=MODEL,
        system_prompt="",
        params={"top_k": 75, "top_p": 1, "temperature": 0.7, "seed": 666},
    ) -> str:
        """generates completions with the OpenRouter API"""

        if not self.session:
            self.session = aiohttp.ClientSession()

        # Create a proper message structure for OpenRouter
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        try:
            async with self.session.post(
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
                result = await response.json()

                # Extract the assistant's message content
                if "choices" in result and len(result["choices"]) > 0:
                    reply = result["choices"][0]["message"]["content"]
                    return str(reply)
                else:
                    logging.error(
                        f"Unexpected response format from OpenRouter: {result}"
                    )
                    return "I couldn't generate a response. Please try again."

        except Exception as e:
            logging.error(f"Error in OpenRouter API call: {e}")
            raise e  # Re-raise to be handled by the calling method

    async def close(self):
        """Closes the bot's resources gracefully"""
        if self.session:
            await self.session.close()
        await super().close()

    # commands for everyone #

    @commands.command()
    async def hello(self, ctx: commands.Context):
        """display the help message"""
        await ctx.reply(
            "Hello, my name is faebot, I'm an AI chatbot developed by the transfaeries. "
            "I'll chime in on the chat and reply every so often, and I'll always reply to messages with my name on them. For mod commands use 'fb;mods'"
        )

    @commands.command()
    async def help(self, ctx: commands.Context):
        """display the help message"""
        await ctx.reply(
            "Hello, my name is faebot, I'm an AI chatbot developed by the transfaeries. I'll chime in on the chat and reply every so often, "
            "and I'll always reply to messages with my name on them.For mod commands use 'fb;mods'"
        )

    @commands.command()
    async def invite(self, ctx: commands.Context):
        """Invite Faebot to your channel"""
        await ctx.reply(
            "Thanks for the invitation, but you should ask the transfaeries first. Send faer a whisper!"
        )

    @commands.command()
    async def mods(self, ctx: commands.Context):
        """display the mods command message"""
        await ctx.reply(
            "Here are the commands mods can use with faebot. | fb;freq to set the frequency of responses. | fb;hist to set message history length.| "
            "fb;silence to silence faebot entirely. | fb;clear to clear faebot's memory. | fb;part to have faebot leave the channel."
        )

    @commands.command()
    async def ping(self, ctx: commands.Context):
        """ping the bot"""
        message = ctx.message.content
        message_tokens = message.split(" ")
        await ctx.reply(f'pong {" ".join(message_tokens[1:])}')

    @commands.command()
    async def alias(self, ctx: commands.Context):
        """set or check your preferred alias"""
        arguments = ctx.message.content.split(" ")
        username = ctx.message.author.name

        if len(arguments) > 1:
            # Set the alias
            new_alias = " ".join(arguments[1:])
            self.aliases[username] = new_alias
            reply = f"Got it! From now on I'll think of you as {new_alias}"
            # log users request and faebot's response so it shows up in chatlog
            self.conversations[ctx.channel.name].chatlog.append(
                f"{username}: fae;alias {new_alias}"
            )
            self.conversations[ctx.channel.name].chatlog.append(f"faebot: {reply}")
            return await ctx.reply(reply)

        # Check current alias
        if username in self.aliases:
            return await ctx.reply(
                f"I currently know you as {self.aliases[username]}, should I call you something else?"
            )
        else:
            return await ctx.reply(
                "You haven't given me a different name to use. Use 'fae;alias <name>' to set one!"
            )

    # commands for mods ##

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
        """clear faebot's memory"""
        self.conversations[ctx.channel.name].chatlog = []
        return await ctx.reply("message history has been cleared. faebot has forgotten")

    @commands.command()
    @requires_mod
    async def freq(self, ctx: commands.Context):
        """check or change message frequency in this channel"""
        arguments = ctx.message.content.split(" ")
        if len(arguments) > 1:
            if str(arguments[1]).isdigit():
                self.conversations[ctx.channel.name].frequency = int(arguments[1])
                return await ctx.send(
                    f"changed message frequency in this channel to {self.conversations[ctx.channel.name].frequency}"
                )

        return await ctx.send(
            f"current message frequency in this channel is {self.conversations[ctx.channel.name].frequency}"
        )

    @commands.command()
    @requires_mod
    async def hist(self, ctx: commands.Context):
        """check or change message history length in the channel"""

        arguments = ctx.message.content.split(" ")
        if len(arguments) > 1:
            if str(arguments[1]).isdigit():
                self.conversations[ctx.channel.name].history = int(arguments[1])
                return await ctx.send(
                    f"changed message history length in this channel to {self.conversations[ctx.channel.name].history}"
                )

        return await ctx.send(
            f"current message history length in this channel is {self.conversations[ctx.channel.name].history}"
        )

    @commands.command()
    @requires_mod
    async def part(self, ctx: commands.Context):
        """ask faebot to leave the channel"""
        await ctx.reply("Oki, bye bye. *faebot has left the channel*")
        return await self.part_channels([ctx.channel.name])

    @commands.command()
    @requires_mod
    async def prompt(self, ctx: commands.Context):
        """check or change the system prompt in the channel"""

        arguments = ctx.message.content.split(" ")
        if len(arguments) > 1:
            self.conversations[ctx.channel.name].system_prompt = " ".join(arguments[1:])
            return await ctx.send(
                f"changed system prompt in this channel to {self.conversations[ctx.channel.name].system_prompt}"
            )

        return await ctx.send(
            f"current system_prompt in this channel is {self.conversations[ctx.channel.name].system_prompt}"
        )

    @commands.command()
    @requires_mod
    async def silence(self, ctx: commands.Context):
        """ask faebot to be quiet"""
        if self.conversations[ctx.channel.name].silenced:
            reply = "Yay I can speak again!"
        else:
            reply = "oki, I'll be quiet. 🤐"
        self.conversations[ctx.channel.name].silenced = not self.conversations[
            ctx.channel.name
        ].silenced
        logging.info(
            f"faebot silent status toggled to {self.conversations[ctx.channel.name].silenced}"
        )
        return await ctx.send(reply)

    # commands for admins ###

    @commands.command()
    async def join(self, ctx: commands.Context, user: str | None) -> coroutine:
        """invite faebot to join a channel"""
        if ctx.author.name not in ADMIN:
            return await ctx.send("sorry you need to be an admin to use that command")

        await self.join_channels([user])
        logging.info(f"Joined new channel: {user}")
        return await ctx.reply(f"Joined new channel: {user}")

    @commands.command()
    async def model(self, ctx: commands.Context):
        """check or change the model used to generate in the channel"""
        if ctx.author.name not in ADMIN:
            return await ctx.send("sorry you need to be an admin to use that command")
        arguments = ctx.message.content.split(" ")
        if len(arguments) > 1:
            self.conversations[ctx.channel.name].model = " ".join(arguments[1:])
            return await ctx.send(
                f"changed model in this channel to {self.conversations[ctx.channel.name].model}"
            )

        return await ctx.send(
            f"current model in this channel is {self.conversations[ctx.channel.name].model}"
        )


if __name__ == "__main__":
    if not TWITCH_TOKEN:
        logging.error(
            "TWITCH_TOKEN not set. Did you forget to source secrets?\n"
        )
    else:
        bot = Faebot()
        bot.run()
