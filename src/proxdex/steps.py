"""The pipeline, declared once.

A card flows through an ordered list of steps, each producing one
:class:`~proxdex.library.Stage`. Everything that wants to know *what the steps
are* — the CLI's flags, ``/api/meta``, ``/api/step``'s request validation and the
web UI's settings panels — reads this module, so adding a step means adding one
:class:`StepSpec` (plus the code that does the work) and nothing else has to be
edited in three places and kept in sync.

Each step also declares its **settings**: a closed schema of typed options whose
defaults live in :class:`~proxdex.config.Config`. That is what lets the UI render
a step's controls without knowing anything about the step, and what lets the API
reject a bad value at the boundary instead of passing it to an external tool.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any, TypeVar, cast

from proxdex.config import Config, UpscaylModel, UpscaylScale
from proxdex.frames import GuideId
from proxdex.library import Stage, Step

#: a click command callback, which is all `click_options` ever decorates
F = TypeVar("F", bound=Callable[..., Any])


class OptKind(StrEnum):
    """How one setting is entered — the UI picks its control from this."""

    BOOL = "bool"
    CHOICE = "choice"
    NUMBER = "number"


@dataclass(frozen=True, slots=True)
class StepOption:
    """One setting of a step: its wire key, how to render it, where its default
    comes from, and the CLI flag it becomes."""

    key: str
    label: str
    help: str
    kind: OptKind
    #: for CHOICE, the enum whose members are the allowed values
    enum: type[Enum] | None = None
    #: the :class:`Config` field holding this library's default, if any
    config_field: str = ""
    #: an option with no default is simply omitted when unset (border's frame
    #: falls back to the card's own era, which only the CLI can resolve)
    optional: bool = False

    @property
    def choices(self) -> tuple[str, ...]:
        return tuple(str(m.value) for m in self.enum) if self.enum else ()

    def default(self, cfg: Config) -> Any:
        """This library's value for the setting, as JSON."""
        if not self.config_field:
            return None
        value = getattr(cfg, self.config_field)
        return value.value if isinstance(value, Enum) else value

    def coerce(self, value: Any) -> Any:
        """A request/CLI value as its declared type, or ``None`` if invalid.

        Untrusted input is turned into the enum (or number, or bool) here, at the
        boundary, so only well-typed values travel inwards.
        """
        if self.kind is OptKind.BOOL:
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            return text in {"1", "true", "yes", "on"} if text else None
        if self.kind is OptKind.CHOICE and self.enum is not None:
            return _member(self.enum, value)
        if self.kind is OptKind.NUMBER:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        return None

    def argv(self, value: Any) -> list[str]:
        """The CLI spelling of this setting — a flag pair, or a flag + value."""
        clean = self.coerce(value)
        if clean is None:
            return []
        if self.kind is OptKind.BOOL:
            return [f"--{self.key}" if clean else f"--no-{self.key}"]
        if isinstance(clean, Enum):
            return [f"--{self.key}", str(clean.value)]
        return [f"--{self.key}", f"{clean:g}"]

    def json(self, cfg: Config) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "help": self.help,
            "kind": self.kind.value,
            "choices": list(self.choices),
            "default": self.default(cfg),
            "optional": self.optional,
        }


@dataclass(frozen=True, slots=True)
class StepSpec:
    """One step of the pipeline: what it makes, what it is called, how it runs."""

    #: how the step is addressed everywhere — a URL segment, a CLI verb, a key
    key: str
    stage: Stage
    label: str
    #: one line, in the UI's voice, on what running this does
    blurb: str
    #: the pipeline verb (``Step``), or None for the source, which is not a step
    step: Step | None = None
    skippable: bool = False
    #: what the primary button says. "Run border" reads worse than "Reshape".
    run_label: str = ""
    options: tuple[StepOption, ...] = field(default_factory=tuple)

    @property
    def option_keys(self) -> tuple[str, ...]:
        return tuple(o.key for o in self.options)

    def option(self, key: str) -> StepOption | None:
        return next((o for o in self.options if o.key == key), None)

    def argv(self, opts: dict[str, Any]) -> list[str]:
        """Every declared setting present in ``opts``, as CLI arguments.

        Anything not declared is ignored rather than forwarded — the schema is
        the boundary, so an unknown key can never reach argv.
        """
        out: list[str] = []
        for opt in self.options:
            if opt.key in opts and opts[opt.key] is not None:
                out += opt.argv(opts[opt.key])
        return out

    def json(self, cfg: Config) -> dict[str, Any]:
        return {
            "key": self.key,
            "stage": self.stage.label,
            "label": self.label,
            "blurb": self.blurb,
            "skippable": self.skippable,
            "run_label": self.run_label or f"Run {self.label.lower()}",
            "options": [o.json(cfg) for o in self.options],
        }


# --------------------------------------------------------------- the pipeline --
# Ordered. Each stage is produced by exactly one step, and every step after the
# source can be run or skipped — never automatically.
SOURCE = StepSpec(
    key="original",
    stage=Stage.ORIGINAL,
    label="Original",
    blurb="The source scan every later stage derives from.",
    run_label="Fetch original",
)

BORDER = StepSpec(
    key="border",
    stage=Stage.BORDERED,
    label="Border",
    blurb="Reshape to the exact card size with the era's border width.",
    step=Step.BORDER,
    skippable=True,
    run_label="Reshape",
    options=(
        StepOption(
            key="frame",
            label="Frame spec",
            help="Which era's border widths to fit to. Defaults to the card's "
            "own set; pick borderless for a full-art print.",
            kind=OptKind.CHOICE,
            enum=GuideId,
            optional=True,
        ),
        StepOption(
            key="stretch",
            label="Stretch to hit the borders exactly",
            help="Un-distorts the art so the borders land on spec instead of "
            "as close as the source allows.",
            kind=OptKind.BOOL,
        ),
    ),
)

