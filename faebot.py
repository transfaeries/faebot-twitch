from typing import Optional
from twitchio.ext import commands
import os
import aiohttp
import logging
import asyncio
import datetime
from random import randrange, random
from dataclasses import dataclass, field
from functools import wraps
import re


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
    chatlog: list = field(default_factory=list)
    frequency: float = 0.1
    voice_frequency: float = 0.05
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

    def fix_emote_spacing(self, text: str) -> str:
        """Ensure emotes are surrounded by whitespace so Twitch renders them."""
        if not self.emotes:
            return text
        # Split text on emote boundaries, longest first so transf23Fluttering
        # is matched before transf23Flutter (re.split doesn't backtrack like sub)
        sorted_emotes = sorted(self.emotes, key=len, reverse=True)
        pattern = "(" + "|".join(re.escape(e) for e in sorted_emotes) + ")"
        parts = re.split(pattern, text)
        # Pad each emote with spaces, then collapse doubles
        result = []
        for part in parts:
            if part in self.emotes:
                result.append(f" {part} ")
            else:
                result.append(part)
        return re.sub(r"  +", " ", "".join(result)).strip()

    def filter_transcription(self, text: str) -> str | None:
        """Filter out known Whisper mistranscriptions. Returns None to skip entirely."""
        for banned in self.whisper_filter:
            if banned.lower() in text.lower():
                logging.debug(
                    f"Filtered banned string '{banned}' from transcription: {text}"
                )
                return None
        return text

    def ensure_conversation(self, channel_name: str) -> Conversation:
        """Get or create a conversation for a channel."""
        if channel_name not in self.conversations:
            self.conversations[channel_name] = Conversation(
                channel=channel_name,
            )
            logging.info(f"Created new conversation for {channel_name}")
        return self.conversations[channel_name]

    async def handle_transcription(self, channel_name: str, text: str):
        """Handle a voice transcription from the streamer."""
        filtered = self.filter_transcription(text)
        if filtered is None:
            return
        text = filtered

        conversation = self.ensure_conversation(channel_name)
        # TODO: apply aliases here — streamer's alias isn't reflected in voice transcriptions
        conversation.chatlog.append(f"[streamer voice] {channel_name}: {text}")
        logging.debug(f"Voice transcription added to {channel_name}: {text}")

        if "faebot" in text.lower():
            logging.info(
                f"faebot mentioned by streamer, boosting to chat frequency ({conversation.frequency})"
            )
            frequency = conversation.frequency
        else:
            frequency = conversation.voice_frequency
        if self.choose_to_reply(channel_name, frequency):
            asyncio.create_task(self.generate_response(channel_name))

    async def event_message(self, message):
        # Messages with echo set to True are messages sent by the bot...
        # For now we just want to ignore them...
        if message.echo:
            return

        logging.debug(f"received message: {message.author}: {message.content}")
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

        conversation = self.conversations[message.channel.name]
        if "faebot" in message.content.lower():
            logging.info(f"faebot mentioned by {display_name}, replying")
            frequency = 1.0
        else:
            frequency = conversation.frequency
        if self.choose_to_reply(message.channel.name, frequency):
            return asyncio.create_task(self.generate_response(message.channel.name))

    def choose_to_reply(self, channel_name: str, frequency: float) -> bool:
        """Determine whether faebot replies based on frequency. Callers compute the effective frequency."""
        conversation = self.conversations[channel_name]

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

    def permalog(self, log_message):
        with open("permalog.txt", "a") as permalog:
            permalog.write(log_message)

    async def generate_response(self, channel_name: str):
        """prompt the GenAI API for a message"""

        conversation = self.conversations[channel_name]
        channel = self.get_channel(channel_name)

        # Build system prompt with current channel info
        channel_info = await self.fetch_channel(channel_name)
        stream_title = channel_info.title if channel_info else "Unknown"
        game_name = channel_info.game_name if channel_info else "Unknown"

        system_prompt = (
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
            f"Emotes I can use: {self.emotes}. My favourite is transf23Botlove since it's literally a picture of me hugging a cyber-heart! I'm also transf23Yay transf23Generating"
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
        self.permalog(
            f"generating message in channel {channel_name}'s channel at {current_time}\n"
        )
        self.permalog(
            f"generating with parameters: \nTemperature:{params['temperature']}\nTop_k:{params['top_k']} \ntop_p: {params['top_p']}\nSeed: {params['seed']}\n"
        )

        try:
            response = await self.generate(
                model=conversation.model,
                prompt=prompt,
                system_prompt=system_prompt,
                params=params,
            )
            response = self.fix_emote_spacing(response)
            logging.info(f"received response: {response}")
            if len(response) > 499:
                logging.debug("generated content exceeded 500 characters, trimming.")
                response = response[:499] + "–"
            self.permalog(
                f"generated message:{response}\n------------------------------------------------------------\n\n"
            )
            await channel.send(response)

        except Exception as e:
            logging.error(
                f"Unknown error has occured, please contact the administrator. Error: {e}"
            )
            response = (
                "Oops, something strange has happened. Please let the developer know!"
            )
            await channel.send(response)

        conversation.chatlog.append(f"faebot: {response}")
        return

    async def generate(
        self,
        prompt: str = "",
        model=MODEL,
        system_prompt="",
        params=None,
    ) -> str:
        """generates completions with the OpenRouter API"""

        if params is None:
            params = {"top_k": 75, "top_p": 1, "temperature": 0.7, "seed": 666}

        if not self.session:
            self.session = aiohttp.ClientSession()

        # Create a proper message structure for OpenRouter
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        max_retries = 3
        for attempt in range(max_retries):
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
                    # Retry on transient HTTP errors (429 rate limit, 5xx server errors)
                    if response.status == 429 or response.status >= 500:
                        retry_after = min(2 ** attempt, 8)
                        logging.warning(
                            f"OpenRouter returned {response.status}, "
                            f"retrying in {retry_after}s (attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    # Non-retryable HTTP error (auth failure, bad request, etc.)
                    if response.status >= 400:
                        body = await response.text()
                        logging.error(
                            f"OpenRouter returned {response.status}: {body}"
                        )
                        return "I couldn't generate a response. Please try again."

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

            except (aiohttp.ClientError, ValueError, asyncio.TimeoutError) as e:
                retry_after = min(2 ** attempt, 8)
                logging.warning(
                    f"Network/parse error calling OpenRouter: {type(e).__name__}: {e}, "
                    f"retrying in {retry_after}s (attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(retry_after)
                continue

        # All retries exhausted
        logging.error(f"OpenRouter API call failed after {max_retries} attempts")
        raise Exception(f"OpenRouter API call failed after {max_retries} attempts")

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
        """check or change message frequency in this channel.
        Usage: fb;freq [chat_freq] [voice_freq]
        Frequency is 0-1 (e.g. 0.1 = 10% chance to reply)"""
        arguments = ctx.message.content.split(" ")
        conversation = self.conversations[ctx.channel.name]
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
        """display the current system prompt (auto-generated each response from channel info)"""
        # TODO: Phase 6 — allow mods to set a persistent custom system prompt
        return await ctx.send(
            "The system prompt is auto-generated each reply from current channel info (game, title, emotes). "
            "A custom prompt override is planned for a future update."
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
    async def join(self, ctx: commands.Context, user: str | None) -> None:
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
        logging.error("TWITCH_TOKEN not set. Did you forget to source secrets?\n")
    else:
        bot = Faebot()
        bot.run()
