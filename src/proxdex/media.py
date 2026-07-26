"""Medium compensation: the starting-point recipes a print profile begins from.

Some media wash colours out — notably transparent plastic foil, where ink is
semi-transparent so the print reads lighter, flatter and less saturated than the
screen master. A recipe pre-distorts the image to cancel that: push saturation
and density up so the *printed* result matches what you intended.

These are guesses that look about right, and they are only ever a starting point.
A profile you have actually calibrated (:mod:`proxdex.profiles`) carries a
*measured* correction, and that supersedes the recipe entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance


class Preset(StrEnum):
    """The built-in starting points.

    The set of *profiles* is open — `proxdex profile new` adds one under any name
    — so a profile name is a ``str``. These are the ones that ship, and the ones
    a new profile can be based on.
    """

    NONE = "none"
    PAPER = "paper"
    FOIL = "foil"

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def recipe(self) -> Recipe:
        return PRESETS[self]


@dataclass(slots=True, frozen=True)
class Recipe:
    """A hand-set medium correction: four multipliers, no measurement."""

    saturation: float = 1.0
    contrast: float = 1.0
    brightness: float = 1.0
    gamma: float = 1.0  # < 1 darkens midtones → more ink density

    @property
    def neutral(self) -> bool:
        return (self.saturation, self.contrast, self.brightness, self.gamma) == (
            1.0,
            1.0,
            1.0,
            1.0,
        )

    def json(self) -> dict[str, float]:
        return {
            "saturation": self.saturation,
            "contrast": self.contrast,
            "brightness": self.brightness,
            "gamma": self.gamma,
        }

    @classmethod
    def read(cls, data: object) -> Recipe:
        """A recipe from untrusted JSON — anything unreadable stays neutral."""
        if not isinstance(data, dict):
            return cls()
        raw: dict[str, Any] = data
        return cls(
            saturation=_num(raw.get("saturation"), 1.0),
            contrast=_num(raw.get("contrast"), 1.0),
            brightness=_num(raw.get("brightness"), 1.0),
            gamma=_num(raw.get("gamma"), 1.0),
        )


PRESETS: dict[Preset, Recipe] = {
    Preset.NONE: Recipe(),
    Preset.PAPER: Recipe(saturation=1.02, contrast=1.02),
    # transparent plastic foil washes out hard → boost saturation + density
    Preset.FOIL: Recipe(saturation=1.38, contrast=1.16, brightness=0.95, gamma=0.88),
}

_LABELS: dict[Preset, str] = {
    Preset.NONE: "No correction",
    Preset.PAPER: "Plain paper",
    Preset.FOIL: "Transparent foil",
}

#: the fields of a recipe, for the CLI's and UI's editors
RECIPE_KEYS: tuple[str, ...] = ("saturation", "contrast", "brightness", "gamma")


def preset(name: str) -> Preset | None:
    """The built-in preset ``name`` refers to, or None if it is a real profile."""
    try:
        return Preset(name.strip().lower())
    except ValueError:
        return None


def compensate(im: Image.Image, recipe: Recipe) -> Image.Image:
    from proxdex.grade import gamma as apply_gamma

    im = im.convert("RGB")
    im = ImageEnhance.Brightness(im).enhance(recipe.brightness)
    im = ImageEnhance.Contrast(im).enhance(recipe.contrast)
    im = ImageEnhance.Color(im).enhance(recipe.saturation)
    return apply_gamma(im, recipe.gamma)


def _num(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    out = float(value)
    return default if not np.isfinite(out) else out
