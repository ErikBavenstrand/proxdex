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
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, field, fields
from enum import Enum, IntEnum, StrEnum
from pathlib import Path
from typing import Any, Literal, get_args, get_origin, get_type_hints

from proxdex.errors import ConfigError
from proxdex.games import CARD_H_MM, CARD_W_MM, GameId

#: the file that both configures a library and marks the directory as one
MARKER = "proxdex.toml"


class Run(StrEnum):
    """A surface where a setting can be overridden **for one run**.

    A library's settings are its defaults; a job is this paper on this printer today.
    So the sheet builder and ``proxdex sheet`` change the run and never the file — and
    which settings that covers is declared **here, once**, on the settings themselves.

    Before this it was declared four times over: a hand-written ``click.option``, a
    field on the request body, a line in the CLI's override helper and another in the
    web layer's. Twenty of the settings a print run reads had been added
    to the config and to none of those four, so the page they belong to could not
    touch them — the front/back offsets among them, which are the ones you reach for
    with a misregistered duplex sheet in your hand.
    """

    #: a print run: everything ``sheet`` reads about paper, grid, guides and marks
    SHEET = "sheet"


def setting(
    default: Any = MISSING,
    *,
    label: str,
    help: str,  # noqa: A002 — reads as documentation, and that is what it is
    unit: str = "",
    factory: Any = None,
    run: Run | None = None,
    low: float | None = None,
    high: float | None = None,
    flag: str = "",
    group: str = "",
    auto: str = "",
) -> Any:
    """A config field that can describe itself.

    The label, explanation and unit live next to the value they document, and
    ``/api/config`` serves them — so the settings screen can be a real form with
    real prose instead of a list of raw TOML keys, and there is exactly one place
    to edit when a setting's meaning changes.

    ``run`` says this setting may be overridden for one job on the page that uses it
    (see :class:`Run`), which is what makes the CLI flag, the request field, the
    validation and the UI control all derive from this line instead of restating it.
    ``low``/``high`` bound a numeric override — proxdex's own guard against a typo
    becoming a 4800mm margin, not a claim about what the maths can take. ``group``
    is the heading the page files it under: twenty-seven controls in one list is a
    wall, and which heading a setting belongs to is a fact about the setting.
    ``auto`` is what leaving an **optional** setting unset means, in words — required
    for a field declared ``T | None``, because "unset" is then an answer of its own and
    a control that does not say which answer is a blank box.
    """
    meta: dict[str, Any] = {"label": label, "help": help, "unit": unit}
    if run is not None:
        meta["run"] = run.value
    if low is not None:
        meta["low"] = low
    if high is not None:
        meta["high"] = high
    if flag:
        meta["flag"] = flag
    if group:
        meta["group"] = group
    if auto:
        meta["auto"] = auto
    if factory is not None:
        return field(default_factory=factory, metadata=meta)
    return field(default=default, metadata=meta)


class OptKind(StrEnum):
    """What an override takes, which is what a CLI flag and a UI control must honour.

    Derived from the field's declared type rather than restated, so a setting that
    changes type cannot keep an control that no longer fits it.
    """

    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    TEXT = "text"
    CHOICE = "choice"


