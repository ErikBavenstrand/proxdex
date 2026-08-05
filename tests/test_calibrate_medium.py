"""The medium itself: the patch set that can measure it, the paper, the aim, and what
happens to a colour the paper cannot make.

Everything here is invisible until paper, which is why it is in the suite. The four
things pinned are the four that were wrong:

* the chart could not measure a **substrate** (its lightest patch was printed at 252,
  which is ink) and could not **linearize a channel** (it had no per-channel ramps at
  all), so two of the four industry stages were unreachable;
* the paper's own colour was never read, so aiming at an absolute neutral asked a blue
  holographic sticker for ink that does not exist;
* what could not be reached was **clipped per channel**, which moves the hue — and it
  moves it worst on a *neutral*, which is the one case chroma compression alone cannot
  help with and the one the real failure was;
* rounds scanned in different instrument states were pooled as though they were not.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from proxdex import calibrate, colour, profiles
from proxdex.calibrate import Intent, Role, Substrate, SurveySize
from proxdex.profiles import Profile, Round

#: the real holographic sticker, as its own scan read it: blue +64 above red
HOLO = (144.0, 189.0, 208.0)
#: and the heaviest ink it took
HOLO_BLACK = (24.0, 27.0, 26.0)


def _substrate(white: tuple[float, float, float] = HOLO) -> Substrate:
    return Substrate(white=white, black=HOLO_BLACK, spread=0.4, patches=35)


class TestTheChartCanMeasureWhatTheModelNeeds:
    @pytest.mark.parametrize("size", list(SurveySize))
    def test_every_role_is_present_at_every_density(self, size: SurveySize) -> None:
        """The ramps, neutrals, substrate and max-ink are the same at every size —
        only the interior lattice shrinks, because the others are what the
        linearization, the grey balance and the substrate term are built from."""
        card = calibrate.survey(size)
        for role in Role:
            assert len(card.of_role(role)) > 0, f"{size} has no {role}"

    @pytest.mark.parametrize("size", list(SurveySize))
    def test_it_fills_its_grid_exactly(self, size: SurveySize) -> None:
        card = calibrate.survey(size)
        assert len(card) == card.cols * card.rows

    def test_only_the_lattice_shrinks(self) -> None:
        full = calibrate.survey(SurveySize.FULL)
        quarter = calibrate.survey(SurveySize.QUARTER)
        assert len(quarter.of_role(Role.LATTICE)) < len(full.of_role(Role.LATTICE))
        assert len(quarter.of_role(Role.MAX_INK)) == len(full.of_role(Role.MAX_INK))

    def test_substrate_patches_are_bare_paper_and_spread_out(self) -> None:
        """Bare, because that is the only thing that measures the paper; spread across
        the sheet, because that is the only handle on a flatbed's non-uniformity."""
        card = calibrate.survey(SurveySize.FULL)
        bare = card.substrate
        assert (card.target[bare] == 255.0).all()
        rows = bare // card.cols
        assert len(set(rows.tolist())) > card.rows // 2, "clumped, not spread"

    def test_a_channel_ramp_moves_one_channel_only(self) -> None:
        card = calibrate.survey(SurveySize.FULL)
        for channel, idx in card.ramps.items():
            block = card.target[idx]
            others = [c for c in range(3) if c != channel]
            assert (block[:, others] == 255.0).all()
            assert block[:, channel].min() == 0.0
            assert block[:, channel].max() == 255.0

    def test_the_neutral_ramp_is_even_in_lstar_not_in_code_values(self) -> None:
        """The old ramp was linspace(4, 252) in device values, which crowds almost all
        of its perceptual movement into the highlights — the one region where a tinted
        substrate shows through hardest."""
        card = calibrate.survey(SurveySize.FULL)
        lstar = colour.to_lab(card.target[card.neutrals])[:, 0]
        steps = np.diff(np.sort(lstar))
        assert steps.std() < 0.5, "L* steps are not even"

    def test_a_repeat_names_the_patch_it_duplicates(self) -> None:
        card = calibrate.survey(SurveySize.FULL)
        for i in card.of_role(Role.REPEAT):
            patch = card.patches[i]
            assert patch.of >= 0
            assert card.patches[patch.of].rgb == patch.rgb


