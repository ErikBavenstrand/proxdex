"""Frame specs: where a card's printed border sits.

The align overlay draws the card outline plus these inner border lines (square
corners — the lines meet at 90° regardless of the trim's corner rounding) so a
scan can be expanded until its border matches the real card. Insets are
fractions of the card, per edge; a uniform physical border is a smaller
fraction of the long axis than the short axis, so top/bottom ≠ left/right.

**A spec is four numbers — nothing else.** There is deliberately no confidence
level, no provenance enum and no coverage grade. An earlier version had all three,
and they were built on a premise that is false: that reading a border off the
publisher's scan measures the card. It does not. A scan carries its own crop, and a
crop that trims 0.3mm inside the cut edge shrinks every border read off it by 0.3mm
with nothing in the image to say so — no sample size and no agreement between cards
removes that, because it is systematic. Grading such a number as "scanned,
therefore trusted" dressed the guess up.

Where a shipped spec's numbers came from is recorded **in the source, above the
spec**, and one card per row in ``docs/measuring-frames.md``. That is prose in the
repository rather than a field on the object, so there is nothing for a screen to
render as a verdict.

**One card size, both games, and it is the same one proxdex trims to.** Magic and
Pokémon print on identical stock — 2.5×3.5in, the poker-size standard, **63.5×88.9mm** —
so there is no per-spec reference size *and* no second constant:
:data:`proxdex.games.CARD_W_MM` is the trim *and* the card a spec's millimetres are
fractions of. That identity is the point: a caliper reading of a 3.45mm border becomes a
printed border of 3.45mm, and `frames show` reports the width that actually gets
printed. Insets are *fractions*, which travel between sizes untouched; the only
genuinely different card is the oversized one, and that is a **boolean**
(:attr:`FrameGuide.oversized`).

**A printing with no spec resolves to nothing, and that is a state rather than a
failure.** A spec ships only once somebody has measured a card and written down what
they measured; every number read off the publisher's scans is gone. So a card whose
frame generation nobody has measured gets :attr:`~proxdex.specs.Via.NONE`, the reports
name it, and ``border`` refuses to run until someone measures a spec (``proxdex frames
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

from proxdex.games import (
    CARD_H_MM,
    CARD_W_MM,
    OVERSIZED_H_MM,
    OVERSIZED_W_MM,
    GameId,
)


#: shipped spec ids. Closed on purpose: these are the ones code names — the
#: fallbacks, the era rules, and `borderless`, which :func:`proxdex.sources`
#: returns when a provider says a printing has no frame. A library's own specs
#: are plain ids validated by :func:`valid_id`.
class GuideId(StrEnum):
    POKEMON_WOTC = "pokemon-wotc"
    MTG_1993 = "mtg-1993"
    MTG_1993_ALPHA = "mtg-1993-alpha"
    MTG_1993_UNLIMITED = "mtg-1993-unlimited"
    MTG_1997 = "mtg-1997"
    MTG_2003 = "mtg-2003"
    MTG_M15 = "mtg-m15"
    MTG_YELLOW_BAND = "mtg-yellow-band"
    MTG_OVERSIZED = "mtg-oversized"
    MTG_VANGUARD = "mtg-vanguard"
    BORDERLESS = "borderless"


#: `borderless` cannot be redefined or removed: code *returns* it, so there has
#: to be a spec by that name whatever a library has done to the rest.
RESERVED: frozenset[str] = frozenset({GuideId.BORDERLESS.value})

#: the card a spec's millimetres are fractions of — :data:`proxdex.games.CARD_W_MM`,
#: which is also the trim, because they are the same card. Re-exported as a pair for
#: the callers that want one; the *value* is defined once, in `games`.
CARD_MM: tuple[float, float] = (CARD_W_MM, CARD_H_MM)

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
    #: this spec describes an **oversized** card (89×127mm) rather than the standard
    #: 63.5×88.9 both games print on. A *boolean* and not a size pair, deliberately:
    #: there are exactly two card sizes in proxdex, and the field that used to hold an
    #: arbitrary reference size was really only ever answering this question. Without
    #: it the millimetres reported for `mtg-vanguard` came out 3.71/2.88 "of a
    #: 63.5×88.9mm card", which describes no card that exists.
    oversized: bool = False

    @property
    def frameless(self) -> bool:
        """No printed frame at all, so a fit is pure aspect correction."""
        return not any(self.inset)

    @property
    def card_mm(self) -> tuple[float, float]:
        """The card this spec's fractions are fractions *of*."""
        if self.oversized:
            return (OVERSIZED_W_MM, OVERSIZED_H_MM)
        return CARD_MM

    def mm(
        self, w: float | None = None, h: float | None = None
    ) -> tuple[float, float, float, float]:
        """The inset back as per-edge millimetres of a ``w``×``h`` card.

        Defaults to the card this spec is *about* (:attr:`card_mm`), so a reader is
        never shown a width of a card the spec does not describe. Pass a size only to
        ask a hypothetical — "what would this spec mean on that card?" — which is how
        the 1.2mm oversized error is demonstrated in the tests.
        """
        card_w, card_h = self.card_mm
        width = w if w is not None else card_w
        height = h if h is not None else card_h
        top, right, bottom, left = self.inset
        return (top * height, right * width, bottom * height, left * width)

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "game": self.game.value if self.game else None,
            "inset": [round(v, 6) for v in self.inset],
            "oversized": self.oversized,
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
    w: float = CARD_W_MM,
    h: float = CARD_H_MM,
) -> tuple[float, float, float, float]:
    """Per-edge border widths (mm) → inset fractions [top, right, bottom, left]
    of a ``w``×``h`` mm card, :data:`CARD_MM` by default. Insets are taken against
    the true card so the ratios stay consistent with the card aspect."""
    return (top / h, right / w, bottom / h, left / w)


