from pathlib import Path
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from silero_vad import load_silero_vad, get_speech_timestamps
from faster_whisper import WhisperModel

from datetime import datetime

import wave
import logging
import uvicorn

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Create FastAPI app
app = FastAPI()

# Load models
vad_model = load_silero_vad()
logging.info("VAD model loaded")

whisper_model = WhisperModel("base", device="cuda", compute_type="float16")
logging.info("Whisper model loaded")

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
    try:
        logging.info("WebSocket handler entered")
        await websocket.accept()
        logging.info("Audio WebSocket connected")

        audio_buffer = bytearray()
        sample_rate = 16000
        chunk_duration = 3  # seconds to analyze at a time

        while True:
            data = await websocket.receive_bytes()
            audio_buffer.extend(data)

            bytes_per_chunk = sample_rate * 2 * chunk_duration

            if len(audio_buffer) >= bytes_per_chunk:
                # Convert bytes to numpy array for VAD
                import numpy as np

                audio_array = np.frombuffer(
                    audio_buffer[:bytes_per_chunk], dtype=np.int16
                )
                audio_float = audio_array.astype(np.float32) / 32768.0

                # Check for speech
                import torch

                audio_tensor = torch.from_numpy(audio_float)
                speech_timestamps = get_speech_timestamps(
                    audio_tensor, vad_model, sampling_rate=sample_rate
                )

                if speech_timestamps:
                    logging.info(
                        f"Speech detected: {len(speech_timestamps)} segment(s)"
                    )

                audio_buffer = audio_buffer[bytes_per_chunk:]

    except Exception as e:
        logging.info(f"WebSocket disconnected: {e}")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
