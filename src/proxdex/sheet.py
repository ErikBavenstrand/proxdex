"""Impose card cells onto print pages and export a PDF.

proxdex owns the whole path to paper. The caller passes cells — each a
trim-size card with cut bleed already added around it, carrying the physical size
it prints at — which are placed on the page with the cut guides at the trim edge.
Supports fronts-only, backs-only, or duplex (back pages mirrored for the
print-flip edge, nudged by a back offset to line up with the fronts). Because
proxdex renders the PDF itself, the print path is fully determined, which is what
lets colour calibration transfer.

**Every card prints at its own size.** Almost all of them are the one configured
trim (``[card] w_mm/h_mm``) and share one grid, but an oversized card is 89×127mm
and would be a small, wrong card in a 63×88 cell. So cells are grouped by trim
size and each group gets its own pages, with its own grid — the configured
``cols``/``rows`` at the configured size, and as many as the page holds at any
other. A card's size is never silently changed to fit the sheet.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterator, Sequence
from dataclasses import astuple, dataclass
from pathlib import Path
from typing import Any, cast

import img2pdf
from PIL import Image, ImageDraw

from proxdex import games, progress, scratch, steps
from proxdex.config import (
    Config,
    DuplexFlip,
    Faces,
    Fit,
    GuidePlacement,
    GuideReach,
    GuideStyle,
    Orientation,
    PageSize,
    RegMarks,
)
from proxdex.library import FRONT, Card, Stage

# our high-DPI pages are large by design; we generate them, so lift PIL's guard
Image.MAX_IMAGE_PIXELS = None

PAGES: dict[PageSize, tuple[float, float]] = {  # portrait, mm
    PageSize.A4: (210.0, 297.0),
    PageSize.LETTER: (215.9, 279.4),
}


def _ppm(cfg: Config) -> float:
    return cfg.sheet_dpi / 25.4


def _hex(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _page_size_px(cfg: Config) -> tuple[int, int]:
    w_mm, h_mm = page_mm(cfg)
    ppm = _ppm(cfg)
    return round(w_mm * ppm), round(h_mm * ppm)


def blank_page(cfg: Config) -> Image.Image:
    """An empty print page at the configured size and resolution.

    Public because the calibration chart is printed through the same renderer as
    a card sheet — measuring a correction on a different path to paper than it is
    applied on would measure the wrong thing.
    """
    return Image.new("RGB", _page_size_px(cfg), (255, 255, 255))


#: a physical card size in mm — what a card actually prints at
Trim = tuple[float, float]


@dataclass(frozen=True, slots=True)
class Cell:
    """One card ready to place: its pixels, and the size it prints at.

    Front and back travel together rather than as two parallel lists, because a
    back belongs behind exactly one front and at exactly its size — keeping them
    in one object is what makes that impossible to get wrong.
    """

    front: Image.Image
    back: Image.Image | None
    trim: Trim


@dataclass(frozen=True, slots=True)
class Geo:
    """Resolved page/grid geometry in pixels (bleed/ppm in px and px/mm)."""

    ppm: float
    cell_w: int
    cell_h: int
    cols: int
    rows: int
    gap_x: int
    gap_y: int
    bleed: float
    page_w: int
    page_h: int
    x_off: int
    y_off: int

    @property
    def per_page(self) -> int:
        return self.cols * self.rows

    def cell_xy(self, col: int, row: int) -> tuple[int, int]:
        """The top-left of one cell, **before** the ink offset — which is added by
        whoever draws, because it is a property of the side and not of the grid."""
        return (
            self.x_off + col * (self.cell_w + self.gap_x),
            self.y_off + row * (self.cell_h + self.gap_y),
        )


def page_mm(cfg: Config) -> Trim:
    w_mm, h_mm = PAGES[cfg.sheet_page]
    if cfg.sheet_orientation is Orientation.LANDSCAPE:
        w_mm, h_mm = h_mm, w_mm
    return w_mm, h_mm


@dataclass(frozen=True, slots=True)
class Margins:
    """The unprintable border to keep clear, per edge, in mm.

    Per edge because a printer's is: 4mm at the sides and 5mm at the top is an ordinary
    inkjet, and many are worse at the bottom, where the paper is still gripped. One
    number cannot describe that, so ``[sheet] margin_mm`` is the default and each edge
    may override it.
    """

    top: float
    right: float
    bottom: float
    left: float

    @property
    def uniform(self) -> bool:
        return self.top == self.right == self.bottom == self.left

    def __str__(self) -> str:
        if self.uniform:
            return f"{self.top:g}mm"
        return f"{self.top:g}/{self.right:g}/{self.bottom:g}/{self.left:g}mm (t/r/b/l)"


def margins(cfg: Config) -> Margins:
    """This library's margins, per edge, with each unset edge taking the page margin."""
    fallback = cfg.sheet_margin_mm
    return Margins(
        top=fallback if cfg.sheet_margin_top_mm is None else cfg.sheet_margin_top_mm,
        right=(
            fallback if cfg.sheet_margin_right_mm is None else cfg.sheet_margin_right_mm
        ),
        bottom=(
            fallback
            if cfg.sheet_margin_bottom_mm is None
            else cfg.sheet_margin_bottom_mm
        ),
        left=fallback if cfg.sheet_margin_left_mm is None else cfg.sheet_margin_left_mm,
    )


