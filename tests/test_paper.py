"""Does the grid fit the paper? Nothing checked, and both shipped defaults were wrong.

This is arithmetic, so it is exactly the kind of thing that looks fine forever: the
renderer places the grid where it is told and PIL clips whatever falls outside the page,
silently, and what gets clipped **first** is the cut bleed — the part you were going to
throw away. So the sheet looks perfect until a row is short.

The two defects, both measured on the shipped defaults:

* **A4 3×3 at 2.5mm bleed is 205.5mm wide on a 210mm sheet** — 2.25mm from each edge,
  which no real printer can reach. With the 5mm margin *forced* (the placement was
  ``max(margin, centred)``) the whole 5.5mm of overflow went onto the right edge and the
  page clipped 0.51mm of it off every sheet.
* **Letter 3×3 is 281.7mm tall on a 279.4mm sheet**, so it never fitted at all: the
  bottom row of *cards* — not bleed, cards — hung 4.81mm off the paper.

Three things changed and each is pinned here. The margin is a **constraint that is
reported** rather than an offset that is forced; the grid is **centred in the printable
box**; and the default bleed is 1.5mm, because at 2.5 the arithmetic cannot close on A4
at any honest margin. Plus per-edge margins, because a printer's unprintable border is
not symmetrical — 4mm at the sides and 5mm at the top is ordinary, and many grip 12mm at
the bottom.
"""

from __future__ import annotations

import pytest

from proxdex import sheet
from proxdex.config import Config, Orientation, PageSize

#: both games print at this, and it is the only card size a library has
CARD = (63.5, 88.9)
A4 = (210.0, 297.0)
LETTER = (215.9, 279.4)


def cfg_for(**over: object) -> Config:
    cfg = Config()
    for key, value in over.items():
        setattr(cfg, key, Config.coerce(key, value))
    return cfg


def placed(cfg: Config) -> tuple[float, float, float, float]:
    """Where the grid really lands, in mm: (left, top, right, bottom).

    Measured off the `Geo`'s own **pixels** rather than recomputed in millimetres. The
    first version mixed the two and reported a 0.02mm asymmetry that does not exist:
    a cell is rounded to whole pixels once, so a three-cell grid is up to 1.5px shy of
    three times the millimetre figure, and comparing one against the other measures the
    rounding rather than the placement.
    """
    geo = sheet.geometry(cfg, CARD)
    gw = geo.cols * geo.cell_w + (geo.cols - 1) * geo.gap_x
    gh = geo.rows * geo.cell_h + (geo.rows - 1) * geo.gap_y
    return (
        geo.x_off / geo.ppm,
        geo.y_off / geo.ppm,
        (geo.x_off + gw) / geo.ppm,
        (geo.y_off + gh) / geo.ppm,
    )


class TestTheDefaultsFitTheirPaper:
    """The whole point. A fresh library must print on the paper it says it prints on."""

    def test_a4_at_the_defaults_fits_inside_the_margin(self) -> None:
        cfg = cfg_for()
        assert cfg.sheet_page is PageSize.A4, "the default paper"
        fit = sheet.paper_fit(cfg, CARD)
        assert fit.ok, fit.note
        # and with room to spare on both axes, not by a hair
        assert fit.safe[0] - fit.grid[0] > 0.4
        assert fit.safe[1] - fit.grid[1] > 0.4

    def test_nothing_is_off_the_paper_at_the_defaults(self) -> None:
        left, top, right, bottom = placed(cfg_for())
        assert left >= 0
        assert top >= 0
        assert right <= A4[0]
        assert bottom <= A4[1]

    def test_the_grid_is_symmetric_on_the_page(self) -> None:
        """`max(margin, centred)` made it lopsided the moment the margin bit — the very
        case where you least want the layout quietly shifted."""
        left, top, right, bottom = placed(cfg_for())
        assert left == pytest.approx(A4[0] - right, abs=0.02)
        assert top == pytest.approx(A4[1] - bottom, abs=0.02)

    def test_the_bleed_default_is_what_makes_the_width_close(self) -> None:
        """Three columns cost `190.5 + 6 × bleed` mm. The margin is the honest number —
        5mm is a real printer's border — so the bleed is what had to give, and it is a
        sheet-time value that no stored master depends on."""
        cfg = cfg_for()
        assert cfg.bleed_mm == 1.5
        three_cols = 3 * CARD[0] + 6 * cfg.bleed_mm
        assert three_cols == pytest.approx(199.5)
        assert three_cols <= A4[0] - 2 * cfg.sheet_margin_mm
        # at the old 2.5 it did not, which is the whole reason it changed
        assert 3 * CARD[0] + 6 * 2.5 > A4[0] - 2 * cfg.sheet_margin_mm


