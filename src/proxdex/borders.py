"""Image helpers: frame colour for grading, size for format checks, and finding
where a card's printed border actually ends.

The border step needs one number per edge — how far in from the trim the printed
frame runs — and asking a person to drag four marks onto every card is the slow
part of the job. :func:`detect_inset` measures it by scanning inward until the
picture stops looking like the border.

What "looking like the border" means is decided **per scan line**, from the pixels
that line holds just inside the cut edge. The ring as a whole is very often not
one colour — a silver frame is a gradient, an ex-era frame is a sheen — and one
colour read for the whole ring needs a tolerance wide enough to cover that
variation, which is a tolerance wide enough to swallow the art too. Locally there
is nothing to average away.

It reports the *per-edge support* alongside the numbers — how many of the scan
lines agreed — because a card whose art runs to the frame has no clean border to
find on that edge, and a silent guess there would be worse than no answer at all.
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
#: the outer skin of the image, ignored entirely: the cut edge, its antialiasing,
#: and whatever a transparent die-cut corner was composited onto. The *scan* has
#: to start here too, not at pixel 0 — reading the border colour past the skin
#: while scanning from before it makes every card whose extreme edge differs from
#: its border (a black MTG border under a lighter cut edge) read as "no border at
#: all" on the first pixel.
_SKIN = 0.008
#: how deep the reference window runs, past the skin — the pixels taken to *be*
#: the border on this lane. Small, because the thinnest border proxdex sees is
#: about 2% of the card and the window has to fit inside it.
_REF = 0.012
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
#: how far outside the border's own observed range a pixel must fall to count as
#: not being the border. The range does the adapting — this is only a floor, so
#: that a dead-flat border still needs a real difference rather than sensor noise
#: to end, and it is absolute because a printed edge is a step, not a percentage.
_MIN_DELTA = 18.0
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
        """No border worth measuring — a borderless or full-art print.

        Not simply "the numbers came out small". Art that happens to change a
        little way in from the trim yields four plausible-looking numbers that no
        scan line agreed on, and cropping a card to its own art is the one outcome
        worse than declining to measure. So the finding is that **not one edge**
        found a border its own scan lines agreed about.
        """
        return max(self.inset) < _NO_FRAME or max(self.support) < _TRUST

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

    Each edge is scanned along many lines, each judged against its *own* few
    pixels just inside the cut edge, and the answer is the depth the most lines
    agree on — which keeps one piece of art that touches the frame from moving it,
    and makes the size of that agreement the edge's support. So a card this does
    not work on says which edge to check instead of quietly returning a plausible
    number.

    The result is a *pre-placement*, not a decision: proxdex never fits against a
    guess without saying so, and the align marks are still yours to nudge.
    """
    arr = load_rgb(path)
    measured = tuple(_edge_depth(arr, edge) for edge in Detection.EDGES)
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


def _lanes(arr: RGB, edge: str) -> RGB:
    """The image rotated so this edge's scan runs from index 0 inwards.

    Which is the only difference between the four edges — everything after this
    is one piece of code.
    """
    if edge == "top":
        return arr
    if edge == "bottom":
        return arr[::-1]
    if edge == "left":
        return arr.transpose(1, 0, 2)
    return arr.transpose(1, 0, 2)[::-1]  # right


def _edge_depth(arr: RGB, edge: str) -> tuple[float, float]:
    """One edge's border depth as a fraction, plus how much the scans agreed.

    Each scan line carries **its own** idea of what the border looks like: the
    range of colours the lane holds in a window just past the outer skin. A
    printed border is locally one colour, but the ring as a whole very often is
    not — a holo silver frame is a gradient, an ex-era frame is a sheen — and one
    colour read for the whole ring plus a tolerance wide enough to cover that
    variation is a tolerance wide enough to swallow the art as well. Locally,
    there is nothing to average away.
    """
    lanes = _lanes(arr, edge)
    span, across = lanes.shape[0], lanes.shape[1]
    short = min(arr.shape[0], arr.shape[1])
    skin = max(1, round(short * _SKIN))
    ref = max(2, round(short * _REF))
    depth = max(skin + ref + _RUN + 1, round(span * _MAX_DEPTH))
    margin = round(across * _EDGE_MARGIN)
    starts = np.linspace(margin, across - margin - _BLOCK, _SCANS, dtype=int)
    # each scan line is the mean of a small block of neighbouring lines, which
    # costs nothing and stops per-pixel scan noise in a flat black border from
    # reading as the end of it
    profile = np.stack(
        [lanes[:depth, start : start + _BLOCK, :].mean(axis=1) for start in starts],
        axis=1,
    )
    # what this lane's border *is*: the span of colours it holds just inside the
    # cut edge, widened by the floor. A window rather than a single colour, so a
    # border that ramps across the window is still described by it.
    window = profile[skin : skin + ref]
    low = window.min(axis=0) - _MIN_DELTA
    high = window.max(axis=0) + _MIN_DELTA
    off = ((profile < low) | (profile > high)).any(axis=2)
    # the border cannot end inside the window it was measured from
    off[: skin + ref] = False
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
    found, agreed = _consensus(lane_depths, span * _TOLERANCE)
    return found / span, agreed / len(starts)


def _consensus(depths: RGB, tol: float) -> tuple[float, int]:
    """The depth the most scan lines agree on, and how many agreed.

    The median is the wrong summary when the lanes are split between two edges,
    and on a decorated frame they are: an ex-era border has a thin printed line
    just inside the colour, so some lanes stop at the colour and some at the line
    — and the median then lands in the *gap* between the two, a number not one
    lane measured. The densest cluster is always a real edge, and how big it is
    happens to be exactly the honest confidence in it. Ties go to the shallower
    candidate, because the border is the outermost ring: anything deeper is the
    frame's own decoration.
    """
    best, most = 0.0, -1
    # the candidates are the measurements themselves, and `np.unique` sorts them,
    # so a strict `>` leaves the shallowest of any equally-supported cluster
    for candidate in np.unique(depths):
        near = np.abs(depths - candidate) <= tol
        count = int(np.count_nonzero(near))
        if count > most:
            best, most = float(candidate), count
    members = depths[np.abs(depths - best) <= tol]
    return float(np.median(members)), most
