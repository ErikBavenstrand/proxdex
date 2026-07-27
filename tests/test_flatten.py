"""No stage image in the library carries an alpha channel.

A card's die-cut corners arrive transparent, and every tool downstream decides for
itself what is under them: Pillow's ``convert("RGB")`` keeps whatever bytes the
encoder happened to write there. It went unnoticed across fourteen files and two
stages of a real library — near-white corners on one card (212,225,229 against a
141,169,177 border), near-black on an upscaled one (mean 51, min 0) — because
nothing about it is visible on screen until the sheet is printed.

So the invariant is worth pinning: what fills a transparent pixel is the card's
*own* border colour, and it has to be applied wherever an image is filed, not only
where one is downloaded.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from proxdex.sources import flatten, transparent

#: a card-shaped RGBA array: a flat blue-grey border, art inside, corners cut out
BORDER = (140, 170, 180)
ART = (30, 90, 40)


def die_cut(w: int = 120, h: int = 168, corner: int = 10) -> Image.Image:
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[..., :3] = BORDER
    arr[..., 3] = 255
    arr[
        round(0.05 * h) : h - round(0.05 * h), round(0.07 * w) : w - round(0.07 * w), :3
    ] = ART
    # the corners: transparent, and holding a colour that is on the card nowhere
    for ys in (slice(0, corner), slice(h - corner, h)):
        for xs in (slice(0, corner), slice(w - corner, w)):
            arr[ys, xs] = (255, 0, 255, 0)
    return Image.fromarray(arr, mode="RGBA")


class TestTransparent:
    def test_it_sees_an_alpha_channel(self) -> None:
        assert transparent(die_cut())

    def test_it_sees_a_palette_with_transparency(self) -> None:
        """The case that turned this up was mode ``P`` with a transparency index,
        not RGBA — so the check cannot just look for an A channel."""
        paletted = die_cut().convert("P", palette=Image.Palette.ADAPTIVE)
        paletted.info["transparency"] = 0
        assert transparent(paletted)

    def test_a_plain_rgb_card_needs_nothing(self) -> None:
        assert not transparent(die_cut().convert("RGB"))


class TestFlatten:
    def test_the_fill_is_the_cards_own_border(self) -> None:
        """Not a fixed colour: black would swallow a Pokémon card's yellow corner
        and yellow would ring an MTG card, so it is read off this card's ring."""
        flat = flatten(die_cut())
        assert flat.mode == "RGB"
        corner = np.asarray(flat)[:8, :8].reshape(-1, 3)
        assert np.allclose(corner.mean(axis=0), BORDER, atol=2)

    def test_nothing_else_moves(self) -> None:
        """Only the transparent pixels are filled — the picture is not regraded."""
        before = np.asarray(die_cut().convert("RGB"))
        after = np.asarray(flatten(die_cut()))
        middle = (slice(20, -20), slice(20, -20))
        assert np.array_equal(before[middle], after[middle])

    def test_it_is_idempotent(self) -> None:
        """Every filing point applies it, so a file may pass through twice."""
        once = flatten(die_cut())
        assert not transparent(once)
        assert np.array_equal(np.asarray(once), np.asarray(flatten(once)))
