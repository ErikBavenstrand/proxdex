"""The press model, measured — and the numbers that can fail.

Every other file in this suite pins something a person cannot re-check by eye. This one
pins something nobody could re-check *at all*: whether splitting a colour correction
into ink limit → linearization → grey balance → colour transform actually helps. The
evidence that started the rebuild was four stored rounds of one profile, which is one
sample of one medium and cannot answer that. So the answer comes from
``tests/press_sim.py`` — a Murray-Davies press and a scanner that is wrong in the way
the literature says a flatbed is wrong — and every threshold here is a number that was
measured rather than chosen.

Four things earn their place:

* **the split beats one polynomial**, on both presses and both scanners, and each stage
  measurably reduces the residual the next one sees. Without this the stage order is a
  claim about the code;
* **a colour the model cannot make is compressed, not sent** — including the case where
  the send comes out perfectly in range and the model is extrapolating past the region
  it was fitted over. That one was found by driving the loop end to end: a wanted dark
  orange went out as (21, 66, 95), heavy ink for a light colour, and came back blue;
* **a verification round can fail**, which is the whole of stage 7 — it is the first
  number in this system that is not the fit judged against its own training data;
* **the two rejected hypotheses of §1.4 stay rejected**, so nobody rebuilds them: the
  fitted response is monotone, and scrambling the patch layout buys nothing.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from press_sim import (
    BIASED,
    HOLO,
    HONEST,
    MATTE,
    NOISY,
    Rig,
    SimPress,
    SimScanner,
)
from proxdex import calibrate, colour, profiles
from proxdex.calibrate import Intent, Role, SurveySize
from proxdex.press import Curve, Stage
from proxdex.profiles import Profile, Purpose, Round

#: every press and scanner the harness has, as the table each measurement is taken over.
#: A stage that helped on one medium and hurt the other would be indistinguishable from
#: a stage that helped, over a single case.
RIGS = [
    ("matte/honest", MATTE, HONEST),
    ("matte/biased", MATTE, BIASED),
    ("holo/honest", HOLO, HONEST),
    ("holo/biased", HOLO, BIASED),
]


def _measured(
    sim: SimPress, scanner: SimScanner, size: SurveySize = SurveySize.HALF
) -> Profile:
    """A profile with one survey round on it, printed and scanned by the harness."""
    spec = calibrate.survey(size)
    scan = Rig(sim, scanner).chart(spec)
    return Profile(
        name=sim.name,
        rounds=[
            Round(
                n=1,
                slot=calibrate.Slot(0, 0),
                sent=spec.target,
                scanned=scan,
                chart=calibrate.ChartId.of_survey(size),
                substrate=calibrate.Substrate.of(scan, spec),
            )
        ],
    )


def _checked(prof: Profile, sim: SimPress, scanner: SimScanner) -> Profile:
    """Print this profile's verification chart through its model, and record it.

    What `calibrate verify` then `calibrate add` do, in two lines: the model's own
    predictions onto paper, scanned back, scored, and kept out of the fit.
    """
    model = prof.model
    assert model is not None
    spec = calibrate.ChartId.VERIFY.spec
    asked = calibrate.adaptive(spec, prof.gamut, prof.seen)
    sent = model.send(prof.aim(asked))
    prof.add_round(
        Rig(sim, scanner).run(sent),
        sent,
        calibrate.Slot(0, 1),
        chart=calibrate.ChartId.VERIFY,
        purpose=Purpose.VERIFY,
        wanted=asked,
    )
    return prof


def _greys(count: int = 10) -> calibrate.Patches:
    """A neutral ramp of card values, well inside any medium's range."""
    step = np.linspace(70.0, 210.0, count)
    return np.stack([step, step, step], axis=-1).astype(np.float32)


