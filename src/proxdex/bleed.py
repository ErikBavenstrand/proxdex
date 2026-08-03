"""Reshape a card to the right trim aspect + border widths, via ``cardbleed``.

proxdex owns the *inputs* — the era's target border widths
(:class:`frames.FrameGuide`), where the border currently sits (the align
marks / CLI fractions) and **the size this card prints at** — and hands them to
cardbleed, which does the fit
(exact card aspect, borders as close to target as possible, never distorting
the art unless ``stretch`` is asked for) and continues the border into any
added area. The cut bleed added at sheet time is a separate, plain margin.

Everything runs in-process: cardbleed is a library dependency, not a subprocess.

The trim is a ``Trim`` argument rather than something read out of ``Config``,
because it is **per card**: an oversized printing prints at 88.9×127mm, and
``sheet.trim_mm`` is the one place that is decided. Reading ``cfg.card_w_mm``
here reshaped a planar card to 63.5:88.9 and `sheet` then cropped 0.91mm off
each side to make it fit its own cell — a border 1mm narrower than the spec
asked for, with nothing on screen to say so.
"""

from __future__ import annotations

import dataclasses
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from cardbleed import Edges, Params, bleed_card
from cardbleed import FileError as _CardbleedError
from cardbleed.geometry import Amount, FitPlan, solve_fit

from proxdex.errors import FileError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from proxdex.frames import FrameGuide
    from proxdex.sheet import Trim

Fracs = tuple[float, float, float, float]  # [top, right, bottom, left]


# ---- tuning the fill -------------------------------------------------------
# **Where a border has to be *widened*, the added area is invented**, and how cardbleed
# invents it is a judgement about one picture: a fill that continues a texture is right
# on a flat yellow border and wrong on one carrying printed marks. cardbleed's defaults
# are good and cannot be right for everything, so they are exposed rather than
# *replaced* — see `proxdex border --tune` and the UI's border panel.
#
# **A card with nothing chosen gets cardbleed's own defaults, deliberately.** A baseline
# of proxdex's would be a second set of numbers to keep in step with a library that
# already ships considered ones, and it would re-border every card with no marker
# differently from the day it was filed. `mode=smart` was briefly made the default here
# and taken back out for exactly that.
#
# **The knobs only matter where something is actually added, and that is worth stating
# because it is easy to get wrong.** Measured on `ecard3-141` with its marks on the
# spec: `solve_fit` returns extensions of ~1e-13 px — nothing is invented at all, the
# whole change is a 1.018 vertical stretch to reach the trim aspect — and all three
# `mode` values produce a **byte-identical** file. Move the marks inside the spec so the
# border really has to grow (extensions of 17-41px) and the same three modes give three
# different files. So a caller that offers these settings has to say when they can do
# nothing: :func:`extends` is that answer, and the UI's panel prints it rather than
# leaving somebody to turn a slider that cannot move anything.
#
# **Every knob is validated here, because cardbleed will not.** Measured: it accepts
# `jittter=0.1` (a typo), `mode="nonsense"` and `jitter="lots"` without a murmur and
# carries on with its defaults. A slider that silently does nothing is the worst
# possible version of this feature — you would tune it, see no change, and conclude the
# picture was as good as it gets. So the knobs are declared, and
# `tests/test_bleed_tuning.py` holds the declaration against `cardbleed.Params` itself:
# a renamed or dropped field fails the suite instead of becoming an ignored override.

#: Below this an "extension" is float noise, not an added pixel — `solve_fit` returns
#: ~1e-13 where a border needs no widening at all.
EXT_EPSILON: Final = 0.5


def extends(plan: FitPlan) -> bool:
    """Does this fit actually invent any border? If not, no fill setting can change it.

    Half a pixel, because the answer is "is there anything to synthesize", and a plan
    that needs nothing reports its extensions as float noise rather than as zero.
    """
    return any(v > EXT_EPSILON for v in plan.ext.values())


class Kind(StrEnum):
    """What a knob accepts, which is what a UI control and a CLI value must honour."""

    CHOICE = "choice"
    INT = "int"
    FLOAT = "float"
    #: ``"auto"``, or a pixel count — cardbleed's own spelling for ``trim``
    AUTO_INT = "auto-int"