def safe_mm(cfg: Config) -> Trim:
    """The printable box: the paper less its margins. Never negative."""
    page_w, page_h = page_mm(cfg)
    m = margins(cfg)
    return (
        max(0.0, page_w - m.left - m.right),
        max(0.0, page_h - m.top - m.bottom),
    )


def cell_mm(cfg: Config, trim: Trim) -> Trim:
    """One cell: the card plus the cut bleed added round it at sheet time."""
    return (trim[0] + 2 * cfg.bleed_mm, trim[1] + 2 * cfg.bleed_mm)


def grid_mm(cfg: Config, trim: Trim, cols: int, rows: int) -> Trim:
    """What a ``cols``×``rows`` grid of this trim measures, gaps included."""
    cw, ch = cell_mm(cfg, trim)
    return (
        cols * cw + (cols - 1) * cfg.sheet_spacing_mm,
        rows * ch + (rows - 1) * cfg.sheet_spacing_y_mm,
    )


def holds(cfg: Config, trim: Trim) -> tuple[int, int]:
    """The largest grid of this trim that fits inside the printable box.

    ``(0, 0)`` when not even one cell does — which is a real answer, and the reason
    :func:`grid_for` clamps to 1 rather than taking this verbatim.
    """
    safe_w, safe_h = safe_mm(cfg)
    cw, ch = cell_mm(cfg, trim)
    cols = int((safe_w + cfg.sheet_spacing_mm + _EPS_MM) // (cw + cfg.sheet_spacing_mm))
    rows = int(
        (safe_h + cfg.sheet_spacing_y_mm + _EPS_MM) // (ch + cfg.sheet_spacing_y_mm)
    )
    return max(0, cols), max(0, rows)


def grid_for(cfg: Config, trim: Trim) -> tuple[int, int]:
    """Columns and rows for a trim size.

    The configured size keeps the configured grid — a library that prints normal
    cards sees exactly the layout it always did, **even when it does not fit**, which
    is `PaperFit`'s job to say rather than this function's to quietly correct. Any
    other size has no configured grid to keep, so it takes as many cells as the page
    actually holds.
    """
    if trim == (cfg.card_w_mm, cfg.card_h_mm):
        return cfg.sheet_cols, cfg.sheet_rows
    cols, rows = holds(cfg, trim)
    # one per page even if the card is larger than the paper: proxdex would rather
    # print an over-margin page than silently shrink a card
    return max(1, cols), max(1, rows)


#: slack when counting how many cells fit, so a card that fills the page exactly
#: is not excluded by float error
_EPS_MM = 1e-6


@dataclass(frozen=True, slots=True)
class PaperFit:
    """Whether the configured grid actually fits the paper it is being printed on.

    **Nothing used to check**, and both shipped defaults were wrong. The grid was placed
    at ``max(margin, centred)``, so when it was too wide to honour the margin the whole
    overflow landed on the right and bottom and was clipped by the page — silently, and
    invisibly, because what is clipped first is the bleed you were going to cut off.

    - A4 3×3 at 2.5mm bleed is 205.5mm wide on a 210mm sheet: **2.25mm per side.** With
      a 5mm margin forced, it ran 0.51mm off the right edge of every sheet.
    - Letter 3×3 is 281.7mm tall on a 279.4mm sheet, so it never fitted at all: the
      bottom row of **cards** — not bleed, cards — hung 4.8mm off the paper.

    So the margin is now a *constraint that is reported* rather than an offset that is
    forced, and the grid is centred in the printable box. Centring is never worse: where
    the grid fits, ``max(margin, centred)`` already chose centred, and where it does
    not, centring is symmetric and loses half as much off each edge rather than all of
    it off one. The A4 case stops overflowing entirely; the Letter one is reported.
    """

    #: the printable box, mm — paper less margins
    safe: Trim
    #: what the configured grid measures, mm
    grid: Trim
    #: how far it exceeds the box per axis, mm — 0 when it fits
    over_w: float
    over_h: float
    #: the largest grid that would fit
    cols: int
    rows: int
    #: cut bleed that would let the configured grid fit, mm — ``None`` if no bleed would
    bleed_fix: float | None

    @property
    def ok(self) -> bool:
        return self.over_w <= _EPS_MM and self.over_h <= _EPS_MM

    @property
    def note(self) -> str:
        """Why it does not fit and what to change — the same sentence both surfaces
        print, because a page count is no use beside a row that will be cut off."""
        if self.ok:
            return ""
        parts = [
            f"{axis} by {over:.2f}mm"
            for axis, over in (("too wide", self.over_w), ("too tall", self.over_h))
            if over > _EPS_MM
        ]
        fits = (
            f"{self.cols}×{self.rows} fits"
            if self.cols and self.rows
            else "not even one card fits"
        )
        fix = (
            f", or keep the grid with bleed ≤ {self.bleed_fix:g}mm"
            if self.bleed_fix is not None
            else ""
        )
        return (
            f"the grid is {self.grid[0]:.1f}×{self.grid[1]:.1f}mm and the printable "
            f"box is {self.safe[0]:.1f}×{self.safe[1]:.1f}mm — {' and '.join(parts)}. "
            f"{fits}{fix}"
        )

    def json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "safe": list(self.safe),
            "grid": list(self.grid),
            "over_w": self.over_w,
            "over_h": self.over_h,
            "cols": self.cols,
            "rows": self.rows,
            "bleed_fix": self.bleed_fix,
            "note": self.note,
        }


