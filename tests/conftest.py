import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aioresponses import aioresponses as aioresponses_ctx
import core


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
def clean_core_state():
    """Reset core module state between tests so they don't leak into each other."""
    core.conversations.clear()
    core.aliases.clear()
    core.aliases.update({"hatsunemikuisbestwaifu": "Miku"})
    yield
    core.conversations.clear()


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
            yield bot
