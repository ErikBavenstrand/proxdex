"""What is stored, against what proxdex would write now.

A library outlives the code that filled it, and proxdex has since learned things
about how a stage image has to be *stored* that it did not know when the first
cards were filed. Every one of them shares the property that makes this whole
project careful: nothing about it is visible on screen. It reaches paper.

Six findings, and they are the only six claimed here — a check nobody can act on
is noise, and a repair nobody measured is worse than the defect:

``alpha``
    A stored image carrying transparency. Every tool downstream then decides for
    itself what is under a die-cut corner, so the corner prints as whatever bytes
    the encoder happened to write there — found on a real library across fourteen
    files and three stages, near-white on one card and near-black on an upscaled
    one. Repairable: composite it onto the card's own outer-ring colour, which is
    exactly what :func:`proxdex.sources.flatten` does at every filing point today.

``mode``
    A stored image that is not RGB — a grayscale or CMYK scan that ``import``
    copied in verbatim, back when filing only looked for transparency. Repairable
    by the same call, since a file with nothing to composite is converted.

``aspect``
    A *bordered* master that is not the configured trim's aspect. The border step
    produces exactly that aspect by construction (cardbleed reshapes to it), so a
    file that misses it was placed by hand or written by something else — and
    `sheet` will silently ``cover``-crop it, losing border on two edges. **Not**
    repairable: re-fitting a border needs to know where the border currently is,
    which is a decision (the align marks), not a repair. Only the bordered stage
    is checked, because every later stage inherits its aspect and reporting one
    cause three times is not a better report.

``unreadable``
    A stage file Pillow cannot open at all. Not repairable — the pixels are gone;
    the step that made it has to run again.

``stale-spec``
    A bordered master fitted to frame-spec numbers that are no longer the numbers
    this library's rules resolve — the spec was corrected, a rule was added, or the
    card was pinned since. Everything else here is about *pixels*; this one is
    about a *number*, which is why it needs recording rather than measuring: the
    border step writes what it fitted to beside the file (``.fit-bordered``) and
    this compares that against what would happen today. Not repairable — re-fitting
    needs to know where the border currently is, which is a decision. A master
    filed before proxdex recorded the fit is **not** a finding: nothing is known
    about it, and inventing a comparison would be worse than staying quiet.

``dangling-pin``
    A card pinned to a frame spec that no longer exists. The fit falls back to the
    game's generic spec, which is a different border, silently. Not repairable —
    only a person knows whether the pin or the spec was the mistake.

Examining is cheap and never destructive: Pillow is asked for the header, not for
the pixels, and only :func:`repair` ever writes. A repair replaces the file
atomically through :func:`proxdex.scratch.file` and ``Path.replace``, and it does
**not** invalidate downstream stages — the picture is unchanged, so throwing away
an upscale over a corner fill would destroy work to fix a corner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from PIL import Image, UnidentifiedImageError

from proxdex import scratch, sources, specs
from proxdex.library import PIN_MARKER, Stage
from proxdex.steps import STAGES

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from proxdex.config import Config
    from proxdex.library import Card
    from proxdex.specs import Registry

#: the one stage whose aspect proxdex dictates. Everything after it inherits that
#: aspect from it, so checking them too would report one cause three times.
_FITTED = Stage.BORDERED


class Ailment(StrEnum):
    """What was found. The report groups by these, and so do the CLI and the UI."""

    UNREADABLE = "unreadable"
    ALPHA = "alpha"
    MODE = "mode"
    ASPECT = "aspect"
    STALE_SPEC = "stale-spec"
    DANGLING_PIN = "dangling-pin"


@dataclass(frozen=True, slots=True)
class Check:
    """One declared check: what it means, and what can be done about it.

    The text lives here rather than in the CLI or the JS, so the terminal and the
    settings screen explain a finding with the same sentence.
    """

    id: Ailment
    label: str
    #: why this matters — always ending at paper, since that is where it shows
    why: str
    #: can :func:`repair` rewrite the file into the form proxdex writes now?
    repairable: bool
    #: what to do when it cannot
    hint: str


CHECKS: tuple[Check, ...] = (
    Check(
        id=Ailment.UNREADABLE,
        label="not a readable image",
        why=(
            "The file is in place, so the step counts as done, but nothing can "
            "open it — a sheet run would fail on this card instead of skipping it."
        ),
        repairable=False,
        hint="Reset that step and run it again (`proxdex reset <step> <id>`).",
    ),
    Check(
        id=Ailment.ALPHA,
        label="carries transparency",
        why=(
            "A die-cut corner left transparent prints as whatever happens to be "
            "under it, and which bytes that is depends on the tool that reads the "
            "file next. Invisible on screen, permanent on paper."
        ),
        repairable=True,
        hint="",
    ),
    Check(
        id=Ailment.MODE,
        label="not stored as RGB",
        why=(
            "A grayscale or CMYK file is converted by every downstream tool in its "
            "own way. proxdex files RGB so the pixels that reach the imposition are "
            "the pixels it measured."
        ),
        repairable=True,
        hint="",
    ),
    Check(
        id=Ailment.ASPECT,
        label="not the trim aspect",
        why=(
            "The border step reshapes a card to exactly the configured trim, so a "
            "bordered master that misses it did not come from that step. `sheet` "
            "fits it with `cover`, which crops border off two edges silently."
        ),
        repairable=False,
        hint=(
            "Re-run the border step on that side, placing the marks (`proxdex border`)."
        ),
    ),
    Check(
        id=Ailment.STALE_SPEC,
        label="fitted to a frame spec that has changed",
        why=(
            "This master was reshaped to border widths that are no longer the ones "
            "this library resolves for the card — a spec was corrected, a rule was "
            "added, or the card was pinned since. The picture is fine and the "
            "border is the wrong width, which is exactly the defect nobody sees "
            "until two cards are side by side on cut paper."
        ),
        repairable=False,
        hint=(
            "Re-run the border step on that side (`proxdex border <id> --force`), "
            "placing the marks again."
        ),
    ),
    Check(
        id=Ailment.DANGLING_PIN,
        label="pinned to a frame spec that no longer exists",
        why=(
            "The pin names a spec this library does not have, so the fit falls back "
            "to the game's generic one — a different border, chosen by nothing."
        ),
        repairable=False,
        hint=(
            "Pin it to a spec that exists (`proxdex frames pin`), or drop the pin "
            "(`proxdex frames unpin`) and let the rules answer."
        ),
    ),
)
CHECK: dict[Ailment, Check] = {c.id: c for c in CHECKS}


@dataclass(frozen=True, slots=True)
class Finding:
    """One stored file, and the one thing wrong with it.

    ``stage``/``face`` are ``None`` for a finding about the *card* rather than one
    of its images — a dangling pin belongs to the card, and pretending it belongs
    to a stage would put a fake row in the table.
    """

    id: str  # the card
    stage: Stage | None
    face: int | None
    ailment: Ailment
    #: what was measured, in the fewest words that let someone check it by hand
    detail: str
    path: Path

    @property
    def check(self) -> Check:
        return CHECK[self.ailment]

    @property
    def repairable(self) -> bool:
        return self.check.repairable


@dataclass(slots=True)
class Report:
    """What the walk found, and how much it looked at.

    ``images`` matters as much as the findings: "3 findings" means something
    different over 12 files than over 1200, and a clean library should be able to
    say what was actually checked.
    """

    findings: list[Finding] = field(default_factory=list)
    cards: int = 0
    images: int = 0

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def repairable(self) -> list[Finding]:
        return [f for f in self.findings if f.repairable]

    @property
    def stuck(self) -> list[Finding]:
        """The findings a repair cannot touch — they need a step re-run."""
        return [f for f in self.findings if not f.repairable]

    def counts(self) -> dict[Ailment, int]:
        """How many of each, in the order the checks are declared."""
        return {
            a: n
            for a in Ailment
            if (n := sum(1 for f in self.findings if f.ailment is a))
        }


#: how far a bordered master's height may sit from its exact trim aspect before it
#: is a finding. cardbleed reshapes to the aspect and then rounds to whole pixels,
#: so half a pixel of slip is arithmetic; two is not an aspect proxdex produced.
_ASPECT_SLACK_PX = 2.0


def examine(cards: Sequence[Card], cfg: Config, reg: Registry | None = None) -> Report:
    """Walk every stored stage image of every side, reading headers only.

    ``reg`` is this library's frame-spec registry. Without it the two frame
    findings are simply not claimed — an absent registry is not evidence that a
    master is stale, and a check that guesses is worse than one that stays quiet.
    """
    report = Report(cards=len(cards))
    for card in cards:
        for face in card.faces:
            for stage in STAGES:
                path = card.stage_path(stage, face)
                if not path.exists():
                    continue
                report.images += 1
                report.findings.extend(_examine(card, stage, face, path, cfg))
        if reg is not None:
            report.findings.extend(_examine_specs(card, reg))
    return report


def _examine_specs(card: Card, reg: Registry) -> list[Finding]:
    """The two findings that are about a *number* rather than about pixels."""
    out: list[Finding] = []
    pin = card.pin
    if pin and reg.get(pin) is None:
        out.append(
            Finding(
                id=card.id,
                stage=None,
                face=None,
                ailment=Ailment.DANGLING_PIN,
                detail=f"pinned to '{pin}'",
                path=card.dir / PIN_MARKER,
            )
        )
    for face in card.faces:
        if not card.has(_FITTED, face):
            continue
        fit = card.fit(_FITTED, face)
        if fit is None:
            # filed before proxdex recorded what it fitted to: unknown, not stale
            continue
        want = specs.resolve(
            reg,
            card.id,
            card.set_id,
            card.game,
            pin=pin,
            printing=card.printing_frame,
            traits=card.traits,
        ).spec
        # nothing measured describes this printing any more (a scan-derived spec was
        # withdrawn, say). That is a `frames check` finding, not a stale master: the
        # picture is fine and there is nothing to compare it against.
        if want is None or fit.matches(want.id, want.inset):
            continue
        out.append(
            Finding(
                id=card.id,
                stage=_FITTED,
                face=face,
                ailment=Ailment.STALE_SPEC,
                detail=(
                    f"fitted to '{fit.spec}' "
                    + " / ".join(f"{v * 100:.2f}" for v in fit.inset)
                    + f"% — '{want.id}' now wants "
                    + " / ".join(f"{v * 100:.2f}" for v in want.inset)
                    + "%"
                ),
                path=card.stage_path(_FITTED, face),
            )
        )
    return out


def _examine(
    card: Card, stage: Stage, face: int, path: Path, cfg: Config
) -> list[Finding]:
    def found(ailment: Ailment, detail: str) -> Finding:
        return Finding(
            id=card.id,
            stage=stage,
            face=face,
            ailment=ailment,
            detail=detail,
            path=path,
        )

    try:
        with Image.open(path) as im:
            mode, size = im.mode, im.size
            alpha = sources.transparent(im)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        return [found(Ailment.UNREADABLE, _oneline(exc))]

    out: list[Finding] = []
    # one file, one repair: `transparent` already covers RGBA, LA and palette
    # transparency, so a mode that is merely not RGB is the *other* case
    if alpha:
        out.append(found(Ailment.ALPHA, f"mode {mode}"))
    elif mode != "RGB":
        out.append(found(Ailment.MODE, f"mode {mode}"))
    slip = _aspect_slip(size, cfg)
    if stage is _FITTED and slip > _ASPECT_SLACK_PX:
        w, h = size
        want = round(w * cfg.card_h_mm / cfg.card_w_mm)
        out.append(
            found(
                Ailment.ASPECT,
                f"{w}×{h}px — {cfg.card_w_mm:g}×{cfg.card_h_mm:g}mm wants {w}×{want}",
            )
        )
    return out


def _aspect_slip(size: tuple[int, int], cfg: Config) -> float:
    """How many pixels of height separate this image from the trim's aspect."""
    w, h = size
    if w <= 0 or h <= 0 or cfg.card_w_mm <= 0:
        return 0.0
    return abs(h - w * cfg.card_h_mm / cfg.card_w_mm)


