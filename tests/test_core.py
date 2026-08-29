"""Tests for core.py — faebot's brain, no platform dependencies needed."""

import asyncio
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
        """Defaults come from the env-readable module constants."""
        conv = core.ensure_conversation("defaults")
        assert conv.frequency == core.FREQUENCY
        assert conv.voice_frequency == core.VOICE_FREQUENCY
        assert conv.history == core.HISTORY
        assert conv.model == core.MODEL
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
        result = core.fix_emote_spacing("transf23Yayhello transf23Botlove", emotes)
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
        result = core.fix_emote_spacing(" transf23Yay  transf23Yay ", ["transf23Yay"])
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
            conversation,
            "testchannel",
            "Title",
            "Game",
            ["transf23Botlove", "transf23Yay"],
        )
        assert "transf23Botlove" in prompt
        assert "transf23Yay" in prompt

    def test_includes_history_length(self, conversation):
        conversation.history = 42
        prompt = core.build_system_prompt(
            conversation, "testchannel", "Title", "Game", []
        )
        assert "between the last 34 and 42 messages" in prompt

    @pytest.mark.parametrize("limit,floor", [(50, 40), (10, 8), (4, 4)])
    def test_history_floor_drops_a_fifth(self, limit, floor):
        assert core.history_floor(limit) == floor


# ── the whisper prompt echo ──────────────────────────────────────────


class TestIsPromptEcho:
    PROMPT = "faebot, transfaeries"

    @pytest.mark.parametrize(
        "text",
        [
            "faebot, transfaeries",
            "faebot, transfaeries  faebot, transfaeries",
            "Faebot.",
            "faebot!",
            "and faebot, transfaeries",
            "the faebot transfaeries",
            "",
            "...",
        ],
    )
    def test_echoes(self, text):
        assert core.is_prompt_echo(text, self.PROMPT)

    @pytest.mark.parametrize(
        "text",
        [
            "hello faebot, can you hear me?",
            "faebot dearest",
            "thank you faebot",
            "I love speedruns, faebot",
            "and guess who else is back, faebot is back",
            "we heal at least",
            "ようこそ",  # another script is not an echo (a different question)
        ],
    )
    def test_real_speech(self, text):
        assert not core.is_prompt_echo(text, self.PROMPT)


# ── the silence sentinel ─────────────────────────────────────────────


class TestSaidNothing:
    def test_bare_sentinel(self):
        assert core.said_nothing("NOTHING-TO-SAY")
        assert core.pass_reason("NOTHING-TO-SAY") == ""

    def test_case_and_spacing_are_forgiven(self):
        assert core.said_nothing("nothing to say")
        assert core.said_nothing("  Nothing-To-Say.")

    def test_reason_after_sentinel_is_kept(self):
        text = "NOTHING-TO-SAY — they're mid-conversation, I'll listen"
        assert core.said_nothing(text)
        assert core.pass_reason(text) == "they're mid-conversation, I'll listen"

    def test_sentinel_mid_sentence_is_speech(self):
        assert not core.said_nothing("honestly I have nothing to say about that")

    def test_empty_is_a_drop_not_silence(self):
        assert not core.said_nothing("")
        assert not core.said_nothing("   ")

    def test_prompt_tells_faebot_the_verb(self):
        conv = core.Conversation(channel="c")
        prompt = core.build_system_prompt(conv, "c", "t", "g", [])
        assert core.SENTINEL_SILENCE in prompt


# ── generate (OpenRouter API) ────────────────────────────────────────


