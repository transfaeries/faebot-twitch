"""Tests for core.py — faebot's brain, no platform dependencies needed."""

import pytest
from unittest.mock import patch
from aioresponses import aioresponses as aioresponses_ctx

import core


# ── ensure_conversation ──────────────────────────────────────────────


class TestEnsureConversation:
    def test_creates_new_conversation(self):
        conv = core.ensure_conversation("newchannel")
        assert conv.channel == "newchannel"
        assert "newchannel" in core.conversations

    def test_returns_existing_conversation(self):
        first = core.ensure_conversation("samechannel")
        first.frequency = 0.99
        second = core.ensure_conversation("samechannel")
        assert first is second
        assert second.frequency == 0.99

    def test_defaults(self):
        conv = core.ensure_conversation("defaults")
        assert conv.frequency == 0.1
        assert conv.voice_frequency == 0.05
        assert conv.history == 20
        assert conv.silenced is False
        assert conv.chatlog == []


# ── choose_to_reply ──────────────────────────────────────────────────


class TestChooseToReply:
    def test_silenced_never_replies(self, conversation):
        conversation.silenced = True
        assert core.choose_to_reply("testchannel", 1.0) is False

    def test_zero_frequency_never_replies(self, conversation):
        assert core.choose_to_reply("testchannel", 0.0) is False

    def test_negative_frequency_never_replies(self, conversation):
        assert core.choose_to_reply("testchannel", -0.5) is False

    def test_frequency_1_always_replies(self, conversation):
        assert core.choose_to_reply("testchannel", 1.0) is True

    def test_frequency_above_1_always_replies(self, conversation):
        assert core.choose_to_reply("testchannel", 5.0) is True

    @patch("core.random", return_value=0.05)
    def test_roll_below_frequency_replies(self, mock_random, conversation):
        assert core.choose_to_reply("testchannel", 0.1) is True

    @patch("core.random", return_value=0.5)
    def test_roll_above_frequency_skips(self, mock_random, conversation):
        assert core.choose_to_reply("testchannel", 0.1) is False

    @patch("core.random", return_value=0.1)
    def test_roll_equal_to_frequency_skips(self, mock_random, conversation):
        """Edge case: roll == frequency should NOT reply (strict less-than)."""
        assert core.choose_to_reply("testchannel", 0.1) is False


# ── fix_emote_spacing ────────────────────────────────────────────────


class TestFixEmoteSpacing:
    def test_no_emotes_returns_unchanged(self):
        assert core.fix_emote_spacing("hello world", []) == "hello world"

    def test_emote_gets_padded(self):
        result = core.fix_emote_spacing("hiTransf23Botlovebye", ["Transf23Botlove"])
        assert result == "hi Transf23Botlove bye"

    def test_already_spaced_emote_stays_clean(self):
        result = core.fix_emote_spacing("hi Transf23Botlove bye", ["Transf23Botlove"])
        assert result == "hi Transf23Botlove bye"

    def test_multiple_emotes(self):
        emotes = ["transf23Yay", "transf23Botlove"]
        result = core.fix_emote_spacing(
            "transf23Yayhello transf23Botlove", emotes
        )
        assert "transf23Yay" in result
        assert "transf23Botlove" in result
        # Each emote should be space-separated from surrounding text
        assert "transf23Yay hello" in result or "transf23Yay hello" in result

    def test_longer_emote_matched_first(self):
        """transf23Fluttering should match before transf23Flutter."""
        emotes = ["transf23Flutter", "transf23Fluttering"]
        result = core.fix_emote_spacing("transf23Fluttering", emotes)
        assert result == "transf23Fluttering"

    def test_no_double_spaces(self):
        result = core.fix_emote_spacing(
            " transf23Yay  transf23Yay ", ["transf23Yay"]
        )
        assert "  " not in result


# ── build_system_prompt ──────────────────────────────────────────────