class TestReadingThePaper:
    def test_it_reads_the_white_the_black_and_how_much_they_disagree(self) -> None:
        card = calibrate.survey(SurveySize.QUARTER)
        scanned = card.target.copy()
        scanned[card.substrate] = np.array(HOLO, np.float32)
        scanned[card.of_role(Role.MAX_INK)] = np.array(HOLO_BLACK, np.float32)
        sub = Substrate.of(scanned, card)
        assert sub.measured
        assert sub.white == pytest.approx(HOLO, abs=0.5)
        assert sub.black == pytest.approx(HOLO_BLACK, abs=0.5)
        assert sub.spread == pytest.approx(0.0, abs=1e-3)
        assert sub.even

    def test_a_blue_paper_reports_a_blue_cast_that_no_ink_removes(self) -> None:
        assert _substrate().cast.visible
        assert _substrate().cast.b < -10.0

    def test_readings_that_disagree_across_the_sheet_are_said_out_loud(self) -> None:
        """A holographic or metallic stock changes colour with the angle it is lit at,
        so one scanning geometry cannot measure it — and a scan that comes back clean
        off such a stock is evidence *against* the reading, not for it."""
        card = calibrate.survey(SurveySize.QUARTER)
        scanned = card.target.copy()
        bare = card.substrate
        half = len(bare) // 2
        scanned[bare[:half]] = np.array([144.0, 189.0, 208.0], np.float32)
        scanned[bare[half:]] = np.array([210.0, 190.0, 150.0], np.float32)
        sub = Substrate.of(scanned, card)
        assert not sub.even
        assert "angle" in sub.warning

    def test_nothing_measured_is_not_a_white_paper_claim(self) -> None:
        assert not Substrate().measured
        assert (
            Substrate.of(np.zeros((0, 3), np.float32), calibrate.survey()).measured
            is False
        )

    def test_it_survives_a_round_trip_and_a_rubbish_file(self) -> None:
        back = Substrate.read(_substrate().json())
        assert back.white == pytest.approx(HOLO)
        for junk in (None, [], "no", {"white": "x"}, {"white": [1, 2]}):
            assert not Substrate.read(junk).measured


class TestTheAim:
    def test_full_adaptation_asks_the_paper_for_its_own_white(self) -> None:
        got = calibrate.aim(
            np.array([[255.0, 255.0, 255.0]], np.float32), _substrate(), Intent(1.0)
        )
        assert got[0] == pytest.approx(HOLO, abs=1.0)

    def test_no_adaptation_is_the_old_behaviour_and_is_reachable_deliberately(
        self,
    ) -> None:
        goal = np.array([[136.0, 136.0, 136.0]], np.float32)
        assert calibrate.aim(goal, _substrate(), Intent(0.0)) == pytest.approx(goal)

    def test_partial_adaptation_lands_between(self) -> None:
        goal = np.array([[136.0, 136.0, 136.0]], np.float32)
        half = calibrate.aim(goal, _substrate(), Intent(0.5))[0]
        full = calibrate.aim(goal, _substrate(), Intent(1.0))[0]
        assert (full < half).all()
        assert (half < goal[0]).all()

    def test_an_unmeasured_substrate_cannot_be_aimed_at(self) -> None:
        goal = calibrate.target()
        assert calibrate.aim(goal, Substrate(), Intent(1.0)) == pytest.approx(goal)

    def test_the_intent_is_stated_in_words_and_survives_json(self) -> None:
        assert "relative" in Intent(1.0).text
        assert "absolute" in Intent(0.0).text
        assert Intent.read(Intent(0.25).json()).adaptation == pytest.approx(0.25)
        assert Intent.read({}).adaptation == 1.0, "relative is the default"
        assert Intent.read({"adaptation": 9.0}).adaptation == 1.0
        assert Intent.read({"adaptation": -3.0}).adaptation == 0.0


