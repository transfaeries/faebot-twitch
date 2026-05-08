// Faebot Dashboard - Audio Capture
// Step 1: Microphone access and visualization

// Whisper emits ISO 639-1 codes; the browser's Intl.DisplayNames turns those
// into the user's localized full name. Falls back to the raw code if the
// browser can't resolve it (very old browsers or unknown codes).
const LANGUAGE_DISPLAY = new Intl.DisplayNames([navigator.language || 'en'], {
    type: 'language',
});

function languageName(code) {
    try {
        return LANGUAGE_DISPLAY.of(code) || code;
    } catch {
        return code;
    }
}

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

            this.reconnectAttempts = 0;
            
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
            
            // Reset reconnect backoff on successful connection
            this.reconnectAttempts = 0;
            
            // Keep-alive ping every 30 seconds
            this.keepAliveInterval = setInterval(() => {
                if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
                    this.websocket.send(new ArrayBuffer(0));
                }
            }, 30000);
        };
        
        this.websocket.onclose = (event) => {
            console.log('WebSocket disconnected, code:', event.code);
            document.getElementById('connectionStatus').textContent = 'Disconnected';
            document.getElementById('connectionStatus').classList.remove('connected');
            document.getElementById('connectionStatus').classList.add('disconnected');
            
            if (this.keepAliveInterval) {
                clearInterval(this.keepAliveInterval);
                this.keepAliveInterval = null;
            }
            
            // Auto-reconnect if we're still recording
            if (this.isRecording) {
                this.reconnectAttempts = (this.reconnectAttempts || 0) + 1;
                const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts - 1), 30000);
                console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
                setTimeout(() => this.connectWebSocket(), delay);
            }
        };
        
        this.websocket.onerror = (error) => {
            console.error('WebSocket error:', error);
        };

        this.websocket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            const text = data.text;
            const language = data.language;
            console.log('Transcription:', text, `[${language}]`);

            const log = document.getElementById('transcriptionLog');
            const empty = log.querySelector('.log-empty');
            if (empty) empty.remove();

            const entry = document.createElement('div');
            entry.className = 'log-entry';
            entry.innerHTML = `
                <div class="time"></div>
                <div class="text"></div>
            `;
            // textContent for untrusted strings — Whisper output isn't user-typed,
            // but it can contain HTML chars if someone happens to say them.
            entry.querySelector('.time').textContent =
                `${new Date().toLocaleTimeString()} [${languageName(language)}]`;
            entry.querySelector('.text').textContent = text;
            log.appendChild(entry);
            log.scrollTop = log.scrollHeight;
        };
    }
}

// EventStream: listens to /ws/events and renders generation cards.
// Cards are correlated by generation_id — `generating` opens a card,
// `response` fills it in, `error` marks it failed. Multiple in-flight
// cards are fine; each closes independently.
class EventStream {
    constructor() {
        this.log = document.getElementById('generationsLog');
        this.cards = new Map(); // generation_id -> { el, generating }
        this.maxCards = 256;
        this.websocket = null;
        this.reconnectAttempts = 0;
        this.connect();
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/events`;
        this.websocket = new WebSocket(wsUrl);

        this.websocket.onopen = () => {
            console.log('Events WebSocket connected');
            this.reconnectAttempts = 0;
        };

        this.websocket.onmessage = (e) => {
            try {
                const event = JSON.parse(e.data);
                this.handleEvent(event);
            } catch (err) {
                console.error('Failed to parse event:', err, e.data);
            }
        };

        this.websocket.onclose = () => {
            console.log('Events WebSocket disconnected');
            this.reconnectAttempts += 1;
            const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts - 1), 30000);
            setTimeout(() => this.connect(), delay);
        };

        this.websocket.onerror = (err) => {
            console.error('Events WebSocket error:', err);
        };
    }

    handleEvent(event) {
        switch (event.type) {
            case 'generating':
                this.openCard(event);
                break;
            case 'response':
                this.fillCard(event);
                break;
            case 'error':
                this.failCard(event);
                break;
            default:
                console.warn('Unknown event type:', event.type);
        }
    }

    openCard(event) {
        // If we're replaying from the ring buffer, the same id may already exist.
        if (this.cards.has(event.id)) return;

        const empty = this.log.querySelector('.log-empty');
        if (empty) empty.remove();

        const card = document.createElement('div');
        card.className = 'gen-card pending';
        card.dataset.id = event.id;

        const triggerIcon = event.trigger_type === 'voice' ? '🎤' : '💬';
        const time = event.timestamp
            ? new Date(event.timestamp).toLocaleTimeString()
            : '';

        card.innerHTML = `
            <div class="gen-header">
                <span class="gen-icon">${triggerIcon}</span>
                <span class="gen-time">${time}</span>
            </div>
            <div class="gen-trigger"></div>
            <div class="gen-response"><span class="gen-pending">generating…</span></div>
            <div class="gen-details" hidden>
                <h3>Trigger</h3>
                <pre class="gen-trigger-full"></pre>
                <h3>Prompt</h3>
                <pre class="gen-prompt"></pre>
                <h3>System prompt</h3>
                <pre class="gen-system"></pre>
                <h3>Params</h3>
                <pre class="gen-params"></pre>
                <div class="gen-meta"></div>
            </div>
        `;
        // textContent for untrusted strings (chat content can contain anything)
        card.querySelector('.gen-trigger').textContent = event.trigger || '';
        card.querySelector('.gen-trigger-full').textContent = event.trigger || '';
        card.querySelector('.gen-prompt').textContent = event.prompt || '';
        card.querySelector('.gen-system').textContent = event.system_prompt || '';
        card.querySelector('.gen-params').textContent = JSON.stringify(
            event.params || {}, null, 2
        );
        card.querySelector('.gen-meta').textContent = `model: ${event.model || 'unknown'}`;

        card.addEventListener('click', () => {
            const details = card.querySelector('.gen-details');
            details.hidden = !details.hidden;
        });

        this.log.appendChild(card);
        this.cards.set(event.id, { el: card, generating: event });
        this.evictExtras();
        this.scrollToBottom();
    }

    fillCard(event) {
        const entry = this.cards.get(event.id);
        if (!entry) {
            console.debug('response for unknown id (likely aged out of buffer):', event.id);
            return;
        }
        const card = entry.el;
        card.classList.remove('pending');
        card.querySelector('.gen-response').textContent = event.text || '';
        // Update the meta line with timing if we have both timestamps
        const meta = card.querySelector('.gen-meta');
        const startTs = entry.generating && entry.generating.timestamp;
        if (meta && startTs && event.timestamp) {
            const dt = (new Date(event.timestamp) - new Date(startTs)) / 1000;
            meta.textContent = `model: ${entry.generating.model || 'unknown'} · ${dt.toFixed(2)}s`;
        }
        this.scrollToBottom();
    }

    failCard(event) {
        const entry = this.cards.get(event.id);
        if (!entry) {
            console.debug('error for unknown id:', event.id);
            return;
        }
        const card = entry.el;
        card.classList.remove('pending');
        card.classList.add('error');
        card.querySelector('.gen-response').textContent = event.error || 'unknown error';
        this.scrollToBottom();
    }

    evictExtras() {
        while (this.log.children.length > this.maxCards) {
            const oldest = this.log.firstElementChild;
            if (!oldest) break;
            const id = oldest.dataset.id;
            if (id) this.cards.delete(id);
            oldest.remove();
        }
    }

    scrollToBottom() {
        this.log.scrollTop = this.log.scrollHeight;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.audioCapture = new AudioCapture();
    window.eventStream = new EventStream();
});