"""The calibration chart: render it, read it back, fit a correction.

Colour calibration here is a **loop you can walk several times on one sheet of
paper**, because the medium is the thing being measured and paper is the thing
you have least of:

1. :func:`chart_page` renders a page with the chart in **one slot** of a grid
   (2×3 on A4 by default) and the rest left blank. Print it on the medium.
2. Scan it (scanner auto-correction OFF). :func:`read_scan` crops that slot and
   reads what every patch actually came back as.
3. That round is recorded on the profile, and the correction is refitted over
   **every round's** samples at once (:func:`fit`) — so each round adds real
   measurements instead of replacing the last ones.
4. Re-feed the *same* sheet, print the next round into the next free slot, and go
   again. The residual per round (:func:`error`) is what tells you it converged.

Round 1 prints the raw target, so it measures how far off the medium is. Round 2
prints the target *through* the correction learned so far, which samples the space
right where the cards live, and the fit gets truer exactly there. Six rounds fit
on one A4 sheet.

The scanner is the measuring device, so accuracy is "true as your scanner sees
it", not colorimetric — good enough for proxies, and bounded by scanner
neutrality and printer gamut.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont

from proxdex import colour
from proxdex.colour import Cast
from proxdex.config import Config
from proxdex.errors import ProxdexError

RGB = NDArray[np.float32]
#: an (n, 3) block of patch colours — one row per chart patch
Patches = NDArray[np.float32]
#: the (10, 3) polynomial coefficients of a correction
Coef = NDArray[np.float32]

# chart geometry, in normalized [0, 1] chart coordinates -----------------------
#: the width every chart is drawn at. The *height* follows the patch grid
#: (:func:`canvas_for`) rather than being a second constant, because a chart drawn at
#: the wrong aspect is letterboxed inside its box on the page — and the reader crops by
#: where the art really landed, so a writer and a reader disagreeing about that is the
#: whole of "read the gutters and call it paper".
CANVAS_W = 1200
#: the shape the old fixed canvas had, kept as the fallback for a chart with no cells
CANVAS_H = 1350
FIDUCIALS = ((0.06, 0.05), (0.94, 0.05), (0.06, 0.95), (0.94, 0.95))
_FID_SIZE = 0.045
_GRID = (0.12, 0.16, 0.88, 0.90)  # x0, y0, x1, y1 patch region
_LABEL_Y = 0.115


class Role(StrEnum):
    """What a patch is *for*.

    Every stage of the model selects patches by role, never by index arithmetic. The
    previous chart was a bare tuple of colours with ``slice(0, 16)`` spelled at each
    call site to mean "the greys", which is precisely the implicit index that breaks in
    silence the moment the patch set changes shape — and the patch set had to change
    shape, because it could not measure the substrate or linearize a channel.
    """

    #: bare paper: no ink at all. The reference the whole profile hangs from.
    SUBSTRATE = "substrate"
    #: one channel swept with the others at full — i.e. one ink's own ramp
    RAMP_R = "ramp-r"
    RAMP_G = "ramp-g"
    RAMP_B = "ramp-b"
    #: the grey axis, spaced in L*
    NEUTRAL = "neutral"
    #: the heaviest ink each way — the black point and the ink limit
    MAX_INK = "max-ink"
    #: a duplicate of another patch, placed far from it, to measure read noise
    REPEAT = "repeat"
    #: the interior of the colour cube
    LATTICE = "lattice"

    @property
    def ramp_channel(self) -> int | None:
        """Which channel this role sweeps, or None if it is not a channel ramp."""
        return {Role.RAMP_R: 0, Role.RAMP_G: 1, Role.RAMP_B: 2}.get(self)


@dataclass(frozen=True, slots=True)
class Patch:
    """One patch: the colour to send, and what it is being sent for."""

    rgb: tuple[int, int, int]
    role: Role
    #: for :attr:`Role.REPEAT`, the index of the patch it duplicates; -1 otherwise
    of: int = -1


class ChartId(StrEnum):
    """Which patch set a chart is — the closed set of targets proxdex can print.

    A round has to record this, and that it did not was a real blocker: every patch
    array was validated against ``len(chart())`` alone, so a 468-patch survey round was
    stored and then read back as **unreadable** — a verb that writes a round the loader
    silently discards. It is a closed set because code names every member (the
    verification chart and one per survey density), and it is on the *round* rather than
    on the profile because a profile characterized by a survey is then verified by a
    different chart, which is the whole shape of the new loop.
    """

    VERIFY = "verify"
    SURVEY_FULL = "survey-full"
    SURVEY_HALF = "survey-half"
    SURVEY_QUARTER = "survey-quarter"

    @property
    def size(self) -> SurveySize | None:
        """The survey density this is, or None for the verification chart."""
        return {
            ChartId.SURVEY_FULL: SurveySize.FULL,
            ChartId.SURVEY_HALF: SurveySize.HALF,
            ChartId.SURVEY_QUARTER: SurveySize.QUARTER,
        }.get(self)

    @property
    def spec(self) -> Chart:
        """The patch set itself."""
        size = self.size
        return verification() if size is None else survey(size)

    @property
    def label(self) -> str:
        size = self.size
        return "verification" if size is None else f"survey ({size.value})"

    @classmethod
    def of_survey(cls, size: SurveySize) -> ChartId:
        return {
            SurveySize.FULL: cls.SURVEY_FULL,
            SurveySize.HALF: cls.SURVEY_HALF,
            SurveySize.QUARTER: cls.SURVEY_QUARTER,
        }[size]

    @classmethod
    def read(cls, data: object) -> ChartId:
        """A chart id out of untrusted JSON.

        Total, and it answers :attr:`VERIFY` for anything unrecognised — including a
        round written before charts had ids. That is not a migration: such a round's
        patch arrays are then checked against the verification chart's length and fail,
        which is exactly the "counted, never dropped in silence" behaviour
        :attr:`proxdex.profiles.Profile.unreadable` exists for.
        """
        if isinstance(data, str):
            try:
                return cls(data)
            except ValueError:
                return cls.VERIFY
        return cls.VERIFY


@dataclass(frozen=True, slots=True)
class Chart:
    """The patch target: what it prints, and how it is laid out."""

    id: ChartId
    cols: int
    rows: int
    patches: tuple[Patch, ...]
    #: white gutter around each patch, as a share of its cell. Enough that wet ink
    #: from a neighbour cannot reach the sampled centre — and wide enough that the
    #: flare each patch picks up from its surroundings is *the same* for all of them,
    #: which is what makes a substrate-relative reading cancel it.
    pad: float

    def __len__(self) -> int:
        return len(self.patches)

    @property
    def target(self) -> Patches:
        return np.array([p.rgb for p in self.patches], np.float32)

    def of_role(self, *roles: Role) -> NDArray[np.intp]:
        """The indices of every patch serving one of ``roles``."""
        want = set(roles)
        return np.array(
            [i for i, p in enumerate(self.patches) if p.role in want], np.intp
        )

    @property
    def neutrals(self) -> NDArray[np.intp]:
        """Which patches are meant to be grey — where a cast is measured."""
        return self.of_role(Role.NEUTRAL)

    @property
    def substrate(self) -> NDArray[np.intp]:
        """Which patches are bare paper."""
        return self.of_role(Role.SUBSTRATE)

    @property
    def ramps(self) -> dict[int, NDArray[np.intp]]:
        """Per channel, the indices of that channel's own ramp."""
        return {
            channel: self.of_role(role)
            for role, channel in (
                (Role.RAMP_R, 0),
                (Role.RAMP_G, 1),
                (Role.RAMP_B, 2),
            )
        }

    def centers(self) -> list[tuple[float, float]]:
        x0, y0, x1, y1 = _GRID
        out: list[tuple[float, float]] = []
        for i in range(len(self.patches)):
            col, row = i % self.cols, i // self.cols
            out.append(
                (
                    x0 + (col + 0.5) / self.cols * (x1 - x0),
                    y0 + (row + 0.5) / self.rows * (y1 - y0),
                )
            )
        return out


#: the colour lattice is pulled *inside* the printable box on purpose. Corner
#: colours (pure red, 255 white) are unreachable on paper, so a patch spent there
#: is a patch that measures nothing; this range measured best on a narrow-gamut
#: matte, a wide-gamut glossy and a flat plain-paper press alike.
_LATTICE_LO, _LATTICE_HI = 50, 200


def _lstar_ramp(steps: int) -> list[tuple[int, int, int]]:
    """A neutral ramp spaced evenly in **L\\***, not in device code values.

    It used to be ``linspace(4, 252)``, which is even in the numbers and badly uneven
    in what an eye sees: after a printer's tone response most of the perceptual
    movement is crowded into the highlights, which is *also* where a tinted substrate
    shows through hardest — the real holographic sticker reads +57.75 blue-minus-red in
    the highlights against +5.50 in the shadows. So the one region that most needed
    samples had the fewest.
    """
    lab = np.zeros((steps, 3), np.float64)
    lab[:, 0] = np.linspace(2.0, 98.0, steps)
    return [tuple(round(float(v)) for v in row) for row in colour.from_lab(lab)]  # type: ignore[misc]


def _channel_ramp(channel: int, steps: int) -> list[tuple[int, int, int]]:
    """One channel swept while the others stay at full — the ink's own ramp.

    In an RGB send, *reducing* a channel is *adding* its complementary ink, so these
    three are the cyan, magenta and yellow ramps. Without them no per-channel
    linearization is possible at all, which is step two of every industry workflow and
    which the previous chart simply could not do: it had a neutral ramp and a lattice,
    and neither isolates one ink.
    """
    out: list[tuple[int, int, int]] = []
    for v in np.linspace(0.0, 255.0, steps):
        rgb = [255, 255, 255]
        rgb[channel] = round(float(v))
        out.append((rgb[0], rgb[1], rgb[2]))
    return out


