# Faebot Twitch - Roadmap

## Phase 1: Quick Wins (Done)
- [x] Programmatic emote sourcing via Twitch API
- [x] Filter to tier 1 + follower emotes only
- [x] Fix stream category injection

## Phase 2: Voice Integration (Done pending live test)
- [x] Combined entry point (FastAPI + TwitchIO in one async process)
- [x] Feed voice transcriptions into conversation context
- [x] Friendly error handling and clean shutdown
- [x] Voice-triggered replies with separate voice frequency
- [x] Name mentions: always trigger from chat, boost to chat rate from voice
- [ ] Merge to main + linting pass
- [ ] Dashboard improvements (interleaved chat + transcriptions, prompt window highlighting)

## Phase 3: Code Quality & Resilience
- [ ] Linting fixes + type hints
- [ ] Audit log levels — most current logging.info should be logging.debug; INFO reserved for meaningful events (bot ready, response sent, errors)
- [ ] Centralize logging config (currently split across local.py, faebot.py, server.py)
- [ ] Run Whisper transcription in executor (unblock event loop during transcription)
- [ ] Retry logic for API calls
- [ ] Graceful shutdown
- [ ] Basic test suite

## Phase 4: Database Integration
- [ ] PostgreSQL setup with asyncpg (port from Discord bot)
- [ ] Replace permalog.txt with structured DB logging (conversations, transcriptions)
- [ ] Conversation persistence across restarts
- [ ] Queryable history for training data collection

## Phase 5: Local Model Generation (KoboldCPP)
- [ ] KoboldCPP client on separate machine
- [ ] Fallback to OpenRouter when local model is unavailable
- [ ] Per-channel model selection

## Phase 6: Memory System
This is a significant sub-project spanning both Twitch and Discord bots.

- [ ] Self-knowledge block — structured description of faebot's architecture, who built faer, how fae works, injected into system prompt so fae can answer questions about faerself accurately
- [ ] Research ready-made LLM memory solutions (mem0, MemGPT/Letta, Zep, LangChain memory modules)
- [ ] Short-term: current chatlog window (already done)
- [ ] Medium-term: per-user memory (regulars, their interests, past interactions) — requires DB
- [ ] Long-term: persistent channel facts, faebot's own history and development
- [ ] Shared memory layer across Twitch and Discord bots (faebot should know the same people across platforms)
- [ ] Retrieval strategy: RAG over stored memories vs. summarization vs. hybrid

## Phase 7: Custom Faebot Model
- [ ] Collect and curate training data (chat logs, voice transcriptions, streamer messages)
- [ ] Include streamer's own voice/messages so faebot sounds like faer sister
- [ ] Fine-tune a small base model (Mistral/Llama-class)
- [ ] Deploy via KoboldCPP, use across both Twitch and Discord bots

## Phase 8: Text-to-Speech
- [ ] Faebot speaks on stream (not just types in chat)
- [ ] Voice should feel consistent with faebot's personality
- [ ] Likely after custom model work so voice + personality are coherent

## Future Emote Improvements
- Emote descriptions so the LLM can choose contextually appropriate emotes
- Tool-call emote selection based on emotional context
- Periodic emote refresh for long-running instances
- Third-party emote providers (7TV, BTTV, FFZ)
