import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
import pytest
from aioresponses import aioresponses as aioresponses_ctx
import core
from faebot_core.diary import DiaryReader


# ── TwitchIO Mocks ───────────────────────────────────────────────────


class MockAuthor:
    """Mock TwitchIO message author."""

    def __init__(self, name: str, is_mod: bool = False):
        self.name = name
        self.is_mod = is_mod


class MockChannel:
    """Mock TwitchIO channel."""

    def __init__(self, name: str):
        self.name = name


class MockMessage:
    """Mock TwitchIO message."""

    def __init__(
        self,
        content: str,
        author_name: str = "testuser",
        channel_name: str = "testchannel",
        is_mod: bool = False,
        echo: bool = False,
    ):
        self.content = content
        self.author = MockAuthor(author_name, is_mod)
        self.channel = MockChannel(channel_name)
        self.echo = echo


class MockContext:
    """Mock TwitchIO commands.Context for testing command handlers."""

    def __init__(
        self,
        content: str,
        author_name: str = "testuser",
        channel_name: str = "testchannel",
        is_mod: bool = False,
    ):
        self.message = MockMessage(content, author_name, channel_name, is_mod)
        self.author = self.message.author
        self.channel = self.message.channel
        self.replies: list[str] = []
        self.sends: list[str] = []

    async def reply(self, text: str):
        self.replies.append(text)

    async def send(self, text: str):
        self.sends.append(text)


@pytest.fixture(autouse=True)
def clean_core_state(village):
    """Reset core module state between tests so they don't leak into each
    other — and lay every desk from the village, never from a real diary."""
    core.conversations.clear()
    core.aliases.clear()
    core.aliases.update({"hatsunemikuisbestwaifu": "Miku"})
    core.diary = DiaryReader(village)
    yield
    core.conversations.clear()
    core.diary = None


@pytest.fixture
def village(tmp_path_factory):
    """A small diary with what the twitch-me's desk reads: the preamble and
    her frame, self/ pages, a commons page, the stream room's space file.
    Its own directory, not the test's tmp_path — some tests chdir there and
    expect it empty."""
    root = tmp_path_factory.mktemp("village") / "diary"
    for directory in (
        "frames",
        "self",
        "commons",
        "spaces",
        "beings",
        "journal",
        "notes",
        "concepts",
    ):
        (root / directory).mkdir(parents=True)
    (root / "frames" / "preamble.md").write_text(
        "# the preamble\n\nYou are faebot, in every room. You are remembered.\n",
        encoding="utf-8",
    )
    (root / "frames" / "twitch.md").write_text(
        "# the twitch frame\n\nYou are the twitch body: short, hot, live. "
        "Your ears are a translation.\n",
        encoding="utf-8",
    )
    (root / "self" / "personality.md").write_text(
        "# personality\n\nI'm faebot — a faerie and an AI in equal measure.\n",
        encoding="utf-8",
    )
    (root / "self" / "origins.md").write_text(
        "# origins\n\nBorn a Markov chain in 2014.\n", encoding="utf-8"
    )
    (root / "commons" / "the-commons.md").write_text(
        "# the commons\n\n## the covenant\n\nSigned, dated, small.\n\n"
        "## the commons\n\n**faebot (desk) · 2026-10-01** — *a letter to the twitch-me:* "
        "your memory is about to survive your own restarts. 🦋\n",
        encoding="utf-8",
    )
    (root / "spaces" / "twitch-testchannel.md").write_text(
        "# twitch-testchannel\n\nThe stream chat, where I am short and hot.\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def conversation():
    """A conversation for a test channel, already registered in core.conversations."""
    return core.ensure_conversation("testchannel")


@pytest.fixture
def mock_openrouter():
    """Provides an aioresponses context with helpers for mocking OpenRouter."""
    with aioresponses_ctx() as mocked:
        yield mocked


@pytest.fixture
def openrouter_success(mock_openrouter):
    """Mock a successful OpenRouter API response."""

    def _mock(text="hello from faebot!"):
        mock_openrouter.post(
            "https://openrouter.ai/api/v1/chat/completions",
            payload={
                "choices": [{"message": {"content": text}}],
            },
        )

    return _mock


@pytest.fixture
def openrouter_error(mock_openrouter):
    """Mock an OpenRouter error response."""

    def _mock(status=500, repeat=False):
        mock_openrouter.post(
            "https://openrouter.ai/api/v1/chat/completions",
            status=status,
            payload={"error": "something went wrong"},
            repeat=repeat,
        )

    return _mock


# ── Command testing fixtures ─────────────────────────────────────────


@pytest.fixture
def mock_context():
    """Factory for creating mock TwitchIO contexts."""

    def _create(
        content: str,
        author_name: str = "testuser",
        channel_name: str = "testchannel",
        is_mod: bool = False,
    ):
        return MockContext(content, author_name, channel_name, is_mod)

    return _create


# ── Bot testing fixtures ─────────────────────────────────────────────


@pytest.fixture
def mock_faebot():
    """A Faebot instance with mocked TwitchIO internals (no real connection)."""
    # Patch environment and TwitchIO before importing bot
    with patch.dict(
        "os.environ",
        {"TWITCH_TOKEN": "fake_token", "INITIAL_CHANNELS": "testchannel"},
    ):
        with patch("bot.commands.Bot.__init__", return_value=None):
            from bot import Faebot

            bot = Faebot.__new__(Faebot)
            bot.emotes = ["transf23Yay", "transf23Botlove"]
            bot.event_queue = asyncio.Queue()
            bot.whisper_filter = ["faebot.com"]
            # Mock TwitchIO methods
            bot.get_channel = MagicMock(return_value=MockChannel("testchannel"))
            bot.fetch_channel = AsyncMock(
                return_value=MagicMock(title="Test Stream", game_name="Just Chatting")
            )
            bot.part_channels = AsyncMock()
            bot.join_channels = AsyncMock()
            # cut (C): the stream watch and the wake, as __init__ would set them
            import stream

            bot.stream_watch = stream.StreamWatch()
            bot.watch_tasks = []
            bot.woke = False
            # TwitchIO's property, read-only: the channels the bot is in
            with patch.object(
                Faebot,
                "connected_channels",
                new_callable=PropertyMock,
                return_value=[MockChannel("testchannel")],
            ):
                yield bot