class TestWhatDoesNotFitIsReported:
    def test_letter_cannot_hold_three_rows_and_says_so(self) -> None:
        """Not a matter of margins: 3 rows of an 88.9mm card is 266.7mm of ink before
        any bleed at all, and Letter is 279.4mm tall. It was cutting cards in half."""
        cfg = cfg_for(sheet_page="letter")
        fit = sheet.paper_fit(cfg, CARD)
        assert not fit.ok
        assert fit.over_h > 0
        assert fit.rows == 2, "and it names what does fit"
        assert "3×2 fits" in fit.note
        assert "too tall" in fit.note

    def test_the_note_names_the_numbers_and_a_way_out(self) -> None:
        cfg = cfg_for(bleed_mm=2.5)  # the old default, on A4
        fit = sheet.paper_fit(cfg, CARD)
        assert not fit.ok
        assert "205.5" in fit.note, "what the grid measures"
        assert "200.0" in fit.note, "and what there is room for"
        assert fit.bleed_fix is not None
        assert "bleed ≤" in fit.note

    def test_the_suggested_bleed_really_fits(self) -> None:
        """A suggestion that does not itself fit is worse than none, so it is rounded
        *down* to a hundredth rather than to the nearest."""
        cfg = cfg_for(bleed_mm=2.5)
        fix = sheet.paper_fit(cfg, CARD).bleed_fix
        assert fix is not None
        assert sheet.paper_fit(cfg_for(bleed_mm=fix), CARD).ok

    def test_no_bleed_is_offered_when_bleed_cannot_help(self) -> None:
        """Four columns is 254mm of bare card against a 200mm box, so no bleed however
        small would fit it — and naming one would send somebody to change the single
        setting that cannot possibly be the answer."""
        cfg = cfg_for(sheet_cols=4)
        fit = sheet.paper_fit(cfg, CARD)
        assert not fit.ok
        assert fit.bleed_fix is None
        assert "bleed" not in fit.note
        assert "3×3 fits" in fit.note

    def test_a_bleed_it_offers_may_still_be_impractical(self) -> None:
        """Letter's three rows *do* fit at 0.44mm — 266.7mm of bare card in a 269.4mm
        box — and that is reported because it is true, not recommended. The note names
        the smaller grid too, and 0.44mm of bleed is visibly not something you can cut
        to. proxdex says what the arithmetic is and leaves the judgement alone."""
        fit = sheet.paper_fit(cfg_for(sheet_page="letter"), CARD)
        assert fit.bleed_fix == 0.44
        assert sheet.paper_fit(cfg_for(sheet_page="letter", bleed_mm=0.44), CARD).ok
        assert "3×2 fits" in fit.note

    def test_a_fitting_run_has_nothing_to_say(self) -> None:
        assert sheet.paper_fit(cfg_for(), CARD).note == ""

    def test_the_plan_carries_it_per_group(self) -> None:
        """Per trim size, because each has its own grid: an oversized card can fit while
        the ordinary ones do not, and one answer for the page would be wrong for one of
        them."""
        cfg = cfg_for(sheet_page="letter")
        run = sheet.plan([], cfg).json(cfg)
        assert run["safe"] == [205.9, 269.4]
        assert run["margins"] == [5.0, 5.0, 5.0, 5.0]