#: bare paper, and the heaviest ink each way. The substrate patch is the reference the
#: whole profile hangs from (see :class:`Substrate`) and the previous chart had none —
#: its lightest patch was *printed* at 252, which is ink, not paper.
_MAX_INK: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),
    (0, 255, 255),
    (255, 0, 255),
    (255, 255, 0),
    (0, 0, 255),
    (255, 0, 0),
)


class SurveySize(StrEnum):
    """How much paper one characterization costs.

    A sheet of holographic sticker is not free, so this is a choice with its
    consequence stated rather than a constant. The ramps, the neutral ramp, the
    substrate patches and the max-ink patches are **the same at every size** — they are
    what the linearization, the grey balance and the substrate term are built from, and
    there is no patch to save there. Only the interior lattice shrinks.
    """

    FULL = "full"
    HALF = "half"
    QUARTER = "quarter"

    @property
    def grid(self) -> tuple[int, int]:
        """Patch columns and rows."""
        return {
            SurveySize.FULL: (18, 26),
            SurveySize.HALF: (18, 13),
            SurveySize.QUARTER: (12, 13),
        }[self]

    @property
    def lattice(self) -> int:
        """Steps per axis of the interior lattice — 7³, 5³ or 4³."""
        return {SurveySize.FULL: 7, SurveySize.HALF: 5, SurveySize.QUARTER: 4}[self]

    @property
    def ramp(self) -> int:
        """Steps in each single-channel ramp."""
        return {SurveySize.FULL: 17, SurveySize.HALF: 13, SurveySize.QUARTER: 9}[self]

    @property
    def neutrals(self) -> int:
        return {SurveySize.FULL: 21, SurveySize.HALF: 21, SurveySize.QUARTER: 15}[self]

    @property
    def repeats(self) -> int:
        return {SurveySize.FULL: 12, SurveySize.HALF: 8, SurveySize.QUARTER: 6}[self]

    @property
    def page_fraction(self) -> tuple[float, float]:
        """How much of the printable box it covers, as (width, height) shares."""
        return {
            SurveySize.FULL: (1.0, 1.0),
            SurveySize.HALF: (1.0, 0.5),
            SurveySize.QUARTER: (0.5, 0.5),
        }[self]

    def slots(self, grid: tuple[int, int] | None = None) -> tuple[Slot, ...]:
        """Which slots of the sheet this survey spends — so the rest stay offerable.

        A survey is not printed *in* a slot, but it is printed on paper the slots are
        cut out of, and the sheet does not care that the ink came from a different verb.
        A quarter survey therefore leaves four slots for verification, which is the
        cheap way through when paper is short; a full one spends the sheet and the next
        chart starts a fresh one.
        """
        sheet = grid or GRID
        frac_w, frac_h = self.page_fraction
        cols = max(1, math.ceil(sheet[0] * frac_w))
        rows = max(1, math.ceil(sheet[1] * frac_h))
        return tuple(Slot(col=c, row=r) for r in range(rows) for c in range(cols))