UPSCALE = StepSpec(
    key="upscale",
    stage=Stage.UPSCALED,
    label="Upscale",
    blurb="Enlarge and sharpen with Upscayl.",
    step=Step.UPSCALE,
    skippable=True,
    options=(
        StepOption(
            key="model",
            label="Model",
            help="Which Upscayl network to run. digital-art suits card art; "
            "remacri and ultrasharp favour photographic scans.",
            kind=OptKind.CHOICE,
            enum=UpscaylModel,
            config_field="upscayl_model",
        ),
        StepOption(
            key="scale",
            label="Scale",
            help="How much to enlarge in one pass.",
            kind=OptKind.CHOICE,
            enum=UpscaylScale,
            config_field="upscayl_scale",
        ),
        StepOption(
            key="double",
            label="Double Upscayl",
            help="Run the model twice, so 2× becomes 4×. Slower, and sharper "
            "on small sources.",
            kind=OptKind.BOOL,
            config_field="upscayl_double",
        ),
    ),
)

GRADE = StepSpec(
    key="grade",
    stage=Stage.EDITED,
    label="Grade",
    blurb="Colour-normalise to a uniform batch look — this is the trim master.",
    step=Step.GRADE,
    skippable=True,
    options=(
        StepOption(
            key="normalize",
            label="Normalise first",
            help="Pull the card to the library's baseline — frame white balance "
            "and black/white points — before the shared look.",
            kind=OptKind.BOOL,
            config_field="grade_normalize",
        ),
    ),
)

#: the pipeline, in order. Index 0 is the source; everything after it is a step.
PIPELINE: tuple[StepSpec, ...] = (SOURCE, BORDER, UPSCALE, GRADE)
BY_KEY: dict[str, StepSpec] = {s.key: s for s in PIPELINE}
BY_STAGE: dict[Stage, StepSpec] = {s.stage: s for s in PIPELINE}
#: every stage, in pipeline order — the one place this order is written down
STAGES: tuple[Stage, ...] = tuple(s.stage for s in PIPELINE)
#: best-master preference: the furthest-along stage wins
BEST: tuple[Stage, ...] = tuple(reversed(STAGES))


def get(key: str) -> StepSpec | None:
    return BY_KEY.get(str(key).strip().lower())


def steps() -> tuple[StepSpec, ...]:
    """The runnable steps — the pipeline without its source."""
    return tuple(s for s in PIPELINE if s.step is not None)


def json_pipeline(cfg: Config) -> list[dict[str, Any]]:
    return [s.json(cfg) for s in PIPELINE]


def _member(enum_cls: type[Enum], value: Any) -> Enum | None:
    """An enum member from untrusted input. JSON blurs 2 and "2", so a StrEnum's
    text and an IntEnum's number are both tried — the rule
    :func:`proxdex.config._coerce` applies to TOML."""
    text = str(value).strip().lower()
    candidates: list[Any] = [value, text]
    if text.lstrip("-").isdigit():
        candidates.append(int(text))
    for candidate in candidates:
        try:
            return enum_cls(candidate)
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------- the CLI --
def click_options(key: str) -> Callable[[F], F]:
    """The step's settings as click options, generated from its schema.

    Every option defaults to ``None`` meaning "whatever this library's config
    says", which is what the commands already did by hand — this just stops the
    flag names, their choices and their help text from being written twice.
    """
    import rich_click as click

    spec = BY_KEY[key]

    def decorate(fn: F) -> F:
        wrapped: Any = fn
        for opt in reversed(spec.options):
            default_note = (
                f"  [dim](default: \\[{_section(opt.config_field)}])[/]"
                if opt.config_field
                else ""
            )
            if opt.kind is OptKind.BOOL:
                flag = f"--{opt.key}/--no-{opt.key}"
                kind: Any = None
            elif opt.kind is OptKind.CHOICE:
                flag = f"--{opt.key}"
                kind = click.Choice(list(opt.choices))
            else:
                flag = f"--{opt.key}"
                kind = float
            wrapped = click.option(
                flag,
                opt.key,
                type=kind,
                default=None,
                help=opt.help + default_note,
            )(wrapped)
        return cast("F", wrapped)

    return decorate


def resolve(key: str, cfg: Config, **given: Any) -> dict[str, Any]:
    """A step's settings for this run: what was passed, else the config default.

    Values come back as their declared types (enums, not strings), so a command
    can hand them straight to the code that does the work.
    """
    spec = BY_KEY[key]
    out: dict[str, Any] = {}
    for opt in spec.options:
        value = given.get(opt.key)
        if value is None:
            out[opt.key] = (
                Config.coerce(opt.config_field, getattr(cfg, opt.config_field))
                if opt.config_field
                else None
            )
        else:
            out[opt.key] = opt.coerce(value)
    return out


def _section(field_name: str) -> str:
    """``upscayl_model`` → ``tools] upscayl_model``-ish label for CLI help.

    Only cosmetic: it tells the reader which table in ``proxdex.toml`` holds the
    default without hard-coding a second copy of the section names.
    """
    return {
        "upscayl_model": "tools] upscayl_model",
        "upscayl_scale": "tools] upscayl_scale",
        "upscayl_double": "tools] upscayl_double",
        "grade_normalize": "grade] normalize",
    }.get(field_name, field_name)