_mm = mm_to_inset  # the name this module used before the specs split

# --- Pokémon ----------------------------------------------------------------
# Base Set..Neo Destiny — the yellow-bordered WOTC era, and **only** that era. It
# stops where the e-Card series begins: `base1-6`, `gym1-2`, `neo1-4` are WOTC's own
# printings, and everything from `ecard1` onward was a different operation with a
# frame nobody has measured. Calipers on a real card (top 3.3 / bottom 3.6 /
# left 3.2 / right 3.1 mm); the border wanders a little card-to-card, so these are
# the tidy averages top/bottom 3.45, sides 3.15mm. Taken against the real card
# (63.5×88.9mm), the card both games print on.
_POKEMON_WOTC = FrameGuide(
    id=GuideId.POKEMON_WOTC.value,
    name="Pokémon · WOTC vintage (Base-Neo Destiny)",
    game=GameId.POKEMON,
    inset=_mm(3.45, 3.15, 3.45, 3.15),
)

# --- Magic: The Gathering ---------------------------------------------------
# **Measured generations only, and the rest deliberately absent.** Every MTG spec that
# was read off the publisher's scans by the old auto-detector is gone, because checking
# one against a hand reading showed why that cannot stand: the detector wants a
# *luminance* step, so on a card whose frame is itself dark (a Beta artifact's stone
# frame) it walks past the real border edge and reported 37-41px where the border ends
# at 23px — 65% too far. Every scan-derived MTG number inherited some of that, so they
# are gone rather than carried forward with a caveat. A card whose frame generation has
# no spec resolves to **nothing**, says so, and refuses to be bordered until someone
# measures it or passes `--frame`. That is the point: proxdex would rather stop than fit
# a card to a number nobody took.
#
# What replaces them is one card read by hand per generation, logged in
# `docs/measuring-frames.md` a row at a time. Each is stored as the **exact pixel
# fractions** of the image it was read off rather than converted through millimetres, so
# nothing rounds: Scryfall's card images are opaque at all four mid-edges (only the
# rounded corners are transparent), which makes a pixel count a fraction of the card
# directly.
#
# **The 1993 frame is three geometries, not one.** Scryfall calls Alpha, Unlimited, 4th
# Edition and every 1993-96 expansion one frame; the design really is the same, and the
# printings really do not share a border. Twenty-six sets of it have now been read by
# hand — the log in `docs/measuring-frames.md` has every row — and they collapse into
# three bands, not twenty-six numbers. These are **general rules**: a set is placed by
# which band it lands in, and a card sitting a pixel or two off its band is reading
# noise, not a spec of its own.
#
# Band 1, the narrow one: **23px sides / 32px top**. Alpha, Beta, and the two
# Collectors' Editions, which are Beta-derived and read the same to the pixel — a
# genuinely independent confirmation, since nothing about `ced`/`cei` was used to arrive
# at the number.
#
# Band 2, the ordinary one: **29px / 32px**, and it is the majority — 18 sets, from
# Arabian Nights through 4th Edition and the 1995-96 reprint sets. Sides span 27-32
# (0.43mm) and top 30-36 (0.51mm) with medians of 29 and 32, so one spec covers the lot.
# It absorbed what was briefly a separate `mtg-1993-4ed` (30/33): one pixel from the
# median on each edge is not a spec.
#
# Band 3, the wide one: **35px / 42.5px**. Unlimited and Revised only, and by a long way
# — their top border is 6.5px clear of the next widest 1993 reading. Confirmed on two
# cards each and by an independent run-length scan.
#
# The one set that fits none of them is `4bb`, Fourth Edition Foreign Black Border, at
# 36/40 — which is exactly the **1997** frame's numbers, so it is pointed there in
# `BASELINE` rather than given a spec of its own. The Foreign Black Border sets were
# printed in Belgium, a separate print run, which makes a genuinely different border
# plausible; a run-length scan reads it wider still (38-39 sides, 41 top), so it is not
# a misreading. Its sibling `fbb` went the other way and sits in band 2, so there is no
# "foreign" rule to write — only these two facts.
#
# Each band is stored as the **exact pixel fractions** of a 745x1040 file rather than
# converted through millimetres, so nothing rounds: Scryfall's images are opaque at all
# four mid-edges, so a pixel count is a fraction of the card directly.

