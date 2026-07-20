"""Frame-border measurement and target math.

A vintage Pokémon card has a uniform coloured frame on the top and both
sides, and a deliberately *thicker* frame at the bottom (set symbol, ©). So
the bottom edge is never measured — the sides are the reference for what a
correct top should be.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from .config import Config

RGB = NDArray[np.float32]


def load_rgb(path: Path) -> RGB:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)


def frame_color(arr: RGB) -> RGB:
    """Median colour of the top + left + right edge bands (bottom excluded)."""
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


def _first_edge(line: NDArray[np.bool_], run: int) -> int:
    """Index of the first non-frame pixel that begins a run of ``run`` of them.

    The run requirement rejects single-pixel holo speckle noise inside the
    frame from being mistaken for the frame's inner boundary.
    """
    n = len(line)
    for i in range(n):
        if not line[i] and not line[i : i + run].any():
            return i
    return n


@dataclass(slots=True)
class Borders:
    w: int
    h: int
    top: float
    left: float
    right: float
    frame: tuple[float, float, float]

    @property
    def side(self) -> float:
        return (self.left + self.right) / 2

    @property
    def side_ratio(self) -> float:
        return self.side / self.w

    @property
    def top_ratio(self) -> float:
        return self.top / self.h


def measure(arr: RGB, cfg: Config) -> Borders:
    h, w, _ = arr.shape
    color = frame_color(arr)
    dist = np.sqrt(((arr - color) ** 2).sum(axis=2))
    is_frame = dist <= cfg.border_thresh
    run = max(3, int(0.004 * min(h, w)))
    # central strips avoid rounded / bloomed corners
    cx0, cx1 = int(w * 0.40), int(w * 0.60)
    cy0, cy1 = int(h * 0.40), int(h * 0.60)
    top = float(np.median([_first_edge(is_frame[:, x], run) for x in range(cx0, cx1)]))
    left = float(np.median([_first_edge(is_frame[y, :], run) for y in range(cy0, cy1)]))
    right = float(
        np.median([_first_edge(is_frame[y, ::-1], run) for y in range(cy0, cy1)])
    )
    frame = (float(color[0]), float(color[1]), float(color[2]))
    return Borders(w=w, h=h, top=top, left=left, right=right, frame=frame)


@dataclass(slots=True)
class Target:
    top: float
    side: float


def target(b: Borders, cfg: Config) -> Target:
    """Desired frame thickness (px). Configured ratio wins; else symmetric."""
    ref = max(b.left, b.right)
    side = cfg.target_side_ratio * b.w if cfg.target_side_ratio > 0 else ref
    top = cfg.target_top_ratio * b.h if cfg.target_top_ratio > 0 else ref
    return Target(top=top, side=side)
