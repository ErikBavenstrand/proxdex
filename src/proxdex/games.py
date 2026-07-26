"""The trading-card games proxdex knows how to fetch, frame and print.

A :class:`Game` is the small bundle of facts that differ per TCG: what its
card ids look like, which API answers for them, the nominal trim size, and
whether the card back can be downloaded at all. Everything downstream —
sources, frame guides, card backs — keys off a :class:`GameId`.

Which game a card belongs to is *filesystem state* like everything else: a
``.game`` file in the card folder (see :mod:`proxdex.library`). Set ids alone
can't tell you — Pokémon and MTG both use short lowercase codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GameId(StrEnum):
    POKEMON = "pokemon"
    MTG = "mtg"


#: used when nothing on disk or in config says otherwise
DEFAULT = GameId.POKEMON


class Layout(StrEnum):
    """How a card is physically printed.

    Providers name two dozen layouts (``saga``, ``adventure``, ``prototype``,
    ``leveler``, …) but almost all of them are one ordinary picture on one side
    of one card — a difference in *rules text*, not in ink. proxdex only needs
    the cases that change what goes on paper, so the provider's own word travels
    on as a readable fact and this enum carries the print consequence.
    """

    #: one printed side (the overwhelming majority, and every Pokémon card)
    SINGLE = "single"
    #: two printed sides, each its own picture — transform, modal DFC,
    #: reversible and art-series cards, and double-faced tokens
    DOUBLE = "double"
    #: half of a meld pair: prints as a normal card, and its real reverse is
    #: half of the melded card, which proxdex files as its own card instead
    MELD_PART = "meld-part"
    #: the melded card — a third card with its own id and its own image
    MELD_RESULT = "meld-result"

    @property
    def label(self) -> str:
        return _LAYOUT_LABELS[self]

    @property
    def note(self) -> str:
        """One line on what this means for printing, for the CLI and the UI."""
        return _LAYOUT_NOTES[self]

    @property
    def sides(self) -> int:
        """How many printed sides a card of this layout has."""
        return 2 if self is Layout.DOUBLE else 1


_LAYOUT_LABELS: dict[Layout, str] = {
    Layout.SINGLE: "Single-sided",
    Layout.DOUBLE: "Double-faced",
    Layout.MELD_PART: "Meld part",
    Layout.MELD_RESULT: "Meld result",
}

_LAYOUT_NOTES: dict[Layout, str] = {
    Layout.SINGLE: "One printed side; the reverse is the shared card back.",
    Layout.DOUBLE: "Two printed sides, each with its own pipeline. Print duplex, "
    "or flip which side goes on the paper.",
    Layout.MELD_PART: "Melds with its partner into a third card. All three print "
    "as ordinary cards — fetch the related ones too.",
    Layout.MELD_RESULT: "The melded card, printed as an ordinary card of its own.",
}

#: planar, scheme and Vanguard cards are printed at this size, not 63×88 — so a
#: sheet imposed at the standard trim would print them too small. proxdex says
#: so rather than silently shrinking them.
OVERSIZED_W_MM = 88.9
OVERSIZED_H_MM = 127.0


def parse_layout(value: str | None) -> Layout | None:
    """A layout from untrusted text (a marker file, a request body)."""
    try:
        return Layout(str(value).strip().lower())
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Game:
    id: GameId
    name: str
    #: nominal trim size in mm — both current games are the standard 63×88
    card_w_mm: float
    card_h_mm: float
    #: shape of a canonical card id, for help text and error messages
    id_example: str
    #: where a card back can be downloaded from; None = no reliable source
    back_url: str | None
    #: one line on where the metadata comes from
    source: str
    #: a public page for one card, as ``{set}``/``{number}`` — used when the
    #: provider's own response carries no link to it. None = the API links out
    #: itself (Scryfall does), or no stable per-card URL exists.
    card_page: str | None = None
    #: what ``card_page`` is, for the link's label
    card_page_name: str = ""


POKEMON = Game(
    id=GameId.POKEMON,
    name="Pokémon",
    card_w_mm=63.0,
    card_h_mm=88.0,
    id_example="ex3-90",
    # the back is a single image owned by TPC — no API serves it
    back_url=None,
    source="pokemontcg.io (metadata) + scrydex (images)",
    # pokemon.com's own card database has no stable per-card URL (it addresses
    # cards through a search form), so the card page links to Limitless, which
    # keys off the same <set>/<number> pokemontcg.io ids do.
    card_page="https://limitlesstcg.com/cards/{set}/{number}",
    card_page_name="Limitless TCG",
)

MTG = Game(
    id=GameId.MTG,
    name="Magic: The Gathering",
    card_w_mm=63.0,
    card_h_mm=88.0,
    id_example="neo-136",
    back_url="https://cards.scryfall.io/back.png",
    source="Scryfall (metadata + images)",
)

GAMES: dict[GameId, Game] = {g.id: g for g in (POKEMON, MTG)}


def parse(value: str | None) -> GameId | None:
    """A game id from untrusted text (TOML, a marker file, a query param)."""
    try:
        return GameId(str(value).strip().lower())
    except ValueError:
        return None


def coerce(value: str | None, fallback: GameId = DEFAULT) -> GameId:
    """Like :func:`parse`, but never fails — unknown text means ``fallback``."""
    return parse(value) or fallback


def get(game_id: GameId) -> Game:
    return GAMES[game_id]


def order(preferred: GameId = DEFAULT) -> tuple[GameId, ...]:
    """Game ids to try, preferred first — used when an id's game is unknown."""
    return (preferred, *(g for g in GameId if g is not preferred))
