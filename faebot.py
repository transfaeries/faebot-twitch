from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.type import AuthScope, ChatEvent
from twitchAPI.chat import Chat, EventData, ChatMessage, ChatSub, ChatCommand
import asyncio
import os
import logging
import replicate
from dataclasses import dataclass

APP_ID = os.getenv("TWITCH_APP_ID", "")
APP_SECRET = os.getenv("TWITCH_TOKEN", "")
USER_SCOPE = [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT]
TARGET_CHANNEL = os.getenv("LIST_CHANNELS", ["transfaeries", "faebot_01"])

# set up logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


## define a dataclass to store conversations
@dataclass
class Conversation:
    """for storing conversations"""

    id: int
    channel: str
    chatlog: list[str]  # dict[int, Message]
    prompt: str


# this will be called when the event READY is triggered, which will be on bot start
async def on_ready(ready_event: EventData):
    logging.info("Bot is ready for work, joining channels")
    # join our target channel, if you want to join multiple, either call join for each individually
    # or even better pass a list of channels as the argument
    await ready_event.chat.join_room(TARGET_CHANNEL)
    # you can do other bot initialization things in here


# this will be called whenever a message in a channel was send by either the bot OR another user
async def on_message(msg: ChatMessage):
    logging.info(f"in {msg.room.name}, {msg.user.name} said: {msg.text}")


# this will be called whenever someone subscribes to a channel
async def on_sub(sub: ChatSub):
    print(
        f"New subscription in {sub.room.name}:\\n"
        f"  Type: {sub.sub_plan}\\n"
        f"  Message: {sub.sub_message}"
    )


# this will be called whenever the !reply command is issued
async def ping(cmd: ChatCommand):
    if len(cmd.parameter) == 0:
        await cmd.reply("pong")
    else:
        await cmd.reply(f"pong {cmd.parameter}")


# this is where we set up the bot
async def run():
    # set up twitch api instance and add user authentication with some scopes
    twitch = await Twitch(APP_ID, APP_SECRET)
    auth = UserAuthenticator(twitch, USER_SCOPE)
    token, refresh_token = await auth.authenticate()
    await twitch.set_user_authentication(token, USER_SCOPE, refresh_token)

    # create chat instance
    chat = await Chat(twitch)

    # register the handlers for the events you want

    # listen to when the bot is done starting up and ready to join channels
    chat.register_event(ChatEvent.READY, on_ready)
    # listen to chat messages
    chat.register_event(ChatEvent.MESSAGE, on_message)
    # listen to channel subscriptions
    chat.register_event(ChatEvent.SUB, on_sub)
    # there are more events, you can view them all in this documentation

    # you can directly register commands and their handlers, this will register the !reply command
    chat.register_command("ping", ping)

    # we are done with our setup, lets start this bot up!
    chat.start()

    # lets run till we press enter in the console
    try:
        input("press ENTER to stop\n")
    finally:
        # now we can close the chat bot and the twitch api client
        chat.stop()
        await twitch.close()


# lets run our setup
asyncio.run(run())
