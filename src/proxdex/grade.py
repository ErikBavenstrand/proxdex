"""Grade cards toward a uniform look.

Uniformity across a mixed collection (crisp digital art next to warm, flat
scans) needs two steps, in order:

1. **normalize** — pull each card to a common baseline *dynamically*:
   * white-balance the shared card frame to one target colour (fixes the
     warm/cool cast that scans and digital art disagree on), and
   * stretch the tonal range to consistent black/white points (auto levels).
2. **look** — apply one identical creative recipe (brightness, contrast,
   saturation, gamma) on top. Because every card now starts from the same
   baseline, this single "intended saturation" lands the same way on all of
   them, so the batch prints uniformly.

Set the frame target explicitly (``match_border_target``) or let the caller
pass the library's own median frame colour, so the collection converges on its
own consensus rather than a magic number.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageEnhance

from .borders import frame_color
from .config import Config

RGB = NDArray[np.float32]
_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _white_balance(arr: RGB, target: Sequence[float] | None) -> RGB:
    if target is None:
        return arr
    current = frame_color(arr)
    wanted = np.asarray(target, dtype=np.float32)
    scale = np.where(current > 1.0, wanted / current, 1.0)
    scale = np.clip(scale, 0.6, 1.6)  # keep corrections sane
    return arr * scale


def _auto_levels(arr: RGB, low_pct: float, high_pct: float, strength: float) -> RGB:
    """Stretch black/white points, but blend with the original by ``strength``
    so a legitimately dark or bright card isn't forced to a standard range."""
    if strength <= 0.0:
        return arr
    lum = arr @ _LUMA
    lo = float(np.percentile(lum, low_pct))
    hi = float(np.percentile(lum, high_pct))
    if hi - lo < 1.0:
        return arr
    leveled = (arr - lo) * (255.0 / (hi - lo))
    return (arr * (1.0 - strength) + leveled * strength).astype(np.float32)


def grade(
    im: Image.Image,
    cfg: Config,
    *,
    frame_target: tuple[float, float, float] | None = None,
    normalize: bool | None = None,
) -> Image.Image:
    im = im.convert("RGB")
    do_norm = cfg.grade_normalize if normalize is None else normalize
    if do_norm:
        arr = np.asarray(im, dtype=np.float32)
        target: Sequence[float] | None = cfg.match_border_target or frame_target
        arr = _white_balance(arr, target)
        arr = _auto_levels(
            arr, cfg.grade_black_pct, cfg.grade_white_pct, cfg.grade_level_strength
        )
        im = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
    im = ImageEnhance.Brightness(im).enhance(cfg.grade_brightness)
    im = ImageEnhance.Contrast(im).enhance(cfg.grade_contrast)
    im = ImageEnhance.Color(im).enhance(cfg.grade_saturation)
    if cfg.grade_gamma != 1.0:
        arr = (np.asarray(im, dtype=np.float32) / 255.0) ** (1.0 / cfg.grade_gamma)
        im = Image.fromarray((arr * 255.0).clip(0, 255).astype(np.uint8))
    return im
