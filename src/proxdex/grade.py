"""Grade a card: one creative look, applied identically to every card.

Grade is deliberately *only* a look — brightness, contrast, saturation, gamma,
and optionally a per-card levels stretch. It does not try to normalise cards
against each other.

An earlier version did: it read the colour of the card's frame and white-balanced
every card's frame to one shared target. That is wrong at the premise. A card
frame is not a grey card — Pokémon's is yellow, MTG's is black, and a full-art
print has none. Balancing them to a common colour does not remove a cast, it
*invents* one: with a mixed library the shared target came out olive, and a
neutral grey inside a yellow-bordered card graded to deep blue while the same
grey inside a black-bordered card blew out to white. Both were measured, and
both are gone.

Matching a *medium* is a real problem, but it is a print-time one: the paper and
ink are the same for every card on the sheet, so the correction is measured once
per medium (`proxdex profile` / `proxdex calibrate`) and applied at `sheet` time,
outside the stored master. See :mod:`proxdex.profiles`.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageEnhance

from proxdex.config import Config

RGB = NDArray[np.float32]
_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)

#: luminance percentiles the levels stretch maps to black and white. Not
#: settings: the interesting knob is *how much* of the stretch to apply, and two
#: more percentile fields only ever made that harder to reason about.
_BLACK_PCT = 0.5
_WHITE_PCT = 99.5
#: below this the black and white points are already together — leave it alone
_MIN_SPAN = 1.0


def auto_levels(arr: RGB, strength: float) -> RGB:
    """Stretch this image's own black/white points, blended by ``strength``.

    Reads one card and changes only that card, so a legitimately dark card is
    pulled toward full range rather than forced into someone else's.
    """
    if strength <= 0.0:
        return arr
    lum = arr @ _LUMA
    lo = float(np.percentile(lum, _BLACK_PCT))
    hi = float(np.percentile(lum, _WHITE_PCT))
    if hi - lo < _MIN_SPAN:
        return arr
    leveled = (arr - lo) * (255.0 / (hi - lo))
    blend = min(1.0, strength)
    return (arr * (1.0 - blend) + leveled * blend).astype(np.float32)


def gamma(im: Image.Image, value: float) -> Image.Image:
    if value == 1.0:
        return im
    arr = (np.asarray(im, dtype=np.float32) / 255.0) ** (1.0 / value)
    return Image.fromarray((arr * 255.0).clip(0, 255).astype(np.uint8))


def grade(
    im: Image.Image,
    cfg: Config,
    *,
    brightness: float | None = None,
    contrast: float | None = None,
    saturation: float | None = None,
    gamma_value: float | None = None,
    levels: float | None = None,
) -> Image.Image:
    """Apply the look. Any value left ``None`` comes from ``[grade]`` in config."""
    im = im.convert("RGB")
    strength = cfg.grade_levels if levels is None else levels
    if strength > 0.0:
        arr = auto_levels(np.asarray(im, dtype=np.float32), strength)
        im = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
    im = ImageEnhance.Brightness(im).enhance(
        cfg.grade_brightness if brightness is None else brightness
    )
    im = ImageEnhance.Contrast(im).enhance(
        cfg.grade_contrast if contrast is None else contrast
    )
    im = ImageEnhance.Color(im).enhance(
        cfg.grade_saturation if saturation is None else saturation
    )
    return gamma(im, cfg.grade_gamma if gamma_value is None else gamma_value)
