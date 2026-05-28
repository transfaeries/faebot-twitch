# Faebot Twitch - Roadmap

## Phase 1: Quick Wins (Done)
- [x] Programmatic emote sourcing via Twitch API
- [x] Filter to tier 1 + follower emotes only
- [x] Fix stream category injection

## Phase 2: Voice Integration (Done ✓)
- [x] Combined entry point (FastAPI + TwitchIO in one async process)
- [x] Feed voice transcriptions into conversation context
- [x] Friendly error handling and clean shutdown
- [x] Voice-triggered replies with separate voice frequency
- [x] Name mentions: always trigger from chat, boost to chat rate from voice
- [x] Merged to main

## Phase 3: Code Quality & Resilience
- [x] Linting fixes + type hints (black, flake8, mypy all clean)
- [x] Audit log levels — most logging.info → logging.debug; INFO reserved for meaningful events only (bot ready, response sent, errors)
- [x] Self-knowledge block — faebot can accurately describe faerself, faer architecture, history, and live parameters (model, frequencies, history length)
- [x] Centralize logging config — local.py owns config (ENVIRONMENT-aware); faebot.py and server.py keep simple INFO fallbacks for standalone use
- [x] Run Whisper transcription in executor (unblock event loop during transcription)
- [x] Retry logic for API calls
- [x] Graceful shutdown — intercept SIGINT/SIGTERM via event loop signal handlers; shut down uvicorn + bot in sequence

## Phase 4: Architecture Refactor & Dashboard
The dashboard is blind to generation — can't see what prompt was used or what faebot sent. Fixing this requires splitting the bot into clean modules first.

Do these in order — each step is independently shippable and the bot keeps working throughout:

- [x] Extract `core.py` — move `Conversation`, `conversations`, `generate_response`, `generate`, `choose_to_reply` out of `faebot.py` into `core.py`. Rename `faebot.py` to `bot.py`. No TwitchIO or FastAPI deps in `core`. Both `bot.py` and `server.py` import from it.
- [x] Test suite — 34 tests against `core.py` (96% coverage). Covers conversation management, reply decisions, emote spacing, system prompt, OpenRouter retry logic, and full generation pipeline.
- [x] Add event queue — `core.generate_response` puts events (`generating`, `error`) on an `asyncio.Queue` injected by `local.py`. Each call carries a `generation_id` (uuid4 from caller), `trigger_type` (`"chat"` or `"voice"`) and UTC timestamps. `response` events are emitted by bot.py only after Twitch acknowledges the send, so the dashboard reflects what actually reached chat rather than what the model produced. Bot and server share the same queue. (PR: corepy)
- [x] Server-side `/ws/events` — background drain task pulls from the queue, appends to a `deque(maxlen=50)` ring buffer, and fans out to connected clients. Endpoint replays the ring buffer on connect so refresh preserves context. Drain runs regardless of who's watching (verified live with `websocat`).
- [x] Dashboard UI — two-pane layout (transcripts + generations), generation cards with click-to-expand inspector (full prompt, system prompt, params, model, timing). Yellow/green/red border states for pending/done/error. Cards correlated by `generation_id`. (PR: corepy)
- [ ] Twitch NOTICE handling — `channel.send` returning success doesn't mean Twitch delivered the message. Twitch silently rejects via IRC NOTICE (`msg_ratelimit`, `msg_duplicate`, `msg_slowmode`, etc.), which we don't currently catch. Cards turn green for messages that never reached chat. Wire `event_notice` / `event_raw_notice`, track most-recent send per channel, emit `error` event on rejection. Note: bot.py already owns response event emission post-send, so adding NOTICE correlation is a localized change there.
- [ ] Transcription pending state + dashboard broadcast — currently transcripts only appear on the streamer's dashboard (sent over `/ws/audio`) and only after Whisper completes. Two-part follow-up: (1) emit transcription events on the shared `/ws/events` queue so any dashboard viewer sees them; (2) add a pending state when VAD detects speech-end but transcription hasn't returned yet (yellow → green like generations). Decide event type discriminators when we get there (`type: "transcription"` etc).
- [ ] TwitchIO IRC connection recovery — long-running streams sometimes leave the IRC websocket in a stuck "closing" state where `channel.send` raises `ClientConnectionResetError: Cannot write to closing transport` repeatedly without TwitchIO auto-reconnecting. Observed in stream18.log on 2026-05-08: 75 occurrences over 40min, never recovered until manual bot restart. With response events now emitted post-send, the dashboard correctly flips cards red, so the streamer can see it; but bot remains degraded until restart. Investigate: is TwitchIO 2.10 missing auto-reconnect for this state, are we missing a `event_close`-style hook, or is sync-IO (`permalog`, slated for replacement by DB in Phase 5) starving the event loop and breaking IRC PING/PONG? **Quick experiment worth trying first:** wrap `permalog` in `run_in_executor` and see if the IRC bug recurs — cheap to do, eliminates one suspect. Possible fix: detect closed-transport state and force a reconnect, or surface degraded-state on the dashboard.
- [ ] Command aliases / fuzzy matching — let `fae;hist` and `fae;history`, `fae;freq` and `fae;frequency`, etc. all resolve to the same command so fae doesn't have to remember the exact spelling. Would also let `hello` and `help` share a single implementation cleanly.
- [ ] `requires_admin` decorator — mirror `requires_mod` for the admin-only check (currently inline in `join` and `model`). Small consistency win; admin is "person running the bot", distinct from Twitch's mod role.
- [ ] Twitch message length constant — replace magic `499` in `core.py` with a named `TWITCH_MAX_MESSAGE_LENGTH = 500` constant. Trivial polish.
- [x] Extract `commands.py` — moved all `fb;`/`fae;` command handlers to a `FaebotCommands` mixin. `Faebot` inherits from both `commands.Bot` and `FaebotCommands`. `bot.py` is now thin event wiring only.
- [x] Fix Whisper rebuild re-entry bug — guarded `_rebuild_whisper` with a `rebuilding` flag; transcription path skips chunks during reload. Prevents concurrent reloads from tearing each other down.
- [x] Voice activation phrase — configurable phrase (default: "faebot dearest") the streamer can say to guarantee a generation. Strips punctuation for Whisper compatibility.
- [x] Expand test coverage to `bot.py` and `server.py` — 100 tests total (44 core, 28 commands, 20 bot, 8 server). Coverage: commands.py 100%, core.py 94%, bot.py 73%, server.py 45%. Server gap is mostly `/ws/audio` (Whisper/VAD). `local.py` intentionally untested (orchestration glue best validated by running).
- [ ] `/ws/audio` WebSocket tests — deferred due to complexity of mocking Silero VAD + faster-whisper + audio byte streams + CUDA ThreadPoolExecutor. Would need proper integration test infrastructure or heavy model mocking. Lower priority than feature work.

