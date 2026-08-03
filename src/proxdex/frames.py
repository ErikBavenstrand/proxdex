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
    POKEMON_ECARD = "pokemon-ecard"
    POKEMON_ECARD_DEEP_TOP = "pokemon-ecard-deep-top"
    POKEMON_ECARD_EX = "pokemon-ecard-ex"
    POKEMON_EX_PLAIN = "pokemon-ex-plain"
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

# **The e-Card frame is not symmetric, and that is the whole of this entry.**
# Expedition, Aquapolis and Skyridge (`ecard1`/`ecard2`/`ecard3`, 2002-03) carry the
# Nintendo e-Reader dot-code strip **down the left edge and along the bottom**, so two
# of the four edges are roughly twice the others — and unlike every other spec here,
# the *shape* is the finding rather than the size.
#
# Read by hand off two cards at different scales, and they agree per edge to a degree
# that leaves nothing to argue about: left 11.276% against 11.262% (**0.014pp**),
# right 5.045 / 5.156, top 3.640 / 3.378, bottom 7.495 / 7.722 — every edge inside
# 0.27pp on images whose widths differ by a factor of 2.2. That is what says the
# asymmetry belongs to the card and not to either scan: a crop shifts the two opposite
# edges *against* each other, so a lopsided crop cannot reproduce the same lopsided
# reading twice at two scales, while a real asymmetric frame does exactly that. One row
# per card in `docs/measuring-frames.md`; the numbers below are the per-edge average.
#
# Two of the four land on WOTC's border almost exactly — top 3.12mm against 3.45, right
# 3.24 against 3.15 — which is the corroboration worth having, since those readings
# were taken by different methods (calipers there, pixels here): the same operation
# printed the same border and added a strip along the other two (bottom 6.76mm, left
# 7.16). Anything resolving these sets to `pokemon-wotc` would have been right on two
# edges and ~3-4mm short on the other two, which is far worse than refusing them, and
# is what happened until this spec existed.
#
# A run-length scan was tried as a check — over ten *other* e-Card scans, since the two
# measured images are not the ones in hand — and is deliberately **not** recorded as
# one: it read the left edge anywhere from 19px to 61px (3.2% to 10.2%) and the top
# from 2.06% to 11.64%, because an e-Card's art runs into the strip and much of it is
# yellow. That is the third failure mode of the deleted auto-detector — a border found
# where the picture is dark, or missed where the picture matches it — and it is an
# argument for reading by hand rather than against these numbers.
_POKEMON_ECARD = FrameGuide(
    id=GuideId.POKEMON_ECARD.value,
    name="Pokémon · e-Card (Expedition-Skyridge)",
    game=GameId.POKEMON,
    # [top, right, bottom, left] — the two wide edges carry the e-Reader dot code
    inset=(0.035093, 0.051003, 0.076083, 0.112689),
)

