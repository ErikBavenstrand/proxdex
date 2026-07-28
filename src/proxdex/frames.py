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

**A printing with no spec resolves to nothing, and that is a state rather than a
failure.** Only three specs ship, because only three have been measured; every other
MTG number was read off the publisher's scans and is gone. So a card whose frame
generation nobody has measured gets :attr:`~proxdex.specs.Via.NONE`, the reports name
it, and ``border`` refuses to run until someone measures a spec (``proxdex frames
set``, and ``docs/measuring-frames.md`` says how) or passes ``--frame`` for the run.
Stopping is the right answer there: the alternative is reshaping a card to a number
nobody took, which looks perfect and is wrong on paper.

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
    MTG_1993 = "mtg-1993"
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

# --- Pokémon ----------------------------------------------------------------
# Base Set..Neo Destiny — the yellow-bordered WOTC era, and **only** that era. It
# stops where the e-Card series begins: `base1-6`, `gym1-2`, `neo1-4` are WOTC's own
# printings, and everything from `ecard1` onward was a different operation with a
# frame nobody has measured. Calipers on a real card (top 3.3 / bottom 3.6 /
# left 3.2 / right 3.1 mm); the border wanders a little card-to-card, so these are
# the tidy averages top/bottom 3.45, sides 3.15mm.
_POKEMON_WOTC = FrameGuide(
    id=GuideId.POKEMON_WOTC.value,
    name="Pokémon · WOTC vintage (Base-Neo Destiny)",
    game=GameId.POKEMON,
    inset=_mm(3.45, 3.15, 3.45, 3.15),
    note="Calipers on a real card: top 3.3 / bottom 3.6 / sides 3.1-3.2 mm.",
)

# --- Magic: The Gathering ---------------------------------------------------
# **One spec, measured, and the rest deliberately absent.** Every other MTG spec was
# read off the publisher's scans, and checking one against a hand reading showed why
# that cannot stand: `borders.detect_inset` wants a *luminance* step, so on a card
# whose frame is itself dark (a Beta artifact's stone frame) it walks past the real
# border edge and reported 37-41px where the border ends at 23px — 65% too far. Every
# scan-derived MTG number inherited some of that, so they are gone rather than
# carried forward with a caveat. A card whose frame generation has no spec now
# resolves to **nothing**, says so, and refuses to be bordered until someone measures
# it or passes `--frame`. That is the point: proxdex would rather stop than fit a card
# to a number nobody took.
#
# Read off the Beta Sol Ring scan (`leb-270`, 672x936) at 21.5px on the sides and
# 28.5px top and bottom. Stored as those exact fractions rather than converted through
# millimetres, so nothing rounds: the scan is the whole card (its alpha channel is
# opaque from pixel 0 on all four straight edges), which makes a pixel count a
# *fraction of the card* directly.
_MTG_1993 = FrameGuide(
    id=GuideId.MTG_1993.value,
    name="MTG · 1993 frame (Alpha-4th Edition)",
    game=GameId.MTG,
    inset=(28.5 / 936, 21.5 / 672, 28.5 / 936, 21.5 / 672),
    note=(
        "Read off Scryfall's Beta Sol Ring scan (leb-270) at 672x936: sides 21.5px, "
        "top and bottom 28.5px. Alpha and Beta measure about 1mm narrower on the "
        "sides than white-bordered Unlimited and Revised (2.98mm), which is not a "
        "crop artifact — the art box sits at the same pixels in both scans — so this "
        "describes the black-bordered printings and those two want their own spec."
    ),
    #: a real Magic card is 2.5x3.5in, so the millimetres shown to a human are
    #: fractions of *that*, not of the 63x88 proxdex trims to
    ref_mm=(63.5, 88.9),
)

# --- any game ---------------------------------------------------------------
# Borderless / art-series printings have no frame at all, so the fit is pure aspect
# correction. Never inferred from a *set* id — a modern set mixes bordered and
# borderless prints under one code — but it *is* inferred per card from what the
# provider says about that printing (Scryfall's `border_color`), recorded in the
# card's own `.frame` marker. `border --frame` overrides either way.
_BORDERLESS = FrameGuide(
    id=GuideId.BORDERLESS.value,
    name="Borderless (no printed frame)",
    game=None,
    inset=(0.0, 0.0, 0.0, 0.0),
    note="No frame to match — reshapes to the card aspect only. Nothing to measure.",
)

#: the specs proxdex ships. Three, and the shortness is the design: a spec is here
#: only if somebody measured it.
SHIPPED: dict[str, FrameGuide] = {
    g.id: g for g in (_POKEMON_WOTC, _MTG_1993, _BORDERLESS)
}

#: set-id prefixes per era, per game — the shipped baseline a library's own rules are
#: consulted *before*. Pokémon ids come from pokemontcg.io, and this list stops
#: exactly where WOTC did: `base1-6`, `gym1-2`, `neo1-4`, then the e-Card series
#: begins and nothing here describes it. MTG's split is by frame generation rather
#: than by set, below.
ERAS: dict[GameId, tuple[tuple[tuple[str, ...], str], ...]] = {
    GameId.POKEMON: ((("base", "gym", "neo"), GuideId.POKEMON_WOTC.value),),
    GameId.MTG: (),
}

#: MTG's border width changed with the frame and **not** with the set, so the baseline
#: reads the generation Scryfall names per printing (``frame``), which the card records
#: in its ``.traits``. Scryfall documents five values — `1993`, `1997`, `2003`, `2015`,
#: `future` — and only one of them has a measured spec. The other four are **absent on
#: purpose**: a card of those generations resolves to no spec and says so, rather than
#: being fitted to a number read off a scan.
FRAME_GENERATIONS: dict[GameId, dict[str, str]] = {
    GameId.MTG: {"1993": GuideId.MTG_1993.value}
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
