from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from silero_vad import load_silero_vad, VADIterator
from faster_whisper import WhisperModel
from os import getenv
import asyncio
import json
import logging
import uvicorn
import numpy as np
import torch

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


WHISPER_TIMEOUT = int(getenv("WHISPER_TIMEOUT", "30"))


def create_app(bot=None):
    """Create the FastAPI app, optionally with a reference to the Twitch bot."""
    app = FastAPI()
    app.state.bot = bot

    # Load models
    vad_model = load_silero_vad()
    logging.info("VAD model loaded")

    whisper_model_name = getenv("WHISPER_MODEL_NAME", "medium")
    whisper_device = getenv("WHISPER_DEVICE", "cuda")
    whisper_compute = getenv("WHISPER_COMPUTE", "float16")

    def _load_whisper():
        """Load (or reload) the Whisper model."""
        model = WhisperModel(whisper_model_name, device=whisper_device, compute_type=whisper_compute)
        logging.getLogger("faster_whisper").setLevel(logging.WARNING)
        logging.info("Whisper model loaded")
        return model

    whisper_model = _load_whisper()

    # Single-thread executor for Whisper — keeps transcription off the event loop
    # while ensuring only one CUDA call runs at a time
    whisper_state = {
        "executor_is_fresh": True,
        "executor": ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper"),
        "model": whisper_model,
    }
    app.state.whisper = whisper_state

    def _transcribe_sync(audio: np.ndarray, initial_prompt: str):
        """Run Whisper transcription synchronously (called from executor thread)."""
        segments, info = whisper_state["model"].transcribe(audio, initial_prompt=initial_prompt)
        text = " ".join(segment.text for segment in segments).strip()
        return text, info

    def _rebuild_executor():
        """Abandon a stuck executor thread and create a fresh one (keeps the model)."""
        logging.warning("Whisper executor stuck — replacing with fresh thread")
        whisper_state["executor"].shutdown(wait=False)
        whisper_state["executor"] = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")

    async def _rebuild_whisper():
        """Full recovery: new executor + reload the Whisper model (fixes corrupted CUDA state)."""
        logging.warning("Whisper timed out on fresh executor — reloading model")
        whisper_state["executor"].shutdown(wait=False)
        del whisper_state["model"]
        new_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
        whisper_state["executor"] = new_executor
        loop = asyncio.get_event_loop()
        whisper_state["model"] = await loop.run_in_executor(new_executor, _load_whisper)

    # Set up templates and static files
    BASE_DIR = Path(__file__).parent
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        """Render the dashboard page."""
        return templates.TemplateResponse("dashboard.html", {"request": request})

    @app.websocket("/ws/audio")
    async def audio_websocket(websocket: WebSocket) -> None:
        """WebSocket endpoint for receiving audio data and performing VAD."""
        initial_prompt = "faebot, transfaeries"
        # Whisper sometimes echoes back substrings of the prompt instead of real speech.
        # Substring check is intentional — catches partial echoes like "faebot" or "transfaeries".
        prompt_echo_source = initial_prompt
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
                            # Concatenate all chunks and transcribe
                            full_audio = torch.cat(speech_buffer).numpy()
                            duration = len(full_audio) / sample_rate
                            logging.debug(
                                f"Transcribing {duration:.1f}s of audio"
                            )

                            try:
                                loop = asyncio.get_event_loop()
                                text, info = await asyncio.wait_for(
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

                            if text and text.lower() not in prompt_echo_source:
                                logging.debug(
                                    f"Transcription [{info.language}]: {text}"
                                )
                                await websocket.send_text(
                                    json.dumps(
                                        {"text": text, "language": info.language}
                                    )
                                )

                                # Feed transcription to bot if connected
                                if app.state.bot:
                                    streamer = getenv(
                                        "STREAMER_CHANNEL", "transfaeries"
                                    )
                                    await app.state.bot.handle_transcription(
                                        streamer, text
                                    )
                            else:
                                logging.debug(f"Filtered prompt echo: {text}")

                            speech_buffer = []

        except Exception as e:
            logging.warning(f"WebSocket disconnected: {e}")
        finally:
            vad_iterator.reset_states()

    return app


if __name__ == "__main__":
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)
