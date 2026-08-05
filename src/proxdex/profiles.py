"""Print profiles: one per medium you actually print on.

A profile is everything proxdex needs to know about "matte 200g on the XP-15000
with colour management off": a name, **your notes**, and how it corrects — either
four numbers you set by hand or the calibration rounds measured on it. One library
holds as many as you like, and `[print] profile` names the one a sheet uses by
default.

Nothing ships pre-filled. There is one built-in name, ``none``, and it is the
identity: no correction at all. Every real profile is one you made, because a
medium is a thing you own and nobody else's numbers describe it.

Why a file per profile rather than settings in ``proxdex.toml``:

* a medium is a *thing you own*, not a preference — it wants a name and notes
  ("Canon matte 200g, plain-paper setting, no colour management") because six
  months later the notes are the only way to reproduce the print;
* its correction is measured data (a polynomial and every patch of every round),
  which does not belong in a hand-edited config file; and
* two media coexist — you print the same deck on paper and on foil — so one
  active set of numbers was always the wrong shape.

Rounds are never deleted, only switched off, and the correction is refitted from
the live ones on every read — so nothing is stored that cannot be rederived from
the measurements, and turning a round back on restores exactly what it was doing.
A profile file is a record of what happened, not a cache.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from proxdex import calibrate, colour, media
from proxdex.calibrate import (
    GRID,
    Chart,
    ChartId,
    Correction,
    Error,
    Gamut,
    Intent,
    Patches,
    Reference,
    Slot,
    Substrate,
)
from proxdex.colour import Cast
from proxdex.config import Config
from proxdex.errors import ProxdexError
from proxdex.media import RECIPE_KEYS, Recipe
from proxdex.press import PressModel, Sample

#: where profiles live inside a library
DIR = "profiles"
#: the one built-in name: the identity, correcting nothing. Reserved, so a real
#: profile can never be called it and quietly shadow "leave my cards alone".
NONE = "none"
#: a grid is exactly (cols, rows)
_PAIR = 2
#: how many rounds in a row have to stop buying anything before the loop is called
#: done. One round can come back worse for reasons that are not the loop's — a
#: slightly crooked scan, a sheet fed warm — so a single flat round is noise and
#: three in a row is a floor.
_FLAT_ROUNDS = 3
#: mean RGB per round below which another sheet is not worth printing. This is a
#: judgement about your paper and your afternoon, not a measurement error: read
#: noise barely shows in a mean over ~70 patches (one level of noise per patch moves
#: the scored mean by 0.1), so a round can be *measured* to have gained 0.3 and still
#: not be worth having gained it.
_FLAT_GAIN = 0.5
#: how many rounds it takes before a growing cast is a trend rather than one sheet
_DRIFT_ROUNDS = 3
#: how much neutral chroma has to have been *gained* over the best earlier round to
#: call it drift. Half a ΔE00 is under what an eye can see side by side, so this is not
#: "the print is bad" — it is "the direction of travel is wrong", which is the thing
#: worth knowing three rounds in rather than after four sheets of holographic sticker.
_DRIFT_GAIN = 0.5


@dataclass(frozen=True, slots=True)
class Drift:
    """The correction is being driven further off neutral each round.

    Kept separate from :class:`Plateau` because they answer different questions, and
    the pair of them is the point: *did it stop improving* and *is it going the wrong
    way* used to be one number, which is how a profile that asked for more yellow every
    round reported progress.
    """

    first: int
    last: int
    #: neutral chroma the demand has gained since the best earlier round
    grew: float
    #: how far off neutral the printer is now being driven to make a grey
    cast: Cast

    @property
    def text(self) -> str:
        return (
            f"the correction is driven further off neutral every round — grown "
            f"{self.grew:.2f} since round {self.first}, now asking "
            f"{self.cast.text} for a grey. It is chasing something the paper "
            f"cannot give"
        )

    @property
    def hint(self) -> str:
        return (
            "the substrate is probably tinted, and aiming at an absolute neutral on "
            "tinted stock asks for ink that does not exist — see docs/calibration.md"
        )

    def json(self) -> dict[str, Any]:
        return {
            "first": self.first,
            "last": self.last,
            "grew": self.grew,
            "cast": self.cast.json(),
            "text": self.text,
            "hint": self.hint,
        }


@dataclass(frozen=True, slots=True)
class Plateau:
    """A run of rounds at the end of a calibration that improved nothing."""

    first: int
    last: int
    #: ΔE00 the best round in the run won over everything before it — below what
    #: :data:`_FLAT_GAIN` asks of that many rounds, and negative if the run came back
    #: worse than what it followed
    gain: float
    #: whether this was judged on **verification** rounds (the model's own predictions,
    #: which can fail) or on the fit's residual over its own training data. The word
    #: converged means something much weaker in the second case, so it is carried rather
    #: than left for a reader to assume.
    on_checks: bool = False

    @property
    def rounds(self) -> int:
        return self.last - self.first + 1

    @property
    def per_round(self) -> float:
        return max(self.gain, 0.0) / self.rounds

    def json(self) -> dict[str, Any]:
        return {
            "first": self.first,
            "last": self.last,
            "gain": self.gain,
            "per_round": self.per_round,
            "on_checks": self.on_checks,
            "text": self.text,
        }

    @property
    def text(self) -> str:
        span = (
            f"round {self.first}"
            if self.first == self.last
            else f"rounds {self.first} to {self.last}"
        )
        what = "the model's own predictions" if self.on_checks else "the fit"
        return (
            f"{span} improved {what} by {max(self.gain, 0.0):.2f} ΔE00 in total, "
            f"{self.per_round:.1f} a round"
        )


_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,47}$")


def slug(name: str) -> str:
    """A profile name as a filename — lowercase, dashes, nothing surprising."""
    clean = re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower()).strip("-.")
    if not clean or not _NAME_RE.match(clean):
        raise ProxdexError(
            f"profile name {name!r}: use letters, digits, dashes or dots "
            "(up to 48 characters)"
        )
    return clean


class Purpose(StrEnum):
    """What a round is for — and it decides whether it feeds the fit or judges it.

    This is the distinction the rebuilt loop turns on, and it is deliberately *not* the
    chart id: a refinement round and a verification round print the same small chart.
    What differs is what is done with the answer. A refinement round is more evidence,
    so it re-fits the model and can only ever agree with itself; a verification round
    prints what the model **predicts** and asks how far off it landed, which is the
    first number in this system that can fail — and the absence of such a number is why
    a profile could drive itself 32 levels toward yellow while every surface called it
    progress.
    """

    #: characterize or refine — this round's pairs are evidence and enter the fit
    MEASURE = "measure"
    #: check — printed through the finished model, scored, and kept **out** of the fit,
    #: because a model marking its own homework is not a test
    VERIFY = "verify"

    @property
    def fits(self) -> bool:
        return self is Purpose.MEASURE

    @property
    def label(self) -> str:
        return "measured" if self.fits else "verified"

    @classmethod
    def read(cls, data: object) -> Purpose:
        if isinstance(data, str):
            try:
                return cls(data)
            except ValueError:
                return cls.MEASURE
        return cls.MEASURE


@dataclass(frozen=True, slots=True)
class Pending:
    """A chart that has been emitted and not yet read back.

    Two facts, because reading a scan needs both: **where** on the sheet it was printed
    and **which chart** it is. It used to be the slot alone, which is what made a survey
    round impossible to record — `calibrate add` had no way to know it was looking at
    468 patches rather than 81, so it read the verification grid over a survey and got
    the gutters: bare paper at every position, which on a blue sticker would have
    reported the stock as pure white.
    """

    slot: Slot
    chart: ChartId = ChartId.VERIFY
    #: the patches that really went on the paper. Recorded rather than recomputed when
    #: the scan comes back, because "what was printed" is a fact about a sheet and
    #: recomputing it is a guess that can be wrong three ways: a survey prints
    #: **uncorrected** while a verification chart prints through the model, the model
    #: moves the moment another round is added, and the aim moves with the intent. Any
    #: of those silently pairs every scanned patch with a target it was never sent.
    sent: Patches | None = None
    #: the colours it asked for, when adaptive placement moved them off the chart's own
    wanted: Patches | None = None
    #: whether the scan coming back is evidence or a test of the model
    purpose: Purpose = Purpose.MEASURE

    def json(self) -> dict[str, Any]:
        return {
            "slot": self.slot.json(),
            "chart": self.chart.value,
            "purpose": self.purpose.value,
            "sent": None if self.sent is None else _rows(self.sent),
            "wanted": None if self.wanted is None else _rows(self.wanted),
        }

    @classmethod
    def read(cls, data: object) -> Pending | None:
        """A pending chart out of untrusted JSON, in either spelling.

        A bare ``[col, row]`` is how this was written before a chart had an id, and it
        means the verification chart at that slot — which is what it was.
        """
        if isinstance(data, list):
            return cls(slot=Slot.read(data))
        if isinstance(data, dict):
            raw: dict[str, object] = data
            card = ChartId.read(raw.get("chart"))
            return cls(
                slot=Slot.read(raw.get("slot")),
                chart=card,
                sent=_read_patches(raw.get("sent"), card),
                wanted=_read_patches(raw.get("wanted"), card),
                purpose=Purpose.read(raw.get("purpose")),
            )
        return None


@dataclass(frozen=True, slots=True)
class Round:
    """One print-and-scan iteration: what was sent, and what came back.

    Both halves are kept because the fit needs the pair, and because they are the
    evidence — a round that went wrong can be read, judged and dropped instead of
    quietly poisoning a correction nobody can inspect.
    """

    n: int
    slot: Slot
    sent: Patches
    scanned: Patches
    #: the colours this round **asked for**, when they are not the chart's own target.
    #: Adaptive placement moves the lattice patches to where the model is least sure, so
    #: a round scored against the nominal target would be compared with colours it never
    #: asked for — and the comparison would look perfectly healthy. None means the
    #: chart's own target, which is the ordinary case and what every fixed-lattice round
    #: is.
    wanted: Patches | None = None
    #: which patch set this round measured. Recorded because it is the only thing that
    #: makes the arrays mean anything: a survey and a verification round are both
    #: ``(n, 3)`` blocks of numbers, and reading one as the other pairs every patch with
    #: the wrong target while looking perfectly healthy.
    chart: ChartId = ChartId.VERIFY
    #: whether this round is evidence or a test of the model built from the evidence
    purpose: Purpose = Purpose.MEASURE
    date: str = ""
    scan: str = ""
    note: str = ""
    #: the paper this round was printed on, read off its own bare patches. Per round
    #: rather than per profile because it is also how two rounds are put into the same
    #: instrument state — see :meth:`Profile.normalised`.
    substrate: Substrate = field(default_factory=Substrate)
    #: whether this round feeds the fit. A round is never deleted — a bad one is
    #: switched off, so you can see what it was doing and put it back.
    enabled: bool = True

    def switched(self, *, on: bool) -> Round:
        return replace(self, enabled=on)

    @property
    def spec(self) -> Chart:
        """The patch set this round's arrays are rows of."""
        return self.chart.spec

    @property
    def goal(self) -> Patches:
        """The colours this round asked for — its own, else its chart's."""
        return self.spec.target if self.wanted is None else self.wanted

    def json(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "slot": self.slot.json(),
            "chart": self.chart.value,
            "purpose": self.purpose.value,
            "date": self.date,
            "scan": self.scan,
            "note": self.note,
            "enabled": self.enabled,
            "substrate": self.substrate.json(),
            "wanted": None if self.wanted is None else _rows(self.wanted),
            "sent": _rows(self.sent),
            "scanned": _rows(self.scanned),
        }

    @classmethod
    def read(cls, data: object, n: int) -> Round | None:
        """One stored round, or None if its measurements cannot be trusted.

        The patch arrays are checked against **the chart this round names**, not against
        whatever chart is current. Checking against one global length is what made a
        survey round unstorable: it was written with 468 rows and read back as
        unreadable, so the verb wrote a round the loader then discarded.
        """
        if not isinstance(data, dict):
            return None
        raw: dict[str, Any] = data
        card = ChartId.read(raw.get("chart"))
        sent = _read_patches(raw.get("sent"), card)
        scanned = _read_patches(raw.get("scanned"), card)
        if sent is None or scanned is None:
            return None
        return cls(
            n=int(raw.get("n", n)) if isinstance(raw.get("n"), int) else n,
            slot=Slot.read(raw.get("slot")),
            sent=sent,
            scanned=scanned,
            wanted=_read_patches(raw.get("wanted"), card),
            chart=card,
            purpose=Purpose.read(raw.get("purpose")),
            date=_text(raw.get("date")),
            scan=_text(raw.get("scan")),
            note=_text(raw.get("note")),
            substrate=Substrate.read(raw.get("substrate")),
            enabled=raw.get("enabled") is not False,
        )