def paper_fit(cfg: Config, trim: Trim) -> PaperFit:
    """Does this trim's configured grid fit inside the printable box?

    Public because the answer belongs on every surface that promises a page count: a
    plan that says "1 page" without saying "and the bottom row is off the paper" is
    the confident wrong number this project is careful about everywhere else.
    """
    cols, rows = grid_for(cfg, trim)
    safe_w, safe_h = safe_mm(cfg)
    gw, gh = grid_mm(cfg, trim, cols, rows)
    fit_cols, fit_rows = holds(cfg, trim)
    return PaperFit(
        safe=(safe_w, safe_h),
        grid=(gw, gh),
        over_w=max(0.0, gw - safe_w),
        over_h=max(0.0, gh - safe_h),
        cols=fit_cols,
        rows=fit_rows,
        bleed_fix=_bleed_that_fits(cfg, trim, cols, rows),
    )


def _bleed_that_fits(cfg: Config, trim: Trim, cols: int, rows: int) -> float | None:
    """The largest cut bleed at which the configured grid still fits, or ``None``.

    Worth offering because it is very often the answer: A4 holds 3 columns of a 63.5mm
    card at 1.9mm of bleed and not at 2.5mm, and losing 0.6mm of *waste* is a far
    smaller change than dropping to two columns.
    """
    safe_w, safe_h = safe_mm(cfg)
    room_w = safe_w - cols * trim[0] - (cols - 1) * cfg.sheet_spacing_mm
    room_h = safe_h - rows * trim[1] - (rows - 1) * cfg.sheet_spacing_y_mm
    best = min(room_w / (2 * cols), room_h / (2 * rows))
    if best < 0 or best >= cfg.bleed_mm:
        # negative = the cards alone do not fit, so no bleed would help; >= = bleed is
        # not what is wrong, and offering the configured number back reads as nonsense
        return None
    # rounded **down** to a hundredth, because a suggestion that does not itself fit is
    # worse than no suggestion
    return math.floor(best * 100) / 100


def master(card: Card, face: int = FRONT) -> Path | None:
    """The furthest-along image to print — the graded master, or the best earlier
    stage when a later step was skipped."""
    return card.best(*steps.BEST, face=face)


def print_ready(card: Card, face: int = FRONT) -> bool:
    """A side is ready to impose once grade is *settled* — done, or skipped so an
    earlier stage stands as the master."""
    settled = card.has(Stage.EDITED, face) or card.skipped(Stage.EDITED, face)
    return settled and master(card, face) is not None


def trim_mm(card: Card, cfg: Config) -> Trim:
    """The physical size this card prints at.

    Ordinary cards are the configured trim; an oversized card is its own real
    size, because a planar card imposed into a 63×88 cell is not that card — it is
    a small, wrong one. Nothing has to be configured for this: the size came from
    the provider at fetch time and lives in the card's own marker.
    """
    if card.oversized:
        return (games.OVERSIZED_W_MM, games.OVERSIZED_H_MM)
    return (cfg.card_w_mm, cfg.card_h_mm)


@dataclass(frozen=True, slots=True)
class Group:
    """One trim size's share of a print run: how many cards, and how many pages."""

    trim: Trim
    cards: int
    grid: tuple[int, int]
    pages: int

    def standard(self, cfg: Config) -> bool:
        return self.trim == (cfg.card_w_mm, cfg.card_h_mm)

    def name(self, cfg: Config) -> str:
        if self.standard(cfg):
            return "standard"
        return f"{self.trim[0]:g}×{self.trim[1]:g}mm"

    def fit(self, cfg: Config) -> PaperFit:
        return paper_fit(cfg, self.trim)

    def json(self, cfg: Config) -> dict[str, Any]:
        return {
            "trim": list(self.trim),
            "name": self.name(cfg),
            "standard": self.standard(cfg),
            "cards": self.cards,
            "grid": list(self.grid),
            "pages": self.pages,
            # per group, because each trim size has its own grid — an oversized card
            # can fit while the ordinary ones do not, and vice versa
            "fit": self.fit(cfg).json(),
        }


