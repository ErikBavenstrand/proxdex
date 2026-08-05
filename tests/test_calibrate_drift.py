"""That a calibration says when it is going the *wrong way*, not just how far out it is.

This file exists because of a defect the whole suite could not see. The real
``holo-plain`` profile — a blue holographic sticker — asked its printer for more yellow
ink on every one of four rounds while the single figure proxdex reported *fell*, so
`profile show` read as progress, `plateau` stood ready to certify it as converged, and
the cards came off the printer yellow. Nothing lied; there was simply no number that
could have objected.

Two things are pinned here, and the second one is the reason for the first:

* a residual is **three numbers** — ΔE00, the cast off the neutrals, and how many
  patches were unreachable — because one number cannot express "landing closer while
  working harder";
* :attr:`Profile.drift` watches what is **sent**, not what comes back. The first version
  of this detector watched the scans, which on the real profile *improve* every round
  (the fit really does drag the print toward neutral) — so it would have passed the
  very profile it was written to catch. That near-miss is the test below.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from proxdex import calibrate, profiles
from proxdex.calibrate import Slot
from proxdex.profiles import Profile, Round

#: the real substrate, as the scanner read it bare: blue is +64 above red
HOLO = np.array([144.0, 189.0, 208.0], np.float32)


def _round(n: int, sent: np.ndarray, scanned: np.ndarray) -> Round:
    return Round(
        n=n,
        slot=Slot(col=(n - 1) % 2, row=(n - 1) // 2),
        sent=sent.astype(np.float32),
        scanned=scanned.astype(np.float32),
    )


def _tinted(strength: float) -> np.ndarray:
    """A scan off a substrate that shows through in proportion to how light the ink is.

    Which is what the real sticker does — measured, its blue-minus-red is +57.75 in the
    highlights against +5.50 in the shadows, because ink covers the paper where the card
    is dark and the paper shows through where it is light.
    """
    goal = calibrate.target()
    coverage = 1.0 - goal / 255.0
    return (
        goal * (1.0 - strength * (1.0 - coverage)) + HOLO * strength * (1.0 - coverage)
    ).astype(np.float32)


class TestAResidualIsThreeNumbers:
    def test_it_reports_de00_the_cast_and_what_it_could_not_reach(self) -> None:
        goal = calibrate.target()
        prof = Profile(name="p", rounds=[_round(1, goal, _tinted(0.6))])
        err = prof.residual
        assert err is not None
        assert err.de00_mean > 0.0
        assert err.cast.patches > 0
        assert err.total == len(goal)

    def test_the_cast_covers_the_neutrals_this_medium_can_reach(self) -> None:
        """And **only** those, which is a correction rather than a detail.

        The neutral ramp is spaced in L\\* from 2 to 98 on purpose, so on any real stock
        its ends are outside the printable range: those patches come back at the ink
        floor or at the paper, carrying whatever hue *those* have. Averaging them in
        reports a cast the print does not have anywhere anyone can see it — measured on
        the simulated blue sticker, every neutral read a\\* +5.10 (red) where the
        printable ones read a\\* +0.86 (neutral), so the number on screen contradicted
        the sheet in hand. The error was already masked this way; the cast was not.
        """
        goal = calibrate.target()
        greys = calibrate.chart().neutrals
        prof = Profile(name="p", rounds=[_round(1, goal, _tinted(0.6))])
        err = prof.residual
        assert err is not None
        reach = prof.gamut.holds(goal)
        assert err.cast.patches == int(reach[greys].sum())
        assert err.cast.patches <= len(greys)

    def test_a_perfect_print_is_zero_and_neutral(self) -> None:
        goal = calibrate.target()
        prof = Profile(name="p", rounds=[_round(1, goal, goal)])
        err = prof.residual
        assert err is not None
        assert err.de00_mean == pytest.approx(0.0, abs=1e-3)
        assert not err.cast.visible

    def test_a_blue_substrate_shows_as_a_blue_cast(self) -> None:
        prof = Profile(name="p", rounds=[_round(1, calibrate.target(), _tinted(0.8))])
        err = prof.residual
        assert err is not None
        assert err.cast.visible
        assert err.cast.b < 0.0  # blue, which is what the sticker is


class TestTheGamutIsMeasuredFromScans:
    def test_it_does_not_depend_on_the_fit(self) -> None:
        """The bug: `reachable` used to invert the correction, so the mask came from
        the same fit it was then used to score. Refitting over 20 patches instead of 80
        moved the reported gamut from 37 to 49 on identical scan data, which made no two
        residuals comparable. Now it is a property of the measurements alone.
        """
        scanned = _tinted(0.6)
        whole = calibrate.reachable(scanned)
        # the same scans, fitted very differently — the mask may not move
        assert calibrate.reachable(scanned).tolist() == whole.tolist()

    def test_a_narrow_medium_reaches_less_than_a_wide_one(self) -> None:
        wide = calibrate.reachable(calibrate.target())
        narrow = calibrate.reachable(_tinted(0.9))
        assert int(narrow.sum()) < int(wide.sum())

    def test_nothing_measured_rules_nothing_out(self) -> None:
        empty = calibrate.reachable(np.zeros((0, 3), np.float32))
        assert bool(empty.all())


class TestDriftWatchesWhatIsSentNotWhatComesBack:
    """The near-miss, kept as a test.

    A loop fighting a tinted substrate drives its send further off neutral every round
    *while* the scan lands closer to neutral. A detector on either quantity alone reads
    the opposite story, so which one it watches is the whole design.
    """

    @staticmethod
    def diverging() -> Profile:
        """Rounds whose *sends* go steadily yellow and whose *scans* improve."""
        goal = calibrate.target()
        rounds: list[Round] = []
        for n, (demand, tint) in enumerate(
            [(0.0, 0.60), (0.30, 0.34), (0.34, 0.30), (0.36, 0.29)], start=1
        ):
            warm = np.array([1.0 + demand, 1.0, 1.0 - demand], np.float32)
            rounds.append(_round(n, (goal * warm).clip(0, 255), _tinted(tint)))
        return Profile(name="holo", rounds=rounds)

    def test_the_scans_get_better_which_is_why_watching_them_fails(self) -> None:
        prof = self.diverging()
        casts = [c.chroma for c in prof.casts]
        assert casts[-1] < casts[0], "the print really is landing nearer neutral"

    def test_the_demand_gets_worse_which_is_the_real_signal(self) -> None:
        prof = self.diverging()
        demands = [c.chroma for c in prof.demands]
        assert demands == sorted(demands), "each round asks for more correction"
        assert demands[-1] > demands[0]

    def test_drift_fires_and_names_the_direction(self) -> None:
        drift = self.diverging().drift
        assert drift is not None
        assert drift.grew > 0.0
        assert drift.cast.hue.startswith(("yellow", "red")), drift.cast.text
        assert "cannot give" in drift.text
        assert "docs/calibration.md" in drift.hint

    def test_plateau_refuses_to_certify_a_drifting_profile(self) -> None:
        """Flat *and* drifting is not converged, it is stuck pulling the wrong way —
        and certifying it is exactly what would have told you `holo-plain` was done."""
        prof = self.diverging()
        assert prof.drift is not None
        assert prof.plateau is None

    def test_a_settled_profile_does_not_drift(self) -> None:
        """The other half: a correction that stops moving must not be accused."""
        goal = calibrate.target()
        rounds = [_round(n, goal, _tinted(0.05)) for n in range(1, 5)]
        prof = Profile(name="matte", rounds=rounds)
        assert prof.drift is None

    def test_one_bad_sheet_is_not_a_trend(self) -> None:
        goal = calibrate.target()
        warm = np.array([1.4, 1.0, 0.6], np.float32)
        prof = Profile(
            name="p",
            rounds=[
                _round(1, goal, _tinted(0.1)),
                _round(2, (goal * warm).clip(0, 255), _tinted(0.1)),
            ],
        )
        assert prof.drift is None, "two rounds cannot be a direction of travel"

    def test_a_switched_off_round_is_out_of_it(self) -> None:
        prof = self.diverging()
        assert prof.drift is not None
        prof.rounds = [r.switched(on=r.n == 1) for r in prof.rounds]
        assert prof.drift is None


class TestItIsReported:
    def test_the_summary_carries_the_drift(self) -> None:
        prof = TestDriftWatchesWhatIsSentNotWhatComesBack.diverging()
        out = prof.summary()
        assert out["drift"] is not None
        assert "text" in out["drift"]
        assert out["residual"]["cast"]["visible"] in {True, False}

    def test_a_clean_profile_says_so(self) -> None:
        prof = Profile(
            name="p", rounds=[_round(1, calibrate.target(), calibrate.target())]
        )
        assert prof.summary()["drift"] is None

    def test_it_survives_being_written_and_read_back(self, tmp_path: Path) -> None:
        prof = TestDriftWatchesWhatIsSentNotWhatComesBack.diverging()
        profiles.save(tmp_path, prof)
        back = profiles.read(tmp_path, "holo")
        assert back is not None
        assert back.drift is not None
        mine = prof.drift
        assert mine is not None
        assert back.drift.grew == pytest.approx(mine.grew, abs=1e-6)
