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


def _mm(
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


# Base Set..Neo Destiny (yellow-border WOTC era). Measured off a real card with
# calipers (top 3.3 / bottom 3.6 / left 3.2 / right 3.1 mm); the border wanders a
# little card-to-card, so we use the tidy averages top/bottom 3.45, sides 3.15mm.
_WOTC = FrameGuide(
    id="wotc",
    name="WOTC vintage (Base-Neo Destiny)",
    inset=_mm(3.45, 3.15, 3.45, 3.15),
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
    inner_w, inner_h = w - bl - br, h - bt - bb  # the (fixed) inner frame in px
    # The final card is card_w_mm:card_h_mm exactly, so it has ONE free variable:
    # the scale s (px per mm), with w_final = card_w_mm*s, h_final = card_h_mm*s.
    # aw, ah are how many px the inner frame *should* span at s=1 per axis; each
    # axis alone wants s_axis = inner_px / a. With the aspect locked we can't hit
    # both, so take the least-squares s that minimizes total border deviation
    # (edges of the longer axis carry more weight).
    aw = (1 - gl - gr) * card_w_mm
    ah = (1 - gt - gb) * card_h_mm
    s = (aw * inner_w + ah * inner_h) / (aw * aw + ah * ah)
    s = max(s, w / card_w_mm, h / card_h_mm)  # never crop: final ≥ current
    w_final, h_final = card_w_mm * s, card_h_mm * s
    # grow each edge toward its own target border (deficit); _split re-centres and
    # never crops, so a single cropped/thin edge takes the growth.
    ext_l, ext_r = _split(w_final - w, gl, gr, bl, br)
    ext_t, ext_b = _split(h_final - h, gt, gb, bt, bb)
    ppm = w / card_w_mm
    border_w = (bl + br + (w_final - w)) / 2  # resulting per-edge border, px
    border_h = (bt + bb + (h_final - h)) / 2
    return Extend(
        top=ext_t / ppm,
        bottom=ext_b / ppm,
        left=ext_l / ppm,
        right=ext_r / ppm,
        result_aspect=(w + ext_l + ext_r) / (h + ext_t + ext_b),
        inflation=max(
            abs(border_w - gl * w_final) / (gl * w_final),
            abs(border_h - gt * h_final) / (gt * h_final),
        ),
    )
