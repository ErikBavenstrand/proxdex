"""Colour maths, and it earns its place because every number it produces is one a
calibration *decides* on and no screen can contradict.

A wrong ΔE00 does not fail: it silently mis-scores every round of every profile, so
the loop optimises the wrong direction and reports progress doing it — which is
precisely the defect this module was written for. So the metric is held against
**Sharma et al.'s published reference table**, the same
data every implementation is checked against, rather than against itself.

The second half pins the two things a *cast* has to do: be measured off the neutrals
alone (a mean over eighty patches hides it), and be measured **relative to the paper**
(a blue substrate judged against an absolute neutral reports a cast that is the paper's
and not the print's — and aiming at that absolute neutral is what put yellow on every
card).
"""

from __future__ import annotations

import numpy as np
import pytest

from proxdex.colour import (
    CAST_VISIBLE,
    Cast,
    de00_rgb,
    delta_e00,
    encode,
    linearize,
    relative_to,
    to_lab,
)

#: Sharma, Wu & Dalal's CIEDE2000 test data — (Lab1, Lab2, expected ΔE00). The
#: near-neutral and near-blue rows are the ones that separate a correct
#: implementation from one that dropped the `Rt` rotation term, which is why they
#: are over-represented here: a blue substrate is the whole subject.
SHARMA: tuple[
    tuple[tuple[float, float, float], tuple[float, float, float], float], ...
] = (
    ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
    ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
    ((50.0, 2.8361, -74.0200), (50.0, 0.0, -82.7485), 3.4412),
    ((50.0, -1.3802, -84.2814), (50.0, 0.0, -82.7485), 1.0000),
    ((50.0, -1.1848, -84.8006), (50.0, 0.0, -82.7485), 1.0000),
    ((50.0, -0.9009, -85.5211), (50.0, 0.0, -82.7485), 1.0000),
    ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
    ((50.0, -1.0, 2.0), (50.0, 0.0, 0.0), 2.3669),
    ((50.0, 2.4900, -0.0010), (50.0, -2.4900, 0.0009), 7.1792),
    ((50.0, 2.4900, -0.0010), (50.0, -2.4900, 0.0011), 7.2195),
    ((50.0, -0.0010, 2.4900), (50.0, 0.0009, -2.4900), 4.8045),
    ((50.0, 2.5, 0.0), (50.0, 0.0, -2.5), 4.3065),
    ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
    ((61.2901, 3.7196, -5.3901), (61.4292, 2.2480, -4.9620), 1.8731),
    ((35.0831, -44.1164, 3.7933), (35.0232, -40.0716, 1.5901), 1.8645),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((36.4612, 47.8580, 18.3852), (36.2715, 50.5065, 21.2231), 1.4146),
    ((90.8027, -2.0831, 1.4410), (91.1528, -1.6435, 0.0447), 1.4441),
    ((90.9257, -0.5406, -0.9208), (88.6381, -0.8985, -0.7239), 1.5381),
    ((6.7747, -0.2908, -2.4247), (5.8714, -0.0985, -2.2286), 0.6377),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
)

#: what the real `holo-plain` holographic sticker measured, bare, as the scanner saw
#: it. Kept as a literal because every claim in docs/calibration.md rests on it.
HOLO_SUBSTRATE = (144.0, 189.0, 208.0)


class TestDeltaE00:
    @pytest.mark.parametrize(("lab1", "lab2", "want"), SHARMA)
    def test_matches_the_published_reference(
        self,
        lab1: tuple[float, float, float],
        lab2: tuple[float, float, float],
        want: float,
    ) -> None:
        got = delta_e00(np.array([lab1]), np.array([lab2]))
        assert float(got[0]) == pytest.approx(want, abs=1e-4)

    def test_is_symmetric(self) -> None:
        a = np.array([row[0] for row in SHARMA])
        b = np.array([row[1] for row in SHARMA])
        assert delta_e00(a, b) == pytest.approx(delta_e00(b, a), abs=1e-9)

    def test_a_colour_is_zero_from_itself(self) -> None:
        a = np.array([row[0] for row in SHARMA])
        assert float(delta_e00(a, a).max()) == pytest.approx(0.0, abs=1e-9)

    def test_vectorizes(self) -> None:
        """Every caller scores a whole chart at once, not a patch at a time."""
        a = np.array([row[0] for row in SHARMA])
        b = np.array([row[1] for row in SHARMA])
        want = np.array([row[2] for row in SHARMA])
        assert delta_e00(a, b) == pytest.approx(want, abs=1e-4)


class TestLab:
    def test_the_ends_and_the_middle_of_the_grey_axis_are_neutral(self) -> None:
        greys = np.array(
            [[0.0, 0.0, 0.0], [128.0, 128.0, 128.0], [255.0, 255.0, 255.0]]
        )
        lab = to_lab(greys)
        assert lab[:, 1] == pytest.approx(np.zeros(3), abs=1e-9)
        assert lab[:, 2] == pytest.approx(np.zeros(3), abs=1e-9)
        assert float(lab[0, 0]) == pytest.approx(0.0, abs=1e-9)
        assert float(lab[2, 0]) == pytest.approx(100.0, abs=1e-6)

    def test_the_transfer_function_round_trips(self) -> None:
        v = np.linspace(0, 255, 256, dtype=np.float32).repeat(3).reshape(-1, 3)
        assert encode(linearize(v)) == pytest.approx(v, abs=1e-3)


