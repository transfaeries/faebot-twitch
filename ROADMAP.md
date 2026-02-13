# Faebot Twitch - Roadmap

## Phase 1: Quick Wins (Done)
- [x] Programmatic emote sourcing via Twitch API
- [x] Filter to tier 1 + follower emotes only
- [x] Fix stream category injection

## Phase 2: Voice Integration (Next Priority)
- [ ] Combined entry point (FastAPI + TwitchIO in one async process)
- [ ] Feed voice transcriptions into conversation context
- [ ] Separate reply frequency for streamer voice vs chat messages

## Phase 3: Code Quality & Resilience
- [ ] Linting fixes + type hints
- [ ] Retry logic for API calls
- [ ] Graceful shutdown
- [ ] Basic test suite

## Phase 4: Database Integration
- [ ] PostgreSQL setup with asyncpg
- [ ] Conversation persistence across restarts
- [ ] Voice transcription logging

## Phase 5: Local Model Generation (KoboldCPP)
- [ ] KoboldCPP client on separate machine
- [ ] Fallback to OpenRouter when local model is unavailable
- [ ] Per-channel model selection

## Phase 6: Feature Improvements
- [ ] Better prompt system (placeholder-based, admin-editable, persistent)
- [ ] Richer context in messages (timestamps, reply tracking)
- [ ] Dev environment support

## Future Emote Improvements
- Emote descriptions so the LLM can choose contextually appropriate emotes
- Tool-call emote selection based on emotional context
- Periodic emote refresh for long-running instances
- Third-party emote providers (7TV, BTTV, FFZ)