_MTG_1993 = FrameGuide(
    id=GuideId.MTG_1993.value,
    name="MTG · 1993 frame (Arabian Nights-4th Edition)",
    game=GameId.MTG,
    inset=(32 / 1040, 29 / 745, 32 / 1040, 29 / 745),
)

# Alpha and Beta, a full millimetre inside band 2 on the sides. Measured off Lightning
# Bolt in all five 1993 printings — the one card they share, so the art is identical and
# nothing but the border can differ — and Sol Ring agrees exactly. `ced`/`cei` land here
# too, which is what a Beta-derived printing should do.
_MTG_1993_ALPHA = FrameGuide(
    id=GuideId.MTG_1993_ALPHA.value,
    name="MTG · 1993 frame (Alpha, Beta, Collectors' Edition)",
    game=GameId.MTG,
    inset=(32 / 1040, 23 / 745, 32 / 1040, 23 / 745),
)

# The white-bordered pair, and the widest border of Magic's first decade on both axes.
# Four readings agree (`2ed-162`, `2ed-270`, `3ed-162`, `3ed-274`) and a run-length scan
# reproduces 42/35 at every quartile. Note their **sides** land on `mtg-2003`'s 35px
# exactly while their top is 7.5px wider — so this is not that spec under another name.
_MTG_1993_UNLIMITED = FrameGuide(
    id=GuideId.MTG_1993_UNLIMITED.value,
    name="MTG · 1993 frame (Unlimited, Revised)",
    game=GameId.MTG,
    inset=(42.5 / 1040, 35 / 745, 42.5 / 1040, 35 / 745),
)

# Read off `sld-1664` (Sol Ring, 744x1040) at 36px on the sides and 40px top and bottom.
# **A card that physically exists**, which is why it and not `me4-227`: Masters Edition
# IV was MTGO-only, so its image is a render of the frame template rather than a scan of
# printed card stock. The render reads 41px on top, the two real 1997-frame prints both
# read 40 (`sld-1664` 40/36, the gold-bordered World Championship `wc97-jk54` 40/38), so
# the render was the odd one out by a pixel. Worth 0.09mm and worth taking: a spec
# should rest on ink on card.
#
# The sides come out 1.11mm wider than `mtg-1993`'s Alpha/Beta reading and 0.09mm wider
# than `mtg-2003`'s, and that second gap is *not* grounds for merging the two: 1997's
# top and bottom are 40px against 2003's 35, a 0.43mm difference unanimous across three
# cards each way. The 2003 redesign is what made all four edges equal.
_MTG_1997 = FrameGuide(
    id=GuideId.MTG_1997.value,
    name="MTG · 1997 frame (Mirage-7th Edition)",
    game=GameId.MTG,
    inset=(40 / 1040, 36 / 744, 40 / 1040, 36 / 744),
)

