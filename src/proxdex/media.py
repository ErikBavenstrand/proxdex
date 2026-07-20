"""Media / output compensation, baked into the print stage.

Some media wash colours out — notably transparent plastic foil, where ink is
semi-transparent so the print reads lighter, flatter and less saturated than
the screen master. A media profile pre-distorts the image to cancel that: push
saturation and density up so the *printed* result matches what you intended.

Profiles are starting points; calibrate with a test print and override any
value under ``[print]`` in ``proxdex.toml``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageEnhance

from .config import Config


@dataclass(slots=True, frozen=True)
class Recipe:
    saturation: float = 1.0
    contrast: float = 1.0
    brightness: float = 1.0
    gamma: float = 1.0  # < 1 darkens midtones → more ink density


PROFILES: dict[str, Recipe] = {
    "none": Recipe(),
    "paper": Recipe(saturation=1.02, contrast=1.02),
    # transparent plastic foil washes out hard → boost saturation + density
    "foil": Recipe(saturation=1.38, contrast=1.16, brightness=0.95, gamma=0.88),
}


def resolve(cfg: Config, profile: str | None = None) -> tuple[str, Recipe]:
    """Active profile name + recipe (built-in defaults, [print] overrides win)."""
    name = (profile or cfg.print_profile or "none").lower()
    base = PROFILES.get(name, PROFILES["none"])
    recipe = Recipe(
        saturation=_pick(cfg.print_saturation, base.saturation),
        contrast=_pick(cfg.print_contrast, base.contrast),
        brightness=_pick(cfg.print_brightness, base.brightness),
        gamma=_pick(cfg.print_gamma, base.gamma),
    )
    return name, recipe


def compensate(im: Image.Image, recipe: Recipe) -> Image.Image:
    im = im.convert("RGB")
    im = ImageEnhance.Brightness(im).enhance(recipe.brightness)
    im = ImageEnhance.Contrast(im).enhance(recipe.contrast)
    im = ImageEnhance.Color(im).enhance(recipe.saturation)
    if recipe.gamma != 1.0:
        arr = (np.asarray(im, dtype=np.float32) / 255.0) ** (1.0 / recipe.gamma)
        im = Image.fromarray((arr * 255.0).clip(0, 255).astype(np.uint8))
    return im


def _pick(override: float | None, default: float) -> float:
    return default if override is None else override
