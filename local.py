"""
Local entry point for running faebot with voice integration.
Runs both the Twitch bot and the FastAPI dashboard/transcription server
in a single async process so they can share state.
"""

import asyncio
import logging
import os
import signal
import threading
import uvicorn

# Configure logging BEFORE importing bot/server — their module-level
# basicConfig calls are no-ops once a handler exists
_env = os.getenv("ENVIRONMENT", "dev").lower()
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.DEBUG if _env != "prod" else logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

logging.getLogger("torio").setLevel(logging.WARNING)  # suppress FFmpeg probe noise

from twitchio.errors import AuthenticationError  # noqa: E402
import core  # noqa: E402
from bot import Faebot  # noqa: E402
from server import create_app  # noqa: E402


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

    # Graceful shutdown: intercept signals before asyncio cancels tasks
    shutdown_event = asyncio.Event()

    def _signal_handler():
        if not shutdown_event.is_set():
            logging.info("Shutdown signal received, cleaning up...")
            shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    async def _shutdown_watcher():
        """Wait for shutdown signal, then stop services in order."""
        await shutdown_event.wait()

        # Force exit if graceful shutdown takes too long (stuck CUDA threads)
        def _force_exit():
            logging.warning("Graceful shutdown timed out — forcing exit")
            os._exit(1)

        force_timer = threading.Timer(10, _force_exit)
        force_timer.daemon = True
        force_timer.start()

        logging.info("Shutting down Whisper executor...")
        whisper_state = getattr(app.state, "whisper", None)
        if whisper_state:
            whisper_state["executor"].shutdown(wait=False)
        logging.info("Stopping uvicorn...")
        server.should_exit = True
        logging.info("Closing bot...")
        await bot.close()

        force_timer.cancel()

    try:
        await asyncio.gather(
            bot.start(),
            server.serve(),
            _shutdown_watcher(),
        )
    except AuthenticationError:
        logging.error("Twitch authentication failed. Your token may be expired.\n")
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
