"""
Local entry point — the body, the ear, or both in one process.

    python local.py              both (the ear posts to the body over loopback)
    python local.py --role body  the Twitch bot + the dashboard (no GPU needed)
    python local.py --role ear   the mic page + VAD + Whisper (see ear.py)

"Both" is one machine doing everything, the way it always has — but the ear
still reaches the body through its `/hear` door, so the one-machine shape and
the two-machine shape run the same code.
"""

import argparse
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


def parse_role(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role", choices=("both", "body", "ear"), default="both", help="what to run"
    )
    return parser.parse_args(argv).role


async def main(role: str = "both"):
    run_body = role in ("both", "body")
    run_ear = role in ("both", "ear")

    services = []
    bot = None
    ear_app = None

    if run_body:
        from twitchio.errors import AuthenticationError  # noqa: F401
        from bot import Faebot
        from server import create_app, BODY_PORT

        # Check for required env vars before anything heavy
        if not os.getenv("TWITCH_TOKEN"):
            logging.error("TWITCH_TOKEN not set. Did you forget to source secrets?\n")
            return

        # Shared event queue: core.generate_response writes generation events,
        # server.py's /ws/events drains them to connected dashboards.
        events: asyncio.Queue = asyncio.Queue(maxsize=256)
        bot = Faebot(event_queue=events)
        body_app = create_app(bot=bot, events=events)
        body_server = uvicorn.Server(
            uvicorn.Config(body_app, host="0.0.0.0", port=BODY_PORT, log_level="info")
        )
        services.append(body_server)

    if run_ear:
        from ear import create_ear_app, EAR_PORT

        ear_app = create_ear_app()
        ear_server = uvicorn.Server(
            uvicorn.Config(ear_app, host="0.0.0.0", port=EAR_PORT, log_level="info")
        )
        services.append(ear_server)

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

        if ear_app is not None:
            logging.info("Shutting down Whisper executor...")
            whisper_state = getattr(ear_app.state, "whisper", None)
            if whisper_state:
                whisper_state["executor"].shutdown(wait=False)
        logging.info("Stopping uvicorn...")
        for service in services:
            service.should_exit = True
        if bot is not None:
            logging.info("Closing bot...")
            await bot.close()

        force_timer.cancel()

    runners = [service.serve() for service in services]
    if bot is not None:
        runners.append(bot.start())
    runners.append(_shutdown_watcher())

    try:
        await asyncio.gather(*runners)
    except Exception as error:
        # TwitchIO's AuthenticationError lives behind the body import; name it
        # by type so the ear-only role never needs twitchio.
        if type(error).__name__ == "AuthenticationError":
            logging.error("Twitch authentication failed. Your token may be expired.\n")
        else:
            raise
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main(parse_role()))
    except KeyboardInterrupt:
        pass