class TestTheHarnessIsTheMediumItClaimsToBe:
    """If the simulator is not the defect, nothing measured on it means anything."""

    def test_the_blue_sticker_reads_as_the_real_one_did(self) -> None:
        spec = calibrate.survey(SurveySize.QUARTER)
        scan = Rig(HOLO, HONEST).chart(spec)
        sub = calibrate.Substrate.of(scan, spec)
        assert sub.white == pytest.approx((144.0, 189.0, 208.0), abs=1.0)
        assert sub.cast.visible

    def test_the_paper_shows_through_in_proportion_to_how_little_ink_covers_it(
        self,
    ) -> None:
        """The measured mechanism of the whole defect: the real sticker reads +57.75
        blue-minus-red in the highlights against +5.50 in the shadows, and a global
        colour transform cannot represent a gradient like that."""
        spec = calibrate.survey(SurveySize.QUARTER)
        scan = Rig(HOLO, HONEST).chart(spec)
        greys = spec.neutrals
        order = np.argsort(spec.target[greys][:, 0])
        tilt = scan[greys][:, 2] - scan[greys][:, 0]
        light, dark = tilt[order][-4:].mean(), tilt[order][:4].mean()
        assert light > 40.0, "the highlights have to show the paper"
        assert dark < 10.0, "and the shadows have to be covered by ink"

    def test_it_gives_the_same_answer_twice(self) -> None:
        """A simulator whose answer moves between runs cannot be what a threshold is set
        against — which is why the noise is seeded rather than free."""
        spec = calibrate.survey(SurveySize.QUARTER)
        first = Rig(HOLO, NOISY).chart(spec)
        assert first == pytest.approx(Rig(HOLO, NOISY).chart(spec))


class TestTheSplitEarnsItsKeep:
    @pytest.mark.parametrize(("label", "sim", "scanner"), RIGS)
    def test_each_stage_reduces_what_the_next_one_sees(
        self, label: str, sim: SimPress, scanner: SimScanner
    ) -> None:
        """The acceptance test for the *order*. A stage downstream cannot repair a stage
        upstream, so if any of them left the residual where it found it, the sequence
        would be decoration."""
        model = _measured(sim, scanner).model
        assert model is not None
        rows = {r.stage: r for r in model.residuals}
        assert rows[Stage.LINEARIZE].gained > 5.0, label
        assert rows[Stage.GREY].gained > 0.0, label
        assert rows[Stage.COLOUR].gained > 0.0, label

    @pytest.mark.parametrize(("label", "sim", "scanner"), RIGS)
    def test_it_beats_the_single_polynomial_it_replaced(
        self, label: str, sim: SimPress, scanner: SimScanner
    ) -> None:
        """Measured, on the same scans, printing the same neutral ramp: staged 1.34
        against 2.22 on matte/honest, 3.87 vs 4.99, 3.64 vs 4.98, and 6.42 vs 10.05 on
        holo/biased. A split that did not beat one polynomial would not be worth the
        four classes it costs."""
        prof = _measured(sim, scanner)
        model = prof.model
        assert model is not None
        rig = Rig(sim, scanner)
        want = _greys()
        aim = prof.aim(want)
        white = rig.truth(np.array([[255.0, 255.0, 255.0]], np.float32))
        staged = colour.de00_rgb(rig.truth(model.send(aim)), want, white).mean()
        alone = colour.de00_rgb(rig.truth(model.poly.send(aim)), want, white).mean()
        assert staged < alone, f"{label}: staged {staged:.2f} vs poly {alone:.2f}"

    def test_the_blue_sticker_prints_a_neutral_grey(self) -> None:
        """Stage 4 + stage 5's acceptance, and the point of the whole exercise: on the
        medium whose profile was driving 32 levels toward yellow, a grey comes out grey.

        Judged relative to the paper, which is what the eye does with a sheet in its
        hand — and on the honest scanner, because what a *biased* one leaves behind is
        the reference of §1.1 and no amount of fitting removes it.
        """
        prof = _measured(HOLO, HONEST, SurveySize.FULL)
        model = prof.model
        assert model is not None
        rig = Rig(HOLO, HONEST)
        want = _greys()
        got = rig.truth(model.send(prof.aim(want)))
        white = rig.truth(np.array([[255.0, 255.0, 255.0]], np.float32))
        cast = colour.Cast.of(colour.to_lab(colour.relative_to(got, white)))
        assert not cast.visible, cast.text

    def test_a_profile_with_no_uncorrected_round_says_so_instead_of_guessing(
        self,
    ) -> None:
        """A ramp printed *through* a correction is no longer a sweep of one channel, so
        the patch a chart labels ``ramp-r`` did not measure red's own response. Such a
        profile gets identity curves and is told, rather than a linearization inferred
        from the wrong patches."""
        spec = calibrate.survey(SurveySize.QUARTER)
        rig = Rig(MATTE, HONEST)
        bent = (spec.target * 0.7 + 20.0).astype(np.float32)
        prof = Profile(
            name="indirect",
            rounds=[
                Round(
                    n=1,
                    slot=calibrate.Slot(0, 0),
                    sent=bent,
                    scanned=rig.chart(spec, bent),
                    chart=calibrate.ChartId.SURVEY_QUARTER,
                )
            ],
        )
        model = prof.model
        assert model is not None
        assert not model.staged
        assert model.direct == 0
        rows = {r.stage: r for r in model.residuals}
        assert not rows[Stage.LINEARIZE].measured
        assert "not measured" in rows[Stage.LINEARIZE].text