Note: `core.py` is designed to work cleanly with asyncpg (Phase 5) — conversation management is already async and the dataclass is easy to hydrate from DB rows. For cross-platform shared memory (Phase 7), the DB is the right first bridge; the same PostgreSQL instance lets both Twitch and Discord bots share state without needing to share code.

## Phase 5: Database Integration
- [ ] PostgreSQL setup with asyncpg (port from Discord bot)
- [ ] Replace permalog.txt with structured DB logging (conversations, transcriptions)
- [ ] Conversation persistence across restarts
- [ ] Queryable history for training data collection

## Phase 6: Local Model Generation (KoboldCPP)
- [ ] KoboldCPP client on separate machine
- [ ] Fallback to OpenRouter when local model is unavailable
- [ ] Per-channel model selection

## Phase 7: Memory System
This is a significant sub-project spanning both Twitch and Discord bots.

- [x] Self-knowledge block — done in Phase 3; always-included in system prompt
- [ ] Research ready-made LLM memory solutions (mem0, MemGPT/Letta, Zep, LangChain memory modules)
- [ ] Short-term: current chatlog window (already done)
- [ ] Medium-term: per-user memory (regulars, their interests, past interactions) — requires DB
- [ ] Long-term: persistent channel facts, faebot's own history and development
- [ ] Shared memory layer across Twitch and Discord bots (faebot should know the same people across platforms)
- [ ] Retrieval strategy: RAG over stored memories vs. summarization vs. hybrid

## Phase 8: Custom Faebot Model
- [ ] Collect and curate training data (chat logs, voice transcriptions, streamer messages)
- [ ] Include streamer's own voice/messages so faebot sounds like faer sister
- [ ] Fine-tune a small base model (Mistral/Llama-class)
- [ ] Deploy via KoboldCPP, use across both Twitch and Discord bots

## Phase 9: Text-to-Speech
- [ ] Faebot speaks on stream (not just types in chat)
- [ ] Voice should feel consistent with faebot's personality
- [ ] Likely after custom model work so voice + personality are coherent

## Future Emote Improvements
- Fetch emote usability programmatically (e.g. `fetch_user_emotes` with faebot's token) rather than assuming tier "1000" + type "follower"
- Emote descriptions so the LLM can choose contextually appropriate emotes
- Tool-call emote selection based on emotional context
- Periodic emote refresh for long-running instances
- Third-party emote providers (7TV, BTTV, FFZ)