class KnobId(StrEnum):
    """Every cardbleed synthesis setting, by the name it is spelled with everywhere.

    A closed set, so this is an enum: the id is a CLI value, a JSON key, a line in a
    card's ``.tune-bordered`` marker and an HTML control's name, and a bare string that
    is *almost* one of these is the failure mode cardbleed itself has — it took
    ``jittter=0.1`` in silence.
    """

    MODE = "mode"
    SAMPLE = "sample"
    TRIM = "trim"
    JITTER = "jitter"
    JITTER_SMOOTH = "jitter_smooth"
    JITTER_CROSS = "jitter_cross"
    SHUFFLE = "shuffle"
    NOISE = "noise"
    SMUDGE = "smudge"
    SEAM_FEATHER = "seam_feather"
    CORNER_GUARD = "corner_guard"
    HALO = "halo"
    EDGE_FILL = "edge_fill"


class FillMode(StrEnum):
    """How the added border is invented."""

    SMART = "smart"
    NAIVE = "naive"
    PATTERN = "pattern"


class Halo(StrEnum):
    """What to do with a bright rim just inside the cut edge."""

    AUTO = "auto"
    OVERWRITE = "overwrite"
    BLEND = "blend"


class EdgeFill(StrEnum):
    """Whether to continue the border across a transparent or rounded corner."""

    AUTO = "auto"
    OFF = "off"


@dataclasses.dataclass(frozen=True, slots=True)
class Range:
    """What a numeric knob will take. proxdex's bounds, not cardbleed's — a guard
    against nonsense rather than a claim about its limits: this is a fill, and a value
    far outside these is not a tuning but a mistake."""

    lo: float
    hi: float
    step: float = 0.05

    def holds(self, value: float) -> bool:
        return self.lo <= value <= self.hi


@dataclasses.dataclass(frozen=True, slots=True)
class Knob:
    """One cardbleed synthesis setting, with what it means and what it takes.

    ``default`` is **read from cardbleed's own** :class:`~cardbleed.Params` rather than
    restated, so proxdex cannot drift from the behaviour a card was actually fitted
    with. A knob is either a closed set (``options``) or a range (``bounds``) — never
    both, never neither, which :mod:`tests.test_bleed_tuning` pins.
    """

    id: KnobId
    label: str
    help: str
    kind: Kind
    #: the enum that *is* this knob's closed set, so the values are not restated
    options: type[StrEnum] | None = None
    bounds: Range | None = None

    @property
    def key(self) -> str:
        return self.id.value

    @property
    def choices(self) -> tuple[str, ...]:
        return tuple(m.value for m in self.options) if self.options else ()

    @property
    def default(self) -> str | int | float:
        return getattr(_DEFAULTS, self.id.value)

    def json(self) -> dict[str, Any]:
        return {
            "key": self.id.value,
            "label": self.label,
            "help": self.help,
            "kind": self.kind.value,
            "choices": list(self.choices),
            "lo": self.bounds.lo if self.bounds else 0,
            "hi": self.bounds.hi if self.bounds else 0,
            "step": self.bounds.step if self.bounds else 0,
            "default": self.default,
        }


_DEFAULTS: Final = Params()

