// Faebot Dashboard - Audio Capture
// Step 1: Microphone access and visualization

class AudioCapture {
    constructor() {
        this.startBtn = document.getElementById('startBtn');
        this.stopBtn = document.getElementById('stopBtn');
        this.statusEl = document.getElementById('audioStatus');
        this.canvas = document.getElementById('visualizer');
        this.durationEl = document.getElementById('duration');
        
        this.audioContext = null;
        this.analyser = null;
        this.mediaStream = null;
        this.isRecording = false;
        this.startTime = null;
        this.durationInterval = null;
        this.canvasCtx = this.canvas.getContext('2d');
        this.animationId = null;
        
        this.startBtn.addEventListener('click', () => this.start());
        this.stopBtn.addEventListener('click', () => this.stop());

        this.websocket = null;
    }
    
    async start() {
        try {
            this.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 }
            });
            
            this.audioContext = new AudioContext({ sampleRate: 16000 });
            this.analyser = this.audioContext.createAnalyser();
            this.analyser.fftSize = 256;
            
            const source = this.audioContext.createMediaStreamSource(this.mediaStream);
            source.connect(this.analyser);
            this.connectWebSocket();
            
            this.isRecording = true;
            this.startBtn.disabled = true;
            this.stopBtn.disabled = false;
            this.statusEl.classList.add('recording');
            this.statusEl.querySelector('.label').textContent = 'Recording...';
            
            this.startTime = Date.now();
            this.durationInterval = setInterval(() => this.updateDuration(), 1000);
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
        if (this.durationInterval) {
            clearInterval(this.durationInterval);
            this.durationInterval = null;
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
        
        // Clear canvas
        this.canvasCtx.fillStyle = '#252540';
        this.canvasCtx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        
        console.log('Audio capture stopped');
    }
    
    updateDuration() {
        const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
        const minutes = Math.floor(elapsed / 60);
        const seconds = elapsed % 60;
        this.durationEl.textContent = `${minutes}:${seconds.toString().padStart(2, '0')}`;
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
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/audio`;
    
    this.websocket = new WebSocket(wsUrl);
    
    this.websocket.onopen = () => {
        console.log('WebSocket connected');
        document.getElementById('connectionStatus').textContent = 'Connected';
        document.getElementById('connectionStatus').classList.remove('disconnected');
        document.getElementById('connectionStatus').classList.add('connected');
    };
    
    this.websocket.onclose = () => {
        console.log('WebSocket disconnected');
        document.getElementById('connectionStatus').textContent = 'Disconnected';
        document.getElementById('connectionStatus').classList.remove('connected');
        document.getElementById('connectionStatus').classList.add('disconnected');
    };
    
    this.websocket.onerror = (error) => {
        console.error('WebSocket error:', error);
    };
}
}

document.addEventListener('DOMContentLoaded', () => {
    window.audioCapture = new AudioCapture();
});