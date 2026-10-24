"""The twitch-me's verbs, as the wire describes them: two to begin with,
the same two the discord-me got first.

`open` — the diary's door, the one promised since her desk was laid from
her diary: a page by its path from the roster, back whole, open in front
of her for the rest of this wake (a trigger opens her turn; her line or
her chosen quiet closes it, and opens close with it). Every open is a row
in her record, never a line in chat.

`pass` — her chosen quiet as a verb she calls, not a sentinel she says.
`why` is hers to give, never owed; it is kept in her record and never
posted. Chat sees nothing; her window keeps the machinery's mark, as
before.

Terse on purpose: these are the machinery's words in the frame's slot. The
loop that carries them is core's, one for every body.
"""

from faebot_core.cognition.verbs import Verb, definitions

OPEN = Verb(
    "open",
    "Open a page of your diary by its path as the roster lists it. It comes "
    "back whole and stays open in front of you for the rest of this wake — "
    "until you answer chat or pass. Chat sees nothing of it.",
    {"path": "e.g. beings/someone.md"},
    ("path",),
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

VERBS = (OPEN, PASS)
TOOLS = tuple(definitions(VERBS))
# Once the desk's call budget is spent, only the exit stays on offer.
PASS_ONLY = tuple(definitions((PASS,)))
