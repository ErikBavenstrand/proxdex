"""Colour maths for judging a print: sRGB → Lab, ΔE00, and adapting to a substrate.

This module exists because of a defect, and the defect is worth stating. Calibration
used to score itself as a Euclidean distance in RGB, and that number cannot see the
thing that actually goes wrong with a print. The real ``holo-plain`` profile drove its
neutral axis 32 levels toward yellow over four rounds — monotonically, every round
worse — while the RGB residual it reported *fell* and ``Profile.plateau`` stood ready to
certify it as converged. Nothing on any screen could have told you.

So there are two answers here, and every calibration surface reports both:

* :func:`delta_e00` — CIEDE2000, the perceptual distance the whole industry judges a
  print by. A mean of it means something; a mean of RGB distance does not, because RGB
  distance underweights hue error exactly where the eye is most critical.
* :class:`Cast` — the mean a\\* and b\\* of the **neutral** patches alone. A cast is
  what you see first and what a mean over eighty patches hides, and "grey is grey"
  (a\\* = b\\* = 0) is the same definition G7 calibrates a press to.

:func:`relative_to` is the third piece: a print is judged **against the paper it is
on**. A white on a blue holographic sticker *is* blue-white, no ink makes it whiter,
and your eye adapts to the sheet in your hand. Judging such a print against an
absolute neutral measures the substrate rather than the calibration — and *aiming* at
one is what put the yellow there.

Everything is sRGB-referred and D65, deliberately: the target values, the scanner's
readings and a card's pixels are all sRGB-ish, so the reference white only has to be
*consistent*. Switching to the printing world's D50 would mean chromatically adapting
the target too, and buying nothing for it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

#: (..., 3) sRGB, 0..255
RGB = NDArray[np.float32]
#: (..., 3) CIE XYZ, Y in 0..1
XYZ = NDArray[np.float64]
#: (..., 3) CIE L*a*b*
Lab = NDArray[np.float64]

#: sRGB primaries → XYZ (D65), IEC 61966-2-1
_TO_XYZ = np.array(
    [
        [0.4123908, 0.3575843, 0.1804808],
        [0.2126390, 0.7151687, 0.0721923],
        [0.0193308, 0.1191948, 0.9505322],
    ],
    np.float64,
)
#: XYZ back to sRGB primaries — inverted once here rather than at every call
_FROM_XYZ = np.linalg.inv(_TO_XYZ)
#: D65 white, the reference every Lab value here is relative to
_D65 = np.array([0.9504559, 1.0, 1.0890578], np.float64)

#: the CIE Lab break between the cube root and its linear extension
_LAB_EPS = 216.0 / 24389.0
_LAB_KAPPA = 24389.0 / 27.0

#: below this the sRGB transfer function is linear
_SRGB_KNEE = 0.04045
_SRGB_KNEE_LIN = 0.0031308


def linearize(rgb: RGB) -> NDArray[np.float64]:
    """sRGB 0..255 → linear light 0..1."""
    n = np.asarray(rgb, np.float64).clip(0.0, 255.0) / 255.0
    return np.where(n <= _SRGB_KNEE, n / 12.92, ((n + 0.055) / 1.055) ** 2.4)


def encode(lin: NDArray[np.float64]) -> RGB:
    """Linear light 0..1 → sRGB 0..255."""
    n = np.asarray(lin, np.float64).clip(0.0, 1.0)
    out = np.where(n <= _SRGB_KNEE_LIN, n * 12.92, 1.055 * n ** (1.0 / 2.4) - 0.055)
    return (out * 255.0).astype(np.float32)


def to_xyz(rgb: RGB) -> XYZ:
    return linearize(rgb) @ _TO_XYZ.T


def to_lab(rgb: RGB) -> Lab:
    """sRGB 0..255 → CIE L\\*a\\*b\\* against D65."""
    r = to_xyz(rgb) / _D65
    f = np.where(r > _LAB_EPS, np.cbrt(r), (_LAB_KAPPA * r + 16.0) / 116.0)
    return np.stack(
        [
            116.0 * f[..., 1] - 16.0,
            500.0 * (f[..., 0] - f[..., 1]),
            200.0 * (f[..., 1] - f[..., 2]),
        ],
        axis=-1,
    )


def from_lab(lab: Lab) -> RGB:
    """CIE L\\*a\\*b\\* → sRGB 0..255, the inverse of :func:`to_lab`.

    Needed to *space a ramp perceptually*: a neutral ramp even in L\\* is the one an eye
    reads as even, and the previous chart's ``linspace(4, 252)`` in code values crowded
    almost all of its perceptual movement into the highlights.
    """
    arr = np.asarray(lab, np.float64)
    fy = (arr[..., 0] + 16.0) / 116.0
    fx = fy + arr[..., 1] / 500.0
    fz = fy - arr[..., 2] / 200.0
    f = np.stack([fx, fy, fz], axis=-1)
    cubed = f**3
    r = np.where(cubed > _LAB_EPS, cubed, (116.0 * f - 16.0) / _LAB_KAPPA)
    return encode((r * _D65) @ _FROM_XYZ.T)


def relative_to(rgb: RGB, white: RGB) -> RGB:
    """``rgb`` as it reads once the eye has adapted to a sheet whose paper is ``white``.

    A von Kries scaling, done in **linear light** — which is where a ratio of
    reflectances means something. Scaling the *encoded* values instead is the cheap
    version, and it is harmless on exactly the paper that does not need it: measured
    over a 17³ cube its worst case is 0.23 ΔE00 on near-white matte, 1.21 on a warm
    foil and **4.65** on a blue holographic sticker. The error lives in the shadows,
    where the sRGB curve is steepest (0.40 ΔE00 at mid-grey against 3.9 at code 12),
    so probing the middle proves nothing.

    A patch the same colour as the paper comes back as white, which is the point: it is
    the lightest thing the medium can produce, so it *is* this medium's white.
    """
    w = linearize(np.asarray(white, np.float32).reshape(-1, 3)[0])
    return encode(linearize(rgb) / np.maximum(w, 1e-6))


def delta_e00(first: Lab, second: Lab) -> NDArray[np.float64]:
    """CIEDE2000 between two (..., 3) blocks of Lab, elementwise.

    The full formulation, including the ``Rt`` rotation term that the simplified
    versions drop — it is what makes the metric behave in the blues, which on a blue
    substrate is the whole region under discussion.
    """
    lab1 = np.asarray(first, np.float64)
    lab2 = np.asarray(second, np.float64)
    l1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    l2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    c1 = np.hypot(a1, b1)
    c2 = np.hypot(a2, b2)
    c_bar = (c1 + c2) / 2.0
    c_bar7 = c_bar**7
    g = 0.5 * (1.0 - np.sqrt(c_bar7 / (c_bar7 + 25.0**7)))
    a1p, a2p = (1.0 + g) * a1, (1.0 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)

    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0
    grey = (c1p * c2p) == 0.0

    dlp = l2 - l1
    dcp = c2p - c1p
    dh = h2p - h1p
    dhp = np.where(
        grey,
        0.0,
        np.where(np.abs(dh) <= 180.0, dh, np.where(dh > 180.0, dh - 360.0, dh + 360.0)),
    )
    dhp_cap = 2.0 * np.sqrt(c1p * c2p) * np.sin(np.radians(dhp) / 2.0)

    lbp = (l1 + l2) / 2.0
    cbp = (c1p + c2p) / 2.0
    hsum = h1p + h2p
    hbp = np.where(
        grey,
        hsum,
        np.where(
            np.abs(h1p - h2p) <= 180.0,
            hsum / 2.0,
            np.where(hsum < 360.0, (hsum + 360.0) / 2.0, (hsum - 360.0) / 2.0),
        ),
    )

    t = (
        1.0
        - 0.17 * np.cos(np.radians(hbp - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * hbp))
        + 0.32 * np.cos(np.radians(3.0 * hbp + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * hbp - 63.0))
    )
    dtheta = 30.0 * np.exp(-(((hbp - 275.0) / 25.0) ** 2))
    cbp7 = cbp**7
    rc = 2.0 * np.sqrt(cbp7 / (cbp7 + 25.0**7))
    sl = 1.0 + (0.015 * (lbp - 50.0) ** 2) / np.sqrt(20.0 + (lbp - 50.0) ** 2)
    sc = 1.0 + 0.045 * cbp
    sh = 1.0 + 0.015 * cbp * t
    rt = -np.sin(np.radians(2.0 * dtheta)) * rc

    dl, dc, dhh = dlp / sl, dcp / sc, dhp_cap / sh
    return np.sqrt(dl**2 + dc**2 + dhh**2 + rt * dc * dhh)


def de00_rgb(first: RGB, second: RGB, white: RGB | None = None) -> NDArray[np.float64]:
    """ΔE00 between two blocks of sRGB, optionally judged relative to a substrate."""
    if white is None:
        return delta_e00(to_lab(first), to_lab(second))
    return delta_e00(to_lab(relative_to(first, white)), to_lab(second))


#: mean neutral chroma above which a print reads as tinted rather than grey. Two ΔE00
#: is the ordinary "a careful eye can just see it side by side" figure, and a cast is
#: the easiest error of all to see because every neutral on the card carries it.
CAST_VISIBLE = 2.0


@dataclass(frozen=True, slots=True)
class Cast:
    """How far the neutral axis has drifted off grey — mean a\\* and b\\*.

    Reported beside every residual, because it is a different question from "how far
    is the average patch out" and it is the one you notice. It is also the number that
    has to be *watched over rounds*: a fit whose mean error falls while its cast grows
    is diverging on the axis that matters, which is exactly what happened.
    """

    a: float = 0.0
    b: float = 0.0
    #: how many neutral patches it was measured over
    patches: int = 0

    @property
    def chroma(self) -> float:
        """Distance off the grey axis, in Lab units."""
        return math.hypot(self.a, self.b)

    @property
    def visible(self) -> bool:
        return self.chroma >= CAST_VISIBLE

    @property
    def hue(self) -> str:
        """Which way it is off, in the words a person would use."""
        if not self.visible:
            return "neutral"
        warm = "yellow" if self.b > 0 else "blue"
        tint = "red" if self.a > 0 else "green"
        # name the dominant axis first; both only when they are comparable
        if abs(self.b) >= 2.0 * abs(self.a):
            return warm
        if abs(self.a) >= 2.0 * abs(self.b):
            return tint
        return f"{warm}-{tint}"

    @property
    def text(self) -> str:
        return f"a* {self.a:+.2f} b* {self.b:+.2f} ({self.hue})"

    def json(self) -> dict[str, Any]:
        return {
            "a": self.a,
            "b": self.b,
            "chroma": self.chroma,
            "hue": self.hue,
            "visible": self.visible,
            "patches": self.patches,
        }

    @classmethod
    def of(cls, neutrals: Lab) -> Cast:
        """The cast of a block of Lab values that were *meant* to be neutral."""
        arr = np.asarray(neutrals, np.float64).reshape(-1, 3)
        if not arr.size:
            return cls()
        return cls(
            a=float(arr[:, 1].mean()), b=float(arr[:, 2].mean()), patches=len(arr)
        )

    @classmethod
    def read(cls, data: object) -> Cast:
        raw: dict[str, object] = data if isinstance(data, dict) else {}
        return cls(
            a=_float(raw.get("a")),
            b=_float(raw.get("b")),
            patches=int(_float(raw.get("patches"))),
        )


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    out = float(value)
    return 0.0 if not math.isfinite(out) else out
