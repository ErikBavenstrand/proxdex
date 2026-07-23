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
