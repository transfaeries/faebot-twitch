"""Tests for bot.py — TwitchIO event handlers and transcription processing."""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import core


# ── filter_transcription ─────────────────────────────────────────────


class TestFilterTranscription:
    def test_clean_text_passes_through(self, mock_faebot):
        """Clean text without banned strings should pass through unchanged."""
        result = mock_faebot.filter_transcription("hello everyone how are you")
        assert result == "hello everyone how are you"

    def test_banned_string_returns_none(self, mock_faebot):
        """Text containing banned strings should return None."""
        result = mock_faebot.filter_transcription("check out faebot.com for more")
        assert result is None

    def test_banned_string_case_insensitive(self, mock_faebot):
        """Banned string matching should be case insensitive."""
        result = mock_faebot.filter_transcription("visit FAEBOT.COM today")
        assert result is None


# ── handle_transcription ─────────────────────────────────────────────


class TestHandleTranscription:
    @pytest.mark.asyncio
    async def test_filtered_text_skipped(self, mock_faebot):
        """Transcriptions containing banned strings should be skipped entirely."""
        core.ensure_conversation("testchannel")

        await mock_faebot.handle_transcription("testchannel", "go to faebot.com")

        # Chatlog should be empty - nothing was added
        assert core.conversations["testchannel"].chatlog == []

    @pytest.mark.asyncio
    async def test_adds_to_chatlog(self, mock_faebot):
        """Valid transcriptions should be added to the channel's chatlog."""
        core.ensure_conversation("testchannel")

        await mock_faebot.handle_transcription("testchannel", "hello chat")

        assert len(core.conversations["testchannel"].chatlog) == 1
        assert "[streamer voice]" in core.conversations["testchannel"].chatlog[0]
        assert "hello chat" in core.conversations["testchannel"].chatlog[0]

    @pytest.mark.asyncio
    async def test_voice_activation_triggers_generation(self, mock_faebot):
        """Voice activation phrase should trigger generation."""
        core.ensure_conversation("testchannel")
        mock_faebot._generate_and_send = AsyncMock()

        with patch("bot.VOICE_ACTIVATION", "faebot dearest"):
            await mock_faebot.handle_transcription(
                "testchannel", "faebot dearest, what do you think?"
            )

        # Give the task a moment to be created
        await asyncio.sleep(0.01)
        mock_faebot._generate_and_send.assert_called_once()
        call_args = mock_faebot._generate_and_send.call_args
        assert call_args[0][0] == "testchannel"
        assert call_args[1]["trigger_type"] == "voice"

    @pytest.mark.asyncio
    async def test_name_mention_boosts_to_chat_frequency(self, mock_faebot):
        """Mentioning 'faebot' should boost to chat frequency."""
        conv = core.ensure_conversation("testchannel")
        conv.frequency = 0.5  # 50% chat frequency
        mock_faebot._generate_and_send = AsyncMock()

        # Patch random to return value that would trigger at 0.5 but not at voice_frequency
        with patch("core.random", return_value=0.3):
            await mock_faebot.handle_transcription(
                "testchannel", "hey faebot what's up"
            )

        await asyncio.sleep(0.01)
        mock_faebot._generate_and_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_random_voice_roll(self, mock_faebot):
        """Normal voice should use voice_frequency for roll."""
        conv = core.ensure_conversation("testchannel")
        conv.voice_frequency = 0.1
        mock_faebot._generate_and_send = AsyncMock()

        # Roll of 0.05 should trigger at voice_frequency 0.1
        with patch("core.random", return_value=0.05):
            await mock_faebot.handle_transcription(
                "testchannel", "just talking about stuff"
            )

        await asyncio.sleep(0.01)
        mock_faebot._generate_and_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_voice_roll_can_fail(self, mock_faebot):
        """Voice roll should not always trigger."""
        conv = core.ensure_conversation("testchannel")
        conv.voice_frequency = 0.1
        mock_faebot._generate_and_send = AsyncMock()

        # Roll of 0.5 should NOT trigger at voice_frequency 0.1
        with patch("core.random", return_value=0.5):
            await mock_faebot.handle_transcription(
                "testchannel", "just talking about stuff"
            )

        await asyncio.sleep(0.01)
        mock_faebot._generate_and_send.assert_not_called()


# ── _generate_and_send ───────────────────────────────────────────────