def _oneline(exc: Exception) -> str:
    return " ".join(str(exc).split())[:120] or type(exc).__name__


def repair(finding: Finding) -> bool:
    """Rewrite one file into the form proxdex files today. ``False`` if it can't.

    The rewrite is :func:`proxdex.sources.flatten` — the same call every filing
    point makes — so the repaired file is byte-for-byte what a fresh run would
    have produced, and running the repair twice changes nothing the second time.
    Written to a temp file beside the original and moved over it with
    ``Path.replace``, which is atomic on Windows too, so an interrupted repair
    never leaves a half-written master where a card used to be.
    """
    if not finding.repairable:
        return False
    with Image.open(finding.path) as im:
        fixed = sources.flatten(im)
    tmp = scratch.file(".png", folder=finding.path.parent)
    try:
        fixed.save(tmp)
        tmp.replace(finding.path)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def json_report(report: Report) -> dict[str, object]:
    """The report as the web UI reads it — one shape, served and rendered once."""
    return {
        "cards": report.cards,
        "images": report.images,
        "clean": report.clean,
        "repairable": len(report.repairable),
        "counts": {a.value: n for a, n in report.counts().items()},
        "checks": [
            {
                "id": c.id.value,
                "label": c.label,
                "why": c.why,
                "repairable": c.repairable,
                "hint": c.hint,
            }
            for c in CHECKS
        ],
        "findings": [
            {
                "id": f.id,
                "stage": f.stage.label if f.stage else None,
                "face": f.face + 1 if f.face is not None else None,
                "ailment": f.ailment.value,
                "detail": f.detail,
                "file": f.path.name,
                "repairable": f.repairable,
            }
            for f in report.findings
        ],
    }
