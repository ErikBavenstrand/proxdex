"""Image helpers: frame colour for grading, size for format checks, and finding
where a card's printed border actually ends.

The border step needs one number per edge — how far in from the trim the printed
frame runs — and asking a person to drag four marks onto every card is the slow
part of the job. :func:`detect_inset` measures it: the frame is a ring of nearly
one colour, so each edge is scanned inward until the picture stops looking like
the ring. It reports the *per-edge support* alongside the numbers — how many of
the scan lines agreed — because a card whose art runs to the frame has no clean
border to find on that edge, and a silent guess there would be worse than no
answer at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image

RGB = NDArray[np.float32]


def load_rgb(path: Path) -> RGB:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)


def size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.width, im.height


def frame_color(arr: RGB) -> RGB:
    """Median colour of the top + left + right edge bands (bottom excluded).

    The card frame is uniform on top/sides; the bottom is deliberately thicker,
    so it's left out. Used by grading to white-balance every card's frame to a
    common colour.
    """
    h, w, _ = arr.shape
    band = max(2, int(0.012 * min(h, w)))
    edge = np.concatenate(
        [
            arr[:band, :, :].reshape(-1, 3),
            arr[:, :band, :].reshape(-1, 3),
            arr[:, -band:, :].reshape(-1, 3),
        ]
    )
    return np.median(edge, axis=0).astype(np.float32)


# --------------------------------------------------- inner border detection ---
#: where to read the border colour from: a band this far in from the trim, past
#: the rounded corners and any antialiased cut edge, still well outside the art
_RING_FROM = 0.008
_RING_TO = 0.022
#: how far inward to look for the end of the frame, as a fraction of the edge's
#: own dimension. A real border is 3-4% of the card; 25% is generous and bounds
#: the work.
_MAX_DEPTH = 0.25
#: scan lines per edge, spread over the middle of it — the corners are rounded
#: and the art often reaches the frame near them
_SCANS = 64
_EDGE_MARGIN = 0.12
#: neighbouring lines averaged into each scan line, to smooth out scan noise
_BLOCK = 5
#: a pixel counts as "not the border" once any channel is this far off the ring
#: colour. Floors the adaptive threshold so a perfectly flat border still needs a
#: real difference, not sensor noise, to end.
_MIN_DELTA = 16.0
_DELTA_SPREAD = 6.0
#: consecutive off-colour pixels required, so one speck of dust is not an edge
_RUN = 3
#: two scan lines count as agreeing when they land this close, as a fraction of
#: the edge's dimension — about a third of a millimetre on a real card
_TOLERANCE = 0.004
#: below this the card has no measurable frame at all — a full-art or borderless
#: print, which is a finding rather than a failure
_NO_FRAME = 0.004
#: agreement across the scan lines below which the numbers should not be trusted
_TRUST = 0.6


@dataclass(frozen=True, slots=True)
class Detection:
    """Where the printed frame ends, measured off the image itself."""

    #: inner border edge as fractions of the image, [top, right, bottom, left] —
    #: exactly what ``border --inner-*`` and the align marks take
    inset: tuple[float, float, float, float]
    #: per edge, the share of scan lines that agreed on that edge's number.
    #: Reported per edge on purpose: a decorated frame reaching into the top
    #: border says nothing about the left one, which may be dead flat.
    support: tuple[float, float, float, float]
    #: one line on what was found, for the CLI readout and the UI panel
    note: str

    #: the edges, in the order everything in proxdex writes them
    EDGES = ("top", "right", "bottom", "left")

    @property
    def confidence(self) -> float:
        """The weakest edge — how much to trust the set of four as a whole."""
        return min(self.support)

    @property
    def reliable(self) -> bool:
        """Whether every edge measured cleanly enough to fit against as-is."""
        return self.confidence >= _TRUST

    @property
    def frameless(self) -> bool:
        """No measurable border on any edge — a borderless or full-art print."""
        return max(self.inset) < _NO_FRAME

    @property
    def weak(self) -> tuple[str, ...]:
        """Which edges the scan lines disagreed about — the ones to look at."""
        return tuple(
            name
            for name, share in zip(self.EDGES, self.support, strict=True)
            if share < _TRUST
        )


def detect_inset(path: Path) -> Detection:
    """Find the inner edge of the printed border on all four sides.

    The frame is a ring of nearly one colour, so its colour is read from a band
    just inside the trim and each edge is then scanned inward until the picture
    stops matching it. Scanning many lines per edge and taking the median keeps
    one piece of art that touches the frame from moving the answer, and the share
    of lines that landed on that median becomes the edge's support — so a card
    this does not work on says which edge to check instead of quietly returning a
    plausible number.

    The result is a *pre-placement*, not a decision: proxdex never fits against a
    guess without saying so, and the align marks are still yours to nudge.
    """
    arr = load_rgb(path)
    ring, delta = _ring(arr)
    measured = tuple(_edge_depth(arr, ring, delta, edge) for edge in Detection.EDGES)
    inset = (
        round(measured[0][0], 5),
        round(measured[1][0], 5),
        round(measured[2][0], 5),
        round(measured[3][0], 5),
    )
    support = (
        round(measured[0][1], 3),
        round(measured[1][1], 3),
        round(measured[2][1], 3),
        round(measured[3][1], 3),
    )
    found = Detection(inset, support, "")
    return Detection(inset, support, _note(found))


def _note(found: Detection) -> str:
    if found.frameless:
        return (
            "no printed border found on any edge — this looks like a borderless "
            "or full-art print, so there is nothing to fit but the card aspect."
        )
    edges = " ".join(
        f"{e}{v * 100:.2f}" for e, v in zip("TRBL", found.inset, strict=True)
    )
    if found.reliable:
        return f"border ends at {edges}% — every edge measured cleanly."
    return (
        f"border ends at about {edges}%. The {_and(found.weak)} scan lines "
        "disagreed — a decorated frame or art touching the border — so check "
        f"{'those' if len(found.weak) > 1 else 'that'} mark(s) before reshaping."
    )


def _and(names: tuple[str, ...]) -> str:
    if len(names) < 2:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]


def _ring(arr: RGB) -> tuple[RGB, float]:
    """The border's own colour, and how far off it a pixel must be to not be it.

    The threshold is adaptive: a flat black MTG border tolerates very little
    variation, while a holo Pokémon border varies on its own, so the ring's
    spread sets the bar and :data:`_MIN_DELTA` floors it.
    """
    h, w, _ = arr.shape
    short = min(h, w)
    lo, hi = max(1, round(short * _RING_FROM)), max(2, round(short * _RING_TO))
    band = np.concatenate(
        [
            arr[lo:hi, :, :].reshape(-1, 3),
            arr[-hi:-lo, :, :].reshape(-1, 3),
            arr[:, lo:hi, :].reshape(-1, 3),
            arr[:, -hi:-lo, :].reshape(-1, 3),
        ]
    )
    ring = np.median(band, axis=0).astype(np.float32)
    spread = float(np.median(np.abs(band - ring).max(axis=1)))
    return ring, max(_MIN_DELTA, spread * _DELTA_SPREAD)


def _edge_depth(arr: RGB, ring: RGB, delta: float, edge: str) -> tuple[float, float]:
    """One edge's border depth as a fraction, plus how much the scans agreed.

    Every edge is rotated so the scan always runs from index 0 inwards, which is
    the only difference between the four of them.
    """
    if edge == "top":
        lanes = arr
    elif edge == "bottom":
        lanes = arr[::-1]
    elif edge == "left":
        lanes = arr.transpose(1, 0, 2)
    else:  # right
        lanes = arr.transpose(1, 0, 2)[::-1]

    span, across = lanes.shape[0], lanes.shape[1]
    depth = max(_RUN + 1, round(span * _MAX_DEPTH))
    margin = round(across * _EDGE_MARGIN)
    starts = np.linspace(margin, across - margin - _BLOCK, _SCANS, dtype=int)
    # each scan line is the mean of a small block of neighbouring lines, which
    # costs nothing and stops per-pixel scan noise in a flat black border from
    # reading as the end of it
    profile = np.stack(
        [lanes[:depth, start : start + _BLOCK, :].mean(axis=1) for start in starts],
        axis=1,
    )
    off = np.abs(profile - ring).max(axis=2) > delta
    # the first run of _RUN consecutive off-colour pixels is where the frame ends
    runs = off[: depth - _RUN + 1]
    for k in range(1, _RUN):
        runs = runs & off[k : depth - _RUN + 1 + k]
    hit = runs.any(axis=0)
    # a lane that never leaves the border colour says nothing about where the
    # frame ends — drop it rather than let it pull the answer inward
    if not hit.any():
        return 0.0, 0.0
    lane_depths = runs.argmax(axis=0)[hit].astype(np.float32)
    middle = float(np.median(lane_depths))
    agreed = int(np.count_nonzero(np.abs(lane_depths - middle) <= span * _TOLERANCE))
    return middle / span, agreed / len(starts)
