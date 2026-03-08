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
- [ ] Run Whisper transcription in executor (unblock event loop during transcription)
- [ ] Retry logic for API calls
- [ ] Graceful shutdown — currently Ctrl+C produces a TwitchIO `WSConnection._task_callback` traceback; fix is to intercept SIGINT before asyncio cancels tasks and shut down bot + uvicorn in sequence

## Phase 4: Architecture Refactor & Dashboard
The dashboard is blind to generation — can't see what prompt was used or what faebot sent. Fixing this requires splitting `faebot.py` into clean modules first. Full design notes in snippets/architecture-refactor.md (not versioned).

Do these in order — each step is independently shippable and the bot keeps working throughout:

- [ ] Extract `core.py` — move `Conversation`, `conversations`, `generate_response`, `generate`, `choose_to_reply` out of `faebot.py`. No TwitchIO or FastAPI deps in `core`. Both `faebot.py` and `server.py` import from it. This is the prerequisite for everything else.
- [ ] Test suite — write against `core.py` now that it has no platform deps. Tests act as a contract for the remaining refactor steps.
- [ ] Add event queue — `core.generate_response` puts events (`generating`, `response`, `error`) on an `asyncio.Queue` injected by `local.py`. Both bot and server share the same queue.
- [ ] Dashboard event WebSocket — `server.py` gains `/ws/events`; drains the queue and pushes to browser. Dashboard renders: generation indicator, response card with collapsible prompt/system prompt inspector.
- [ ] Extract `commands.py` — move all `fb;`/`fae;` command handlers to a `FaebotCommands` mixin. `Faebot` inherits from both `commands.Bot` and `FaebotCommands`. `faebot.py` becomes thin event wiring only.

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
- Emote descriptions so the LLM can choose contextually appropriate emotes
- Tool-call emote selection based on emotional context
- Periodic emote refresh for long-running instances
- Third-party emote providers (7TV, BTTV, FFZ)
