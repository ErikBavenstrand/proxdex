"""Uniform grade recipe — applied identically to every card so a whole
batch prints with a consistent look.

Order: optional frame white-balance (normalize every card's frame colour to
one target so all yellows match), then brightness → contrast → saturation →
gamma. Runs on the highest-resolution stage available so what you grade is
what prints.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageEnhance

from .borders import frame_color
from .config import Config


def grade(im: Image.Image, cfg: Config) -> Image.Image:
    im = im.convert("RGB")
    if cfg.match_border_target:
        arr = np.asarray(im, dtype=np.float32)
        current = frame_color(arr)
        wanted = np.asarray(cfg.match_border_target, dtype=np.float32)
        scale = np.where(current > 1.0, wanted / current, 1.0)
        im = Image.fromarray((arr * scale).clip(0, 255).astype(np.uint8))
    im = ImageEnhance.Brightness(im).enhance(cfg.grade_brightness)
    im = ImageEnhance.Contrast(im).enhance(cfg.grade_contrast)
    im = ImageEnhance.Color(im).enhance(cfg.grade_saturation)
    if cfg.grade_gamma != 1.0:
        arr = (np.asarray(im, dtype=np.float32) / 255.0) ** (1.0 / cfg.grade_gamma)
        im = Image.fromarray((arr * 255.0).clip(0, 255).astype(np.uint8))
    return im