class TestWhatHappensToAColourThePaperCannotMake:
    """The measured failure, and it is a *neutral* — which is why chroma compression
    alone does not fix it.

    A 200-grey wanted send (273, 264, 299) and got (255, 255, 255): 44 of blue refused
    against 18 of red. A neutral has no chroma to give up, so the first version of this
    compression did nothing for it and the per-channel clamp happened anyway.
    """

    #: a synthetic correction that overdrives every channel by a different amount, which
    #: is what reproduces the real numbers exactly
    SCALE = np.array([348.0, 337.0, 381.0])

    def _correction(self) -> calibrate.Correction:
        corr = calibrate.Correction(coef=calibrate.IDENTITY.copy())
        for channel, value in enumerate(self.SCALE):
            corr.coef[channel + 1, channel] = value
        return corr

    def _delivered(self, send: np.ndarray) -> np.ndarray:
        """What you actually get, inverting this diagonal model."""
        return (send / self.SCALE * 255.0).astype(np.float32)

    def test_it_reproduces_the_real_send(self) -> None:
        raw = self._correction().raw(np.array([[200.0, 200.0, 200.0]], np.float32))[0]
        assert raw == pytest.approx([273.0, 264.0, 299.0], abs=1.0)

    def test_a_per_channel_clip_delivers_a_cast_where_grey_was_asked_for(self) -> None:
        raw = self._correction().raw(np.array([[200.0, 200.0, 200.0]], np.float32))[0]
        got = self._delivered(raw.clip(0.0, 255.0))
        cast = colour.Cast.of(colour.to_lab(got.reshape(1, 3)))
        assert cast.visible, "this is the yellow, and it is what clipping does"
        assert cast.b > 5.0

    def test_compression_delivers_a_neutral_that_is_simply_darker(self) -> None:
        corr = self._correction()
        want = np.array([[200.0, 200.0, 200.0]], np.float32)
        got = self._delivered(corr.send(want)[0])
        cast = colour.Cast.of(colour.to_lab(got.reshape(1, 3)))
        assert not cast.visible, cast.text
        assert got.mean() < 200.0, "it gives up lightness, which is the honest loss"

    def test_it_never_sends_out_of_range(self) -> None:
        corr = self._correction()
        g = np.linspace(0, 255, 9, dtype=np.float32)
        cube = np.stack(np.meshgrid(g, g, g, indexing="ij"), axis=-1).reshape(-1, 3)
        sent = corr.send(cube.astype(np.float32))
        assert sent.min() >= 0.0
        assert sent.max() <= 255.0

    def test_a_colour_that_fits_is_left_alone(self) -> None:
        corr = calibrate.Correction(coef=calibrate.IDENTITY.copy())
        probe = np.array([[10.0, 120.0, 240.0]], np.float32)
        assert corr.send(probe) == pytest.approx(probe, abs=1e-3)

    def test_it_keeps_the_shape_it_was_given(self) -> None:
        corr = self._correction()
        block = np.full((4, 5, 3), 200.0, np.float32)
        assert corr.send(block).shape == block.shape


