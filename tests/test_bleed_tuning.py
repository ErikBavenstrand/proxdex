"""Tuning how a widened border is filled — the knobs, and what they cannot do.

Three reasons this earns a file, none of them about the numbers themselves:

1. **cardbleed validates nothing.** Measured: it accepts ``jittter=0.1`` (a typo),
   ``mode="nonsense"`` and ``jitter="lots"`` without a murmur and carries on with its
   defaults. So a knob proxdex spells wrong, or a value it fails to check, becomes a
   control that silently does nothing — you would turn it, see no change, and conclude
   the picture was as good as it gets. Every rejection below is a rejection cardbleed
   would not have made.
2. **The declaration must not drift from cardbleed.** Defaults are *read* from
   :class:`cardbleed.Params` rather than restated, and the test holds the knob list
   against that class: a renamed or dropped field fails here instead of becoming an
   override that is quietly ignored.
3. **A fill setting only matters where border is actually invented**, and that is easy
   to get wrong — it was got wrong while building this. With the marks on the spec,
   ``solve_fit`` reports extensions of ~1e-13px and every ``mode`` produces a
   byte-identical file; move them inside the spec and the same modes differ.
   :func:`bleed.extends` is what lets a caller say so, and both the CLI and the panel
   do.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import numpy as np
import pytest
from cardbleed import Params
from PIL import Image

from proxdex import bleed
from proxdex.bleed import Kind, Tuning, TuningError
from proxdex.frames import SHIPPED, GuideId

ECARD = SHIPPED[GuideId.POKEMON_ECARD.value]
#: the e-Card spec's own numbers, i.e. marks already on target
ON_SPEC = ECARD.inset
#: marks inside the spec, so the border genuinely has to be widened
NARROW = (0.018, 0.026, 0.038, 0.056)
TRIM = (63.5, 88.9)


class TestTheKnobsAreCardbleedsOwn:
    """The declaration is held against `cardbleed.Params`, in both directions."""

    def test_every_knob_is_a_real_field(self) -> None:
        fields = {f.name for f in dataclasses.fields(Params)}
        assert {k.key for k in bleed.KNOBS} <= fields

    def test_every_field_is_offered(self) -> None:
        """All of them, deliberately: a hidden knob is the one somebody needed, and the
        whole point is to tune until the picture looks right."""
        fields = {f.name for f in dataclasses.fields(Params)}
        assert {k.key for k in bleed.KNOBS} == fields

    def test_each_default_is_read_from_cardbleed(self) -> None:
        """Not restated. A default written out here could disagree with the behaviour a
        card was actually fitted with, and nothing would say so — and proxdex keeps
        cardbleed's defaults rather than substituting a baseline of its own, so this is
        the *only* place these numbers live."""
        params = Params()
        for knob in bleed.KNOBS:
            assert knob.default == getattr(params, knob.key), knob.key

    def test_a_choice_knob_offers_its_own_default(self) -> None:
        for knob in bleed.KNOBS:
            if knob.kind is Kind.CHOICE:
                assert str(knob.default) in knob.choices, knob.key

    def test_a_numeric_knobs_default_is_inside_its_bounds(self) -> None:
        for knob in bleed.KNOBS:
            if knob.bounds is not None and knob.kind is not Kind.AUTO_INT:
                assert knob.bounds.holds(float(knob.default)), knob.key

    def test_a_knob_is_a_closed_set_or_a_range_and_never_both(self) -> None:
        """The two ways a value can be checked, and every knob is exactly one — so
        `_coerce` has no third path where something slips through unvalidated."""
        for knob in bleed.KNOBS:
            assert (knob.options is None) != (knob.bounds is None), knob.key

    def test_a_choice_knobs_values_come_from_its_enum(self) -> None:
        """Not restated beside it: the enum *is* the closed set, so a member added there
        appears in the CLI listing and the UI dropdown without another edit."""
        assert bleed.BY_ID[bleed.KnobId.MODE].choices == tuple(bleed.FillMode)
        assert bleed.BY_ID[bleed.KnobId.HALO].choices == tuple(bleed.Halo)
        assert bleed.BY_ID[bleed.KnobId.EDGE_FILL].choices == tuple(bleed.EdgeFill)

    def test_a_knob_id_is_an_enum_member(self) -> None:
        """Keyed by `KnobId` end to end, so nothing downstream can hold a setting that
        was never checked — a string that is *almost* a knob is the one mistake
        cardbleed cannot catch."""
        for knob in bleed.KNOBS:
            assert isinstance(knob.id, bleed.KnobId)
        parsed = Tuning.parse({"mode": "smart"})
        assert parsed.values[0][0] is bleed.KnobId.MODE

    def test_every_knob_says_what_it_does(self) -> None:
        """These are read by somebody who has never met cardbleed, in a panel with no
        room for a manual."""
        for knob in bleed.KNOBS:
            assert knob.label, knob.key
            assert len(knob.help) > 30, knob.key


class TestWhatItRefuses:
    """Each of these is accepted in silence by cardbleed itself."""

    def test_a_misspelled_knob(self) -> None:
        with pytest.raises(TuningError, match="not a cardbleed setting"):
            Tuning.parse({"jittter": 0.1})

    def test_the_error_names_the_real_ones(self) -> None:
        """A typo is the likeliest mistake here, so the message has to be the fix."""
        with pytest.raises(TuningError, match="jitter"):
            Tuning.parse({"jittter": 0.1})

    def test_a_value_outside_a_choice(self) -> None:
        with pytest.raises(TuningError, match="not one of"):
            Tuning.parse({"mode": "nonsense"})

    def test_a_number_that_is_not_one(self) -> None:
        with pytest.raises(TuningError, match="not a number"):
            Tuning.parse({"jitter": "lots"})

    def test_a_number_out_of_range(self) -> None:
        with pytest.raises(TuningError, match="outside"):
            Tuning.parse({"jitter": 99})

    def test_a_pair_with_no_equals(self) -> None:
        with pytest.raises(TuningError, match="key=value"):
            Tuning.from_pairs(("mode",))


class TestWhatItKeeps:
    def test_only_what_differs_from_the_default(self) -> None:
        """What makes a stored tuning readable: the card's record says `mode=smart`
        rather than restating all thirteen, which is the difference between a decision
        and a dump."""
        tuning = Tuning.parse({"mode": "smart", "jitter": Params().jitter})
        assert tuning.overrides == {"mode": "smart"}

    def test_all_defaults_is_empty(self) -> None:
        every = {k.key: k.default for k in bleed.KNOBS}
        assert Tuning.parse(every).empty

    def test_nothing_is_empty(self) -> None:
        assert Tuning.parse({}).empty
        assert Tuning.parse(None).empty

    def test_an_int_knob_stays_an_int(self) -> None:
        """cardbleed indexes with some of these, and 12.0 is not 12 there."""
        got = Tuning.parse({"sample": "8"}).overrides["sample"]
        assert isinstance(got, int)

    def test_trim_takes_auto_or_a_count(self) -> None:
        assert Tuning.parse({"trim": "12"}).overrides == {"trim": 12}
        # "auto" *is* the default, so asking for it is asking for nothing
        assert Tuning.parse({"trim": "auto"}).empty

    def test_it_round_trips_through_the_cli_spelling(self) -> None:
        """`spelled()` is what the UI sends and what the marker stores, so the two
        cannot disagree about how a tuning is written down."""
        first = Tuning.parse({"mode": "smart", "sample": 8})
        assert Tuning.from_pairs(tuple(first.spelled())) == first

    def test_the_order_is_stable(self) -> None:
        """A marker is a file that gets diffed and re-read; the same tuning must not
        write two different ways."""
        a = Tuning.parse({"sample": 8, "mode": "smart"})
        b = Tuning.parse({"mode": "smart", "sample": 8})
        assert a.spelled() == b.spelled()


class TestAFillOnlyMattersWhereBorderIsInvented:
    """The correction that came out of building this.

    A panel of live controls over a fit that invents nothing is exactly the
    "looks finished, does nothing" trap, and it was nearly shipped: three `mode`
    values were compared by eye and *read* as different when the files were identical.
    """

    @staticmethod
    def _plan(current: tuple[float, float, float, float]) -> object:
        return bleed.fit_plan(600, 825, ECARD, current, TRIM, stretch=True)

    def test_marks_on_the_spec_extend_nothing(self) -> None:
        plan = self._plan(ON_SPEC)
        assert not bleed.extends(plan)  # type: ignore[arg-type]

    def test_the_extensions_are_float_noise_rather_than_zero(self) -> None:
        """Which is why the predicate needs an epsilon at all — `> 0` would call
        1e-13 an added pixel and answer yes on every card."""
        plan = self._plan(ON_SPEC)
        values = plan.ext.values()  # type: ignore[attr-defined]
        assert all(0 < v < bleed.EXT_EPSILON for v in values)

    def test_marks_inside_the_spec_do_extend(self) -> None:
        plan = self._plan(NARROW)
        assert bleed.extends(plan)  # type: ignore[arg-type]
        assert min(plan.ext.values()) > 10  # type: ignore[attr-defined]


class TestAZeroTargetInventsNothing:
    """`bleed.by_resize` / `reshape_only` — the fix for a *visible* defect.

    A spec of 0 on all four edges is a card with no border: a full-bleed printing, or a
    game of your own whose cards carry none. With the stretch on, the fit is pure
    geometry — crop to the marks, resize to the trim — and cardbleed has no area to
    fill. It fills anyway: its synthesis pass rewrites the outermost pixels whether or
    not there is anything to synthesize, which on a card whose art reaches the edge is a
    smeared line down two edges. Flat test colour hides it completely, which is why this
    test uses a **gradient with marked outer rows** and asserts on the pixels.

    The stretch stays the caller's choice. Unticked, a zero target really does need
    border invented to reach the aspect, and that goes through cardbleed as before.
    """

    FULL_BLEED = SHIPPED[GuideId.BORDERLESS.value]
    NONE = (0.0, 0.0, 0.0, 0.0)

    @pytest.fixture
    def art(self, tmp_path: Path) -> Path:
        """Art to all four edges, with a distinct outermost row top and bottom."""
        a = np.zeros((825, 600, 3), np.uint8)
        yy, xx = np.mgrid[0:825, 0:600]
        a[..., 0] = xx * 255 // 599
        a[..., 1] = yy * 255 // 824
        a[..., 2] = 128
        a[0, :] = (255, 0, 255)
        a[-1, :] = (0, 255, 255)
        Image.fromarray(a).save(path := tmp_path / "bleed.png")
        return path

    def plan(self, *, stretch: bool) -> object:
        return bleed.fit_plan(
            600, 825, self.FULL_BLEED, self.NONE, TRIM, stretch=stretch
        )

    def test_a_stretched_zero_target_is_written_by_resizing(self) -> None:
        assert bleed.by_resize(self.FULL_BLEED, self.plan(stretch=True))  # type: ignore[arg-type]

    def test_without_the_stretch_it_extends_and_goes_through_cardbleed(self) -> None:
        """The other half of the choice: with no stretch the trim cannot match the
        art's aspect, so border has to be invented and this must *not* take the
        resize path."""
        plan = self.plan(stretch=False)
        assert bleed.extends(plan)  # type: ignore[arg-type]
        assert not bleed.by_resize(self.FULL_BLEED, plan)  # type: ignore[arg-type]

    def test_a_bordered_spec_never_takes_the_resize_path(self) -> None:
        """Gated on the spec being frameless, not on `extends` alone: a bordered card
        whose marks already exceed its target also invents nothing, but there cardbleed
        is squaring die-cut corners, which is real work on a real border."""
        assert not bleed.by_resize(
            ECARD, bleed.fit_plan(600, 825, ECARD, ON_SPEC, TRIM, stretch=True)
        )  # type: ignore[arg-type]

    def test_the_cards_own_edge_pixels_survive(self, art: Path, tmp_path: Path) -> None:
        """The defect, in the terms it was found in: cardbleed's fit buried the
        outermost rows under invented ones."""
        plan = self.plan(stretch=True)
        bleed.reshape_only(art, out := tmp_path / "out.png", self.NONE, plan)  # type: ignore[arg-type]
        got = np.asarray(Image.open(out).convert("RGB"))
        mid = got.shape[1] // 2
        assert tuple(got[0][mid]) == (255, 0, 255)
        assert tuple(got[-1][mid]) == (0, 255, 255)

    def test_it_is_a_resize_of_the_art_and_nothing_else(
        self, art: Path, tmp_path: Path
    ) -> None:
        """No pixel invented anywhere — the whole claim, checked against Pillow's own
        resize of the same source. cardbleed's path differed from this by up to 255
        levels along two edges."""
        plan = self.plan(stretch=True)
        bleed.reshape_only(art, out := tmp_path / "out.png", self.NONE, plan)  # type: ignore[arg-type]
        with Image.open(out) as im:
            got = np.asarray(im.convert("RGB"), np.int16)
            size = im.size
        with Image.open(art) as src:
            want = np.asarray(
                src.convert("RGB").resize(size, Image.Resampling.LANCZOS), np.int16
            )
        assert np.abs(got - want).max() == 0

    def test_the_aspect_is_exactly_the_trim(self, art: Path, tmp_path: Path) -> None:
        """What the stretch is *for*, and the reason a resize is acceptable here at
        all: the output is the card's proportions to the pixel."""
        plan = self.plan(stretch=True)
        bleed.reshape_only(art, out := tmp_path / "out.png", self.NONE, plan)  # type: ignore[arg-type]
        with Image.open(out) as im:
            w, h = im.size
        assert w / h == pytest.approx(TRIM[0] / TRIM[1], abs=1e-3)


