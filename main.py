"""
Main entry point for Faebot with dashboard and voice integration.

This module runs both the FastAPI dashboard server and the Twitch bot
in the same asyncio event loop.

Usage:
    poetry run python main.py
"""

import asyncio
import logging
import os
import signal
import sys

import uvicorn
from uvicorn import Config, Server

from faebot import Faebot, TWITCH_TOKEN, INITIAL_CHANNELS
from server import server, app

# Configure logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# Server settings
HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
PORT = int(os.getenv("DASHBOARD_PORT", "8000"))


class FaebotWithVoice(Faebot):
    """Extended Faebot with voice transcription support."""

    def __init__(self):
        super().__init__()
        self._voice_enabled = True

    def handle_transcription(self, text: str, source: str = "voice"):
        """Handle incoming voice transcription.

        This is called by the server when a transcription is ready.
        We inject it into the conversation like a chat message.
        """
        if not text.strip():
            return

        logger.info(f"Voice transcription received: {text}")

        # For now, log it. Next step: inject into active conversations
        # We'll need to decide which channel(s) to inject into
        # Options:
        # 1. Inject into all active conversations
        # 2. Inject only into a designated "streaming" channel
        # 3. Make it configurable per-channel

        # TODO: Implement injection into conversation context
        # For each active conversation:
        #   self.conversations[channel].chatlog.append(f"[Voice] Streamer: {text}")


async def run_dashboard(host: str, port: int):
    """Run the FastAPI dashboard server."""
    config = Config(app=app, host=host, port=port, log_level="info")
    server_instance = Server(config)

    logger.info(f"Starting dashboard server on http://{host}:{port}")
    await server_instance.serve()


async def run_bot(bot: FaebotWithVoice):
    """Run the Twitch bot."""
    logger.info(f"Starting Twitch bot, joining channels: {INITIAL_CHANNELS}")

    # The bot's run() method is blocking, so we use start() instead
    # which is the async version
    await bot.start()


async def main():
    """Run both the dashboard and bot concurrently."""

    # Create the bot instance
    bot = FaebotWithVoice()

    # Connect the transcription callback
    server.set_transcription_callback(bot.handle_transcription)

    # Set up graceful shutdown
    shutdown_event = asyncio.Event()

    def handle_shutdown(sig):
        logger.info(f"Received {sig.name}, initiating shutdown...")
        shutdown_event.set()

    # Register signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_shutdown, sig)

    # Create tasks for both services
    dashboard_task = asyncio.create_task(run_dashboard(HOST, PORT))
    bot_task = asyncio.create_task(run_bot(bot))

    logger.info("=" * 50)
    logger.info("Faebot with Voice Integration")
    logger.info(f"Dashboard: http://localhost:{PORT}")
    logger.info(f"Twitch channels: {INITIAL_CHANNELS}")
    logger.info("=" * 50)

    try:
        # Wait for either task to complete (or shutdown signal)
        done, pending = await asyncio.wait(
            [dashboard_task, bot_task], return_when=asyncio.FIRST_COMPLETED
        )

        # If one task finished, cancel the other
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except asyncio.CancelledError:
        logger.info("Main task cancelled")
    finally:
        # Cleanup
        await bot.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
