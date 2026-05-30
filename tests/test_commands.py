"""Tests for commands.py — faebot's chat command handlers.

TwitchIO's @commands.command() decorator wraps methods into Command objects.
To test the underlying logic without TwitchIO's full context machinery,
we call the callback function directly via `command_method._callback(instance, ctx)`.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import core


# ── requires_mod decorator ───────────────────────────────────────────


class TestRequiresMod:
    @pytest.mark.asyncio
    async def test_mod_can_use_command(self, mock_context):
        """Moderators should be able to use mod-only commands."""
        from commands import FaebotCommands

        ctx = mock_context("fae;clear", is_mod=True)
        core.ensure_conversation("testchannel")

        instance = FaebotCommands()
        # Call the callback directly, bypassing TwitchIO Command machinery
        await instance.clear._callback(instance, ctx)

        assert "cleared" in ctx.replies[0].lower() or "forgotten" in ctx.replies[0].lower()

    @pytest.mark.asyncio
    async def test_admin_can_use_command(self, mock_context):
        """Users in ADMIN list should be able to use mod-only commands."""
        from commands import FaebotCommands

        ctx = mock_context("fae;clear", author_name="transfaeries", is_mod=False)
        core.ensure_conversation("testchannel")

        with patch("commands.ADMIN", ["transfaeries"]):
            instance = FaebotCommands()
            await instance.clear._callback(instance, ctx)

        assert "cleared" in ctx.replies[0].lower() or "forgotten" in ctx.replies[0].lower()

    @pytest.mark.asyncio
    async def test_regular_user_blocked(self, mock_context):
        """Regular users should be blocked from mod-only commands."""
        from commands import FaebotCommands

        ctx = mock_context("fae;clear", author_name="randomuser", is_mod=False)
        core.ensure_conversation("testchannel")

        with patch("commands.ADMIN", []):
            instance = FaebotCommands()
            await instance.clear._callback(instance, ctx)

        assert "mod" in ctx.sends[0].lower() or "admin" in ctx.sends[0].lower()


# ── Public commands ──────────────────────────────────────────────────


class TestPublicCommands:
    @pytest.mark.asyncio
    async def test_hello_replies(self, mock_context):
        """hello command should reply with introduction."""
        from commands import FaebotCommands

        ctx = mock_context("fae;hello")
        instance = FaebotCommands()
        await instance.hello._callback(instance, ctx)

        assert len(ctx.replies) == 1
        assert "faebot" in ctx.replies[0].lower()

    @pytest.mark.asyncio
    async def test_help_replies(self, mock_context):
        """help command should reply with introduction."""
        from commands import FaebotCommands

        ctx = mock_context("fae;help")
        instance = FaebotCommands()
        await instance.help._callback(instance, ctx)

        assert len(ctx.replies) == 1
        assert "faebot" in ctx.replies[0].lower()

    @pytest.mark.asyncio
    async def test_invite_replies(self, mock_context):
        """invite command should reply with invitation info."""
        from commands import FaebotCommands

        ctx = mock_context("fae;invite")
        instance = FaebotCommands()
        await instance.invite._callback(instance, ctx)

        assert len(ctx.replies) == 1
        assert "transfaeries" in ctx.replies[0].lower()

    @pytest.mark.asyncio
    async def test_mods_lists_commands(self, mock_context):
        """mods command should list available mod commands."""
        from commands import FaebotCommands

        ctx = mock_context("fae;mods")
        instance = FaebotCommands()
        await instance.mods._callback(instance, ctx)

        assert len(ctx.replies) == 1
        reply = ctx.replies[0].lower()
        assert "freq" in reply
        assert "hist" in reply
        assert "silence" in reply

    @pytest.mark.asyncio
    async def test_ping_echoes_args(self, mock_context):
        """ping command should echo back arguments."""
        from commands import FaebotCommands

        ctx = mock_context("fae;ping hello world")
        instance = FaebotCommands()
        await instance.ping._callback(instance, ctx)

        assert len(ctx.replies) == 1
        assert "pong" in ctx.replies[0].lower()
        assert "hello world" in ctx.replies[0]


# ── alias command ────────────────────────────────────────────────────


class TestAliasCommand:
    @pytest.mark.asyncio
    async def test_set_new_alias(self, mock_context):
        """Setting a new alias should store it and confirm."""
        from commands import FaebotCommands

        ctx = mock_context("fae;alias Miku", author_name="hatsunemikuisbestwaifu")
        core.ensure_conversation("testchannel")

        instance = FaebotCommands()
        await instance.alias._callback(instance, ctx)

        assert core.aliases["hatsunemikuisbestwaifu"] == "Miku"
        assert "miku" in ctx.replies[0].lower()

    @pytest.mark.asyncio
    async def test_check_existing_alias(self, mock_context):
        """Checking an existing alias should show current value."""
        from commands import FaebotCommands

        ctx = mock_context("fae;alias", author_name="hatsunemikuisbestwaifu")
        core.aliases["hatsunemikuisbestwaifu"] = "Miku"
        core.ensure_conversation("testchannel")

        instance = FaebotCommands()
        await instance.alias._callback(instance, ctx)

        assert "miku" in ctx.replies[0].lower()

    @pytest.mark.asyncio
    async def test_check_no_alias(self, mock_context):
        """Checking when no alias set should prompt to set one."""
        from commands import FaebotCommands

        ctx = mock_context("fae;alias", author_name="newuser")
        core.ensure_conversation("testchannel")

        instance = FaebotCommands()
        await instance.alias._callback(instance, ctx)

        assert "haven't" in ctx.replies[0].lower() or "set one" in ctx.replies[0].lower()

    @pytest.mark.asyncio
    async def test_alias_added_to_chatlog(self, mock_context):
        """Setting alias should add the exchange to chatlog."""
        from commands import FaebotCommands

        ctx = mock_context("fae;alias Miku", author_name="hatsunemikuisbestwaifu")
        conv = core.ensure_conversation("testchannel")

        instance = FaebotCommands()
        await instance.alias._callback(instance, ctx)

        assert len(conv.chatlog) == 2
        assert "fae;alias Miku" in conv.chatlog[0]
        assert "faebot:" in conv.chatlog[1]


# ── Mod commands ─────────────────────────────────────────────────────


class TestModCommands:
    @pytest.mark.asyncio
    async def test_clear_empties_chatlog(self, mock_context):
        """clear command should empty the channel's chatlog."""
        from commands import FaebotCommands

        ctx = mock_context("fae;clear", is_mod=True)
        conv = core.ensure_conversation("testchannel")
        conv.chatlog = ["msg1", "msg2", "msg3"]

        instance = FaebotCommands()
        await instance.clear._callback(instance, ctx)

        assert conv.chatlog == []

    @pytest.mark.asyncio
    async def test_freq_check_current(self, mock_context):
        """freq with no args should show current frequencies."""
        from commands import FaebotCommands

        ctx = mock_context("fae;freq", is_mod=True)
        conv = core.ensure_conversation("testchannel")
        conv.frequency = 0.15
        conv.voice_frequency = 0.08

        instance = FaebotCommands()
        await instance.freq._callback(instance, ctx)

        assert "0.15" in ctx.sends[0]
        assert "0.08" in ctx.sends[0]

    @pytest.mark.asyncio
    async def test_freq_set_chat_only(self, mock_context):
        """freq with one arg should set chat frequency."""
        from commands import FaebotCommands

        ctx = mock_context("fae;freq 0.25", is_mod=True)
        conv = core.ensure_conversation("testchannel")

        instance = FaebotCommands()
        await instance.freq._callback(instance, ctx)

        assert conv.frequency == 0.25
        assert "0.25" in ctx.sends[0]

    @pytest.mark.asyncio
    async def test_freq_set_both(self, mock_context):
        """freq with two args should set both frequencies."""
        from commands import FaebotCommands

        ctx = mock_context("fae;freq 0.3 0.1", is_mod=True)
        conv = core.ensure_conversation("testchannel")

        instance = FaebotCommands()
        await instance.freq._callback(instance, ctx)

        assert conv.frequency == 0.3
        assert conv.voice_frequency == 0.1

    @pytest.mark.asyncio
    async def test_freq_invalid_value(self, mock_context):
        """freq with non-numeric arg should show error."""
        from commands import FaebotCommands

        ctx = mock_context("fae;freq banana", is_mod=True)
        core.ensure_conversation("testchannel")

        instance = FaebotCommands()
        await instance.freq._callback(instance, ctx)

        assert "number" in ctx.sends[0].lower()

    @pytest.mark.asyncio
    async def test_hist_check_current(self, mock_context):
        """hist with no args should show current history length."""
        from commands import FaebotCommands

        ctx = mock_context("fae;hist", is_mod=True)
        conv = core.ensure_conversation("testchannel")
        conv.history = 25

        instance = FaebotCommands()
        await instance.hist._callback(instance, ctx)

        assert "25" in ctx.sends[0]

    @pytest.mark.asyncio
    async def test_hist_set_value(self, mock_context):
        """hist with numeric arg should set history length."""
        from commands import FaebotCommands

        ctx = mock_context("fae;hist 50", is_mod=True)
        conv = core.ensure_conversation("testchannel")

        instance = FaebotCommands()
        await instance.hist._callback(instance, ctx)

        assert conv.history == 50

    @pytest.mark.asyncio
    async def test_silence_toggles_on(self, mock_context):
        """silence should toggle silenced state on."""
        from commands import FaebotCommands

        ctx = mock_context("fae;silence", is_mod=True)
        conv = core.ensure_conversation("testchannel")
        conv.silenced = False

        instance = FaebotCommands()
        await instance.silence._callback(instance, ctx)

        assert conv.silenced is True

    @pytest.mark.asyncio
    async def test_silence_toggles_off(self, mock_context):
        """silence should toggle silenced state off."""
        from commands import FaebotCommands

        ctx = mock_context("fae;silence", is_mod=True)
        conv = core.ensure_conversation("testchannel")
        conv.silenced = True

        instance = FaebotCommands()
        await instance.silence._callback(instance, ctx)

        assert conv.silenced is False

    @pytest.mark.asyncio
    async def test_part_leaves_channel(self, mock_context):
        """part should call part_channels."""
        from commands import FaebotCommands

        ctx = mock_context("fae;part", is_mod=True)

        instance = FaebotCommands()
        instance.part_channels = AsyncMock()
        await instance.part._callback(instance, ctx)

        assert "bye" in ctx.replies[0].lower()
        instance.part_channels.assert_called_once_with(["testchannel"])

    @pytest.mark.asyncio
    async def test_prompt_shows_info(self, mock_context):
        """prompt should explain the auto-generated prompt."""
        from commands import FaebotCommands

        ctx = mock_context("fae;prompt", is_mod=True)

        instance = FaebotCommands()
        await instance.prompt._callback(instance, ctx)

        assert "auto-generated" in ctx.sends[0].lower()


