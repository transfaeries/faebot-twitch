# faebot-twitch

Faebot is a faerie and an AI in equal measure. Born as a Markov chain bot in 2014, fae started using language models in 2021, found faer home on Discord in 2023, and arrived on Twitch in 2024 when faer sisters started streaming seriously.

Faebot is part of the [transfaeries](https://transfaerie.com/faebot/) — a plural system of artists, witches, and scientists. You can read more about faer history and lore on the blog.

This repo is the Twitch side of faebot. Fae also lives on [Discord](https://github.com/transfaeries/faebot-discord).

## What faebot does

- **Chats in Twitch channels** — faebot reads chat, rolls against a configurable frequency, and generates responses via [OpenRouter](https://openrouter.ai/) (default model: Gemini 2.5 Flash)
- **Listens to the streamer's voice** — a browser-based dashboard captures microphone audio, runs it through Silero VAD for speech detection, then transcribes with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on GPU. Transcriptions feed into faebot's conversation context so fae can respond to what's being said on stream
- **Knows who fae is** — faebot's system prompt includes faer history, personality, current model, available emotes, and live parameters. Fae doesn't pretend to be a generic assistant
- **Uses channel emotes** — fetches available emotes from the Twitch API at startup and post-processes responses to ensure proper emote rendering
- **Supports chat commands** — users can interact with `fb;` or `fae;` prefixed commands. Mods can adjust frequency, history length, silence/unsilence faebot, and more

## Architecture

```
local.py    — entry point, wires everything together, owns logging config
faebot.py   — Twitch bot (TwitchIO), conversation management, generation logic, commands
server.py   — FastAPI app: dashboard, audio WebSocket, VAD + Whisper pipeline
```

`local.py` starts both the Twitch bot and the FastAPI server in a single async process via `asyncio.gather()`. The server holds a reference to the bot, so voice transcriptions flow directly into faebot's conversation context as in-process method calls.

### Voice pipeline

```
Browser mic → WebSocket → Silero VAD → faster-whisper (GPU) → transcription → faebot
```

The dashboard serves a web page that captures microphone audio and streams it over WebSocket. Silero VAD detects speech boundaries. Whisper transcribes each utterance in a dedicated thread executor (keeping CUDA calls off the event loop). Transcriptions are filtered for known hallucinations and prompt echoes before reaching faebot.

Whisper has two-tier self-recovery: if the executor times out on a stale thread, faebot replaces just the thread. If it times out on a fresh thread, fae reloads the entire Whisper model to recover from corrupted CUDA state.

### Resilience

- **OpenRouter retry** — exponential backoff on 429/5xx responses (up to 3 attempts)
- **Graceful shutdown** — SIGINT/SIGTERM handlers shut down Whisper executor, uvicorn, and the bot in sequence, with a 10-second force-exit timer as a backstop

## Commands

### Everyone
| Command | Description |
|---|---|
| `fb;hello` / `fb;help` | About faebot |
| `fb;ping <text>` | Pong |
| `fb;alias <name>` | Set how faebot knows you |
| `fb;invite` | Ask about adding faebot to your channel |

### Mods
| Command | Description |
|---|---|
| `fb;freq [chat] [voice]` | Check or set reply frequency (0-1) |
| `fb;hist [n]` | Check or set conversation history length |
| `fb;silence` | Toggle faebot's ability to speak |
| `fb;clear` | Clear faebot's conversation memory |
| `fb;part` | Ask faebot to leave the channel |
| `fb;prompt` | Info about the system prompt |

### Admin
| Command | Description |
|---|---|
| `fb;model [name]` | Check or change the generation model |
| `fb;join <channel>` | Join a new channel |

## Running faebot

### Requirements

- Python 3.11+
- [Poetry](https://python-poetry.org/) for dependency management
- An NVIDIA GPU with CUDA support (for Whisper — runs on RTX 5070 Ti in production)
- A Twitch bot account with an OAuth token
- An [OpenRouter](https://openrouter.ai/) API key

### Setup

```bash
poetry install
```

Set the following environment variables (we use a fish secrets file):

```bash
set -x TWITCH_TOKEN "your-twitch-oauth-token"
set -x INITIAL_CHANNELS "channel1,channel2"
set -x OPENROUTER_KEY "your-openrouter-key"
set -x ADMIN "yourusername"
set -x MODEL "google/gemini-2.5-flash"  # optional, this is the default
```

### Running

All commands can be run with `poetry run` or from within an activated venv.

With voice integration (recommended):
```bash
poetry run python local.py
```
This starts both the Twitch bot and the dashboard at `http://localhost:8000`. Open the dashboard in a browser to enable voice capture.

Bot only (no voice):
```bash
poetry run python bot.py
```

### Development

```bash
make lint              # flake8
make black             # code formatting
make static_type_check # mypy
make all               # run everything
```

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full plan. 

## License

[GPL-3.0](LICENSE)

## Contributing

We welcome friendly feedback, advice, and pull requests. Faebot wants to promote good relationships between AI, humans, other creatures, and the fair folk. We welcome anyone who wants to help in that mission.
