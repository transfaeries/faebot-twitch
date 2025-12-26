"""
FastAPI server for Faebot dashboard and audio capture.

This server provides:
- A web dashboard for monitoring and controlling Faebot
- A WebSocket endpoint for streaming audio from the browser
- REST endpoints for bot status and configuration
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from collections import deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse

import numpy as np

# Configure logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Path setup
BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


@dataclass
class AudioBuffer:
    """Buffer for accumulating audio chunks until we have an utterance."""
    chunks: deque = field(default_factory=deque)
    sample_rate: int = 16000
    is_speaking: bool = False
    silence_chunks: int = 0
    
    def add_chunk(self, audio_data: bytes):
        """Add an audio chunk to the buffer."""
        # Convert bytes to numpy array (assuming 16-bit PCM)
        audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        self.chunks.append(audio_array)
        
    def get_audio(self) -> np.ndarray:
        """Get all buffered audio as a single array."""
        if not self.chunks:
            return np.array([], dtype=np.float32)
        return np.concatenate(list(self.chunks))
    
    def clear(self):
        """Clear the buffer."""
        self.chunks.clear()
        self.is_speaking = False
        self.silence_chunks = 0


class FaebotServer:
    """FastAPI server for Faebot dashboard and audio processing."""
    
    def __init__(self):
        self.app = FastAPI(title="Faebot Dashboard")
        self.audio_buffers: dict[str, AudioBuffer] = {}
        self.transcription_callback: Optional[callable] = None
        
        # Track recent transcriptions for display
        self.recent_transcriptions: deque = deque(maxlen=50)
        
        # Set up routes
        self._setup_routes()
        
        # Set up static files and templates
        STATIC_DIR.mkdir(exist_ok=True)
        TEMPLATES_DIR.mkdir(exist_ok=True)
        
        self.app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
        self.templates = Jinja2Templates(directory=TEMPLATES_DIR)
    
    def _setup_routes(self):
        """Configure all routes."""
        
        @self.app.get("/", response_class=HTMLResponse)
        async def dashboard(request: Request):
            """Serve the main dashboard page."""
            return self.templates.TemplateResponse(
                "dashboard.html",
                {"request": request}
            )
        
        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            return {"status": "ok", "service": "faebot-dashboard"}
        
        @self.app.get("/api/transcriptions")
        async def get_transcriptions():
            """Get recent transcriptions."""
            return {"transcriptions": list(self.recent_transcriptions)}
        
        @self.app.websocket("/ws/audio")
        async def audio_websocket(websocket: WebSocket):
            """WebSocket endpoint for streaming audio from browser."""
            await websocket.accept()
            client_id = str(id(websocket))
            self.audio_buffers[client_id] = AudioBuffer()
            
            logger.info(f"Audio WebSocket connected: {client_id}")
            
            try:
                while True:
                    # Receive audio data
                    data = await websocket.receive_bytes()
                    
                    # Log receipt (we'll process it properly once VAD is added)
                    buffer = self.audio_buffers[client_id]
                    buffer.add_chunk(data)
                    
                    # For now, just log chunk sizes periodically
                    total_samples = sum(len(c) for c in buffer.chunks)
                    if total_samples > 0 and total_samples % 16000 < 512:  # ~every second
                        duration = total_samples / buffer.sample_rate
                        logger.debug(f"Audio buffer: {duration:.1f}s of audio")
                    
                    # Send acknowledgment back to client
                    await websocket.send_json({
                        "type": "ack",
                        "samples": len(data) // 2  # 16-bit = 2 bytes per sample
                    })
                        
            except WebSocketDisconnect:
                logger.info(f"Audio WebSocket disconnected: {client_id}")
            except Exception as e:
                logger.error(f"Audio WebSocket error: {e}")
            finally:
                # Clean up
                if client_id in self.audio_buffers:
                    del self.audio_buffers[client_id]
        
        @self.app.websocket("/ws/status")
        async def status_websocket(websocket: WebSocket):
            """WebSocket for real-time status updates to dashboard."""
            await websocket.accept()
            logger.info("Status WebSocket connected")
            
            try:
                while True:
                    # Send periodic status updates
                    await websocket.send_json({
                        "type": "status",
                        "audio_clients": len(self.audio_buffers),
                        "recent_transcriptions": len(self.recent_transcriptions)
                    })
                    await asyncio.sleep(2)
            except WebSocketDisconnect:
                logger.info("Status WebSocket disconnected")
    
    def add_transcription(self, text: str, source: str = "voice"):
        """Add a transcription to the history and trigger callback."""
        entry = {
            "text": text,
            "source": source,
            "timestamp": asyncio.get_event_loop().time()
        }
        self.recent_transcriptions.append(entry)
        logger.info(f"Transcription [{source}]: {text}")
        
        # Trigger callback if set (this is how we'll inject into the bot)
        if self.transcription_callback:
            self.transcription_callback(text, source)
    
    def set_transcription_callback(self, callback: callable):
        """Set callback for when transcriptions are ready."""
        self.transcription_callback = callback


# Create global server instance
server = FaebotServer()
app = server.app