class TestBuildSystemPrompt:
    def test_includes_channel_name(self, conversation):
        prompt = core.build_system_prompt(
            conversation, "testchannel", "Test Stream", "Art", ["emote1"]
        )
        assert "testchannel" in prompt

    def test_includes_stream_context(self, conversation):
        prompt = core.build_system_prompt(
            conversation, "testchannel", "Making Art", "Art", []
        )
        assert "Making Art" in prompt
        assert "Art" in prompt

    def test_includes_model_name(self, conversation):
        conversation.model = "some-model/v1"
        prompt = core.build_system_prompt(
            conversation, "testchannel", "Title", "Game", []
        )
        assert "some-model/v1" in prompt

    def test_includes_frequency_as_percentage(self, conversation):
        conversation.frequency = 0.15
        conversation.voice_frequency = 0.05
        prompt = core.build_system_prompt(
            conversation, "testchannel", "Title", "Game", []
        )
        assert "15%" in prompt
        assert "5%" in prompt

    def test_includes_emotes(self, conversation):
        prompt = core.build_system_prompt(
            conversation, "testchannel", "Title", "Game",
            ["transf23Botlove", "transf23Yay"],
        )
        assert "transf23Botlove" in prompt
        assert "transf23Yay" in prompt

    def test_includes_history_length(self, conversation):
        conversation.history = 42
        prompt = core.build_system_prompt(
            conversation, "testchannel", "Title", "Game", []
        )
        assert "42" in prompt


# ── generate (OpenRouter API) ────────────────────────────────────────


class TestGenerate:
    @pytest.mark.asyncio
    async def test_successful_generation(self, openrouter_success):
        openrouter_success("test response")
        result = await core.generate(
            prompt="hello", system_prompt="you are faebot"
        )
        assert result == "test response"
        await core.close_session()

    @pytest.mark.asyncio
    async def test_non_retryable_error_returns_fallback(self):
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                status=403,
                payload={"error": "forbidden"},
            )
            result = await core.generate(prompt="hello")
            assert "couldn't generate" in result.lower()
            await core.close_session()

    @pytest.mark.asyncio
    async def test_retries_on_500_then_succeeds(self):
        with aioresponses_ctx() as mocked:
            # First call: 500, second call: success
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                status=500,
                payload={"error": "server error"},
            )
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "recovered!"}}]},
            )
            result = await core.generate(prompt="hello")
            assert result == "recovered!"
            await core.close_session()

    @pytest.mark.asyncio
    async def test_retries_on_429_rate_limit(self):
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                status=429,
                payload={"error": "rate limited"},
            )
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "after rate limit"}}]},
            )
            result = await core.generate(prompt="hello")
            assert result == "after rate limit"
            await core.close_session()

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises(self, openrouter_error):
        openrouter_error(status=500, repeat=True)
        with pytest.raises(Exception, match="failed after 3 attempts"):
            await core.generate(prompt="hello")
        await core.close_session()

    @pytest.mark.asyncio
    async def test_malformed_response_returns_fallback(self):
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"unexpected": "format"},
            )
            result = await core.generate(prompt="hello")
            assert "couldn't generate" in result.lower()
            await core.close_session()


# ── generate_response (full pipeline) ────────────────────────────────


class TestGenerateResponse:
    @pytest.mark.asyncio
    async def test_returns_response_and_appends_to_chatlog(self, conversation):
        conversation.chatlog.append("viewer: hello faebot!")
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "hi there!"}}]},
            )
            result = await core.generate_response(
                "testchannel", stream_title="Test", game_name="Art"
            )
        assert result == "hi there!"
        assert "faebot: hi there!" in conversation.chatlog
        await core.close_session()

    @pytest.mark.asyncio
    async def test_trims_chatlog_to_history_length(self, conversation):
        conversation.history = 5
        conversation.chatlog = [f"msg{i}" for i in range(10)]
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "reply"}}]},
            )
            await core.generate_response("testchannel")
        # 5 kept from trim + 1 appended response
        assert len(conversation.chatlog) == 6
        await core.close_session()

    @pytest.mark.asyncio
    async def test_long_response_gets_trimmed(self, conversation):
        long_text = "a" * 600
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": long_text}}]},
            )
            result = await core.generate_response("testchannel")
        assert len(result) == 500
        assert result.endswith("\u2013")
        await core.close_session()

    @pytest.mark.asyncio
    async def test_emotes_get_spaced(self, conversation):
        emotes = ["transf23Botlove"]
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={
                    "choices": [
                        {"message": {"content": "hitransf23Botlovebye"}}
                    ]
                },
            )
            result = await core.generate_response(
                "testchannel", emotes=emotes
            )
        assert result == "hi transf23Botlove bye"
        await core.close_session()

    @pytest.mark.asyncio
    @patch("core.permalog")
    async def test_permalog_gets_called(self, mock_permalog, conversation):
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "logged"}}]},
            )
            await core.generate_response("testchannel")
        assert mock_permalog.call_count >= 2  # params + response
        await core.close_session()
