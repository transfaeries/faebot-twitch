from pathlib import Path
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from silero_vad import load_silero_vad, VADIterator
from faster_whisper import WhisperModel
from os import getenv
import json

import logging
import uvicorn

env = getenv("ENVIRONMENT", "dev").lower()
if env == "prod":
    logging_level = logging.INFO
else:
    logging_level = logging.DEBUG

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging_level,
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Create FastAPI app
app = FastAPI()

# Load models
vad_model = load_silero_vad()
logging.info("VAD model loaded")

whisper_model_name = getenv("WHISPER_MODEL_NAME", "medium")
whisper_model = WhisperModel(whisper_model_name, device="cuda", compute_type="float16")
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
    initial_prompt = "faebot, transfaeries"
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
        speech_buffer = []  # Will hold audio tensors during speech

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
                import numpy as np
                import torch

                audio_array = np.frombuffer(chunk_bytes, dtype=np.int16)
                audio_float = audio_array.astype(np.float32) / 32768.0
                audio_tensor = torch.from_numpy(audio_float)

                # Feed to VAD iterator
                event = vad_iterator(audio_tensor, return_seconds=True)

                if event and "start" in event:
                    logging.info(f"Speech started at {event['start']:.2f}s")
                    is_speaking = True
                    speech_buffer = []

                if is_speaking:
                    speech_buffer.append(audio_tensor)

                if event and "end" in event:
                    logging.info(f"Speech ended at {event['end']:.2f}s")
                    is_speaking = False

                    if speech_buffer:
                        # Concatenate all chunks and transcribe
                        full_audio = torch.cat(speech_buffer).numpy()
                        logging.info(
                            f"Transcribing {len(full_audio) / sample_rate:.1f}s of audio"
                        )

                        segments, info = whisper_model.transcribe(
                            full_audio, initial_prompt=initial_prompt
                        )
                        text = " ".join(segment.text for segment in segments).strip()

                        # Filter out prompt echoes
                        if text and text.lower() not in initial_prompt:
                            logging.info(f"Transcription [{info.language}]: {text}")
                            await websocket.send_text(
                                json.dumps({"text": text, "language": info.language})
                            )
                        else:
                            logging.debug(f"Filtered prompt echo: {text}")

                        speech_buffer = []

    except Exception as e:
        logging.info(f"WebSocket disconnected: {e}")
    finally:
        vad_iterator.reset_states()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