class TestEveryStageInvertsItself:
    """``forward`` and ``inverse`` are not two fits of one thing.

    Each stage's inverse is the arithmetic inverse of its forward — bisection on a
    monotone curve, the mirrored solve on the grey axis — so a round-trip error can only
    come from the one stage that has no closed-form inverse, and that one *reports* it.
    """

    def test_a_monotone_curve_inverts_exactly(self) -> None:
        xs = np.linspace(0.0, 255.0, 9)
        ys = (xs / 255.0) ** 1.8
        curve = Curve.through(xs, ys)
        probe = np.linspace(5.0, 250.0, 40)
        assert curve.back(curve.at(probe)) == pytest.approx(probe, abs=0.01)

    def test_a_curve_cannot_reverse_a_gradient_even_on_noisy_samples(self) -> None:
        """A linearization that reversed would band a smooth ramp. Monotonicity is
        enforced rather than hoped for, because read noise really does put a reversal in
        a real ramp."""
        xs = np.linspace(0.0, 255.0, 17)
        ys = np.linspace(0.0, 1.0, 17)
        ys[7], ys[8] = ys[8], ys[7]  # one pair out of order, as noise leaves it
        curve = Curve.through(xs, ys)
        walked = curve.at(np.linspace(0.0, 255.0, 200))
        assert (np.diff(walked) >= -1e-9).all(), "the curve reversed"

    def test_the_model_round_trips_a_colour_it_can_make(self) -> None:
        prof = _measured(MATTE, HONEST)
        model = prof.model
        assert model is not None
        want = prof.aim(_greys(6))
        assert model.fits(want).all()
        back = model.forward(model.send(want))
        assert colour.de00_rgb(back, want).max() < 3.0

    def test_the_one_stage_without_a_closed_form_inverse_reports_its_error(
        self,
    ) -> None:
        model = _measured(MATTE, HONEST).model
        assert model is not None
        assert model.transform.measured
        assert model.transform.round_trip < 0.05