@dataclass(frozen=True, slots=True)
class Run:
    """A planned print run: what is in it, what is not, and what it costs in paper.

    Worked out before a single pixel is rendered, so `sheet --dry-run` and the
    UI's sheet builder can both promise the page count you actually get — they ask
    this, not their own arithmetic.
    """

    ready: tuple[Card, ...]
    copies: tuple[int, ...]
    missing: tuple[str, ...]
    groups: tuple[Group, ...]

    @property
    def cards(self) -> int:
        return sum(self.copies)

    @property
    def pages(self) -> int:
        return sum(g.pages for g in self.groups)

    @property
    def oversized(self) -> tuple[Card, ...]:
        return tuple(c for c in self.ready if c.oversized)

    @property
    def two_sided(self) -> tuple[Card, ...]:
        return tuple(c for c in self.ready if c.back_face is not None)

    def json(self, cfg: Config) -> dict[str, Any]:
        return {
            "cards": self.cards,
            "pages": self.pages,
            "ready": [c.id for c in self.ready],
            "copies": list(self.copies),
            "missing": list(self.missing),
            "groups": [g.json(cfg) for g in self.groups],
            "oversized": [c.id for c in self.oversized],
            "two_sided": [c.id for c in self.two_sided],
            "faces": cfg.sheet_faces.value,
            "dpi": cfg.sheet_dpi,
            # what will be *drawn on* the paper, resolved rather than restated: the
            # backs' settings each fall back to the fronts', and a reader working that
            # out for itself is a second implementation of the rule that can disagree
            # with the one the renderer uses. `None` = this side prints no guides.
            # the printable box every group's grid is checked against — reported once
            # for the run, since it is a fact about the paper rather than about a trim
            "margins": list(astuple(margins(cfg))),
            "safe": list(safe_mm(cfg)),
            "guides": front.json() if (front := guides_for(cfg, back=False)) else None,
            "back_guides": None
            if cfg.sheet_faces is Faces.FRONTS
            else (back.json() if (back := guides_for(cfg, back=True)) else None),
        }


def plan(cards: Sequence[tuple[Card, int]], cfg: Config) -> Run:
    """Group a run by trim size and count its pages, imposing nothing.

    ``cards`` is (card, copies) pairs. A card whose grade is not settled is left
    out and named in ``missing`` rather than silently dropped.
    """
    ready: list[Card] = []
    copies: list[int] = []
    missing: list[str] = []
    for card, count in cards:
        if print_ready(card, card.front_face):
            ready.append(card)
            copies.append(count)
        else:
            missing.append(card.id)
    per_trim: dict[Trim, int] = {}
    for card, count in zip(ready, copies, strict=True):
        trim = trim_mm(card, cfg)
        per_trim[trim] = per_trim.get(trim, 0) + count
    groups: list[Group] = []
    for trim, count in per_trim.items():
        grid = grid_for(cfg, trim)
        pages = pages_for(count, grid[0] * grid[1], cfg.sheet_faces)
        groups.append(Group(trim=trim, cards=count, grid=grid, pages=pages))
    return Run(
        ready=tuple(ready),
        copies=tuple(copies),
        missing=tuple(missing),
        groups=tuple(groups),
    )


def geometry(cfg: Config, trim: Trim) -> Geo:
    ppm = _ppm(cfg)
    cell_w = round((trim[0] + 2 * cfg.bleed_mm) * ppm)
    cell_h = round((trim[1] + 2 * cfg.bleed_mm) * ppm)
    cols, rows = grid_for(cfg, trim)
    gap_x = round(cfg.sheet_spacing_mm * ppm)
    gap_y = round(cfg.sheet_spacing_y_mm * ppm)
    page_w, page_h = _page_size_px(cfg)
    grid_w = cols * cell_w + (cols - 1) * gap_x
    grid_h = rows * cell_h + (rows - 1) * gap_y
    # **Centred in the printable box, never forced to the margin.** It used to be
    # `max(margin, centred)`, which meant the margin was a no-op wherever the grid
    # fitted — centred is already further in — and pushed the whole overflow onto the
    # right and bottom wherever it did not, where the page clipped it without a word.
    # See `PaperFit` for the two shipped defaults that were wrong because of it.
    m = margins(cfg)
    left, top = round(m.left * ppm), round(m.top * ppm)
    safe_w = page_w - left - round(m.right * ppm)
    safe_h = page_h - top - round(m.bottom * ppm)
    return Geo(
        ppm=ppm,
        cell_w=cell_w,
        cell_h=cell_h,
        cols=cols,
        rows=rows,
        gap_x=gap_x,
        gap_y=gap_y,
        bleed=cfg.bleed_mm * ppm,
        page_w=page_w,
        page_h=page_h,
        x_off=left + (safe_w - grid_w) // 2,
        y_off=top + (safe_h - grid_h) // 2,
    )


