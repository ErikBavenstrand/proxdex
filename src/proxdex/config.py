"""Configuration: dataclass defaults overlaid by ``<root>/proxdex.toml``.

The TOML may be flat or grouped into ``[sections]``. A key under a section is
matched to a field by trying the bare key first, then ``<section>_<key>`` —
so ``[grade] contrast`` sets ``grade_contrast`` and ``[border]
target_side_ratio`` sets ``target_side_ratio``.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass(slots=True)
class Config:
    # --- sources -------------------------------------------------------------
    scrydex_url: str = "https://images.scrydex.com/pokemon/{id}/large"
    api_url: str = "https://api.pokemontcg.io/v2/cards/{id}"
    # --- card geometry (mm); embedded DPI is never trusted -------------------
    card_w_mm: float = 63.0
    card_h_mm: float = 88.0
    bleed_mm: float = 2.5
    # --- border detection / correction --------------------------------------
    border_thresh: float = 62.0
    #: side border / card width; 0 = auto (match the thickest measured side)
    target_side_ratio: float = 0.0
    #: top border / card height; 0 = auto (match the sides)
    target_top_ratio: float = 0.0
    # --- grade recipe (applied identically to every card) --------------------
    grade_brightness: float = 1.03
    grade_contrast: float = 1.06
    grade_saturation: float = 1.10
    grade_gamma: float = 1.0
    #: normalize each card's frame to this [r, g, b]; [] = off
    match_border_target: list[int] = field(default_factory=list)
    # --- external tools ------------------------------------------------------
    cardbleed_cmd: str = "cardbleed"
    #: upscayl-bin path; "" = auto-detect (bundled macOS app, then PATH)
    upscayl_bin: str = ""
    #: Upscayl models folder; "" = auto-detect
    upscayl_models: str = ""
    upscayl_model: str = "digital-art-4x"
    upscayl_scale: int = 2
    #: "double upscayl" — run the model twice (2x doubled → 4x, up to 16x)
    upscayl_double: bool = False

    @classmethod
    def load(cls, root: Path) -> Config:
        cfg = cls()
        f = root / "proxdex.toml"
        if not f.exists():
            return cfg
        known = {fld.name for fld in fields(cls)}
        for key, value in tomllib.loads(f.read_text()).items():
            if isinstance(value, dict):
                for sub, subval in value.items():
                    for candidate in (sub, f"{key}_{sub}"):
                        if candidate in known:
                            setattr(cfg, candidate, subval)
                            break
            elif key in known:
                setattr(cfg, key, value)
        return cfg

    def px_per_mm(self, image_w: int) -> float:
        return image_w / self.card_w_mm