@dataclass(frozen=True, slots=True)
class RunOption:
    """One setting, as the page that uses it offers it for a single run.

    Everything here is read off the :func:`setting` that declared it — see
    :meth:`Config.run_options`. ``None`` for the value of an override means "use the
    library's setting", which is the state every control has to be able to be in: the
    point is to change one number for one job, not to restate a page of settings.
    """

    key: str
    #: the CLI spelling, without the leading dashes (`sheet_front_offset_x_mm` →
    #: `front-offset-x`)
    flag: str
    label: str
    help: str
    unit: str
    kind: OptKind
    #: the enum that *is* this setting's closed set, so the values are not restated
    enum: type[Enum] | None
    default: Any
    #: the heading the page files this control under
    group: str = ""
    low: float | None = None
    high: float | None = None
    #: this setting is declared ``T | None`` — its unset state is an answer, not a gap
    optional: bool = False
    #: what unset *means* for this one setting, in words. Carried per option because it
    #: differs per option, which is the lesson :class:`steps.StepOption` already
    #: learned: the UI printed one setting's wording over every "automatic" control.
    auto: str = ""

    @property
    def choices(self) -> tuple[str, ...]:
        return tuple(str(m.value) for m in self.enum) if self.enum else ()

    @property
    def default_text(self) -> str:
        """The library default as a person reads it — never blank for a bool, which
        would render as "no default" beside a checkbox that plainly has one."""
        if self.default is MISSING or self.default is None:
            return ""
        if isinstance(self.default, Enum):
            return str(self.default.value)
        if isinstance(self.default, bool):
            return "on" if self.default else "off"
        return str(self.default)

    def json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "flag": self.flag,
            "label": self.label,
            "help": self.help,
            "unit": self.unit,
            "kind": self.kind.value,
            "group": self.group,
            "choices": list(self.choices),
            "default": self.default_text,
            "low": self.low,
            "high": self.high,
            "optional": self.optional,
            "auto": self.auto,
        }


def apply_run(cfg: Config, run: Run, values: Mapping[str, Any]) -> None:
    """Apply one run's overrides to a loaded config, in place. **Never written.**

    The one implementation, called by ``proxdex sheet`` and by the web layer's plan
    route alike. It matters that it is one and not two: the plan and the print have to
    be configured identically or the page count the sheet builder promises is not the
    page count the PDF has, and `sheet.plan` exists precisely so those cannot differ.
    A key that is absent, ``None`` or empty means "the library's setting" — the state
    every control starts in.
    """
    for opt in Config.run_options(run):
        value = values.get(opt.key)
        if value is None or value == "":
            continue
        setattr(cfg, opt.key, Config.coerce(opt.key, value))


def bad_run_value(run: Run, values: Mapping[str, Any]) -> str:
    """The first thing wrong with a set of overrides, or ``""``.

    Checked against the declaration at whichever boundary they arrived at, so an
    unknown key is refused rather than silently dropped on the way to argv, and a
    number outside its bounds is refused by the API *and* by click — the two enforce
    the same range because they read the same one.
    """
    known = {o.key: o for o in Config.run_options(run)}
    for key, value in values.items():
        opt = known.get(key)
        if opt is None:
            return f"{key} is not a page setting for a print run"
        if value is None or value == "":
            continue
        if opt.choices and str(value) not in opt.choices:
            return f"{key}: {value!r} is not one of {', '.join(opt.choices)}"
        if opt.kind in {OptKind.INT, OptKind.FLOAT}:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return f"{key}: {value!r} is not a number"
            bounded = opt.low is not None and opt.high is not None
            if bounded and not opt.low <= number <= opt.high:  # type: ignore[operator]
                return f"{key}: {number:g} is outside {opt.low:g}..{opt.high:g}"
        try:
            Config.coerce(key, value)
        except ConfigError as exc:
            return f"{key}: {exc}"
    return ""


def optional_of(declared: Any) -> Any:
    """``T`` for a field declared ``T | None``, else ``None``.

    An optional setting is one whose **unset state is an answer of its own**, not a
    missing value: the back-of-the-sheet cut guides are unset by default and that means
    "whatever the fronts do". Exactly the shape :class:`steps.StepOption` already has,
    where "unset" means the frame the card's set resolves to rather than no frame — and
    for the same reason it is a declared type here rather than a sentinel number: the
    enum stays closed, the millimetres stay floats, and every reader learns from the
    annotation that ``None`` is a value it has to answer for.
    """
    args = get_args(declared)
    if len(args) != 2 or type(None) not in args:
        return None
    return next(a for a in args if a is not type(None))


def _kind_of(declared: Any) -> OptKind:
    inner = optional_of(declared)
    if inner is not None:
        declared = inner
    if isinstance(declared, type) and issubclass(declared, Enum):
        return OptKind.CHOICE
    if declared is bool:
        return OptKind.BOOL
    if declared is int:
        return OptKind.INT
    if declared is float:
        return OptKind.FLOAT
    return OptKind.TEXT


