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
    # --- sheet imposition (proxdex owns the print PDF) -----------------------
    sheet_page: str = "a4"  # a4 | letter
    sheet_orientation: str = "portrait"  # portrait | landscape
    sheet_dpi: int = 600
    #: how any-size input is scaled to the exact card size: cover | contain | stretch
    sheet_fit: str = "cover"
    sheet_cols: int = 3
    sheet_rows: int = 3
    sheet_margin_mm: float = 5.0
    sheet_spacing_mm: float = 0.0  # x spacing
    sheet_spacing_y_mm: float = 0.0
    # faces & duplex
    sheet_faces: str = "fronts"  # fronts | backs | duplex
    sheet_duplex_flip: str = "long"  # long | short print-flip edge (mirrors backs)
    sheet_back_image: str = ""  # shared card back; <card>/<id>_back.png overrides
    # offsets (mm) to align print vs cut, and front vs back on duplex
    sheet_front_offset_x_mm: float = 0.0
    sheet_front_offset_y_mm: float = 0.0
    sheet_back_offset_x_mm: float = 0.0
    sheet_back_offset_y_mm: float = 0.0
    # cut guides
    sheet_guides: bool = True
    sheet_guide_style: str = "corners"  # full | corners | none
    sheet_guide_placement: str = "outside"  # outside | inside
    sheet_guide_mm: float = 4.0  # tick / crop-mark length
    sheet_guide_color: str = "#00ff00"
    sheet_guide_width_mm: float = 0.3
    sheet_guides_front: bool = True
    sheet_guides_back: bool = False
    # registration marks (printer front/back alignment)
    sheet_reg_marks: str = "none"  # none | corners
    sheet_reg_inset_mm: float = 10.0
    sheet_open: bool = False  # open the PDF after writing
    # --- border detection / correction --------------------------------------
    border_thresh: float = 62.0
    #: side border / card width; 0 = auto (match the thickest measured side)
    target_side_ratio: float = 0.0
    #: top border / card height; 0 = auto (match the sides)
    target_top_ratio: float = 0.0
    # --- grade: normalize (per-card, dynamic) then look (uniform) ------------
    #: pull every card to a common baseline before the creative recipe
    grade_normalize: bool = True
    #: black/white points for auto-levels, as luminance percentiles
    grade_black_pct: float = 0.5
    grade_white_pct: float = 99.5
    #: how hard to pull toward the stretched levels (0 = off, 1 = full)
    grade_level_strength: float = 0.6
    #: frame white-balance target [r, g, b]; [] = use the library's median frame
    match_border_target: list[int] = field(default_factory=list)
    # --- grade: the creative look (applied identically to every card) --------
    grade_brightness: float = 1.03
    grade_contrast: float = 1.06
    grade_saturation: float = 1.10
    grade_gamma: float = 1.0
    # --- print / media compensation (baked into stage 4) --------------------
    #: media profile: "none" | "paper" | "foil" (see proxdex.media.PROFILES)
    print_profile: str = "none"
    #: per-value overrides of the active profile; None = use the profile default
    print_saturation: float | None = None
    print_contrast: float | None = None
    print_brightness: float | None = None
    print_gamma: float | None = None
    # --- external tools ------------------------------------------------------
    cardbleed_cmd: str = "cardbleed"
    #: upscayl-bin path; "" = auto-detect (bundled macOS app, then PATH)
    upscayl_bin: str = ""
    #: Upscayl models folder; "" = auto-detect
    upscayl_models: str = ""
    upscayl_model: str = "digital-art-4x"
    upscayl_scale: int = 2
    #: "double upscayl" — run the model twice (2x doubled → 4x, up to 16x)
    upscayl_double: bool = True

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