@cache
def survey(size: SurveySize = SurveySize.FULL) -> Chart:
    """The characterization target: every role, at this density.

    Laid out by :func:`_lay_out`, which weaves the substrate patches through it. Cached
    because it is asked for per round, per surface and per patch lookup, and building
    468 patches to answer "how many are there" is work nobody asked for.
    """
    cols, rows = size.grid
    content: list[Patch] = []
    content += [Patch(rgb=v, role=Role.NEUTRAL) for v in _lstar_ramp(size.neutrals)]
    for channel, role in enumerate((Role.RAMP_R, Role.RAMP_G, Role.RAMP_B)):
        content += [Patch(rgb=v, role=role) for v in _channel_ramp(channel, size.ramp)]
    content += [Patch(rgb=v, role=Role.MAX_INK) for v in _MAX_INK]
    steps = [
        round(float(v)) for v in np.linspace(_LATTICE_LO, _LATTICE_HI, size.lattice)
    ]
    content += [
        Patch(rgb=(r, g, b), role=Role.LATTICE)
        for r in steps
        for g in steps
        for b in steps
    ]
    # repeats are duplicates of content patches placed far from their originals, so the
    # spread between a pair measures read noise and cross-sheet drift rather than
    # anything about the colour
    stride = max(1, len(content) // max(1, size.repeats))
    repeats = [
        Patch(rgb=content[i * stride].rgb, role=Role.REPEAT, of=i * stride)
        for i in range(size.repeats)
        if i * stride < len(content)
    ]
    content += repeats

    return _lay_out(
        content, ident=ChartId.of_survey(size), cols=cols, rows=rows, pad=0.16
    )


def _lay_out(
    content: list[Patch], *, ident: ChartId, cols: int, rows: int, pad: float
) -> Chart:
    """Weave substrate patches through a patch list and fill the grid with them.

    Interleaved rather than grouped so the bare-paper readings sample the sheet at
    points spread across it — the only handle there is on a flatbed's centre-to-edge
    non-uniformity, measured in the literature at up to ΔE 5. Every leftover cell
    becomes one more substrate patch, so no cell is wasted and the white reference gets
    denser rather than the grid ragged.
    """
    cells = cols * rows
    spare = max(0, cells - len(content))
    # interleave: one substrate patch every `gap` content patches
    gap = max(1, len(content) // spare) if spare else len(content) + 1
    laid: list[Patch] = []
    placed = 0
    for i, patch in enumerate(content):
        if placed < spare and i % gap == 0:
            laid.append(Patch(rgb=(255, 255, 255), role=Role.SUBSTRATE))
            placed += 1
        laid.append(patch)
    laid += [Patch(rgb=(255, 255, 255), role=Role.SUBSTRATE)] * (spare - placed)
    laid = laid[:cells]
    # `of` was recorded against the pre-interleave list, so it has to be remapped once
    # the final order is known — otherwise a repeat names whatever landed at that index,
    # which is a silently wrong pairing and exactly the class of bug `Role` exists to
    # prevent. Found by the test that asserts a repeat matches the patch it duplicates.
    moved = {id(p): i for i, p in enumerate(laid)}
    fixed = [
        replace(patch, of=moved.get(id(content[patch.of]), -1))
        if patch.role is Role.REPEAT and 0 <= patch.of < len(content)
        else patch
        for patch in laid
    ]
    return Chart(id=ident, cols=cols, rows=rows, patches=tuple(fixed), pad=pad)


@cache
def verification() -> Chart:
    """The small chart that goes in **one slot** of the sheet grid, six to a sheet.

    Deliberately its own patch set rather than the smallest survey. A survey has to
    characterize, so it wants hundreds of patches; this one only has to *confirm* — is
    the model right, and is the grey still grey — and it lives in a sixth of a page,
    where patch **area** is the binding constraint. The trade's own advice for a
    scanner-read target is fewer, wider patches measured twice, and proxdex's earlier
    measurement agreed (228 patches scored worse than 80). Dropping the quarter survey
    in here would have put 156 patches where 80 used to sit — half the area each, for a
    job that does not need the density.

    9x9 = 81 cells: a coarse 3³ lattice, the neutral ramp (a cast is the thing being
    confirmed), one ramp step per channel, max ink, and substrate for its own white.
    """
    content: list[Patch] = []
    content += [Patch(rgb=v, role=Role.NEUTRAL) for v in _lstar_ramp(15)]
    for channel, role in enumerate((Role.RAMP_R, Role.RAMP_G, Role.RAMP_B)):
        content += [Patch(rgb=v, role=role) for v in _channel_ramp(channel, 5)]
    content += [Patch(rgb=v, role=Role.MAX_INK) for v in _MAX_INK]
    steps = [round(float(v)) for v in np.linspace(_LATTICE_LO, _LATTICE_HI, 3)]
    content += [
        Patch(rgb=(r, g, b), role=Role.LATTICE)
        for r in steps
        for g in steps
        for b in steps
    ]
    content += [
        Patch(rgb=content[i * 7].rgb, role=Role.REPEAT, of=i * 7) for i in range(4)
    ]
    return _lay_out(content, ident=ChartId.VERIFY, cols=9, rows=9, pad=0.16)


def chart() -> Chart:
    """The chart a round prints by default — the verification one.

    The survey is printed once per medium and asked for by name, so the *default* is
    the small chart that goes six to a sheet.
    """
    return verification()


def chart_patches() -> tuple[Patch, ...]:
    return verification().patches


def target() -> Patches:
    """The chart's patches as a float array — what a true print would scan as."""
    return verification().target


# ------------------------------------------------------------------- slots ----
#: how many charts one sheet holds. Six rounds of A4 is enough to converge and
#: still leaves the numbers readable at this chart size.
GRID: tuple[int, int] = (2, 3)
#: the chart sits inside its cell, so a small crop error lands on white paper
#: rather than in the neighbouring chart
_CELL_INSET = 0.06
#: and the crop is padded by less than that inset, for scan/page misalignment
_CROP_PAD = 0.04
#: a crop smaller than this is not a chart, it is a mistake
_MIN_CROP_PX = 2
#: a slot, or a grid, is exactly two numbers
_PAIR = 2


@dataclass(frozen=True, slots=True)
class Slot:
    """Which cell of the sheet a round was printed in."""

    col: int
    row: int

    @property
    def text(self) -> str:
        return f"{self.col + 1},{self.row + 1}"

    def json(self) -> list[int]:
        return [self.col, self.row]

    def within(self, grid: tuple[int, int]) -> bool:
        return 0 <= self.col < grid[0] and 0 <= self.row < grid[1]

    @classmethod
    def parse(cls, text: str, grid: tuple[int, int] = GRID) -> Slot:
        """``"2,3"`` → the slot, 1-based as printed on the chart."""
        parts = [p for p in text.replace("x", ",").split(",") if p.strip()]
        try:
            col, row = (int(p) - 1 for p in parts)
        except ValueError:
            raise ProxdexError(f"slot {text!r}: expected COL,ROW like '1,2'") from None
        slot = cls(col=col, row=row)
        if not slot.within(grid):
            raise ProxdexError(
                f"slot {text!r} is outside the {grid[0]}×{grid[1]} sheet grid"
            )
        return slot

    @classmethod
    def read(cls, data: object) -> Slot:
        if isinstance(data, list) and len(data) == _PAIR:
            pair: list[object] = data
            if all(isinstance(v, int) for v in pair):
                return cls(col=int(pair[0]), row=int(pair[1]))  # type: ignore[arg-type]
        return cls(col=0, row=0)


#: the slot a sheet starts at, and the default when none is given
FIRST_SLOT = Slot(col=0, row=0)


def slots(grid: tuple[int, int] = GRID) -> tuple[Slot, ...]:
    """Every slot of a sheet, in the order they are used (row-major)."""
    return tuple(Slot(col=c, row=r) for r in range(grid[1]) for c in range(grid[0]))


def _cell(
    cfg: Config, slot: Slot, grid: tuple[int, int]
) -> tuple[float, float, float, float]:
    """The slot's rectangle as a fraction of the page: x0, y0, x1, y1."""
    from proxdex.sheet import page_mm

    page_w, page_h = page_mm(cfg)
    mx = cfg.sheet_margin_mm / page_w
    my = cfg.sheet_margin_mm / page_h
    cw = (1.0 - 2 * mx) / grid[0]
    ch = (1.0 - 2 * my) / grid[1]
    x0 = mx + slot.col * cw
    y0 = my + slot.row * ch
    return x0, y0, x0 + cw, y0 + ch


# --------------------------------------------------------- correction ---------
# Degree-2 polynomial colour correction: the square terms act as per-channel
# tone curves, the cross terms as a colour matrix — so it subsumes a
# curves+matrix model without assuming an application order.
def _features(arr: RGB) -> RGB:
    n = (arr.clip(0, 255) / 255.0).astype(np.float32)
    r, g, b = n[..., 0], n[..., 1], n[..., 2]
    o = np.ones_like(r)
    return np.stack([o, r, g, b, r * r, g * g, b * b, r * g, g * b, b * r], axis=-1)


#: bisection steps for the gamut compression below. Twelve halvings resolve the
#: chroma scale to 1/4096, far finer than a send value can express.
_COMPRESS_STEPS = 12
#: the largest value a channel can be sent as — the edge of the reachable solid
_SEND_MAX = 255.0

#: the coefficients that change nothing — features(v) @ IDENTITY == v
IDENTITY: Coef = np.zeros((10, 3), np.float32)
IDENTITY[1, 0] = IDENTITY[2, 1] = IDENTITY[3, 2] = 255.0

#: ridge weight, in pseudo-samples of "change nothing". Deliberately small and
#: *absolute*: it conditions the solve and keeps a single round from inventing a
#: wild curve, but it does not grow with the data, so every round you add makes
#: the fit truer rather than being damped by the prior. It is not a defence
#: against a bad round — measured, a shuffled scan is ruinous at any weight — and
#: the honest defence for that is naming it (`calibrate add` warns) and being able
#: to remove it (`calibrate drop`).
_RIDGE = 0.1


#: what asking for a send looks like: a wanted colour in, the send it needs out,
#: unclamped — so a caller can see *how far* out of range a colour is
Asker = Callable[[RGB], RGB]
#: whether each wanted colour is one this medium can actually be asked for. Separate
#: from the asker because "the send came out in range" is **not** the same question, and
#: the difference is a measured defect: a degree-2 transform extrapolating outside the
#: region it was fitted over returns a perfectly valid-looking send for a colour the
#: paper cannot make. On the simulated blue sticker a wanted dark yellow was sent (97,
#: 94, 207) — in range, so nothing compressed it — and came back **blue**, at ΔE00 63.6.
Fits = Callable[[RGB], NDArray[np.bool_]]


class Sender(Protocol):
    """Anything that can say what to print for a colour you want.

    A protocol rather than a base class because there are two, and the module holding
    the interesting one (:class:`proxdex.press.PressModel`) imports *this* module:
    naming it structurally is what lets a chart be rendered through either without the
    cycle. The two are the staged press model and the bare degree-2 :class:`Correction`
    it demotes to its own last stage.
    """

    def send(self, arr: RGB, /) -> RGB:
        """The value to print, with unreachable colours compressed.

        Positional-only, so the two implementations may name the argument for what it
        means to each of them — a bare correction takes the colour you *want*, the
        staged model the same — without a protocol quibbling about the spelling.
        """
        ...  # pragma: no cover — a protocol body


def in_range(raw: Asker) -> Fits:
    """The default reachability test: the send this colour needs fits in 0..255.

    Necessary and **not** sufficient — see :data:`Fits`. A model that knows the region
    it was measured over should pass a stricter one.
    """

    def fits(arr: RGB) -> NDArray[np.bool_]:
        asked = raw(arr)
        return np.asarray(
            ((asked >= 0.0) & (asked <= _SEND_MAX)).all(axis=-1), dtype=np.bool_
        )

    return fits


def _fits(fits: Fits, lab: colour.Lab) -> NDArray[np.bool_]:
    return fits(colour.from_lab(lab))


def _give_up_chroma(fits: Fits, lab: colour.Lab) -> colour.Lab:
    """Pull toward the neutral axis of the same lightness, at constant hue."""
    lo = np.zeros(len(lab), np.float64)
    hi = np.ones(len(lab), np.float64)
    for _ in range(_COMPRESS_STEPS):
        mid = (lo + hi) / 2.0
        trial = lab.copy()
        trial[:, 1:] *= (1.0 - mid)[:, None]
        ok = _fits(fits, trial)
        hi = np.where(ok, mid, hi)
        lo = np.where(ok, lo, mid)
    out = lab.copy()
    out[:, 1:] *= (1.0 - hi)[:, None]
    return out


def _toward(fits: Fits, lab: colour.Lab, end: float) -> tuple[colour.Lab, RGB]:
    """Bisect L\\* toward ``end``: the least move that fits, and how far that was.

    The distance comes back so the caller can pick between the two directions, and it is
    ``inf`` where even the whole way does not fit — which is what makes "neither
    direction works" a case rather than a silently bad answer.
    """
    lo = np.zeros(len(lab), np.float64)
    hi = np.ones(len(lab), np.float64)
    for _ in range(_COMPRESS_STEPS):
        mid = (lo + hi) / 2.0
        trial = lab.copy()
        trial[:, 0] = lab[:, 0] * (1.0 - mid) + end * mid
        ok = _fits(fits, trial)
        hi = np.where(ok, mid, hi)
        lo = np.where(ok, lo, mid)
    out = lab.copy()
    out[:, 0] = lab[:, 0] * (1.0 - hi) + end * hi
    reached = _fits(fits, out)
    cost = np.where(reached, np.abs(out[:, 0] - lab[:, 0]), np.inf)
    return out, np.asarray(cost, np.float32)


def _give_up_lightness(fits: Fits, lab: colour.Lab) -> colour.Lab:
    """For what still does not fit, move along L\\* — never per channel.

    This half was missing from the first version, and its absence left the real measured
    failure unfixed. The real case is a **neutral**: a 200-grey wanted send
    (273, 264, 299) and got (255, 255, 255), 44 of blue refused against 18 of red. A
    neutral has no chroma to give up, so chroma compression does nothing at all for it
    and the per-channel clamp — the thing that moves the hue — happens anyway.

    What a colour out of gamut with no chroma left really means is a *lightness* this
    medium does not have, so the answer is to move along its own axis until it does.
    **Both** directions are tried and the smaller move wins, which is the second thing
    this got wrong: the direction used to be inferred from which end the *send*
    overflowed, and that says nothing at all about a colour whose send is in range and
    which is unprintable for another reason — a real case, since a transform
    extrapolating past the region it was fitted over returns in-range nonsense. On a
    medium whose white is L\\* 74, guessing "toward 100" moved such a colour further
    outside the gamut every time, so it ended up fully white and its send came back
    arbitrary: a wanted dark orange was sent (21, 66, 95) — heavy ink for a light colour
    — where the least move puts it at (223, 154, 56).
    """
    stuck = ~_fits(fits, lab)
    if not stuck.any():
        return lab
    darker, cost_down = _toward(fits, lab, 0.0)
    lighter, cost_up = _toward(fits, lab, 100.0)
    # where neither direction ever fits, keep the smaller move rather than throwing the
    # colour at an endpoint: the send is clamped downstream either way, and a colour
    # that stayed near where it was asked for is the less wrong of the two
    up = np.where(
        np.isinf(cost_down) & np.isinf(cost_up),
        np.abs(lighter[:, 0] - lab[:, 0]) < np.abs(darker[:, 0] - lab[:, 0]),
        cost_up < cost_down,
    )
    out = lab.copy()
    moved = np.where(up, lighter[:, 0], darker[:, 0])
    out[:, 0] = np.where(stuck, moved, lab[:, 0])
    return out


def compress(raw: Asker, arr: RGB, fits: Fits | None = None) -> RGB:
    """The value to send, with the unreachable **compressed rather than clamped**.

    A per-channel clamp is what put the yellow on the cards, and it is visible in one
    line of the real profile: a 200-grey wanted send (273, 264, 299) and got (255, 255,
    255) — 44 of blue refused against 18 of red, so the hue moved. Per channel, clipping
    "favors preserving high saturation" and wrecks lightness; the CIE's own anchors for
    doing it properly (HPMINDE, SGCK) both hold **hue** constant and give up chroma.

    So a colour whose send does not fit is pulled toward the neutral axis of its own
    lightness, at constant hue, by the least amount that makes it fit — found by
    bisection because neither model's response has a closed form. Anything still out of
    range after all its chroma is gone is a *lightness* the medium does not have, and
    only then is it clamped.

    A free function taking the asker rather than a method, because there are two things
    that ask — the degree-2 polynomial and the staged press model — and compression
    living on one of them is a second implementation waiting to happen on the other.
    """
    reachable = in_range(raw) if fits is None else fits
    want = np.asarray(arr, np.float32)
    flat = want.reshape(-1, 3)
    out = raw(flat)
    bad = ~reachable(flat)
    if not bad.any():
        return out.clip(0, 255).reshape(want.shape).astype(np.float32)
    lab = colour.to_lab(flat[bad])
    lab = _give_up_chroma(reachable, lab)
    lab = _give_up_lightness(reachable, lab)
    out[bad] = raw(colour.from_lab(lab))
    return out.clip(0, 255).reshape(want.shape).astype(np.float32)


@dataclass(frozen=True, slots=True)
class Correction:
    """What to send the printer so the paper comes back the colour you asked for.

    The degree-2 polynomial — which is now **one stage of**
    :class:`proxdex.press.PressModel` rather than the whole model. Its square terms act
    as per-channel tone curves and its cross terms as a colour matrix, which is why it
    can stand alone; standing alone is exactly the defect, because a transform fitted
    over a non-linear, grey-unbalanced response has to spend its parameters undoing them
    before it can describe a colour.
    """

    coef: Coef  # (10, 3): send = features(wanted) . coef

    def apply(self, arr: RGB) -> RGB:
        return (_features(arr) @ self.coef).clip(0, 255).astype(np.float32)

    def raw(self, arr: RGB) -> RGB:
        """The send this correction asks for, **before** anything is clamped."""
        return (_features(arr) @ self.coef).astype(np.float32)

    def send(self, arr: RGB) -> RGB:
        """The value to send, the unreachable compressed — see :func:`compress`."""
        return compress(self.raw, arr)

    def apply_to_image(self, im: Image.Image) -> Image.Image:
        arr = np.asarray(im.convert("RGB"), np.float32)
        return Image.fromarray(self.send(arr).round().astype(np.uint8))

    def json(self) -> list[list[float]]:
        return [[float(v) for v in row] for row in self.coef]

    @classmethod
    def read(cls, data: object) -> Correction | None:
        """A correction from untrusted JSON, or None if it isn't one."""
        if not isinstance(data, list):
            return None
        arr = np.asarray(data, np.float32)
        if arr.shape != IDENTITY.shape or not np.isfinite(arr).all():
            return None
        return cls(coef=arr)


# ------------------------------------------------------------- the substrate ----
#: within-patch/inter-patch disagreement above which the bare-paper readings are not
#: describing one colour. Either the scanner is very non-uniform across the sheet (the
#: literature measures up to ΔE 5 centre-to-edge on a flatbed) or the substrate is
#: gonioapparent — a holographic or metallic stock whose colour depends on the angle it
#: is lit and viewed at, which one fixed scanning geometry cannot measure at all.
SUBSTRATE_UNEVEN = 4.0


@dataclass(frozen=True, slots=True)
class Substrate:
    """The paper itself: bare, and under the heaviest ink it takes.

    The chart is covered in unprinted paper — every gutter and margin — and the previous
    one measured none of it, which is why nothing knew that ``holo-plain``'s stock is
    blue. Its lightest patch was *printed* at 252, which is ink, not substrate.

    Three separate facts come out of the bare patches, all free and all needing no
    reference target: what the paper *is* (which is what a relative aim needs), a
    per-round white (so rounds scanned on different days are not pooled as though the
    lamp had not moved), and how much the readings disagree **across the sheet**, which
    is the only honest signal that a reading is not a measurement.
    """

    white: tuple[float, float, float] = (255.0, 255.0, 255.0)
    black: tuple[float, float, float] = (0.0, 0.0, 0.0)
    #: the largest ΔE00 between any two bare-paper readings on this sheet
    spread: float = 0.0
    #: how many bare patches it was measured over
    patches: int = 0

    @property
    def measured(self) -> bool:
        return self.patches > 0

    @property
    def cast(self) -> Cast:
        """How far the bare paper is off neutral — what no ink can remove."""
        return Cast.of(colour.to_lab(np.array([self.white], np.float32)))

    @property
    def even(self) -> bool:
        return self.spread < SUBSTRATE_UNEVEN

    @property
    def text(self) -> str:
        w = " ".join(f"{v:.0f}" for v in self.white)
        return f"paper {w} — {self.cast.hue}, spread {self.spread:.2f} ΔE00"

    @property
    def warning(self) -> str:
        """Said out loud when the paper cannot be measured at one geometry."""
        if self.even:
            return ""
        return (
            f"the bare-paper readings disagree by {self.spread:.2f} ΔE00 across this "
            "sheet, so they are not describing one colour — either the scan is very "
            "uneven or the stock is holographic/metallic, whose colour depends on the "
            "angle it is lit at and which one scanning geometry cannot measure"
        )

    def json(self) -> dict[str, Any]:
        return {
            "white": list(self.white),
            "black": list(self.black),
            "spread": self.spread,
            "patches": self.patches,
            # served explicitly, though it is `patches > 0`: it was left out, and the
            # print screen read `undefined` as false and printed **"Not measured"** over
            # a paper the CLI was reporting as (151, 189, 203) on the same profile. A
            # derived property a reader has to re-derive is a property two readers will
            # disagree about.
            "measured": self.measured,
            "cast": self.cast.json(),
            "even": self.even,
            "text": self.text,
            "warning": self.warning,
        }

    @classmethod
    def read(cls, data: object) -> Substrate:
        raw: dict[str, object] = data if isinstance(data, dict) else {}
        return cls(
            white=_triple(raw.get("white"), 255.0),
            black=_triple(raw.get("black"), 0.0),
            spread=_float(raw.get("spread")),
            patches=int(_float(raw.get("patches"))),
        )

    @classmethod
    def of(cls, scanned: Patches, spec: Chart | None = None) -> Substrate:
        """Read the paper off a scanned chart's bare and max-ink patches."""
        card = spec or chart()
        seen = np.asarray(scanned, np.float32)
        bare = card.substrate
        inked = card.of_role(Role.MAX_INK)
        if not len(bare) or len(seen) < len(card):
            return cls()
        whites = seen[bare]
        lab = colour.to_lab(whites)
        spread = 0.0
        if len(lab) > 1:
            pairs = colour.delta_e00(lab[:, None, :], lab[None, :, :])
            spread = float(pairs.max())
        black = (
            tuple(float(v) for v in seen[inked].min(axis=0))
            if len(inked)
            else (0.0, 0.0, 0.0)
        )
        return cls(
            white=tuple(float(v) for v in np.median(whites, axis=0)),  # type: ignore[arg-type]
            black=black,  # type: ignore[arg-type]
            spread=spread,
            patches=len(bare),
        )


# ------------------------------------------------------------- the reference ----
#: how far a scanner reading can be from the colour it is a reading *of*, in ΔE00, with
#: nothing characterizing the scanner. Measured in the literature: patches with
#: identical scanned RGB can differ by more than this in CIELAB, because a flatbed's
#: R/G/B sensitivities are not a linear transform of the CIE colour matching functions.
ASSUMED_FLOOR = 10.0
#: the ΔE00 a matrix profile off an IT8 target gets a flatbed down to, also measured —
#: uncorrected 12.3 → 4.9. Quoted so the offer says what it is worth rather than
#: implying a reference makes a flatbed a colorimeter.
REFERENCE_FLOOR = 4.9
#: a 3x3 needs at least three independent readings to be determined at all
_MATRIX_ROWS = 3


#: the 24 ColorChecker patches, as CIE L*a*b* under D50 — X-Rite's own published values
#: for the current (post-2014) manufacture. Published numbers rather than a measurement
#: of one card is the whole point: it is what makes the target a *reference*, and it is
#: also the honest limit — a real chart fades, and a faded one is a confident wrong
#: answer. Reading order is the chart's own, left to right, top to bottom.
_COLORCHECKER_LAB: tuple[tuple[float, float, float], ...] = (
    (37.99, 13.56, 14.06),  # dark skin
    (65.71, 18.13, 17.81),  # light skin
    (49.93, -4.88, -21.93),  # blue sky
    (43.14, -13.10, 21.91),  # foliage
    (55.11, 8.84, -25.40),  # blue flower
    (70.72, -33.40, -0.20),  # bluish green
    (62.66, 36.07, 57.10),  # orange
    (40.02, 10.41, -45.96),  # purplish blue
    (51.12, 48.24, 16.25),  # moderate red
    (30.33, 22.98, -21.59),  # purple
    (72.53, -23.71, 57.26),  # yellow green
    (71.94, 19.36, 67.86),  # orange yellow
    (28.78, 14.18, -50.30),  # blue
    (55.26, -38.34, 31.37),  # green
    (42.10, 53.38, 28.19),  # red
    (81.73, 4.04, 79.82),  # yellow
    (51.94, 49.99, -14.57),  # magenta
    (51.04, -28.63, -28.64),  # cyan
    (96.54, -0.43, 1.19),  # white
    (81.26, -0.64, -0.34),  # neutral 8
    (66.77, -0.73, -0.50),  # neutral 6.5
    (50.87, -0.15, -0.27),  # neutral 5
    (35.66, -0.42, -1.23),  # neutral 3.5
    (20.46, -0.08, -0.97),  # black
)


class ReferenceTarget(StrEnum):
    """Which published target a reference scan is of.

    A closed set because each member carries *numbers* — the patches' known Lab values
    and the grid they are printed in — and a target proxdex does not have the numbers
    for is one it cannot use. IT8.7/2 is the industry answer here and is deliberately
    **not** a member: its 264 patches are batch-specific and come with a data file per
    production run, so hardcoding "the" IT8 values would be inventing a measurement —
    exactly the thing this whole area exists to stop.
    """

    COLORCHECKER = "colorchecker"

    @property
    def lab(self) -> colour.Lab:
        """Its patches, in the **D65** Lab everything here uses.

        The published values are D50/2°, being graphic-arts numbers, so the adaptation
        happens here — once, where the target's own illuminant is known — rather than
        being left to each caller to notice. Ignoring it would put several ΔE00 on the
        blues, which is the same order as the error a reference is read to remove.
        """
        return colour.from_lab_d50(np.array(_COLORCHECKER_LAB, np.float64))

    @property
    def grid(self) -> tuple[int, int]:
        """Patch columns and rows, in the order :attr:`lab` lists them."""
        return {ReferenceTarget.COLORCHECKER: (6, 4)}[self]

    @property
    def cols(self) -> int:
        return self.grid[0]

    @property
    def rows(self) -> int:
        return self.grid[1]

    @property
    def patches(self) -> int:
        return len(self.lab)


def read_reference(path: Path, kind: ReferenceTarget) -> Patches:
    """Read a reference target's patches out of a scan cropped to the chart.

    Deliberately simpler than :func:`read_scan`: there are no fiducials on a
    ColorChecker, so the scan has to *be* the chart and the grid is assumed to fill it.
    Each patch is the median of its middle half, which is the same sampling rule the
    calibration chart uses and for the same reason — a median over thousands of pixels
    survives a speck of dust, a mean does not.
    """
    arr = np.asarray(Image.open(path).convert("RGB"), np.float32)
    height, width, _ = arr.shape
    cols, rows = kind.grid
    out = np.zeros((cols * rows, 3), np.float32)
    for i in range(cols * rows):
        col, row = i % cols, i // cols
        x0, x1 = (col + 0.3) * width / cols, (col + 0.7) * width / cols
        y0, y1 = (row + 0.3) * height / rows, (row + 0.7) * height / rows
        cell = arr[round(y0) : round(y1), round(x0) : round(x1)]
        if cell.size:
            out[i] = np.median(cell.reshape(-1, 3), axis=0)
    return out


@dataclass(frozen=True, slots=True)
class Reference:
    """Scanner reading → reference space. The identity, and **loudly** so, by default.

    This is the one piece of the whole rebuild that cannot be built out of measurements
    proxdex can already take, and it is the root cause of §1.1. ``fit`` learns *scanner
    reading → send*, so the correction's input domain is scanner RGB; ``render`` then
    feeds it sRGB card pixels. Nothing establishes that those are the same space, and
    they are not.

    That makes the whole loop converge — correctly — on **"the print scans as the
    target's numbers"** when what anyone wants is **"the print looks like the card"**.
    It is the documented guarantee of the closed-loop method: accurate only for input
    with the same ink and halftone characteristics as the calibration patches. A
    Scryfall PNG is not that. On ordinary matte a decent scanner is near enough to sRGB
    that the two objectives nearly coincide, which is why matte profiles work; on a
    coloured or specular substrate they diverge, and the divergence is the cast.

    So the transform is **named** rather than tuned around. Every profile has one, it is
    the identity until a reference target is read, and :attr:`assumed` is reported on
    every surface that names a profile — because §1.1 happened as an *unstated*
    assumption, and an unstated assumption is exactly how it happened.

    Deliberately last, and deliberately optional: everything above it — relative aiming,
    the staged model, grey balance, gamut compression, ΔE00 scoring, a gamut measured
    from scans — is worth having without it, and it is the only stage that needs a
    purchase.
    """

    #: (3, 3) reading → reference. The identity says "assume the scanner is right".
    matrix: NDArray[np.float64] = field(
        default_factory=lambda: np.eye(3, dtype=np.float64)
    )
    #: what it was read off, for the record — "" while assumed
    target: str = ""
    #: mean ΔE00 the fitted matrix achieved over the reference patches
    error: float = 0.0
    #: how many known patches it was fitted from
    patches: int = 0

    @property
    def assumed(self) -> bool:
        """True while nothing has characterized the scanner — the ordinary state."""
        return self.patches == 0

    @property
    def floor(self) -> float:
        """How far off a reading may be, in ΔE00, given what is known of the scanner."""
        return ASSUMED_FLOOR if self.assumed else max(self.error, REFERENCE_FLOOR)

    @property
    def text(self) -> str:
        if self.assumed:
            return (
                f"assumed — the scanner is taken to read sRGB, which is worth about "
                f"ΔE00 {ASSUMED_FLOOR:.0f} of accuracy no calibration can remove"
            )
        return (
            f"measured off {self.target or 'a reference target'} over {self.patches} "
            f"patch(es), ΔE00 {self.error:.2f}"
        )

    @property
    def warning(self) -> str:
        """Said wherever this profile is named, while the reference is assumed."""
        if not self.assumed:
            return ""
        return (
            "this profile's scanner is uncharacterized, so its numbers mean 'the print "
            f"scans as the target' rather than 'the print looks like the card' — worth "
            f"about ΔE00 {ASSUMED_FLOOR:.0f} on a coloured or specular stock. "
            "`proxdex calibrate reference <scan>` reads an IT8 or ColorChecker and "
            "removes it"
        )

    def apply(self, reading: Patches) -> Patches:
        """A scanner reading in the reference space — the identity while assumed."""
        arr = np.asarray(reading, np.float32)
        if self.assumed:
            return arr
        lin = colour.linearize(arr) @ self.matrix.T
        return colour.encode(np.clip(lin, 0.0, 1.0))

    def json(self) -> dict[str, Any]:
        return {
            "matrix": [[float(v) for v in row] for row in self.matrix],
            "target": self.target,
            "error": self.error,
            "patches": self.patches,
            "assumed": self.assumed,
            "floor": self.floor,
            "text": self.text,
            "warning": self.warning,
        }

    @classmethod
    def read(cls, data: object) -> Reference:
        """A reference out of untrusted JSON, and **total** like every reader here.

        A profile file is hand-editable, so ``{"matrix": "x"}`` is a thing that can
        be on disk; converting it raised, which would take the whole profile with it. An
        unreadable reference is the *assumed* one, which is the honest fallback: it says
        the scanner is uncharacterized, which it now is.
        """
        raw: dict[str, object] = data if isinstance(data, dict) else {}
        try:
            matrix = np.asarray(raw.get("matrix") or np.eye(3), np.float64)
        except (TypeError, ValueError):
            return cls()
        if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
            return cls()
        return cls(
            matrix=matrix,
            target=str(raw.get("target") or ""),
            error=_float(raw.get("error")),
            patches=int(_float(raw.get("patches"))),
        )

    @classmethod
    def from_target(
        cls, readings: Patches, known: colour.Lab, *, target: str = ""
    ) -> Reference:
        """Fit reading → reference from a target whose Lab values are published.

        A 3x3 in **linear light**, which is the industry answer for profiling without a
        spectrophotometer — IT8-calibrate the scanner, then use it as the instrument. A
        matrix and not a polynomial on purpose: a scanner's departure from colorimetric
        is a linear-algebra fact about its filters, and a higher-order fit over a few
        dozen patches would chase read noise and be worse off the target's own colours.
        """
        read = np.asarray(readings, np.float32).reshape(-1, 3)
        want = np.asarray(known, np.float64).reshape(-1, 3)
        if len(read) < _MATRIX_ROWS or len(read) != len(want):
            return cls()
        src = colour.linearize(read)
        dst = colour.linearize(colour.from_lab(want))
        matrix, *_ = np.linalg.lstsq(src, dst, rcond=None)
        out = cls(matrix=matrix.T.astype(np.float64), target=target, patches=len(read))
        error = float(colour.delta_e00(colour.to_lab(out.apply(read)), want).mean())
        return replace(out, error=error)


# ----------------------------------------------------------------- the aim ----
@dataclass(frozen=True, slots=True)
class Intent:
    """How much of the paper's own colour to accept rather than fight.

    ``1.0`` — the default — aims **relative to the substrate**: a card's white prints as
    the paper's white, because on a blue holographic sticker that *is* white and no ink
    makes it whiter. Your eye adapts to the sheet in your hand, which is what ordinary
    relative-colorimetric rendering assumes.

    ``0.0`` is the old behaviour, aiming at an absolute neutral, and it is reachable
    deliberately rather than by default — because on the real sticker it is what drove
    the fit to demand a\\* +4.27 b\\* +10.62 for a grey, pinning red and green at the
    ceiling so that the per-channel clip turned every highlight yellow.

    Anything between is a partial adaptation, which is a legitimate preference and what
    a perceptual intent does. Black point compensation is deliberately **not** offered:
    it was tried and measured, and it roughly doubled ΔE00 (7.56 → 16.92) by lifting the
    whole shadow end. Map the white; do not map the black.
    """

    adaptation: float = 1.0

    @property
    def relative(self) -> bool:
        return self.adaptation > 0.0

    @property
    def text(self) -> str:
        if self.adaptation >= 1.0:
            return "relative to the paper (a card's white prints as the paper's white)"
        if self.adaptation <= 0.0:
            return "absolute (aims at a neutral white the paper may not reach)"
        return f"{self.adaptation:.0%} of the way toward the paper's own white"

    def json(self) -> dict[str, Any]:
        return {
            "adaptation": self.adaptation,
            "relative": self.relative,
            "text": self.text,
        }

    @classmethod
    def read(cls, data: object) -> Intent:
        raw: dict[str, object] = data if isinstance(data, dict) else {}
        if "adaptation" not in raw:
            return cls()
        return cls(adaptation=min(1.0, max(0.0, _float(raw.get("adaptation")))))


def aim(goal: Patches, substrate: Substrate, intent: Intent) -> Patches:
    """What to actually ask the paper for, given what the paper is.

    Blended in **linear light**, so the white end lands exactly on the measured paper at
    full adaptation and exactly on the target at none.

    Measured on the real ``holo-plain`` rounds, this one change takes the mid-grey send
    from (232, 189, 166) to (139.5, 137.5, 144.4), the neutral ramp's blue-minus-red
    tilt from -34.23 to +2.06, and the number of patches whose send clips from 48 of 80
    to 5 of 80 — refitting evidence already on disk, with nothing new printed.
    """
    if not intent.relative or not substrate.measured:
        return np.asarray(goal, np.float32)
    lin_goal = colour.linearize(goal)
    lin_white = colour.linearize(np.array(substrate.white, np.float32))
    scale = (1.0 - intent.adaptation) + intent.adaptation * lin_white
    return colour.encode(lin_goal * scale)


#: how many candidate colours adaptive placement chooses from. A few thousand is plenty
#: to fill the lattice's gaps and cheap enough to do on every chart.
_CANDIDATES = 4096


def adaptive(spec: Chart, gamut: Gamut, seen: Patches) -> Patches:
    """This chart's patches moved to where the model is least sure — inside the gamut.

    A verification chart with a fixed lattice spends its patches on colours the paper
    cannot make: measured, **43 of 80** round-2 sends clipped on foil, and ``usable``
    then drops them, leaving 37 samples to say anything with. So the lattice patches are
    replaced by the reachable colours **furthest from anything already measured**, which
    is the ``targen -c`` mechanism — ArgyllCMS raises its sampling adaptation from 0.1
    to 1.0 once a prior profile exists, for exactly this reason.

    Three things stay where they are, and that is the point of doing this by
    :class:`Role`: the neutral ramp (a cast is what is being confirmed, and it has to be
    confirmed at the same lightnesses every time), the substrate patches (a round needs
    its own white) and the ramps and max-ink (which is what the numbers rest on). Only
    the interior moves, so two verification rounds remain comparable on everything a
    trend is read from.

    Deterministic, because a chart that placed its patches differently on two renders of
    the same profile would make two verification errors incomparable.
    """
    target = spec.target.copy()
    lattice = spec.of_role(Role.LATTICE)
    if not len(lattice) or not gamut.measured:
        return target
    grid = np.linspace(_LATTICE_LO, _LATTICE_HI, 16, dtype=np.float32)
    cube = np.stack(np.meshgrid(grid, grid, grid, indexing="ij"), axis=-1).reshape(
        -1, 3
    )
    cube = cube[:: max(1, len(cube) // _CANDIDATES)]
    inside = cube[gamut.holds(cube)]
    if len(inside) < len(lattice):
        return target
    # farthest-point sampling in Lab from what has already been measured: each pick is
    # the reachable colour the model has the least evidence anywhere near
    lab = colour.to_lab(inside)
    known = colour.to_lab(np.asarray(seen, np.float32).reshape(-1, 3))
    far = (
        np.min(np.linalg.norm(lab[:, None, :] - known[None, :, :], axis=-1), axis=1)
        if len(known)
        else np.full(len(lab), np.inf)
    )
    picked: list[int] = []
    for _ in range(len(lattice)):
        best = int(np.argmax(far))
        picked.append(best)
        far = np.minimum(far, np.linalg.norm(lab - lab[best], axis=-1))
    target[lattice] = inside[picked]
    return target


#: below this many usable samples, take the clipped ones back rather than fit a
#: 10-term polynomial to almost nothing
_MIN_SAMPLES = 12
#: what counts as pinned against the bottom or top of what can be sent
_FLOOR, _CEILING = 0.5, 254.5


def usable(sent: Patches) -> NDArray[np.bool_]:
    """Which samples carry information about the printer's response.

    A send value pinned at 0 or 255 is a colour the printer was *asked* for and
    could not make: several different wanted colours all clip to the same send, so
    the pair says nothing about the invertible part of the response and only drags
    the polynomial away from the range the cards actually live in.
    """
    inside = (sent > _FLOOR) & (sent < _CEILING)
    return np.asarray(inside.all(axis=1), dtype=np.bool_)


def fit(scanned: Patches, sent: Patches) -> Correction:
    """Fit ``wanted -> send`` from every (scanned, sent) pair measured so far.

    ``scanned`` is what the paper gave back, ``sent`` is what was asked for, so
    the fit maps a colour you *want* to the value that produces it. Stacking every
    round's pairs is what makes the loop converge: later rounds sample the space
    nearer the target, where accuracy actually matters.
    """
    keep = usable(sent)
    if keep.sum() >= _MIN_SAMPLES:
        scanned, sent = scanned[keep], sent[keep]
    feat = _features(scanned)
    # an absolute prior, not one that grows with the data: a ridge proportional to
    # the sample count damps every round you add, which is exactly backwards for a
    # loop whose whole purpose is to get truer the more you measure
    gram = feat.T @ feat + _RIDGE * np.eye(feat.shape[1], dtype=np.float32)
    rhs = feat.T @ sent + _RIDGE * IDENTITY
    coef = np.linalg.solve(gram, rhs)
    return Correction(coef=coef.astype(np.float32))


@dataclass(frozen=True, slots=True)
class Error:
    """How far a print landed from the target, over the colours it could reach.

    **Three numbers, never one.** This used to be a single Euclidean distance in RGB,
    and one number is what let a diverging profile look converged: the real
    ``holo-plain`` drove its neutral axis 32 levels toward yellow over four rounds
    while that figure improved every round. RGB distance cannot see a cast, and a cast
    is the first thing an eye sees.

    So ``de00_mean``/``de00_max`` are CIEDE2000, the distance a print is really judged
    by, and ``cast`` is measured off the **neutral patches alone**. A medium also has a
    *gamut* — white paper is not 255, no ink is 0, and a saturated colour can need more
    of one ink than exists — so those cover the patches this medium can actually hit
    (:func:`reachable`) and ``clipped`` says how many it cannot, because averaging in
    colours no ink can make gives a number that can never fall.
    """

    de00_mean: float
    de00_max: float
    cast: Cast = field(default_factory=Cast)
    #: patches the error was measured over
    measured: int = 0
    #: target patches outside what this medium can print — a fact about the paper
    #: and ink, not a fault in the calibration
    clipped: int = 0

    @property
    def total(self) -> int:
        return self.measured + self.clipped

    @property
    def text(self) -> str:
        return f"ΔE00 {self.de00_mean:.2f} · {self.cast.text}"

    def json(self) -> dict[str, Any]:
        return {
            "de00_mean": self.de00_mean,
            "de00_max": self.de00_max,
            "cast": self.cast.json(),
            "measured": self.measured,
            "clipped": self.clipped,
        }

    @classmethod
    def read(cls, data: object) -> Error:
        raw: dict[str, object] = data if isinstance(data, dict) else {}
        return cls(
            de00_mean=_float(raw.get("de00_mean")),
            de00_max=_float(raw.get("de00_max")),
            cast=Cast.read(raw.get("cast")),
            measured=int(_float(raw.get("measured"))),
            clipped=int(_float(raw.get("clipped"))),
        )


#: how many directions the reachability hull is tested along. In three dimensions a
#: couple of hundred well-spread normals bound a hull closely, and the approximation is
#: deliberately an *outer* one — see :func:`reachable`.
_HULL_DIRECTIONS = 256


def _directions(count: int = _HULL_DIRECTIONS) -> NDArray[np.float64]:
    """Roughly-even unit vectors on the sphere — a golden-angle spiral.

    Deterministic, because a gamut that came out differently on two reads of the same
    file would make every residual incomparable with itself.
    """
    i = np.arange(count, dtype=np.float64) + 0.5
    z = 1.0 - 2.0 * i / count
    r = np.sqrt(np.maximum(0.0, 1.0 - z * z))
    phi = i * math.pi * (3.0 - math.sqrt(5.0))
    return np.stack([r * np.cos(phi), r * np.sin(phi), z], axis=-1)


@dataclass(frozen=True, slots=True)
class Gamut:
    """The solid this medium has been **seen** to produce, in Lab.

    A *solid* rather than a mask over one chart's patches, and that distinction is what
    made a survey round scorable at all: a profile has one gamut, but a survey asks
    about 468 colours and its verification chart about 81, so a boolean array of the
    wrong length is not a smaller answer — it is an index error or, worse, a silent
    mispairing. Holding the hull instead means :meth:`holds` answers for whatever patch
    set it is handed.

    Stored as **support distances along fixed directions**, which is an *outer* bound: a
    colour just past the hull may pass. That is the safe direction deliberately — too
    generous a gamut can only inflate the reported error, never hide it.
    """

    #: the farthest any measured colour reached along each of :func:`_directions`
    support: NDArray[np.float64] = field(
        default_factory=lambda: np.zeros(0, np.float64)
    )
    #: how many measured colours it was built from
    patches: int = 0

    @property
    def measured(self) -> bool:
        return self.patches > 0

    def holds(self, wanted: Patches) -> NDArray[np.bool_]:
        """Which of ``wanted`` this medium demonstrably reaches."""
        want = np.asarray(wanted, np.float32).reshape(-1, 3)
        if not self.measured:  # nothing measured means nothing ruled out
            return np.ones(len(want), dtype=np.bool_)
        inside = (colour.to_lab(want) @ _directions().T) <= self.support + 1e-9
        return np.asarray(inside.all(axis=1), dtype=np.bool_)

    @classmethod
    def of(cls, scanned: Patches) -> Gamut:
        seen = np.asarray(scanned, np.float32).reshape(-1, 3)
        if not len(seen):
            return cls()
        hull = colour.to_lab(seen)
        return cls(support=(hull @ _directions().T).max(axis=0), patches=len(seen))


def reachable(scanned: Patches, wanted: Patches | None = None) -> NDArray[np.bool_]:
    """Which target colours this medium demonstrably reaches, **measured from scans**.

    A colour is reachable when it lies inside the solid the medium has been *seen* to
    produce: the convex hull, in Lab, of every patch that came back. So it depends on
    the measurements and on nothing else.

    It used to be decided by inverting the fitted correction — asking what send each
    target would need and calling it reachable if that landed in 0..255 — and that is
    circular, because the mask came from the same fit it was then used to score.
    Measured: refitting over 20 patches instead of 80 moved the reported gamut from
    **37 to 49 patches on identical scan data**, so no two residuals were comparable.

    It has to be a solid and not a per-channel range, because a gamut *is* a solid: a
    saturated blue at mid-lightness can sit inside the box on all three channels and
    still need more cyan than exists. A real matte profile reported 17.7 mean error over
    "76 reachable" patches while the 67 it could truly hit sat at 12.7.

    The hull is tested by support functions along :data:`_HULL_DIRECTIONS` fixed
    normals, which bounds it from *outside* — a colour just past the hull may pass. That
    is the safe direction deliberately: too generous a gamut can only inflate the
    reported error, never hide it.

    Nothing measured means nothing ruled out.
    """
    return Gamut.of(scanned).holds(target() if wanted is None else wanted)


def score(
    scanned: Patches,
    reach: NDArray[np.bool_],
    wanted: Patches | None = None,
    white: Patches | None = None,
    spec: Chart | None = None,
) -> Error:
    """ΔE00 from the target over ``reach``, plus the cast off the neutrals.

    The mask is an argument because *whose* gamut it is matters. A medium has one
    gamut, so every round of a calibration is scored against the same one (see
    :meth:`proxdex.profiles.Profile.gamut`); scoring each round against its own
    scan compares means over different patch sets, and the trend then moves when
    the set moves rather than when the print improves.

    ``white`` is the substrate, and passing it is what makes the answer a statement
    about the *print* rather than about the paper. A blue holographic sticker judged
    against an absolute neutral reports a large cast that no ink can remove; judged
    against itself it reports the calibration.
    """
    card = spec or chart()
    goal = card.target if wanted is None else wanted
    seen = np.asarray(scanned, np.float32)
    if white is not None:
        seen = colour.relative_to(seen, np.asarray(white, np.float32))
    d = colour.delta_e00(
        colour.to_lab(seen), colour.to_lab(np.asarray(goal, np.float32))
    )
    inside = d[reach]
    if not inside.size:  # nothing reachable — report the whole thing rather than
        inside = d  # claim a clean sheet on no evidence
        reach = np.ones_like(reach)
    # the cast is measured over the neutrals this medium can **reach**, for exactly the
    # reason the error is. The neutral ramp is spaced in L* from 2 to 98 on purpose, so
    # on any real stock its ends are outside the printable range: those patches come
    # back at the ink floor or the paper, carrying whatever hue *those* have, and
    # averaging them in reports a cast the print does not have where anyone can see it.
    # Measured on the simulated blue sticker: over every neutral it read a* +5.10 (red)
    # while the printable ones read a* +0.86 (neutral) — the number that would have been
    # on screen contradicted the print in hand.
    greys = card.neutrals
    printable = greys[reach[greys]] if len(greys) else greys
    return Error(
        de00_mean=float(inside.mean()),
        de00_max=float(inside.max()),
        cast=Cast.of(colour.to_lab(seen[printable if len(printable) else greys])),
        measured=int(reach.sum()),
        clipped=int((~reach).sum()),
    )


def _triple(value: object, default: float) -> tuple[float, float, float]:
    """Three finite numbers out of untrusted JSON, or the default for each."""
    raw: list[object] = value if isinstance(value, list) else []
    out = [
        _float(raw[i]) if i < len(raw) and isinstance(raw[i], (int, float)) else default
        for i in range(3)
    ]
    return (out[0], out[1], out[2])


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    out = float(value)
    return 0.0 if not np.isfinite(out) else out


# ------------------------------------------------------------ chart render ----
def sent_patches(correction: Sender | None, goal: Patches | None = None) -> Patches:
    """What this round actually puts on paper: the aim, through what we know.

    ``goal`` is the *aim* rather than the bare target, so a profile printing relative to
    its own substrate asks the paper for what it can give. Passing nothing keeps
    the absolute target, which is what round one has to print — there is no substrate
    reading yet, and getting one is what round one is for.
    """
    want = target() if goal is None else np.asarray(goal, np.float32)
    return want if correction is None else correction.send(want)


def canvas_for(spec: Chart) -> tuple[int, int]:
    """The pixel shape this chart wants, so its patches come out **square**.

    It was two constants, which is right for exactly one patch grid. The verification
    chart is 9x9 and the full survey is 18x26, so one fixed 1200x1350 canvas drew the
    survey's patches half as tall as they are wide *and* letterboxed the art inside its
    box on the page — and the reader crops by where the art landed, so a shape the
    writer and the reader disagree about is how a scan of the gutters passes for a
    reading of the paper.
    """
    x0, y0, x1, y1 = _GRID
    if not spec.cols or not spec.rows:  # pragma: no cover — an empty chart
        return CANVAS_W, CANVAS_H
    cell = CANVAS_W * (x1 - x0) / spec.cols
    return CANVAS_W, max(1, round(cell * spec.rows / (y1 - y0)))


def render_chart(
    correction: Sender | None = None,
    label: str = "",
    size: tuple[int, int] | None = None,
    goal: Patches | None = None,
    spec: Chart | None = None,
) -> Image.Image:
    """The chart itself. ``label`` is printed above the patches.

    ``size`` is in pixels: the chart is *drawn* at whatever size it will print
    at, never drawn small and scaled up, so every patch stays exactly the colour
    it is meant to be and no resampler invents one in between.
    """
    spec = spec or chart()
    size = size or canvas_for(spec)
    width, height = size
    im = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(im)
    fid = int(_FID_SIZE * width)
    for fx, fy in FIDUCIALS:
        cx, cy = fx * width, fy * height
        draw.rectangle(
            [cx - fid / 2, cy - fid / 2, cx + fid / 2, cy + fid / 2], fill=(0, 0, 0)
        )
    if label:
        draw.text(
            (_GRID[0] * width, _LABEL_Y * height),
            label,
            fill=(0, 0, 0),
            font=_font(width),
            anchor="ls",
        )
    x0, y0, x1, y1 = _GRID
    cw = (x1 - x0) / spec.cols * width
    ch = (y1 - y0) / spec.rows * height
    pad = min(cw, ch) * spec.pad
    want = spec.target if goal is None else goal
    for i, color in enumerate(sent_patches(correction, want).round().astype(int)):
        col, row = i % spec.cols, i // spec.cols
        px = (x0 + col / spec.cols * (x1 - x0)) * width
        py = (y0 + row / spec.rows * (y1 - y0)) * height
        fill = (int(color[0]), int(color[1]), int(color[2]))
        draw.rectangle([px + pad, py + pad, px + cw - pad, py + ch - pad], fill=fill)
    return im


#: label height as a share of the chart width, so it scales with the print
_LABEL_SCALE = 0.028


def _font(width: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A label font sized for this chart, falling back on old Pillow."""
    try:
        return ImageFont.load_default(size=round(_LABEL_SCALE * width))
    except (TypeError, AttributeError):  # pragma: no cover — Pillow < 10.1
        return ImageFont.load_default()


def fit_size(box: tuple[int, int], spec: Chart | None = None) -> tuple[int, int]:
    """The largest chart of the right shape that fits a box, in pixels."""
    canvas_w, canvas_h = canvas_for(spec or chart())
    box_w, box_h = box
    scale = min(box_w / canvas_w, box_h / canvas_h)
    return max(1, round(canvas_w * scale)), max(1, round(canvas_h * scale))


def chart_page(
    cfg: Config,
    correction: Sender | None = None,
    *,
    slot: Slot | None = None,
    grid: tuple[int, int] = GRID,
    label: str = "",
    goal: Patches | None = None,
) -> Image.Image:
    """A full print page with the chart in ``slot`` and every other slot blank.

    That is the whole trick behind iterating on one sheet: print this, then feed
    the same paper back in and print the next round into the next slot.
    """
    from proxdex.sheet import blank_page

    page = blank_page(cfg)
    pw, ph = page.size
    x0, y0, x1, y1 = _cell(cfg, slot or FIRST_SLOT, grid)
    inset_x = (x1 - x0) * _CELL_INSET * pw
    inset_y = (y1 - y0) * _CELL_INSET * ph
    box_w = round((x1 - x0) * pw - 2 * inset_x)
    box_h = round((y1 - y0) * ph - 2 * inset_y)
    art = render_chart(correction, label, fit_size((box_w, box_h)), goal)
    page.paste(
        art,
        (
            round(x0 * pw + inset_x + (box_w - art.width) / 2),
            round(y0 * ph + inset_y + (box_h - art.height) / 2),
        ),
    )
    return page


# ------------------------------------------------------------ extraction ------
def detect_fiducials(arr: RGB) -> list[tuple[float, float]]:
    """Locate the four corner fiducials (dark blob centroid per corner)."""
    h, w, _ = arr.shape
    lum = arr @ np.array([0.299, 0.587, 0.114], np.float32)
    win = 0.18
    points: list[tuple[float, float]] = []
    for fx, fy in FIDUCIALS:
        xs = slice(0, int(w * win)) if fx < 0.5 else slice(int(w * (1 - win)), w)
        ys = slice(0, int(h * win)) if fy < 0.5 else slice(int(h * (1 - win)), h)
        region = lum[ys, xs]
        dark = region < 70
        if dark.sum() < 20:
            raise ProxdexError(
                "couldn't find a corner fiducial. Scan the whole page with "
                "auto-correction off and pass the slot you printed in, or crop to "
                "just the one chart and pass --whole."
            )
        yy, xx = np.nonzero(dark)
        points.append((float(xx.mean() + xs.start), float(yy.mean() + ys.start)))
    return points


def locate(arr: RGB) -> NDArray[np.float32]:
    """Find the chart in a scan: the map from chart coordinates to scan pixels.

    Public because reading a scan is two steps — locate the chart, then sample it with
    the patch set you actually printed — and a caller that has to reach for a private
    helper to do the second step is a caller who will skip it.
    """
    return _affine(detect_fiducials(arr))


def _affine(dst: list[tuple[float, float]]) -> NDArray[np.float32]:
    """Map chart-normalized (fx, fy) -> scan (x, y) from the four fiducials."""
    src = np.array([[fx, fy, 1.0] for fx, fy in FIDUCIALS], np.float32)
    out = np.array(dst, np.float32)
    params, *_ = np.linalg.lstsq(src, out, rcond=None)  # (3, 2)
    return params.astype(np.float32)


def sample_patches(
    arr: RGB, params: NDArray[np.float32], spec: Chart | None = None
) -> Patches:
    """Read every patch centre out of a located chart.

    ``spec`` is required in spirit and defaulted only for the verification chart: a
    reader that assumes one patch set will happily sample a 9x9 grid over an 18x26
    survey and return the gutters, which is white paper at every position — a perfectly
    plausible answer that is wrong about everything.
    """
    spec = spec or chart()
    h, w, _ = arr.shape
    measured = np.zeros((len(spec), 3), np.float32)
    # the sampled window scales with the patch, so a denser chart reads its own
    # centres rather than a fixed box that would spill into the gutter
    r = max(3, int(0.6 * min(h / spec.rows, w / spec.cols) * (0.5 - spec.pad)))
    for i, (cx, cy) in enumerate(spec.centers()):
        x, y = np.array([cx, cy, 1.0], np.float32) @ params
        xi, yi = round(float(x)), round(float(y))
        patch = arr[max(0, yi - r) : yi + r, max(0, xi - r) : xi + r]
        measured[i] = np.median(patch.reshape(-1, 3), axis=0)
    return measured


def crop_region(
    arr: RGB, rect: tuple[float, float, float, float], *, what: str, pad: float = 0.0
) -> RGB:
    """The part of a whole-page scan inside ``rect``, given as page fractions."""
    h, w, _ = arr.shape
    x0, y0, x1, y1 = rect
    pad_x, pad_y = (x1 - x0) * pad, (y1 - y0) * pad
    left = max(0, round((x0 + pad_x) * w))
    top = max(0, round((y0 + pad_y) * h))
    right = min(w, round((x1 - pad_x) * w))
    bottom = min(h, round((y1 - pad_y) * h))
    if right - left < _MIN_CROP_PX or bottom - top < _MIN_CROP_PX:
        raise ProxdexError(f"{what} is not inside this scan")
    return arr[top:bottom, left:right]


def crop_slot(arr: RGB, cfg: Config, slot: Slot, grid: tuple[int, int] = GRID) -> RGB:
    """The part of a whole-page scan holding one slot's chart.

    The scan is assumed to be the page — which a flatbed gives you — and the
    crop is padded by less than the chart's own inset, so any slack lands on
    blank paper instead of in the next chart along.
    """
    return crop_region(
        arr, _cell(cfg, slot, grid), what=f"slot {slot.text}", pad=_CROP_PAD
    )


#: one proof patch: wide and shallow, so a pair reads as one swatch split in two
#: and 36 pairs fit a shape you can look at without scrolling
_PROOF_W, _PROOF_H = 120, 27
#: the gap between pairs — the seam inside a pair must be the tighter one, or the
#: eye compares the wrong two colours
_PROOF_GAP = 8


def proof_sheet(scanned: Patches, spec: Chart | None = None) -> Image.Image:
    """Target above, scanned below, patch by patch.

    A mean error says a print is off; this says *how* — whether the paper is
    running warm, crushing the darks or losing the cyans — which is what decides
    whether another round is worth the paper. The two halves of a pair touch, so
    any difference between them shows as a visible seam.
    """
    spec = spec or chart()
    goal = spec.target
    pair_h = _PROOF_H * 2 + _PROOF_GAP
    im = Image.new(
        "RGB",
        (spec.cols * (_PROOF_W + _PROOF_GAP), spec.rows * pair_h),
        (255, 255, 255),
    )
    draw = ImageDraw.Draw(im)
    for i in range(min(len(goal), len(scanned))):
        col, row = i % spec.cols, i // spec.cols
        x = col * (_PROOF_W + _PROOF_GAP)
        y = row * pair_h
        for offset, patch in ((0, goal[i]), (_PROOF_H, scanned[i])):
            fill = tuple(int(v) for v in patch.clip(0, 255).round())
            draw.rectangle(
                [x, y + offset, x + _PROOF_W - 1, y + offset + _PROOF_H - 1], fill=fill
            )
    return im


def read_scan(
    path: Path,
    cfg: Config | None = None,
    *,
    slot: Slot | None = None,
    grid: tuple[int, int] = GRID,
    spec: Chart | None = None,
    whole: bool = False,
) -> Patches:
    """Read every patch of one chart out of a scan.

    Where the chart sits on the page follows from **which chart it is**: a verification
    chart is in one ``slot`` of the sheet grid, a survey is the rectangle
    :func:`survey_rect` put it in. ``whole`` says the image is already cropped to the
    chart and nothing should be looked for.
    """
    arr = np.asarray(Image.open(path).convert("RGB"), np.float32)
    card = spec or chart()
    size = card.id.size
    if whole or cfg is None:
        pass
    elif size is not None:
        arr = crop_region(arr, survey_rect(cfg, size), what=card.id.label)
    elif slot is not None:
        arr = crop_slot(arr, cfg, slot, grid)
    return sample_patches(arr, _affine(detect_fiducials(arr)), card)


def survey_rect(cfg: Config, size: SurveySize) -> tuple[float, float, float, float]:
    """Where a survey's art really lands on the page, as page fractions.

    **One answer for the writer and the reader**, which is the same argument
    :func:`proxdex.imports.plan` and :func:`proxdex.sheet.plan` rest on. The art keeps
    its own aspect inside the box ``size`` asks for, so on a density whose grid is not
    the box's shape it is letterboxed — and a reader that cropped the *box* instead
    would hand :func:`detect_fiducials` a band of blank paper to hunt corners in.
    """
    frac_w, frac_h = size.page_fraction
    margin_x = cfg.sheet_margin_mm / page_mm_width(cfg)
    margin_y = cfg.sheet_margin_mm / page_mm_height(cfg)
    box_w = (1.0 - 2 * margin_x) * frac_w
    box_h = (1.0 - 2 * margin_y) * frac_h
    # the chart's aspect is in pixels of a square-patch canvas, and the page's own
    # aspect turns that into a share of the page
    canvas_w, canvas_h = canvas_for(survey(size))
    page_w, page_h = page_mm_width(cfg), page_mm_height(cfg)
    want = (canvas_w / canvas_h) * (page_h / page_w)
    art_w, art_h = box_w, box_w / want
    if art_h > box_h:
        art_w, art_h = box_h * want, box_h
    x0 = margin_x + (box_w - art_w) / 2
    y0 = margin_y + (box_h - art_h) / 2
    return x0, y0, x0 + art_w, y0 + art_h


def survey_page(
    cfg: Config,
    size: SurveySize = SurveySize.FULL,
    correction: Sender | None = None,
    *,
    label: str = "",
    goal: Patches | None = None,
) -> Image.Image:
    """The characterization target on its own page — printed once, per medium.

    It gets the sheet (or the fraction of it ``size`` asks for) because this is the
    measurement everything else rests on. The verification chart in
    :func:`chart_page` is the small one that goes six to a sheet afterwards.
    """
    from proxdex.sheet import blank_page

    page = blank_page(cfg)
    pw, ph = page.size
    x0, y0, x1, y1 = survey_rect(cfg, size)
    spec = survey(size)
    box = (round((x1 - x0) * pw), round((y1 - y0) * ph))
    art = render_chart(correction, label, fit_size(box, spec), goal, spec)
    page.paste(art, (round(x0 * pw), round(y0 * ph)))
    return page


def page_mm_width(cfg: Config) -> float:
    from proxdex.sheet import page_mm

    return page_mm(cfg)[0]


def page_mm_height(cfg: Config) -> float:
    from proxdex.sheet import page_mm

    return page_mm(cfg)[1]