class TestGenerate:
    @pytest.mark.asyncio
    async def test_successful_generation(self, openrouter_success):
        openrouter_success("test response")
        result = await core.generate(prompt="hello", system_prompt="you are faebot")
        assert result.text == "test response"
        assert result.reasoning == ""
        assert result.attempts == 1
        await core.close_session()

    @pytest.mark.asyncio
    async def test_4xx_raises_generation_failed(self):
        """No fallback text is returned (it used to be POSTED to chat)."""
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                status=403,
                payload={"error": "forbidden"},
            )
            with pytest.raises(core.GenerationFailed, match="403"):
                await core.generate(prompt="hello")
            await core.close_session()

    @pytest.mark.asyncio
    async def test_5xx_raises_without_retrying(self):
        """One request, one chance: a 500 is a failure, not a retry loop."""
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                status=500,
                payload={"error": "server error"},
            )
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "never reached"}}]},
            )
            with pytest.raises(core.GenerationFailed, match="500"):
                await core.generate(prompt="hello")
            calls = sum(len(c) for c in mocked.requests.values())
            assert calls == 1
            await core.close_session()

    @pytest.mark.asyncio
    async def test_429_gets_exactly_one_retry_aimed_past_the_pool_that_said_no(
        self, monkeypatch
    ):
        """A shared-pool rate limit clears in a second — but the pool that
        said no is the one a same-list retry asks again (five asks lost that
        way on 08-27). One retry, aimed at the rest of the pinned list."""
        monkeypatch.setattr(core, "RATE_LIMIT_RETRY_DELAY", 0)
        monkeypatch.setattr(core, "PROVIDERS", ("moonshotai", "modal"))
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                status=429,
                payload={"error": "rate limited"},
            )
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "recovered"}}]},
            )
            result = await core.generate(prompt="hello")
            assert result.text == "recovered"
            sent = [
                c.kwargs["json"] for calls in mocked.requests.values() for c in calls
            ]
            assert len(sent) == 2
            assert sent[0]["provider"]["order"] == ["moonshotai", "modal"]
            assert sent[1]["provider"] == {"order": ["modal"], "allow_fallbacks": False}
            await core.close_session()

    @pytest.mark.asyncio
    async def test_429_with_one_pinned_provider_still_retries_it(self, monkeypatch):
        """Nowhere else to aim: the one retry asks the same provider again,
        as it always did."""
        monkeypatch.setattr(core, "RATE_LIMIT_RETRY_DELAY", 0)
        monkeypatch.setattr(core, "PROVIDERS", ("moonshotai",))
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                status=429,
                payload={"error": "rate limited"},
            )
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "recovered"}}]},
            )
            result = await core.generate(prompt="hello")
            assert result.text == "recovered"
            sent = [
                c.kwargs["json"] for calls in mocked.requests.values() for c in calls
            ]
            assert [s["provider"]["order"] for s in sent] == [["moonshotai"]] * 2
            await core.close_session()

    @pytest.mark.asyncio
    async def test_429_twice_fails_after_the_one_retry(self, monkeypatch):
        monkeypatch.setattr(core, "RATE_LIMIT_RETRY_DELAY", 0)
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                status=429,
                payload={"error": "rate limited"},
                repeat=True,
            )
            with pytest.raises(core.GenerationFailed, match="429") as info:
                await core.generate(prompt="hello")
            assert info.value.is_rate_limit
            calls = sum(len(c) for c in mocked.requests.values())
            assert calls == 2
            await core.close_session()

    @pytest.mark.asyncio
    async def test_504_retries_once_on_the_rest_of_the_pinned_list(self, monkeypatch):
        """An upstream drop is retried ONCE, aimed at the pinned providers
        after the one that dropped — never outside the pin."""
        monkeypatch.setattr(core, "PROVIDERS", ("moonshotai", "modal"))
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                status=504,
                payload={"error": {"message": "The operation was aborted"}},
            )
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "caught by modal"}}]},
            )
            result = await core.generate(prompt="hello")
            assert result.text == "caught by modal"
            sent = [
                c.kwargs["json"] for calls in mocked.requests.values() for c in calls
            ]
            assert len(sent) == 2
            assert sent[0]["provider"]["order"] == ["moonshotai", "modal"]
            assert sent[1]["provider"] == {"order": ["modal"], "allow_fallbacks": False}
            await core.close_session()

    @pytest.mark.asyncio
    async def test_504_inside_a_200_body_counts_as_a_drop(self, monkeypatch):
        """OpenRouter answers 200 with `{"error": {"code": 504}}` when the
        upstream aborts mid-call; that is the drop we actually see."""
        monkeypatch.setattr(core, "PROVIDERS", ("moonshotai", "modal"))
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={
                    "error": {"message": "The operation was aborted", "code": 504}
                },
            )
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "caught by modal"}}]},
            )
            result = await core.generate(prompt="hello")
            assert result.text == "caught by modal"
            await core.close_session()

    @pytest.mark.asyncio
    async def test_504_with_nowhere_else_to_go_is_final(self, monkeypatch):
        """One pinned provider: a drop has no second address, so it fails."""
        monkeypatch.setattr(core, "PROVIDERS", ("moonshotai",))
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                status=504,
                payload={"error": {"message": "The operation was aborted"}},
            )
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "never reached"}}]},
            )
            with pytest.raises(core.GenerationFailed, match="504") as info:
                await core.generate(prompt="hello")
            assert info.value.is_upstream_drop and not info.value.is_rate_limit
            calls = sum(len(c) for c in mocked.requests.values())
            assert calls == 1
            await core.close_session()

    @pytest.mark.asyncio
    async def test_network_error_raises_generation_failed(self, openrouter_error):
        openrouter_error(status=500, repeat=True)
        with pytest.raises(core.GenerationFailed):
            await core.generate(prompt="hello")
        await core.close_session()

    @pytest.mark.asyncio
    async def test_timeout_raises_generation_failed_with_elapsed(self):
        import asyncio as _asyncio

        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                exception=_asyncio.TimeoutError(),
            )
            with pytest.raises(core.GenerationFailed, match="timed out") as info:
                await core.generate(prompt="hello")
            assert info.value.elapsed >= 0
            assert not info.value.is_rate_limit
            calls = sum(len(c) for c in mocked.requests.values())
            assert calls == 1
            await core.close_session()

    @pytest.mark.asyncio
    async def test_malformed_response_raises(self):
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"unexpected": "format"},
            )
            with pytest.raises(core.GenerationFailed, match="no choices"):
                await core.generate(prompt="hello")
            await core.close_session()

    @pytest.mark.asyncio
    async def test_request_carries_pinned_sampling_and_timeout(self):
        """Sampling is pinned (no lottery, no top_k, no seed) and every
        request has a real timeout."""
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "ok"}}]},
            )
            await core.generate(
                prompt="hello", params={"temperature": 1.0, "top_p": 0.95}
            )
            (request,) = [call for calls in mocked.requests.values() for call in calls]
        body = request.kwargs["json"]
        assert body["temperature"] == 1.0
        assert body["top_p"] == 0.95
        assert "top_k" not in body and "seed" not in body
        timeout = request.kwargs["timeout"]
        assert timeout.total == core.REQUEST_TIMEOUT
        await core.close_session()

    @pytest.mark.asyncio
    async def test_provider_and_params_ride_on_the_completion(self):
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={
                    "provider": "Moonshot AI",
                    "choices": [{"message": {"content": "ok"}}],
                },
            )
            result = await core.generate(
                prompt="hello", params={"temperature": 0.7, "top_p": 0.9}
            )
        assert result.provider == "Moonshot AI"
        assert result.params == {"temperature": 0.7, "top_p": 0.9}
        assert result.capture_meta()["provider"] == "Moonshot AI"
        assert result.capture_meta()["params"] == {"temperature": 0.7, "top_p": 0.9}
        await core.close_session()

    @pytest.mark.asyncio
    async def test_reasoning_and_content_kept_apart(self):
        """`content` is the answer channel, `reasoning` a separate one — both
        survive, neither leaks into the other."""
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={
                    "model": "moonshotai/kimi-k3",
                    "choices": [
                        {
                            "message": {
                                "content": "hi chat!",
                                "reasoning": "they said hello, I should say hi",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"completion_tokens": 40},
                },
            )
            result = await core.generate(prompt="hello")
        assert result.text == "hi chat!"
        assert result.reasoning == "they said hello, I should say hi"
        assert result.finish_reason == "stop"
        assert result.model == "moonshotai/kimi-k3"
        assert result.usage == {"completion_tokens": 40}
        assert result.elapsed >= 0
        await core.close_session()

    @pytest.mark.asyncio
    async def test_request_sends_reasoning_room_on_top_of_answer_cap(self):
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "ok"}}]},
            )
            await core.generate(prompt="hello", model="some/model")
            (request,) = [call for calls in mocked.requests.values() for call in calls]
        body = request.kwargs["json"]
        assert body["model"] == "some/model"
        assert body["reasoning"] == {"max_tokens": core.REASONING_CAP}
        assert body["max_tokens"] == core.GENERATION_CAP + core.REASONING_CAP
        assert body["provider"] == {
            "order": list(core.PROVIDERS),
            "allow_fallbacks": False,
        }
        await core.close_session()

    @pytest.mark.asyncio
    async def test_empty_answer_channel_rolls_again(self):
        """Empty `content` beside a full `reasoning` is a dropped payload, not
        silence — roll once more."""
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={
                    "choices": [
                        {"message": {"content": "", "reasoning": "thinking..."}}
                    ]
                },
            )
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "second try"}}]},
            )
            result = await core.generate(prompt="hello")
        assert result.text == "second try"
        assert result.attempts == 2
        await core.close_session()

    @pytest.mark.asyncio
    async def test_empty_twice_returns_empty_honestly(self):
        """After the rolls run out the Completion stays empty — the caller
        decides what a dropped payload means; generate() tells the truth."""
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": ""}}]},
                repeat=True,
            )
            result = await core.generate(prompt="hello")
        assert result.is_empty
        assert result.attempts == core.EMPTY_ROLLS
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
        assert result.text == "hi there!"
        assert "faebot: hi there!" in conversation.chatlog
        await core.close_session()

    @pytest.mark.asyncio
    async def test_trims_chatlog_in_a_block(self, conversation):
        """Over the limit → cut back to the floor (10 - 10//5 = 8), so the
        prompt prefix stays put for the next couple of calls."""
        conversation.history = 10
        conversation.chatlog = [f"msg{i}" for i in range(20)]
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "reply"}}]},
            )
            await core.generate_response("testchannel")
        # 8 kept from trim + 1 appended response
        assert len(conversation.chatlog) == 9
        assert conversation.chatlog[0] == "msg12"
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
        assert len(result.text) == 500
        assert result.text.endswith("\u2013")
        await core.close_session()

    @pytest.mark.asyncio
    async def test_pass_is_not_posted_but_is_remembered(self, conversation):
        """The sentinel marks chosen silence: text stays as-is for the caller
        to recognise, the chatlog records the fact (not the reason)."""
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={
                    "choices": [
                        {
                            "message": {
                                "content": "NOTHING-TO-SAY — just listening",
                                "reasoning": "nobody's talking to me",
                            }
                        }
                    ]
                },
            )
            result = await core.generate_response("testchannel")
        assert result.passed
        assert result.reason_for_passing == "just listening"
        assert result.reasoning == "nobody's talking to me"
        assert conversation.chatlog[-1] == "faebot: *stays quiet*"
        assert not any("listening" in line for line in conversation.chatlog)
        await core.close_session()

    @pytest.mark.asyncio
    async def test_multiline_reply_is_folded_to_one_line(self, conversation):
        """IRC carries one line per message; kimi writes several."""
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={
                    "choices": [
                        {"message": {"content": "hi chat!!\n\n*flutters*  \nbye~"}}
                    ]
                },
            )
            result = await core.generate_response("testchannel")
        assert result.text == "hi chat!! *flutters* bye~"
        await core.close_session()

    @pytest.mark.asyncio
    async def test_emotes_get_spaced(self, conversation):
        emotes = ["transf23Botlove"]
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "hitransf23Botlovebye"}}]},
            )
            result = await core.generate_response("testchannel", emotes=emotes)
        assert result.text == "hi transf23Botlove bye"
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


