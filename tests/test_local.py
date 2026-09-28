"""Tests for local.py — how the process ends when it can't run."""

import pytest

import local


@pytest.mark.asyncio
async def test_a_body_without_a_token_exits_as_a_failure(monkeypatch):
    """A supervisor restarts on failure; a missing token must read as one,
    not as a clean stop."""
    monkeypatch.delenv("TWITCH_TOKEN", raising=False)
    with pytest.raises(SystemExit) as stopped:
        await local.main("body")
    assert stopped.value.code == 1
