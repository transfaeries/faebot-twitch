"""
The ear — faebot's hearing, as a peripheral.

A small FastAPI app that serves the mic page, takes the browser's audio over a
WebSocket, cuts it into utterances with Silero VAD, transcribes each one with
faster-whisper, and POSTs the transcription (with Whisper's own metadata) to
the body's `/hear`. That is all it does. It knows nothing about Twitch, holds
no Twitch credentials, keeps no conversation, and filters nothing but silence:
eyes catch a lot of things that brains filter out, so what the ear hears goes
to the body whole and the body decides what faebot heard (bot.py).

The ear can live on a different machine from the body — it wants the GPU, the
body wants to be where the diary is — and it can lose the body for a while
without losing what it heard: a failed delivery is retried once and then
spooled to disk, and the spool is drained in order when the body is back
(BodyLink).
"""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from os import getenv
import aiohttp
import asyncio
import datetime
import json
import logging
import uvicorn
import numpy as np


WHISPER_TIMEOUT = int(getenv("WHISPER_TIMEOUT", "30"))

# Whisper is primed with our names so it spells them right. The body knows
# this prompt too (it is what a prompt echo is an echo OF), so it travels
# with every transcription rather than being assumed on both sides.
WHISPER_PROMPT = getenv("WHISPER_PROMPT", "faebot, transfaeries")

# Where the body is. On one machine the default loopback; across the tailnet,
# the body's tailscale address. The token is a shared secret in a header —
# the tailnet is the wall, the token is the latch.
BODY_URL = getenv("BODY_URL", "http://127.0.0.1:8000").rstrip("/")
EAR_TOKEN = getenv("EAR_TOKEN", "")
EAR_PORT = int(getenv("EAR_PORT", "8001"))
EAR_SPOOL = Path(getenv("EAR_SPOOL", "ear-spool.jsonl"))
DELIVERY_TIMEOUT = float(getenv("EAR_DELIVERY_TIMEOUT", "5"))
SPOOL_DRAIN_INTERVAL = float(getenv("EAR_SPOOL_DRAIN_INTERVAL", "30"))