# **The same three sets hold a second frame, deeper at the top.** Read by hand off one
# card, 468×650: left 52px, right 20px, top 67px, bottom 49px — so left 11.111%, right
# 4.274%, top 10.308%, bottom 7.538%. The dot-code strip is in the same place (left and
# bottom agree with `pokemon-ecard` to 0.158pp and 0.070pp, i.e. 0.10mm and 0.06mm),
# and the **top is 6.04mm deeper as read** (5.98mm as shipped — the vertical
# compensation below moves it by 0.45px), which is the finding.
#
# Two of the four numbers below are `pokemon-ecard`'s own, and the other two are
# *derived* rather than taken, which is worth spelling out because it is not what the
# reading says on its face:
#
# * `left` and `bottom` agree with the existing spec well inside its demonstrated
#   reproducibility, so they are the existing spec's — two specs differing by a tenth
#   of a millimetre on an edge would be two answers to one question.
# * `top` and `right` are then re-derived to hold this reading's **sums**, not its
#   individual edges: 17.846% vertically (art panel 534px of 650) and 15.385%
#   horizontally. So top = 17.846 - 7.608 = **10.238%** and
#   right = 15.385 - 11.269 = **4.116%**.
#
# Deriving from the sums is the better reading of the same measurement, and for the
# reason the asymmetry above rests on: a crop shifts two *opposite* edges against each
# other, so the sum of a pair survives a crop that neither edge alone does. It also
# makes the substitution lossless — the design's own width and height as a fraction of
# the card are exactly what was measured, which is the thing a fit has to reproduce.
#
# `right` was **not** replaced by the existing spec's 5.100%: that reading is 0.827pp
# (0.53mm) away, seven times the 0.112pp the existing spec's two independent readings
# agreed to on that same edge, and past the ±0.5mm cutting tolerance. Swapping it would
# not have been self-consistent either — holding the horizontal sum would then have
# forced `left` to 10.28%, which contradicts left being the edge that agreed.
#
# **Which printing this is, is not recorded here, deliberately.** The measurement is
# one card's and the row in `docs/measuring-frames.md` says what is known about it; a
# guess at *why* the top is deeper would be provenance prose asserting more than was
# read, which is the failure this file deletes confidence grades for. It resolves as a
# second candidate on every e-Card set (see `BASELINE`) and a person picks per card,
# which is the honest shape for a question only they can answer.
_POKEMON_ECARD_DEEP_TOP = FrameGuide(
    id=GuideId.POKEMON_ECARD_DEEP_TOP.value,
    name="Pokémon · e-Card, deep top (Expedition-Skyridge)",
    game=GameId.POKEMON,
    # [top, right, bottom, left] — top/right derived to hold the measured sums,
    # left/bottom shared with `pokemon-ecard`
    inset=(0.102379, 0.041157, 0.076083, 0.112689),
)

# **The ex era moved the dot code to the bottom alone, and that is the finding.** The
# e-Reader strip is what makes the two specs above asymmetric on *two* edges; on the
# Nintendo Black Star Promos (`np`, 40 cards, 2003-10-01) three edges are ordinary and
# only the bottom is deep — top 3.26mm, right 2.33, left 2.76, bottom **6.01**. So this
# is not the e-Card frame with different numbers, it is a different shape, and resolving
# an `np` card to `pokemon-ecard` would have asked for 7.16mm of left border where the
# card has 2.76 — the sort of error that looks fine on screen, since the overlay is
# drawn in fractions too.
#
# Read by hand off two cards, 747×1040 and 455×642, and they reproduce the way the
# e-Card pair does: every edge agrees to **0.17pp or less** (top 3.750 / 3.583, right
# 3.614 / 3.736, bottom 6.827 / 6.698, left 4.284 / 4.396) across images whose widths
# differ by 1.64×. Both cards also read `left` wider than `right` by the same 0.67pp,
# which is why those two are not collapsed into one number the way most specs here
# collapse opposite edges: a cutting error cancels when it is averaged, and a difference
# that reproduces in the same direction at two scales is not a cutting error.
#
# The numbers below are the **per-edge average**, and the independently-stated totals
# corroborate three of the four: 59px of 747 and 37px of 455 horizontally, 110px of 1040
# vertically, all exact against the edges. The fourth is a 1px arithmetic slip in the
# reading — card 2's vertical total is given as 65 where its own 23 + 43 is 66 — which
# moves the vertical sum by 0.156pp (0.14mm), well inside the spread above. The edges
# are what was read off the picture, so the edges are what is stored.
#
# **Keyed to `np` alone for now, and the era is why it is not keyed wider.** The strip
# ran through the ex series, so this geometry very likely covers more of it — but
# "likely" is what this file does not ship, and every other ex-era set resolves to
# nothing and refuses to be bordered until somebody reads one. (`Baseline.sets` is a
# prefix match, and `np` is the only one of pokemontcg.io's 174 Pokémon set ids that
# begins with it, so the key names exactly one set today.)
_POKEMON_ECARD_EX = FrameGuide(
    id=GuideId.POKEMON_ECARD_EX.value,
    name="Pokémon · e-Card, ex era (Nintendo Black Star Promos)",
    game=GameId.POKEMON,
    # [top, right, bottom, left] — only the bottom carries the e-Reader dot code
    inset=(0.036663, 0.036754, 0.067624, 0.043397),
)