# Read off the Commander 2013 Sol Ring image (`c13-259`, 745x1040) at 35px on every
# edge.
# The best-corroborated spec here: a colour scan of the same file reads 35 on all four
# edges, and a *white*-bordered card of the same generation read by hand independently
# (`8ed-274`, Rampant Growth) also gives 35/35. Two cards, two border colours, two
# methods, one number — which is also what both scan surveys said (2.92-3.00mm), and it
# is the generation whose redesign made all four edges equal.
#
# It covers the `future` frame too, by measurement rather than by assumption: `mb2-233`
# Sol Ring, the one card of that generation read by hand, comes out at 35px as well.
_MTG_2003 = FrameGuide(
    id=GuideId.MTG_2003.value,
    name="MTG · 2003 frame (8th Edition-M14, and the `future` frame)",
    game=GameId.MTG,
    inset=(35 / 1040, 35 / 745, 35 / 1040, 35 / 745),
)

# Read off the Mystery Booster Convention Edition Sol Ring image (`msc-211`, 744x1040)
# at 30px on every edge: a plain black-bordered card carrying no treatment at all,
# which is what the spec is meant to describe. Two independent measurements of the
# same file agree — a dark-run scan over 240 columns reads the top at exactly 30 at
# every percentile, and the detector that used to pre-place the marks read T2.89%
# against the stored 2.88% before it was removed.
#
# **744, not 745.** Scryfall's PNGs are nominally 745 wide and some come back 744, so
# every spec here divides by the width of the file it was actually read off. It is
# worth 0.006mm and nothing else, but a number nobody can reproduce is worth less.
#
# Four *treated* cards of the same generation were read by hand alongside it and land
# 28-29px: extended art (`cmr-700`) 28, etched foil (`p30m-1F★`) 29, silver border
# (`und-85`) 28, legendary crown sides (`tsr-287`) 28. So the generation sits inside a
# 0.17mm band and **no treatment needs a spec of its own** — the 1-2px scatter is most
# likely each scan's own crop, which is exactly the error a survey cannot remove. The
# plain card is the one stored; calipers can correct it with `frames set`.
_MTG_M15 = FrameGuide(
    id=GuideId.MTG_M15.value,
    name="MTG · M15 frame (Magic 2015-present)",
    game=GameId.MTG,
    inset=(30 / 1040, 30 / 744, 30 / 1040, 30 / 744),
)

# The one MTG *treatment* that is its own geometry rather than its own picture. A yellow
# `border_color` is Aetherdrift's box-topper band: a wide flat yellow frame, 1.7mm wider
# on the sides than the generation it sits in, and the only combination in the whole
# survey where the border colour and the border geometry travel together. All 79 such
# printings are `2015`-frame, so the printing settles it (`sources.mtg_frame`) rather
# than a rule.
#
# Read off `dft-501` Bleachbone Verge (744x1040) at 50px on the sides and 44px top and
# bottom, and measuring how far the *band's own colour* reaches confirms both numbers at
# every percentile. The old auto-detector read 56px and 50px instead, because just
# inside the flat band sit a black keyline, a thin yellow line and a second black
# keyline — the
# "decorated frame with two inner edges" case it warns about. The flat band is the
# border; the keylines are decoration on the frame.
_MTG_YELLOW_BAND = FrameGuide(
    id=GuideId.MTG_YELLOW_BAND.value,
    name="MTG · yellow box-topper band",
    game=GameId.MTG,
    inset=(44 / 1040, 50 / 744, 44 / 1040, 50 / 744),
)

