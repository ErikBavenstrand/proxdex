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
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

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
CANVAS_W, CANVAS_H = 1200, 1350
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


@dataclass(frozen=True, slots=True)
class Chart:
    """The patch target: what it prints, and how it is laid out."""

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


def survey(size: SurveySize = SurveySize.FULL) -> Chart:
    """The characterization target: every role, at this density.

    Laid out by :func:`_lay_out`, which weaves the substrate patches through it.
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

    return _lay_out(content, cols=cols, rows=rows, pad=0.16)


def _lay_out(content: list[Patch], *, cols: int, rows: int, pad: float) -> Chart:
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
    return Chart(cols=cols, rows=rows, patches=tuple(fixed), pad=pad)


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
    return _lay_out(content, cols=9, rows=9, pad=0.16)


#: the chart a round prints by default — the verification one, since the survey is
#: printed once and asked for by name
CHART = verification()


def chart() -> Chart:
    return CHART


def chart_patches() -> tuple[Patch, ...]:
    return CHART.patches


def target() -> Patches:
    """The chart's patches as a float array — what a true print would scan as."""
    return CHART.target


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


def _cell(cfg: Config, slot: Slot, grid: tuple[int, int]) -> tuple[float, ...]:
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


@dataclass(frozen=True, slots=True)
class Correction:
    """What to send the printer so the paper comes back the colour you asked for."""

    coef: Coef  # (10, 3): send = features(wanted) . coef

    def apply(self, arr: RGB) -> RGB:
        return (_features(arr) @ self.coef).clip(0, 255).astype(np.float32)

    def raw(self, arr: RGB) -> RGB:
        """The send this correction asks for, **before** anything is clamped."""
        return (_features(arr) @ self.coef).astype(np.float32)

    def send(self, arr: RGB) -> RGB:
        """The value to send, with the unreachable compressed rather than clamped.

        A per-channel clamp is what put the yellow on the cards, and it is visible in
        one line of the real profile: a 200-grey wanted send (273, 264, 299) and got
        (255, 255, 255) — 44 of blue refused against 18 of red, so the hue moved. Per
        channel, clipping "favors preserving high saturation" and wrecks lightness; the
        CIE's own anchors for doing it properly (HPMINDE, SGCK) both hold **hue**
        constant and give up chroma.

        So a colour whose send does not fit is pulled toward the neutral axis of its own
        lightness, at constant hue, by the least amount that makes it fit — found by
        bisection because the response is a polynomial and there is no closed form.
        Anything still out of range after all its chroma is gone is a *lightness* the
        medium does not have, and only then is it clamped.
        """
        want = np.asarray(arr, np.float32)
        flat = want.reshape(-1, 3)
        out = self.raw(flat)
        bad = ((out < 0.0) | (out > _SEND_MAX)).any(axis=-1)
        if not bad.any():
            return out.clip(0, 255).reshape(want.shape).astype(np.float32)

        lab = colour.to_lab(flat[bad])
        lab = self._give_up_chroma(lab)
        lab = self._give_up_lightness(lab)
        out[bad] = self.raw(colour.from_lab(lab))
        return out.clip(0, 255).reshape(want.shape).astype(np.float32)

    def _fits(self, lab: colour.Lab) -> NDArray[np.bool_]:
        asked = self.raw(colour.from_lab(lab))
        return np.asarray(
            ((asked >= 0.0) & (asked <= _SEND_MAX)).all(axis=-1), dtype=np.bool_
        )

    def _give_up_chroma(self, lab: colour.Lab) -> colour.Lab:
        """Pull toward the neutral axis of the same lightness, at constant hue."""
        lo = np.zeros(len(lab), np.float64)
        hi = np.ones(len(lab), np.float64)
        for _ in range(_COMPRESS_STEPS):
            mid = (lo + hi) / 2.0
            trial = lab.copy()
            trial[:, 1:] *= (1.0 - mid)[:, None]
            fits = self._fits(trial)
            hi = np.where(fits, mid, hi)
            lo = np.where(fits, lo, mid)
        out = lab.copy()
        out[:, 1:] *= (1.0 - hi)[:, None]
        return out

    def _give_up_lightness(self, lab: colour.Lab) -> colour.Lab:
        """For what still does not fit, move along L\\* — never per channel.

        This half was missing from the first version, and its absence left the real
        measured failure unfixed. The real case is a **neutral**: a 200-grey wanted send
        (273, 264, 299) and got (255, 255, 255), 44 of blue refused against 18 of red. A
        neutral has no chroma to give up, so chroma compression does nothing at all for
        it and the per-channel clamp — the thing that moves the hue — happens anyway.

        What a neutral out of range really means is a *lightness* this medium does not
        have, so the answer is to move along its own axis until it does. The send is
        monotone in L\\* along the neutral axis, so which way to go is decided by which
        end the send overflowed.
        """
        stuck = ~self._fits(lab)
        if not stuck.any():
            return lab
        asked = self.raw(colour.from_lab(lab))
        # too much ink asked for → go lighter; too little → go darker
        toward = np.where(asked.max(axis=-1) > _SEND_MAX, 0.0, 100.0)
        toward = np.where(asked.min(axis=-1) < 0.0, 100.0, toward)
        lo = np.zeros(len(lab), np.float64)
        hi = np.ones(len(lab), np.float64)
        for _ in range(_COMPRESS_STEPS):
            mid = (lo + hi) / 2.0
            trial = lab.copy()
            trial[:, 0] = lab[:, 0] * (1.0 - mid) + toward * mid
            fits = self._fits(trial)
            hi = np.where(fits, mid, hi)
            lo = np.where(fits, lo, mid)
        out = lab.copy()
        moved = lab[:, 0] * (1.0 - hi) + toward * hi
        out[:, 0] = np.where(stuck, moved, lab[:, 0])
        return out

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
    goal = target() if wanted is None else wanted
    seen = np.asarray(scanned, np.float32).reshape(-1, 3)
    if not len(seen):
        return np.ones(len(goal), dtype=np.bool_)
    hull = colour.to_lab(seen)
    want = colour.to_lab(np.asarray(goal, np.float32))
    dirs = _directions()
    support = (hull @ dirs.T).max(axis=0)
    inside = (want @ dirs.T) <= support + 1e-9
    return np.asarray(inside.all(axis=1), dtype=np.bool_)