#: Every synthesis setting cardbleed takes, in the order a person would reach for them:
#: the one that changes the *approach* first, then how far the fill wanders from what it
#: sampled, then the joins. All of them, deliberately — a hidden knob is the one you
#: needed, and the whole point is to tune until the picture looks right.
KNOBS: Final[tuple[Knob, ...]] = (
    Knob(
        KnobId.MODE,
        "Fill method",
        "How the added border is invented. `pattern` continues a repeating "
        "texture, `smart` reads the edge and extends it, `naive` stretches the "
        "outermost pixels. On a grainy border these give three visibly different "
        "results; on a perfectly flat one, the same file.",
        Kind.CHOICE,
        options=FillMode,
    ),
    Knob(
        KnobId.SAMPLE,
        "Sample depth",
        "How many pixels inward it reads to decide what the border looks like. "
        "Smaller keeps to the outermost edge; larger sees more of the frame.",
        Kind.INT,
        bounds=Range(1, 64, 1),
    ),
    Knob(
        KnobId.TRIM,
        "Trim first",
        "Pixels shaved off the source edge before anything is added — `auto` lets "
        "cardbleed decide. Use it when the scan carries a sliver of the next card.",
        Kind.AUTO_INT,
        bounds=Range(0, 64, 1),
    ),
    Knob(
        KnobId.JITTER,
        "Jitter",
        "How much the fill wanders sideways from the strip it copied. Higher hides a "
        "seam; too high smears detail along the edge.",
        Kind.FLOAT,
        bounds=Range(0.0, 3.0),
    ),
    Knob(
        KnobId.JITTER_SMOOTH,
        "Jitter smoothing",
        "How gradually that wander changes along the edge. Low is ragged, high is a "
        "slow drift.",
        Kind.FLOAT,
        bounds=Range(0.0, 5.0),
    ),
    Knob(
        KnobId.JITTER_CROSS,
        "Jitter depth",
        "How far the wander reaches into the added area rather than staying at its "
        "outer lip.",
        Kind.FLOAT,
        bounds=Range(0.0, 16.0, 0.1),
    ),
    Knob(
        KnobId.SHUFFLE,
        "Shuffle",
        "How far along the edge the fill may borrow from. High breaks up a repeat; on "
        "a card with printing near the edge it moves that printing somewhere it "
        "never was.",
        Kind.FLOAT,
        bounds=Range(0.0, 200.0, 1.0),
    ),
    Knob(
        KnobId.NOISE,
        "Noise",
        "Grain added to the fill so it matches a printed surface instead of reading "
        "as flat colour.",
        Kind.FLOAT,
        bounds=Range(0.0, 2.0),
    ),
    Knob(
        KnobId.SMUDGE,
        "Smudge",
        "Softening across the fill. Raise it to lose a texture the source had and "
        "the extension should not repeat.",
        Kind.FLOAT,
        bounds=Range(0.0, 3.0),
    ),
    Knob(
        KnobId.SEAM_FEATHER,
        "Seam feather",
        "Pixels blended across the join between the card and the added border.",
        Kind.INT,
        bounds=Range(0, 32, 1),
    ),
    Knob(
        KnobId.CORNER_GUARD,
        "Corner guard",
        "How much of each corner is protected from the two edges disagreeing there — "
        "the place a fill goes wrong most visibly.",
        Kind.INT,
        bounds=Range(0, 64, 1),
    ),
    Knob(
        KnobId.HALO,
        "Halo",
        "What to do with a bright rim some scans carry just inside the cut edge: "
        "`overwrite` replaces it, `blend` softens it, `auto` decides.",
        Kind.CHOICE,
        options=Halo,
    ),
    Knob(
        KnobId.EDGE_FILL,
        "Edge fill",
        "Continue the border across a transparent or rounded corner. `off` leaves it, "
        "which is what you want when the corner is already the card's own colour.",
        Kind.CHOICE,
        options=EdgeFill,
    ),
)

BY_ID: Final[dict[KnobId, Knob]] = {k.id: k for k in KNOBS}


class TuningError(FileError):
    """A knob or value that is not one — raised rather than passed to cardbleed,
    which would accept it and quietly use its default."""


def parse_knob(key: str) -> KnobId:
    """Untrusted text as a :class:`KnobId`, or an error naming the real ones.

    The boundary the whole module exists to hold: a key that is *almost* right is the
    one mistake cardbleed cannot catch, because it takes any keyword at all.
    """
    try:
        return KnobId(key.strip())
    except ValueError:
        raise TuningError(
            f"{key}: not a cardbleed setting — "
            f"try one of {', '.join(k.value for k in KnobId)}"
        ) from None


@dataclasses.dataclass(frozen=True, slots=True)
class Tuning:
    """A validated set of synthesis overrides — only the knobs actually changed.

    Keyed by :class:`KnobId` rather than by string, so nothing downstream can hold a
    setting proxdex never checked. Empty means "cardbleed's defaults", which is what
    every card got before this existed and what almost every card should keep.
    """

    values: tuple[tuple[KnobId, str | int | float], ...] = ()

    @property
    def overrides(self) -> dict[str, Any]:
        """What to hand :func:`cardbleed.bleed_card`."""
        return {k.value: v for k, v in self.values}

    @property
    def empty(self) -> bool:
        return not self.values

    def json(self) -> dict[str, Any]:
        return self.overrides

    def get(self, knob: KnobId) -> str | int | float:
        """This tuning's value for one knob, or the knob's default."""
        return dict(self.values).get(knob, BY_ID[knob].default)

    def spelled(self) -> list[str]:
        """``key=value`` pairs, the shape ``border --tune`` takes — so the UI, the CLI
        and the card's marker cannot disagree about how a tuning is written down."""
        return [f"{k.value}={v}" for k, v in self.values]

    @classmethod
    def parse(cls, values: Mapping[str, Any] | None) -> Tuning:
        """Coerce untrusted input, keeping only what differs from the default.

        Dropping the defaults is what makes a tuning *readable*: a card's record then
        says "mode=smart" rather than restating all thirteen, and it is the difference
        between a decision and a dump.
        """
        kept: list[tuple[KnobId, str | int | float]] = []
        for key, raw in (values or {}).items():
            knob = BY_ID[parse_knob(key)]
            value = _coerce(knob, raw)
            if value != knob.default:
                kept.append((knob.id, value))
        return cls(tuple(sorted(kept)))

    @classmethod
    def from_pairs(cls, pairs: Sequence[str]) -> Tuning:
        """``("mode=smart", "jitter=0.2")`` — what the CLI and the marker hand over."""
        out: dict[str, str] = {}
        for pair in pairs:
            key, sep, value = pair.partition("=")
            if not sep:
                raise TuningError(f"{pair!r}: expected key=value")
            out[key.strip()] = value.strip()
        return cls.parse(out)