# --- MTG, oversized --------------------------------------------------------- **An
# oversized card needs its own spec even when its border is the same width**, and these
# two measurements are the clearest possible statement of why. An Archenemy scheme's
# border is 2.98 / 3.00mm — *physically identical* to an ordinary 2003-frame card's 2.99
# / 2.98. But a spec is a **fraction**, and the card is 89×127mm rather than 63.5×88.9,
# so the same millimetres are a completely different fraction: 2.35% / 3.37% here
# against `mtg-2003`'s 3.37% / 4.70%. Resolving a scheme to `mtg-2003` — which is what
# happened before these existed, via its frame generation — asks for 4.27 / 4.18mm on a
# card whose border is 2.98 / 3.00. That is 1.2mm too wide on every edge, and it looks
# perfect on screen because the overlay is drawn in fractions too.
#
# Read off `oarc-1★` (All in Good Time, 1040x1490) at 35px on all four edges. It covers
# **planes and phenomena as well as schemes**, which share the `planar` layout: those
# could not be read directly (their art runs to the card edges and what border there is
# comes out uneven), so they take the number measured off the same stock rather than
# being treated as borderless. That is the safe direction — calling a bordered card
# borderless throws its border fit away and looks perfect — and it is not a guess about
# geometry: same product line, same 89x127mm stock, same era, and the number measured
# here is the *same physical border* an ordinary 2003-frame card carries.
_MTG_OVERSIZED = FrameGuide(
    id=GuideId.MTG_OVERSIZED.value,
    name="MTG · oversized plane or scheme (89×127mm)",
    game=GameId.MTG,
    inset=(35 / 1490, 35 / 1040, 35 / 1490, 35 / 1040),
    oversized=True,
)

# Vanguard is a third size again — its files come at 1060x1510 where planes and schemes
# are 1040x1490 — and a genuinely thicker border: 5.30 / 4.03mm, nearly twice an
# ordinary card's. It reports `frame: 1993`, which would otherwise hand it Alpha's
# 1.96mm sides.
#
# Read off `pvan-101` (Ertai, 1060x1510) at 63px top and bottom, 48px on the sides.
_MTG_VANGUARD = FrameGuide(
    id=GuideId.MTG_VANGUARD.value,
    name="MTG · oversized Vanguard",
    game=GameId.MTG,
    inset=(63 / 1510, 48 / 1060, 63 / 1510, 48 / 1060),
    oversized=True,
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
)

#: the specs proxdex ships. The shortness is the design: a spec is here only if somebody
#: measured a card and wrote down what they measured.
SHIPPED: dict[str, FrameGuide] = {
    g.id: g
    for g in (
        _POKEMON_WOTC,
        _MTG_1993,
        _MTG_1993_ALPHA,
        _MTG_1993_UNLIMITED,
        _MTG_1997,
        _MTG_2003,
        _MTG_M15,
        _MTG_YELLOW_BAND,
        _MTG_OVERSIZED,
        _MTG_VANGUARD,
        _BORDERLESS,
    )
}


class Generation(StrEnum):
    """A frame generation, as Scryfall names it on the printing.

    Closed, and quoted from Scryfall's own documentation — these are the only five
    values that field takes. MTG's border width changed with the **frame** rather
    than with the set (a modern set holds retro-frame cards beside modern ones), so
    this is what :data:`BASELINE` keys MTG on, read back from the card's own
    ``.traits`` marker so choosing a spec never costs an API call.

    A generation is in :data:`BASELINE` only once somebody has measured a card of
    it. A value that is *absent* there resolves to no spec and says so, rather than
    borrowing another generation's numbers.
    """

    F1993 = "1993"
    F1997 = "1997"
    F2003 = "2003"
    F2015 = "2015"
    FUTURE = "future"


#: the trait key :class:`Generation` is read from. Named here rather than in
#: `specs` so the two cannot drift: `sources` writes it, this reads it.
FRAME_TRAIT = "frame"


def parse_generation(value: Any) -> Generation | None:
    """A :class:`Generation` from a card's recorded traits, or ``None``.

    Total, like every other reader of provider data: the trait was written out of
    untyped JSON, so a generation nobody has heard of is an absent answer rather
    than a traceback in the middle of a card walk.
    """
    try:
        return Generation(str(value or "").strip().lower())
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Baseline:
    """One shipped "this printing takes that spec" fact.

    There were two tables here — set-id eras and frame generations — and they were
    the same thing keyed two ways: both answer :attr:`~proxdex.specs.Via.ERA`, both
    are the shipped baseline a library's own rules are consulted *before*. What
    differs is only which fact about a card decides, and that follows the game:
    Pokémon's yellow border ran for a known list of **sets**, MTG's changed with the
    printing's **frame generation**. So it is one table with two typed keys, and a
    game fills in whichever its border actually followed.
    """

    #: the spec these cards are fitted to. A shipped id, because code names it.
    spec: GuideId
    #: set-id prefixes this covers, for a game whose border followed its sets
    sets: tuple[str, ...] = ()
    #: frame generations this covers, for a game whose border followed its frame
    frames: tuple[Generation, ...] = ()


