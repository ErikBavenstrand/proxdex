"""Frame-size guides: where a card's printed border sits, keyed by era.

The align overlay draws the card outline plus these inner border lines (square
corners — the lines meet at 90° regardless of the trim's corner rounding) so a
scan can be expanded until its border matches the real card. Insets are
fractions of the card, per edge; a uniform physical border is a smaller
fraction of the long axis than the short axis, so top/bottom ≠ left/right.

The era is inferred from the card's set id. Sets we haven't measured fall back
to :data:`DEFAULT`. Add a :class:`FrameGuide` and its set ids as more eras get
measured.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FrameGuide:
    id: str
    name: str
    #: inner border edge inset as card fractions, [top, right, bottom, left]
    inset: tuple[float, float, float, float]


def _uniform(border_px: float, w: float, h: float) -> tuple[float, float, float, float]:
    """A uniform ``border_px`` on a ``w``×``h`` reference card, as edge fractions."""
    fx, fy = border_px / w, border_px / h
    return (fy, fx, fy, fx)


# Base Set..Neo Destiny (yellow-border WOTC era): uniform 20px on a 372x515 card.
_WOTC = FrameGuide(
    id="wotc",
    name="WOTC vintage (Base-Neo Destiny)",
    inset=_uniform(20, 372, 515),
)

GUIDES: dict[str, FrameGuide] = {_WOTC.id: _WOTC}
DEFAULT = _WOTC  # sets we haven't measured fall back here

# set-id prefixes per era (pokemontcg.io ids: base1-6, gym1-2, neo1-4)
_ERA_PREFIXES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("base", "gym", "neo"), "wotc"),
)


def for_set(set_id: str) -> FrameGuide:
    """Pick the frame guide for a set id, falling back to :data:`DEFAULT`."""
    sid = (set_id or "").lower()
    for prefixes, guide_id in _ERA_PREFIXES:
        if sid.startswith(prefixes):
            return GUIDES[guide_id]
    return DEFAULT


@dataclass(frozen=True, slots=True)
class Extend:
    """Per-edge expansion in mm plus what it achieves."""

    top: float
    bottom: float
    left: float
    right: float
    result_aspect: float  # exactly the card aspect, by construction
    inflation: float  # how much thicker than spec a border ends (0 = spec exact)


def _split(
    total: float, frac_a: float, frac_b: float, cur_a: float, cur_b: float
) -> tuple[float, float]:
    """Divide ``total`` growth between two opposite edges toward the spec
    proportion (``frac_a:frac_b`` of the final border), never cropping — a
    negative share is pushed to the other edge so the sum is always ``total``.
    """
    border_total = cur_a + cur_b + total
    a = border_total * frac_a / (frac_a + frac_b)
    ext_a, ext_b = a - cur_a, (border_total - a) - cur_b
    if ext_a < 0:
        ext_b += ext_a
        ext_a = 0.0
    if ext_b < 0:
        ext_a += ext_b
        ext_b = 0.0
    return max(ext_a, 0.0), max(ext_b, 0.0)


def solve_extension(
    w: int,
    h: int,
    inner: tuple[float, float, float, float],
    guide: FrameGuide,
    card_w_mm: float = 63.0,
    card_h_mm: float = 88.0,
) -> Extend:
    """From the marked inner-border edges (``inner`` = top,right,bottom,left as
    fractions of the image, i.e. current border thickness per edge) and the era
    ``guide``, compute the per-edge expansion (mm) that makes the card **exactly**
    ``card_w_mm:card_h_mm`` while bringing every border to (at least) spec.

    The image edge is assumed to be the card's trim edge. Expansion only — the
    content is never scaled or cropped; we grow the proportionally-short axis, so
    the exact aspect is always reachable. When the marks/scan aren't perfectly
    on-aspect the borders land spec-proportional but slightly thick (``inflation``).
    """
    ft, fr, fb, fl = inner
    bt, br, bb, bl = ft * h, fr * w, fb * h, fl * w  # current border px per edge
    gt, gr, gb, gl = guide.inset
    r = card_w_mm / card_h_mm
    inner_w, inner_h = w - bl - br, h - bt - bb
    w_spec = inner_w / (1 - gl - gr)  # card width that makes L/R borders exact
    h_spec = inner_h / (1 - gt - gb)  # card height that makes T/B borders exact
    # final card: exact aspect, big enough that no edge crops and borders ≥ spec
    w_final = max(float(w), r * h, w_spec, r * h_spec)
    h_final = w_final / r
    ext_l, ext_r = _split(w_final - w, gl, gr, bl, br)
    ext_t, ext_b = _split(h_final - h, gt, gb, bt, bb)
    ppm = w / card_w_mm
    return Extend(
        top=ext_t / ppm,
        bottom=ext_b / ppm,
        left=ext_l / ppm,
        right=ext_r / ppm,
        result_aspect=(w + ext_l + ext_r) / (h + ext_t + ext_b),
        inflation=max(w_final / w_spec, h_final / h_spec) - 1,
    )
