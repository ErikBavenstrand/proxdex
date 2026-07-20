"""Impose finished card fronts onto print pages and export a PDF.

proxdex owns the whole path to paper: each card is placed at its exact physical
size (cards already carry cardbleed's bleed, so the trim box sits inset by the
bleed), optional crop marks mark where to cut. Because proxdex controls
everything down to the PDF, the print path is fully determined here — which is
what lets colour calibration actually transfer (calibrate the chart through
this same renderer).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .config import Config

# page sizes in mm (portrait)
PAGES: dict[str, tuple[float, float]] = {
    "a4": (210.0, 297.0),
    "letter": (215.9, 279.4),
}


def _ppm(cfg: Config) -> float:
    return cfg.sheet_dpi / 25.4


def _page_size_px(cfg: Config) -> tuple[int, int]:
    w_mm, h_mm = PAGES.get(cfg.sheet_page.lower(), PAGES["a4"])
    ppm = _ppm(cfg)
    return round(w_mm * ppm), round(h_mm * ppm)


def _blank_page(cfg: Config) -> Image.Image:
    return Image.new("RGB", _page_size_px(cfg), (255, 255, 255))


def _crop_marks(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], cfg: Config):
    """Draw outward ticks at the four trim corners of a placed card."""
    x0, y0, x1, y1 = box
    n = round(cfg.sheet_guide_mm * _ppm(cfg))
    for cx, cy, dx, dy in (
        (x0, y0, -1, -1),
        (x1, y0, 1, -1),
        (x0, y1, -1, 1),
        (x1, y1, 1, 1),
    ):
        draw.line([(cx, cy), (cx + dx * n, cy)], fill=(0, 0, 0), width=1)
        draw.line([(cx, cy), (cx, cy + dy * n)], fill=(0, 0, 0), width=1)


def impose(images: list[Image.Image], cfg: Config) -> list[Image.Image]:
    """Lay out card images across pages; returns one PIL page image per page."""
    ppm = _ppm(cfg)
    bleed = cfg.bleed_mm * ppm
    cell_w = round((cfg.card_w_mm + 2 * cfg.bleed_mm) * ppm)
    cell_h = round((cfg.card_h_mm + 2 * cfg.bleed_mm) * ppm)
    gap = round(cfg.sheet_spacing_mm * ppm)
    margin = round(cfg.sheet_margin_mm * ppm)
    per_page = cfg.sheet_cols * cfg.sheet_rows
    grid_w = cfg.sheet_cols * cell_w + (cfg.sheet_cols - 1) * gap
    page_w, _ = _page_size_px(cfg)
    x_off = max(margin, (page_w - grid_w) // 2)

    pages: list[Image.Image] = []
    for start in range(0, len(images), per_page):
        page = _blank_page(cfg)
        draw = ImageDraw.Draw(page)
        for i, im in enumerate(images[start : start + per_page]):
            col, row = i % cfg.sheet_cols, i // cfg.sheet_cols
            x = x_off + col * (cell_w + gap)
            y = margin + row * (cell_h + gap)
            page.paste(im.convert("RGB").resize((cell_w, cell_h)), (x, y))
            if cfg.sheet_guides:
                trim = (
                    round(x + bleed),
                    round(y + bleed),
                    round(x + cell_w - bleed),
                    round(y + cell_h - bleed),
                )
                _crop_marks(draw, trim, cfg)
        pages.append(page)
    return pages


def write_pdf(pages: list[Image.Image], dst: Path, cfg: Config) -> None:
    if not pages:
        raise ValueError("no pages to write")
    pages[0].save(
        dst,
        "PDF",
        resolution=float(cfg.sheet_dpi),
        save_all=True,
        append_images=pages[1:],
    )


def single_page_pdf(image: Image.Image, dst: Path, cfg: Config) -> None:
    """Center one image on a blank page and write a PDF (calibration charts).

    Uses the same page renderer as card sheets, so a printed chart travels the
    identical path to paper as real cards.
    """
    page = _blank_page(cfg)
    pw, ph = page.size
    margin = round(cfg.sheet_margin_mm * _ppm(cfg))
    avail = (pw - 2 * margin, ph - 2 * margin)
    im = image.convert("RGB").copy()
    im.thumbnail(avail)
    page.paste(im, ((pw - im.width) // 2, (ph - im.height) // 2))
    write_pdf([page], dst, cfg)
