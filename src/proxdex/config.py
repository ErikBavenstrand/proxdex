"""Configuration: dataclass defaults overlaid by ``<root>/proxdex.toml``.

The TOML may be flat or grouped into ``[sections]``. A key under a section is
matched to a field by trying the bare key first, then ``<section>_<key>`` —
so ``[grade] contrast`` sets ``grade_contrast`` and ``[print] profile`` sets
``print_profile``.

Every field whose values form a closed set is typed as a :class:`~enum.StrEnum`
(or a :data:`~typing.Literal`) and **coerced at load time**: a typo in
``proxdex.toml`` raises :class:`ConfigError` naming the valid options instead of
falling through a string comparison to a plausible-but-wrong default.
"""

from __future__ import annotations

import tomllib
from dataclasses import MISSING, dataclass, field, fields
from enum import Enum, IntEnum, StrEnum
from pathlib import Path
from typing import Any, Literal, get_args, get_origin, get_type_hints

from proxdex.errors import ConfigError
from proxdex.games import GameId

#: the file that both configures a library and marks the directory as one
MARKER = "proxdex.toml"


def setting(
    default: Any = MISSING,
    *,
    label: str,
    help: str,  # noqa: A002 — reads as documentation, and that is what it is
    unit: str = "",
    factory: Any = None,
) -> Any:
    """A config field that can describe itself.

    The label, explanation and unit live next to the value they document, and
    ``/api/config`` serves them — so the settings screen can be a real form with
    real prose instead of a list of raw TOML keys, and there is exactly one place
    to edit when a setting's meaning changes.
    """
    meta = {"label": label, "help": help, "unit": unit}
    if factory is not None:
        return field(default_factory=factory, metadata=meta)
    return field(default=default, metadata=meta)


class PageSize(StrEnum):
    A4 = "a4"
    LETTER = "letter"


