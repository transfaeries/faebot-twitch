// Faebot Dashboard — the body's generation cards, live from /ws/events.

// EventStream: listens to /ws/events and renders generation cards.
// Cards are correlated by generation_id — `generating` opens a card,
// `response` fills it in, `pass` marks it as chosen silence, `error` marks
// it failed. Multiple in-flight cards are fine; each closes independently.
// The reasoning channel (when the model has one) sits in a collapsed
// <details> on the card — thoughts visible, words trusted.
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

        const badge = document.getElementById('connectionStatus');
        this.websocket.onopen = () => {
            console.log('Events WebSocket connected');
            this.reconnectAttempts = 0;
            if (badge) {
                badge.textContent = 'Connected';
                badge.classList.remove('disconnected');
                badge.classList.add('connected');
            }
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
            if (badge) {
                badge.textContent = 'Disconnected';
                badge.classList.remove('connected');
                badge.classList.add('disconnected');
            }
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
            case 'pass':
                this.passCard(event);
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
            <details class="gen-reasoning" hidden>
                <summary>reasoning</summary>
                <pre class="gen-reasoning-text"></pre>
            </details>
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

        card.addEventListener('click', (click) => {
            // The reasoning dropdown toggles itself; don't also toggle the card.
            if (click.target.closest('.gen-reasoning')) return;
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
        this.finishCard(card, entry, event);
    }

    passCard(event) {
        const entry = this.cards.get(event.id);
        if (!entry) {
            console.debug('pass for unknown id:', event.id);
            return;
        }
        const card = entry.el;
        card.classList.remove('pending');
        card.classList.add('passed');
        const response = card.querySelector('.gen-response');
        response.textContent = event.reason
            ? `— stayed quiet: ${event.reason}`
            : '— stayed quiet —';
        this.finishCard(card, entry, event);
    }

    // Shared tail of response/pass: show the reasoning channel if there is
    // one, and write the meta line (model · api time · finish reason).
    finishCard(card, entry, event) {
        if (event.reasoning) {
            const reasoning = card.querySelector('.gen-reasoning');
            reasoning.querySelector('.gen-reasoning-text').textContent = event.reasoning;
            reasoning.hidden = false;
        }
        const meta = card.querySelector('.gen-meta');
        if (meta) {
            const parts = [`model: ${event.model || (entry.generating && entry.generating.model) || 'unknown'}`];
            if (typeof event.elapsed === 'number') parts.push(`${event.elapsed.toFixed(1)}s`);
            if (event.finish_reason && event.finish_reason !== 'stop') parts.push(`finish: ${event.finish_reason}`);
            if (event.attempts && event.attempts > 1) parts.push(`attempts: ${event.attempts}`);
            meta.textContent = parts.join(' · ');
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
    window.eventStream = new EventStream();
});
