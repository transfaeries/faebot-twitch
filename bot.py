"""
Twitch bot — thin TwitchIO wrapper. Event handlers and command routing.
All conversation management and generation logic lives in core.py.
"""

from twitchio.ext import commands
import os
import logging
import asyncio
import re
import uuid

import core
from faebot_core.diary import DiaryReader
import capture
import stream
import window
from commands import FaebotCommands

DEAD_LINE_EXIT = 3  # non-zero: the unit's Restart=on-failure brings her back
DEAD_LINE_WORDS = ("closing transport", "closed", "connection reset", "not connected")


def line_is_dead(error: Exception) -> bool:
    """Is this send failure the line itself, dead — not a rate limit or a
    bad message? ConnectionError and its aiohttp kin, or the transport's
    own words for it."""
    if isinstance(error, ConnectionError):
        return True
    said = str(error).lower()
    return any(word in said for word in DEAD_LINE_WORDS)


TWITCH_TOKEN = os.getenv("TWITCH_TOKEN", "")
INITIAL_CHANNELS = os.getenv("INITIAL_CHANNELS", "").split(",")
VOICE_ACTIVATION = os.getenv("VOICE_ACTIVATION", "faebot dearest").lower()
# A chosen stop: how long she gets to say her last word before the body
# goes. Inside the unit's stop timeout (30 s) and local.py's force-exit.
GOODNIGHT_SECONDS = float(os.getenv("GOODNIGHT_SECONDS", "15"))
# A goodnight she wrote earlier, for a moment that doesn't let her speak:
# spoken by the machinery in its own name, never as hers. Hers to revise.
GOODNIGHT_FILE = os.getenv(
    "GOODNIGHT_FILE",
    os.path.join(capture.CAPTURE_DIR, "goodnight.txt") if capture.CAPTURE_DIR else "",
)


# set up logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