def utterance(
    text: str,
    language: str | None,
    language_probability: float | None,
    duration: float,
    no_speech_prob: float | None,
) -> dict:
    """One heard thing, as the body receives it. `heard_at` is the ear's clock
    at transcription time, so a line delivered late from the spool still says
    when it was said."""
    return {
        "text": text,
        "language": language,
        "language_probability": language_probability,
        "duration": duration,
        "no_speech_prob": no_speech_prob,
        "heard_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


class BodyLink:
    """Delivers utterances to the body, in order, and never loses one.

    One POST per utterance, awaited from the audio loop so order is kept the
    way the old in-process call kept it. A delivery that fails (body down,
    tailnet hiccup) is retried once after a beat; if that fails too the
    utterance is appended to the spool file, and a background task tries the
    spool again every so often, oldest first, stopping at the first failure
    so order survives the outage as well.
    """

    def __init__(
        self,
        body_url: str = BODY_URL,
        token: str = EAR_TOKEN,
        spool: Path = EAR_SPOOL,
        timeout: float = DELIVERY_TIMEOUT,
    ):
        self.body_url = body_url
        self.token = token
        self.spool = spool
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Ear-Token"] = self.token
        return headers

    async def _post(self, payload: dict) -> dict:
        """One attempt. Raises on any failure to deliver."""
        session = await self.session()
        async with session.post(
            f"{self.body_url}/hear",
            json=payload,
            headers=self._headers(),
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"body answered {response.status}")
            return await response.json()

    async def deliver(self, payload: dict) -> dict | None:
        """Deliver now if we can; spool if we can't. Returns the body's
        answer (what it made of the line) or None when spooled."""
        async with self._lock:
            # Anything already spooled goes first, so the body hears things
            # in the order they were said.
            if self.spool.exists() and not await self._drain_locked():
                self._spool(payload)
                return None
            for attempt in (1, 2):
                try:
                    return await self._post(payload)
                except Exception as error:
                    logging.warning(
                        f"delivery to the body failed (attempt {attempt}): "
                        f"{type(error).__name__}: {error}"
                    )
                    if attempt == 1:
                        await asyncio.sleep(1)
            self._spool(payload)
            return None

    def _spool(self, payload: dict) -> None:
        try:
            with open(self.spool, "a", encoding="utf-8") as spool_file:
                spool_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
            logging.warning(f"spooled an utterance to {self.spool}")
        except Exception as error:
            logging.error(f"could not spool an utterance: {error}")

    async def drain(self) -> bool:
        """Try to deliver everything spooled, oldest first. True if the spool
        is empty afterwards."""
        async with self._lock:
            return await self._drain_locked()

    async def _drain_locked(self) -> bool:
        if not self.spool.exists():
            return True
        try:
            lines = [
                line
                for line in self.spool.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except Exception as error:
            logging.error(f"could not read the spool: {error}")
            return False
        delivered = 0
        for line in lines:
            try:
                await self._post(json.loads(line))
            except Exception as error:
                logging.debug(f"spool drain stopped: {type(error).__name__}: {error}")
                break
            delivered += 1
        remaining = lines[delivered:]
        try:
            if remaining:
                self.spool.write_text("\n".join(remaining) + "\n", encoding="utf-8")
            else:
                self.spool.unlink()
        except Exception as error:
            logging.error(f"could not rewrite the spool: {error}")
            return False
        if delivered:
            logging.info(
                f"drained {delivered} spooled utterance(s); {len(remaining)} left"
            )
        return not remaining

    async def drain_forever(self, interval: float = SPOOL_DRAIN_INTERVAL) -> None:
        while True:
            try:
                await asyncio.sleep(interval)
                await self.drain()
            except asyncio.CancelledError:
                return
            except Exception as error:
                logging.debug(f"spool drain error: {error}")


def create_ear_app(link: BodyLink | None = None) -> FastAPI:
    """The ear's FastAPI app: the mic page and the audio WebSocket.

    Loads the VAD and Whisper models at creation — this is the heavy process,
    the one that wants the GPU.
    """
    # Imported here, not at module top: the ear's models are the one thing in
    # this repo that needs torch + CUDA, and the body must import nothing of it.
    from silero_vad import load_silero_vad, VADIterator
    from faster_whisper import WhisperModel
    import torch

    app = FastAPI()
    link = link or BodyLink()
    app.state.link = link

    @app.on_event("startup")
    async def _start_drain() -> None:
        app.state.drain_task = asyncio.create_task(link.drain_forever())

    @app.on_event("shutdown")
    async def _stop_drain() -> None:
        task = getattr(app.state, "drain_task", None)
        if task:
            task.cancel()
        await link.close()

    # Load models
    vad_model = load_silero_vad()
    logging.info("VAD model loaded")

    whisper_model_name = getenv("WHISPER_MODEL_NAME", "medium")
    whisper_device = getenv("WHISPER_DEVICE", "cuda")
    whisper_compute = getenv("WHISPER_COMPUTE", "float16")

    def _load_whisper():
        """Load (or reload) the Whisper model."""
        model = WhisperModel(
            whisper_model_name, device=whisper_device, compute_type=whisper_compute
        )
        logging.getLogger("faster_whisper").setLevel(logging.WARNING)
        logging.info("Whisper model loaded")
        return model

    whisper_model = _load_whisper()

    # Single-thread executor for Whisper — keeps transcription off the event loop
    # while ensuring only one CUDA call runs at a time
    whisper_state = {
        "executor_is_fresh": True,
        "rebuilding": False,
        "executor": ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper"),
        "model": whisper_model,
    }
    app.state.whisper = whisper_state

    def _transcribe_sync(audio: np.ndarray, initial_prompt: str):
        """Run Whisper transcription synchronously (called from executor thread)."""
        model = whisper_state.get("model")
        if model is None:
            raise RuntimeError("Whisper model not loaded")
        segments, info = model.transcribe(audio, initial_prompt=initial_prompt)
        segments = list(segments)
        text = " ".join(segment.text for segment in segments).strip()
        # Whisper's own estimate that a segment is not speech — captured so
        # the prompt-echo problem can be studied against real numbers.
        no_speech_prob = (
            max(
                (getattr(segment, "no_speech_prob", 0.0) or 0.0) for segment in segments
            )
            if segments
            else None
        )
        return text, info, no_speech_prob

    def _rebuild_executor():
        """Abandon a stuck executor thread and create a fresh one (keeps the model)."""
        logging.warning("Whisper executor stuck — replacing with fresh thread")
        whisper_state["executor"].shutdown(wait=False)
        whisper_state["executor"] = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="whisper"
        )

    async def _rebuild_whisper():
        """Full recovery: new executor + reload the Whisper model (fixes corrupted CUDA state).

        Guarded against re-entry — if a rebuild is already in progress,
        subsequent calls are no-ops. Transcription is skipped while rebuilding.
        """
        if whisper_state["rebuilding"]:
            logging.warning("Whisper rebuild already in progress — skipping")
            return
        whisper_state["rebuilding"] = True
        try:
            logging.warning("Whisper timed out on fresh executor — reloading model")
            whisper_state["executor"].shutdown(wait=False)
            whisper_state["model"] = None
            new_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="whisper"
            )
            whisper_state["executor"] = new_executor
            loop = asyncio.get_event_loop()
            whisper_state["model"] = await loop.run_in_executor(
                new_executor, _load_whisper
            )
        except Exception as e:
            logging.error(f"Whisper rebuild failed: {e}")
        finally:
            whisper_state["rebuilding"] = False

    # Set up templates and static files
    BASE_DIR = Path(__file__).parent
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        """Render the mic page."""
        return templates.TemplateResponse(
            "ear.html", {"request": request, "body_url": link.body_url}
        )

    @app.websocket("/ws/audio")
    async def audio_websocket(websocket: WebSocket) -> None:
        """WebSocket endpoint for receiving audio data and performing VAD."""
        initial_prompt = WHISPER_PROMPT
        try:
            logging.debug("WebSocket handler entered")
            await websocket.accept()
            logging.info("Audio WebSocket connected")

            sample_rate = 16000
            vad_chunk_size = 512  # VADIterator requires 512, 1024, or 1536 samples

            # Create VAD iterator for this connection
            vad_iterator = VADIterator(
                model=vad_model,
                sampling_rate=sample_rate,
                threshold=0.5,
                min_silence_duration_ms=500,
                speech_pad_ms=100,
            )

            audio_buffer = bytearray()

            # Speech accumulation
            is_speaking = False
            speech_buffer: list = []  # Will hold audio tensors during speech

            while True:
                data = await websocket.receive_bytes()

                # Keep-alive ping (empty message)
                if len(data) == 0:
                    logging.debug("Keep-alive ping received")
                    continue

                audio_buffer.extend(data)

                bytes_per_chunk = vad_chunk_size * 2  # 2 bytes per int16 sample

                # Process in 512-sample chunks as required by VADIterator
                while len(audio_buffer) >= bytes_per_chunk:
                    chunk_bytes = bytes(audio_buffer[:bytes_per_chunk])
                    audio_buffer = audio_buffer[bytes_per_chunk:]

                    # Convert to tensor for VAD
                    audio_array = np.frombuffer(chunk_bytes, dtype=np.int16)
                    audio_float = audio_array.astype(np.float32) / 32768.0
                    audio_tensor = torch.from_numpy(audio_float)

                    # Feed to VAD iterator
                    event = vad_iterator(audio_tensor, return_seconds=True)

                    if event and "start" in event:
                        logging.debug(f"Speech started at {event['start']:.2f}s")
                        is_speaking = True
                        speech_buffer = []

                    if is_speaking:
                        speech_buffer.append(audio_tensor)

                    if event and "end" in event:
                        logging.debug(f"Speech ended at {event['end']:.2f}s")
                        is_speaking = False

                        if speech_buffer:
                            # Skip transcription while Whisper is rebuilding
                            if whisper_state["rebuilding"]:
                                logging.debug(
                                    "Whisper rebuilding — dropping audio chunk"
                                )
                                speech_buffer = []
                                continue

                            # Concatenate all chunks and transcribe
                            full_audio = torch.cat(speech_buffer).numpy()
                            duration = len(full_audio) / sample_rate
                            logging.debug(f"Transcribing {duration:.1f}s of audio")

                            try:
                                loop = asyncio.get_event_loop()
                                text, info, no_speech_prob = await asyncio.wait_for(
                                    loop.run_in_executor(
                                        whisper_state["executor"],
                                        _transcribe_sync,
                                        full_audio,
                                        initial_prompt,
                                    ),
                                    timeout=WHISPER_TIMEOUT,
                                )
                                whisper_state["executor_is_fresh"] = False
                            except asyncio.TimeoutError:
                                logging.error(
                                    f"Whisper transcription timed out after {WHISPER_TIMEOUT}s "
                                    f"on {duration:.1f}s of audio — skipping chunk"
                                )
                                if whisper_state["executor_is_fresh"]:
                                    # Fresh executor timed out — CUDA/model is broken
                                    await _rebuild_whisper()
                                else:
                                    # Executor was stuck from a previous timeout — just replace the thread
                                    _rebuild_executor()
                                whisper_state["executor_is_fresh"] = True
                                speech_buffer = []
                                continue

                            logging.debug(f"Transcription [{info.language}]: {text}")
                            heard = utterance(
                                text,
                                getattr(info, "language", None),
                                getattr(info, "language_probability", None),
                                duration,
                                no_speech_prob,
                            )
                            heard["whisper_prompt"] = initial_prompt
                            # To the body, awaited so order is kept; the body
                            # says what it made of the line, and the mic page
                            # shows both.
                            answer = await link.deliver(heard)
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "text": text,
                                        "language": heard["language"],
                                        "duration": duration,
                                        "body": answer,
                                    }
                                )
                            )

                            speech_buffer = []

        except Exception as e:
            logging.warning(f"WebSocket disconnected: {e}")
        finally:
            vad_iterator.reset_states()

    return app


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("torio").setLevel(logging.WARNING)
    app = create_ear_app()
    uvicorn.run(app, host="0.0.0.0", port=EAR_PORT)