def score(
    scanned: Patches,
    reach: NDArray[np.bool_],
    wanted: Patches | None = None,
    white: Patches | None = None,
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
    goal = target() if wanted is None else wanted
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
    return Error(
        de00_mean=float(inside.mean()),
        de00_max=float(inside.max()),
        cast=Cast.of(colour.to_lab(seen[chart().neutrals])),
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
def sent_patches(correction: Correction | None, goal: Patches | None = None) -> Patches:
    """What this round actually puts on paper: the aim, through what we know.

    ``goal`` is the *aim* rather than the bare target, so a profile printing relative to
    its own substrate asks the paper for what it can give. Passing nothing keeps
    the absolute target, which is what round one has to print — there is no substrate
    reading yet, and getting one is what round one is for.
    """
    want = target() if goal is None else np.asarray(goal, np.float32)
    return want if correction is None else correction.send(want)


def render_chart(
    correction: Correction | None = None,
    label: str = "",
    size: tuple[int, int] = (CANVAS_W, CANVAS_H),
    goal: Patches | None = None,
    spec: Chart | None = None,
) -> Image.Image:
    """The chart itself. ``label`` is printed above the patches.

    ``size`` is in pixels: the chart is *drawn* at whatever size it will print
    at, never drawn small and scaled up, so every patch stays exactly the colour
    it is meant to be and no resampler invents one in between.
    """
    spec = spec or CHART
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


def fit_size(box: tuple[int, int]) -> tuple[int, int]:
    """The largest chart of the right shape that fits a box, in pixels."""
    box_w, box_h = box
    scale = min(box_w / CANVAS_W, box_h / CANVAS_H)
    return max(1, round(CANVAS_W * scale)), max(1, round(CANVAS_H * scale))


def chart_page(
    cfg: Config,
    correction: Correction | None = None,
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
    spec = spec or CHART
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


def crop_slot(arr: RGB, cfg: Config, slot: Slot, grid: tuple[int, int] = GRID) -> RGB:
    """The part of a whole-page scan holding one slot's chart.

    The scan is assumed to be the page — which a flatbed gives you — and the
    crop is padded by less than the chart's own inset, so any slack lands on
    blank paper instead of in the next chart along.
    """
    h, w, _ = arr.shape
    x0, y0, x1, y1 = _cell(cfg, slot, grid)
    pad_x = (x1 - x0) * _CROP_PAD
    pad_y = (y1 - y0) * _CROP_PAD
    left = max(0, round((x0 + pad_x) * w))
    top = max(0, round((y0 + pad_y) * h))
    right = min(w, round((x1 - pad_x) * w))
    bottom = min(h, round((y1 - pad_y) * h))
    if right - left < _MIN_CROP_PX or bottom - top < _MIN_CROP_PX:
        raise ProxdexError(f"slot {slot.text} is not inside this scan")
    return arr[top:bottom, left:right]


#: one proof patch: wide and shallow, so a pair reads as one swatch split in two
#: and 36 pairs fit a shape you can look at without scrolling
_PROOF_W, _PROOF_H = 120, 27
#: the gap between pairs — the seam inside a pair must be the tighter one, or the
#: eye compares the wrong two colours
_PROOF_GAP = 8


def proof_sheet(scanned: Patches) -> Image.Image:
    """Target above, scanned below, patch by patch.

    A mean error says a print is off; this says *how* — whether the paper is
    running warm, crushing the darks or losing the cyans — which is what decides
    whether another round is worth the paper. The two halves of a pair touch, so
    any difference between them shows as a visible seam.
    """
    spec = CHART
    goal = spec.target
    pair_h = _PROOF_H * 2 + _PROOF_GAP
    im = Image.new(
        "RGB",
        (spec.cols * (_PROOF_W + _PROOF_GAP), spec.rows * pair_h),
        (255, 255, 255),
    )
    draw = ImageDraw.Draw(im)
    for i in range(len(goal)):
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
) -> Patches:
    """Read every patch of one chart out of a scan.

    With a ``slot`` (and the config the page was rendered with) the whole page is
    cropped to that slot first; without one the image is taken to be a single
    chart.
    """
    arr = np.asarray(Image.open(path).convert("RGB"), np.float32)
    if slot is not None and cfg is not None:
        arr = crop_slot(arr, cfg, slot, grid)
    return sample_patches(arr, _affine(detect_fiducials(arr)), spec)


def survey_page(
    cfg: Config,
    size: SurveySize = SurveySize.FULL,
    correction: Correction | None = None,
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
    frac_w, frac_h = size.page_fraction
    margin_x = cfg.sheet_margin_mm / page_mm_width(cfg)
    margin_y = cfg.sheet_margin_mm / page_mm_height(cfg)
    box_w = round((1.0 - 2 * margin_x) * frac_w * pw)
    box_h = round((1.0 - 2 * margin_y) * frac_h * ph)
    spec = survey(size)
    art = render_chart(correction, label, fit_size((box_w, box_h)), goal, spec)
    page.paste(
        art,
        (
            round(margin_x * pw + (box_w - art.width) / 2),
            round(margin_y * ph + (box_h - art.height) / 2),
        ),
    )
    return page


def page_mm_width(cfg: Config) -> float:
    from proxdex.sheet import page_mm

    return page_mm(cfg)[0]


def page_mm_height(cfg: Config) -> float:
    from proxdex.sheet import page_mm

    return page_mm(cfg)[1]