#: the shipped baseline, per game. Short on purpose: an entry exists only once a card
#: of it has been read by hand (``docs/measuring-frames.md``), and the absent ones are
#: absent **deliberately** — such a printing resolves to no spec and refuses to be
#: bordered, rather than being fitted to a number nobody took. Adding one is purely
#: additive, since what it covers resolved to nothing before.
BASELINE: dict[GameId, tuple[Baseline, ...]] = {
    # Pokémon ids come from pokemontcg.io, and this stops exactly where WOTC did:
    # `base1-6`, `gym1-2`, `neo1-4`. Then the e-Card series begins, which was a
    # different operation with a frame nobody has measured.
    GameId.POKEMON: (Baseline(GuideId.POKEMON_WOTC, sets=("base", "gym", "neo")),),
    GameId.MTG: (
        # **The 1993 frame is three bands, so the exceptions are keyed by set and the
        # majority by the generation.** Scryfall calls them all one frame; 26 sets read
        # by hand say otherwise, but they say it in three groups rather than 26 — so
        # what is written here is three rules, not a table of sets. The two set entries
        # are the exceptions; everything else falls through to the generation entry
        # below, which is band 2 and covers 18 sets.
        #
        # `4bb` is the one printing that fits no band: 36/40, which is the **1997**
        # frame's numbers exactly, so it is pointed there. Measurement, not assumption —
        # the same basis on which `future` shares `mtg-2003`.
        Baseline(GuideId.MTG_1993_ALPHA, sets=("lea", "leb", "ced", "cei")),
        Baseline(GuideId.MTG_1993_UNLIMITED, sets=("2ed", "3ed")),
        Baseline(GuideId.MTG_1997, sets=("4bb",)),
        # Band 2, and the reason nothing in this frame resolves to *nothing* any more:
        # 18 sets landed inside 0.43mm of each other, so a generation-wide answer is a
        # measurement rather than the coin flip it would have been with only Alpha and
        # Revised read.
        Baseline(GuideId.MTG_1993, frames=(Generation.F1993,)),
        Baseline(GuideId.MTG_1997, frames=(Generation.F1997,)),
        # `future` shares the 2003 numbers **because a card of it was measured at
        # them** (`mb2-233`, 35px on every edge), not because it was assumed to. One
        # entry rather than two, so there is one number to correct; if calipers ever
        # split them, `mtg-future` is a new spec and a new entry.
        Baseline(GuideId.MTG_2003, frames=(Generation.F2003, Generation.FUTURE)),
        Baseline(GuideId.MTG_M15, frames=(Generation.F2015,)),
    ),
}


def baseline(
    set_id: str, game: GameId, traits: Mapping[str, str] | None = None
) -> GuideId | None:
    """The shipped spec for this card, before any rule of the library's own.

    A **set-id era** answers first and a **frame generation** second, in two passes
    rather than one — so which kind of key wins is a property of this function and
    not of the order somebody happened to list :data:`BASELINE` in. ``None`` means
    the shipped baseline has nothing to say about this printing, which is a state
    (:attr:`~proxdex.specs.Via.NONE`) and not a failure.
    """
    entries = BASELINE.get(game, ())
    sid = (set_id or "").lower()
    for entry in entries:
        if entry.sets and sid.startswith(entry.sets):
            return entry.spec
    generation = parse_generation((traits or {}).get(FRAME_TRAIT))
    if generation is None:
        return None
    return next((e.spec for e in entries if generation in e.frames), None)


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
        oversized=bool(data.get("oversized")),
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


def _game(value: Any) -> GameId | None:
    try:
        return GameId(str(value).strip().lower())
    except ValueError:
        return None