class TestAColourThisPaperCannotMake:
    """Compression, and the two ways a colour is out of reach.

    The send overflowing is only one of them. The other was found by driving the loop
    end to end on the blue sticker and is the more dangerous, because nothing looks
    wrong: a degree-2 transform extrapolating past the region it was fitted over returns
    a send that is perfectly in range for a colour the paper cannot make.
    """

    def test_a_colour_outside_the_measured_reach_is_refused_even_with_a_valid_send(
        self,
    ) -> None:
        prof = _measured(HOLO, HONEST)
        model = prof.model
        assert model is not None
        # lighter than this paper can go: the send for it comes out inside 0..255, so
        # the send-range test alone calls it printable
        want = np.array([[250.0, 250.0, 250.0]], np.float32)
        assert calibrate.in_range(model.raw)(want).all(), "the premise: the send fits"
        assert not model.fits(want).any(), "and yet the paper cannot make it"

    def test_it_gives_up_lightness_toward_whichever_end_is_nearer(self) -> None:
        """The direction used to be inferred from which end the *send* overflowed, which
        says nothing about a colour whose send is in range. On a medium whose white is
        L* 74 that guessed "toward 100" every time and pushed such colours further out:
        measured, a wanted dark orange went out as (21, 66, 95) — heavy ink for a light
        colour — where the least move puts it at (223, 154, 56)."""
        prof = _measured(HOLO, HONEST)
        model = prof.model
        assert model is not None
        # a dark saturated orange, well outside a blue sticker's reach
        want = np.array([[200.0, 125.0, 50.0]], np.float32)
        sent = model.send(prof.aim(want))[0]
        assert sent[0] > sent[2], f"a warm colour must not go out blue: {sent}"

    def test_nothing_is_ever_sent_out_of_range(self) -> None:
        prof = _measured(HOLO, HONEST)
        model = prof.model
        assert model is not None
        grid = np.linspace(0.0, 255.0, 7, dtype=np.float32)
        cube = np.stack(np.meshgrid(grid, grid, grid, indexing="ij"), axis=-1).reshape(
            -1, 3
        )
        sent = model.send(prof.aim(cube.astype(np.float32)))
        assert sent.min() >= 0.0
        assert sent.max() <= 255.0

    def test_the_ink_limit_never_asks_for_ink_that_does_nothing(self) -> None:
        model = _measured(HOLO, HONEST).model
        assert model is not None
        assert model.limits.measured
        black = model.limits.clamp(np.zeros((1, 3), np.float32))[0]
        assert (black >= np.array(model.limits.floor) - 1e-6).all()


