from types import coroutine
from typing import Optional
from twitchio import Message, InvalidContent
from twitchio.ext import commands
import json
import twitchio
import os
import logging
import replicate
import asyncio
import datetime
from random import randrange
from dataclasses import dataclass, field
from functools import wraps


TWITCH_TOKEN = os.getenv("TWITCH_TOKEN", "")
INITIAL_CHANNELS = os.getenv("INITIAL_CHANNELS", "").split(",")
INITIAL_MODEL_LIST = os.getenv("MODEL", "meta/llama-2-13b-chat,meta/llama-2-70b").split(
    ","
)
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
    current_model: str
    chatlog: list = field(default_factory=list)  # dict[int, Message]
    conversants: list = field(default_factory=list)
    system_prompt: str = ""
    frequency: int = 0
    history: int = 5
    silenced: bool = False


class Faebot(commands.Bot):
    def __init__(self):
        # Initialise our Bot with our access token, prefix and a list of channels to join on boot...
        self.conversations: dict[str, Conversation] = {}
        self.model_list = INITIAL_MODEL_LIST
        super().__init__(
            token=TWITCH_TOKEN,
            prefix=["fb;", "fae;"],
            initial_channels=INITIAL_CHANNELS,
        )

    async def event_ready(self):
        # We are logged in and ready to chat and use commands...
        logging.info(f"Logged in as | {self.nick}")
        logging.info(f"User id is | {self.user_id}")
        logging.info(f"Joined channels {INITIAL_CHANNELS}")

    async def event_message(self, message: Message):
        # Messages with echo set to True are messages sent by the bot...
        # For now we just want to ignore them...
        if message.echo:
            return

        # Print the contents of our message to console...
        logging.info(f"received message: {message.author}: {message.content}")
        logging.info(f"channel object {message.channel.name}")
        if message.channel.name not in self.conversations:
            self.conversations[message.channel.name] = Conversation(
                channel=message.channel.name,
                system_prompt=(
                    f"You are an AI chatbot called faebot. \n"
                    f"You are hanging out in {message.channel.name}'s chat on twitch where you enjoy talking with chatters about whatever the streamer, {message.channel.name}, is doing.  \n"
                    "You always make sure your messages are below the twitch character limit which is 500 characters. You prioritize replying to the last message and you never ask followup questions."
                ),
                current_model=self.model_list[0],
            )
            logging.info(
                f"added new conversation to Conversations. {self.conversations[message.channel.name].channel}"
            )

        ##command, execute command if appropriate otherwise return out
        # TODO: change if statement to use prefixes directly
        if (
            message.content.startswith("!")
            or message.content.startswith("fb;")
            or message.content.startswith("fae;")
        ):
            return await self.handle_commands(message)

        ## log message
        self.conversations[message.channel.name].chatlog.append(
            f"{message.author.name}: {message.content}"
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

        if "faebot" in message.content:
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

    def jsonlog(self, log_message: dict):
        if os.path.exists("chatlog.json") and os.path.getsize("chatlog.json")>0:
            with open("chatlog.json", "r") as jsonfile:
                json_memory = json.load(jsonfile)
        else:
            json_memory = {"messages": []}
        messages: list = json_memory["messages"]
        messages.append(log_message)
        json_memory["messages"] = messages
        with open("chatlog.json", "w") as jsonlog:
            json.dump(json_memory, jsonlog, indent=4)

    async def generate_response(self, message):
        """prompt the GenAI API for a message"""

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
            f"model: {self.conversations[message.channel.name].current_model}\nsystem_prompt: \n{self.conversations[message.channel.name].system_prompt}\nprompt: \n{prompt}"
        )

        params = {
            "temperature": randrange(75, 150) / 100,
            "top_p": randrange(5, 11) / 10,
            "top_k": randrange(1, 1024),
            "seed": randrange(1, 1024),
        }
        current_time = datetime.datetime.now()
        current_time = str(current_time.isoformat())

        logging.info(
            f"generating with parameters: \nTemperature:{params['temperature']}\nTop_k:{params['top_k']} \ntop_p: {params['top_p']}\nseed: {params['seed']}"
        )

        self.permalog(
            f"generating message in channel {message.channel.name}'s channel at {current_time}\n"
        )
        self.permalog(
            f"generating with parameters: \nTemperature:{params['temperature']}\nTop_k:{params['top_k']} \ntop_p: {params['top_p']}\nSeed: {params['seed']}\n"
        )

        try:
            response = await self.generate(
                model=self.conversations[message.channel.name].current_model,
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

        except:
            logging.info("Unknown error has occured, please contact the administrator.")
            response = (
                "Oops, something strange has happened. Please let the developer know!"
            )
            await message.channel.send(response)

        self.conversations[message.channel.name].chatlog.append(f"faebot: {response}")
        to_log = {
            "timestamp": current_time,
            "channel": str(message.channel.name),
            "message_content": response,
            "params": params,
        }
        self.jsonlog(to_log)

        return

    async def generate(
        self,
        prompt: str = "",
        author="",
        model=INITIAL_MODEL_LIST[0],
        system_prompt="",
        params={"top_k": 75, "top_p": 1, "temperature": 0.7, "seed": 666},
    ) -> str:
        """generates completions with the replicate api"""

        output = await replicate.async_run(
            model,
            input={
                "debug": False,
                "top_k": params["top_k"],
                "top_p": params["top_p"],
                "prompt": prompt,
                "temperature": params["temperature"],
                "system_prompt": system_prompt,
                "max_new_tokens": 150,
                "min_new_tokens": -1,
                "seed": params["seed"],
            },
        )
        response = "".join(output)
        return response

    ## commands for everyone ##

    @commands.command()
    async def hello(self, ctx: commands.Context):
        """display the help message"""
        await ctx.reply(
            "Hello, my name is faebot, I'm an AI chatbot developed by the transfaeries. I'll chime in on the chat and reply every so often, and I'll always reply to messages with my name on them. For mod commands use 'fb;mods'"
        )

    @commands.command()
    async def help(self, ctx: commands.Context):
        """display the help message"""
        await ctx.reply(
            "Hello, my name is faebot, I'm an AI chatbot developed by the transfaeries. I'll chime in on the chat and reply every so often, and I'll always reply to messages with my name on them. For mod commands use 'fb;mods'"
        )

    @commands.command()
    async def invite(self, ctx: commands.Context) -> coroutine:
        """Invite Faebot to your channel"""
        await ctx.reply(
            "Thanks for the invitation, but you should ask the transfaeries first. Send faer a whisper!"
        )

    @commands.command()
    async def mods(self, ctx: commands.Context):
        """display the mods command message"""
        await ctx.reply(
            "Here are the commands mods can use with faebot. | fb;freq to set the frequency of responses. | fb;hist to set message history length.| fb;silence to silence faebot entirely. | fb;clear to clear faebot's memory. | fb;part to have faebot leave the channel."
        )

    @commands.command()
    async def ping(self, ctx: commands.Context):
        """ping the bot"""
        message = ctx.message.content
        message_tokens = message.split(" ")
        await ctx.reply(f'pong {" ".join(message_tokens[1:])}')

    ## commands for mods ##

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

    @commands.command()
    @requires_mod
    async def switch(self, ctx: commands.Context):
        """switch faebot's model"""

        current_index = self.model_list.index(
            self.conversations[ctx.channel.name].current_model
        )
        next_index = current_index + 1
        if next_index == len(self.model_list):
            next_index = 0

        self.conversations[ctx.channel.name].current_model = self.model_list[next_index]
        logging.info(
            f"model changed to {next_index}: {self.conversations[ctx.channel.name].current_model}"
        )
        return await ctx.send(
            f"model changed to {next_index}: {self.conversations[ctx.channel.name].current_model}"
        )

    ### commands for admins ###

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
            self.conversations[ctx.channel.name].current_model = " ".join(arguments[1:])
            return await ctx.send(
                f"changed model in this channel to {self.conversations[ctx.channel.name].current_model}"
            )

        return await ctx.send(
            f"current model in this channel is {self.conversations[ctx.channel.name].current_model}"
        )


bot = Faebot()
bot.run()
