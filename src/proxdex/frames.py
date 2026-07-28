"""Frame specs: where a card's printed border sits.

The align overlay draws the card outline plus these inner border lines (square
corners — the lines meet at 90° regardless of the trim's corner rounding) so a
scan can be expanded until its border matches the real card. Insets are
fractions of the card, per edge; a uniform physical border is a smaller
fraction of the long axis than the short axis, so top/bottom ≠ left/right.

**A spec is four numbers and a note about where they came from — nothing else.**
There is deliberately no confidence level, no provenance enum and no coverage
grade. An earlier version had all three, and they were built on a premise that is
false: that reading a border off the publisher's scan measures the card. It does
not. A scan carries its own crop, and a crop that trims 0.3mm inside the cut edge
shrinks every border read off it by 0.3mm with nothing in the image to say so —
no sample size and no agreement between cards removes that, because it is
systematic. Grading such a number as "scanned, therefore trusted" dressed the
guess up. So the note says what was measured and on what, in words, and that is
the whole of it.

The numbers that ship are **working defaults**: enough that a fresh library
borders a card sensibly with nothing configured, and every one of them is meant
to be replaced by a caliper reading (``proxdex frames set``, and
``docs/measuring-frames.md`` says which cards to measure and how). A library's
own file always wins.

This module owns the *geometry* and the specs proxdex **ships**.
:mod:`proxdex.specs` owns the ones a library adds, the rules that say which card
gets which, and the resolution that picks one — so a spec id is an open set (a
user can measure a new era tonight) while the shipped ids stay a closed one.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from proxdex.games import GameId


#: shipped spec ids. Closed on purpose: these are the ones code names — the
#: fallbacks, the era rules, and `borderless`, which :func:`proxdex.sources`
#: returns when a provider says a printing has no frame. A library's own specs
#: are plain ids validated by :func:`valid_id`.
class GuideId(StrEnum):
    POKEMON_WOTC = "pokemon-wotc"
    POKEMON_GENERIC = "pokemon-generic"
    MTG_1993 = "mtg-1993"
    MTG_1997 = "mtg-1997"
    MTG_2003 = "mtg-2003"
    MTG_M15 = "mtg-m15"
    MTG_EXTENDED_ART = "mtg-extended-art"
    MTG_YELLOW_BAND = "mtg-yellow-band"
    BORDERLESS = "borderless"


#: `borderless` cannot be redefined or removed: code *returns* it, so there has
#: to be a spec by that name whatever a library has done to the rest.
RESERVED: frozenset[str] = frozenset({GuideId.BORDERLESS.value})

#: what a spec id may look like — it is a filename, a CLI value and a URL
#: segment, so keep it to the shape every one of those carries without quoting
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ID_MAX = 48


@dataclass(frozen=True, slots=True)
class FrameGuide:
    """One frame's border widths, as fractions of the card they were taken from."""

    id: str
    name: str
    #: the game this spec describes; None = applies to any game
    game: GameId | None
    #: inner border edge inset as card fractions, [top, right, bottom, left]
    inset: tuple[float, float, float, float]
    #: where the numbers came from, in words. The only account there is of how
    #: much to trust them, and deliberately prose rather than a grade.
    note: str = ""
    #: the card size the insets were taken against. Fractions travel fine between
    #: sizes, but the millimetres shown to a human do not: an oversized card's 3mm
    #: border is a different fraction from a 63×88 one's. It also matters for a
    #: caliper reading, since a real card is 63.5×88.9mm rather than the 63×88
    #: proxdex trims to — measure the card, then say what you measured.
    ref_mm: tuple[float, float] = (63.0, 88.0)

    @property
    def frameless(self) -> bool:
        """No printed frame at all, so a fit is pure aspect correction."""
        return not any(self.inset)

    def mm(
        self, w: float | None = None, h: float | None = None
    ) -> tuple[float, float, float, float]:
        """The inset back as per-edge millimetres of a ``w``×``h`` card."""
        ref_w, ref_h = self.ref_mm
        width, height = w if w is not None else ref_w, h if h is not None else ref_h
        top, right, bottom, left = self.inset
        return (top * height, right * width, bottom * height, left * width)

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "game": self.game.value if self.game else None,
            "inset": [round(v, 6) for v in self.inset],
            "note": self.note,
            "ref_mm": list(self.ref_mm),
        }


def is_shipped(spec_id: str) -> bool:
    """Is this an id proxdex ships? A library may still hold its own numbers for
    it — that is the point of ``frames set`` — but the id is not one it invented."""
    return spec_id in SHIPPED


def valid_id(value: str) -> bool:
    """Is ``value`` usable as a spec id — a filename, a flag value, a URL part?"""
    return bool(_ID.match(value or "")) and len(value) <= _ID_MAX


