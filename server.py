"""
The body's server: the events dashboard and the door the ear speaks through.

Two endpoints that matter. `/ws/events` broadcasts generation events to
connected dashboards (a ring buffer replays on connect). `/hear` receives one
transcription from the ear (ear.py) and hands it to the bot — what used to be
an in-process method call is now a small HTTP wire, so the ear can live on
the machine with the GPU and the body on the machine with the diary.

No ML models load here; the body is light.
"""

from pathlib import Path
from collections import OrderedDict, deque
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from os import getenv
import asyncio
import logging
import uvicorn

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

# The ear's shared secret. Unset = any caller on the network the body listens
# on may speak into it; fine on one machine or a tailnet, said loudly at start.
EAR_TOKEN = getenv("EAR_TOKEN", "")
STREAMER_CHANNEL = getenv("STREAMER_CHANNEL", "transfaeries")
BODY_PORT = int(getenv("BODY_PORT", "8000"))
# Where the body listens. Every interface by default; on a machine with other
# networks, bind the one the ear reaches it by (a tailnet address) — the
# dashboard shows prompts, and prompts carry chat.
BODY_HOST = getenv("BODY_HOST", "0.0.0.0")
# How many utterance ids the body remembers for de-duplication. A stream is a
# few thousand lines; a repeat arrives within seconds to minutes (a retry, or
# the spool draining), so this is far more than needed.
HEARD_IDS_KEPT = int(getenv("HEARD_IDS_KEPT", "4096"))


def create_app(bot=None, events: asyncio.Queue | None = None):
    """Create the body's FastAPI app, optionally with a reference to the Twitch bot.

    `events` is the shared generation event queue; `/ws/events` drains it and
    broadcasts to connected dashboards.
    """
    app = FastAPI()
    app.state.bot = bot
    app.state.events = events
    # The body's memory of utterance ids it has taken, newest last, each with
    # its answer (a future while the line is still being handled) — so a
    # repeat, even one that arrives while the first is in flight, gets the
    # same answer and is never heard twice. Bounded; a body restart forgets,
    # which is the one repeat this can't catch. It lives in this process:
    # run the body as one process, or a repeat landing on a sibling worker
    # would be heard again.
    heard_ids: OrderedDict[str, asyncio.Future] = OrderedDict()
    app.state.heard_ids = heard_ids

    # Dashboard event plumbing: a single drain task pulls from the generation
    # queue into a ring buffer and fans out to connected /ws/events clients.
    # The drain runs regardless of whether any clients are connected — the bot
    # must behave identically whether anyone is watching.
    event_clients: set[WebSocket] = set()
    event_history: deque = deque(maxlen=50)
    app.state.event_clients = event_clients
    app.state.event_history = event_history

    async def _drain_events() -> None:
        assert events is not None
        while True:
            try:
                event = await events.get()
            except asyncio.CancelledError:
                return
            event_history.append(event)
            for ws in list(event_clients):
                try:
                    await ws.send_json(event)
                except Exception as e:
                    logging.debug(f"Dropping dead event client: {e}")
                    event_clients.discard(ws)

    if events is not None:

        @app.on_event("startup")
        async def _start_drain() -> None:
            app.state.drain_task = asyncio.create_task(_drain_events())
            logging.info("Event drain task started")

        @app.on_event("shutdown")
        async def _stop_drain() -> None:
            task = getattr(app.state, "drain_task", None)
            if task:
                task.cancel()

    if not EAR_TOKEN:
        logging.warning("EAR_TOKEN is not set — /hear accepts any caller")

    # Set up templates and static files
    BASE_DIR = Path(__file__).parent
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        """Render the dashboard page."""
        return templates.TemplateResponse("dashboard.html", {"request": request})

    @app.post("/hear")
    async def hear(request: Request) -> JSONResponse:
        """One utterance from the ear.

        The payload is what ear.py's `utterance()` builds — text plus
        Whisper's metadata plus `heard_at`. Everything the ear sends is handed
        to the bot; the bot decides whether faebot heard it (bot.py's
        filters) and answers with `heard` and, when not, `why` — the ear's
        page shows that answer beside the line.
        """
        if EAR_TOKEN and request.headers.get("X-Ear-Token") != EAR_TOKEN:
            return JSONResponse({"error": "wrong ear token"}, status_code=403)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "not json"}, status_code=400)
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            return JSONResponse({"error": "no text"}, status_code=400)
        if app.state.bot is None:
            return JSONResponse({"error": "no body listening"}, status_code=503)
        text = payload["text"]
        utterance_id = payload.get("utterance_id")
        if isinstance(utterance_id, str) and utterance_id:
            if utterance_id in heard_ids:
                answer = await asyncio.shield(heard_ids[utterance_id])
                logging.info(f"/hear: {utterance_id} again — answered once already")
                return JSONResponse({**answer, "repeat": True})
            heard_ids[utterance_id] = asyncio.get_running_loop().create_future()
            while len(heard_ids) > HEARD_IDS_KEPT:
                heard_ids.popitem(last=False)
        else:
            utterance_id = None
        whisper_meta = {
            key: payload.get(key)
            for key in (
                "language",
                "language_probability",
                "duration",
                "no_speech_prob",
                "heard_at",
                "whisper_prompt",
                "utterance_id",
            )
        }
        try:
            why = await app.state.bot.handle_transcription(
                STREAMER_CHANNEL, text, **whisper_meta
            )
        except BaseException as error:
            # A line that failed to be heard can be offered again: forget
            # its id, and tell anyone waiting on it.
            if utterance_id is not None:
                future = heard_ids.pop(utterance_id, None)
                if future is not None and not future.done():
                    future.set_exception(error)
                    future.exception()  # retrieved, so it isn't logged as lost
            raise
        answer = {"heard": why is None, "why": why}
        if utterance_id is not None:
            future = heard_ids.get(utterance_id)
            if future is not None and not future.done():
                future.set_result(answer)
        return JSONResponse(answer)

    @app.websocket("/ws/events")
    async def events_websocket(websocket: WebSocket) -> None:
        """Broadcast generation events to connected dashboards.

        On connect, replays the ring buffer so a refresh preserves context;
        after that, new events arrive live from the drain task.
        """
        await websocket.accept()
        logging.info("Events WebSocket connected")
        for event in list(event_history):
            try:
                await websocket.send_json(event)
            except Exception:
                return
        event_clients.add(websocket)
        try:
            while True:
                # We don't expect messages from the client — this just parks
                # the coroutine until the client disconnects.
                await websocket.receive_text()
        except Exception as e:
            logging.debug(f"Events WebSocket disconnected: {e}")
        finally:
            event_clients.discard(websocket)

    return app


if __name__ == "__main__":
    app = create_app()
    uvicorn.run(app, host=BODY_HOST, port=BODY_PORT)