class TestRelativeToTheSubstrate:
    def test_paper_is_this_medium_s_white(self) -> None:
        """The lightest thing a medium can make *is* its white, by definition."""
        paper = np.array(HOLO_SUBSTRATE, np.float32)
        assert relative_to(paper.reshape(1, 3), paper) == pytest.approx(
            np.full((1, 3), 255.0), abs=1e-3
        )

    def test_a_blue_substrate_reads_neutral_against_itself_and_blue_absolutely(
        self,
    ) -> None:
        """The measured defect, in two lines.

        Judged absolutely the holographic sticker is a strong blue-green cast that no
        ink can remove; judged against itself it is this medium's neutral. Aiming at
        the first is what drove `holo-plain` 32 levels toward yellow.
        """
        paper = np.array(HOLO_SUBSTRATE, np.float32)
        absolute = Cast.of(to_lab(paper.reshape(1, 3)))
        assert absolute.visible
        assert absolute.b < -10.0  # blue
        assert absolute.hue in {"blue", "blue-green"}

        adapted = Cast.of(to_lab(relative_to(paper.reshape(1, 3), paper)))
        assert not adapted.visible
        assert adapted.hue == "neutral"

    def test_it_scales_in_linear_light_not_in_code_values(self) -> None:
        """Measured, and the shape of the error is the point.

        Scaling the *encoded* values instead is what the first draft did, and at
        mid-grey it is worth only 0.40 ΔE00 — which is why probing there proves
        nothing. The error lives in the **shadows**, where the sRGB curve is steepest,
        and it grows with how tinted the substrate is: over a 17³ cube the worst case
        is 0.23 ΔE00 on near-white matte, 1.21 on a warm foil and **4.65** on this
        holographic sticker, at a near-black red. So the cheap version is harmless on
        the paper that does not need it and wrong on the paper that does.
        """
        g = np.linspace(0, 255, 17, dtype=np.float32)
        cube = (
            np.stack(np.meshgrid(g, g, g, indexing="ij"), axis=-1)
            .reshape(-1, 3)
            .astype(np.float32)
        )

        def gap(paper: tuple[float, float, float]) -> float:
            p = np.array(paper, np.float32)
            proper = relative_to(cube, p)
            naive = (cube * (255.0 / p)).astype(np.float32)
            return float(delta_e00(to_lab(proper), to_lab(naive)).max())

        assert gap((250.0, 249.0, 246.0)) < 0.5
        assert gap(HOLO_SUBSTRATE) > 4.0
        # and it really is a shadow effect, not a general offset
        mid = np.array([[128.0, 128.0, 128.0]], np.float32)
        dark = np.array([[16.0, 16.0, 16.0]], np.float32)
        paper = np.array(HOLO_SUBSTRATE, np.float32)
        at_mid = float(
            delta_e00(
                to_lab(relative_to(mid, paper)),
                to_lab((mid * (255.0 / paper)).astype(np.float32)),
            )[0]
        )
        at_dark = float(
            delta_e00(
                to_lab(relative_to(dark, paper)),
                to_lab((dark * (255.0 / paper)).astype(np.float32)),
            )[0]
        )
        assert at_dark > 3.0 * at_mid

    def test_de00_rgb_takes_the_substrate_or_leaves_it(self) -> None:
        paper = np.array(HOLO_SUBSTRATE, np.float32)
        got = np.array([[144.0, 189.0, 208.0]], np.float32)
        want = np.array([[255.0, 255.0, 255.0]], np.float32)
        assert float(de00_rgb(got, want, paper)[0]) == pytest.approx(0.0, abs=1e-3)
        assert float(de00_rgb(got, want)[0]) > 10.0


class TestCast:
    def test_it_is_measured_over_the_neutrals_it_is_given(self) -> None:
        lab = np.array([[50.0, 4.0, -6.0], [60.0, 2.0, -2.0]])
        cast = Cast.of(lab)
        assert cast.a == pytest.approx(3.0)
        assert cast.b == pytest.approx(-4.0)
        assert cast.patches == 2
        assert cast.chroma == pytest.approx(5.0)

    def test_nothing_measured_is_not_a_clean_sheet_it_is_no_answer(self) -> None:
        cast = Cast.of(np.zeros((0, 3)))
        assert cast.patches == 0
        assert not cast.visible

    @pytest.mark.parametrize(
        ("a", "b", "hue"),
        [
            (0.0, 6.0, "yellow"),
            (0.0, -6.0, "blue"),
            (6.0, 0.0, "red"),
            (-6.0, 0.0, "green"),
            (5.0, 5.0, "yellow-red"),
            (0.1, 0.2, "neutral"),
        ],
    )
    def test_it_says_which_way_in_the_words_a_person_would_use(
        self, a: float, b: float, hue: str
    ) -> None:
        assert Cast(a=a, b=b, patches=1).hue == hue

    def test_visible_is_a_stated_threshold(self) -> None:
        assert not Cast(a=0.0, b=CAST_VISIBLE - 0.01, patches=1).visible
        assert Cast(a=0.0, b=CAST_VISIBLE, patches=1).visible

    def test_it_survives_a_round_trip_through_json(self) -> None:
        cast = Cast(a=-1.25, b=3.5, patches=21)
        back = Cast.read(cast.json())
        assert (back.a, back.b, back.patches) == (cast.a, cast.b, cast.patches)

    def test_the_reader_is_total_over_rubbish(self) -> None:
        for junk in (None, [], "nope", {"a": "x", "b": None}, {"a": float("nan")}):
            assert Cast.read(junk).patches == 0
