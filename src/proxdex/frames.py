"""Frame-size guides: where a card's printed border sits, keyed by game + era.

The align overlay draws the card outline plus these inner border lines (square
corners — the lines meet at 90° regardless of the trim's corner rounding) so a
scan can be expanded until its border matches the real card. Insets are
fractions of the card, per edge; a uniform physical border is a smaller
fraction of the long axis than the short axis, so top/bottom ≠ left/right.

Each guide records how much to trust it. Only :attr:`FrameGuide.measured`
guides come from calipers on a real card; everything else is a documented
estimate, and the CLI (:command:`proxdex frames`, the ``border`` readout) and
the UI say so rather than pretending the spec is known. Add a
:class:`FrameGuide` and its set ids as more eras get measured.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from proxdex.games import GameId


class GuideId(StrEnum):
    POKEMON_WOTC = "pokemon-wotc"
    POKEMON_GENERIC = "pokemon-generic"
    MTG_BORDERED = "mtg-bordered"
    BORDERLESS = "borderless"


class Confidence(StrEnum):
    #: calipers on a real card — trust the fit
    MEASURED = "measured"
    #: a documented guess; the CLI and UI say so rather than pretend
    ESTIMATED = "estimated"


@dataclass(frozen=True, slots=True)
class FrameGuide:
    id: GuideId
    name: str
    #: the game this guide describes; None = applies to any game
    game: GameId | None
    #: inner border edge inset as card fractions, [top, right, bottom, left]
    inset: tuple[float, float, float, float]
    confidence: Confidence
    #: where the numbers came from / what to check before trusting them
    note: str = ""

    @property
    def measured(self) -> bool:
        return self.confidence is Confidence.MEASURED

    def mm(self, w: float = 63.0, h: float = 88.0) -> tuple[float, float, float, float]:
        """The inset back as per-edge millimetres of a ``w``×``h`` card."""
        top, right, bottom, left = self.inset
        return (top * h, right * w, bottom * h, left * w)


def _mm(
    top: float,
    right: float,
    bottom: float,
    left: float,
    w: float = 63.0,
    h: float = 88.0,
) -> tuple[float, float, float, float]:
    """Per-edge border widths (mm) → inset fractions [top, right, bottom, left]
    of a ``w``×``h`` mm card. Insets are taken against the true card so the
    ratios stay consistent with the card aspect (no reference-size skew)."""
    return (top / h, right / w, bottom / h, left / w)


# --- Pokémon ----------------------------------------------------------------
# Base Set..Neo Destiny (yellow-border WOTC era). Measured off a real card with
# calipers (top 3.3 / bottom 3.6 / left 3.2 / right 3.1 mm); the border wanders a
# little card-to-card, so we use the tidy averages top/bottom 3.45, sides 3.15mm.
_POKEMON_WOTC = FrameGuide(
    id=GuideId.POKEMON_WOTC,
    name="Pokémon · WOTC vintage (Base-Neo Destiny)",
    game=GameId.POKEMON,
    inset=_mm(3.45, 3.15, 3.45, 3.15),
    confidence=Confidence.MEASURED,
    note="calipers on a real card: top 3.3 / bottom 3.6 / sides 3.1-3.2 mm.",
)

# Everything from e-Card onward. The yellow border stayed visually similar, but
# nobody has put calipers on one for proxdex yet — so this reuses the WOTC
# averages and says out loud that it is a guess.
_POKEMON_GENERIC = FrameGuide(
    id=GuideId.POKEMON_GENERIC,
    name="Pokémon · generic (era not measured)",
    game=GameId.POKEMON,
    inset=_mm(3.45, 3.15, 3.45, 3.15),
    confidence=Confidence.ESTIMATED,
    note=(
        "no measurement for this era — reuses the WOTC border widths. "
        "Measure a real card and add a guide before trusting the fit."
    ),
)

# --- Magic: The Gathering ---------------------------------------------------
# MTG's frame is uniform: unlike Pokémon there is no thicker bottom border (the
# type line and copyright sit *inside* the frame). That holds across the old,
# modern and M15 frames and across black- and white-bordered printings, so one
# guide covers every bordered MTG set.
_MTG_BORDERED = FrameGuide(
    id=GuideId.MTG_BORDERED,
    name="MTG · standard border (all bordered sets)",
    game=GameId.MTG,
    inset=_mm(3.0, 3.0, 3.0, 3.0),
    confidence=Confidence.ESTIMATED,
    note=(
        "nominal 3.0 mm border, uniform on all four edges — MTG's frame does "
        "not thicken at the bottom. Not verified with calipers."
    ),
)

# --- any game ---------------------------------------------------------------
# Borderless / full-art printings have no frame at all, so the fit is pure
# aspect correction. Never inferred from a *set* id — a modern set mixes bordered
# and borderless prints in the same numbering — but it *is* inferred per card
# from what the provider says about that printing (Scryfall's `border_color` and
# `full_art`), recorded in the card's own `.frame` marker. `border --frame`
# overrides either way.
_BORDERLESS = FrameGuide(
    id=GuideId.BORDERLESS,
    name="Borderless / full-art (no printed frame)",
    game=None,
    inset=(0.0, 0.0, 0.0, 0.0),
    confidence=Confidence.MEASURED,
    note="no frame to match — reshapes to the card aspect only.",
)

GUIDES: dict[GuideId, FrameGuide] = {
    g.id: g for g in (_POKEMON_WOTC, _POKEMON_GENERIC, _MTG_BORDERED, _BORDERLESS)
}

#: per game, the guide used when no era rule matches
FALLBACK: dict[GameId, FrameGuide] = {
    GameId.POKEMON: _POKEMON_GENERIC,
    GameId.MTG: _MTG_BORDERED,
}

# set-id prefixes per era, per game. Pokémon ids come from pokemontcg.io
# (base1-6, gym1-2, neo1-4); MTG uses one guide for every set, so it needs none.
_ERAS: dict[GameId, tuple[tuple[tuple[str, ...], GuideId], ...]] = {
    GameId.POKEMON: ((("base", "gym", "neo"), GuideId.POKEMON_WOTC),),
    GameId.MTG: (),
}


def parse(value: str | None) -> GuideId | None:
    """A guide id from untrusted text (a CLI flag, a UI request body)."""
    try:
        return GuideId(str(value).strip().lower())
    except ValueError:
        return None


def for_set(set_id: str, game: GameId = GameId.POKEMON) -> FrameGuide:
    """The frame guide for a set id in ``game``.

    Falls back to that game's generic guide — which is flagged
    :attr:`Confidence.ESTIMATED`, so callers warn instead of silently guessing.
    """
    sid = (set_id or "").lower()
    for prefixes, guide_id in _ERAS[game]:
        if sid.startswith(prefixes):
            return GUIDES[guide_id]
    return FALLBACK[game]


def resolve(
    set_id: str, game: GameId = GameId.POKEMON, override: GuideId | None = None
) -> FrameGuide:
    """The guide a fit should run against.

    ``override`` is either what the user typed (``border --frame``) or what the
    card itself records (its ``.frame`` marker, written at fetch time when the
    provider said the printing is borderless) — a card knows its own frame better
    than its set id does.
    """
    if override is not None:
        return GUIDES[override]
    return for_set(set_id, game)


def choices(game: GameId | None = None) -> list[FrameGuide]:
    """Guides selectable for ``game`` (its own, plus the game-agnostic ones)."""
    return [
        g for g in GUIDES.values() if g.game is None or game is None or g.game is game
    ]