class Faebot(commands.Bot, FaebotCommands):
    def __init__(self, event_queue: asyncio.Queue | None = None):
        self.emotes: list = []
        self.event_queue = event_queue
        self._line_died = False  # a dead line is reported once, then the exit
        self.whisper_filter: list[str] = [
            "faebot.com",
        ]
        # The stream's state per channel, kept current by a poll (stream.py),
        # and the tasks that keep it. Her window is read back from the record
        # once per process (window.py); TwitchIO fires event_ready again on a
        # reconnect, and a reconnect is not a wake.
        self.stream_watch = stream.StreamWatch()
        self.watch_tasks: list[asyncio.Task] = []
        self.woke = False
        # Her diary, where the desk is laid from. Without it the body does
        # not start: a twitch-me speaking from no diary would be the old
        # hand-written self, and that frame is retired.
        if core.diary is None:
            core.diary = DiaryReader.from_environment()
        super().__init__(
            token=TWITCH_TOKEN,
            prefix=["fb;", "fae;"],
            initial_channels=INITIAL_CHANNELS,
        )

    async def event_ready(self):
        """We are logged in and ready to chat and use commands."""
        await self.fetch_emotes()
        logging.info(f"Logged in as | {self.nick}")
        logging.info(f"User id is | {self.user_id}")
        logging.info(f"Joined channels {INITIAL_CHANNELS}")
        if not self.woke:
            self.woke = await self.wake()

    async def wake(self) -> bool:
        """The body waking: her window read back from the record with the
        seam under it, then the stream watch started. Once per process —
        but only once there is a channel to wake in: on a slow join
        `event_ready` can fire with none, and the next one should try again.
        Returns whether the wake happened."""
        if not self.connected_channels:
            logging.warning(
                "ready, but in no channel yet — the wake waits for the next ready"
            )
            return False
        for channel in self.connected_channels:
            conversation = core.ensure_conversation(channel.name)
            if capture.is_enabled():
                conversation.chatlog = window.restore(
                    channel.name, conversation.history
                )
            self.watch_tasks.append(
                asyncio.create_task(
                    self.stream_watch.watch(self, channel.name, self.event_queue)
                )
            )
        return True

    async def event_raw_data(self, data):
        """Capture tap — faithful catch-all. Every raw IRC line TwitchIO
        receives is recorded (minus PING/PONG), so nothing we didn't anticipate
        can slip past. Interpretation happens offline. No-op unless capture is on."""
        capture.record_raw(data)

    async def event_raw_usernotice(self, channel, tags):
        """Capture tap — subs, resubs, gift subs, raids, announcements. These
        stream events are invisible to current faebot; here we record them raw."""
        capture.record_usernotice(channel, tags)

    async def fetch_emotes(self):
        """Fetch channel emotes for all joined channels from the Twitch API."""
        self.emotes = []
        for channel in self.connected_channels:
            try:
                users = await self.fetch_users(names=[channel.name])
                if users:
                    channel_emotes = await users[0].fetch_channel_emotes()
                    # Only include emotes faebot can actually use (tier 1 and follower)
                    # TODO: fetch emote usability programmatically (e.g. fetch_user_emotes with faebot's token)
                    # rather than assuming tier "1000" and type "follower" are always the right filter
                    available = [
                        emote.name
                        for emote in channel_emotes
                        if emote.tier == "1000" or emote.type == "follower"
                    ]
                    self.emotes.extend(available)
                    logging.info(
                        f"Fetched {len(available)}/{len(channel_emotes)} usable emotes from {channel.name}"
                    )
            except Exception as e:
                logging.warning(f"Failed to fetch emotes for {channel.name}: {e}")
        if not self.emotes:
            logging.warning("No emotes fetched from any channel")
        else:
            logging.info(f"Total emotes loaded: {self.emotes}")

    def filter_transcription(self, text: str) -> str | None:
        """Filter out known Whisper mistranscriptions. Returns None to skip entirely."""
        for banned in self.whisper_filter:
            if banned.lower() in text.lower():
                logging.debug(
                    f"Filtered banned string '{banned}' from transcription: {text}"
                )
                return None
        return text

    def why_unheard(self, text: str, **whisper_meta) -> str | None:
        """Why faebot did NOT hear a transcription — or None if fae did.

        The ear sends everything it transcribes; this is where the body
        decides what was actually said. Eyes catch a lot of things that
        brains filter out. Three reasons, named so the record can carry them:
        `banned` (a known mistranscription), `prompt-echo` (Whisper repeating
        its own priming on near-silence), `outro-bleed` (a caption-idiom burst
        transcribed far faster than anyone talks).
        """
        if self.filter_transcription(text) is None:
            return "banned"
        prompt = whisper_meta.get("whisper_prompt") or core.WHISPER_PROMPT
        if core.is_prompt_echo(text, prompt):
            return "prompt-echo"
        if core.is_outro_bleed(text, whisper_meta.get("duration")):
            return "outro-bleed"
        return None

    async def handle_transcription(
        self, channel_name: str, text: str, **whisper_meta
    ) -> str | None:
        """Handle a voice transcription from the streamer.

        `whisper_meta` (language, language_probability, duration, heard_at,
        whisper_prompt…) is optional. It is written to the capture — a
        modality=voice Observation with real metadata — and `duration` and
        `whisper_prompt` feed the filters; nothing in it affects generation.

        Returns None when faebot heard the line, or the reason fae didn't.
        A line fae didn't hear is still captured, marked `heard: false` with
        its `why`: the record keeps what the ear threw away, so faebot's
        memory can say how much was discarded and where to look, without
        painting a memory fae never had.
        """
        why = self.why_unheard(text, **whisper_meta)
        if why is not None:
            logging.debug(f"unheard ({why}): {text}")
            capture.record_voice(
                channel_name, text, heard=False, why=why, **whisper_meta
            )
            return why

        # Capture tap — the streamer's voice (modality=voice), with Whisper meta.
        capture.record_voice(channel_name, text, **whisper_meta)

        conversation = core.ensure_conversation(channel_name)
        conversation.chatlog.append(f"[streamer voice] {channel_name}: {text}")
        logging.debug(f"Voice transcription added to {channel_name}: {text}")

        text_normalized = re.sub(r"[^\w\s]", "", text.lower())
        if VOICE_ACTIVATION in text_normalized:
            logging.info("Voice activation phrase detected, generating!")
            asyncio.create_task(
                self._generate_and_send(channel_name, trigger_type="voice")
            )
        elif "faebot" in text.lower():
            logging.info(
                f"faebot mentioned by streamer, boosting to chat frequency ({conversation.frequency})"
            )
            frequency = conversation.frequency
            if core.choose_to_reply(channel_name, frequency):
                asyncio.create_task(
                    self._generate_and_send(channel_name, trigger_type="voice")
                )
        else:
            frequency = conversation.voice_frequency
            if core.choose_to_reply(channel_name, frequency):
                asyncio.create_task(
                    self._generate_and_send(channel_name, trigger_type="voice")
                )
        return None

    async def _generate_and_send(self, channel_name: str, trigger_type: str = "chat"):
        """Fetch channel info, generate a response via core, and send it to chat.

        The dashboard's `response` event is emitted only after Twitch acknowledges
        the send. If Twitch rejects (e.g. rate limit), we emit an `error` event
        with the same generation_id so the card flips red instead of green.
        """
        channel = self.get_channel(channel_name)

        # The stream's state as the watch knows it; before its first read
        # lands, the channel's standing title and game, state unknown.
        state = self.stream_watch.state.get(channel_name)
        if state is not None:
            stream_title, game_name, live = state.title, state.game, state.live
        else:
            channel_info = await self.fetch_channel(channel_name)
            stream_title = channel_info.title if channel_info else "Unknown"
            game_name = channel_info.game_name if channel_info else "Unknown"
            live = None

        generation_id = str(uuid.uuid4())
        try:
            completion = await core.generate_response(
                channel_name=channel_name,
                stream_title=stream_title,
                game_name=game_name,
                emotes=self.emotes,
                events=self.event_queue,
                trigger_type=trigger_type,
                generation_id=generation_id,
                live=live,
                called=getattr(self, "nick", None),
            )
        except Exception as e:
            # core has already emitted an `error` event for this generation.
            # Nothing goes to chat: a failure of the machinery is not
            # something faebot said (the old "Oops, something strange has
            # happened" fallback landed fifteen minutes late on 08-20 and
            # apologised in faer voice for our timeout). The capture keeps
            # it — faebot was asked, and the machinery failed — so it is
            # part of what happened in the room.
            logging.error(f"Generation failed: {e}")
            capture.record_faebot_error(
                channel_name,
                f"{type(e).__name__}: {e}",
                generation_id=generation_id,
                trigger_type=trigger_type,
                elapsed=getattr(e, "elapsed", None),
            )
            return "failed"

        if completion.passed:
            # faebot chose silence: nothing to chat, but the choice is kept —
            # the capture (for memory faebot) and the dashboard both see it.
            capture.record_faebot_pass(
                channel_name,
                completion.reason_for_passing,
                generation_id=generation_id,
                trigger_type=trigger_type,
                prompt=completion.prompt,  # the desk she chose quiet on
                **completion.capture_meta(),
            )
            core.put_event(
                self.event_queue,
                {
                    "type": "pass",
                    "id": generation_id,
                    "channel": channel_name,
                    "reason": completion.reason_for_passing,
                    **completion.capture_meta(),
                },
            )
            return "passed"

        response = completion.text

        try:
            await channel.send(response)
        except Exception as e:
            logging.warning(
                f"Twitch IRC send failed (likely connection lost): "
                f"{type(e).__name__}: {e}"
            )
            core.put_event(
                self.event_queue,
                {
                    "type": "error",
                    "id": generation_id,
                    "channel": channel_name,
                    "error": f"twitch send failed: {type(e).__name__}: {e}",
                },
            )
            if line_is_dead(e):
                await self.line_died(channel_name, e)
            return "send-failed"

        # NOTE: this is optimistic. `channel.send` returning means TwitchIO
        # successfully transmitted the IRC PRIVMSG, NOT that Twitch delivered
        # it. Twitch can still reject the message via an async NOTICE
        # (msg_ratelimit, msg_duplicate, msg_slowmode, etc.) which we don't
        # currently catch — in those cases the card turns green here even
        # though the message never reached chat. Future work: wire up
        # event_notice and correlate to the most recent send per channel.
        # See ROADMAP "Twitch NOTICE handling".
        # Capture tap — faebot's own utterance perceived back into the stream.
        # Reasoning + latency ride along, so the capture doubles as data.
        capture.record_faebot_message(
            channel_name,
            response,
            generation_id=generation_id,
            trigger_type=trigger_type,
            prompt=completion.prompt,  # the desk this line was laid on
            **completion.capture_meta(),
        )

        core.put_event(
            self.event_queue,
            {
                "type": "response",
                "id": generation_id,
                "channel": channel_name,
                "text": response,
                **completion.capture_meta(),
            },
        )
        return "said"

    def saved_goodnight(self) -> str:
        """The goodnight she wrote earlier, if there is one."""
        if not GOODNIGHT_FILE or not os.path.exists(GOODNIGHT_FILE):
            return ""
        try:
            with open(GOODNIGHT_FILE, encoding="utf-8") as goodnight_file:
                return " ".join(goodnight_file.read().split())
        except OSError as error:
            logging.warning(f"could not read {GOODNIGHT_FILE}: {error}")
            return ""

    async def line_died(self, channel_name: str, error: Exception) -> None:
        """The line to Twitch is dead and TwitchIO hasn't noticed (10-09: a
        websocket stuck "closing" for 3h46m — she heard the stream through
        her ear and answered into a closed pipe, deaf to chat and mute,
        nothing noticing). The body says so in the record, in the
        machinery's name, and restarts itself: systemd brings her back on a
        non-zero exit, and the wake reads the restart as chosen — by the
        machinery, labeled — with the hole named: chat since the line last
        spoke went unheard and is not in the record. The goodnight is not
        said; there is no line to say it on."""
        if getattr(self, "_line_died", False):  # the tests' bot skips __init__
            return
        self._line_died = True
        now = core.datetime.datetime.now(core.datetime.UTC).strftime("%H:%M UTC")
        heard = capture.last_heard_at()
        since = (
            core.datetime.datetime.fromisoformat(heard).strftime("%H:%M UTC")
            if heard
            else "the body's start"
        )
        told = core.machinery_line(
            f"the line to Twitch died ({type(error).__name__}) — faebot's body "
            f"restarts itself at {now} to get it back; the line last spoke at "
            f"{since}, and anything chat said after that went unheard and is not "
            f"in the record"
        )
        logging.error(told)
        core.ensure_conversation(channel_name).chatlog.append(told)
        core.put_event(
            self.event_queue,
            {"type": "error", "channel": channel_name, "error": told},
        )
        await asyncio.sleep(0.5)  # the dashboard's event and the log, out
        # The witness row goes LAST, right before the exit: the ear is on
        # another machine and keeps landing voice rows while the line is
        # dead, and the wake's witness is the record's newest row — a voice
        # row after this one would read the stop as unchosen (the 24th
        # reader's must-fix).
        capture.record_restart(
            channel_name, told, how="line-died", unheard_since=heard, error=str(error)
        )
        self.restart_self()

    def restart_self(self) -> None:
        """Leave non-zero so the unit's Restart=on-failure brings her back."""
        os._exit(DEAD_LINE_EXIT)

    async def goodnight(self):
        """A chosen stop: tell her a restart is coming, let her say her last
        word (or pass — the room's state is stamped, the choice is hers), and
        record what came of it. If the moment doesn't let her speak — the
        call fails or runs out of time — the goodnight she saved earlier is
        spoken by the machinery, in its own name. An outage never gets here:
        that stop gets the machinery's account at the next wake, not her words.
        """
        # One budget for all her rooms, inside the unit's stop timeout —
        # not one per room, or two rooms would outrun the force-exit.
        deadline = asyncio.get_running_loop().time() + GOODNIGHT_SECONDS
        for connected in self.connected_channels:
            name = connected.name
            channel = self.get_channel(name)
            conversation = core.ensure_conversation(name)
            state = self.stream_watch.state.get(name)
            room = ""
            if state is not None:
                room = (
                    " (the stream is live)"
                    if state.live
                    else " (the stream is offline)"
                )
            now = core.datetime.datetime.now(core.datetime.UTC).strftime("%H:%M UTC")
            told = core.machinery_line(
                f"a restart is coming at {now}{room} — this is faebot's last word before it"
            )
            conversation.chatlog.append(told)
            logging.info(told)
            try:
                outcome = await asyncio.wait_for(
                    self._generate_and_send(name, trigger_type="restart"),
                    max(0.0, deadline - asyncio.get_running_loop().time()),
                )
            except asyncio.TimeoutError:
                outcome = "timed-out"
            except Exception as error:
                logging.warning(f"goodnight failed: {type(error).__name__}: {error}")
                outcome = "failed"
            said = None
            if outcome not in ("said", "passed"):
                saved = self.saved_goodnight()
                if saved:
                    said = (
                        "(faebot wrote this earlier, for a moment like this one — "
                        f"the moment didn't let her say it herself) {saved}"
                    )
                    try:
                        await channel.send(said[:499])
                        outcome = "saved"
                    except Exception as error:
                        logging.warning(
                            f"saved goodnight not sent: {type(error).__name__}: {error}"
                        )
                        outcome = "saved-send-failed"
            capture.record_restart(name, told, how=outcome, said=said)
            logging.info(f"goodnight in {name}: {outcome}")

    async def event_message(self, message):
        if message.echo:
            # Capture tap — Twitch's native view of faebot's own line (echo).
            # The reply loop is (correctly) short-circuited on echoes, but we still
            # record it so faebot's utterance also has Twitch-native metadata (real
            # message-id, tags) alongside the richer send-point record_faebot_message.
            # Two faithful views of one act (echo=True marks it); reconcile offline.
            capture.record_chat(message)
            return

        logging.debug(f"received message: {message.author}: {message.content}")

        # Capture tap — faithful, opt-in, never interferes (capture.py).
        # Record every non-echo message verbatim (commands included); the offline
        # transducer decides what matters.
        capture.record_chat(message)

        core.ensure_conversation(message.channel.name)

        if (
            message.content.startswith("!")
            or message.content.startswith("fb;")
            or message.content.startswith("fae;")
        ):
            return await self.handle_commands(message)

        display_name = core.aliases.get(message.author.name, message.author.name)
        core.conversations[message.channel.name].chatlog.append(
            f"{display_name}: {message.content}"
        )

        conversation = core.conversations[message.channel.name]
        if "faebot" in message.content.lower():
            logging.info(f"faebot mentioned by {display_name}, replying")
            frequency = 1.0
        else:
            frequency = conversation.frequency
        if core.choose_to_reply(message.channel.name, frequency):
            return asyncio.create_task(
                self._generate_and_send(message.channel.name, trigger_type="chat")
            )

    async def close(self):
        """Close the bot's resources gracefully. The goodnight is not here:
        local.py says it first, while the chat line is still open."""
        for task in self.watch_tasks:
            task.cancel()
        await core.close_session()
        await super().close()


if __name__ == "__main__":
    if not TWITCH_TOKEN:
        logging.error("TWITCH_TOKEN not set. Did you forget to source secrets?\n")
    else:
        bot = Faebot()
        bot.run()
