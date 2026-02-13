"""
Local entry point for running faebot with voice integration.
Runs both the Twitch bot and the FastAPI dashboard/transcription server
in a single async process so they can share state.
"""

import asyncio
import logging
import uvicorn
from faebot import Faebot
from server import create_app

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def main():
    bot = Faebot()
    app = create_app(bot=bot)

    # Configure uvicorn to run without blocking
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)

    # Run both the Twitch bot and the FastAPI server concurrently
    await asyncio.gather(
        bot.start(),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