class TestRoundsArePutInTheSameInstrumentState:
    @staticmethod
    def _round(n: int, white: tuple[float, float, float]) -> Round:
        card = calibrate.chart()
        goal = card.target
        return Round(
            n=n,
            slot=calibrate.Slot(col=0, row=n - 1),
            sent=goal,
            scanned=(goal * (np.array(white, np.float32) / 255.0)).astype(np.float32),
            substrate=Substrate(white=white, black=HOLO_BLACK, patches=8),
        )

    def test_the_profile_pools_one_substrate_over_its_rounds(self) -> None:
        prof = Profile(
            name="p",
            rounds=[
                self._round(1, (144.0, 189.0, 208.0)),
                self._round(2, (140.0, 184.0, 207.0)),
                self._round(3, (135.0, 180.0, 200.0)),
            ],
        )
        assert prof.substrate.white == pytest.approx((140.0, 184.0, 207.0), abs=0.5)

    def test_a_drifting_lamp_isnormalised_away(self) -> None:
        """The four real rounds' bare-paper readings drift 9 levels (6%) and were
        pooled as though the lamp had not moved.

        Normalisation is *within* one profile — it puts that profile's rounds into a
        common state — so the comparison has to be between two rounds of the same
        profile. Comparing two separate profiles proves nothing, since each is already
        its own reference.
        """
        prof = Profile(
            name="p",
            rounds=[
                self._round(1, (144.0, 189.0, 208.0)),
                self._round(2, (135.0, 180.0, 200.0)),
            ],
        )
        raw = colour.delta_e00(
            colour.to_lab(prof.rounds[0].scanned), colour.to_lab(prof.rounds[1].scanned)
        ).mean()
        fixed = colour.delta_e00(
            colour.to_lab(prof.normalised(prof.rounds[0])),
            colour.to_lab(prof.normalised(prof.rounds[1])),
        ).mean()
        assert raw > 1.0, "the two rounds really were in different states"
        assert fixed < raw / 2.0, f"normalising left {fixed:.2f} of {raw:.2f}"

    def test_an_unmeasured_round_is_left_exactly_as_scanned(self) -> None:
        card = calibrate.chart()
        rnd = Round(
            n=1, slot=calibrate.Slot(0, 0), sent=card.target, scanned=card.target
        )
        prof = Profile(name="p", rounds=[rnd])
        assert prof.normalised(rnd) is rnd.scanned


class TestOneFunnelToPaper:
    def test_a_profile_renders_a_picture_through_aim_fit_and_compression(self) -> None:
        """One method, so the three cannot be applied in one order here and another
        there. `_apply_profile` in the CLI is the only caller."""
        card = calibrate.chart()
        goal = card.target
        prof = Profile(
            name="p",
            rounds=[
                Round(
                    n=1,
                    slot=calibrate.Slot(0, 0),
                    sent=goal,
                    scanned=goal * 0.8,
                    substrate=_substrate(),
                )
            ],
        )
        im = Image.new("RGB", (4, 4), (200, 200, 200))
        out = prof.render(im)
        assert out.size == im.size
        assert out.mode == "RGB"

    def test_an_uncalibrated_profile_falls_back_to_its_recipe(self) -> None:
        im = Image.new("RGB", (2, 2), (128, 128, 128))
        assert profiles.Profile(name="p").render(im).getpixel((0, 0)) == (128, 128, 128)


class TestTheReaderKnowsWhichChartItIsReading:
    """A reader that assumes one patch set is the plausible-wrong-answer failure again.

    `sample_patches` defaulted to the module-level verification chart, so reading a
    468-patch survey sampled a 9x9 grid over an 18x26 one and returned the **gutters** —
    which are bare paper at every position. A clean, self-consistent, entirely wrong
    reading, and the substrate would have come back as pure white on a blue sticker.
    """

    def test_a_survey_scan_read_as_a_survey_recovers_the_paper(self) -> None:
        card = calibrate.survey(SurveySize.HALF)
        art = calibrate.render_chart(None, "", (2400, 2700), None, card)
        # print it on the blue sticker, then read it back
        lin = colour.linearize(np.asarray(art.convert("RGB"), np.float32))
        black, white = (
            colour.linearize(np.array(HOLO_BLACK, np.float32)),
            colour.linearize(np.array(HOLO, np.float32)),
        )
        printed = colour.encode(black + (white - black) * lin)
        arr = printed.astype(np.float32)
        read = calibrate.sample_patches(arr, calibrate.locate(arr), card)
        assert read.shape == (len(card), 3)
        sub = Substrate.of(read, card)
        assert sub.measured
        assert sub.white == pytest.approx(HOLO, abs=2.0)
        assert sub.cast.visible, "the paper is blue and the reading has to say so"

    def test_reading_it_as_the_wrong_chart_does_not_pass_for_a_measurement(
        self,
    ) -> None:
        """The guard that saved this: a scan with fewer rows than the chart claims is
        no measurement, rather than a confident white."""
        card = calibrate.survey(SurveySize.HALF)
        too_few = np.full((len(calibrate.verification()), 3), 255.0, np.float32)
        assert not Substrate.of(too_few, card).measured
