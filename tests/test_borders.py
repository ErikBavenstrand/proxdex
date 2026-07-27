"""`detect_inset`: the number it finds, and — as much — when it says it isn't sure.

The synthetic cards here have a border whose width the test chose, so the
measurement can be asserted to the pixel. The rest of the module is about the
promise the docstring makes: a pre-placement that names the edges it could not
measure, rather than four numbers presented as equally sure.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from conftest import SCAN_H, SCAN_W, Arr, bordered_card, save
from proxdex.borders import Detection, detect_inset

#: a pixel either way on a 1040px edge — one line of block-averaged scan
PIXEL = 1.0 / SCAN_H


def measure(arr: Arr, tmp_path: Path) -> Detection:
    return detect_inset(save(arr, tmp_path / "card.png"))


def ramped_border(
    inset: tuple[float, float, float, float],
    *,
    lo: int = 140,
    hi: int = 230,
    art: tuple[int, int, int] = (40, 170, 70),
) -> Arr:
    """A card whose border is a *gradient* along its own length — a silver or
    holo frame. Locally one colour, globally not one at all, which is the case a
    single colour read for the whole ring cannot describe.
    """
    top, right, bottom, left = inset
    ramp = np.linspace(lo, hi, SCAN_W, dtype=np.float32)[None, :, None]
    arr = np.repeat(np.repeat(ramp, SCAN_H, axis=0), 3, axis=2)
    arr[
        round(top * SCAN_H) : SCAN_H - round(bottom * SCAN_H),
        round(left * SCAN_W) : SCAN_W - round(right * SCAN_W),
    ] = art
    return arr.astype(np.uint8)


def with_cut_edge(arr: Arr, *, depth: int = 4, colour: tuple[int, int, int]) -> Arr:
    """Paint the outermost pixels a different colour — the pale cut edge and its
    antialiasing that a real scan carries outside the printed border."""
    out = arr.copy()
    out[:depth, :] = out[-depth:, :] = colour
    out[:, :depth] = out[:, -depth:] = colour
    return out


class TestFlatBorder:
    def test_it_measures_the_border_it_was_given(self, tmp_path: Path) -> None:
        inset = (0.04, 0.05, 0.04, 0.05)
        found = measure(bordered_card(inset), tmp_path)
        assert found.inset == pytest.approx(inset, abs=PIXEL)
        assert found.support == (1.0, 1.0, 1.0, 1.0)
        assert found.reliable
        assert not found.frameless
        assert found.weak == ()

    def test_the_edges_are_reported_top_right_bottom_left(self, tmp_path: Path) -> None:
        """Four different widths, so a transposed axis or a reversed scan cannot
        pass — the bug this ordering invites."""
        inset = (0.03, 0.06, 0.05, 0.08)
        found = measure(bordered_card(inset), tmp_path)
        assert found.inset == pytest.approx(inset, abs=PIXEL)
        assert Detection.EDGES == ("top", "right", "bottom", "left")

    def test_a_white_border_measures_like_a_black_one(self, tmp_path: Path) -> None:
        """The ring colour is read off the card, so there is no per-game
        configuration: a yellow Pokémon frame and a black MTG one are one path."""
        pale = bordered_card(
            (0.04, 0.05, 0.04, 0.05), border=(248, 216, 32), art=(20, 40, 90)
        )
        found = measure(pale, tmp_path)
        assert found.inset == pytest.approx((0.04, 0.05, 0.04, 0.05), abs=PIXEL)
        assert found.reliable

    def test_a_noisy_border_is_still_one_ring(self, tmp_path: Path) -> None:
        """A holo frame varies on its own; each scan line judges against its own
        pixels rather than against a fixed number."""
        rng = np.random.default_rng(7)
        arr = bordered_card((0.04, 0.05, 0.04, 0.05), border=(24, 24, 28)).astype(
            np.int16
        )
        arr += rng.integers(-6, 7, size=arr.shape, dtype=np.int16)
        found = measure(np.clip(arr, 0, 255).astype(np.uint8), tmp_path)
        assert found.inset == pytest.approx((0.04, 0.05, 0.04, 0.05), abs=4 * PIXEL)
        assert found.reliable


class TestBordersThatAreNotOneColour:
    """The class of card the single-ring-colour reading could not measure at all.

    A silver-framed full-art Pokémon card is the one that turned this up: its
    border is a gradient, so the ring's own spread set a tolerance of ~156 levels,
    and the art sat 107 levels from the ring's median — inside it. Nothing read as
    "not the border" and the card came back with no border at all.
    """

    def test_a_gradient_border_is_measured(self, tmp_path: Path) -> None:
        inset = (0.04, 0.05, 0.04, 0.05)
        found = measure(ramped_border(inset), tmp_path)
        assert found.inset == pytest.approx(inset, abs=PIXEL)
        assert found.reliable
        assert not found.frameless

    def test_the_gradient_may_run_either_way(self, tmp_path: Path) -> None:
        """Nothing in the measurement is oriented, so a frame that lightens to the
        right must read exactly like one that darkens to the right."""
        inset = (0.04, 0.05, 0.04, 0.05)
        light = measure(ramped_border(inset, lo=140, hi=230), tmp_path)
        dark = measure(ramped_border(inset, lo=230, hi=140), tmp_path)
        assert light.inset == pytest.approx(dark.inset, abs=PIXEL)

    def test_a_pale_cut_edge_outside_a_dark_border(self, tmp_path: Path) -> None:
        """The scan has to start where the border colour is read from, not at pixel
        zero. A black MTG border under a pale cut edge used to end on the very
        first pixel — every such card reporting "no border at all"."""
        arr = with_cut_edge(
            bordered_card((0.04, 0.05, 0.04, 0.05), border=(18, 18, 22)),
            colour=(228, 228, 231),
        )
        found = measure(arr, tmp_path)
        assert found.inset == pytest.approx((0.04, 0.05, 0.04, 0.05), abs=PIXEL)
        assert not found.frameless

    def test_a_pale_cut_edge_outside_a_gradient_border(self, tmp_path: Path) -> None:
        """Both at once, which is what a real silver-bordered card is."""
        arr = with_cut_edge(
            ramped_border((0.04, 0.05, 0.04, 0.05)), colour=(250, 250, 250)
        )
        found = measure(arr, tmp_path)
        assert found.inset == pytest.approx((0.04, 0.05, 0.04, 0.05), abs=PIXEL)


class TestADecoratedFrame:
    """A frame with a printed line inside it, so the scan lines find two edges.

    The answer then has to be one of the two — the median of the pooled depths
    lands in the *gap* between them, a number not one scan line measured.
    """

    def two_edges(self, shallow: float, deep: float) -> Arr:
        arr = bordered_card((deep, 0.05, 0.05, 0.05))
        # a line inside the top border, over the right-hand part of the card, so
        # roughly 60% of the scan lines stop at it and the rest at the border
        band = round(shallow * SCAN_H)
        arr[band : band + 6, round(0.4 * SCAN_W) :] = (30, 30, 30)
        return arr

    def test_the_answer_is_a_depth_lines_actually_measured(
        self, tmp_path: Path
    ) -> None:
        shallow, deep = 0.02, 0.05
        found = measure(self.two_edges(shallow, deep), tmp_path)
        assert found.inset[0] == pytest.approx(shallow, abs=2 * PIXEL)
        # the midpoint is what the median of the pooled depths would have given
        assert abs(found.inset[0] - (shallow + deep) / 2) > 10 * PIXEL

    def test_the_support_is_the_size_of_that_cluster(self, tmp_path: Path) -> None:
        """Not the share of lines that hit *something* — the share that agreed on
        the answer given. Here 60% of the card carries the inner line, so that is
        what the number has to come out as."""
        found = measure(self.two_edges(0.02, 0.05), tmp_path)
        assert found.support[0] == pytest.approx(0.6, abs=0.05)
        assert found.support[1:] == (1.0, 1.0, 1.0)
        assert found.confidence == found.support[0]

    def test_the_note_states_the_numbers(self, tmp_path: Path) -> None:
        # 700×1000 so every inset lands on a whole pixel and the note is exact
        arr = bordered_card((0.04, 0.05, 0.04, 0.05), w=700, h=1000)
        found = measure(arr, tmp_path)
        assert found.note == (
            "border ends at T4.00 R5.00 B4.00 L5.00% — every edge measured cleanly."
        )


class TestBorderless:
    def test_a_flat_image_has_no_measurable_frame(self, tmp_path: Path) -> None:
        arr = np.full((SCAN_H, SCAN_W, 3), 90, dtype=np.uint8)
        found = measure(arr, tmp_path)
        assert found.frameless
        assert found.inset == (0.0, 0.0, 0.0, 0.0)
        assert "borderless" in found.note

    def test_full_art_is_a_finding_not_a_failure(self, tmp_path: Path) -> None:
        """Art running to the trim: the answer is "there is no border", which
        `border --auto` uses to skip measuring rather than to crop to the art."""
        rng = np.random.default_rng(3)
        arr = rng.integers(0, 256, size=(SCAN_H, SCAN_W, 3), dtype=np.uint8)
        found = measure(arr, tmp_path)
        assert found.frameless
        assert "nothing to fit but the card aspect" in found.note

    def test_frameless_is_about_agreement_not_small_numbers(
        self, tmp_path: Path
    ) -> None:
        """Art that changes a little way in produces four plausible numbers that
        no scan line agreed on. Cropping a card to its own art is the one outcome
        worse than declining to measure, so "not one edge agreed" is frameless."""
        rng = np.random.default_rng(3)
        arr = rng.integers(0, 256, size=(SCAN_H, SCAN_W, 3), dtype=np.uint8)
        found = measure(arr, tmp_path)
        assert max(found.inset) > 0.0  # it did find edges
        assert max(found.support) < 0.6  # and not one of them held up
        assert found.frameless

    def test_frameless_is_about_every_edge(self, tmp_path: Path) -> None:
        """Frameless is a fact about the whole card: a real 3% frame on all
        four edges is a card to fit, however modest."""
        found = measure(bordered_card((0.03, 0.03, 0.03, 0.03)), tmp_path)
        assert not found.frameless
        assert found.inset == pytest.approx((0.03, 0.03, 0.03, 0.03), abs=PIXEL)

    def test_a_border_thinner_than_the_reference_window_reads_as_none(
        self, tmp_path: Path
    ) -> None:
        """A known limit, pinned so it stays a documented one: each line reads what
        the border *is* from 0.8-2.0% in from the trim, so a border thinner than
        that window is described by the art as well as itself and comes back
        frameless. Real cards are 2-4%; a 1% frame reports "nothing to fit", which
        is the safe direction — never a confident wrong fit."""
        found = measure(bordered_card((0.01, 0.01, 0.01, 0.01)), tmp_path)
        assert found.frameless


class TestSupport:
    def test_art_touching_one_edge_makes_that_edge_weak(self, tmp_path: Path) -> None:
        arr = bordered_card((0.04, 0.05, 0.04, 0.05))
        # a decoration reaching into the top border on half the card's width —
        # those lanes end the frame early, the rest do not
        arr[: round(0.015 * SCAN_H), SCAN_W // 2 :] = (210, 60, 40)

        found = measure(arr, tmp_path)

        assert found.weak == ("top",)
        assert found.support[0] < 0.6
        assert min(found.support[1:]) == 1.0
        assert not found.reliable
        assert "top" in found.note
        assert "check that mark" in found.note

    def test_a_weak_edge_says_nothing_about_the_others(self, tmp_path: Path) -> None:
        """Support is per edge on purpose: the three clean edges keep their
        numbers, and the confidence is the weakest of the four."""
        arr = bordered_card((0.04, 0.05, 0.04, 0.05))
        arr[: round(0.015 * SCAN_H), SCAN_W // 2 :] = (210, 60, 40)
        found = measure(arr, tmp_path)
        assert found.inset[1:] == pytest.approx((0.05, 0.04, 0.05), abs=PIXEL)
        assert found.confidence == found.support[0]

    def test_two_weak_edges_are_both_named(self, tmp_path: Path) -> None:
        arr = bordered_card((0.04, 0.05, 0.04, 0.05))
        arr[: round(0.015 * SCAN_H), SCAN_W // 2 :] = (210, 60, 40)
        arr[SCAN_H // 2 :, : round(0.02 * SCAN_W)] = (210, 60, 40)
        found = measure(arr, tmp_path)
        assert set(found.weak) == {"top", "left"}
        assert "and" in found.note
        assert "check those mark" in found.note
