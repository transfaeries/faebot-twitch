from pathlib import Path
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from datetime import datetime

import wave
import logging
import uvicorn

app = FastAPI()

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.websocket("/ws/audio")
async def audio_websocket(websocket: WebSocket):
    await websocket.accept()
    logging.info("Audio WebSocket connected")

    audio_buffer = bytearray()
    sample_rate = 16000

    try:
        while True:
            data = await websocket.receive_bytes()
            audio_buffer.extend(data)

            # Save a test file every ~5 seconds of audio (16000 samples/sec * 2 bytes * 5 sec)
            if len(audio_buffer) >= sample_rate * 2 * 5:
                filename = f"test_audio_{datetime.now().strftime('%H%M%S')}.wav"
                with wave.open(filename, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)  # 2 bytes for int16
                    wav_file.setframerate(sample_rate)
                    wav_file.writeframes(audio_buffer)
                logging.info(f"Saved test audio: {filename}")
                audio_buffer.clear()

    except Exception as e:
        logging.info(f"WebSocket disconnected: {e}")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