def _flag_for(name: str) -> str:
    """A field name as its CLI flag: drop the section prefix and the unit suffix.

    ``sheet_front_offset_x_mm`` → ``front-offset-x``, ``bleed_mm`` → ``bleed``. Derived
    rather than listed because a second spelling is a second thing to keep in step —
    and it reproduces all eight flags `sheet` had before any of this, which
    `tests/test_run_overrides.py` pins so no existing command line changes meaning.
    """
    stem = name.removeprefix("sheet_").removesuffix("_mm")
    return stem.replace("_", "-")


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
    """Where the cut guides for one side of the paper are drawn.

    Only two answers, because **how far a mark reaches is a different question** — see
    :class:`GuideReach`. ``CORNERS`` never puts ink on a card (beyond the deliberate
    ``guide_cross_mm``); ``FULL`` draws the trim lines straight across and over them,
    which is only useful when the cards have bleed you are about to cut off anyway.
    """

    CORNERS = "corners"  # marks at each card's cut corners, outside the card
    FULL = "full"  # trim lines straight across the page, over the cards
    NONE = "none"


class GuideReach(StrEnum):
    """How far a corner mark's arms run away from the cut corner.

    Separate from :class:`GuideStyle` because it is a separate decision, and conflating
    the two is what the mtg-jumpstart-dividers generator did in both directions: its
    first version drew fixed-length ticks and its rewrite drew unbroken lines to the
    sheet edge, dropping the length setting rather than offering both. There are three
    useful answers and no style can hold them all.
    """

    #: the configured length and no further — a tick at the corner
    FIXED = "fixed"
    #: as far as the neighbouring card's near edge, so the gap between two cards is
    #: bridged into one line; the configured length where there is no neighbour, so the
    #: outer margin stays clean
    JOIN = "join"
    #: the same, but with no neighbour it runs on to the edge of the paper — what a
    #: rotary trimmer wants, since you line its blade up on the sheet edge
    PAPER = "paper"


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
    #: **A name, not an enum** — the same shape ``print_profile`` has, and for the
    #: same reason: a library can define its own games (``<root>/games/<id>.json``),
    #: so the set of legal values is not knowable at load time. Coercing an unknown
    #: one into an enum here would either raise on a game that exists or silently
    #: rewrite it to Pokémon. A name nothing answers to is instead *reported*, by
    #: :func:`proxdex.games.dangling` and ``proxdex where`` — exactly as a dangling
    #: print profile is.
    library_game: str = setting(
        GameId.POKEMON.value,
        label="Default game",
        help="Which game a search or a bare card id means. Every card also "
        "records its own, so one library can hold several.",
    )
    # --- sources: Pokémon (metadata + images come from different hosts) ------
    scrydex_url: str = setting(
        "https://images.scrydex.com/pokemon/{id}/large",
        label="Pokémon image URL",
        help="Where Pokémon card scans are downloaded from. {id} is the card id.",
    )
    scrydex_thumb_url: str = setting(
        "https://images.scrydex.com/pokemon/{id}/small",
        label="Pokémon thumbnail URL",
        help="Where the small scan behind a search or browse tile comes from. "
        "Never what gets filed — that is always the full image above.",
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
    #: both games print at the same 2.5×3.5in poker size, so this is global.
    #:
    #: **Deliberately not a per-run override** (no ``run=``), unlike everything else a
    #: print run reads. This is not a page setting: it is the size every stored master
    #: was *fitted* to, by `border`, against a frame spec whose millimetres are
    #: fractions of it. Overriding it for one run would impose cards at a size nothing
    #: was fitted at — `sheet.fit`'s `cover` would crop the difference off two edges —
    #: and it would look perfect on screen, which is the failure mode this whole area
    #: is careful about. A library that prints a different card size is a different
    #: library, with its own `proxdex.toml`.
    card_w_mm: float = setting(
        CARD_W_MM,
        label="Card width",
        help="Finished trim width. Both games print at 2.5×3.5in — 63.5×88.9 mm — "
        "which is also the card a frame spec's millimetres are measured against.",
        unit="mm",
    )
    card_h_mm: float = setting(
        CARD_H_MM, label="Card height", help="Finished trim height.", unit="mm"
    )
    #: **1.5 and not 2.5, because the arithmetic has to close.** Three columns of a
    #: 63.5mm card cost ``190.5 + 6 × bleed`` mm, so at 2.5 the grid is 205.5mm — that
    #: is 2.25mm from each edge of A4, which no real printer can reach, and it ran
    #: 0.51mm off the paper once the 5mm margin was applied. At 1.5 it is 199.5mm and
    #: clears 5.25mm a side on the default 3×3, so a fresh library prints on the paper
    #: it says it does. A *sheet-time* number — nothing stored depends on it (see
    #: `sheet.cell_mm`) — so it costs 1mm of waste an edge and changes no master. For
    #: scale, mtg-jumpstart-dividers ships 1mm of bleed with a 2mm gutter.
    bleed_mm: float = setting(
        1.5,
        label="Cut bleed",
        help="Extra image extended outside the trim on every edge, so a slightly "
        "off cut still lands on artwork. Added at sheet time, never baked in.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=20,
        group="Paper & grid",
    )
    # --- sheet imposition (proxdex owns the print PDF) -----------------------
    sheet_page: PageSize = setting(
        PageSize.A4,
        label="Paper size",
        help="The sheet you print on.",
        run=Run.SHEET,
        group="Paper & grid",
    )
    sheet_orientation: Orientation = setting(
        Orientation.PORTRAIT,
        label="Orientation",
        help="How the paper is fed.",
        run=Run.SHEET,
        group="Paper & grid",
    )
    #: page render resolution; high so the printer/driver never upsamples
    sheet_dpi: int = setting(
        1400,
        label="Render resolution",
        help="How finely the sheet is rendered. High enough that the printer "
        "driver never has to upsample your cards.",
        unit="dpi",
        run=Run.SHEET,
        low=72,
        high=4800,
        group="Paper & grid",
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
        run=Run.SHEET,
        group="Paper & grid",
    )
    sheet_cols: int = setting(
        3,
        label="Columns",
        help="Cards across the page.",
        run=Run.SHEET,
        low=1,
        high=12,
        group="Paper & grid",
    )
    sheet_rows: int = setting(
        3,
        label="Rows",
        help="Cards down the page.",
        run=Run.SHEET,
        low=1,
        high=12,
        group="Paper & grid",
    )
    sheet_margin_mm: float = setting(
        5.0,
        label="Page margin",
        help="How close to the paper's edge anything may be printed — your printer's "
        "unprintable border. The grid is centred inside what is left, and you are "
        "told when it does not fit rather than having a row silently cut off.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=50,
        group="Paper & grid",
    )
    # A printer's unprintable border is rarely the same on all four edges — 4mm at the
    # sides and 5mm at the top is an ordinary inkjet, and many are worse at the bottom
    # where the paper leaves the rollers. One number cannot say that, so each edge may
    # override it and unset means the number above.
    sheet_margin_top_mm: float | None = setting(
        None,
        label="Margin · top",
        help="Overrides the page margin for this edge alone.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=50,
        group="Paper & grid",
        auto="the page margin",
    )
    sheet_margin_right_mm: float | None = setting(
        None,
        label="Margin · right",
        help="Overrides the page margin for this edge alone.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=50,
        group="Paper & grid",
        auto="the page margin",
    )
    sheet_margin_bottom_mm: float | None = setting(
        None,
        label="Margin · bottom",
        help="Overrides the page margin for this edge alone. Often the worst edge — "
        "it is the one the paper is still gripped at.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=50,
        group="Paper & grid",
        auto="the page margin",
    )
    sheet_margin_left_mm: float | None = setting(
        None,
        label="Margin · left",
        help="Overrides the page margin for this edge alone.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=50,
        group="Paper & grid",
        auto="the page margin",
    )
    sheet_spacing_mm: float = setting(
        0.0,
        label="Horizontal gap",
        help="Gap between cards across. Zero means shared cut lines — less paper, "
        "one cut per column.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=50,
        group="Paper & grid",
    )
    sheet_spacing_y_mm: float = setting(
        0.0,
        label="Vertical gap",
        help="Gap between cards down.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=50,
        group="Paper & grid",
    )
    # faces & duplex
    sheet_faces: Faces = setting(
        Faces.FRONTS,
        label="What to print",
        help="Fronts only, backs only, or duplex — fronts and backs interleaved "
        "so you can print both sides in one pass.",
        run=Run.SHEET,
        group="What to print",
    )
    sheet_duplex_flip: DuplexFlip = setting(
        DuplexFlip.LONG,
        label="Duplex flip edge",
        help="Which edge your printer flips the paper on. Wrong here and the "
        "backs land mirrored.",
        run=Run.SHEET,
        group="What to print",
    )
    sheet_back_image: str = setting(
        "",
        label="Shared card back",
        help="Path to a back image for every card. A per-card <id>_back.png, and "
        "a two-sided card's own reverse, both win over this.",
        run=Run.SHEET,
        group="What to print",
    )
    # offsets (mm) to align print vs cut, and front vs back on duplex
    sheet_front_offset_x_mm: float = setting(
        0.0,
        label="Front offset X",
        help="Nudge every front sideways to match where your printer actually "
        "lays ink down.",
        unit="mm",
        run=Run.SHEET,
        low=-20,
        high=20,
        group="Ink alignment",
    )
    sheet_front_offset_y_mm: float = setting(
        0.0,
        label="Front offset Y",
        help="Nudge every front up or down.",
        unit="mm",
        run=Run.SHEET,
        low=-20,
        high=20,
        group="Ink alignment",
    )
    sheet_back_offset_x_mm: float = setting(
        0.0,
        label="Back offset X",
        help="Nudge the backs to line up with the fronts on a duplex print.",
        unit="mm",
        run=Run.SHEET,
        low=-20,
        high=20,
        group="Ink alignment",
    )
    sheet_back_offset_y_mm: float = setting(
        0.0,
        label="Back offset Y",
        help="Nudge the backs up or down.",
        unit="mm",
        run=Run.SHEET,
        low=-20,
        high=20,
        group="Ink alignment",
    )
    # cut guides
    sheet_guides: bool = setting(
        default=True,
        label="Print cut guides",
        help="Draw where to cut.",
        run=Run.SHEET,
        group="Cut guides",
    )
    sheet_guide_style: GuideStyle = setting(
        GuideStyle.CORNERS,
        label="Guide style",
        help="Marks at each card's cut corners, which never touch a card; or the trim "
        "lines drawn straight across the page, over them. How far a mark reaches is "
        "the separate Guide reach setting.",
        run=Run.SHEET,
        group="Cut guides",
    )
    sheet_guide_reach: GuideReach = setting(
        GuideReach.FIXED,
        label="Guide reach",
        help="How far each corner mark runs away from the card. fixed = the length "
        "below and no more. join = on to the neighbouring card's mark, so the gap "
        "between two cards becomes one line, but the outer margin stays clean. paper = "
        "the same, and out to the edge of the sheet where there is no neighbour — what "
        "a rotary trimmer needs. It never runs past a neighbour onto its face.",
        run=Run.SHEET,
        group="Cut guides",
    )
    sheet_guide_placement: GuidePlacement = setting(
        GuidePlacement.OUTSIDE,
        label="Guide placement",
        help="Outside the trim keeps marks off the card; inside puts them on it.",
        run=Run.SHEET,
        group="Cut guides",
    )
    sheet_guide_mm: float = setting(
        4.0,
        label="Guide length",
        help="How long each crop mark is.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=20,
        # the derived flag would be `--guide`, a hair from `--guides` and not obviously
        # a length. The one place the rule is overruled, said beside the setting.
        flag="guide-length",
        group="Cut guides",
    )
    sheet_guide_color: str = setting(
        "#00ff00",
        label="Guide colour",
        help="A colour no card uses, so the marks are easy to see and to ignore.",
        run=Run.SHEET,
        group="Cut guides",
    )
    sheet_guide_width_mm: float = setting(
        0.3,
        label="Guide thickness",
        help="Line weight of the marks.",
        unit="mm",
        run=Run.SHEET,
        low=0.05,
        high=3,
        group="Cut guides",
    )
    sheet_guide_cross_mm: float = setting(
        0.0,
        label="Guide overshoot",
        help="How far each line runs past the trim edge onto the card. A little makes "
        "the four lines meet in a + at every corner, which is what tells you the "
        "grid is square; 0 leaves the cards completely clean.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=10,
        group="Cut guides",
    )
    sheet_guides_front: bool = setting(
        default=True,
        label="Guides on fronts",
        help="Cut from the front, so usually yes.",
        run=Run.SHEET,
        group="Cut guides",
    )
    sheet_guides_back: bool = setting(
        default=False,
        label="Guides on backs",
        help="Off by default — you cut from one side. Worth turning on to check "
        "registration: hold the sheet to a light and see whether the two sides' "
        "lines land on each other.",
        run=Run.SHEET,
        group="Cut guides",
    )
    # ---- the backs' own guides ----
    # Unset means "whatever the fronts do", which is the same shape `[print]
    # back_profile` has and is right for the same reason: one sheet of paper, one set
    # of guides, until you say otherwise. What makes a *different* answer worth having
    # is checking registration — with guides on both sides you hold the sheet to a
    # light, and two colours are how you tell which side's line you are looking at.
    sheet_back_guide_style: GuideStyle | None = setting(
        None,
        label="Back guide style",
        help="The style for the backs alone. Note `none` here means "
        "draw none on the backs — leaving this unset is what follows the fronts.",
        run=Run.SHEET,
        group="Cut guides · backs",
        auto="same as the fronts",
    )
    sheet_back_guide_reach: GuideReach | None = setting(
        None,
        label="Back guide reach",
        help="How far the backs' marks run. A short tick on the backs against long "
        "lines on the fronts is one way to tell the two apart through the paper.",
        run=Run.SHEET,
        group="Cut guides · backs",
        auto="same as the fronts",
    )
    sheet_back_guide_placement: GuidePlacement | None = setting(
        None,
        label="Back guide placement",
        help="Which side of the trim the backs' marks sit on.",
        run=Run.SHEET,
        group="Cut guides · backs",
        auto="same as the fronts",
    )
    sheet_back_guide_mm: float | None = setting(
        None,
        label="Back guide length",
        help="How long each of the backs' crop marks is.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=20,
        # `--back-guide` would be as unclear as `--guide` was, and for the same reason
        flag="back-guide-length",
        group="Cut guides · backs",
        auto="same as the fronts",
    )
    sheet_back_guide_color: str | None = setting(
        None,
        label="Back guide colour",
        help="A second colour is what lets you tell the backs' lines from the fronts' "
        "with the sheet held up to a light.",
        run=Run.SHEET,
        group="Cut guides · backs",
        auto="same as the fronts",
    )
    sheet_back_guide_width_mm: float | None = setting(
        None,
        label="Back guide thickness",
        help="Line weight of the backs' marks.",
        unit="mm",
        run=Run.SHEET,
        low=0.05,
        high=3,
        group="Cut guides · backs",
        auto="same as the fronts",
    )
    sheet_back_guide_cross_mm: float | None = setting(
        None,
        label="Back guide overshoot",
        help="How far the backs' lines run onto the card.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=10,
        group="Cut guides · backs",
        auto="same as the fronts",
    )
    # registration marks (printer front/back alignment)
    sheet_reg_marks: RegMarks = setting(
        RegMarks.NONE,
        label="Registration marks",
        help="Corner targets for measuring how far your printer's backs drift "
        "from its fronts.",
        run=Run.SHEET,
        group="Registration marks",
    )
    sheet_reg_inset_mm: float = setting(
        10.0,
        label="Registration inset",
        help="How far in from the page corner the targets sit.",
        unit="mm",
        run=Run.SHEET,
        low=0,
        high=50,
        group="Registration marks",
    )
    #: Overridable per run, but by ``sheet``'s **own** ``--open/--no-open`` rather than
    #: through :class:`Run`: this one is not a property of the printed page at all — it
    #: launches an application on the machine the command was typed on, which is why
    #: `/api/sheet` always passes `--no-open` and the UI links the PDF instead.
    sheet_open: bool = setting(
        default=False,
        label="Open the PDF",
        help="Open each sheet as soon as it's written. Applies to the CLI, on "
        "the machine you type it on; a sheet imposed from this UI is linked "
        "instead, since the server may not be the machine you are looking at.",
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
    #
    # **Every default here is identity: grade changes nothing until you ask it to.**
    # They used to lift the image (1.03 / 1.06 / 1.10) on the reasoning that paper and
    # ink dull it — which is true, and still the wrong place to fix it twice over. It
    # is a fact about *your* printer and *your* paper, so numbers proxdex invented for
    # a press it has never seen are a guess wearing a label — the same mistake as the
    # print presets that were deleted for it ("foil needs saturation 1.38" described
    # exactly one setup). And a correction that is identical for every card on the
    # sheet is by definition the medium's, which is what a print profile is *for* and
    # where it can be measured instead of typed. So a run through the pipeline with
    # nothing configured returns the card, and a look is something you chose.
    grade_brightness: float = setting(
        1.0,
        label="Brightness",
        help="1.0 leaves it be. Applied identically to every card, so it is a look "
        "for the batch rather than a fix for one scan.",
    )
    grade_contrast: float = setting(1.0, label="Contrast", help="1.0 leaves it be.")
    grade_saturation: float = setting(1.0, label="Saturation", help="1.0 leaves it be.")
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
    #: card backs can be a different medium — the reverse of a one-sided glossy
    #: stock is a different surface, and backs-only runs are often on other paper
    print_back_profile: str = setting(
        "",
        label="Print profile for backs",
        help="Correct card backs for a different medium than the fronts. Leave "
        "empty and they use the same profile — which is right when both sides land "
        "on the same paper.",
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
    #: the resolution a master must clear, in dots per inch of the finished card. A
    #: **minimum** rather than a fixed factor, because the factor is the wrong thing to
    #: hold still: sources arrive anywhere from 400 to 745px wide, so one factor
    #: scatters the masters it makes — measured on a real library, identical settings
    #: gave 592 dpi on one card and 1011 on another. And a minimum rather than a target
    #: because :attr:`sheet_dpi` renders the page at 1400 dpi, so a master under that is
    #: resampled *up* by a plain filter at print time — which is the work the neural
    #: upscaler was run to avoid. 0 turns it off and :attr:`upscayl_scale` is used.
    upscayl_min_dpi: int = setting(
        1000,
        label="Minimum resolution",
        unit="dpi",
        help="Dots per inch of the finished card that a master must reach. The step "
        "picks the smallest factor that clears it, so a small scan is enlarged harder "
        "than a large one. 1000 dpi is 2480px across a 63mm card; 0 ignores this and "
        "uses the fixed factor below.",
    )
    #: the fallback when there is no target, and the floor the derivation starts from
    upscayl_scale: UpscaylScale = setting(
        UpscaylScale.X2,
        label="Upscale factor",
        help="How much to enlarge in one pass, when no minimum resolution is set. "
        "With a minimum, this is ignored — the factor is worked out per card.",
    )
    #: "double upscayl" — run the model twice (2x doubled → 4x, up to 16x)
    upscayl_double: bool = setting(
        default=True,
        label="Double upscale",
        help="Run the model twice, so 2× becomes 4×. Slower, and sharper on small "
        "sources.",
    )

    @classmethod
    def run_options(cls, run: Run) -> tuple[RunOption, ...]:
        """Every setting overridable for one ``run``, in declaration order.

        **The single declaration.** ``proxdex sheet``'s flags, the request body's
        validation, the web layer's apply step and the sheet builder's controls are all
        derived from this, so adding ``run=Run.SHEET`` to a setting is the whole of
        making it overridable — there is nowhere else it has to be listed, and nothing
        that can be listed in one place and forgotten in the other three. That is not
        hypothetical: it is what had happened to twenty of these twenty-seven.
        """
        hints = get_type_hints(cls)
        out: list[RunOption] = []
        for fld in fields(cls):
            if fld.metadata.get("run") != run.value:
                continue
            declared = hints[fld.name]
            inner = optional_of(declared)
            bare = inner if inner is not None else declared
            out.append(
                RunOption(
                    key=fld.name,
                    flag=str(fld.metadata.get("flag") or _flag_for(fld.name)),
                    label=str(fld.metadata.get("label", fld.name)),
                    help=str(fld.metadata.get("help", "")),
                    unit=str(fld.metadata.get("unit", "")),
                    enum=bare
                    if isinstance(bare, type) and issubclass(bare, Enum)
                    else None,
                    kind=_kind_of(declared),
                    default=fld.default,
                    group=str(fld.metadata.get("group", "")),
                    low=fld.metadata.get("low"),
                    high=fld.metadata.get("high"),
                    optional=inner is not None,
                    auto=str(fld.metadata.get("auto", "")),
                )
            )
        return tuple(out)

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
                # `None` is a real default for an optional setting and reads as the
                # word "None" if it is not caught here — a settings screen offering
                # `None` as a value nobody may type
                "default": ""
                if default is MISSING or default is None
                else str(default.value if isinstance(default, Enum) else default),
                "auto": str(fld.metadata.get("auto", "")),
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
            raw = tomllib.loads(f.read_text(encoding="utf-8"))
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
    inner = optional_of(hint)
    if inner is not None:
        # An optional setting's "unset" is a *value* — see `optional_of`. TOML has no
        # way to write it other than by leaving the key out, so an empty string is the
        # spelling, and it is deliberately **not** the word "none": `guide_style` has a
        # real `none` member meaning "draw no guides", which is a different answer from
        # "whatever the fronts do".
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        hint = inner
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
    if hint is bool:
        return _as_bool(name, value)
    if hint in (int, float):
        return _as_number(name, hint, value)
    return value


def _as_number(name: str, hint: type[int | float], value: Any) -> int | float:
    """A number, however it was spelled.

    **A string spelling of a number is a number**, and that is not a nicety: every
    boundary this crosses can only send text — an HTML control's `value` is a string, so
    is argv, and TOML tolerates `dpi = "1400"`. Without it the value was stored *as the
    string* and the field's declared type was a lie. Measured: every numeric override
    the sheet builder sent (`margin`, `cols`, the ink offsets) reached the config as
    text, and `"4" * 3` is `"444"`, so a page count became a `TypeError` while the PDF
    was being written rather than a refusal at the boundary. It hid because nothing
    looked at the value until then; the first thing to *format* one is what surfaced it.
    """
    if isinstance(value, bool):  # `true` is not 1 here — a bool is the wrong type
        raise ConfigError(f"{name}: {value!r} is not a number")
    if isinstance(value, (int, float)):
        return hint(value)
    try:
        return int(value) if hint is int else float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name}: {value!r} is not a number") from exc


#: how a boolean arrives when the sender can only send text — `bool("false")` is `True`,
#: so this can never be `bool(value)`
_TRUE = frozenset({"true", "yes", "on", "1"})
_FALSE = frozenset({"false", "no", "off", "0"})


def _as_bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ConfigError(f"{name}: {value!r} — expected true or false")


def _options(enum_cls: type[Enum]) -> str:
    return "expected one of " + ", ".join(repr(m.value) for m in enum_cls)