def clean_id(value: str | None) -> str:
    """Untrusted text as a spec id, or ``""`` if it is not one."""
    text = str(value or "").strip().lower()
    return text if valid_id(text) else ""


def parse(value: str | None) -> str | None:
    """A spec id from untrusted text (a CLI flag, a request body, a marker).

    Only the *shape* is checked here — whether a spec by that id exists is a
    question for the library's registry (:func:`proxdex.specs.load`), which is
    the only thing that knows what this library has measured.
    """
    return clean_id(value) or None


def mm_to_inset(
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


_mm = mm_to_inset  # the name this module used before the specs split

#: said once, by every spec whose numbers nobody has put calipers on. The specs
#: still need four numbers to fit against, so these are the working defaults —
#: and this sentence is how a listing says they are not measurements.
PROVISIONAL = (
    "Provisional: read off the publisher's own card scans, which carry each "
    "scan's crop, so the number can be uniformly wrong with nothing in the image "
    "to show it. Measure a real card and replace it — `proxdex frames set`, and "
    "docs/measuring-frames.md says which card and how."
)


# --- Pokémon ----------------------------------------------------------------
# Base Set..Neo Destiny (yellow-border WOTC era). Calipers on a real card
# (top 3.3 / bottom 3.6 / left 3.2 / right 3.1 mm); the border wanders a little
# card-to-card, so we use the tidy averages top/bottom 3.45, sides 3.15mm.
_POKEMON_WOTC = FrameGuide(
    id=GuideId.POKEMON_WOTC.value,
    name="Pokémon · WOTC vintage (Base-Neo Destiny)",
    game=GameId.POKEMON,
    inset=_mm(3.45, 3.15, 3.45, 3.15),
    note="Calipers on a real card: top 3.3 / bottom 3.6 / sides 3.1-3.2 mm.",
)

# Everything from e-Card onward. The yellow border stayed visually similar, but
# nobody has put calipers on one for proxdex yet.
_POKEMON_GENERIC = FrameGuide(
    id=GuideId.POKEMON_GENERIC.value,
    name="Pokémon · generic (era not measured)",
    game=GameId.POKEMON,
    inset=_mm(3.45, 3.15, 3.45, 3.15),
    note=(
        "Nothing measured for this era — reuses the WOTC widths, which is a guess "
        "that 20 years of frame revisions did not move the border. Pokémon's own "
        "scans are cropped inconsistently enough that reading it off one is worse "
        "than saying so. Measure a modern card and replace this."
    ),
)

# --- Magic: The Gathering ---------------------------------------------------
# One spec per frame generation, and that split comes from the metadata rather
# than from anybody's eye: Scryfall documents exactly five `frame` values — 1993,
# 1997, 2003, 2015, future — so the list is closed and `FRAME_GENERATIONS` covers
# all of it (pinned by a test, which is how a sixth announces itself). A *set*
# cannot answer which applies: a 2023 set holds retro-frame cards at the old width
# beside modern ones (`dmr-354` is frame 1997 inside a frame 2015 set), so the
# baseline reads the printing's own frame, recorded in the card's `.traits`.
#
# The numbers are provisional and say so. They come from `scripts/mtg-census.py`
# reading Scryfall's scans, which is a survey — good enough to establish that the
# five generations really are four distinct widths (1993/1997 thicken top and
# bottom, 2003 made all four edges equal, M15 took ~0.55mm off everything) and not
# good enough to be the spec, because every one of them inherits the scans' crop.
# `docs/measuring-frames.md` names the five cards that settle it.
_MTG_1993 = FrameGuide(
    id=GuideId.MTG_1993.value,
    name="MTG · 1993 frame (Alpha-4th Edition)",
    game=GameId.MTG,
    inset=_mm(3.55, 2.96, 3.55, 2.96),
    note=(
        "Top and bottom read thicker than the sides on this generation. Alpha is "
        f"cut differently again (~3.20 sides, ~4.26 bottom) — pin those. {PROVISIONAL}"
    ),
)

_MTG_1997 = FrameGuide(
    id=GuideId.MTG_1997.value,
    name="MTG · 1997 frame (Mirage-7th Edition)",
    game=GameId.MTG,
    inset=_mm(3.38, 3.05, 3.38, 3.05),
    note=f"Top and bottom still thicker than the sides, by less. {PROVISIONAL}",
)

_MTG_2003 = FrameGuide(
    id=GuideId.MTG_2003.value,
    name="MTG · 2003 frame (8th Edition-M14)",
    game=GameId.MTG,
    inset=_mm(3.00, 3.00, 3.00, 3.00),
    note=f"The redesign that made all four edges equal. {PROVISIONAL}",
)

# Everything from Magic 2015 onward, which is most of what anyone proxies: 71110 of
# MTG's ~106000 prints, and this game's fallback for that reason.
_MTG_M15 = FrameGuide(
    id=GuideId.MTG_M15.value,
    name="MTG · M15 frame (Magic 2015 onward)",
    game=GameId.MTG,
    inset=_mm(2.45, 2.45, 2.45, 2.45),
    note=(
        "About 0.55mm narrower than every frame before it, which is the reduction "
        "Wizards announced for M15. Full-art printings of this generation measure "
        "the same, which is why full-art is not treated as borderless. The bottom "
        "edge cannot be read off a scan at all — the black collector strip sits in "
        f"it — so it is assumed symmetric with the top. {PROVISIONAL}"
    ),
)

# Two treatments really do change the geometry, and the other ~24 really do not.
# `scripts/mtg-variants.py` measured all 54 (frame x border_color x frame_effects)
# combinations that have 20+ printings: 31 of them come out at their own
# generation's border, so a legendary crown, an inverted text box, the Nyx
# enchantment treatment, an etched foil, `snow`, `devoid`, `miracle`, `companion`,
# `draft`, `spree`, `colorshifted` and `fullart` need no spec and no rule. That is
# the survey's most useful result and it is a measurement, not an assumption.
#
# Extended art is the exception that has a shape no four-edge inset described before:
# the picture runs to the **left and right card edges** while the top and bottom keep
# the generation's border. So its sides are 0 — which is exactly what the detector
# reports by refusing to measure them, over 2824 printings — and its top reads 2.40
# against an ordinary 2.48. It only exists on the M15 frame.
_MTG_EXTENDED_ART = FrameGuide(
    id=GuideId.MTG_EXTENDED_ART.value,
    name="MTG · extended art (art to the left and right edges)",
    game=GameId.MTG,
    inset=_mm(2.45, 0.0, 2.45, 0.0),
    note=(
        "Top and bottom keep the M15 border; the sides have none, because the art "
        "runs off the card. The detector agrees by declining to measure the sides on "
        f"every sample of 2824 printings. {PROVISIONAL}"
    ),
)

# The one border *colour* that is also a geometry: Aetherdrift's yellow full-art box
# toppers carry a decorative band measuring 4.70mm on the sides against an ordinary
# 2.45 — a 2.25mm error, the largest in the whole survey, on 79 printings. Every other
# colour (white, gold, silver) measures at its generation's width.
_MTG_YELLOW_BAND = FrameGuide(
    id=GuideId.MTG_YELLOW_BAND.value,
    name="MTG · yellow band (Aetherdrift box toppers)",
    game=GameId.MTG,
    inset=_mm(4.19, 4.70, 4.19, 4.70),
    note=(
        "A decorative band, not a border colour: 4.70mm on the sides and 4.15-4.23 "
        f"top over 79 printings, against 2.45 for an ordinary card. {PROVISIONAL}"
    ),
)


# --- any game ---------------------------------------------------------------
# Borderless / art-series printings have no frame at all, so the fit is pure
# aspect correction. Never inferred from a *set* id — a modern set mixes bordered
# and borderless prints in the same numbering — but it *is* inferred per card from
# what the provider says about that printing (Scryfall's `border_color`), recorded
# in the card's own `.frame` marker. `border --frame` overrides either way.
_BORDERLESS = FrameGuide(
    id=GuideId.BORDERLESS.value,
    name="Borderless (no printed frame)",
    game=None,
    inset=(0.0, 0.0, 0.0, 0.0),
    note="No frame to match — reshapes to the card aspect only. Nothing to measure.",
)

#: the specs proxdex ships. A library's registry starts from these, so a fresh
#: library borders a Base Set card correctly with nothing configured.
SHIPPED: dict[str, FrameGuide] = {
    g.id: g
    for g in (
        _POKEMON_WOTC,
        _POKEMON_GENERIC,
        _MTG_1993,
        _MTG_1997,
        _MTG_2003,
        _MTG_M15,
        _MTG_EXTENDED_ART,
        _MTG_YELLOW_BAND,
        _BORDERLESS,
    )
}

#: per game, the spec used when nothing else answers. For MTG that means a card
#: whose frame generation was never recorded — filed before proxdex kept traits —
#: so it takes the M15 frame, which two thirds of all MTG prints carry and which is
#: therefore the least-wrong answer to "no idea". Re-fetching the card records its
#: frame and resolves it exactly.
FALLBACK: dict[GameId, str] = {
    GameId.POKEMON: GuideId.POKEMON_GENERIC.value,
    GameId.MTG: GuideId.MTG_M15.value,
}

#: set-id prefixes per era, per game — the shipped baseline a library's own rules
#: are consulted *before*. Pokémon ids come from pokemontcg.io (base1-6, gym1-2,
#: neo1-4); MTG's split is by frame generation rather than by set, see below.
ERAS: dict[GameId, tuple[tuple[tuple[str, ...], str], ...]] = {
    GameId.POKEMON: ((("base", "gym", "neo"), GuideId.POKEMON_WOTC.value),),
    GameId.MTG: (),
}

#: MTG's border width changed with the frame and **not** with the set, so the
#: baseline reads the generation Scryfall names per printing (``frame``), which the
#: card records in its ``.traits``. One spec each, no aliasing: five documented
#: values, five entries, and a test that fails if Scryfall adds a sixth.
FRAME_GENERATIONS: dict[GameId, dict[str, str]] = {
    GameId.MTG: {
        "1993": GuideId.MTG_1993.value,
        "1997": GuideId.MTG_1997.value,
        "2003": GuideId.MTG_2003.value,
        "2015": GuideId.MTG_M15.value,
        # Future Sight's timeshifted design is its own *look* and not its own
        # *border*: surveyed over 226 printings it reads 2.94 top / 2.95 sides /
        # 2.99 bottom against the 2003 frame's 2.96 / 2.92 / 2.94 — the same border
        # to within 0.05mm. It had its own spec for part of this session, on a
        # smaller sample that put it 0.07mm off; two specs carrying the same four
        # numbers is two things to measure and maintain for no difference on paper.
        "future": GuideId.MTG_2003.value,
    }
}

#: the trait key :data:`FRAME_GENERATIONS` is keyed on. Named here rather than in
#: `specs` so the two cannot drift: `sources` writes it, this reads it.
FRAME_TRAIT = "frame"


def baseline(
    set_id: str, game: GameId, traits: Mapping[str, str] | None = None
) -> str | None:
    """The shipped spec id for this card, before any rule of the library's own.

    Two ways it can answer, and a game uses whichever its border actually follows:
    a **set-id era** (Pokémon's yellow WOTC border ran for a known list of sets),
    or the **printing's frame generation** (MTG's border changed with the frame,
    not with the set). ``None`` means the shipped baseline has nothing to say and
    the game's fallback applies.
    """
    sid = (set_id or "").lower()
    for prefixes, spec_id in ERAS.get(game, ()):
        if sid.startswith(prefixes):
            return spec_id
    generation = str((traits or {}).get(FRAME_TRAIT, "")).strip().lower()
    return FRAME_GENERATIONS.get(game, {}).get(generation)


def from_json(data: dict[str, Any]) -> FrameGuide:
    """A stored spec back into a :class:`FrameGuide`.

    Total on purpose — a hand-edited file is untrusted input like TOML is, so
    anything missing or the wrong shape becomes a documented default rather than
    a traceback in the middle of a card walk.
    """
    spec_id = clean_id(data.get("id"))
    return FrameGuide(
        id=spec_id,
        name=str(data.get("name") or spec_id),
        game=_game(data.get("game")),
        inset=_four(data.get("inset")),
        note=str(data.get("note") or ""),
        ref_mm=_pair(data.get("ref_mm")),
    )


def merge(shipped: FrameGuide, stored: FrameGuide) -> FrameGuide:
    """A shipped spec as this library has corrected it — the stored numbers win,
    with the shipped name and game filled in where the file did not bother."""
    return replace(
        stored,
        name=stored.name if stored.name != stored.id else shipped.name,
        game=stored.game if stored.game is not None else shipped.game,
    )


def _four(value: Any) -> tuple[float, float, float, float]:
    try:
        nums = [float(v) for v in list(value)[:4]]  # pyright: ignore[reportGeneralTypeIssues]
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0, 0.0)
    nums += [0.0] * (4 - len(nums))
    # an inset outside 0..0.5 is not a border, it is a typo or a percentage
    clipped = [v if 0.0 <= v < 0.5 else 0.0 for v in nums]
    return (clipped[0], clipped[1], clipped[2], clipped[3])


def _pair(value: Any) -> tuple[float, float]:
    try:
        nums = [float(v) for v in list(value)[:2]]  # pyright: ignore[reportGeneralTypeIssues]
    except (TypeError, ValueError):
        return (63.0, 88.0)
    if len(nums) != 2 or not all(v > 0 for v in nums):
        return (63.0, 88.0)
    return (nums[0], nums[1])


def _game(value: Any) -> GameId | None:
    try:
        return GameId(str(value).strip().lower())
    except ValueError:
        return None