def fit(im: Image.Image, cw: int, ch: int, mode: Fit) -> Image.Image:
    """Scale any-size input to exactly the card cell (cw x ch).

    Guarantees the printed card is the configured physical size regardless of
    input resolution. ``cover`` fills the cell preserving aspect (center-crops
    the small overflow — matching-aspect cards lose nothing); ``contain`` fits
    the whole image with white padding; ``stretch`` forces the exact size.
    """
    im = im.convert("RGB")
    if mode is Fit.STRETCH:
        return im.resize((cw, ch))
    iw, ih = im.size
    cover = mode is Fit.COVER
    ratio = max(cw / iw, ch / ih) if cover else min(cw / iw, ch / ih)
    nw, nh = max(1, round(iw * ratio)), max(1, round(ih * ratio))
    scaled = im.resize((nw, nh))
    if cover:
        left, top = (nw - cw) // 2, (nh - ch) // 2
        return scaled.crop((left, top, left + cw, top + ch))
    canvas = Image.new("RGB", (cw, ch), (255, 255, 255))
    canvas.paste(scaled, ((cw - nw) // 2, (ch - nh) // 2))
    return canvas


def _grid_reorder(
    items: list[Image.Image | None], cfg: Config, g: Geo
) -> list[Image.Image | None]:
    """Mirror cells for the duplex flip so a back lands behind its front."""
    padded = list(items) + [None] * (g.per_page - len(items))
    rows = [padded[r * g.cols : (r + 1) * g.cols] for r in range(g.rows)]
    if cfg.sheet_duplex_flip is DuplexFlip.LONG:
        rows = [row[::-1] for row in rows]  # flip on long edge → mirror columns
    else:
        rows = rows[::-1]  # flip on short edge → mirror rows
    return [cell for row in rows for cell in row]


#: how each reach reads in the one line both surfaces print. Spelled out rather than
#: using the enum's own value, because "reaching the join" is not a sentence and the
#: readout is what somebody checks before spending a sheet of paper.
_REACH_SAID = {
    GuideReach.FIXED: "{mm}mm ticks",
    GuideReach.JOIN: "{mm}mm ticks, joined between cards",
    GuideReach.PAPER: "joined between cards and out to the paper",
}


@dataclass(frozen=True, slots=True)
class GuideSpec:
    """The cut guides for **one side** of the paper, resolved.

    A side is the unit because the two sides of a duplex sheet are asked different
    questions: the fronts carry the lines you cut by, and the backs — when they carry
    any — are there to be compared against the fronts through the paper, which wants
    its own colour and can want its own style. Resolving to one object is what keeps
    every drawing function from reading the config and deciding for itself which
    side's setting applies; :func:`guides_for` is the only place that rule lives.
    """

    style: GuideStyle
    reach: GuideReach
    placement: GuidePlacement
    #: crop-mark length, mm — the arm's reach where nothing longer is asked for
    length: float
    #: how far a mark runs past the trim edge onto the card, mm
    cross: float
    color: str
    #: line weight, mm
    width: float

    @property
    def summary(self) -> str:
        """One line, for the ``sheet`` readout and the builder's plan alike — so the
        two cannot describe the same run differently."""
        bits = [self.style.value]
        if self.style is GuideStyle.CORNERS:
            bits.append(_REACH_SAID[self.reach].format(mm=f"{self.length:g}"))
            bits.append(self.placement.value)
        if self.cross:
            bits.append(f"{self.cross:g}mm onto the card")
        return f"{', '.join(bits)} · {self.color}"

    def json(self) -> dict[str, Any]:
        return {
            "style": self.style.value,
            "reach": self.reach.value,
            "placement": self.placement.value,
            "length": self.length,
            "cross": self.cross,
            "color": self.color,
            "width": self.width,
            "summary": self.summary,
        }


def guides_for(cfg: Config, *, back: bool) -> GuideSpec | None:
    """This side's cut guides, or ``None`` when it prints none.

    The backs' settings are each an **optional** override of the fronts': unset means
    "the same as the fronts", the shape ``[print] back_profile`` already has, because
    one sheet of paper wants one set of guides until you say otherwise. Public because
    ``proxdex sheet``'s readout and the sheet builder's plan both report what a run
    will draw, and reporting it from a second reading of the config is how the report
    and the paper come to disagree.
    """
    if not cfg.sheet_guides:
        return None
    if not (cfg.sheet_guides_back if back else cfg.sheet_guides_front):
        return None
    spec = GuideSpec(
        style=cfg.sheet_guide_style,
        reach=cfg.sheet_guide_reach,
        placement=cfg.sheet_guide_placement,
        length=cfg.sheet_guide_mm,
        cross=cfg.sheet_guide_cross_mm,
        color=cfg.sheet_guide_color,
        width=cfg.sheet_guide_width_mm,
    )
    if back:
        spec = GuideSpec(
            style=cfg.sheet_back_guide_style or spec.style,
            reach=cfg.sheet_back_guide_reach or spec.reach,
            placement=cfg.sheet_back_guide_placement or spec.placement,
            length=spec.length
            if cfg.sheet_back_guide_mm is None
            else cfg.sheet_back_guide_mm,
            cross=spec.cross
            if cfg.sheet_back_guide_cross_mm is None
            else cfg.sheet_back_guide_cross_mm,
            color=cfg.sheet_back_guide_color or spec.color,
            width=spec.width
            if cfg.sheet_back_guide_width_mm is None
            else cfg.sheet_back_guide_width_mm,
        )
    return None if spec.style is GuideStyle.NONE else spec


#: one card's trim box on the page, in pixels — the four lines a blade follows
Box = tuple[float, float, float, float]


def _trim_box(g: Geo, col: int, row: int, ox: int, oy: int) -> Box:
    """Where cell (col, row)'s cut edges really land, ink offset included.

    Every guide is measured from here rather than from the grid, because the guide has
    to mark where the card **really** is. That was the bug: the cards were pasted at
    ``x + ox`` and the lines drawn at ``x``, so the moment you used a back offset to fix
    a misregistered duplex sheet you were cutting along lines that no longer described
    any card on it — and nothing on screen says so, because both are exactly where they
    were told to be.
    """
    x, y = g.cell_xy(col, row)
    return (
        x + ox + g.bleed,
        y + oy + g.bleed,
        x + ox + g.cell_w - g.bleed,
        y + oy + g.cell_h - g.bleed,
    )


def _blocker(filled: set[tuple[int, int]], col: int, row: int, dc: int, dr: int) -> int:
    """How many cells along ``(dc, dr)`` the nearest **occupied** cell is, or 0.

    An arm running outward along a trim line is running along the *edge* of whatever is
    next in that direction, so what it must stop at is that card — and an empty cell is
    not a card. This is why a partial last page does not get a full grid of marks: two
    cards on a nine-up sheet are marked as two cards, not as nine.
    """
    steps = max(_MAX_GRID_SPAN, 1)
    for k in range(1, steps + 1):
        if (col + dc * k, row + dr * k) in filled:
            return k
    return 0


#: how far to look for the next occupied cell — a page never has more cells than this
_MAX_GRID_SPAN = 64


def _arm_end(
    spec: GuideSpec,
    g: Geo,
    filled: set[tuple[int, int]],
    cell: tuple[int, int],
    step: tuple[int, int],
    corner: float,
    ox: int,
    oy: int,
) -> float:
    """Where one arm stops, which is the whole of what ``reach`` decides.

    ``corner`` is the trim coordinate the arm starts at and ``step`` the direction it
    runs, as a cell delta — ``(-1, 0)`` for an arm running left off the card's left
    edge, so the thing it can run into is the cell one to the left. Three answers:

    * ``FIXED`` — ``guide_mm`` and no further. What the corner ticks have always done.
    * ``JOIN`` — as far as the neighbouring card's near edge, so the gutter is bridged
      and two cards' marks make one line; ``guide_mm`` where there is no neighbour, so
      the outer margin stays clean.
    * ``PAPER`` — the same, but with no neighbour it runs to the edge of the paper,
      which is what a rotary trimmer needs (you line the blade up on the sheet edge).

    In every case it stops at the **neighbour**, never past it: a mark may cross the
    cut by ``cross`` and no more, so nothing is ever drawn along a card's own edge.
    """
    n = spec.length * g.ppm
    over = spec.cross * g.ppm
    col, row = cell
    dc, dr = step
    away = dc or dr  # +1 or -1 — which way along this axis the arm runs
    if spec.reach is not GuideReach.FIXED:
        k = _blocker(filled, col, row, dc, dr)
        if k:
            near, far = _span(g, (col + dc * k, row + dr * k), step, ox, oy)
            del far
            return near + away * over
        if spec.reach is GuideReach.PAPER:
            limit = (g.page_w if dc else g.page_h) if away > 0 else 0
            return float(limit)
    return corner + away * n


def _span(
    g: Geo, cell: tuple[int, int], step: tuple[int, int], ox: int, oy: int
) -> tuple[float, float]:
    """A cell's near and far trim edge along one axis, seen from the arm's direction."""
    x0, y0, x1, y1 = _trim_box(g, cell[0], cell[1], ox, oy)
    lo, hi = (x0, x1) if step[0] else (y0, y1)
    # the arm comes *towards* this cell, so its near edge is the one it meets first
    return (lo, hi) if (step[0] or step[1]) > 0 else (hi, lo)


def _mark_guides(
    draw: ImageDraw.ImageDraw,
    spec: GuideSpec,
    g: Geo,
    ox: int,
    oy: int,
    filled: set[tuple[int, int]],
) -> None:
    """Cut marks at the corners of every cell that holds a card.

    **Eight arms per card**, which is the shape jumpstart's original `cropMarks` had and
    the one this went back to: two at each corner, each running away from the card along
    a trim line. How far is `reach`'s answer alone (see :func:`_arm_end`), so "a tick",
    "a line joining its neighbour" and "a line to the paper's edge" are one drawing with
    one limit changed — not three implementations free to disagree about where the cut
    is. `cross` is the other direction: how far the arm crosses onto the card, which is
    what makes the four lines meet in a **+** at every corner and is the only thing on
    the page that says the grid is square.

    `placement = inside` puts the arm on the card instead, and `reach` is then
    meaningless — there is no neighbour in that direction, only the card itself.
    """
    w = max(1, round(spec.width * g.ppm))
    color = _hex(spec.color)
    over = spec.cross * g.ppm
    out = spec.placement is GuidePlacement.OUTSIDE
    for col, row in sorted(filled):
        x0, y0, x1, y1 = _trim_box(g, col, row, ox, oy)
        corners = ((x0, y0, -1, -1), (x1, y0, 1, -1), (x0, y1, -1, 1), (x1, y1, 1, 1))
        for cx, cy, sx, sy in corners:
            # each corner has two arms: one along x (running off the side of the card),
            # one along y (off the top or bottom). `across` is the fixed coordinate.
            for step, corner, across in (((sx, 0), cx, cy), ((0, sy), cy, cx)):
                if out:
                    end = _arm_end(spec, g, filled, (col, row), step, corner, ox, oy)
                    start = corner + (step[0] or step[1]) * -over
                else:
                    # inward: the arm is on the card, and `cross` is what pokes out
                    inward = -(step[0] or step[1])
                    end = corner + inward * spec.length * g.ppm
                    start = corner - inward * over
                a, b, c = round(start), round(end), round(across)
                ends = [(a, c), (b, c)] if step[0] else [(c, a), (c, b)]
                draw.line(ends, fill=color, width=w)


def _full_guides(
    draw: ImageDraw.ImageDraw,
    spec: GuideSpec,
    g: Geo,
    ox: int,
    oy: int,
    filled: set[tuple[int, int]],
) -> None:
    """Trim lines straight across the paper, over the cards.

    Restricted to the rows and columns that actually hold one, for the same reason the
    marks are: a line across a row of empty cells describes a cut nobody is making.
    """
    w = max(1, round(spec.width * g.ppm))
    color = _hex(spec.color)
    xs: set[int] = set()
    ys: set[int] = set()
    for col, row in filled:
        x0, y0, x1, y1 = _trim_box(g, col, row, ox, oy)
        xs.update((round(x0), round(x1)))
        ys.update((round(y0), round(y1)))
    for x in sorted(xs):
        draw.line([(x, 0), (x, g.page_h)], fill=color, width=w)
    for y in sorted(ys):
        draw.line([(0, y), (g.page_w, y)], fill=color, width=w)


def _reg_marks(draw: ImageDraw.ImageDraw, cfg: Config, g: Geo) -> None:
    """Corner targets at fixed places on the paper.

    Deliberately **not** moved by the ink offset, which is the opposite rule from the
    cut guides and for a reason worth stating: these exist to be measured against each
    other through the paper. Nudged along with the cards they would line up on every
    sheet by construction, reporting a printer as perfectly registered no matter what
    it does. Left where they are, the gap between the two sides' targets is the drift
    that is still there — which is the number the back offset is set from.
    """
    if cfg.sheet_reg_marks is not RegMarks.CORNERS:
        return
    inset = round(cfg.sheet_reg_inset_mm * g.ppm)
    n = round(3 * g.ppm)
    w = max(1, round(0.3 * g.ppm))
    pw, ph = g.page_w, g.page_h
    for x, y in (
        (inset, inset),
        (pw - inset, inset),
        (inset, ph - inset),
        (pw - inset, ph - inset),
    ):
        draw.line([(x - n, y), (x + n, y)], fill=(0, 0, 0), width=w)
        draw.line([(x, y - n), (x, y + n)], fill=(0, 0, 0), width=w)


def render_page(
    images: list[Image.Image | None], cfg: Config, g: Geo, *, is_back: bool
) -> Image.Image:
    page = blank_page(cfg)
    draw = ImageDraw.Draw(page)
    ppm = g.ppm
    ox = round(
        (cfg.sheet_back_offset_x_mm if is_back else cfg.sheet_front_offset_x_mm) * ppm
    )
    oy = round(
        (cfg.sheet_back_offset_y_mm if is_back else cfg.sheet_front_offset_y_mm) * ppm
    )
    spec = guides_for(cfg, back=is_back)
    cw, ch = g.cell_w, g.cell_h
    # **which cells hold a card**, which is what the guides are about. A partial last
    # page used to get a full grid of lines from the page-wide styles — nine cards'
    # worth of cut marks around two cards, describing seven cuts nobody is making.
    filled: set[tuple[int, int]] = set()
    for i, im in enumerate(images):
        if im is None:
            continue
        col, row = i % g.cols, i // g.cols
        filled.add((col, row))
        x, y = g.cell_xy(col, row)
        page.paste(fit(im, cw, ch, cfg.sheet_fit), (x + ox, y + oy))
    if spec is not None and filled:
        if spec.style is GuideStyle.CORNERS:
            _mark_guides(draw, spec, g, ox, oy, filled)
        elif spec.style is GuideStyle.FULL:
            _full_guides(draw, spec, g, ox, oy, filled)
    _reg_marks(draw, cfg, g)
    return page


def _by_trim(cells: list[Cell]) -> dict[Trim, list[Cell]]:
    """Cells grouped by the size they print at, first size encountered first.

    One group is the overwhelmingly common case; a second only appears when the
    batch mixes an oversized card in, and it gets its own pages rather than
    being squeezed into someone else's grid.
    """
    groups: dict[Trim, list[Cell]] = {}
    for cell in cells:
        groups.setdefault(cell.trim, []).append(cell)
    return groups


def pages_for(count: int, per_page: int, faces: Faces) -> int:
    """How many pages ``count`` cards of one trim fill — the *one* place this is
    worked out, so the count :func:`plan` promises and the one the imposition
    actually writes cannot drift apart."""
    sides = 2 if faces is Faces.DUPLEX else 1
    return -(-count // per_page) * sides  # ceil


def page_count(cells: list[Cell], cfg: Config) -> int:
    """How many pages these cells will fill, imposing nothing."""
    return sum(
        pages_for(len(group), geometry(cfg, trim).per_page, cfg.sheet_faces)
        for trim, group in _by_trim(cells).items()
    )


def _iter_pages(cells: list[Cell], cfg: Config) -> Iterator[Image.Image]:
    """Impose per ``sheet_faces``; duplex interleaves front + mirrored back."""
    faces = cfg.sheet_faces
    for trim, group in _by_trim(cells).items():
        g = geometry(cfg, trim)
        for start in range(0, len(group), g.per_page):
            chunk = group[start : start + g.per_page]
            fronts: list[Image.Image | None] = [c.front for c in chunk]
            backs: list[Image.Image | None] = [c.back for c in chunk]
            if faces in (Faces.FRONTS, Faces.DUPLEX):
                yield render_page(fronts, cfg, g, is_back=False)
            if faces is Faces.DUPLEX:
                yield render_page(_grid_reorder(backs, cfg, g), cfg, g, is_back=True)
            elif faces is Faces.BACKS:
                yield render_page(backs, cfg, g, is_back=True)


def _pages_to_pdf(
    pages: Iterator[Image.Image],
    dst: Path,
    cfg: Config,
    total: int = progress.UNKNOWN,
) -> int:
    """Write pages losslessly via img2pdf, one page raster in memory at a time.

    Each page is dumped to a temp PNG (Flate/lossless, DPI-tagged) then embedded
    by img2pdf without re-encoding — so print output is never JPEG-degraded, and
    huge high-DPI pages don't all sit in RAM at once.

    Progress is reported from *here* rather than from the generator, because this
    is where both halves of the wait happen: rendering the pages, and then the
    embed, which on a real run is seconds of its own. Reported from the generator
    it read as a bar that filled to the last page and then fell back to a spinner.
    """
    sink = progress.Sink()
    sink.start("Imposing", total)
    tmp: list[str] = []
    try:
        for page in pages:
            path = scratch.file(".png")
            page.save(path, "PNG", dpi=(cfg.sheet_dpi, cfg.sheet_dpi))
            tmp.append(str(path))
            sink.advance(f"page {len(tmp)}")
        if not tmp:
            raise ValueError("no pages to write")
        sink.at("writing the PDF")
        dst.write_bytes(cast(bytes, img2pdf.convert(tmp)))
        return len(tmp)
    finally:
        sink.finish()
        for path in tmp:
            with contextlib.suppress(OSError):
                Path(path).unlink()


def impose_to_pdf(cells: list[Cell], cfg: Config, dst: Path) -> int:
    """Impose the cards and write a lossless print PDF; returns the page count.

    Cards of one trim size share pages; a size that is not the configured trim
    (an oversized card) gets pages of its own, at its own size.
    """
    return _pages_to_pdf(
        _iter_pages(cells, cfg), dst, cfg, total=page_count(cells, cfg)
    )


def labelled_page(
    cfg: Config, tiles: Sequence[tuple[str, Image.Image]], trim: Trim
) -> Image.Image:
    """One page of the same card at several settings, each labelled underneath.

    The no-scanner way to choose a number: print this, look at it on the medium,
    and read off the label under the one that looks right. Every tile is at true
    card size, because a correction judged at thumbnail size is not judged.
    """
    page = blank_page(cfg)
    draw = ImageDraw.Draw(page)
    ppm = _ppm(cfg)
    cell_w, cell_h = round(trim[0] * ppm), round(trim[1] * ppm)
    label_h = round(_LABEL_MM * ppm)
    m = margins(cfg)
    left, top = round(m.left * ppm), round(m.top * ppm)
    edge_x, edge_y = round(m.right * ppm), round(m.bottom * ppm)
    gap = round(_TILE_GAP_MM * ppm)
    cols = max(1, (page.width - left - edge_x + gap) // (cell_w + gap))
    font = _label_font(round(_LABEL_MM * ppm * 0.55))
    for i, (label, im) in enumerate(tiles):
        col, row = i % cols, i // cols
        x = left + col * (cell_w + gap)
        y = top + row * (cell_h + label_h + gap)
        if y + cell_h + label_h > page.height - edge_y:
            break  # the rest do not fit; the caller is told how many were placed
        page.paste(fit(im, cell_w, cell_h, cfg.sheet_fit), (x, y))
        draw.text(
            (x, y + cell_h + label_h * 0.7),
            label,
            fill=(0, 0, 0),
            font=font,
            anchor="ls",
        )
    return page


def tiles_per_page(cfg: Config, trim: Trim) -> int:
    """How many labelled tiles one page holds, so the caller can say what it cut."""
    ppm = _ppm(cfg)
    cell_w, cell_h = round(trim[0] * ppm), round(trim[1] * ppm)
    label_h = round(_LABEL_MM * ppm)
    m = margins(cfg)
    gap = round(_TILE_GAP_MM * ppm)
    page_w, page_h = _page_size_px(cfg)
    keep_x = round((m.left + m.right) * ppm)
    keep_y = round((m.top + m.bottom) * ppm)
    cols = max(1, (page_w - keep_x + gap) // (cell_w + gap))
    rows = max(1, (page_h - keep_y + gap) // (cell_h + label_h + gap))
    return int(cols * rows)


#: room under each tile for its label, and the space between tiles
_LABEL_MM = 6.0
_TILE_GAP_MM = 4.0


def _label_font(size: int) -> Any:
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=max(8, size))
    except (TypeError, AttributeError):  # pragma: no cover — Pillow < 10.1
        return ImageFont.load_default()


def write_page_pdf(page: Image.Image, dst: Path, cfg: Config) -> None:
    """Write one already-composed page as a lossless PDF (the calibration chart).

    Same writer as card sheets, so a printed chart travels the identical path to
    paper as real cards — otherwise the correction would be measured on one print
    path and applied on another.
    """
    _pages_to_pdf(iter([page.convert("RGB")]), dst, cfg)