class TestPerEdgeMargins:
    """A printer's unprintable border is not symmetrical."""

    def test_an_unset_edge_takes_the_page_margin(self) -> None:
        m = sheet.margins(cfg_for(sheet_margin_mm=7.0))
        assert (m.top, m.right, m.bottom, m.left) == (7.0, 7.0, 7.0, 7.0)
        assert m.uniform

    def test_each_edge_can_differ(self) -> None:
        """The case that prompted this: 4mm at the sides, 5mm at the top."""
        m = sheet.margins(
            cfg_for(
                sheet_margin_mm=5.0,
                sheet_margin_left_mm=4.0,
                sheet_margin_right_mm=4.0,
            )
        )
        assert (m.top, m.right, m.bottom, m.left) == (5.0, 4.0, 5.0, 4.0)
        assert not m.uniform
        assert sheet.safe_mm(cfg_for(sheet_margin_left_mm=4.0)) == (201.0, 287.0)

    def test_an_asymmetric_margin_shifts_the_grid_rather_than_centring_it(self) -> None:
        """Centred in the **printable box**, not on the paper — otherwise a 12mm bottom
        margin would be described and then ignored."""
        # 12mm at the bottom — an ordinary inkjet's grip margin, and the grid still fits
        cfg = cfg_for(sheet_margin_bottom_mm=12.0)
        assert sheet.paper_fit(cfg, CARD).ok
        left, top, right, bottom = placed(cfg)
        assert bottom <= A4[1] - 12.0 + 0.02, "the bottom margin is respected"
        assert top < A4[1] - bottom, "so the grid sits higher than centre"
        assert left == pytest.approx(A4[0] - right, abs=0.02), "x is untouched"

    def test_a_tight_printer_is_told_the_truth(self) -> None:
        """4mm sides on A4 leaves 202mm, and three columns at 2.5mm bleed want 205.5."""
        cfg = cfg_for(bleed_mm=2.5, sheet_margin_left_mm=4.0, sheet_margin_right_mm=4.0)
        fit = sheet.paper_fit(cfg, CARD)
        assert not fit.ok
        assert fit.over_w == pytest.approx(3.5)
        assert fit.bleed_fix == 1.91
        loose = cfg_for(
            bleed_mm=1.91, sheet_margin_left_mm=4.0, sheet_margin_right_mm=4.0
        )
        assert sheet.paper_fit(loose, CARD).ok

    def test_margins_bigger_than_the_paper_do_not_go_negative(self) -> None:
        assert sheet.safe_mm(cfg_for(sheet_margin_mm=50.0, sheet_page="a4")) == (
            110.0,
            197.0,
        )
        # 50 a side on the short axis of a landscape A4 is 210 - 100
        assert sheet.safe_mm(
            cfg_for(sheet_margin_mm=50.0, sheet_orientation="landscape")
        ) == (197.0, 110.0)


class TestHowManyFit:
    def test_landscape_is_measured_on_the_rotated_paper(self) -> None:
        cfg = cfg_for(sheet_orientation="landscape")
        assert sheet.page_mm(cfg) == (297.0, 210.0)
        assert sheet.holds(cfg, CARD) == (4, 2)

    def test_letter_holds_one_row_fewer_than_a4(self) -> None:
        assert sheet.holds(cfg_for(), CARD) == (3, 3)
        assert sheet.holds(cfg_for(sheet_page="letter"), CARD) == (3, 2)

    def test_spacing_costs_cells(self) -> None:
        assert sheet.holds(cfg_for(sheet_spacing_mm=20.0), CARD)[0] == 2

    def test_an_oversized_card_takes_what_the_page_holds(self) -> None:
        """`grid_for` keeps the *configured* grid only for the configured trim; any
        other size has no configured grid to keep."""
        big = (88.9, 127.0)
        assert sheet.grid_for(cfg_for(), CARD) == (3, 3)
        assert sheet.grid_for(cfg_for(), big) == sheet.holds(cfg_for(), big)

    def test_a_card_bigger_than_the_paper_still_gets_one_cell(self) -> None:
        """proxdex would rather print an over-margin page than silently shrink a card,
        and `holds` answering 0 is what makes that a deliberate clamp."""
        huge = (400.0, 500.0)
        assert sheet.holds(cfg_for(), huge) == (0, 0)
        assert sheet.grid_for(cfg_for(), huge) == (1, 1)


class TestEveryPaperAndOrientation:
    """A table, because the arithmetic is the deliverable — and because a default that
    fits A4 portrait and nothing else would have looked like a pass."""

    @pytest.mark.parametrize("page", list(PageSize))
    @pytest.mark.parametrize("orient", list(Orientation))
    def test_the_grid_that_holds_really_fits_on_paper(
        self, page: PageSize, orient: Orientation
    ) -> None:
        cfg = cfg_for(sheet_page=page.value, sheet_orientation=orient.value)
        cols, rows = sheet.holds(cfg, CARD)
        assert cols, (page, orient)
        assert rows, (page, orient)
        cfg.sheet_cols, cfg.sheet_rows = cols, rows
        fit = sheet.paper_fit(cfg, CARD)
        assert fit.ok, (page, orient, fit.note)
        left, top, right, bottom = placed(cfg)
        pw, ph = sheet.page_mm(cfg)
        m = sheet.margins(cfg)
        assert left >= m.left - 0.02, (page, orient)
        assert top >= m.top - 0.02, (page, orient)
        assert right <= pw - m.right + 0.02, (page, orient)
        assert bottom <= ph - m.bottom + 0.02, (page, orient)
