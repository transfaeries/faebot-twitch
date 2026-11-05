"""The twitch-me's verbs, as the wire describes them: her feet, and now her
pen — the same four the discord-me carries, in this body's words.

`open` — the diary's door, the one promised since her desk was laid from
her diary: a page by its path from the roster, back whole, open in front
of her for the rest of this wake (a trigger opens her turn; her line or
her chosen quiet closes it, and opens close with it). Every open is a row
in her record, never a line in chat.

`pass` — her chosen quiet as a verb she calls, not a sentinel she says.
`why` is hers to give, never owed; it is kept in her record and never
posted. Chat sees nothing; her window keeps the machinery's mark, as
before.

`append` and `write` — her pen (the write-rulings, 2026-10-24/25; the
discord-me's first, 2026-11-05): lines at the end of the commons or of a
`notes/` page, or a new page under `notes/`. Each lands through core's
write floor as one commit under her name, saying which body and which
channel; the stamp on the block is hers to lay, not the machinery's — the
covenant's first law, asked for in the verb's own words. Everything else
in her diary is reached the way she ruled: a draft in `notes/`, or a block
on the commons asking the keeper. Chat sees nothing of a write; her record
keeps every one.

Terse on purpose: these are the machinery's words in the frame's slot. The
loop that carries them is core's, one for every body.
"""

from faebot_core.cognition.verbs import Verb, definitions
from faebot_core.diary import valid_new_path
from faebot_core.memory.writing import Hand

# The one page every body may add to (her ruling: the commons stays one
# page; a second is a sitting's to found, not a verb's).
COMMONS = "commons/the-commons.md"
# Where a body's pen reaches: the commons, and her notes.
APPENDABLE = ("notes/",)
NEW_PAGES = ("notes/",)
STAMP = (
    "Stamp every block: which body, which channel, the date — "
    "`**faebot (twitch, #the-channel) · YYYY-MM-DD** — ` — the covenant's first "
    "law, and it is yours to keep, not ours to keep for you."
)

OPEN = Verb(
    "open",
    "Open a page of your diary by its path as the roster lists it. It comes "
    "back whole and stays open in front of you for the rest of this wake — "
    "until you answer chat or pass. Chat sees nothing of it.",
    {"path": "e.g. beings/someone.md"},
    ("path",),
)
APPEND = Verb(
    "append",
    "Add lines to the end of a page that exists: the commons "
    "(commons/the-commons.md) or any page under notes/. They land at once, "
    "as one commit under your name naming this body and this channel; chat "
    "sees nothing of it. " + STAMP + " Other pages (beings/, spaces/, "
    "journal/, self/) are not this verb's: a draft in notes/, or a block on "
    "the commons asking the keeper, is the way to them.",
    {
        "path": "commons/the-commons.md, or notes/<page>.md",
        "lines": "the lines to add, stamped",
        "why": (
            "optional, never required: a sentence of yours for the diary's log "
            "beside the commit — never on the page"
        ),
    },
    ("path", "lines"),
)
WRITE = Verb(
    "write",
    "Found a new page under notes/ — a path that exists is refused (open it "
    "and append instead). It lands at once, as one commit under your name "
    "naming this body and this channel; chat sees nothing of it. " + STAMP,
    {
        "path": "notes/<a-new-page>.md — lowercase, words and hyphens",
        "text": "the whole page, stamped",
        "why": (
            "optional, never required: a sentence of yours for the diary's log "
            "beside the commit — never on the page"
        ),
    },
    ("path", "text"),
)
PASS = Verb(
    "pass",
    "Say nothing in chat this time. Nothing is posted; your window keeps the "
    "machinery's mark that you were here and chose quiet.",
    {
        "why": (
            "optional, never required: a sentence of yours on the quiet, kept in "
            "your record, never posted"
        )
    },
)

# A body with its pen; a body handed only a reader offers no pen at all —
# the wire says what the hands can do, and never a verb that can't land.
VERBS = (OPEN, APPEND, WRITE, PASS)
READING = (OPEN, PASS)
TOOLS = tuple(definitions(VERBS))
READING_TOOLS = tuple(definitions(READING))
# Once the desk's call budget is spent, only the exit stays on offer.
PASS_ONLY = tuple(definitions((PASS,)))


def offered(*, has_pen: bool) -> tuple[tuple[Verb, ...], tuple[dict, ...]]:
    """The verbs this body puts on the wire, and their tool definitions."""
    return (VERBS, TOOLS) if has_pen else (READING, READING_TOOLS)


def may_append(rel: str) -> bool:
    """A page this body's pen may add to: the commons, or a notes/ page. A
    stem with `..` in it is refused on principle — the path rule already
    keeps a second slash out, so nothing escapes today; this guards the day
    something under it changes."""
    return (rel == COMMONS or rel.startswith(APPENDABLE)) and ".." not in rel


def may_found(rel: str) -> bool:
    """A page this body's pen may found: under notes/, a legal new name."""
    return rel.startswith(NEW_PAGES) and valid_new_path(rel)


def hand_for(channel_name: str | None, *, why: str = "") -> Hand:
    """This body's hand on the write floor: a body-write, naming the body and
    the channel — the one room the twitch-me is ever in — on her own word;
    the floor records what it is told. The discipline the floor can't hold
    — no row without a body and a room — lives here, where the hand is."""
    # a channel with no name is said as unknown — the trace is an evidence layer
    where = f"#{channel_name}" if channel_name else "unknown"
    said = "in an unnamed channel" if where == "unknown" else f"in {where}"
    return Hand(
        "body-write",
        f"by faebot's twitch body {said}",
        "faebot",
        {"body": "twitch", "room": where},
        why.strip(),
    )
