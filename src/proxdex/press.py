"""The press model: ink limit → linearization → grey balance → colour transform.

This replaces a single degree-2 polynomial that did all four jobs at once, and the
reason is not tidiness. **A stage downstream cannot repair a stage upstream.** A colour
transform fitted over a non-linear, grey-unbalanced response has to spend its parameters
undoing them before it can describe a colour, and when the result is wrong there is no
way to say *which* of the four things it got wrong — which is how the real
``holo-plain`` profile drove its neutral axis 32 levels toward yellow with every number
on screen reporting progress.

So the model is the sequence every RIP and profiling tool uses, in that order, each
stage fitted on the residual the ones before it could not remove:

* :class:`Limits` — how much ink this medium takes before more stops doing anything, per
  channel and in total. Roark's flatbed method starts here for the same reason: past the
  limit, extra ink costs money, dries badly and carries **no** information, so every
  sample taken there is a sample that says nothing.
* :class:`Curves` — three monotone 1-D curves, one per ink, from the single-channel
  ramps. This is the stage the previous chart could not support **at all**: it had a
  neutral ramp and an interior lattice, and neither isolates one ink.
* :class:`Grey` — the neutral axis forced neutral *relative to the substrate white*, at
  every lightness, from the L\\*-spaced ramp. Grey balance is the perceptual foundation
  (it is what G7 calibrates a press to) because every neutral on a card carries a cast
  and a cast is the easiest error there is to see.
* :class:`~proxdex.calibrate.Correction` — the degree-2 polynomial, demoted to the last
  stage, on what is left over the lattice.

Two properties are load-bearing and both are asserted rather than assumed:

**Every stage is invertible, exactly.** ``forward`` says what a send will produce and
``inverse`` says what to send for a colour you want, and they are not two fits of the
same data — each stage's inverse is the *arithmetic* inverse of its forward (bisection
on a monotone curve, the mirrored solve on the grey axis). Two independently fitted
directions would be free to disagree, and a round-trip error nobody measured is a fit
that quietly lies about what it will print.

**The response space is the substrate's.** A patch is described by how far it has moved
from bare paper toward this channel's heaviest ink, 1 to 0. That makes show-through a
*coordinate* rather than a bend the polynomial has to learn, which is what the
Murray-Davies picture says it is: the paper shows through in proportion to how little
ink covers it — measured on the real sticker as +57.75 blue-minus-red in the highlights
against +5.50 in the shadows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np
from numpy.typing import NDArray

from proxdex import colour
from proxdex.calibrate import (
    IDENTITY,
    Chart,
    Correction,
    Gamut,
    Patches,
    Role,
    Substrate,
    compress,
    in_range,
)
from proxdex.calibrate import (
    fit as fit_poly,
)

#: (..., 3) response: 1 is bare paper, 0 is this channel's heaviest ink
Response = NDArray[np.float64]

#: bisection steps for inverting a monotone curve. Twenty halvings resolve a send to
#: 255/2^20 — far finer than a send value can express, so the inverse is exact for every
#: purpose the model has.
_INVERT_STEPS = 20
#: how far apart two ramp readings must be to count as the ramp still doing something.
#: Below it the press has stopped responding and the extra ink is the ink limit.
_DEAD_LEVELS = 1.0
#: the ridge on the grey-balance and colour stages, in pseudo-samples of "change
#: nothing" — absolute, never proportional to the sample count, for the reason
#: :data:`proxdex.calibrate._RIDGE` gives: a prior that grows with the data damps every
#: round you add, which is backwards for a loop that exists to improve with measurement.
_RIDGE = 0.1
#: how near a round's send has to sit to the chart's own target to count as a **direct**
#: measurement — the only kind from which a ramp can be read (see
#: :meth:`PressModel.fit`)
_DIRECT_LEVELS = 0.75
#: fewest ramp steps that can describe a channel's response. Below it, a curve would be
#: a line drawn through two points and called a measurement.
_MIN_RAMP = 4
#: fewest lattice samples worth fitting a 10-term transform to
_MIN_LATTICE = 12


class Stage(StrEnum):
    """The four jobs, in the order they are done — and each is separately reportable.

    The point of the split is attribution: with one polynomial doing everything, a cast
    was a fact about "the fit". Now it belongs to a stage, and
    :meth:`PressModel.residuals` says which.
    """

    LIMIT = "ink-limit"
    LINEARIZE = "linearization"
    GREY = "grey-balance"
    COLOUR = "colour-transform"

    @property
    def label(self) -> str:
        return {
            Stage.LIMIT: "ink limit",
            Stage.LINEARIZE: "linearization",
            Stage.GREY: "grey balance",
            Stage.COLOUR: "colour transform",
        }[self]

    @property
    def blurb(self) -> str:
        return {
            Stage.LIMIT: "how much ink this medium takes before more does nothing",
            Stage.LINEARIZE: "one monotone curve per ink, from its own ramp",
            Stage.GREY: "the neutral axis made neutral, at every lightness",
            Stage.COLOUR: "what the three stages before it could not remove",
        }[self]


# ------------------------------------------------------------ monotone curves ----
@dataclass(frozen=True, slots=True)
class Curve:
    """A monotone 1-D map, sampled — with an inverse that is *its own* inverse.

    Fitted as a monotone piecewise-cubic (Fritsch-Carlson slopes, the PCHIP shape), so
    it cannot invent a wiggle between two samples: a linearization that reversed a
    gradient would band a smooth ramp, and monotonicity is the property that rules it
    out by construction rather than by inspection.

    ``back`` is **bisection on this curve**, not a second fit of the swapped samples.
    Two fits would be two answers, and the difference between them is a round-trip error
    no screen would ever show.
    """

    xs: NDArray[np.float64]
    ys: NDArray[np.float64]
    #: the slopes at the sample points, chosen to keep the interpolant monotone
    slopes: NDArray[np.float64]
    #: whether ``ys`` rises with ``xs``. Recorded because ``back`` needs to know which
    #: way to bisect, and a channel ramp *falls* (more send, less ink, lighter paper —
    #: so as a response it rises; the sign is measured rather than assumed).
    rising: bool = True

    def at(self, x: NDArray[np.float64]) -> NDArray[np.float64]:
        """The curve at ``x``, held flat outside the samples it was measured over."""
        want = np.asarray(x, np.float64)
        idx = np.clip(np.searchsorted(self.xs, want) - 1, 0, len(self.xs) - 2)
        x0, x1 = self.xs[idx], self.xs[idx + 1]
        y0, y1 = self.ys[idx], self.ys[idx + 1]
        m0, m1 = self.slopes[idx], self.slopes[idx + 1]
        h = np.maximum(x1 - x0, 1e-12)
        t = np.clip((want - x0) / h, 0.0, 1.0)
        t2, t3 = t * t, t * t * t
        out = (
            (2 * t3 - 3 * t2 + 1) * y0
            + (t3 - 2 * t2 + t) * h * m0
            + (-2 * t3 + 3 * t2) * y1
            + (t3 - t2) * h * m1
        )
        return np.where(
            want <= self.xs[0],
            self.ys[0],
            np.where(want >= self.xs[-1], self.ys[-1], out),
        )

    def back(self, y: NDArray[np.float64]) -> NDArray[np.float64]:
        """The ``x`` this curve maps to ``y`` — by bisection, so it is exact."""
        want = np.asarray(y, np.float64)
        lo = np.full(want.shape, float(self.xs[0]))
        hi = np.full(want.shape, float(self.xs[-1]))
        for _ in range(_INVERT_STEPS):
            mid = (lo + hi) / 2.0
            below = self.at(mid) < want if self.rising else self.at(mid) > want
            lo = np.where(below, mid, lo)
            hi = np.where(below, hi, mid)
        return (lo + hi) / 2.0

    @property
    def span(self) -> float:
        """How much of a response this channel covers — its own contrast."""
        return float(abs(self.ys[-1] - self.ys[0]))

    def json(self) -> dict[str, Any]:
        return {
            "xs": [round(float(v), 3) for v in self.xs],
            "ys": [round(float(v), 5) for v in self.ys],
            "rising": self.rising,
        }

    @classmethod
    def through(cls, xs: NDArray[np.float64], ys: NDArray[np.float64]) -> Curve:
        """A monotone curve through measured samples, made monotone if it is not.

        Read noise puts a reversal in a real ramp — one patch a level out of order — and
        the honest answer is not to fit a wiggle to it but to enforce the property the
        press actually has. A running max (or min) is the least violent way to do it: it
        moves only the samples that contradict their neighbours.
        """
        order = np.argsort(xs)
        x, y = np.asarray(xs, np.float64)[order], np.asarray(ys, np.float64)[order]
        x, keep = np.unique(x, return_index=True)
        y = y[keep]
        rising = bool(y[-1] >= y[0])
        y = np.maximum.accumulate(y) if rising else np.minimum.accumulate(y)
        return cls(xs=x, ys=y, slopes=_fc_slopes(x, y), rising=rising)


def _fc_slopes(xs: NDArray[np.float64], ys: NDArray[np.float64]) -> NDArray[np.float64]:
    """Fritsch-Carlson slopes: the largest that keep a cubic Hermite monotone."""
    h = np.diff(xs)
    delta = np.diff(ys) / np.maximum(h, 1e-12)
    n = len(xs)
    m = np.zeros(n, np.float64)
    if n == 1:  # pragma: no cover — a one-sample curve is flat
        return m
    m[0], m[-1] = delta[0], delta[-1]
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0.0:
            m[i] = 0.0  # a local extremum: flatten, never overshoot
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])
    return m


# ---------------------------------------------------------------- the stages ----
@dataclass(frozen=True, slots=True)
class Ends:
    """What bare paper and each ink's own full coverage read as.

    The two ends of the response axis, and they come from the chart rather than from a
    guess: ``white`` is the bare-substrate patches (the previous chart had none, so its
    lightest patch was *printed* at 252, which is ink) and ``black`` is each channel's
    own ramp end.
    """

    white: tuple[float, float, float] = (255.0, 255.0, 255.0)
    black: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def _lin(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        return (
            colour.linearize(np.array(self.white, np.float32)),
            colour.linearize(np.array(self.black, np.float32)),
        )

    def absorb(self, rgb: Patches) -> Response:
        """A reading as a response: 1 at bare paper, 0 at this channel's full ink."""
        white, black = self._lin
        return (colour.linearize(rgb) - black) / np.maximum(white - black, 1e-6)

    def reflect(self, response: Response) -> Patches:
        """A response back to a reading — the exact inverse of :meth:`absorb`."""
        white, black = self._lin
        return colour.encode(black + (white - black) * np.asarray(response, np.float64))

    def json(self) -> dict[str, Any]:
        return {"white": list(self.white), "black": list(self.black)}


