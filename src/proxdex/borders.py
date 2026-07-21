"""Image helpers shared by grading (frame colour) and format checks (size)."""

from __future__ import annotations

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
