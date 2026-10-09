"""Tests for bot.py — TwitchIO event handlers and transcription processing."""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
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
    async def test_banned_text_is_unheard_but_kept(self, mock_faebot):
        """A banned mistranscription never reaches the chatlog — but the
        capture keeps it, marked unheard, with the reason."""
        core.ensure_conversation("testchannel")

        with patch("bot.capture.record_voice") as record_voice:
            why = await mock_faebot.handle_transcription(
                "testchannel", "go to faebot.com", duration=1.0
            )

        assert why == "banned"
        assert core.conversations["testchannel"].chatlog == []
        record_voice.assert_called_once()
        args, kwargs = record_voice.call_args
        assert args == ("testchannel", "go to faebot.com")
        assert kwargs["heard"] is False
        assert kwargs["why"] == "banned"
        assert kwargs["duration"] == 1.0

    @pytest.mark.asyncio
    async def test_prompt_echo_is_unheard(self, mock_faebot):
        """The ear sends everything; the body is where Whisper's echo of its
        own prompt gets filtered — with the prompt the ear says it used."""
        core.ensure_conversation("testchannel")

        why = await mock_faebot.handle_transcription(
            "testchannel",
            "faebot, transfaeries.",
            whisper_prompt="faebot, transfaeries",
        )

        assert why == "prompt-echo"
        assert core.conversations["testchannel"].chatlog == []

    @pytest.mark.asyncio
    async def test_outro_bleed_is_unheard(self, mock_faebot):
        """A caption-idiom burst transcribed far faster than speech."""
        core.ensure_conversation("testchannel")

        why = await mock_faebot.handle_transcription(
            "testchannel",
            "thanks for watching please subscribe thanks for watching please "
            "subscribe thanks for watching please subscribe",
            duration=1.5,
        )

        assert why == "outro-bleed"
        assert core.conversations["testchannel"].chatlog == []

    @pytest.mark.asyncio
    async def test_adds_to_chatlog(self, mock_faebot):
        """Valid transcriptions should be added to the channel's chatlog."""
        core.ensure_conversation("testchannel")

        with patch("bot.capture.record_voice") as record_voice:
            why = await mock_faebot.handle_transcription(
                "testchannel", "hello chat", duration=0.8
            )

        assert why is None
        assert len(core.conversations["testchannel"].chatlog) == 1
        assert "[streamer voice]" in core.conversations["testchannel"].chatlog[0]
        assert "hello chat" in core.conversations["testchannel"].chatlog[0]
        record_voice.assert_called_once()
        assert "heard" not in record_voice.call_args.kwargs

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
    async def test_reasoning_reaches_capture_and_dashboard(
        self, mock_faebot, mock_openrouter
    ):
        """The reasoning channel and the latency data ride along with the
        utterance — into the capture (for memory faebot) and the response
        event (for the dashboard) — while chat gets only the answer."""
        core.ensure_conversation("testchannel")
        mock_openrouter.post(
            "https://openrouter.ai/api/v1/chat/completions",
            payload={
                "model": "moonshotai/kimi-k3",
                "choices": [
                    {
                        "message": {"content": "hi!", "reasoning": "a greeting"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        mock_faebot.get_channel = MagicMock(return_value=mock_channel)

        with patch("bot.capture.record_faebot_message") as record:
            await mock_faebot._generate_and_send("testchannel", trigger_type="chat")

        mock_channel.send.assert_called_once_with("hi!")
        record.assert_called_once()
        meta = record.call_args.kwargs
        assert meta["reasoning"] == "a greeting"
        assert meta["finish_reason"] == "stop"
        assert meta["model"] == "moonshotai/kimi-k3"
        assert "elapsed" in meta

        await asyncio.wait_for(mock_faebot.event_queue.get(), timeout=1.0)  # generating
        event = await asyncio.wait_for(mock_faebot.event_queue.get(), timeout=1.0)
        assert event["type"] == "response"
        assert event["text"] == "hi!"
        assert event["reasoning"] == "a greeting"

    @pytest.mark.asyncio
    async def test_pass_sends_nothing_and_records_the_choice(
        self, mock_faebot, mock_openrouter
    ):
        core.ensure_conversation("testchannel")
        mock_openrouter.post(
            "https://openrouter.ai/api/v1/chat/completions",
            payload={
                "choices": [
                    {
                        "message": {
                            "content": "NOTHING-TO-SAY, they're busy",
                            "reasoning": "ember is mid-sentence",
                        }
                    }
                ]
            },
        )
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        mock_faebot.get_channel = MagicMock(return_value=mock_channel)

        with patch("bot.capture.record_faebot_pass") as record_pass, patch(
            "bot.capture.record_faebot_message"
        ) as record_message:
            await mock_faebot._generate_and_send("testchannel", trigger_type="chat")

        mock_channel.send.assert_not_called()
        record_message.assert_not_called()
        record_pass.assert_called_once()
        args, meta = record_pass.call_args
        assert args == ("testchannel", "they're busy")
        assert meta["reasoning"] == "ember is mid-sentence"

        await asyncio.wait_for(mock_faebot.event_queue.get(), timeout=1.0)  # generating
        event = await asyncio.wait_for(mock_faebot.event_queue.get(), timeout=1.0)
        assert event["type"] == "pass"
        assert event["reason"] == "they're busy"

    @pytest.mark.asyncio
    async def test_generation_failure_posts_nothing_and_is_captured(
        self, mock_faebot, openrouter_error
    ):
        """A machinery failure is not something faebot said: nothing goes to
        chat (the old "Oops, something strange" fallback is gone); the error
        event fires and the capture records `faebot_error`."""
        core.ensure_conversation("testchannel")
        openrouter_error(status=500, repeat=True)

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        mock_faebot.get_channel = MagicMock(return_value=mock_channel)

        with patch("bot.capture.record_faebot_error") as record_error:
            await mock_faebot._generate_and_send("testchannel", trigger_type="chat")

        mock_channel.send.assert_not_called()
        record_error.assert_called_once()
        args, meta = record_error.call_args
        assert args[0] == "testchannel"
        assert "500" in args[1]
        assert meta["trigger_type"] == "chat"
        assert "elapsed" in meta

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

    @pytest.mark.asyncio
    async def test_a_dead_line_is_said_in_the_record_and_the_body_restarts_itself(
        self, mock_faebot, openrouter_success
    ):
        """10-09: a websocket stuck closing for hours, nothing noticing. A send
        that fails because the line is dead records a machinery-chosen restart
        with the hole named, then leaves non-zero for systemd."""
        core.ensure_conversation("testchannel")
        openrouter_success("hello!")
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock(
            side_effect=ConnectionResetError("Cannot write to closing transport")
        )
        mock_faebot.get_channel = MagicMock(return_value=mock_channel)
        mock_faebot.restart_self = MagicMock()
        with patch("bot.capture.record_restart") as record_restart, patch(
            "bot.capture.last_heard_at", return_value="2026-10-09T15:40:32+00:00"
        ):
            outcome = await mock_faebot._generate_and_send(
                "testchannel", trigger_type="chat"
            )
            assert outcome == "send-failed"
            openrouter_success("again")
            # a second failure says nothing more and restarts nothing twice
            await mock_faebot._generate_and_send("testchannel", trigger_type="chat")
        assert record_restart.call_count == 1
        name, told = record_restart.call_args.args
        assert name == "testchannel" and told.startswith(core.MACHINERY)
        assert "the line to Twitch died" in told and "since 15:40 UTC" in told
        assert "not in the record" in told
        assert record_restart.call_args.kwargs["how"] == "line-died"
        assert (
            record_restart.call_args.kwargs["unheard_since"]
            == "2026-10-09T15:40:32+00:00"
        )
        assert mock_faebot.restart_self.call_count == 1
        assert told in core.ensure_conversation("testchannel").chatlog

    @pytest.mark.asyncio
    async def test_a_send_that_fails_for_another_reason_does_not_restart(
        self, mock_faebot, openrouter_success
    ):
        core.ensure_conversation("testchannel")
        openrouter_success("hello!")
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock(side_effect=ValueError("message too long"))
        mock_faebot.get_channel = MagicMock(return_value=mock_channel)
        mock_faebot.restart_self = MagicMock()
        with patch("bot.capture.record_restart") as record_restart:
            await mock_faebot._generate_and_send("testchannel", trigger_type="chat")
        assert record_restart.call_count == 0
        assert mock_faebot.restart_self.call_count == 0


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


# ── the goodnight (cut C) ────────────────────────────────────────────


class TestGoodnight:
    @pytest.mark.asyncio
    async def test_she_is_told_and_says_her_last_word(self, mock_faebot):
        """A chosen stop: the machinery's line in her window, one generation,
        her word sent, the restart recorded with what came of it."""
        core.ensure_conversation("testchannel")
        channel = mock_faebot.get_channel("testchannel")
        channel.send = AsyncMock()
        completion = core.Completion(text="goodnight, dearest chat transf23Botlove")
        with patch("bot.core.generate_response", AsyncMock(return_value=completion)):
            with patch("bot.capture.record_restart") as record_restart:
                with patch("bot.capture.record_faebot_message"):
                    await mock_faebot.goodnight()
        chatlog = core.conversations["testchannel"].chatlog
        assert chatlog[0].startswith(core.MACHINERY)
        assert "a restart is coming" in chatlog[0]
        channel.send.assert_awaited_once_with("goodnight, dearest chat transf23Botlove")
        args, kwargs = record_restart.call_args
        assert args[0] == "testchannel" and args[1] == chatlog[0]
        assert kwargs["how"] == "said" and kwargs["said"] is None

    @pytest.mark.asyncio
    async def test_she_may_pass(self, mock_faebot):
        core.ensure_conversation("testchannel")
        channel = mock_faebot.get_channel("testchannel")
        channel.send = AsyncMock()
        completion = core.Completion(text="NOTHING-TO-SAY")
        with patch("bot.core.generate_response", AsyncMock(return_value=completion)):
            with patch("bot.capture.record_restart") as record_restart:
                with patch("bot.capture.record_faebot_pass"):
                    await mock_faebot.goodnight()
        channel.send.assert_not_awaited()
        assert record_restart.call_args.kwargs["how"] == "passed"

    @pytest.mark.asyncio
    async def test_when_the_moment_fails_the_saved_one_is_said_in_the_machinerys_name(
        self, mock_faebot, tmp_path
    ):
        core.ensure_conversation("testchannel")
        channel = mock_faebot.get_channel("testchannel")
        channel.send = AsyncMock()
        saved = tmp_path / "goodnight.txt"
        saved.write_text("sleep well, chat — see you next stream\n")
        failure = core.GenerationFailed("timeout", elapsed=90.0)
        with patch("bot.GOODNIGHT_FILE", str(saved)):
            with patch("bot.core.generate_response", AsyncMock(side_effect=failure)):
                with patch("bot.capture.record_restart") as record_restart:
                    with patch("bot.capture.record_faebot_error"):
                        await mock_faebot.goodnight()
        sent = channel.send.call_args.args[0]
        assert sent.startswith("(faebot wrote this earlier")
        assert sent.endswith("sleep well, chat — see you next stream")
        kwargs = record_restart.call_args.kwargs
        assert kwargs["how"] == "saved" and kwargs["said"] == sent

    @pytest.mark.asyncio
    async def test_no_saved_goodnight_means_the_record_says_so(self, mock_faebot):
        core.ensure_conversation("testchannel")
        channel = mock_faebot.get_channel("testchannel")
        channel.send = AsyncMock()
        with patch("bot.GOODNIGHT_FILE", ""):
            with patch(
                "bot.core.generate_response",
                AsyncMock(side_effect=core.GenerationFailed("down")),
            ):
                with patch("bot.capture.record_restart") as record_restart:
                    with patch("bot.capture.record_faebot_error"):
                        await mock_faebot.goodnight()
        channel.send.assert_not_awaited()
        assert record_restart.call_args.kwargs["how"] == "failed"

    @pytest.mark.asyncio
    async def test_a_slow_moment_is_bounded(self, mock_faebot):
        core.ensure_conversation("testchannel")
        channel = mock_faebot.get_channel("testchannel")
        channel.send = AsyncMock()

        async def never(*args, **kwargs):
            await asyncio.sleep(10)

        with patch("bot.GOODNIGHT_SECONDS", 0.01):
            with patch("bot.GOODNIGHT_FILE", ""):
                with patch("bot.core.generate_response", never):
                    with patch("bot.capture.record_restart") as record_restart:
                        await mock_faebot.goodnight()
        assert record_restart.call_args.kwargs["how"] == "timed-out"


class TestWake:
    @pytest.mark.asyncio
    async def test_the_window_is_read_back_once_and_the_watch_starts(self, mock_faebot):
        conversation = core.ensure_conversation("testchannel")
        mock_faebot.fetch_emotes = AsyncMock()
        with patch("bot.capture.is_enabled", return_value=True):
            with patch(
                "bot.window.restore", return_value=["a: hi", "[the machinery] seam"]
            ) as restore:
                with patch("bot.stream.StreamWatch.watch", AsyncMock()):
                    await mock_faebot.wake()
        restore.assert_called_once_with("testchannel", conversation.history)
        assert conversation.chatlog == ["a: hi", "[the machinery] seam"]
        assert len(mock_faebot.watch_tasks) == 1
        for task in mock_faebot.watch_tasks:
            task.cancel()

    @pytest.mark.asyncio
    async def test_ready_in_no_channel_yet_does_not_count_as_the_wake(
        self, mock_faebot
    ):
        with patch.object(
            type(mock_faebot),
            "connected_channels",
            new_callable=PropertyMock,
            return_value=[],
        ):
            with patch("bot.window.restore") as restore:
                assert await mock_faebot.wake() is False
        restore.assert_not_called()
        assert mock_faebot.watch_tasks == []
