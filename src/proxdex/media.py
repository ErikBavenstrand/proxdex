"""The hand-set half of a print profile: four multipliers, and how to judge them.

A profile can be defined two ways, and both are legitimate:

* **measured** — print the calibration chart, scan it, and let proxdex fit the
  correction (:mod:`proxdex.calibrate`). Needs a scanner.
* **by hand** — set these four numbers yourself. Needs eyes and a test print.

Nothing ships with numbers pre-filled. A recipe that came from nobody's printer
is not a starting point, it is a guess wearing a label: "foil needs saturation
1.38" was true of exactly one setup that nobody reading this owns. So a new
profile starts at identity — changing nothing — and you move it deliberately,
either by measuring or by looking.

:func:`vary` and the strip it feeds exist so the by-hand path is not blind: print
one page of the same card at a row of values, look at it, and set the one that
looks right.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance

#: the four multipliers of a hand-set correction, in the order they are applied
RECIPE_KEYS: tuple[str, ...] = ("saturation", "contrast", "brightness", "gamma")
#: sensible bounds for each — the same range the CLI and the API enforce
RECIPE_LOW, RECIPE_HIGH = 0.0, 3.0


@dataclass(slots=True, frozen=True)
class Recipe:
    """A hand-set medium correction: four multipliers, no measurement.

    Identity by default, because a correction nobody has verified should change
    nothing.
    """

    saturation: float = 1.0
    contrast: float = 1.0
    brightness: float = 1.0
    gamma: float = 1.0  # < 1 darkens midtones → more ink density

    @property
    def neutral(self) -> bool:
        return all(getattr(self, key) == 1.0 for key in RECIPE_KEYS)

    def json(self) -> dict[str, float]:
        return {key: float(getattr(self, key)) for key in RECIPE_KEYS}

    def text(self) -> str:
        return " · ".join(f"{k} {getattr(self, k):g}" for k in RECIPE_KEYS)

    def with_value(self, key: str, value: float) -> Recipe:
        if key not in RECIPE_KEYS:
            raise ValueError(f"{key!r} is not one of {', '.join(RECIPE_KEYS)}")
        return replace(self, **{key: value})

    @classmethod
    def read(cls, data: object) -> Recipe:
        """A recipe from untrusted JSON — anything unreadable stays neutral."""
        if not isinstance(data, dict):
            return cls()
        raw: dict[str, Any] = data
        return cls(**{key: _num(raw.get(key), 1.0) for key in RECIPE_KEYS})


def vary(recipe: Recipe, key: str, values: list[float]) -> list[tuple[str, Recipe]]:
    """The recipe at each of ``values`` for one knob, labelled with the value.

    One variable at a time: a page where two things changed tells you which page
    you like, not which number to write down.
    """
    return [(f"{key} {value:g}", recipe.with_value(key, value)) for value in values]


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
