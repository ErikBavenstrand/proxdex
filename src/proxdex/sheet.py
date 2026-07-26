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
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import img2pdf
from PIL import Image, ImageDraw

from proxdex.config import (
    Config,
    DuplexFlip,
    Faces,
    Fit,
    GuidePlacement,
    GuideStyle,
    Orientation,
    PageSize,
    RegMarks,
)

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
    w_mm, h_mm = _page_mm(cfg)
    ppm = _ppm(cfg)
    return round(w_mm * ppm), round(h_mm * ppm)


def _blank_page(cfg: Config) -> Image.Image:
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


def _page_mm(cfg: Config) -> Trim:
    w_mm, h_mm = PAGES[cfg.sheet_page]
    if cfg.sheet_orientation is Orientation.LANDSCAPE:
        w_mm, h_mm = h_mm, w_mm
    return w_mm, h_mm


def grid_for(cfg: Config, trim: Trim) -> tuple[int, int]:
    """Columns and rows for a trim size.

    The configured size keeps the configured grid — a library that prints normal
    cards sees exactly the layout it always did. Any other size has no configured
    grid to keep, so it takes as many cells as the page actually holds.
    """
    if trim == (cfg.card_w_mm, cfg.card_h_mm):
        return cfg.sheet_cols, cfg.sheet_rows
    page_w, page_h = _page_mm(cfg)
    cell_w = trim[0] + 2 * cfg.bleed_mm
    cell_h = trim[1] + 2 * cfg.bleed_mm
    usable_w = page_w - 2 * cfg.sheet_margin_mm + cfg.sheet_spacing_mm
    usable_h = page_h - 2 * cfg.sheet_margin_mm + cfg.sheet_spacing_y_mm
    cols = int((usable_w + _EPS_MM) // (cell_w + cfg.sheet_spacing_mm))
    rows = int((usable_h + _EPS_MM) // (cell_h + cfg.sheet_spacing_y_mm))
    # one per page even if the card is larger than the paper: proxdex would rather
    # print an over-margin page than silently shrink a card
    return max(1, cols), max(1, rows)


#: slack when counting how many cells fit, so a card that fills the page exactly
#: is not excluded by float error
_EPS_MM = 1e-6


def _geometry(cfg: Config, trim: Trim) -> Geo:
    ppm = _ppm(cfg)
    cell_w = round((trim[0] + 2 * cfg.bleed_mm) * ppm)
    cell_h = round((trim[1] + 2 * cfg.bleed_mm) * ppm)
    cols, rows = grid_for(cfg, trim)
    gap_x = round(cfg.sheet_spacing_mm * ppm)
    gap_y = round(cfg.sheet_spacing_y_mm * ppm)
    margin = round(cfg.sheet_margin_mm * ppm)
    page_w, page_h = _page_size_px(cfg)
    grid_w = cols * cell_w + (cols - 1) * gap_x
    grid_h = rows * cell_h + (rows - 1) * gap_y
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
        x_off=max(margin, (page_w - grid_w) // 2),
        y_off=max(margin, (page_h - grid_h) // 2),
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


def _cell_xy(g: Geo, col: int, row: int) -> tuple[int, int]:
    return (
        g.x_off + col * (g.cell_w + g.gap_x),
        g.y_off + row * (g.cell_h + g.gap_y),
    )


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


def _corner_guides(
    draw: ImageDraw.ImageDraw, trim: tuple[int, int, int, int], cfg: Config
) -> None:
    x0, y0, x1, y1 = trim
    n = round(cfg.sheet_guide_mm * _ppm(cfg))
    w = max(1, round(cfg.sheet_guide_width_mm * _ppm(cfg)))
    color = _hex(cfg.sheet_guide_color)
    d = -1 if cfg.sheet_guide_placement is GuidePlacement.OUTSIDE else 1
    for cx, cy, sx, sy in (
        (x0, y0, -d, -d),
        (x1, y0, d, -d),
        (x0, y1, -d, d),
        (x1, y1, d, d),
    ):
        draw.line([(cx, cy), (cx + sx * n, cy)], fill=color, width=w)
        draw.line([(cx, cy), (cx, cy + sy * n)], fill=color, width=w)


def _full_guides(draw: ImageDraw.ImageDraw, cfg: Config, g: Geo) -> None:
    w = max(1, round(cfg.sheet_guide_width_mm * g.ppm))
    color = _hex(cfg.sheet_guide_color)
    xs: set[int] = set()
    ys: set[int] = set()
    for col in range(g.cols):
        cx, _ = _cell_xy(g, col, 0)
        xs.update((round(cx + g.bleed), round(cx + g.cell_w - g.bleed)))
    for row in range(g.rows):
        _, cy = _cell_xy(g, 0, row)
        ys.update((round(cy + g.bleed), round(cy + g.cell_h - g.bleed)))
    for x in xs:
        draw.line([(x, 0), (x, g.page_h)], fill=color, width=w)
    for y in ys:
        draw.line([(0, y), (g.page_w, y)], fill=color, width=w)


def _reg_marks(draw: ImageDraw.ImageDraw, cfg: Config, g: Geo) -> None:
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


def _render(
    images: list[Image.Image | None], cfg: Config, g: Geo, *, is_back: bool
) -> Image.Image:
    page = _blank_page(cfg)
    draw = ImageDraw.Draw(page)
    ppm = g.ppm
    ox = round(
        (cfg.sheet_back_offset_x_mm if is_back else cfg.sheet_front_offset_x_mm) * ppm
    )
    oy = round(
        (cfg.sheet_back_offset_y_mm if is_back else cfg.sheet_front_offset_y_mm) * ppm
    )
    guides_on = cfg.sheet_guides and (
        cfg.sheet_guides_back if is_back else cfg.sheet_guides_front
    )
    cw, ch = g.cell_w, g.cell_h
    for i, im in enumerate(images):
        if im is None:
            continue
        col, row = i % g.cols, i // g.cols
        x, y = _cell_xy(g, col, row)
        page.paste(fit(im, cw, ch, cfg.sheet_fit), (x + ox, y + oy))
        if guides_on and cfg.sheet_guide_style is GuideStyle.CORNERS:
            trim = (
                round(x + g.bleed),
                round(y + g.bleed),
                round(x + cw - g.bleed),
                round(y + ch - g.bleed),
            )
            _corner_guides(draw, trim, cfg)
    if guides_on and cfg.sheet_guide_style is GuideStyle.FULL:
        _full_guides(draw, cfg, g)
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


def _iter_pages(cells: list[Cell], cfg: Config) -> Iterator[Image.Image]:
    """Impose per ``sheet_faces``; duplex interleaves front + mirrored back."""
    faces = cfg.sheet_faces
    for trim, group in _by_trim(cells).items():
        g = _geometry(cfg, trim)
        for start in range(0, len(group), g.per_page):
            chunk = group[start : start + g.per_page]
            fronts: list[Image.Image | None] = [c.front for c in chunk]
            backs: list[Image.Image | None] = [c.back for c in chunk]
            if faces in (Faces.FRONTS, Faces.DUPLEX):
                yield _render(fronts, cfg, g, is_back=False)
            if faces is Faces.DUPLEX:
                yield _render(_grid_reorder(backs, cfg, g), cfg, g, is_back=True)
            elif faces is Faces.BACKS:
                yield _render(backs, cfg, g, is_back=True)


def _pages_to_pdf(pages: Iterator[Image.Image], dst: Path, cfg: Config) -> int:
    """Write pages losslessly via img2pdf, one page raster in memory at a time.

    Each page is dumped to a temp PNG (Flate/lossless, DPI-tagged) then embedded
    by img2pdf without re-encoding — so print output is never JPEG-degraded, and
    huge high-DPI pages don't all sit in RAM at once.
    """
    tmp: list[str] = []
    try:
        for page in pages:
            fd, path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            page.save(path, "PNG", dpi=(cfg.sheet_dpi, cfg.sheet_dpi))
            tmp.append(path)
        if not tmp:
            raise ValueError("no pages to write")
        dst.write_bytes(cast(bytes, img2pdf.convert(tmp)))
        return len(tmp)
    finally:
        for path in tmp:
            with contextlib.suppress(OSError):
                Path(path).unlink()


def impose_to_pdf(cells: list[Cell], cfg: Config, dst: Path) -> int:
    """Impose the cards and write a lossless print PDF; returns the page count.

    Cards of one trim size share pages; a size that is not the configured trim
    (an oversized card) gets pages of its own, at its own size.
    """
    return _pages_to_pdf(_iter_pages(cells, cfg), dst, cfg)


def single_page_pdf(image: Image.Image, dst: Path, cfg: Config) -> None:
    """Center one image on a blank page and write a lossless PDF (charts).

    Uses the same page renderer as card sheets, so a printed chart travels the
    identical path to paper as real cards.
    """
    page = _blank_page(cfg)
    pw, ph = page.size
    margin = round(cfg.sheet_margin_mm * _ppm(cfg))
    im = image.convert("RGB").copy()
    im.thumbnail((pw - 2 * margin, ph - 2 * margin))
    page.paste(im, ((pw - im.width) // 2, (ph - im.height) // 2))
    _pages_to_pdf(iter([page]), dst, cfg)