@dataclass(slots=True)
class Profile:
    """A named medium: its notes, and how it corrects — by hand, or measured."""

    name: str
    notes: str = ""
    recipe: Recipe = field(default_factory=Recipe)
    #: how much of the paper's own colour to accept rather than fight. Fully relative
    #: by default: on tinted stock, aiming at an absolute neutral is what asks for ink
    #: that does not exist.
    intent: Intent = field(default_factory=Intent)
    #: scanner reading → reference space. The identity until a reference target is read,
    #: and said out loud everywhere until then: it is the assumption behind every number
    #: here, and it went unstated for as long as there was nothing to state it on.
    reference: Reference = field(default_factory=Reference)
    grid: tuple[int, int] = GRID
    rounds: list[Round] = field(default_factory=list)
    #: the chart last emitted and not yet read back — where it was printed *and* which
    #: chart it is, so reading its scan needs no arguments
    pending: Pending | None = None
    #: False for the built-in identity, which is not a file
    stored: bool = False
    #: rounds in the file that could not be read back — a damaged entry, or one
    #: measured against a chart of a different size. Counted rather than dropped
    #: in silence: a calibration quietly losing half its evidence would leave the
    #: error trend lying about what it is made of.
    unreadable: int = 0

    # ---------------------------------------------------------------- state --
    @property
    def calibrated(self) -> bool:
        return bool(self.live)

    @property
    def how(self) -> str:
        """How this profile corrects, in one word — and it is one of three.

        ``measured`` beats ``by hand`` beats ``identity``: a scan is evidence, four
        numbers are a judgement, and neither is nothing.
        """
        if self.live:
            return "measured"
        return "by hand" if not self.recipe.neutral else "identity"

    @property
    def live(self) -> list[Round]:
        """The rounds that feed the fit. A switched-off round stays in the file.

        A **verification** round is excluded too, and for a different reason: it was
        printed through the finished model in order to test it, so folding it back in
        would let the model mark its own homework — the residual would fall and the
        thing that could have failed would stop being able to.
        """
        return [r for r in self.rounds if r.enabled and r.purpose.fits]

    @property
    def checks(self) -> list[Round]:
        """The verification rounds, newest last — how the model did on predictions."""
        return [r for r in self.rounds if r.enabled and not r.purpose.fits]

    @property
    def model(self) -> PressModel | None:
        """How this medium is corrected, fitted over every live round at once.

        None means nothing usable has been measured, and the recipe is all there is.

        A **staged** model — ink limit, then linearization, then grey balance, then the
        colour transform — because a stage downstream cannot repair a stage upstream,
        and one polynomial doing all four at once is a cast nobody can attribute. The
        polynomial survives inside it as the last stage, fitted on what the three before
        it could not remove; a profile with no *uncorrected* round has nothing to
        linearize from and gets exactly the old behaviour, which
        :attr:`~proxdex.press.PressModel.staged` reports rather than hiding.
        """
        return self._fit(self.live)

    @property
    def correction(self) -> Correction | None:
        """Just the polynomial stage — the baseline the staged model is measured on."""
        model = self.model
        return None if model is None else model.poly

    def observed(self, patches: Patches) -> Patches:
        """A scanner reading in this profile's **reference space**.

        The one place the reference is applied, and it is applied on *read* rather than
        being baked into a stored round — so characterizing the scanner months later
        re-reads every round already on disk instead of asking for them to be printed
        again. That is the whole value of keeping both halves of a round's evidence.

        Everything that consumes a scan goes through here: the fit, the score, the gamut
        and the paper. It was briefly nowhere at all — the reference was stored and
        reported while `calibrate reference` claimed "every round of this profile is
        now read through it", which was false — and a label describing work nobody does
        is worse than no label.
        """
        return self.reference.apply(patches)

    @property
    def substrate(self) -> Substrate:
        """The paper, pooled over every live round that measured it.

        One profile, one substrate — it is a property of the stock, not of a sheet. The
        median over rounds rather than the newest, so one odd scan cannot redefine
        what the paper is.
        """
        seen = [r.substrate for r in self.live if r.substrate.measured]
        if not seen:
            return Substrate()
        whites = self.observed(np.array([s.white for s in seen], np.float32))
        blacks = self.observed(np.array([s.black for s in seen], np.float32))
        return Substrate(
            white=tuple(float(v) for v in np.median(whites, axis=0)),  # type: ignore[arg-type]
            black=tuple(float(v) for v in np.median(blacks, axis=0)),  # type: ignore[arg-type]
            spread=max(s.spread for s in seen),
            patches=sum(s.patches for s in seen),
        )

    def aim(self, goal: Patches) -> Patches:
        """What to ask the paper for — the target through this profile's intent."""
        return calibrate.aim(goal, self.substrate, self.intent)

    def normalised(self, rnd: Round) -> Patches:
        """One round's scan, put back into the same instrument state as the others.

        Rounds get scanned on different days, and nothing used to account for it: the
        four real ``holo-plain`` rounds' bare-paper readings drift **9 levels (6%)** and
        were pooled as though the lamp had not moved. Scaling each round by the ratio of
        its own paper to the profile's paper removes exactly that, in linear light.

        Second-order on the real profile — normalising alone moved the send tilt only
        -34.23 to -33.61, so it did not cause the cast — but it is unmeasured error
        pooled as signal, which is its own problem.
        """
        seen = self.observed(rnd.scanned)
        mine, ours = rnd.substrate, self.substrate
        if not (mine.measured and ours.measured):
            return seen
        # this round's own white in the same space as the profile's, or the ratio is
        # between two different readings of the same paper
        ratio = colour.linearize(np.array(ours.white, np.float32)) / np.maximum(
            colour.linearize(self.observed(np.array([mine.white], np.float32))[0]),
            1e-6,
        )
        return colour.encode(colour.linearize(seen) * ratio)

    def _fit(self, rounds: Sequence[Round]) -> PressModel | None:
        """Build the staged model over these rounds, each normalised to one white.

        A round contributes its **own** chart alongside its numbers, because that is
        what says which patches are the ramps and which are the greys — the whole reason
        :class:`calibrate.Role` exists. A survey and a verification round can therefore
        be fitted together, which is the new loop's ordinary case.
        """
        if not rounds:
            return None
        return PressModel.fit(
            [
                Sample(spec=r.spec, sent=r.sent, scanned=self.normalised(r))
                for r in rounds
            ],
            self.substrate,
        )

    def render(self, im: Image.Image) -> Image.Image:
        """What this profile does to a picture on the way to paper.

        The one place a correction meets an image, so the aim, the fit and the gamut
        compression cannot be applied in one order here and another there. A measured
        correction supersedes the hand-set recipe, because one was printed and scanned
        and the other was a judgement.
        """
        model = self.model
        if model is None:
            return media.compensate(im, self.recipe)
        arr = np.asarray(im.convert("RGB"), np.float32)
        goal = self.aim(arr.reshape(-1, 3)).reshape(arr.shape)
        return Image.fromarray(model.send(goal).round().astype(np.uint8))

    def influence(self, n: int) -> float | None:
        """How much round ``n`` moves the correction — its weight in the answer.

        Refit without it and measure how differently the result maps the target,
        in mean RGB. That is the "with and without" a switch is for: a round
        pulling far harder than its neighbours is either the most informative
        measurement you have or an outlier, and either way you want to know.
        """
        rnd = self.round(n)
        if rnd is None or not rnd.enabled:
            return None
        with_it = self.model
        without = self._fit([r for r in self.live if r.n != n])
        if with_it is None:
            return None
        # measured on the *verification* patches whatever chart the round used, so the
        # numbers of two rounds are comparable — which is the whole point of a pull
        goal = calibrate.target()
        base = with_it.apply(goal)
        other = goal if without is None else without.apply(goal)
        return float(np.sqrt(((base - other) ** 2).sum(axis=1)).mean())

    @property
    def gamut(self) -> Gamut:
        """What this medium can print — one answer for the profile, from its scans.

        A gamut belongs to the paper and the inks, not to one sheet, so it is read from
        **every** round that put ink on paper, verification rounds included: those are
        measurements of the same stock, and what a medium can reach is not a question
        about which rounds feed a fit. Scoring each round against its own scan instead
        made the trend compare means over different patch sets — 63 to 68 of 80 on a
        real matte — so the number moved when the set moved rather than when the print
        got better.

        A :class:`Gamut` rather than a boolean mask, because rounds no longer share a
        patch set: a survey asks about 468 colours and its verification chart about 81,
        so an array of one length cannot answer for the other.
        """
        inked = [r for r in self.rounds if r.enabled]
        if not inked:
            return Gamut()
        return Gamut.of(self.observed(np.concatenate([r.scanned for r in inked])))

    @property
    def seen(self) -> Patches:
        """Every colour this medium has been measured producing, pooled.

        What adaptive placement needs: the next chart's patches go where there is
        *nothing near* an existing measurement, so the question is about every round
        that put ink on paper rather than about the ones feeding a fit.
        """
        inked = [r.scanned for r in self.rounds if r.enabled]
        if not inked:
            return np.zeros((0, 3), np.float32)
        return self.observed(np.concatenate(inked))

    def score(self, rnd: Round) -> Error:
        """How far that round's print landed from the target, over this gamut.

        Judged **relative to the paper** when the intent says so, which is what makes
        the answer a statement about the print rather than about the stock: a blue
        holographic sticker measured against an absolute neutral reports a large cast
        that no ink can remove, and reports it identically however good the calibration
        gets.
        """
        sub = self.substrate
        white = (
            np.array(sub.white, np.float32)
            if sub.measured and self.intent.relative
            else None
        )
        card, goal = rnd.spec, rnd.goal
        return calibrate.score(
            self.observed(rnd.scanned),
            self.gamut.holds(goal),
            wanted=goal,
            white=white,
            spec=card,
        )

    @property
    def casts(self) -> list[Cast]:
        """The cast of what came *back*, per live round — how grey the print looks."""
        return [self.score(r).cast for r in self.live]

    @property
    def demands(self) -> list[Cast]:
        """The cast of what was *sent*, per live round — how hard the printer is being
        driven off neutral in order to make a neutral.

        This is the diagnostic, and picking the wrong one of these two is a mistake
        worth recording: the first version of :attr:`drift` watched :attr:`casts`, and
        on the real ``holo-plain`` profile that **improves** every round (chroma 9.05,
        6.42, 5.76, 5.77) because the fit really is dragging the *scan* toward neutral.
        What diverges is the demand — the blue-minus-red of what it sends for a neutral
        went 0, -28.33, -30.75, -32.42, monotonically more yellow ink. So a detector
        built on the scan would have passed the exact profile it exists to catch.
        """
        return [Cast.of(colour.to_lab(r.sent[r.spec.neutrals])) for r in self.live]

    @property
    def drift(self) -> Drift | None:
        """Whether the correction is being driven *further* off neutral each round.

        The signal that was missing, and its absence is the whole reason the rebuild
        happened: ``holo-plain`` asked the printer for more yellow every round while the
        single RGB figure it reported fell, so every surface called it convergence and
        :attr:`plateau` stood ready to certify it.

        A print landing nearer neutral and a correction working ever harder to put it
        there are different facts, and the second one means the fit is chasing something
        the paper cannot give — a tinted substrate, aimed at as though it were white.
        So this is reported wherever a residual is, and :attr:`plateau` refuses while it
        is true.
        """
        demands = self.demands
        if len(demands) < _DRIFT_ROUNDS:
            return None
        grew = demands[-1].chroma - min(c.chroma for c in demands[:-1])
        if grew < _DRIFT_GAIN:
            return None
        return Drift(
            first=self.live[0].n,
            last=self.live[-1].n,
            grew=grew,
            cast=demands[-1],
        )

    @property
    def residual(self) -> Error | None:
        """How true the most recent live round printed — the number to watch fall."""
        live = self.live
        return self.score(live[-1]) if live else None

    @property
    def verified(self) -> Error | None:
        """How the model did on colours it **predicted** — the number that can fail.

        Every other figure here is a fit judged against the data it was fitted on, so it
        can only ever agree with itself; this one is printed through the finished model
        and scored against what was asked for. The whole rebuild exists because there
        was no such number: a profile drove its neutral axis 32 levels toward yellow
        over four rounds while the one figure on screen fell every round.
        """
        checks = self.checks
        return self.score(checks[-1]) if checks else None

    @property
    def plateau(self) -> Plateau | None:
        """The tail of rounds that stopped buying anything, if there is one.

        A loop that exists to be repeated has to say when repeating it is done.
        Past this point another chart costs a sheet of your paper and an hour and
        buys a fraction of a level: what is left is the medium's own gamut, and no
        amount of measuring puts ink in the printer that is not there.

        **Judged on the verification rounds when there are any**, which is the one
        change that would have stopped this certifying ``holo-plain``. A refinement
        round's residual is the fit measured against its own training data, so a run of
        flat ones says "more of the same evidence stopped moving the fit" — not "the
        print is right". Verification rounds are printed through the finished model and
        scored on how far the *prediction* landed, so a flat run of those is the thing
        the word converged should mean. Without any, this falls back to the fit's own
        residual and stays what it was: an invitation to stop, not a certificate.

        Judged on the *best* round either side rather than the last one, because a
        single round coming back worse is ordinary and should not read as progress
        having stopped, nor a single good one as progress continuing.
        """
        judged = self.checks or self.live
        if len(judged) <= _FLAT_ROUNDS:
            return None
        head, tail = judged[:-_FLAT_ROUNDS], judged[-_FLAT_ROUNDS:]
        if self.drift is not None:
            # flat *and* drifting is not converged, it is stuck pulling the wrong way.
            # Certifying it is what would have told you `holo-plain` was finished.
            return None
        best_before = min(self.score(r).de00_mean for r in head)
        best_after = min(self.score(r).de00_mean for r in tail)
        gain = best_before - best_after
        if gain >= _FLAT_GAIN * len(tail):
            return None
        return Plateau(
            first=tail[0].n,
            last=tail[-1].n,
            gain=gain,
            on_checks=bool(self.checks),
        )

    @property
    def used_slots(self) -> tuple[Slot, ...]:
        """Every slot with ink on it — including a round that is switched off,
        because the paper does not care whether the fit uses it.

        A **survey** is not printed in a slot but it is printed on the paper the slots
        are cut out of, so it spends the ones its own region covers. That is what makes
        a quarter survey leave four slots for verification on the same sheet.
        """
        out: list[Slot] = []
        for rnd in self.rounds:
            size = rnd.chart.size
            out += list(size.slots(self.grid)) if size is not None else [rnd.slot]
        return tuple(out)

    @property
    def free_slots(self) -> tuple[Slot, ...]:
        used = {(s.col, s.row) for s in self.used_slots}
        every = calibrate.slots(self.grid)
        return tuple(s for s in every if (s.col, s.row) not in used)

    @property
    def next_slot(self) -> Slot:
        """Where the next chart should print — the first slot still blank.

        When the sheet is full it wraps to the first slot, which is correct: you
        are starting a fresh sheet, and the round numbers say which is which.
        """
        free = self.free_slots
        return free[0] if free else Slot(0, 0)

    @property
    def sheet_full(self) -> bool:
        return not self.free_slots

    def round(self, n: int) -> Round | None:
        return next((r for r in self.rounds if r.n == n), None)

    # ------------------------------------------------------------- the loop --
    def chart_label(
        self, slot: Slot | None = None, chart: ChartId = ChartId.VERIFY
    ) -> str:
        where = self.next_slot if slot is None else slot
        return (
            f"{self.name}  ·  round {len(self.rounds) + 1}  ·  {chart.label}"
            f"  ·  slot {where.text}"
        )

    def add_round(
        self,
        scanned: Patches,
        sent: Patches,
        slot: Slot,
        *,
        chart: ChartId = ChartId.VERIFY,
        purpose: Purpose = Purpose.MEASURE,
        wanted: Patches | None = None,
        scan: str = "",
        note: str = "",
        substrate: Substrate | None = None,
    ) -> Round:
        """Record a round. A measuring one refits the correction; a check does not."""
        rnd = Round(
            n=len(self.rounds) + 1,
            slot=slot,
            sent=sent,
            scanned=scanned,
            wanted=wanted,
            chart=chart,
            purpose=purpose,
            date=date.today().isoformat(),
            scan=scan,
            note=note,
            substrate=substrate
            if substrate is not None
            else Substrate.of(scanned, chart.spec),
        )
        self.rounds.append(rnd)
        self.pending = None
        return rnd

    def switch_round(self, n: int, *, on: bool) -> Round:
        """Include or exclude one round, keeping it and its number in the file.

        Nothing is ever deleted: a round you switch off can be switched back on,
        and its numbering never shifts under the round you were talking about.
        """
        rnd = self.round(n)
        if rnd is None:
            raise ProxdexError(f"{self.name}: no round {n}")
        if rnd.enabled == on:
            state = "already in the fit" if on else "already switched off"
            raise ProxdexError(f"{self.name}: round {n} is {state}")
        self.rounds = [r.switched(on=on) if r.n == n else r for r in self.rounds]
        return self.rounds[[r.n for r in self.rounds].index(n)]

    def json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "notes": self.notes,
            "recipe": self.recipe.json(),
            "intent": self.intent.json(),
            "reference": self.reference.json(),
            "grid": list(self.grid),
            "pending": None if self.pending is None else self.pending.json(),
            "rounds": [r.json() for r in self.rounds],
        }

    def summary(self) -> dict[str, Any]:
        """What a list or a settings screen shows — no patch arrays.

        **Three numbers, never one**: the residual, the cast (inside it) and the
        verification error. One number is what let a diverging profile look converged.
        """
        residual = self.residual
        plateau = self.plateau
        model = self.model
        verified = self.verified
        return {
            "name": self.name,
            "notes": self.notes,
            "how": self.how,
            "recipe": self.recipe.json(),
            "intent": self.intent.json(),
            "substrate": self.substrate.json(),
            "reference": self.reference.json(),
            "rounds": len(self.rounds),
            "live": len(self.live),
            "checks": len(self.checks),
            "calibrated": self.calibrated,
            "residual": None if residual is None else residual.json(),
            "verified": None if verified is None else verified.json(),
            "model": None if model is None else model.json(),
            "plateau": None if plateau is None else plateau.json(),
            "drift": None if (drift := self.drift) is None else drift.json(),
            "next_slot": self.next_slot.json(),
            "grid": list(self.grid),
            "stored": self.stored,
            "identity": self.name == NONE,
            "recipe_keys": list(RECIPE_KEYS),
            "unreadable": self.unreadable,
            "patches": len(calibrate.chart()),
        }

    def detail(self) -> dict[str, Any]:
        """The whole profile for the print screen: history, and every patch pair."""
        out = self.summary()
        out["rounds_detail"] = [
            {
                "n": r.n,
                "slot": r.slot.json(),
                "slot_text": r.slot.text,
                "chart": r.chart.value,
                "chart_label": r.chart.label,
                "purpose": r.purpose.value,
                "date": r.date,
                "scan": r.scan,
                "note": r.note,
                "error": self.score(r).json(),
                "enabled": r.enabled,
                "influence": self.influence(r.n),
                # this round's own patch set — a screen drawing the verification chart's
                # 81 targets beside a survey's 468 scans pairs every swatch wrongly
                "target": _rows(r.goal),
                "sent": _rows(r.sent),
                "scanned": _rows(r.scanned),
                "substrate": r.substrate.json(),
            }
            for r in self.rounds
        ]
        out["free_slots"] = [s.json() for s in self.free_slots]
        out["sheet_full"] = self.sheet_full
        out["pending"] = None if self.pending is None else self.pending.json()
        return out


