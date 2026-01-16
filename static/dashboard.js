// Faebot Dashboard - Audio Capture
// Step 1: Microphone access and visualization

class AudioCapture {
    constructor() {
        this.startBtn = document.getElementById('startBtn');
        this.stopBtn = document.getElementById('stopBtn');
        this.statusEl = document.getElementById('audioStatus');
        this.canvas = document.getElementById('visualizer');
        this.sessionStartEl = document.getElementById('sessionStart');
        
        this.audioContext = null;
        this.analyser = null;
        this.mediaStream = null;
        this.isRecording = false;
        this.canvasCtx = this.canvas.getContext('2d');
        this.animationId = null;
        
        this.startBtn.addEventListener('click', () => this.start());
        this.stopBtn.addEventListener('click', () => this.stop());

        this.websocket = null;
        this.workletNode = null;
    }
    
    async start() {
        try {
            this.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 }
            });
            
            this.audioContext = new AudioContext({ sampleRate: 16000 });
            this.analyser = this.audioContext.createAnalyser();
            this.analyser.fftSize = 256;
            
            // Create MediaStream source
            const source = this.audioContext.createMediaStreamSource(this.mediaStream);
            source.connect(this.analyser);
            this.connectWebSocket();

            // Setup AudioWorklet for processing audio data
            await this.audioContext.audioWorklet.addModule('/static/audio-processor.js');
            this.workletNode = new AudioWorkletNode(this.audioContext, 'audio-processor');
            
            //the worklet sends us Float32 samples (range -1 to 1), and we convert them to Int16 (range -32768 to 32767) because that's what Whisper expects. 
            // We then send the raw bytes over the WebSocket.
            this.workletNode.port.onmessage = (event) => {
                if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
                    const float32Array = event.data;
                    const int16Array = new Int16Array(float32Array.length);
                    for (let i = 0; i < float32Array.length; i++) {
                        int16Array[i] = Math.max(-32768, Math.min(32767, float32Array[i] * 32768));
                    }
                    this.websocket.send(int16Array.buffer);
                }
            };

            source.connect(this.workletNode);

            
            this.isRecording = true;
            this.startBtn.disabled = true;
            this.stopBtn.disabled = false;
            this.statusEl.classList.add('recording');
            this.statusEl.querySelector('.label').textContent = 'Recording...';
            
            const now = new Date();
            this.sessionStartEl.textContent = `Listening since ${now.toLocaleTimeString()}`;
            
            this.drawVisualizer();
            
            console.log('Audio capture started, sample rate:', this.audioContext.sampleRate);
        } catch (err) {
            console.error('Failed to start audio capture:', err);
            alert('Could not access microphone: ' + err.message);
        }
    }
    
    stop() {
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }
        if (this.audioContext) {
            this.audioContext.close();
            this.audioContext = null;
        }
        if (this.animationId) {
            cancelAnimationFrame(this.animationId);
            this.animationId = null;
        }

        if (this.websocket) {
            this.websocket.close();
            this.websocket = null;
        }
        
        this.isRecording = false;
        this.startBtn.disabled = false;
        this.stopBtn.disabled = true;
        this.statusEl.classList.remove('recording');
        this.statusEl.querySelector('.label').textContent = 'Not recording';
        this.sessionStartEl.textContent = 'Not listening';
        
        // Clear canvas
        this.canvasCtx.fillStyle = '#252540';
        this.canvasCtx.fillRect(0, 0, this.canvas.width, this.canvas.height);

        if (this.workletNode) {
            this.workletNode.disconnect();
            this.workletNode = null;
        }
        
        console.log('Audio capture stopped');

        // Clear keep-alive interval
        if (this.keepAliveInterval) {
            clearInterval(this.keepAliveInterval);
            this.keepAliveInterval = null;
        }

        console.log('WebSocket closed');

    }
    
    drawVisualizer() {
        if (!this.isRecording) return;
        this.animationId = requestAnimationFrame(() => this.drawVisualizer());
        
        const dataArray = new Uint8Array(this.analyser.frequencyBinCount);
        this.analyser.getByteFrequencyData(dataArray);
        
        const rect = this.canvas.getBoundingClientRect();
        this.canvas.width = rect.width;
        this.canvas.height = rect.height;
        
        this.canvasCtx.fillStyle = '#252540';
        this.canvasCtx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        
        const barWidth = (this.canvas.width / dataArray.length) * 2.5;
        let x = 0;
        
        for (let i = 0; i < dataArray.length; i++) {
            const barHeight = (dataArray[i] / 255) * this.canvas.height;
            const ratio = barHeight / this.canvas.height;
            this.canvasCtx.fillStyle = `rgba(135, 206, 250, ${0.3 + ratio * 0.7})`;
            this.canvasCtx.fillRect(x, this.canvas.height - barHeight, barWidth, barHeight);
            x += barWidth + 1;
        }
    }

    connectWebSocket() {
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
            console.log('WebSocket already connected');
            return;
        }
        
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/audio`;
        
        this.websocket = new WebSocket(wsUrl);
        
        this.websocket.onopen = () => {
            console.log('WebSocket connected');
            document.getElementById('connectionStatus').textContent = 'Connected';
            document.getElementById('connectionStatus').classList.remove('disconnected');   
            document.getElementById('connectionStatus').classList.add('connected');

             // Keep-alive ping every 30 seconds
            this.keepAliveInterval = setInterval(() => {
                if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
                    this.websocket.send(new ArrayBuffer(0));  // Empty message as ping
                }
            }, 30000);
        };
        
        this.websocket.onclose = () => {
            console.log('WebSocket disconnected');
            document.getElementById('connectionStatus').textContent = 'Disconnected';
            document.getElementById('connectionStatus').classList.remove('connected');
            document.getElementById('connectionStatus').classList.add('disconnected');
            
            if (this.keepAliveInterval) {
                clearInterval(this.keepAliveInterval);
                this.keepAliveInterval = null;
            }
        };
        
        this.websocket.onerror = (error) => {
            console.error('WebSocket error:', error);
        };

        this.websocket.onmessage = (event) => {
            const text = event.data;
            console.log('Transcription:', text);
            
            const log = document.getElementById('transcriptionLog');
            const empty = log.querySelector('.log-empty');
            if (empty) empty.remove();
            
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            entry.innerHTML = `
                <div class="time">${new Date().toLocaleTimeString()}</div>
                <div class="text">${text}</div>
            `;
            log.appendChild(entry);
            log.scrollTop = log.scrollHeight;
        };
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.audioCapture = new AudioCapture();
});