from twitchio import Message, InvalidContent
from twitchio.ext import commands
import os
import logging
import replicate
from random import randrange
from dataclasses import dataclass, field


TWITCH_TOKEN = os.getenv("TWITCH_TOKEN", "")
INITIAL_CHANNELS = os.getenv("INITIAL_CHANNELS","transfaeries,faebot_01").split(",")
MODEL = os.getenv("MODEL", "meta/llama-2-13b-chat" )
ADMIN = os.getenv("ADMIN", "transfaeries").split(",")



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
    history: int = 5
    model: str = MODEL


class Faebot(commands.Bot):
    def __init__(self):
        # Initialise our Bot with our access token, prefix and a list of channels to join on boot...
        self.conversations: dict[str, Conversation] = {}
        super().__init__(
            token=TWITCH_TOKEN, prefix="fb;", initial_channels=INITIAL_CHANNELS
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
                system_prompt=(f"You are an AI chatbot called faebot. \n"
                                f"You are hanging out in {message.channel.name}'s chat on twitch where you enjoy talking with chatters about whatever the streamer, {message.channel.name}, is doing. You don't ask a lot of questions. \n"
                                "You always make sure your messages are below the twitch character limit which is 500 characters. You prioritize replying to the last message and you never ask followup questions.")
            )
            logging.info(
                f"added new conversation to Conversations. {self.conversations[message.channel.name].channel}"
            )
        


        ##command, execute command if appropriate otherwise return out
        if message.content.startswith('!') or message.content.startswith('fb;'):
            return await self.handle_commands(message)
        
        ## log message
        self.conversations[message.channel.name].chatlog.append(f"{message.author.name}: {message.content}")

        if self.conversations[message.channel.name].frequency<1:
            chance =1
        else:
            chance = randrange(self.conversations[message.channel.name].frequency)
        if "faebot" in message.content or chance == 0:
            logging.info(f"rolled a {chance} generating!")
            return await self.generate_response(message)
        
        else:
            logging.info(f"rolled a {chance}, not generating!")

    async def generate_response(self, message):
        """prompt the GenAI API for a message"""


        if len(self.conversations[message.channel.name].chatlog)>self.conversations[message.channel.name].history:
            logging.info(f"message history has exceeded the set history length of {self.conversations[message.channel.name].history}")
            self.conversations[message.channel.name].chatlog = self.conversations[message.channel.name].chatlog[len(self.conversations[message.channel.name].chatlog)-20:]


        prompt = (
            "\n".join(self.conversations[message.channel.name].chatlog)
            +"\nfaebot:"
        )
        logging.info(f"model: {self.conversations[message.channel.name].model}\nsystem_prompt: \n{self.conversations[message.channel.name].system_prompt}\nprompt: \n{prompt}")
        response = await self.generate(
            model=self.conversations[message.channel.name].model,
            prompt=prompt,
            author=message.author.display_name,
            system_prompt=self.conversations[message.channel.name].system_prompt,
        )
        logging.info(f"received response: {response}")
        
        try:
            await message.channel.send(response)
        except InvalidContent:
            logging.info("generated content exceeded 500 characters, trimming and posting.")
            response = response[0:499]+"–"
            await message.channel.send(response)
        except: 
            logging.info("Unknown error has occured, please contact the administrator.")
            response = "Oops, something strange has happened. Please let the transfaeries know!"
            await message.channel.send(response)
        
        self.conversations[message.channel.name].chatlog.append(f"faebot: {response}")
        return
        

    ## commands for everyone ##
    @commands.command()
    async def hello(self, ctx: commands.Context):
        """say hello get said hello back"""
        await ctx.send(f"Hello {ctx.author.name}!")

    @commands.command()
    async def ping(self, ctx: commands.Context):
        """ping the bot"""
        message = ctx.message.content
        message_tokens = message.split(" ")
        await ctx.send(f'pong {" ".join(message_tokens[1:])}')


    @commands.command()
    async def help(self, ctx: commands.Context):
        """display the help message"""
        await ctx.reply(f"Hello, my name is faebot, I'm an AI chatbot developed by the transfaeries. If you'd like to add faebot to your channel message transfaeries . I'll chime in on the chat and reply every so often, and I'll always reply to messages with my name on them. For mod commands use 'fb;mods'")

    @commands.command()
    async def mods(self, ctx: commands.Context):
        """display the mods command message"""
        await ctx.reply(f"If you're a mod in a channel I'm in, you can edit how frequently I respond to messages with the 'fb;freq' command. Set it to 1 to have me always reply, set it to 0 to have me never reply. I'll still reply to messages with my name on them and commands")

    ## commands for mods ##
    @commands.command()
    async def freq(self, ctx: commands.Context):
        """check or change message frequency in this channel"""
        if not ctx.author.is_mod:
            return await ctx.send(f'sorry you need to be a mod to use that command')\
       
        arguments = ctx.message.content.split(" ")
        if len(arguments)>1:
            if str(arguments[1]).isdigit():
                self.conversations[ctx.channel.name].frequency=int(arguments[1])
                return await ctx.send(f"changed message frequency in this channel to {self.conversations[ctx.channel.name].frequency}")
        
        return await ctx.send(f"current message frequency in this channel is {self.conversations[ctx.channel.name].frequency}")
    
    @commands.command()
    async def hist(self, ctx: commands.Context):
        """check or change message history length in the channel"""
        if not ctx.author.is_mod:
            return await ctx.send(f'sorry you need to be a mod to use that command')\
       
        arguments = ctx.message.content.split(" ")
        if len(arguments)>1:
            if str(arguments[1]).isdigit():
                self.conversations[ctx.channel.name].history=int(arguments[1])
                return await ctx.send(f"changed message history length in this channel to {self.conversations[ctx.channel.name].history}")
        
        return await ctx.send(f"current message history length in this channel is {self.conversations[ctx.channel.name].history}")
    
    @commands.command()
    async def prompt(self, ctx: commands.Context):
        """check or change the system prompt in the channel"""
        if not ctx.author.is_mod:
            return await ctx.send(f'sorry you need to be a mod to use that command')\
       
        arguments = ctx.message.content.split(" ")
        if len(arguments)>1:
            self.conversations[ctx.channel.name].system_prompt=' '.join(arguments[1:])
            return await ctx.send(f"changed system prompt in this channel to {self.conversations[ctx.channel.name].system_prompt}")
        
        return await ctx.send(f"current system_prompt in this channel is {self.conversations[ctx.channel.name].system_prompt}")
    
    @commands.command()
    async def clear(self, ctx: commands.Context):
        """check or change the system prompt in the channel"""
        if not ctx.author.is_mod:
            return await ctx.send(f'sorry you need to be a mod to use that command')\
       
        self.conversations[ctx.channel.name].chatlog = []
        return await ctx.reply("message history has been cleared. faebot has forgotten")
    
    ### commands for admins ###
    @commands.command()
    async def model(self, ctx: commands.Context):
        """check or change the system prompt in the channel"""
        if not ctx.author.name in ADMIN:
            return await ctx.send(f'sorry you need to be an admin to use that command')\
       
        arguments = ctx.message.content.split(" ")
        if len(arguments)>1:
            self.conversations[ctx.channel.name].model=' '.join(arguments[1:])
            return await ctx.send(f"changed model in this channel to {self.conversations[ctx.channel.name].model}")
        
        return await ctx.send(f"current model in this channel is {self.conversations[ctx.channel.name].model}")
        


    async def generate(
        self,
        prompt: str = "",
        author="",
        model=MODEL,
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
                "max_new_tokens": 150,
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