# ------------------------------------------------------------------ storage ----
def profiles_dir(root: Path) -> Path:
    return root / DIR


def path_for(root: Path, name: str) -> Path:
    return profiles_dir(root) / f"{slug(name)}.json"


def exists(root: Path, name: str) -> bool:
    return path_for(root, name).exists()


def save(root: Path, profile: Profile) -> Path:
    dst = path_for(root, profile.name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(
        json.dumps(profile.json(), indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    profile.stored = True
    return dst


def delete(root: Path, name: str) -> None:
    path = path_for(root, name)
    if not path.exists():
        raise ProxdexError(f"no profile named '{name}'")
    path.unlink()


def rename(root: Path, old: str, new: str) -> Profile:
    profile = read(root, old)
    if profile is None:
        raise ProxdexError(f"no profile named '{old}'")
    if exists(root, new):
        raise ProxdexError(f"a profile named '{slug(new)}' already exists")
    profile.name = slug(new)
    save(root, profile)
    path_for(root, old).unlink(missing_ok=True)
    return profile


def read(root: Path, name: str) -> Profile | None:
    """The stored profile, or None. Never raises on a damaged file's contents."""
    path = path_for(root, name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProxdexError(f"{path.name} is not readable: {exc}") from exc
    if not isinstance(data, dict):
        raise ProxdexError(f"{path.name} is not a profile")
    raw: dict[str, Any] = data
    rounds: list[Round] = []
    unreadable = 0
    for i, item in enumerate(raw.get("rounds") or []):
        rnd = Round.read(item, i + 1)
        if rnd is None:
            unreadable += 1
        else:
            rounds.append(rnd)
    return Profile(
        name=slug(_text(raw.get("name")) or path.stem),
        notes=_text(raw.get("notes")),
        recipe=Recipe.read(raw.get("recipe")),
        intent=Intent.read(raw.get("intent")),
        reference=Reference.read(raw.get("reference")),
        grid=_grid(raw.get("grid")),
        rounds=rounds,
        pending=Pending.read(raw.get("pending")),
        stored=True,
        unreadable=unreadable,
    )


def names(root: Path) -> list[str]:
    """Every stored profile, plus the identity, without duplicates."""
    stored = sorted(p.stem for p in profiles_dir(root).glob("*.json"))
    return stored + ([NONE] if NONE not in stored else [])


def listing(root: Path) -> list[Profile]:
    """Every profile a sheet could use — the ones you made, then the identity."""
    out: list[Profile] = []
    for name in names(root):
        profile = resolve(root, name)
        out.append(profile)
    return out


def resolve(root: Path, name: str) -> Profile:
    """The profile called ``name``, or the identity.

    ``none`` is the only name that resolves without a file, and it corrects
    nothing. Everything else has to have been made, because proxdex has no numbers
    of its own to offer for your paper.
    """
    stored = read(root, name)
    if stored is not None:
        return stored
    if slug(name) == NONE:
        return Profile(name=NONE, notes="", recipe=Recipe())
    raise ProxdexError(
        f"no print profile named '{name}' — `proxdex profile list`, or "
        f"`proxdex profile new {name}`"
    )


def named(root: Path, name: str) -> str | None:
    """What ``name`` refers to, as :func:`listing` spells it — or None if nothing
    in this library answers to it.

    An empty name means ``none``, because that is what :func:`active` resolves it
    to. A name that is not a legal profile name at all is None rather than an
    error: this is asked in order to *report*, and a `[print] profile` somebody
    typed by hand can be anything.
    """
    try:
        want = slug(name or NONE)
    except ProxdexError:
        return None
    return want if want == NONE or exists(root, want) else None


class PrintSetting(StrEnum):
    """The two ``[print]`` keys that name a profile."""

    PROFILE = "profile"
    BACK_PROFILE = "back_profile"

    @property
    def label(self) -> str:
        return f"[print] {self.value}"

    @property
    def prints(self) -> str:
        return "fronts" if self is PrintSetting.PROFILE else "backs"


@dataclass(frozen=True, slots=True)
class Dangling:
    """A ``[print]`` setting naming a profile that is not there."""

    setting: PrintSetting
    name: str

    @property
    def message(self) -> str:
        return (
            f"{self.setting.label} names '{self.name}', which is not a profile in "
            f"this library — every sheet run refuses until it is changed"
        )

    @property
    def hint(self) -> str:
        # deliberately not "`profile list`" — one of the two places this is
        # printed *is* that list, and it has just shown you the names
        return (
            f"`proxdex profile use <name>`, or `proxdex profile new {self.name}` "
            f"if that is the medium you meant"
        )

    def json(self) -> dict[str, Any]:
        return {
            "setting": self.setting.value,
            "name": self.name,
            "prints": self.setting.prints,
            "message": self.message,
            "hint": self.hint,
        }


def dangling(root: Path, cfg: Config) -> tuple[Dangling, ...]:
    """Every ``[print]`` profile setting that names nothing.

    A profile name in ``proxdex.toml`` outlives the profile: the real library
    carried ``[print] profile = "foil"`` from the deleted built-in presets, so
    every `sheet` run died with *no print profile named 'foil'* and nothing before
    that moment said so — not `where`, not `profile list`, which is the one place
    an absent marker was already the symptom. So it is asked here, once, by
    everything that draws a profile: it is the same broken reference `frames
    check` reports as :data:`specs.Fault.MISSING`.

    Only a *set* key can dangle. Unset means "the identity" for the fronts and
    "the same medium as the fronts" for the backs, and both are answers.
    """
    return tuple(
        Dangling(setting=setting, name=value)
        for setting, value in (
            (PrintSetting.PROFILE, cfg.print_profile),
            (PrintSetting.BACK_PROFILE, cfg.print_back_profile),
        )
        if value and named(root, value) is None
    )


def active(root: Path, cfg: Config, override: str | None = None) -> Profile:
    """The profile card *fronts* print through: the flag, else ``[print] profile``."""
    return resolve(root, override or cfg.print_profile or NONE)


def active_back(
    root: Path,
    cfg: Config,
    override: str | None = None,
    front: Profile | None = None,
) -> Profile:
    """The profile card *backs* print through.

    Unset means "the same medium as the fronts", which is the ordinary case — a
    duplex sheet is one piece of paper. It is worth a setting because it is not
    *always* one medium: the reverse of a one-sided glossy stock is a different
    surface, and a backs-only run often goes on different paper entirely.
    """
    name = override or cfg.print_back_profile
    if not name:
        return front if front is not None else active(root, cfg)
    return resolve(root, name)


def create(root: Path, name: str, *, notes: str = "") -> Profile:
    """A new profile at identity: it changes nothing until you say what it does.

    From here there are two honest routes — measure it with the chart loop, or set
    the four numbers by hand and judge them off a test print.
    """
    if slug(name) == NONE:
        raise ProxdexError(
            f"'{NONE}' is reserved for no correction at all — name the medium you "
            "are actually printing on"
        )
    if exists(root, name):
        raise ProxdexError(f"a profile named '{slug(name)}' already exists")
    profile = Profile(name=slug(name), notes=notes, recipe=Recipe())
    save(root, profile)
    return profile


def _rows(arr: Patches) -> list[list[float]]:
    return [[round(float(v), 3) for v in row] for row in arr]


def _read_patches(data: object, chart: ChartId) -> Patches | None:
    """One stored patch block, checked against the chart the round says it printed."""
    if not isinstance(data, list) or not data:
        return None
    try:
        arr = np.asarray(data, dtype=np.float32)
    except ValueError:  # ragged rows — not a patch block at all
        return None
    if arr.shape != (len(chart.spec), 3) or not np.isfinite(arr).all():
        return None
    return arr


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _grid(value: object) -> tuple[int, int]:
    if isinstance(value, list) and len(value) == 2:
        pair: list[object] = value
        if all(isinstance(v, int) and v >= 1 for v in pair):
            return (int(pair[0]), int(pair[1]))  # type: ignore[arg-type]
    return GRID
