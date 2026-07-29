"""Shared fixtures: throwaway libraries and synthetic card images.

Nothing here touches a real library. A card's whole state is the filesystem, so
a card fixture is a temp folder; and a border is a flat ring of one colour, so a
card image is two numpy fills — which is why these tests can assert the exact
number that went in.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from PIL import Image

from proxdex.config import MARKER
from proxdex.library import Card, Library, Stage

#: the size a Scryfall PNG arrives at — the shape the real pipeline sees
SCAN_W, SCAN_H = 745, 1040

Arr = NDArray[np.uint8]


@pytest.fixture
def library(tmp_path: Path) -> Library:
    """A library root with the marker and an empty cards dir."""
    (tmp_path / MARKER).write_text('[library]\ngame = "pokemon"\n')
    (tmp_path / "cards").mkdir()
    return Library(tmp_path)


@pytest.fixture
def card(tmp_path: Path) -> Card:
    """One card folder, with no images in it yet."""
    folder = tmp_path / "cards" / "ex3-skyridge" / "ex3-90_charizard"
    folder.mkdir(parents=True)
    return Card(id="ex3-90", dir=folder, set_id="ex3")


def put_stage(card: Card, stage: Stage, face: int = 0) -> Path:
    """Create a stage file for a face. Only the *name* matters to the library
    model, so the bytes are empty."""
    path = card.stage_path(stage, face)
    path.write_bytes(b"")
    return path


def pngs(card: Card) -> set[str]:
    return {p.name for p in card.dir.glob("*.png")}


def bordered_card(
    inset: tuple[float, float, float, float] = (0.04, 0.05, 0.04, 0.05),
    *,
    border: tuple[int, int, int] = (10, 10, 12),
    art: tuple[int, int, int] = (210, 60, 40),
    w: int = SCAN_W,
    h: int = SCAN_H,
) -> Arr:
    """A card-shaped array: a flat frame of one colour, a flat art panel inside.

    ``inset`` is [top, right, bottom, left] as fractions of the image — the same
    shape the align marks and ``border --inner-*`` take.
    """
    top, right, bottom, left = inset
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :] = border
    arr[
        round(top * h) : h - round(bottom * h),
        round(left * w) : w - round(right * w),
    ] = art
    return arr


def save(arr: Arr, path: Path) -> Path:
    Image.fromarray(arr).save(path)
    return path