class TestVerificationIsTheNumberThatCanFail:
    """Stage 7, and the acceptance test the plan asks for: a model whose own residual
    looks healthy has to be caught by a check that it is not."""

    def test_a_check_is_scored_and_kept_out_of_the_fit(self) -> None:
        prof = _checked(_measured(MATTE, HONEST), MATTE, HONEST)
        assert len(prof.rounds) == 2
        assert [r.n for r in prof.live] == [1], "a check may not train the model"
        assert [r.n for r in prof.checks] == [2]
        assert prof.verified is not None

    def test_it_catches_a_model_that_agrees_with_its_own_data(self) -> None:
        """The §1.2 detector, in the form the plan specifies.

        A model fitted on one press is perfectly consistent with the measurements it was
        fitted on — that is what fitting means — and asking it to print on a different
        press is the only thing that can say it is wrong. Measured: the same model
        verifies at **1.6** on the press it was measured on and **18.4** on another.
        """
        own = _checked(_measured(MATTE, HONEST), MATTE, HONEST)
        honest = own.verified
        assert honest is not None
        assert honest.de00_mean < 5.0, (
            f"its own press should verify well: {honest.text}"
        )

        other = _checked(_measured(MATTE, HONEST), HOLO, HONEST)
        wrong = other.verified
        assert wrong is not None
        assert wrong.de00_mean > honest.de00_mean * 3.0, (
            f"verification let a wrong model through: {wrong.de00_mean:.2f} against "
            f"{honest.de00_mean:.2f} on the press it was measured on"
        )

    def test_plateau_is_judged_on_the_checks_once_there_are_any(self) -> None:
        """A run of flat *refinement* rounds means more of the same evidence stopped
        moving the fit, which is not what converged should mean. Once checks exist they
        are what the word is judged on, and `Plateau` says which it was."""
        prof = _measured(MATTE, HONEST)
        for n in range(4):
            _checked(prof, MATTE, HONEST)
            # each check on its own square of paper, as it would really be printed
            prof.rounds[-1] = replace(
                prof.rounds[-1], slot=calibrate.Slot(n % 2, n // 2)
            )
        flat = prof.plateau
        assert flat is not None
        assert flat.on_checks, "with checks present it may not be judged on the fit"
        assert "predictions" in flat.text


class TestTheRejectedHypothesesStayRejected:
    """§1.4 — measured, found not to apply, and pinned so nobody builds them.

    Both cost real work to try, and both would look plausible to the next person reading
    this area. The measurements are the argument.
    """

    def test_the_fitted_response_is_monotone(self) -> None:
        """Non-monotonicity was suspected of banding a ramp. It does not: 0 reversing
        steps on the neutral, red and blue axes, on both presses."""
        for _, sim, scanner in RIGS:
            prof = _measured(sim, scanner)
            model = prof.model
            assert model is not None
            ramp = np.linspace(60.0, 200.0, 24)
            for axis in range(3):
                probe = np.full((len(ramp), 3), 120.0, np.float32)
                probe[:, axis] = ramp
                sent = model.send(prof.aim(probe))[:, axis]
                steps = np.diff(sent)
                reversals = int((steps < -1.0).sum())
                assert reversals == 0, f"{sim.name}/{scanner.name} axis {axis}"

    def test_scrambling_the_patch_layout_buys_nothing(self) -> None:
        """What ArgyllCMS's `printtarg` does, to decorrelate flare from the ramp.

        It does not apply here because every patch is already surrounded by a white
        gutter, which makes flare near-uniform — and that uniformity is precisely *why*
        substrate-relative aiming works.
        """
        spec = calibrate.survey(SurveySize.QUARTER)
        rig = Rig(HOLO, BIASED)
        order = np.arange(len(spec))
        shuffled = np.concatenate([order[1::2], order[0::2]])
        plain = rig.chart(spec)
        mixed = rig.run(spec.target[shuffled])[np.argsort(shuffled)]
        # the same patches, read in a different order, come back the same: the flare
        # each picks up does not depend on its neighbours
        assert colour.de00_rgb(plain, mixed).mean() < 0.5


class TestTheAdaptivePlacementKeepsTheChartComparable:
    def test_only_the_lattice_moves(self) -> None:
        """The greys, the ramps and the substrate patches stay put, so two verification
        rounds remain comparable on everything a trend is read from — a cast has to be
        confirmed at the same lightnesses every time."""
        prof = _measured(HOLO, HONEST)
        spec = calibrate.ChartId.VERIFY.spec
        moved = calibrate.adaptive(spec, prof.gamut, prof.seen)
        for role in (
            Role.NEUTRAL,
            Role.RAMP_R,
            Role.RAMP_G,
            Role.RAMP_B,
            Role.MAX_INK,
            Role.SUBSTRATE,
        ):
            idx = spec.of_role(role)
            assert moved[idx] == pytest.approx(spec.target[idx]), role
        lattice = spec.of_role(Role.LATTICE)
        assert not np.allclose(moved[lattice], spec.target[lattice])

    def test_it_places_inside_what_the_medium_can_reach(self) -> None:
        """The point: a fixed lattice spends its patches on colours the paper cannot
        make — measured, 43 of 80 round-2 sends clipped on foil, and `usable` then drops
        them."""
        prof = _measured(HOLO, HONEST)
        spec = calibrate.ChartId.VERIFY.spec
        lattice = spec.of_role(Role.LATTICE)
        moved = calibrate.adaptive(spec, prof.gamut, prof.seen)
        before = int(prof.gamut.holds(spec.target[lattice]).sum())
        after = int(prof.gamut.holds(moved[lattice]).sum())
        assert after > before, f"{after} reachable against {before}"

    def test_it_is_deterministic(self) -> None:
        """Two renders of one profile must place the same patches, or two verification
        errors are not comparable."""
        prof = _measured(HOLO, HONEST)
        spec = calibrate.ChartId.VERIFY.spec
        first = calibrate.adaptive(spec, prof.gamut, prof.seen)
        assert first == pytest.approx(calibrate.adaptive(spec, prof.gamut, prof.seen))

    def test_nothing_measured_leaves_the_chart_alone(self) -> None:
        spec = calibrate.ChartId.VERIFY.spec
        empty = np.zeros((0, 3), np.float32)
        assert calibrate.adaptive(spec, calibrate.Gamut(), empty) == pytest.approx(
            spec.target
        )


class TestTheReferenceIsNeverSilent:
    """Stage 8. It is the one assumption behind every other number here, and it did its
    damage by going unstated, so the *reporting* is pinned and not just the fit."""

    def test_a_fresh_profile_assumes_the_scanner_and_says_what_that_costs(self) -> None:
        ref = calibrate.Reference()
        assert ref.assumed
        assert ref.floor == calibrate.ASSUMED_FLOOR
        assert "assumed" in ref.text
        assert "scans as the target" in ref.warning

    def test_it_leaves_a_reading_alone_while_assumed(self) -> None:
        probe = np.array([[10.0, 120.0, 240.0]], np.float32)
        assert calibrate.Reference().apply(probe) == pytest.approx(probe)

    def test_reading_a_target_recovers_a_biased_scanner(self) -> None:
        """The measured claim: a matrix off a published target takes an uncharacterized
        flatbed from about ΔE00 12 to about 5. Here the target is printed by nothing —
        its patches *are* the reference — so what the scanner does to them is exactly
        what the matrix has to undo."""
        kind = calibrate.ReferenceTarget.COLORCHECKER
        truth = colour.from_lab(kind.lab)
        readings = BIASED.scan(colour.linearize(truth))
        before = colour.delta_e00(colour.to_lab(readings), kind.lab).mean()
        ref = calibrate.Reference.from_target(readings, kind.lab, target=kind.value)
        assert not ref.assumed
        assert ref.patches == kind.patches
        after = colour.delta_e00(colour.to_lab(ref.apply(readings)), kind.lab).mean()
        # measured: 2.96 → 1.58. Not the order the literature quotes for a real flatbed
        # (12.3 → 4.9) because the harness's scanner is only as wrong as it was told to
        # be; what is pinned is the direction and that the matrix is doing real work
        assert after < before * 0.7, f"{before:.2f} → {after:.2f}"
        assert not ref.warning, "a measured reference has nothing to warn about"

    def test_too_few_readings_is_no_reference_rather_than_a_bad_one(self) -> None:
        kind = calibrate.ReferenceTarget.COLORCHECKER
        assert calibrate.Reference.from_target(
            np.zeros((2, 3), np.float32), kind.lab[:2]
        ).assumed

    def test_the_published_values_are_adapted_from_d50(self) -> None:
        """They are graphic-arts numbers, so D50/2°, and everything here is D65.

        And it is the **blues** the shift lands on, which is exactly what the plan says:
        the ColorChecker's blue patch moves 4.82 ΔE00 through the adaptation while its
        white barely moves at all (0.16). Ignoring it would put several ΔE00 on the one
        region a blue substrate is already the whole discussion about.
        """
        kind = calibrate.ReferenceTarget.COLORCHECKER
        # patch 13 (blue) and patch 19 (white), as X-Rite publishes them under D50
        blue_d50 = np.array([[28.78, 14.18, -50.30]], np.float64)
        white_d50 = np.array([[96.54, -0.43, 1.19]], np.float64)
        assert colour.delta_e00(kind.lab[12:13], blue_d50).max() > 2.0
        assert colour.delta_e00(kind.lab[18:19], white_d50).max() < 1.0
        # and a neutral stays neutral through it, which is what says it is an adaptation
        # and not a tint
        assert abs(kind.lab[18][1]) < 2.0
        assert abs(kind.lab[18][2]) < 2.0

    def test_reading_one_changes_the_answer_and_not_just_the_label(self) -> None:
        """The gap this was found in: nothing applied it.

        It was stored and reported while `calibrate reference` claimed "every round of
        this profile is now read through it", and a label describing work nobody does is
        worse than no label.

        It is applied on **read** (`Profile.observed`) rather than baked into a stored
        round, so characterizing the scanner months later re-reads every round already
        on disk instead of asking for them to be printed again.
        """
        prof = _measured(HOLO, BIASED)
        before = prof.substrate.white
        kind = calibrate.ReferenceTarget.COLORCHECKER
        truth = colour.from_lab(kind.lab)
        prof.reference = calibrate.Reference.from_target(
            BIASED.scan(colour.linearize(truth)), kind.lab, target=kind.value
        )
        assert not prof.reference.assumed
        after = prof.substrate.white
        assert after != pytest.approx(before), "the paper must be re-read through it"
        # and toward the truth: the biased scanner is what put the paper off in the
        # first place, so undoing it must land nearer what the press really printed
        real = Rig(HOLO, HONEST).chart(calibrate.survey(SurveySize.HALF))
        honest = calibrate.Substrate.of(real, calibrate.survey(SurveySize.HALF)).white
        was = colour.de00_rgb(
            np.array([before], np.float32), np.array([honest], np.float32)
        ).mean()
        now = colour.de00_rgb(
            np.array([after], np.float32), np.array([honest], np.float32)
        ).mean()
        assert now < was, f"{was:.2f} → {now:.2f}"

    def test_it_survives_a_round_trip_and_a_rubbish_file(self) -> None:
        kind = calibrate.ReferenceTarget.COLORCHECKER
        truth = colour.from_lab(kind.lab)
        ref = calibrate.Reference.from_target(
            BIASED.scan(colour.linearize(truth)), kind.lab
        )
        back = calibrate.Reference.read(ref.json())
        assert back.matrix == pytest.approx(ref.matrix)
        for junk in (None, [], "no", {"matrix": "x"}, {"matrix": [[1, 2]]}):
            assert calibrate.Reference.read(junk).assumed


class TestARoundKnowsWhichChartItPrinted:
    """The blocker this rebuild opened with: a survey round was written and then read
    back as *unreadable*, because every patch array was checked against one global chart
    length. A verb that writes a round the loader discards is worse than no verb."""

    @pytest.mark.parametrize("size", list(SurveySize))
    def test_a_survey_round_survives_being_written_and_read_back(
        self, size: SurveySize, tmp_path: Path
    ) -> None:
        root = tmp_path
        prof = _measured(MATTE, HONEST, size)
        prof.name = "m"
        profiles.save(root, prof)
        back = profiles.read(root, "m")
        assert back is not None
        assert back.unreadable == 0, "the round it just wrote came back unreadable"
        assert len(back.rounds) == 1
        assert back.rounds[0].chart is calibrate.ChartId.of_survey(size)
        assert len(back.rounds[0].scanned) == len(calibrate.survey(size))

    def test_a_round_naming_no_chart_is_counted_rather_than_coerced(self) -> None:
        """A round written before charts had ids is checked against the verification
        chart, fails, and is *counted* — which is the documented behaviour, not a
        migration. Its numbers were chosen by the method being replaced."""
        stored = {
            "n": 1,
            "slot": [0, 0],
            "sent": [[1.0, 2.0, 3.0]] * 80,  # the old 80-patch chart
            "scanned": [[1.0, 2.0, 3.0]] * 80,
        }
        assert Round.read(stored, 1) is None

    def test_two_charts_can_be_fitted_together(self) -> None:
        """The ordinary case of the new loop: a survey, then the small chart.

        Their arrays are different lengths, and each round carries the patch set that
        says which of its rows are the ramps.
        """
        prof = _measured(MATTE, HONEST)
        spec = calibrate.ChartId.VERIFY.spec
        rig = Rig(MATTE, HONEST)
        model = prof.model
        assert model is not None
        sent = model.send(prof.aim(spec.target))
        prof.add_round(rig.run(sent), sent, calibrate.Slot(1, 0))
        assert len(prof.live) == 2
        assert prof.model is not None
        assert prof.model.staged, "the survey's ramps must still be in the fit"


class TestScoringStaysAboutThePrint:
    def test_a_print_is_judged_against_the_paper_it_is_on(self) -> None:
        """An absolute aim reports a cast no ink can remove.

        And reports it identically however good the calibration gets, which is why a
        residual has to be judged against the paper the print is on. Measured on a check
        printed through the model on the blue sticker: relative it reads ΔE00 3.37
        with a cast of a* +0.07 b* +2.06, absolute **15.94** with a* -5.15 b* -5.00 —
        two verdicts on one sheet, and the second one is about the paper.

        Judged on a **corrected** print deliberately. On the raw survey the relative
        reading is worse, and that is not a counter-example but the same physics: a flat
        division by the paper's white cannot undo show-through that is
        coverage-dependent (+57.75 blue-minus-red in the highlights against +5.50 in
        the shadows). Aiming
        relative is what removes it; scoring relative only judges a print that did.
        """
        prof = _checked(_measured(HOLO, HONEST), HOLO, HONEST)
        check = prof.rounds[-1]
        relative = prof.score(check)
        prof.intent = Intent(0.0)
        absolute = prof.score(check)
        assert relative.de00_mean < absolute.de00_mean
        assert relative.cast.chroma < absolute.cast.chroma