@dataclass(frozen=True, slots=True)
class Limits:
    """How much ink this medium takes before more of it stops doing anything.

    ``floor`` is per channel: the lowest send that still darkens the paper. Below it the
    press is laying ink that carries no information, and every patch printed there
    measures the same colour as the one before — which is why this is stage one in every
    workflow that has one, and why a target with no single-channel wedge cannot find it.

    ``total`` is the sum of coverages at which the composite gets no darker. Both are
    *reported*, and used only to keep a send from asking for ink that does nothing.
    """

    floor: tuple[float, float, float] = (0.0, 0.0, 0.0)
    #: the largest useful sum of the three coverages, 0..3
    total: float = 3.0
    #: whether it was read off a chart at all, or is the do-nothing default
    measured: bool = False

    def clamp(self, send: Patches) -> Patches:
        """Refuse to ask for ink past the limit — proportionally, never per channel.

        Scaling the three coverages together is the same argument gamut compression
        rests on: taking the overshoot out of one channel alone moves the hue, which is
        the defect this whole area exists to remove.
        """
        out = np.asarray(send, np.float32).clip(0.0, 255.0)
        if not self.measured:
            return out
        out = np.maximum(out, np.array(self.floor, np.float32))
        cover = 1.0 - out / 255.0
        heavy = cover.sum(axis=-1)
        over = heavy > self.total
        if over.any():
            scale = np.where(over, self.total / np.maximum(heavy, 1e-6), 1.0)
            out = ((1.0 - cover * scale[..., None]) * 255.0).astype(np.float32)
        return out.clip(0.0, 255.0)

    @property
    def text(self) -> str:
        if not self.measured:
            return "not measured"
        floors = " ".join(f"{v:.0f}" for v in self.floor)
        return f"ink stops working below send {floors}, total {self.total:.2f} of 3"

    def json(self) -> dict[str, Any]:
        return {
            "floor": list(self.floor),
            "total": self.total,
            "measured": self.measured,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class Curves:
    """The three per-channel ramps, linearized — stage two.

    Each is measured with the *other* two channels at full, so it is one ink's own
    response and nothing else's. That isolation is the whole value: what the inks do
    **together** is then left for grey balance and the colour transform to describe,
    instead of all three being tangled in one polynomial that cannot be asked about any
    of them separately.
    """

    per_channel: tuple[Curve, ...] = ()

    @property
    def measured(self) -> bool:
        return len(self.per_channel) == 3

    def response(self, send: Patches) -> Response:
        """What each channel's send asks of its own ink."""
        arr = np.asarray(send, np.float64)
        if not self.measured:
            return arr / 255.0
        return np.stack(
            [self.per_channel[c].at(arr[..., c]) for c in range(3)], axis=-1
        )

    def send(self, response: Response) -> Patches:
        """The send that asks for that response — the curves' own inverse."""
        arr = np.asarray(response, np.float64)
        if not self.measured:
            return (arr * 255.0).astype(np.float32)
        return np.stack(
            [self.per_channel[c].back(arr[..., c]) for c in range(3)], axis=-1
        ).astype(np.float32)

    def json(self) -> dict[str, Any]:
        return {
            "measured": self.measured,
            "channels": [c.json() for c in self.per_channel],
            "span": [c.span for c in self.per_channel],
        }


@dataclass(frozen=True, slots=True)
class Grey:
    """The neutral axis, made neutral at every lightness — stage three.

    Measured off the L\\*-spaced neutral ramp: at each step, what the three channels
    were *asked* for and what level of neutral actually came back. So this stage holds
    "to get a neutral this light, ask for these three" — three monotone curves indexed
    by the neutral level — which is exactly a set of grey-balance curves.

    Off the grey axis it displaces a colour by the same amount as the neutral of its own
    level, which is the right first-order behaviour and reduces to doing nothing on a
    press whose axis is already neutral. It is not a colour transform and does not try
    to be; what it cannot describe is what stage four is fitted on.
    """

    #: per channel, level of neutral → the response to ask of that channel
    ask: tuple[Curve, ...] = ()
    #: the mean of the three asks, so the forward direction can be solved exactly
    mean: Curve | None = None

    @property
    def measured(self) -> bool:
        return len(self.ask) == 3 and self.mean is not None

    def request(self, want: Response) -> Response:
        """What to ask for, to get ``want`` — the image-to-paper direction."""
        arr = np.asarray(want, np.float64)
        if not self.measured:
            return arr
        level = arr.mean(axis=-1)
        off = arr - level[..., None]
        axis = np.stack([self.ask[c].at(level) for c in range(3)], axis=-1)
        return axis + off

    def produced(self, asked: Response) -> Response:
        """What asking that really produces — the exact inverse of :meth:`request`."""
        arr = np.asarray(asked, np.float64)
        if not self.measured or self.mean is None:
            return arr
        level = self.mean.back(arr.mean(axis=-1))
        axis = np.stack([self.ask[c].at(level) for c in range(3)], axis=-1)
        return level[..., None] + (arr - axis)

    def json(self) -> dict[str, Any]:
        return {
            "measured": self.measured,
            "channels": [c.json() for c in self.ask],
        }


def _features01(arr: Response) -> NDArray[np.float64]:
    """The ten degree-2 terms, over a 0..1 response rather than 0..255 sRGB."""
    n = np.clip(np.asarray(arr, np.float64), -0.5, 1.5)
    r, g, b = n[..., 0], n[..., 1], n[..., 2]
    o = np.ones_like(r)
    return np.stack([o, r, g, b, r * r, g * g, b * b, r * g, g * b, b * r], axis=-1)


#: the coefficients that change nothing, in response space
_IDENTITY01 = np.zeros((10, 3), np.float64)
_IDENTITY01[1, 0] = _IDENTITY01[2, 1] = _IDENTITY01[3, 2] = 1.0


def _identity01() -> NDArray[np.float64]:
    return _IDENTITY01.copy()


@dataclass(frozen=True, slots=True)
class Transform:
    """Stage four: what the three stages before it could not remove.

    A degree-2 polynomial over the *response* left after the ink limit, the per-channel
    linearization and the grey balance have each taken out what they can — which is the
    whole argument for the order. Fitted **both ways** because unlike the stages above
    it there is no arithmetic inverse of a polynomial in three variables; the round-trip
    error that costs is measured (:attr:`round_trip`) rather than assumed away, and it
    is small precisely because most of the work was done upstream.
    """

    forward_coef: NDArray[np.float64] = field(default_factory=_identity01)
    inverse_coef: NDArray[np.float64] = field(default_factory=_identity01)
    #: mean response error of asking-then-producing, over the samples it was fitted on
    round_trip: float = 0.0
    measured: bool = False

    def produced(self, asked: Response) -> Response:
        return _features01(asked) @ self.forward_coef

    def request(self, want: Response) -> Response:
        return _features01(want) @ self.inverse_coef

    def json(self) -> dict[str, Any]:
        return {"measured": self.measured, "round_trip": self.round_trip}


def _solve(feat: NDArray[np.float64], rhs: NDArray[np.float64]) -> NDArray[np.float64]:
    gram = feat.T @ feat + _RIDGE * np.eye(feat.shape[1], dtype=np.float64)
    out = np.linalg.solve(gram, feat.T @ rhs + _RIDGE * _IDENTITY01)
    return out.astype(np.float64)


# --------------------------------------------------------------- the residual ----
@dataclass(frozen=True, slots=True)
class StageResidual:
    """What one stage left for the next one, in ΔE00.

    Three numbers per stage — what came in, what went out, and therefore what this stage
    bought — because the acceptance test for the whole split is that *each stage
    measurably reduces the residual the next one sees*. Without this, "the stages are in
    the right order" would be a claim about the code rather than a measurement.
    """

    stage: Stage
    before: float
    after: float
    #: how many samples it was measured over, so a stage fitted on nothing says so
    samples: int = 0
    #: False when the chart carried nothing this stage could be built from
    measured: bool = True
    #: what this stage did, where a ΔE00 pair is the wrong way to say it. The ink limit
    #: does not make a prediction truer — it refuses to ask for ink that does nothing —
    #: so a before/after of the same number is the honest reading and this says why.
    note: str = ""

    @property
    def gained(self) -> float:
        return self.before - self.after

    @property
    def text(self) -> str:
        if not self.measured:
            return f"{self.stage.label}: not measured — {self.stage.blurb}"
        if self.note:
            return f"{self.stage.label}: {self.note}"
        return (
            f"{self.stage.label}: ΔE00 {self.before:.2f} → {self.after:.2f} "
            f"({self.gained:+.2f}) over {self.samples} patch(es)"
        )

    def json(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "label": self.stage.label,
            "blurb": self.stage.blurb,
            "before": self.before,
            "after": self.after,
            "gained": self.gained,
            "samples": self.samples,
            "measured": self.measured,
            "note": self.note,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class Sample:
    """One measured pair, and what the chart says the patch was for."""

    spec: Chart
    sent: Patches
    scanned: Patches

    @property
    def direct(self) -> bool:
        """Whether this round printed the chart's **own** target, uncorrected.

        Only a direct round can linearize anything, and that is a real constraint rather
        than a convenience: a ramp printed through a correction is no longer a sweep of
        one channel, so the patch a chart labels ``ramp-r`` did not measure red's own
        response to red. It is why :command:`calibrate survey` prints raw, and why a
        profile with only corrected rounds gets identity curves and is *told* so instead
        of getting a linearization inferred from the wrong patches.
        """
        return bool(
            np.abs(np.asarray(self.sent, np.float64) - self.spec.target).max()
            <= _DIRECT_LEVELS
        )


# ------------------------------------------------------------------ the model ----
@dataclass(frozen=True, slots=True)
class PressModel:
    """The four stages, composed — and invertible, so it can print and predict.

    :meth:`forward` is what makes stage 7 possible at all: a number that can **fail**.
    Everything before this rebuild could only ever compare a fit against the data it was
    fitted on, which is why nothing objected for four rounds.
    """

    ends: Ends
    limits: Limits
    curves: Curves
    grey: Grey
    transform: Transform
    #: the polynomial that would have been the whole model. Kept as the fallback for a
    #: profile with no direct round, and as the baseline the staged model is measured
    #: against — a split that did not beat one polynomial would not be worth having.
    poly: Correction
    #: what this medium has been **seen** to produce. Not a nicety: past the colours it
    #: was fitted over, a degree-2 transform extrapolates, and it extrapolates to sends
    #: that are perfectly in range and completely wrong. Measured on the simulated blue
    #: sticker, a wanted dark yellow was sent (97, 94, 207) — nothing was out of range,
    #: so nothing compressed it — and came back **blue** at ΔE00 63.6. So the model says
    #: what it cannot make, and :func:`~proxdex.calibrate.compress` gives up chroma and
    #: then lightness until the ask is inside.
    reach: Gamut = field(default_factory=Gamut)
    residuals: tuple[StageResidual, ...] = ()
    #: how many rounds, and how many of them could linearize
    rounds: int = 0
    direct: int = 0

    # ------------------------------------------------------------- both ways --
    def forward(self, send: Patches) -> Patches:
        """What the paper gives back for a send — the prediction that can be wrong."""
        limited = self.limits.clamp(send)
        asked = self.curves.response(limited)
        got = self.grey.produced(asked)
        return self.ends.reflect(self.transform.produced(got))

    def raw(self, want: Patches) -> Patches:
        """The send this colour needs, **unclamped** — so compression sees how far."""
        if not self.staged:
            return self.poly.raw(np.asarray(want, np.float32))
        response = self.ends.absorb(np.asarray(want, np.float32))
        asked = self.grey.request(self.transform.request(response))
        return self.curves.send(asked)

    def fits(self, want: Patches) -> NDArray[np.bool_]:
        """Whether each colour is one this medium has been measured able to make.

        **Two questions, not one.** The send has to come out in range *and* the colour
        has to sit inside what the press was seen to produce — see :attr:`reach` for the
        measured case where the first passed and the second did not.
        """
        arr = np.asarray(want, np.float32)
        return np.asarray(
            in_range(self.raw)(arr) & self.reach.holds(arr), dtype=np.bool_
        )

    def send(self, want: Patches) -> Patches:
        """What to print for a colour you want, unreachable colours compressed."""
        out = compress(self.raw, np.asarray(want, np.float32), fits=self.fits)
        return self.limits.clamp(out)

    def apply(self, want: Patches) -> Patches:
        """The send, clamped rather than compressed — for the diagnostics only."""
        return np.asarray(self.raw(want), np.float32).clip(0.0, 255.0)

    # ------------------------------------------------------------- reporting --
    @property
    def staged(self) -> bool:
        """Whether anything upstream of the polynomial was really measured."""
        return self.curves.measured or self.grey.measured

    @property
    def text(self) -> str:
        which = "staged" if self.staged else "one polynomial"
        return f"{which} over {self.rounds} round(s), {self.direct} of them uncorrected"

    def json(self) -> dict[str, Any]:
        return {
            "staged": self.staged,
            "text": self.text,
            "rounds": self.rounds,
            "direct": self.direct,
            "reach": self.reach.patches,
            "ends": self.ends.json(),
            "limits": self.limits.json(),
            "curves": self.curves.json(),
            "grey": self.grey.json(),
            "transform": self.transform.json(),
            "stages": [r.json() for r in self.residuals],
        }

    # ------------------------------------------------------------------ fit --
    @classmethod
    def fit(
        cls, samples: list[Sample], substrate: Substrate | None = None
    ) -> PressModel | None:
        """Build the model, one stage at a time, each on what the last left behind."""
        if not samples:
            return None
        poly = fit_poly(
            np.concatenate([s.scanned for s in samples]),
            np.concatenate([s.sent for s in samples]),
        )
        direct = [s for s in samples if s.direct]
        ends = _ends(direct, substrate)
        limits = _limits(direct, ends)
        curves = _curves(direct, ends)
        grey = _grey(direct, ends, curves)
        transform, residuals = _transform(samples, ends, limits, curves, grey)
        return cls(
            reach=Gamut.of(np.concatenate([s.scanned for s in samples])),
            ends=ends,
            limits=limits,
            curves=curves,
            grey=grey,
            transform=transform,
            poly=poly,
            residuals=residuals,
            rounds=len(samples),
            direct=len(direct),
        )


def _ends(direct: list[Sample], substrate: Substrate | None) -> Ends:
    """Bare paper, and each channel's own heaviest ink."""
    if substrate is not None and substrate.measured:
        white = substrate.white
    else:
        whites = [
            s.scanned[s.spec.substrate]
            for s in direct
            if len(s.spec.substrate) and len(s.scanned) == len(s.spec)
        ]
        if not whites:
            return Ends()
        white = tuple(float(v) for v in np.median(np.concatenate(whites), axis=0))  # type: ignore[assignment]
    black = [0.0, 0.0, 0.0]
    for channel in range(3):
        seen = [
            s.scanned[idx][np.argmin(s.spec.target[idx][:, channel])][channel]
            for s in direct
            for idx in (s.spec.ramps[channel],)
            if len(idx) and len(s.scanned) == len(s.spec)
        ]
        black[channel] = float(np.median(seen)) if seen else 0.0
    return Ends(white=white, black=(black[0], black[1], black[2]))  # type: ignore[arg-type]


def _limits(direct: list[Sample], ends: Ends) -> Limits:
    """Where each ink stops doing anything, and how much of it the paper holds."""
    floors = [0.0, 0.0, 0.0]
    found = False
    for channel in range(3):
        sends: list[float] = []
        reads: list[float] = []
        for s in direct:
            idx = s.spec.ramps[channel]
            if not len(idx) or len(s.scanned) != len(s.spec):
                continue
            sends += [float(v) for v in s.spec.target[idx][:, channel]]
            reads += [float(v) for v in s.scanned[idx][:, channel]]
        if len(sends) < _MIN_RAMP:
            continue
        found = True
        order = np.argsort(sends)
        send_arr = np.array(sends, np.float64)[order]
        read_arr = np.array(reads, np.float64)[order]
        # walk up from the ink end: the floor is the last send whose neighbour above it
        # reads the same, i.e. where the press had already stopped responding
        floor = float(send_arr[0])
        for i in range(1, len(send_arr)):
            if read_arr[i] - read_arr[0] > _DEAD_LEVELS:
                break
            floor = float(send_arr[i])
        floors[channel] = floor
    total = 3.0
    for s in direct:
        idx = s.spec.of_role(Role.MAX_INK)
        if not len(idx) or len(s.scanned) != len(s.spec):
            continue
        # the composite that came back darkest is the most ink this paper uses; anything
        # heavier is a send that costs ink and reads the same
        response = ends.absorb(s.scanned[idx])
        darkest = int(np.argmin(response.mean(axis=-1)))
        cover = 1.0 - s.spec.target[idx][darkest] / 255.0
        total = min(total, float(cover.sum()))
    return Limits(
        floor=(floors[0], floors[1], floors[2]),
        total=max(total, 1.0),
        measured=found,
    )


def _curves(direct: list[Sample], ends: Ends) -> Curves:
    """One monotone curve per ink, from its own ramp."""
    out: list[Curve] = []
    for channel in range(3):
        sends: list[float] = []
        reads: list[float] = []
        for s in direct:
            idx = s.spec.ramps[channel]
            if not len(idx) or len(s.scanned) != len(s.spec):
                continue
            sends += [float(v) for v in s.spec.target[idx][:, channel]]
            reads += [float(v) for v in ends.absorb(s.scanned[idx])[:, channel]]
        if len(sends) < _MIN_RAMP:
            return Curves()
        out.append(
            Curve.through(np.array(sends, np.float64), np.array(reads, np.float64))
        )
    return Curves(per_channel=tuple(out))


def _grey(direct: list[Sample], ends: Ends, curves: Curves) -> Grey:
    """The grey axis: to get a neutral this light, ask for these three responses."""
    levels: list[float] = []
    asks: list[NDArray[np.float64]] = []
    for s in direct:
        idx = s.spec.neutrals
        if not len(idx) or len(s.scanned) != len(s.spec):
            continue
        asked = curves.response(s.spec.target[idx])
        got = ends.absorb(s.scanned[idx])
        # the neutral this step really achieved is the mean of what came back; the ask
        # that achieved it is what was asked. That pair *is* a grey-balance curve.
        levels += [float(v) for v in got.mean(axis=-1)]
        asks += list(asked)
    if len(levels) < _MIN_RAMP:
        return Grey()
    level_arr = np.array(levels, np.float64)
    ask_arr = np.array(asks, np.float64)
    per = tuple(Curve.through(level_arr, ask_arr[:, c]) for c in range(3))
    return Grey(ask=per, mean=Curve.through(level_arr, ask_arr.mean(axis=-1)))


def _transform(
    samples: list[Sample],
    ends: Ends,
    limits: Limits,
    curves: Curves,
    grey: Grey,
) -> tuple[Transform, tuple[StageResidual, ...]]:
    """Stage four, plus what every stage bought — measured, not asserted.

    The residual of a stage is the ΔE00 between what the model *predicts* the press did
    and what it really did, with the stages up to that point switched on. So each row is
    a real before/after over real patches, which is the acceptance test for the order
    itself.
    """
    asked: list[NDArray[np.float64]] = []
    produced: list[NDArray[np.float64]] = []
    truth: list[Patches] = []
    for s in samples:
        if len(s.scanned) != len(s.spec):
            continue
        asked.append(curves.response(limits.clamp(s.sent)))
        produced.append(ends.absorb(s.scanned))
        truth.append(s.scanned)
    if not asked:
        return Transform(), ()
    ask = np.concatenate(asked)
    got = np.concatenate(produced)
    real = np.concatenate(truth)

    def landed(response: Response) -> float:
        """How far the model's prediction is from what the press really did."""
        return float(colour.de00_rgb(ends.reflect(response), real).mean())

    # each row is the prediction with one more stage switched on, so "this stage
    # reduces the residual the next one sees" is a measurement rather than a claim
    bare = landed(
        np.asarray(np.concatenate([s.sent for s in samples]), np.float64) / 255.0
    )
    flat = landed(ask)
    after_grey = grey.produced(ask)
    balanced = landed(after_grey)
    bound = int(
        (
            np.abs(
                limits.clamp(np.concatenate([s.sent for s in samples]))
                - np.concatenate([s.sent for s in samples])
            )
            > 0.5
        )
        .any(axis=-1)
        .sum()
    )
    rows = [
        StageResidual(
            stage=Stage.LIMIT,
            before=bare,
            after=bare,
            samples=len(ask),
            measured=limits.measured,
            # the ink limit does not make a prediction truer; it stops a send asking for
            # ink that does nothing, so what it bought is a count, not a ΔE00
            note=f"{limits.text} — it held back {bound} of {len(ask)} send(s)",
        ),
        StageResidual(
            stage=Stage.LINEARIZE,
            before=bare,
            after=flat,
            samples=len(ask),
            measured=curves.measured,
        ),
        StageResidual(
            stage=Stage.GREY,
            before=flat,
            after=balanced,
            samples=len(ask),
            measured=grey.measured,
        ),
    ]
    if len(ask) < _MIN_LATTICE:
        rows.append(
            StageResidual(
                stage=Stage.COLOUR,
                before=balanced,
                after=balanced,
                samples=len(ask),
                measured=False,
            )
        )
        return Transform(), tuple(rows)
    forward = _solve(_features01(after_grey), got)
    inverse = _solve(_features01(got), after_grey)
    trip = float(
        np.abs(
            _features01(_features01(after_grey) @ forward) @ inverse - after_grey
        ).mean()
    )
    transform = Transform(
        forward_coef=forward,
        inverse_coef=inverse,
        round_trip=trip,
        measured=True,
    )
    rows.append(
        StageResidual(
            stage=Stage.COLOUR,
            before=balanced,
            after=landed(transform.produced(after_grey)),
            samples=len(ask),
            measured=True,
        )
    )
    return transform, tuple(rows)


#: the identity model: it prints what it is given. What a profile with no measurement
#: has, and what ``profile use none`` means.
def identity() -> PressModel:
    return PressModel(
        ends=Ends(),
        limits=Limits(),
        curves=Curves(),
        grey=Grey(),
        transform=Transform(),
        poly=Correction(coef=IDENTITY.copy()),
    )
