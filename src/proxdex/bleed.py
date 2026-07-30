"""Reshape a card to the right trim aspect + border widths, via ``cardbleed``.

proxdex owns the *inputs* — the era's target border widths
(:class:`frames.FrameGuide`), where the border currently sits (the align
marks / CLI fractions) and **the size this card prints at** — and hands them to
cardbleed, which does the fit
(exact card aspect, borders as close to target as possible, never distorting
the art unless ``stretch`` is asked for) and continues the border into any
added area. The cut bleed added at sheet time is a separate, plain margin.

Everything runs in-process: cardbleed is a library dependency, not a subprocess.

The trim is a ``Trim`` argument rather than something read out of ``Config``,
because it is **per card**: an oversized printing prints at 88.9×127mm, and
``sheet.trim_mm`` is the one place that is decided. Reading ``cfg.card_w_mm``
here reshaped a planar card to 63.5:88.9 and `sheet` then cropped 0.91mm off
each side to make it fit its own cell — a border 1mm narrower than the spec
asked for, with nothing on screen to say so.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cardbleed import Edges, bleed_card
from cardbleed import FileError as _CardbleedError
from cardbleed.geometry import Amount, FitPlan, solve_fit

from proxdex.errors import FileError

if TYPE_CHECKING:
    from pathlib import Path

    from proxdex.frames import FrameGuide
    from proxdex.sheet import Trim

Fracs = tuple[float, float, float, float]  # [top, right, bottom, left]


def _pct(fracs: Fracs) -> Edges:
    """[top, right, bottom, left] fractions → an ``Edges`` of percents."""
    top, right, bottom, left = fracs
    return Edges(
        top=Amount(top * 100, "%"),
        right=Amount(right * 100, "%"),
        bottom=Amount(bottom * 100, "%"),
        left=Amount(left * 100, "%"),
    )


def fit_plan(
    w: int,
    h: int,
    guide: FrameGuide,
    inner: Fracs,
    trim: Trim,
    *,
    stretch: bool,
) -> FitPlan:
    """Preview the reshape (for dry-run / the readout) without writing.

    ``inner`` = current border per edge as image fractions [top, right,
    bottom, left]; ``guide.inset`` = the era's target borders as card fractions.
    """
    return solve_fit(
        w,
        h,
        _pct(inner),
        _pct(guide.inset),
        trim[0],
        trim[1],
        stretch=stretch,
        crop=True,
    )


def fit(
    src: Path,
    dst: Path,
    guide: FrameGuide,
    inner: Fracs,
    trim: Trim,
    *,
    stretch: bool,
) -> None:
    """Reshape ``src`` to the card aspect + the era's target borders."""
    _run(
        src,
        dst,
        trim,
        border_target=_pct(guide.inset),
        border_current=_pct(inner),
        stretch=stretch,
        fill_corners=True,
    )


def grow(src: Path, dst: Path, trim: Trim, **mm: float) -> None:
    """Manually add border to each edge (``top``/``right``/``bottom``/``left``
    mm) — no fit, no distortion."""
    _run(
        src,
        dst,
        trim,
        bleed=Edges(
            top=Amount(mm.get("top", 0.0), "mm"),
            right=Amount(mm.get("right", 0.0), "mm"),
            bottom=Amount(mm.get("bottom", 0.0), "mm"),
            left=Amount(mm.get("left", 0.0), "mm"),
        ),
        fill_corners=True,
    )


def cut_bleed(src: Path, dst: Path, trim: Trim, px: int) -> None:
    """Uniform cut bleed added at sheet time (no fit, no corner squaring)."""
    _run(src, dst, trim, bleed=f"{px}px")


def _run(src: Path, dst: Path, trim: Trim, **kw: Any) -> None:
    try:
        bleed_card(src, dst, card_size=trim, **kw)
    except _CardbleedError as exc:
        raise FileError(f"cardbleed failed on {src.name}: {exc}") from exc