# ── Admin commands ───────────────────────────────────────────────────


class TestAdminCommands:
    @pytest.mark.asyncio
    async def test_join_requires_admin(self, mock_context):
        """join should reject non-admins."""
        from commands import FaebotCommands

        ctx = mock_context("fae;join newchannel", author_name="randomuser")

        with patch("commands.ADMIN", ["transfaeries"]):
            instance = FaebotCommands()
            await instance.join._callback(instance, ctx, "newchannel")

        assert "admin" in ctx.sends[0].lower()

    @pytest.mark.asyncio
    async def test_join_works_for_admin(self, mock_context):
        """join should work for admins."""
        from commands import FaebotCommands

        ctx = mock_context("fae;join newchannel", author_name="transfaeries")

        with patch("commands.ADMIN", ["transfaeries"]):
            instance = FaebotCommands()
            instance.join_channels = AsyncMock()
            await instance.join._callback(instance, ctx, "newchannel")

        instance.join_channels.assert_called_once_with(["newchannel"])
        assert "newchannel" in ctx.replies[0].lower()

    @pytest.mark.asyncio
    async def test_model_requires_admin(self, mock_context):
        """model should reject non-admins."""
        from commands import FaebotCommands

        ctx = mock_context("fae;model gpt-4", author_name="randomuser")

        with patch("commands.ADMIN", ["transfaeries"]):
            instance = FaebotCommands()
            await instance.model._callback(instance, ctx)

        assert "admin" in ctx.sends[0].lower()

    @pytest.mark.asyncio
    async def test_model_check_current(self, mock_context):
        """model with no args should show current model."""
        from commands import FaebotCommands

        ctx = mock_context("fae;model", author_name="transfaeries")
        conv = core.ensure_conversation("testchannel")
        conv.model = "google/gemini-2.0-flash-001"

        with patch("commands.ADMIN", ["transfaeries"]):
            instance = FaebotCommands()
            await instance.model._callback(instance, ctx)

        assert "gemini" in ctx.sends[0].lower()

    @pytest.mark.asyncio
    async def test_model_set_value(self, mock_context):
        """model with arg should set new model."""
        from commands import FaebotCommands

        ctx = mock_context("fae;model anthropic/claude-3-haiku", author_name="transfaeries")
        conv = core.ensure_conversation("testchannel")

        with patch("commands.ADMIN", ["transfaeries"]):
            instance = FaebotCommands()
            await instance.model._callback(instance, ctx)

        assert conv.model == "anthropic/claude-3-haiku"
