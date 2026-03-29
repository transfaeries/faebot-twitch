import pytest
from aioresponses import aioresponses as aioresponses_ctx
import core


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