class TestGenerateAndSend:
    @pytest.mark.asyncio
    async def test_success_emits_response_event(self, mock_faebot, openrouter_success):
        """Successful generation should emit a response event."""
        core.ensure_conversation("testchannel")
        openrouter_success("hello from faebot!")

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        mock_faebot.get_channel = MagicMock(return_value=mock_channel)

        await mock_faebot._generate_and_send("testchannel", trigger_type="chat")

        # Check channel.send was called
        mock_channel.send.assert_called_once()

        # Check response event was emitted
        event = await asyncio.wait_for(mock_faebot.event_queue.get(), timeout=1.0)
        assert event["type"] == "generating"

        event = await asyncio.wait_for(mock_faebot.event_queue.get(), timeout=1.0)
        assert event["type"] == "response"
        assert event["channel"] == "testchannel"

    @pytest.mark.asyncio
    async def test_generation_failure_emits_error_and_fallback(
        self, mock_faebot, openrouter_error
    ):
        """Generation failure should emit error event and send fallback message."""
        core.ensure_conversation("testchannel")
        openrouter_error(status=500, repeat=True)

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        mock_faebot.get_channel = MagicMock(return_value=mock_channel)

        await mock_faebot._generate_and_send("testchannel", trigger_type="chat")

        # Check fallback message was sent
        mock_channel.send.assert_called()
        fallback_call = mock_channel.send.call_args
        assert (
            "oops" in fallback_call[0][0].lower()
            or "strange" in fallback_call[0][0].lower()
        )

        # Check error event was emitted (skip the generating event)
        event = await asyncio.wait_for(mock_faebot.event_queue.get(), timeout=1.0)
        assert event["type"] == "generating"

        event = await asyncio.wait_for(mock_faebot.event_queue.get(), timeout=1.0)
        assert event["type"] == "error"

    @pytest.mark.asyncio
    async def test_send_failure_emits_error_event(
        self, mock_faebot, openrouter_success
    ):
        """Send failure should emit error event with same generation_id."""
        core.ensure_conversation("testchannel")
        openrouter_success("hello!")

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock(side_effect=Exception("Connection lost"))
        mock_faebot.get_channel = MagicMock(return_value=mock_channel)

        await mock_faebot._generate_and_send("testchannel", trigger_type="chat")

        # Skip generating event
        event = await asyncio.wait_for(mock_faebot.event_queue.get(), timeout=1.0)
        assert event["type"] == "generating"
        generation_id = event["id"]

        # Check error event with matching id
        event = await asyncio.wait_for(mock_faebot.event_queue.get(), timeout=1.0)
        assert event["type"] == "error"
        assert event["id"] == generation_id
        assert "send failed" in event["error"].lower()


# ── event_message ────────────────────────────────────────────────────


class TestEventMessage:
    @pytest.mark.asyncio
    async def test_echo_ignored(self, mock_faebot):
        """Echo messages (from the bot itself) should be ignored."""
        from tests.conftest import MockMessage

        message = MockMessage("hello", echo=True)
        mock_faebot._generate_and_send = AsyncMock()
        mock_faebot.handle_commands = AsyncMock()

        await mock_faebot.event_message(message)

        mock_faebot._generate_and_send.assert_not_called()
        mock_faebot.handle_commands.assert_not_called()

    @pytest.mark.asyncio
    async def test_command_prefix_routed_to_handler(self, mock_faebot):
        """Messages with command prefixes should be routed to handle_commands."""
        from tests.conftest import MockMessage

        message = MockMessage("fae;hello")
        mock_faebot.handle_commands = AsyncMock()

        await mock_faebot.event_message(message)

        mock_faebot.handle_commands.assert_called_once_with(message)

    @pytest.mark.asyncio
    async def test_fb_prefix_routed(self, mock_faebot):
        """fb; prefix should also route to handle_commands."""
        from tests.conftest import MockMessage

        message = MockMessage("fb;ping")
        mock_faebot.handle_commands = AsyncMock()

        await mock_faebot.event_message(message)

        mock_faebot.handle_commands.assert_called_once_with(message)

    @pytest.mark.asyncio
    async def test_bang_prefix_routed(self, mock_faebot):
        """! prefix should also route to handle_commands."""
        from tests.conftest import MockMessage

        message = MockMessage("!command")
        mock_faebot.handle_commands = AsyncMock()

        await mock_faebot.event_message(message)

        mock_faebot.handle_commands.assert_called_once_with(message)

    @pytest.mark.asyncio
    async def test_name_mention_always_replies(self, mock_faebot):
        """Mentioning 'faebot' should always trigger a reply (frequency=1.0)."""
        from tests.conftest import MockMessage

        core.ensure_conversation("testchannel")
        message = MockMessage("hey faebot what do you think?")
        mock_faebot._generate_and_send = AsyncMock()
        mock_faebot.handle_commands = AsyncMock()

        await mock_faebot.event_message(message)

        await asyncio.sleep(0.01)
        mock_faebot._generate_and_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_normal_message_respects_frequency(self, mock_faebot):
        """Normal messages should use channel frequency for roll."""
        from tests.conftest import MockMessage

        conv = core.ensure_conversation("testchannel")
        conv.frequency = 0.1
        message = MockMessage("just chatting about stuff")
        mock_faebot._generate_and_send = AsyncMock()
        mock_faebot.handle_commands = AsyncMock()

        # Roll 0.5 should not trigger at frequency 0.1
        with patch("core.random", return_value=0.5):
            await mock_faebot.event_message(message)

        await asyncio.sleep(0.01)
        mock_faebot._generate_and_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_added_to_chatlog(self, mock_faebot):
        """Messages should be added to the channel's chatlog."""
        from tests.conftest import MockMessage

        conv = core.ensure_conversation("testchannel")
        message = MockMessage("hello everyone", author_name="someuser")
        mock_faebot.handle_commands = AsyncMock()

        # Ensure we don't trigger generation
        with patch("core.random", return_value=0.99):
            await mock_faebot.event_message(message)

        assert len(conv.chatlog) == 1
        assert "someuser: hello everyone" in conv.chatlog[0]

    @pytest.mark.asyncio
    async def test_alias_used_in_chatlog(self, mock_faebot):
        """If user has an alias, it should be used in chatlog."""
        from tests.conftest import MockMessage

        core.aliases["hatsunemikuisbestwaifu"] = "Miku"
        conv = core.ensure_conversation("testchannel")
        message = MockMessage("hello!", author_name="hatsunemikuisbestwaifu")
        mock_faebot.handle_commands = AsyncMock()

        with patch("core.random", return_value=0.99):
            await mock_faebot.event_message(message)

        assert "Miku: hello!" in conv.chatlog[0]
