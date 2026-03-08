"""
Local entry point for running faebot with voice integration.
Runs both the Twitch bot and the FastAPI dashboard/transcription server
in a single async process so they can share state.
"""

import asyncio
import logging
import os
import uvicorn
from twitchio.errors import AuthenticationError
from faebot import Faebot
from server import create_app

_env = os.getenv("ENVIRONMENT", "dev").lower()
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.DEBUG if _env != "prod" else logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def main():
    # Check for required env vars before loading heavy models
    if not os.getenv("TWITCH_TOKEN"):
        logging.error("TWITCH_TOKEN not set. Did you forget to source secrets?\n")
        return

    bot = Faebot()
    app = create_app(bot=bot)

    # Configure uvicorn to run without blocking
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)

    try:
        # Run both the Twitch bot and the FastAPI server concurrently
        await asyncio.gather(
            bot.start(),
            server.serve(),
        )
    except AuthenticationError:
        logging.error("Twitch authentication failed. Your token may be expired.\n")
    except asyncio.CancelledError:
        pass
    finally:
        await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Shutting down faebot.")
