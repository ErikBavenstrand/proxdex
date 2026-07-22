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
    """Per-edge expansion in mm to reach the era's target border widths."""

    top: float
    bottom: float
    left: float
    right: float
    #: edges whose border is already wider than target (can't shrink → left as-is)
    over_target: tuple[str, ...]


def solve_extension(
    w: int,
    h: int,
    inner: tuple[float, float, float, float],
    guide: FrameGuide,
    card_w_mm: float = 63.0,
    card_h_mm: float = 88.0,
) -> Extend:
    """Widen each edge to the era's target border, width and height treated
    **independently**.

    The rule: inner-frame-width + left + right = ``card_w_mm``, and inner-frame-
    height + top + bottom = ``card_h_mm``. So on each axis the inner frame spans
    (card minus the two target borders) mm, which fixes that axis's px-per-mm
    from the marked frame alone -- no rescaling, no cross-axis coupling, and no
    assumption that borders are uniform (each edge has its own target). Every
    edge then grows by (its target minus its current border). ``inner`` = the
    current border per edge as image fractions [top, right, bottom, left].
    """
    ft, fr, fb, fl = inner
    cur_t, cur_r, cur_b, cur_l = ft * h, fr * w, fb * h, fl * w  # current px per edge
    gt, gr, gb, gl = guide.inset
    inner_w, inner_h = w - cur_l - cur_r, h - cur_t - cur_b
    ppm = w / card_w_mm  # cardbleed's mm→px unit (single scale for all its flags)
    if inner_w <= 0 or inner_h <= 0 or gl + gr >= 1 or gt + gb >= 1:
        return Extend(0.0, 0.0, 0.0, 0.0, ())
    # px-per-mm per axis, from the sum rule (inner spans card minus its borders)
    sw = inner_w / (card_w_mm * (1 - gl - gr))
    sh = inner_h / (card_h_mm * (1 - gt - gb))
    tgt_l, tgt_r = gl * card_w_mm * sw, gr * card_w_mm * sw
    tgt_t, tgt_b = gt * card_h_mm * sh, gb * card_h_mm * sh
    over = tuple(
        name
        for name, cur, tgt in (
            ("top", cur_t, tgt_t),
            ("bottom", cur_b, tgt_b),
            ("left", cur_l, tgt_l),
            ("right", cur_r, tgt_r),
        )
        if cur > tgt + 0.5
    )
    return Extend(
        top=max(0.0, tgt_t - cur_t) / ppm,
        bottom=max(0.0, tgt_b - cur_b) / ppm,
        left=max(0.0, tgt_l - cur_l) / ppm,
        right=max(0.0, tgt_r - cur_r) / ppm,
        over_target=over,
    )