class TestTheKnobsActuallyReachCardbleed:
    """A tuning that changed no pixels would be the whole feature failing silently."""

    @pytest.fixture
    def scan(self, tmp_path: Path) -> Path:
        """A card-shaped scan with **grain**, which is what the fill methods disagree
        about — see `test_a_perfectly_flat_border_fills_the_same_way`."""
        return _scan(tmp_path / "card.png", grain=True)

    def _fit(self, scan: Path, out: Path, tune: Tuning) -> str:
        bleed.fit(scan, out, ECARD, NARROW, TRIM, stretch=True, tune=tune)
        return hashlib.sha256(out.read_bytes()).hexdigest()

    def test_each_fill_method_produces_a_different_picture(
        self, scan: Path, tmp_path: Path
    ) -> None:
        seen = {
            mode: self._fit(
                scan, tmp_path / f"{mode}.png", Tuning.parse({"mode": mode})
            )
            for mode in ("smart", "naive", "pattern")
        }
        assert len(set(seen.values())) == 3, seen

    def test_a_knob_other_than_the_method_changes_it_too(
        self, scan: Path, tmp_path: Path
    ) -> None:
        plain = self._fit(scan, tmp_path / "a.png", Tuning())
        shuffled = self._fit(
            scan, tmp_path / "b.png", Tuning.parse({"shuffle": 0, "jitter": 0})
        )
        assert plain != shuffled

    def test_an_empty_tuning_is_cardbleeds_own_defaults(
        self, scan: Path, tmp_path: Path
    ) -> None:
        """So "back to the defaults" really is what a card got before any of this, and
        proxdex holds no second set of numbers to keep in step. `mode=smart` was briefly
        made the default here and taken back out for that reason; this is what says it
        stayed out."""
        a = self._fit(scan, tmp_path / "a.png", Tuning())
        b = self._fit(scan, tmp_path / "b.png", Tuning.parse({"mode": "pattern"}))
        assert a == b

    def test_the_geometry_is_untouched_by_a_tuning(
        self, scan: Path, tmp_path: Path
    ) -> None:
        """A fill setting changes what the added border *looks* like and nothing about
        where the card lands — otherwise the readout above the panel would be lying."""
        self._fit(scan, tmp_path / "a.png", Tuning())
        self._fit(scan, tmp_path / "b.png", Tuning.parse({"mode": "naive"}))
        with Image.open(tmp_path / "a.png") as a, Image.open(tmp_path / "b.png") as b:
            assert a.size == b.size

    def test_a_perfectly_flat_border_fills_the_same_way(self, tmp_path: Path) -> None:
        """Measured while writing this, and worth knowing: with no grain in the border
        there is nothing for the methods to invent differently, and all three produce a
        byte-identical file. Real scans have grain, which is why they diverge on one —
        so a synthetic flat card is the wrong thing to test a fill with."""
        flat = _scan(tmp_path / "flat.png", grain=False)
        seen = {
            self._fit(flat, tmp_path / f"{m}.png", Tuning.parse({"mode": m}))
            for m in ("smart", "naive", "pattern")
        }
        assert len(seen) == 1


def _scan(path: Path, *, grain: bool) -> Path:
    """A card-shaped image: a yellow border, an art panel, a repeating edge strip, and
    optionally the grain a real scan carries."""
    rng = np.random.default_rng(7)
    a = np.full((825, 600, 3), (250, 208, 20), np.uint8)
    if grain:
        a = np.clip(a.astype(int) + rng.integers(-18, 18, a.shape), 0, 255).astype(
            np.uint8
        )
    a[795:825, ::7] = (30, 30, 40)  # a dot-code-ish strip along the bottom
    a[::7, 0:40] = (30, 30, 40)  # and down the left
    a[120:700, 120:480] = (90, 140, 200)  # an art panel
    Image.fromarray(a).save(path)
    return path