class Orientation(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


class Fit(StrEnum):
    """How a trim master maps into the exact card cell at sheet time."""

    #: fill the cell preserving aspect (default; shaves only sub-pixel overflow)
    COVER = "cover"
    #: fit the whole image, pad with white
    CONTAIN = "contain"
    #: force the exact size — re-introduces the distortion `border` removes
    STRETCH = "stretch"


class Faces(StrEnum):
    FRONTS = "fronts"
    BACKS = "backs"
    DUPLEX = "duplex"


class DuplexFlip(StrEnum):
    """The edge the printer flips on, which decides how backs are mirrored."""

    LONG = "long"
    SHORT = "short"


class GuideStyle(StrEnum):
    FULL = "full"  # grid lines across the page
    CORNERS = "corners"  # crop marks at each card corner
    NONE = "none"


class GuidePlacement(StrEnum):
    OUTSIDE = "outside"
    INSIDE = "inside"


class RegMarks(StrEnum):
    NONE = "none"
    CORNERS = "corners"


class UpscaylScale(IntEnum):
    """Upscayl's output scale; the app offers exactly these."""

    X1 = 1
    X2 = 2
    X3 = 3
    X4 = 4


class UpscaylModel(StrEnum):
    """Upscayl's built-in models, in the app's own order (the ``-n`` literals).

    A closed set on purpose: a mistyped model name would otherwise reach
    ``upscayl-bin`` and fail there, per card, halfway through a batch.
    """

    UPSCAYL_STANDARD_4X = "upscayl-standard-4x"
    UPSCAYL_LITE_4X = "upscayl-lite-4x"
    HIGH_FIDELITY_4X = "high-fidelity-4x"
    REMACRI_4X = "remacri-4x"
    ULTRAMIX_BALANCED_4X = "ultramix-balanced-4x"
    ULTRASHARP_4X = "ultrasharp-4x"
    DIGITAL_ART_4X = "digital-art-4x"

    @property
    def native_scale(self) -> UpscaylScale:
        """The scale the model was trained at, read from its id — the app's
        ``getModelScale``; it omits ``-s`` when the request matches this."""
        name = self.value.lower()
        for scale in (UpscaylScale.X2, UpscaylScale.X3):
            if f"x{scale.value}" in name or f"{scale.value}x" in name:
                return scale
        return UpscaylScale.X4


@dataclass(slots=True)
class Config:
    # --- which game a bare `search`/`fetch` means (cards record their own) ---
    library_game: GameId = setting(
        GameId.POKEMON,
        label="Default game",
        help="Which game a search or a bare card id means. Every card also "
        "records its own, so one library can hold both.",
    )
    # --- sources: Pokémon (metadata + images come from different hosts) ------
    scrydex_url: str = setting(
        "https://images.scrydex.com/pokemon/{id}/large",
        label="Pokémon image URL",
        help="Where Pokémon card scans are downloaded from. {id} is the card id.",
    )
    api_url: str = setting(
        "https://api.pokemontcg.io/v2/cards/{id}",
        label="Pokémon data API",
        help="Where Pokémon names, sets and rules text come from.",
    )
    # --- sources: MTG (Scryfall serves metadata and images) ------------------
    mtg_api_url: str = setting(
        "https://api.scryfall.com",
        label="Magic API",
        help="Scryfall serves both metadata and images for Magic cards.",
    )
    # --- card geometry (mm); embedded DPI is never trusted -------------------
    #: both supported games print at the standard 63×88mm, so this is global
    card_w_mm: float = setting(
        63.0,
        label="Card width",
        help="Finished trim width. Both games print at 63×88 mm.",
        unit="mm",
    )
    card_h_mm: float = setting(
        88.0, label="Card height", help="Finished trim height.", unit="mm"
    )
    bleed_mm: float = setting(
        2.5,
        label="Cut bleed",
        help="Extra image extended outside the trim on every edge, so a slightly "
        "off cut still lands on artwork. Added at sheet time, never baked in.",
        unit="mm",
    )
    # --- sheet imposition (proxdex owns the print PDF) -----------------------
    sheet_page: PageSize = setting(
        PageSize.A4, label="Paper size", help="The sheet you print on."
    )
    sheet_orientation: Orientation = setting(
        Orientation.PORTRAIT, label="Orientation", help="How the paper is fed."
    )
    #: page render resolution; high so the printer/driver never upsamples
    sheet_dpi: int = setting(
        1400,
        label="Render resolution",
        help="How finely the sheet is rendered. High enough that the printer "
        "driver never has to upsample your cards.",
        unit="dpi",
    )
    #: cover is the default — the border step (cardbleed) already produces an
    #: exactly-63:88 master, so cover scales it into the cell with no crop and no
    #: distortion; it only ever shaves a sub-pixel overflow. Never stretch here:
    #: that re-introduces the print-time rescale the pipeline exists to avoid.
    sheet_fit: Fit = setting(
        Fit.COVER,
        label="Fit to cell",
        help="A bordered master is already exactly card-shaped, so cover neither "
        "crops nor distorts it. Stretch re-introduces the distortion the border "
        "step exists to remove.",
    )
    sheet_cols: int = setting(3, label="Columns", help="Cards across the page.")
    sheet_rows: int = setting(3, label="Rows", help="Cards down the page.")
    sheet_margin_mm: float = setting(
        5.0,
        label="Page margin",
        help="Unprintable edge to keep clear.",
        unit="mm",
    )
    sheet_spacing_mm: float = setting(
        0.0,
        label="Horizontal gap",
        help="Gap between cards across. Zero means shared cut lines — less paper, "
        "one cut per column.",
        unit="mm",
    )
    sheet_spacing_y_mm: float = setting(
        0.0, label="Vertical gap", help="Gap between cards down.", unit="mm"
    )
    # faces & duplex
    sheet_faces: Faces = setting(
        Faces.FRONTS,
        label="What to print",
        help="Fronts only, backs only, or duplex — fronts and backs interleaved "
        "so you can print both sides in one pass.",
    )
    sheet_duplex_flip: DuplexFlip = setting(
        DuplexFlip.LONG,
        label="Duplex flip edge",
        help="Which edge your printer flips the paper on. Wrong here and the "
        "backs land mirrored.",
    )
    sheet_back_image: str = setting(
        "",
        label="Shared card back",
        help="Path to a back image for every card. A per-card <id>_back.png, and "
        "a two-sided card's own reverse, both win over this.",
    )
    # offsets (mm) to align print vs cut, and front vs back on duplex
    sheet_front_offset_x_mm: float = setting(
        0.0,
        label="Front offset X",
        help="Nudge every front sideways to match where your printer actually "
        "lays ink down.",
        unit="mm",
    )
    sheet_front_offset_y_mm: float = setting(
        0.0, label="Front offset Y", help="Nudge every front up or down.", unit="mm"
    )
    sheet_back_offset_x_mm: float = setting(
        0.0,
        label="Back offset X",
        help="Nudge the backs to line up with the fronts on a duplex print.",
        unit="mm",
    )
    sheet_back_offset_y_mm: float = setting(
        0.0, label="Back offset Y", help="Nudge the backs up or down.", unit="mm"
    )
    # cut guides
    sheet_guides: bool = setting(
        default=True, label="Print cut guides", help="Draw where to cut."
    )
    sheet_guide_style: GuideStyle = setting(
        GuideStyle.CORNERS,
        label="Guide style",
        help="Corner crop marks, or full grid lines across the page.",
    )
    sheet_guide_placement: GuidePlacement = setting(
        GuidePlacement.OUTSIDE,
        label="Guide placement",
        help="Outside the trim keeps marks off the card; inside puts them on it.",
    )
    sheet_guide_mm: float = setting(
        4.0, label="Guide length", help="How long each crop mark is.", unit="mm"
    )
    sheet_guide_color: str = setting(
        "#00ff00",
        label="Guide colour",
        help="A colour no card uses, so the marks are easy to see and to ignore.",
    )
    sheet_guide_width_mm: float = setting(
        0.3, label="Guide thickness", help="Line weight of the marks.", unit="mm"
    )
    sheet_guides_front: bool = setting(
        default=True,
        label="Guides on fronts",
        help="Cut from the front, so usually yes.",
    )
    sheet_guides_back: bool = setting(
        default=False,
        label="Guides on backs",
        help="Rarely useful — you cut from one side.",
    )
    # registration marks (printer front/back alignment)
    sheet_reg_marks: RegMarks = setting(
        RegMarks.NONE,
        label="Registration marks",
        help="Corner targets for measuring how far your printer's backs drift "
        "from its fronts.",
    )
    sheet_reg_inset_mm: float = setting(
        10.0,
        label="Registration inset",
        help="How far in from the page corner the targets sit.",
        unit="mm",
    )
    sheet_open: bool = setting(
        default=False,
        label="Open the PDF",
        help="Open each sheet as soon as it's written.",
    )
    # --- border: how the reshape hits the frame spec -------------------------
    border_stretch: bool = setting(
        default=True,
        label="Stretch to hit the borders exactly",
        help="Un-distort the art so the finished borders land on the frame spec "
        "instead of as close as the source allows. On by default — the spec is "
        "the point of the step.",
    )
    # --- grade: one creative look, applied identically to every card ---------
    # Grade is a *look*, nothing else. It does not try to guess what a card
    # "should" look like: card frames are deliberately different colours between
    # games and eras, so there is no common baseline to pull them to. Matching the
    # medium is a print-time job, done by a profile at sheet time.
    grade_brightness: float = setting(
        1.03,
        label="Brightness",
        help="Applied identically to every card. Paper and ink dull an image, so "
        "the defaults lift it slightly.",
    )
    grade_contrast: float = setting(1.06, label="Contrast", help="1.0 leaves it be.")
    grade_saturation: float = setting(
        1.10, label="Saturation", help="1.0 leaves it be."
    )
    grade_gamma: float = setting(
        1.0, label="Gamma", help="Below 1 darkens the midtones."
    )
    #: how hard to pull the card's own black/white points to full range
    grade_levels: float = setting(
        0.0,
        label="Auto levels",
        help="Stretch this card's own darkest and brightest pixels to full range, "
        "blended by this much. 0 is off; 0.5 helps a flat, hazy scan. It reads the "
        "card's own tones only — it never compares one card to another.",
    )
    # --- print: which profile corrects for the medium at sheet time ----------
    #: the name of a profile in `<root>/profiles/`, or a built-in
    #: :class:`proxdex.media.Preset` — so this stays an open set.
    print_profile: str = setting(
        "none",
        label="Print profile",
        help="Which medium the sheet is corrected for. A built-in preset (none, "
        "paper, foil) is a starting point; a profile you calibrated carries a "
        "measured correction and your own notes.",
    )
    # --- external tools ------------------------------------------------------
    #: upscayl-bin path; "" = auto-detect (bundled macOS app, then PATH)
    upscayl_bin: str = setting(
        "",
        label="Upscayl binary",
        help="Leave empty to find it automatically — the installed app first, "
        "then your PATH.",
    )
    #: Upscayl models folder; "" = auto-detect
    upscayl_models: str = setting(
        "",
        label="Upscayl models folder",
        help="Leave empty to find it alongside the binary.",
    )
    upscayl_model: UpscaylModel = setting(
        UpscaylModel.DIGITAL_ART_4X,
        label="Upscale model",
        help="Which network the upscale step runs by default. digital-art suits "
        "card art; remacri and ultrasharp favour photographic scans.",
    )
    upscayl_scale: UpscaylScale = setting(
        UpscaylScale.X2,
        label="Upscale factor",
        help="How much to enlarge in one pass.",
    )
    #: "double upscayl" — run the model twice (2x doubled → 4x, up to 16x)
    upscayl_double: bool = setting(
        default=True,
        label="Double upscale",
        help="Run the model twice, so 2× becomes 4×. Slower, and sharper on small "
        "sources.",
    )

    @classmethod
    def field_name(cls, section: str, key: str) -> str | None:
        """The field a ``[section] key`` sets: bare key first, then prefixed.

        The one place that rule lives — :meth:`load` and the web UI's config
        editor both ask here, so they can never disagree about a key.
        """
        known = {fld.name for fld in fields(cls)}
        for candidate in (key, f"{section}_{key}"):
            if candidate in known:
                return candidate
        return None

    @classmethod
    def describe(cls) -> dict[str, dict[str, str]]:
        """Every documented field → its label, explanation, unit and default.

        The default is read back off the dataclass, so the help text can never
        claim a default the code does not have.
        """
        out: dict[str, dict[str, str]] = {}
        for fld in fields(cls):
            label = fld.metadata.get("label")
            if not label:
                continue
            default = fld.default
            out[fld.name] = {
                "label": str(label),
                "help": str(fld.metadata.get("help", "")),
                "unit": str(fld.metadata.get("unit", "")),
                "default": ""
                if default is MISSING
                else str(default.value if isinstance(default, Enum) else default),
            }
        return out

    @classmethod
    def coerce(cls, name: str, value: Any) -> Any:
        """One raw value (TOML, JSON) as field ``name``'s declared type."""
        return _coerce(name, get_type_hints(cls), value)

    @classmethod
    def load(cls, root: Path) -> Config:
        cfg = cls()
        f = root / MARKER
        if not f.exists():
            return cfg
        known = {fld.name for fld in fields(cls)}
        try:
            raw = tomllib.loads(f.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{f} is not valid TOML: {exc}") from exc
        for key, value in raw.items():
            if isinstance(value, dict):
                for sub, subval in value.items():
                    field_name = cls.field_name(key, sub)
                    if field_name is not None:
                        setattr(cfg, field_name, cls.coerce(field_name, subval))
            elif key in known:
                setattr(cfg, key, cls.coerce(key, value))
        return cfg

    def px_per_mm(self, image_w: int) -> float:
        return image_w / self.card_w_mm


def _coerce(name: str, hints: dict[str, Any], value: Any) -> Any:
    """Turn one raw TOML value into the field's declared type, or explain why
    it can't be — so bad config fails loudly at load, not subtly at print."""
    hint = hints.get(name)
    if isinstance(hint, type) and issubclass(hint, Enum):
        # a StrEnum takes the lowercased text, an IntEnum the number — and TOML
        # can spell either as the other ("2" vs 2), so try both spellings
        text = str(value).strip().lower()
        candidates: list[Any] = [value, text]
        if text.lstrip("-").isdigit():
            candidates.append(int(text))
        for candidate in candidates:
            try:
                return hint(candidate)
            except ValueError:
                continue
        raise ConfigError(f"{name}: {value!r} — {_options(hint)}")
    if get_origin(hint) is Literal:
        allowed = get_args(hint)
        if value in allowed:
            return value
        opts = ", ".join(repr(a) for a in allowed)
        raise ConfigError(f"{name}: {value!r} — expected one of {opts}")
    if hint in (int, float, bool) and isinstance(value, (int, float)):
        return hint(value)
    return value


def _options(enum_cls: type[Enum]) -> str:
    return "expected one of " + ", ".join(repr(m.value) for m in enum_cls)
