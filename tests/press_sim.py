"""A simulated press and a simulated scanner, so a colour model can be *measured*.

This is stage 0 of ``docs/calibration.md``, and it is first because every later stage
has an acceptance test that has to run somewhere. A colour defect only shows on paper —
which is precisely the class of failure the suite exists for — and the real evidence
that started this rebuild was four stored rounds of one profile, which is a single
sample of a single medium and cannot answer "does this stage help".

Two objects, matching the two things between an image and your eye:

* :class:`SimPress` — a substrate, a per-channel tone response and ink crosstalk. Its
  reflectance follows **Murray-Davies**: the paper shows through in proportion to how
  little ink is on it, which is the mechanism behind the real measured gradient (the
  holographic sticker's blue-minus-red is +57.75 in the highlights against +5.50 in the
  shadows) and the reason a substrate has to be its own term rather than a bend the
  polynomial learns.
* :class:`SimScanner` — per-channel gain, an additive offset, flare and read noise. It
  exists to be *wrong* in the specific way §1.1 describes: a flatbed's R/G/B
  sensitivities are not a linear transform of the CIE matching curves, so "the print
  scans as the target's numbers" and "the print looks like the card" are different
  objectives, and on a coloured or specular substrate they diverge.

Nothing here is fitted to anything. The presses are named for what they are and their
numbers are chosen so that :data:`HOLO`'s bare paper reads as the real sticker's
measured **(144, 189, 208)** through an honest scanner — that one anchor is what ties
the simulator to the medium the defect was found on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from proxdex import colour
from proxdex.calibrate import Chart, Patches

#: (..., 3) linear reflectance, 0..1 — what a surface actually returns
Reflect = NDArray[np.float64]


def _linear(rgb: tuple[float, float, float]) -> NDArray[np.float64]:
    return colour.linearize(np.array(rgb, np.float32))


@dataclass(frozen=True, slots=True)
class SimPress:
    """A press: paper, three inks with their own tone response, and crosstalk.

    ``white`` and ``black`` are given as the sRGB an *honest* scanner would read off
    bare paper and off the heaviest ink, because that is the form every real measurement
    in this project arrived in.
    """

    name: str
    white: tuple[float, float, float]
    black: tuple[float, float, float]
    #: per-channel tone response. Above 1 the press lays ink on faster than asked,
    #: which is the ordinary direction for an inkjet.
    gamma: tuple[float, float, float] = (1.0, 1.0, 1.0)
    #: how much each ink contaminates the others. Near-identity; the off-diagonals are
    #: what make a per-channel linearization insufficient on its own, which is exactly
    #: why grey balance and a colour transform are separate stages after it.
    crosstalk: NDArray[np.float64] = field(
        default_factory=lambda: np.eye(3, dtype=np.float64)
    )

    def coverage(self, send: Patches) -> NDArray[np.float64]:
        """How much of each ink a send puts down, 0..1."""
        lin = colour.linearize(np.asarray(send, np.float32))
        tone = np.power(np.clip(lin, 0.0, 1.0), np.array(self.gamma, np.float64))
        mixed = np.clip(1.0 - tone, 0.0, 1.0) @ self.crosstalk.T
        return np.clip(mixed, 0.0, 1.0)

    def reflect(self, send: Patches) -> Reflect:
        """What the paper returns for a send — Murray-Davies over ink coverage.

        At zero coverage it is the bare substrate and at full coverage the ink, so the
        paper's own colour shows through **in proportion to how light the patch is**.
        That coverage weighting is the whole reason the substrate is a term rather than
        a curve.
        """
        cov = self.coverage(send)
        white, black = _linear(self.white), _linear(self.black)
        return black + (white - black) * (1.0 - cov)

    def print_page(self, send: Patches) -> Patches:
        """The print as an honest instrument would read it — the ground truth."""
        return colour.encode(self.reflect(send))


@dataclass(frozen=True, slots=True)
class SimScanner:
    """A flatbed: per-channel gain, an offset, flare, and read noise.

    ``gain`` and ``offset`` are the two ways a scanner lies, and they are **not**
    equally curable. A per-channel gain cancels when a print is judged relative to the
    paper it is on — the paper passes through the same gain — which is measured, and is
    why substrate-relative aiming works without any scanner characterization. The
    offset (flare, a specular pedestal) does *not* cancel, and removing it is what a
    reference target would buy.
    """

    name: str
    gain: tuple[float, float, float] = (1.0, 1.0, 1.0)
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    #: how much of the page's mean brightness leaks into every reading
    flare: float = 0.0
    #: read noise, in sRGB levels, one standard deviation
    noise: float = 0.0
    #: the deterministic seed. A simulator whose answer moves between two runs of the
    #: same test cannot be the thing a threshold is set against.
    seed: int = 7

    def scan(self, reflect: Reflect) -> Patches:
        """Read a page of reflectances as this scanner would."""
        arr = np.asarray(reflect, np.float64)
        lit = arr * np.array(self.gain, np.float64) + np.array(self.offset, np.float64)
        if self.flare:
            lit = lit * (1.0 - self.flare) + self.flare * float(arr.mean())
        out = colour.encode(np.clip(lit, 0.0, 1.0))
        if self.noise:
            rng = np.random.default_rng(self.seed)
            out = (out + rng.normal(0.0, self.noise, out.shape)).astype(np.float32)
        return np.clip(out, 0.0, 255.0).astype(np.float32)


@dataclass(frozen=True, slots=True)
class Rig:
    """A press and the scanner reading it — one printed-and-scanned round, on demand."""

    press: SimPress
    scanner: SimScanner

    def run(self, send: Patches) -> Patches:
        """Print a patch block and scan it back."""
        return self.scanner.scan(self.press.reflect(send))

    def chart(self, spec: Chart, send: Patches | None = None) -> Patches:
        """Print a whole chart — its own target unless another send is given."""
        return self.run(spec.target if send is None else send)

    def truth(self, send: Patches) -> Patches:
        """What the print really is, before the scanner gets an opinion."""
        return self.press.print_page(send)


#: ordinary warm matte: a near-white stock, mild tone response, a little contamination.
#: The medium where the old method *worked*, and it is here so that no stage can improve
#: the hard case by breaking the easy one.
MATTE = SimPress(
    name="matte-200",
    white=(247.0, 243.0, 236.0),
    black=(28.0, 26.0, 27.0),
    gamma=(1.15, 1.12, 1.20),
    crosstalk=np.array(
        [[1.00, 0.05, 0.03], [0.04, 1.00, 0.05], [0.03, 0.06, 1.00]], np.float64
    ),
)

#: the blue holographic sticker, anchored on the real profile's own bare-paper reading
#: of (144, 189, 208) — blue +64 above red — and its measured heaviest ink. This is the
#: medium the whole rebuild is about.
HOLO = SimPress(
    name="holo-plain",
    white=(144.0, 189.0, 208.0),
    black=(24.0, 27.0, 26.0),
    gamma=(1.30, 1.22, 1.35),
    crosstalk=np.array(
        [[1.00, 0.09, 0.06], [0.07, 1.00, 0.08], [0.05, 0.10, 1.00]], np.float64
    ),
)

#: a scanner that tells the truth, so a stage can be measured without the scanner's own
#: error in the way
HONEST = SimScanner(name="honest")

#: and one that does not: a per-channel gain and a small pedestal, which is what makes
#: "scans as the target" and "looks like the card" two different objectives
BIASED = SimScanner(
    name="biased",
    gain=(1.06, 1.00, 0.92),
    offset=(0.012, 0.004, 0.020),
    flare=0.02,
)

#: with read noise, for the runs that have to survive one
NOISY = SimScanner(
    name="noisy", gain=BIASED.gain, offset=BIASED.offset, flare=BIASED.flare, noise=1.5
)


def de00(first: Patches, second: Patches, white: Patches | None = None) -> float:
    """Mean ΔE00 between two patch blocks, optionally judged relative to a paper."""
    return float(colour.de00_rgb(first, second, white).mean())