# **The same promo set also holds an ordinary square border, with no dot code at all.**
# One card, 554×769: **23px on all four edges**. The strip is what makes the three specs
# above asymmetric, and a card without one has nothing to be asymmetric about — so this
# is the plainest spec in the file, and the *fourth* frame the e-Reader era turns out to
# hold. `np` ran from 2003 into the ex series and only some of it carries a dot code,
# which is a difference in what was printed rather than in how it was cut, and nothing
# in the metadata says which a card is: both resolve as candidates and a person picks.
#
# Uniform in pixels **and** in millimetres — 2.64mm on the sides against 2.66 top and
# bottom — which is worth stating because it does not follow from the first: 2.991% and
# 4.152% are different fractions, and they only land on one width once each is taken of
# the axis it belongs to. The 0.02mm between them is the image's own aspect sitting
# 0.85% wide of the card's (554/769 = 0.7204 against 0.7143), so the reading is exactly
# what a genuinely square border on a 63.5×88.9mm card looks like read off this file. It
# is the first Pokémon spec whose four edges are one width, and the thinnest of them —
# WOTC's yellow is 3.45/3.15 and this is nearer `mtg-m15`'s 2.56.
#
# Stored as the fractions of **its own file**, like every other spec here, rather than
# converted to one uniform millimetre figure and back: the house rule is that a spec is
# the pixel count over the width of the image it was read off, and rounding through a
# millimetre would put a number in the file that nobody measured.
_POKEMON_EX_PLAIN = FrameGuide(
    id=GuideId.POKEMON_EX_PLAIN.value,
    # Not "(Nintendo Black Star Promos)" any more: `BASELINE` points the whole of the
    # ex series from `ex5` on at this spec as their *only* frame, so a name saying
    # which set it was read off would read as which sets it describes.
    name="Pokémon · ex era, no dot code",
    game=GameId.POKEMON,
    # [top, right, bottom, left] — 23px all round, of a 554×769 file
    inset=(0.029909, 0.041516, 0.029909, 0.041516),
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
        _POKEMON_ECARD,
        _POKEMON_ECARD_DEEP_TOP,
        _POKEMON_ECARD_EX,
        _POKEMON_EX_PLAIN,
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
    #: the set ids this covers, **exactly** — for a game whose border followed its sets.
    #:
    #: These were prefixes, and a prefix over-claims silently. `("ex1",)` for Ruby &
    #: Sapphire also matches `ex10` through `ex16` — Unseen Forces to Power Keepers,
    #: 2005-07, a different era of card entirely — and `("base",)` was quietly claiming
    #: `basep`, the Wizards Black Star Promos, which nobody measured. A prefix cannot be
    #: written that covers `ex1` and not `ex10`, so the mechanism had to go rather than
    #: the intent. Pokémon's measured eras are closed sets, so enumerating them costs a
    #: dozen strings and buys an answer that cannot reach a set nobody read.
    sets: tuple[str, ...] = ()
    #: frame generations this covers, for a game whose border followed its frame
    frames: tuple[Generation, ...] = ()


#: the shipped baseline, per game. Short on purpose: an entry exists only once a card
#: of it has been read by hand (``docs/measuring-frames.md``), and the absent ones are
#: absent **deliberately** — such a printing resolves to no spec and refuses to be
#: bordered, rather than being fitted to a number nobody took. Adding one is purely
#: additive, since what it covers resolved to nothing before.
BASELINE: dict[GameId, tuple[Baseline, ...]] = {
    # Pokémon ids come from pokemontcg.io, and are **exact** (see `Baseline.sets`).
    # Three eras are read and the rest are deliberately absent: WOTC's yellow border,
    # the e-Card series after it — asymmetric, because two edges carry the Nintendo
    # e-Reader dot code — and the ex era, where that code runs along the bottom alone
    # (`ex1`-`ex4`, `np`) and then stops. The **whole ex series** answers now, but only
    # the first five sets of it were read: `ex5` on inherit the plain border from
    # `np`, which is a decision recorded as one. Everything from Diamond & Pearl
    # (2007-05) onward still resolves to nothing and refuses to be bordered — the honest
    # answer until somebody reads one.
    GameId.POKEMON: (
        Baseline(
            GuideId.POKEMON_WOTC,
            # `basep`, the Wizards Black Star Promos, is here because the `"base"`
            # prefix was already claiming it and dropping it would stop a card that
            # borders today. It is the same operation and the same yellow border as
            # `base1`-`base6`, and it is the one id in this table that was **not**
            # separately read — `docs/measuring-frames.md` says so out loud rather than
            # letting an accident pass for a measurement.
            sets=(
                "base1",
                "base2",
                "base3",
                "base4",
                "base5",
                "base6",
                "basep",
                "gym1",
                "gym2",
                "neo1",
                "neo2",
                "neo3",
                "neo4",
            ),
        ),
        Baseline(GuideId.POKEMON_ECARD, sets=("ecard1", "ecard2", "ecard3")),
        # A *second* answer for the same sets, and the first entry in this table that
        # is one. It is deliberately listed after the frame most e-Card cards take, so
        # `baselines` returns the common one first and this is the alternative a
        # person picks on the cards whose top really is deeper. See the spec above.
        Baseline(GuideId.POKEMON_ECARD_DEEP_TOP, sets=("ecard1", "ecard2", "ecard3")),
        # **The dot code outlived the e-Card sets, and so both ex-era shapes apply to
        # the same five sets.** Ruby & Sapphire through Team Magma vs Team Aqua and the
        # Nintendo Black Star Promos (2003-07 to 2004-03) printed cards *with* an
        # e-Reader strip along the bottom and cards with a plain square border, and
        # nothing in the metadata says which a given card is — the same situation as the
        # two e-Card frames, answered the same way: both resolve as candidates and a
        # person picks per card.
        #
        # `ex5` onward is **not** here, and that is the whole point of the entry below
        # it: the *strip* is a fact about these five printings only, so from `ex5` there
        # is one shape rather than two and nothing to pick between. Exact ids, because a
        # prefix could not say that — `("ex1",)` claims `ex10`-`ex16` too, which is the
        # reason `Baseline.sets` stopped being a prefix at all.
        Baseline(GuideId.POKEMON_ECARD_EX, sets=("ex1", "ex2", "ex3", "ex4", "np")),
        # Second because it is the lighter measurement — two cards against one — and
        # *not* as a claim about which is commoner, which nobody counted. `resolve` fits
        # against the first and offers the rest, so the order here is only ever a
        # default somebody overrides with one click.
        Baseline(GuideId.POKEMON_EX_PLAIN, sets=("ex1", "ex2", "ex3", "ex4", "np")),
        # **The rest of the ex series, and the one place this table answers from a
        # decision rather than a reading.** The dot code stopped after `ex4`: from
        # Hidden Legends (2005-06) to Power Keepers (2007-05) every card carries the
        # plain square border above, so there is nothing to pick between and this is
        # their *only* candidate — which is the difference from the five sets on the
        # line before, where two shapes really coexist.
        #
        # These sixteen ids were **not separately measured**. The number is one card of
        # `np` (554×769, 23px all round), inherited on the grounds that it is the same
        # era, the same operation and the same border with the strip left off — the
        # same basis, and the same caveat, as `basep` sitting on `pokemon-wotc`.
        # `docs/measuring-frames.md` records it as inherited rather than read, out loud,
        # so nothing here passes for a hand reading. One card of `ex10` or so would
        # settle it; until then this is a deliberate decision to let these sets border
        # at a number from their own era instead of refusing them.
        #
        # Exact ids, never a prefix — `("ex1",)` is what used to claim all of these
        # silently, and enumerating them is what makes the claim reviewable. The four
        # Trainer Kits are here because they are the same printings boxed differently.
        Baseline(
            GuideId.POKEMON_EX_PLAIN,
            sets=(
                "ex5",
                "ex6",
                "ex7",
                "ex8",
                "ex9",
                "ex10",
                "ex11",
                "ex12",
                "ex13",
                "ex14",
                "ex15",
                "ex16",
                "tk1a",
                "tk1b",
                "tk2a",
                "tk2b",
            ),
        ),
    ),
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


class Key(StrEnum):
    """What a game's border followed — the fact that decides how coverage reads.

    :class:`Baseline` has two keys because the two games differ in kind: Pokémon's
    yellow border ran for a known list of **sets**, MTG's changed with the printing's
    **frame generation**. So "is this covered?" is a question about a set for one game
    and about a generation for the other, and asking it the wrong way round is exactly
    what the deleted coverage report did — it graded 1046 MTG sets as unmeasured while
    every card in them resolved exactly.

    Derived from :data:`BASELINE` rather than declared beside it, so it cannot drift
    from the table it describes.
    """

    SET = "set"
    GENERATION = "generation"

    @property
    def label(self) -> str:
        return "set" if self is Key.SET else "frame generation"

    @property
    def plural(self) -> str:
        return "sets" if self is Key.SET else "frame generations"


def keyed(game: GameId) -> Key:
    """How this game's border was keyed — see :class:`Key`.

    A game with a generation entry is keyed on the generation *even though it may
    also carry set entries*: MTG's are the three 1993 bands and `4bb`, exceptions to
    a generation rather than a scheme of their own. A game with no baseline at all
    reads as set-keyed, which is the shape a fresh game's measurements arrive in.
    """
    entries = BASELINE.get(game, ())
    return Key.GENERATION if any(e.frames for e in entries) else Key.SET


def set_keys(game: GameId) -> dict[str, tuple[GuideId, ...]]:
    """Every set id :data:`BASELINE` names for ``game``, and what it answers.

    The whole table for one game, keyed the way it is written — so a coverage report
    can list the set exceptions of a generation-keyed game without walking every set
    that has ever printed.
    """
    out: dict[str, list[GuideId]] = {}
    for entry in BASELINE.get(game, ()):
        for set_id in entry.sets:
            out.setdefault(set_id, []).append(entry.spec)
    return {k: tuple(v) for k, v in out.items()}


def generation_keys(game: GameId) -> dict[Generation, tuple[GuideId, ...]]:
    """Every frame generation :data:`BASELINE` answers for ``game``.

    Absent generations are *not* filled in: one that nobody has measured resolves to
    no spec, which is the state a coverage report exists to name.
    """
    out: dict[Generation, list[GuideId]] = {}
    for entry in BASELINE.get(game, ()):
        for generation in entry.frames:
            out.setdefault(generation, []).append(entry.spec)
    return {k: tuple(v) for k, v in out.items()}


def baselines(
    set_id: str, game: GameId, traits: Mapping[str, str] | None = None
) -> tuple[GuideId, ...]:
    """Every shipped spec that describes this card, most-likely first.

    A **set-id era** answers first and a **frame generation** second, in two passes
    rather than one — so which kind of key wins is a property of this function and
    not of the order somebody happened to list :data:`BASELINE` in. Empty means the
    shipped baseline has nothing to say about this printing, which is a state
    (:attr:`~proxdex.specs.Via.NONE`) and not a failure.

    **More than one is allowed, and Pokémon's e-Card sets are why.** Those three sets
    hold two frames whose tops differ by 6mm, and nothing in the metadata says which a
    card is in terms anybody has measured — so both are returned, ``resolve`` fits the
    first and offers the rest, and a person picks per card. Within one pass the table's
    own order decides, which is what makes "the frame most cards of the set take" a
    property of how :data:`BASELINE` is written rather than of this function.
    """
    entries = BASELINE.get(game, ())
    sid = (set_id or "").lower()
    by_set = tuple(e.spec for e in entries if sid in e.sets)
    if by_set:
        return by_set
    generation = parse_generation((traits or {}).get(FRAME_TRAIT))
    if generation is None:
        return ()
    return tuple(e.spec for e in entries if generation in e.frames)


def baseline(
    set_id: str, game: GameId, traits: Mapping[str, str] | None = None
) -> GuideId | None:
    """The one shipped spec a fit would use — the first of :func:`baselines`."""
    found = baselines(set_id, game, traits)
    return found[0] if found else None


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