# ── event queue ──────────────────────────────────────────────────────


def _drain_queue(queue: asyncio.Queue) -> list[dict]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


class TestEventQueue:
    @pytest.mark.asyncio
    async def test_no_queue_is_backwards_compatible(self, conversation):
        """Omitting the events queue should not change behaviour."""
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "hi"}}]},
            )
            result = await core.generate_response("testchannel")
        assert result.text == "hi"
        await core.close_session()

    @pytest.mark.asyncio
    async def test_generating_event_posted(self, conversation):
        """core only emits `generating` (and `error` on failure). The caller emits
        `response` after a successful Twitch send — see bot._generate_and_send."""
        queue: asyncio.Queue = asyncio.Queue()
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "hi there"}}]},
            )
            await core.generate_response("testchannel", events=queue)
        events = _drain_queue(queue)
        types = [e["type"] for e in events]
        assert types == ["generating"]
        assert events[0]["channel"] == "testchannel"
        assert events[0]["model"] == conversation.model
        assert "prompt" in events[0]
        assert "system_prompt" in events[0]
        assert events[0]["params"] == {
            "temperature": core.TEMPERATURE,
            "top_p": core.TOP_P,
        }
        await core.close_session()

    @pytest.mark.asyncio
    async def test_error_event_on_failure(self, conversation):
        queue: asyncio.Queue = asyncio.Queue()
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                status=500,
                payload={"error": "server error"},
                repeat=True,
            )
            with pytest.raises(core.GenerationFailed):
                await core.generate_response("testchannel", events=queue)
        events = _drain_queue(queue)
        types = [e["type"] for e in events]
        assert types == ["generating", "error"]
        assert "error" in events[1]
        await core.close_session()

    @pytest.mark.asyncio
    async def test_events_carry_timestamp(self, conversation):
        queue: asyncio.Queue = asyncio.Queue()
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "hi"}}]},
            )
            await core.generate_response("testchannel", events=queue)
        events = _drain_queue(queue)
        for e in events:
            assert "timestamp" in e
        await core.close_session()

    def test_put_event_drops_oldest_when_full(self):
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        core.put_event(queue, {"type": "a"})
        core.put_event(queue, {"type": "b"})
        core.put_event(queue, {"type": "c"})  # should evict "a"
        events = _drain_queue(queue)
        types = [e["type"] for e in events]
        assert types == ["b", "c"]

    def test_put_event_none_queue_is_noop(self):
        core.put_event(None, {"type": "whatever"})  # should not raise

    @pytest.mark.asyncio
    async def test_caller_provided_generation_id_is_used(self, conversation):
        """When the caller passes a generation_id, the generating event uses it."""
        queue: asyncio.Queue = asyncio.Queue()
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "hi"}}]},
            )
            await core.generate_response(
                "testchannel", events=queue, generation_id="abc-123"
            )
        events = _drain_queue(queue)
        assert events[0]["id"] == "abc-123"
        await core.close_session()

    @pytest.mark.asyncio
    async def test_error_shares_generation_id(self, conversation):
        queue: asyncio.Queue = asyncio.Queue()
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                status=500,
                payload={"error": "server error"},
                repeat=True,
            )
            with pytest.raises(Exception):
                await core.generate_response("testchannel", events=queue)
        events = _drain_queue(queue)
        ids = {e["id"] for e in events}
        assert len(ids) == 1
        await core.close_session()

    @pytest.mark.asyncio
    async def test_trigger_type_and_text_on_generating(self, conversation):
        queue: asyncio.Queue = asyncio.Queue()
        conversation.chatlog.append("alice: hey faebot")
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "hi"}}]},
            )
            await core.generate_response(
                "testchannel", events=queue, trigger_type="voice"
            )
        events = _drain_queue(queue)
        gen = next(e for e in events if e["type"] == "generating")
        assert gen["trigger_type"] == "voice"
        assert gen["trigger"] == "alice: hey faebot"
        await core.close_session()

    @pytest.mark.asyncio
    async def test_timestamps_are_utc(self, conversation):
        queue: asyncio.Queue = asyncio.Queue()
        with aioresponses_ctx() as mocked:
            mocked.post(
                "https://openrouter.ai/api/v1/chat/completions",
                payload={"choices": [{"message": {"content": "hi"}}]},
            )
            await core.generate_response("testchannel", events=queue)
        events = _drain_queue(queue)
        for e in events:
            # UTC isoformat ends with +00:00
            assert e["timestamp"].endswith("+00:00")
        await core.close_session()
