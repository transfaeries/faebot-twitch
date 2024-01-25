from twitchio import Message
from twitchio.ext import commands
import os
import logging
import replicate
from random import randrange
from dataclasses import dataclass, field


TWITCH_TOKEN = os.getenv("TWITCH_TOKEN", "")
INITIAL_CHANNELS = os.getenv("INITIAL_CHANNELS", ["transfaeries", "faebot_01"])
AI_MODEL = os.getenv("MODEL", "meta/llama-2-7b-chat" )


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
    frequency: int = 5


class Faebot(commands.Bot):
    def __init__(self):
        # Initialise our Bot with our access token, prefix and a list of channels to join on boot...
        self.conversations: dict[str, Conversation] = {}

        super().__init__(
            token=TWITCH_TOKEN, prefix="!", initial_channels=INITIAL_CHANNELS
        )

    async def event_ready(self):
        # We are logged in and ready to chat and use commands...
        logging.info(f"Logged in as | {self.nick}")
        logging.info(f"User id is | {self.user_id}")

    async def event_message(self, message):
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
                system_prompt=f"You are an AI chatbot called faebot. You are hanging out in {message.channel.name}'s chat. This is the conversation log: \n",
            )
            logging.info(
                f"added new conversation to Conversations. {self.conversations[message.channel.name].channel}"
            )
        if message.content.startswith('!'):
            return await self.handle_commands(message)
        
        ## log message
        self.conversations[message.channel.name].chatlog.append(f"{message.author.name}: {message.content}")

        chance = randrange(self.conversations[message.channel.name].frequency)
        if "faebot" in message.content or chance == 0:
            logging.info(f"generating!")
            return await self.generate_response(message)
        
        else:
            logging.info(f"rolled a {chance}, not generating!")

    async def generate_response(self, message):
        """prompt the GenAI API for a message"""

        prompt = (
            "\n".join(self.conversations[message.channel.name].chatlog)
            + "\nfaebot:"
        )
        logging.info(f"system_prompt: {self.conversations[message.channel.name].system_prompt}, \n prompt: {prompt}")
        response = await self.generate(
            prompt=prompt,
            author=message.author.display_name,
            system_prompt=self.conversations[message.channel.name].system_prompt,
        )
        await message.channel.send(response)

    @commands.command()
    async def hello(self, ctx: commands.Context):
        # Send a hello back!
        await ctx.send(f"Hello {ctx.author.name}!")

    @commands.command()
    async def ping(self, ctx: commands.Context):
        # Send a hello back!
        message = ctx.message.content
        message_tokens = message.split(" ")
        await ctx.send(f'pong {" ".join(message_tokens[1:])}')

    @commands.command()
    async def setfreq(self, ctx: commands.Context):
        if not ctx.author.is_mod:
            return await ctx.send(f'sorry you need to be a mod to use that command')\
       
        arguments = ctx.message.content.split(" ")
        if len(arguments)>1:
            if str(arguments[1]).isdigit():
                self.conversations[ctx.channel.name].frequency=int(arguments[1])
                return await ctx.send(f"changed message frequency in this channel to {self.conversations[ctx.channel.name].frequency}")
        
        return await ctx.send(f"current frequency is {self.conversations[ctx.channel.name].frequency}")
    
        


    async def generate(
        self,
        prompt: str = "",
        author="",
        model=AI_MODEL,
        system_prompt="",
    ) -> str:
        """generates completions with the replicate api"""

        output = replicate.run(
            model,
            input={
                "debug": False,
                "top_k": 50,
                "top_p": 1,
                "prompt": prompt,
                "temperature": 0.7,
                "system_prompt": system_prompt,
                "max_new_tokens": 250,
                "min_new_tokens": -1,
            },
        )
        response = "".join(output)
        return response

    # # The meta/llama-2-13b-chat model can stream output as it's running.
    # for event in replicate.stream(
    #     "meta/llama-2-13b-chat",
    #     input={
    #         "debug": False,
    #         "top_k": 50,
    #         "top_p": 1,
    #         "prompt": "Write a story in the style of James Joyce. The story should be about a trip to the Irish countryside in 2083, to see the beautiful scenery and robots.",
    #         "temperature": 0.75,
    #         "system_prompt": "You are a helpful, respectful and honest assistant. Always answer as helpfully as possible, while being safe. Your answers should not include any harmful, unethical, racist, sexist, toxic, dangerous, or illegal content. Please ensure that your responses are socially unbiased and positive in nature.\n\nIf a question does not make any sense, or is not factually coherent, explain why instead of answering something not correct. If you don't know the answer to a question, please don't share false information.",
    #         "max_new_tokens": 500,
    #         "min_new_tokens": -1
    #     },
    # ):
    #     print(str(event), end="")


bot = Faebot()
bot.run()