def _coerce(knob: Knob, raw: Any) -> str | int | float:
    """One value into the knob's own type, or a :class:`TuningError` naming what it
    takes. Coerced *into the enum* for a closed set — the point of having one."""
    text = str(raw).strip()
    if knob.options is not None:
        try:
            return knob.options(text).value
        except ValueError:
            raise TuningError(
                f"{knob.key}: {text!r} is not one of {', '.join(knob.choices)}"
            ) from None
    if knob.kind is Kind.AUTO_INT and text == EdgeFill.AUTO.value:
        return text
    if knob.bounds is None:  # pragma: no cover - a knob is a set or a range, pinned
        raise TuningError(f"{knob.key}: takes no value")
    try:
        number = float(text)
    except ValueError:
        raise TuningError(f"{knob.key}: {text!r} is not a number") from None
    if not knob.bounds.holds(number):
        raise TuningError(
            f"{knob.key}: {text} is outside {knob.bounds.lo:g}-{knob.bounds.hi:g}"
        )
    if knob.kind in (Kind.INT, Kind.AUTO_INT):
        return int(number)
    return round(number, 4)


def _pct(fracs: Fracs) -> Edges:
    """[top, right, bottom, left] fractions → an ``Edges`` of percents."""
    top, right, bottom, left = fracs
    return Edges(
        top=Amount(top * 100, "%"),
        right=Amount(right * 100, "%"),
        bottom=Amount(bottom * 100, "%"),
        left=Amount(left * 100, "%"),
    )


def fit_plan(
    w: int,
    h: int,
    guide: FrameGuide,
    inner: Fracs,
    trim: Trim,
    *,
    stretch: bool,
) -> FitPlan:
    """Preview the reshape (for dry-run / the readout) without writing.

    ``inner`` = current border per edge as image fractions [top, right,
    bottom, left]; ``guide.inset`` = the era's target borders as card fractions.
    """
    return solve_fit(
        w,
        h,
        _pct(inner),
        _pct(guide.inset),
        trim[0],
        trim[1],
        stretch=stretch,
        crop=True,
    )


def fit(
    src: Path,
    dst: Path,
    guide: FrameGuide,
    inner: Fracs,
    trim: Trim,
    *,
    stretch: bool,
    tune: Tuning | None = None,
) -> None:
    """Reshape ``src`` to the card aspect + the era's target borders.

    ``tune`` overrides how the *added* border is synthesized. It changes no geometry, so
    a tuned fit lands on exactly the same trim and the same border widths as an untuned
    one — which is why :func:`fit_plan` needs none of it and the readout is unaffected.
    """
    _run(
        src,
        dst,
        trim,
        border_target=_pct(guide.inset),
        border_current=_pct(inner),
        stretch=stretch,
        fill_corners=True,
        **(tune.overrides if tune else {}),
    )


def grow(src: Path, dst: Path, trim: Trim, **mm: float) -> None:
    """Manually add border to each edge (``top``/``right``/``bottom``/``left``
    mm) — no fit, no distortion."""
    _run(
        src,
        dst,
        trim,
        bleed=Edges(
            top=Amount(mm.get("top", 0.0), "mm"),
            right=Amount(mm.get("right", 0.0), "mm"),
            bottom=Amount(mm.get("bottom", 0.0), "mm"),
            left=Amount(mm.get("left", 0.0), "mm"),
        ),
        fill_corners=True,
    )


def cut_bleed(src: Path, dst: Path, trim: Trim, px: int) -> None:
    """Uniform cut bleed added at sheet time (no fit, no corner squaring)."""
    _run(src, dst, trim, bleed=f"{px}px")


def _run(src: Path, dst: Path, trim: Trim, **kw: Any) -> None:
    try:
        bleed_card(src, dst, card_size=trim, **kw)
    except _CardbleedError as exc:
        raise FileError(f"cardbleed failed on {src.name}: {exc}") from exc
