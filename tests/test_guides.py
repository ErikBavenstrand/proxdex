"""Cut guides: the lines you take a blade to, and which side they are on.

This file earns its place because **a guide is only ever wrong on paper**. It is drawn
where it was told to be drawn, so a rendered page always looks correct; what a screen
cannot show is whether that place is where the card's trim edge actually landed. Five
things are pinned, and **two of the five were defects found by writing them down** —
both of them in the shipped defaults, both of them printing ink onto every card:

1. **A guide follows the ink offset.** It did not. Cards were pasted at ``x + ox`` and
   every line drawn at ``x``, so the moment you nudged the backs to fix a misregistered
   duplex sheet — which is exactly what the offsets are for — you were cutting along
   lines that no longer described any card on the page. 1.5mm at 1400dpi is 83px, and
   the PDF looks immaculate.
2. **``outside`` means away from the card**, and the sign said the opposite: with the
   shipped ``corners``/``outside`` every tick was drawn *into* the trim, so a setting
   whose own help reads "outside the trim keeps marks off the card" put four marks on
   every card of every sheet.
3. **A registration mark does not follow the offset**, which is the opposite of (1) and
   the reason both are here: nudged along with the cards they would line up on every
   sheet by construction and measure nothing at all, which is a tool that always
   reports success.
4. **No mark runs onto a neighbouring card**, however far its ``reach`` extends —
   "only to the corner and not more than that". That is the whole difference from
   ``full``, and "the line is 3px inside the artwork" is not something anyone sees
   before it has been printed on sixty cards. Nor is "these seven cut marks are
   around empty cells".
5. **The two sides really get different guides.** The point of the feature. A back that
   silently used the front's colour is invisible until you hold the sheet to a light,
   which is the one thing back guides exist for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from click.testing import CliRunner
from PIL import Image

from proxdex import sheet
from proxdex.cli import cli
from proxdex.config import (
    MARKER,
    Config,
    Faces,
    GuidePlacement,
    GuideReach,
    GuideStyle,
)

FRONT = "#ff0000"
BACK = "#0000ff"
RED = (255, 0, 0)
BLUE = (0, 0, 255)
BLACK = (0, 0, 0)


def base(**over: object) -> Config:
    """A 2x2 sheet at a low dpi, so a page is small enough to scan in a test and one
    millimetre is still several pixels (10, at 254dpi)."""
    cfg = Config()
    cfg.sheet_dpi = 254
    cfg.sheet_cols, cfg.sheet_rows = 2, 2
    cfg.sheet_margin_mm = 5.0
    cfg.sheet_guide_color = FRONT
    cfg.sheet_guide_width_mm = 0.1  # one pixel at this dpi
    for key, value in over.items():
        setattr(cfg, key, value)
    return cfg


def geo_of(cfg: Config) -> sheet.Geo:
    return sheet.geometry(cfg, (cfg.card_w_mm, cfg.card_h_mm))


def render(cfg: Config, *, back: bool = False, cards: bool = True) -> Any:
    """A page as ``(pixels[y][x], geo)``.

    The cells are flat black, so anything drawn over one is visible as not-black —
    which is what lets a test say "no guide crosses the artwork" rather than only
    "a guide exists". ``cards=False`` renders an empty page, for the things that are
    about the paper and not about a card.
    """
    geo = geo_of(cfg)
    cells: list[Image.Image | None] = []
    if cards:
        card = Image.new("RGB", (geo.cell_w, geo.cell_h), BLACK)
        cells = [card] * geo.per_page
    page = sheet.render_page(cells, cfg, geo, is_back=back)
    return np.asarray(page.convert("RGB")), geo


def holds(px: Any, color: tuple[int, int, int]) -> bool:
    return bool((px == color).all(axis=2).any())


class TestAGuideFollowsTheCard:
    """The bug, in both directions.

    Asserted as a **whole-raster translation** rather than by finding the lines and
    comparing coordinates: the invariant is that a nudged page is the same page moved,
    cards and guides together, and any drift between the two — which is precisely the
    defect — shows up as a mismatch somewhere in it. Looking for the lines separately
    would have needed a rule for telling a vertical guide from a horizontal one, and
    the first version of that rule was wrong (every column holds part of a horizontal
    line, so every column looked like a vertical guide).
    """

    @pytest.mark.parametrize("style", [GuideStyle.CORNERS, GuideStyle.FULL])
    def test_an_offset_moves_the_lines_with_the_cards(self, style: GuideStyle) -> None:
        n = 20  # 2mm at 254dpi
        cfg = base(sheet_guide_style=style)
        plain, _ = render(cfg)
        assert holds(plain, RED), f"{style} draws guides at all"
        cfg.sheet_front_offset_x_mm = 2.0
        moved, _ = render(cfg)
        assert np.array_equal(moved[:, n:], plain[:, :-n])

    @pytest.mark.parametrize("style", [GuideStyle.CORNERS, GuideStyle.FULL])
    def test_the_backs_own_offset_moves_them_too(self, style: GuideStyle) -> None:
        """The one that matters: a back offset is what you set with a misregistered
        duplex sheet in your hand, and it is the fronts' guides that were right."""
        n = 20
        cfg = base(sheet_guide_style=style, sheet_guides_back=True)
        plain, _ = render(cfg, back=True)
        cfg.sheet_back_offset_y_mm = 2.0
        moved, _ = render(cfg, back=True)
        assert np.array_equal(moved[n:, :], plain[:-n, :])

    def test_a_registration_mark_stays_put(self) -> None:
        """The opposite rule, deliberately: these are measured *against* the drift, so
        moving them with the correction would report every printer as perfect.

        Rendered on bare paper rather than over cards, because the cards *do* move and
        this is a claim about the marks alone."""
        cfg = base(sheet_guides=False)
        cfg.sheet_reg_marks = Config.coerce("sheet_reg_marks", "corners")
        plain, _ = render(cfg, cards=False)
        assert holds(plain, BLACK), "the fixture draws marks at all"
        cfg.sheet_back_offset_x_mm = 3.0
        moved, _ = render(cfg, back=True, cards=False)
        assert np.array_equal(plain, moved)


class TestPlacementMeansWhatItSays:
    """`outside` is away from the card, and the sign said the opposite.

    With the shipped default — `corners`, `outside`, whose own help text reads "outside
    the trim keeps marks off the card" — every tick was drawn *into* the trim, so four
    marks printed on every card of every sheet. Invisible in the ordinary way: a mark is
    a mark until you notice which side of the cut it is on, and 4mm in from the corner
    lands under a card's own border.
    """

    def test_outside_draws_away_from_the_card(self) -> None:
        cfg = base(sheet_guide_style=GuideStyle.CORNERS)
        px, geo = render(cfg)
        x0, y0 = geo.cell_xy(0, 0)
        tx, ty = round(x0 + geo.bleed), round(y0 + geo.bleed)
        assert tuple(px[ty, tx - 10]) == RED, "into the bleed, where the waste is"
        assert tuple(px[ty, tx + 10]) != RED, "not onto the card"

    def test_inside_draws_onto_the_card(self) -> None:
        cfg = base(
            sheet_guide_style=GuideStyle.CORNERS,
            sheet_guide_placement=GuidePlacement.INSIDE,
        )
        px, geo = render(cfg)
        x0, y0 = geo.cell_xy(0, 0)
        tx, ty = round(x0 + geo.bleed), round(y0 + geo.bleed)
        assert tuple(px[ty, tx + 10]) == RED
        assert tuple(px[ty, tx - 10]) != RED

    def test_the_overshoot_makes_a_tick_straddle_its_corner(self) -> None:
        """`cross` means the same thing for `corners` as for `edges`: how far onto the
        card. `placement` says which side the mark runs to, `cross` how far it
        overshoots to the other — so a non-zero overshoot turns an L into a +."""
        cfg = base(sheet_guide_style=GuideStyle.CORNERS, sheet_guide_cross_mm=1.0)
        px, geo = render(cfg)
        x0, y0 = geo.cell_xy(0, 0)
        tx, ty = round(x0 + geo.bleed), round(y0 + geo.bleed)
        assert tuple(px[ty, tx - 10]) == RED, "still runs out into the bleed"
        assert tuple(px[ty, tx + 5]) == RED, "and now over the cut as well"


class TestReachIsItsOwnQuestion:
    """`fixed` / `join` / `paper` — one arm, three limits.

    The three are asserted at the *same* three probe points, because the whole claim is
    that they differ **only** in how far an arm runs: the margin (is there ink between
    the outer card and the paper edge?), the gutter (do two neighbours' marks join?) and
    the paper edge itself. Written as a table for that reason — a per-reach test would
    let two of them quietly grow different geometry.
    """

    def probes(self, reach: GuideReach) -> dict[str, bool]:
        cfg = base(sheet_guide_style=GuideStyle.CORNERS, sheet_guide_reach=reach)
        px, geo = render(cfg)
        x0, y0 = geo.cell_xy(0, 0)
        x1, _ = geo.cell_xy(1, 0)
        left = round(x0 + geo.bleed)  # col 0's left trim edge
        top = round(y0 + geo.bleed)
        gut = (round(x0 + geo.cell_w - geo.bleed) + round(x1 + geo.bleed)) // 2
        return {
            # halfway between the outer trim edge and the paper: only `paper` reaches
            "margin": tuple(px[top, left // 2]) == RED,
            # the middle of the gap between col 0 and col 1, on the top trim line
            "gutter": tuple(px[top, gut]) == RED,
            "paper_edge": holds(px[top][:, None][:2], RED) or tuple(px[top, 0]) == RED,
        }

    def test_fixed_stays_a_tick(self) -> None:
        """What the corner marks have always been: `guide_mm` and no further. At the
        4mm default against this fixture's 5mm gap the two arms *do* meet — which is why
        `join` is not merely cosmetic: it is the only one that promises to."""
        got = self.probes(GuideReach.FIXED)
        assert got["margin"] is False, "a tick does not reach into the margin"
        assert got["paper_edge"] is False

    def test_join_bridges_the_gap_but_leaves_the_margin_clean(self) -> None:
        got = self.probes(GuideReach.JOIN)
        assert got["gutter"] is True, "two cards' marks make one line"
        assert got["margin"] is False, "and stop at the outermost card"
        assert got["paper_edge"] is False

    def test_paper_runs_to_the_sheet_edge(self) -> None:
        """What a rotary trimmer needs — you line its blade up on the sheet edge, so a
        mark that stops 10mm short is a mark you cannot use."""
        got = self.probes(GuideReach.PAPER)
        assert got["gutter"] is True
        assert got["margin"] is True
        assert got["paper_edge"] is True

    def test_a_wide_gap_defeats_fixed_and_not_join(self) -> None:
        """The case that makes them different rather than a matter of taste: with the
        cards spaced further apart than twice the mark length, `fixed` leaves a gap in
        the middle of the gutter and `join` does not."""
        for reach, bridged in ((GuideReach.FIXED, False), (GuideReach.JOIN, True)):
            cfg = base(
                sheet_guide_style=GuideStyle.CORNERS,
                sheet_guide_reach=reach,
                sheet_guide_mm=2.0,
                sheet_spacing_mm=12.0,  # 12 + 2*2.5 bleed = 17mm between trim edges
            )
            px, geo = render(cfg)
            x0, y0 = geo.cell_xy(0, 0)
            x1, _ = geo.cell_xy(1, 0)
            top = round(y0 + geo.bleed)
            mid = (round(x0 + geo.cell_w - geo.bleed) + round(x1 + geo.bleed)) // 2
            assert (tuple(px[top, mid]) == RED) is bridged, reach


class TestNoMarkRunsOntoANeighbour:
    """ "Only to the corner and not more than that" — the safety property.

    A mark may cross its own cut by `cross` and no further, so however far a reach
    extends, **no line is ever drawn along the middle of a card's edge**. That is the
    whole difference from `full`, and a line up the middle of a Charizard is not a cut
    guide, it is a ruined card.
    """

    @pytest.mark.parametrize("reach", list(GuideReach))
    def test_no_reach_puts_ink_on_a_card(self, reach: GuideReach) -> None:
        cfg = base(
            sheet_guide_style=GuideStyle.CORNERS,
            sheet_guide_reach=reach,
            sheet_guide_cross_mm=0.0,
        )
        px, geo = render(cfg)
        for col, row in ((0, 0), (1, 0), (0, 1), (1, 1)):
            x0, y0 = geo.cell_xy(col, row)
            inset = round(geo.bleed) + 2
            inner = px[
                y0 + inset : y0 + geo.cell_h - inset,
                x0 + inset : x0 + geo.cell_w - inset,
            ]
            assert (inner == BLACK).all(), (reach, col, row)

    def test_full_is_the_style_that_does_paint_over_them(self) -> None:
        """Kept, and the contrast is the point: it is the only way to ask for that."""
        cfg = base(sheet_guide_style=GuideStyle.FULL)
        px, geo = render(cfg)
        x0, y0 = geo.cell_xy(0, 0)
        row = px[y0 + geo.cell_h // 2, x0 + round(geo.bleed) : x0 + geo.cell_w]
        assert holds(row[:, None], RED)

    def test_the_overshoot_crosses_the_corner(self) -> None:
        """`cross` is the one thing that may put ink on a card, and it is what makes the
        four lines meet in a + at each corner — the only thing on the page that says the
        grid is square."""
        cfg = base(
            sheet_guide_style=GuideStyle.CORNERS,
            sheet_guide_reach=GuideReach.PAPER,
            sheet_guide_cross_mm=1.0,
        )
        px, geo = render(cfg)
        x0, y0 = geo.cell_xy(0, 0)
        tx, ty = round(x0 + geo.bleed), round(y0 + geo.bleed)
        assert tuple(px[ty + 5, tx]) == RED, "the vertical arm runs onto the card"
        assert tuple(px[ty, tx + 5]) == RED, "and so does the horizontal one"
        assert tuple(px[ty + 40, tx]) == BLACK, "and stops — it is not a full line"


class TestOnlyRealCardsAreMarked:
    """A partial page is marked as what it holds.

    The page-wide styles used to derive their lines from the *grid*, so two cards on a
    nine-up sheet were surrounded by nine cards' worth of cut marks — seven cuts nobody
    is making, and on a sheet you are about to take a blade to that is worse than none.
    """

    def render_two(self, **over: object) -> Any:
        cfg = base(**over)
        geo = geo_of(cfg)
        card = Image.new("RGB", (geo.cell_w, geo.cell_h), BLACK)
        cells: list[Image.Image | None] = [card, None, None, None]
        page = sheet.render_page(cells, cfg, geo, is_back=False)
        return np.asarray(page.convert("RGB")), geo

    @pytest.mark.parametrize("style", [GuideStyle.CORNERS, GuideStyle.FULL])
    def test_an_empty_cell_gets_no_marks(self, style: GuideStyle) -> None:
        px, geo = self.render_two(
            sheet_guide_style=style, sheet_guide_reach=GuideReach.PAPER
        )
        # the far corner of the last (empty) cell — nothing there to cut around
        x1, y1 = geo.cell_xy(1, 1)
        far = px[y1 : y1 + geo.cell_h, x1 : x1 + geo.cell_w]
        assert not holds(far, RED), style

    def test_the_one_real_card_is_still_marked(self) -> None:
        px, geo = self.render_two(sheet_guide_style=GuideStyle.CORNERS)
        x0, y0 = geo.cell_xy(0, 0)
        tx, ty = round(x0 + geo.bleed), round(y0 + geo.bleed)
        assert tuple(px[ty, tx - 10]) == RED

    def test_an_empty_page_draws_nothing_at_all(self) -> None:
        cfg = base()
        geo = geo_of(cfg)
        page = sheet.render_page([None] * geo.per_page, cfg, geo, is_back=False)
        assert not holds(np.asarray(page.convert("RGB")), RED)


class TestTheTwoSidesAreSeparate:
    def test_a_back_setting_left_unset_follows_the_fronts(self) -> None:
        """One sheet of paper, one set of guides, until you say otherwise — the shape
        `[print] back_profile` already has."""
        cfg = base(sheet_guides_back=True)
        front = sheet.guides_for(cfg, back=False)
        back = sheet.guides_for(cfg, back=True)
        assert front == back

    def test_each_one_can_differ(self) -> None:
        cfg = base(
            sheet_guides_back=True,
            sheet_back_guide_color=BACK,
            sheet_back_guide_style=GuideStyle.FULL,
            sheet_back_guide_reach=GuideReach.PAPER,
            sheet_back_guide_mm=9.0,
            sheet_back_guide_cross_mm=2.0,
            sheet_back_guide_width_mm=0.5,
            sheet_back_guide_placement=GuidePlacement.INSIDE,
        )
        front = sheet.guides_for(cfg, back=False)
        back = sheet.guides_for(cfg, back=True)
        assert front is not None
        assert back is not None
        assert (front.color, back.color) == (FRONT, BACK)
        assert (front.style, back.style) == (GuideStyle.CORNERS, GuideStyle.FULL)
        assert (front.reach, back.reach) == (GuideReach.FIXED, GuideReach.PAPER)
        assert back.length == 9.0
        assert back.cross == 2.0
        assert back.width == 0.5
        assert back.placement is GuidePlacement.INSIDE

    def test_the_second_colour_really_reaches_the_paper(self) -> None:
        """The reason to want a different one: with lines on both sides you hold the
        sheet to a light, and two colours are how you tell whose line is whose."""
        cfg = base(sheet_guides_back=True, sheet_back_guide_color=BACK)
        front, _ = render(cfg)
        back, _ = render(cfg, back=True)
        assert holds(front, RED)
        assert not holds(front, BLUE)
        assert holds(back, BLUE)
        assert not holds(back, RED)

    def test_none_on_the_backs_is_not_the_same_as_unset(self) -> None:
        """The trap the type introduced. `guide_style` has a real `none` member meaning
        "draw none", and an *unset* optional means "the same as the fronts" — two
        answers that a single string field could not tell apart."""
        cfg = base(sheet_guides_back=True)
        assert sheet.guides_for(cfg, back=True) is not None
        cfg.sheet_back_guide_style = GuideStyle.NONE
        assert sheet.guides_for(cfg, back=True) is None
        cfg.sheet_back_guide_style = Config.coerce("sheet_back_guide_style", "")
        assert cfg.sheet_back_guide_style is None
        assert sheet.guides_for(cfg, back=True) is not None

    def test_guides_off_beats_every_other_setting(self) -> None:
        cfg = base(sheet_guides=False, sheet_guides_back=True)
        assert sheet.guides_for(cfg, back=False) is None
        assert sheet.guides_for(cfg, back=True) is None

    def test_backs_are_off_by_default(self) -> None:
        """You cut from one side, so the default is one side's worth of ink."""
        assert sheet.guides_for(base(), back=True) is None
        assert sheet.guides_for(base(), back=False) is not None


class TestUnsetSurvivesTheConfigFile:
    """An optional setting has to be **writable back to unset**, and TOML has no way to
    spell that except by the key not being there. Setting one empty therefore *removes*
    the key — which is not what any other setting does, and the first version tried to
    write `None` into the document and died in tomlkit with a `ConvertError`. It is the
    same distinction the sheet builder's controls already draw between "clear this row"
    and "store a blank", one layer down.
    """

    def test_clearing_one_removes_the_key(self, tmp_path: Path) -> None:
        root = tmp_path
        (root / MARKER).write_text(
            '[library]\ngame = "pokemon"\n\n[sheet]\n'
            '# which colour the backs use\nback_guide_color = "#ff00ff"\n',
            encoding="utf-8",
        )
        (root / "cards").mkdir()
        assert Config.load(root).sheet_back_guide_color == "#ff00ff"
        out = CliRunner().invoke(
            cli,
            ["--root", str(root), "config", "set", "sheet.back_guide_color="],
            catch_exceptions=False,
        )
        assert out.exit_code == 0, out.output
        # said out loud, and in terms of what unset *does* rather than as a blank
        assert "same as the fronts" in out.output
        text = (root / MARKER).read_text(encoding="utf-8")
        assert "back_guide_color" not in text
        assert Config.load(root).sheet_back_guide_color is None
        # and the file is still a file `Config.load` agrees with
        assert Config.load(root).library_game == "pokemon"

    def test_a_written_value_still_round_trips(self, tmp_path: Path) -> None:
        root = tmp_path
        (root / MARKER).write_text('[library]\ngame = "pokemon"\n', encoding="utf-8")
        (root / "cards").mkdir()
        out = CliRunner().invoke(
            cli,
            [
                "--root",
                str(root),
                "config",
                "set",
                "sheet.back_guide_style=full",
                "sheet.back_guide_cross_mm=2.5",
            ],
            catch_exceptions=False,
        )
        assert out.exit_code == 0, out.output
        cfg = Config.load(root)
        assert cfg.sheet_back_guide_style is GuideStyle.FULL
        assert cfg.sheet_back_guide_cross_mm == 2.5

    def test_a_typo_is_still_refused(self, tmp_path: Path) -> None:
        """Optional does not mean unvalidated — the enum is still closed."""
        root = tmp_path
        (root / MARKER).write_text('[library]\ngame = "pokemon"\n', encoding="utf-8")
        (root / "cards").mkdir()
        out = CliRunner().invoke(
            cli, ["--root", str(root), "config", "set", "sheet.back_guide_style=nope"]
        )
        assert out.exit_code != 0
        assert "back_guide_style" not in (root / MARKER).read_text(encoding="utf-8")


class TestWhatTheRunReports:
    """A plan that described a different sheet than the renderer draws would be the one
    thing `sheet.plan` exists to prevent — so the report reads the same call."""

    def test_the_plan_names_both_sides(self) -> None:
        cfg = base(
            sheet_faces=Faces.DUPLEX,
            sheet_guides_back=True,
            sheet_back_guide_color=BACK,
        )
        run = sheet.plan([], cfg).json(cfg)
        assert run["guides"]["color"] == FRONT
        assert run["back_guides"]["color"] == BACK

    def test_a_fronts_only_run_reports_no_back_guides(self) -> None:
        """Not "off" — absent. Nothing is printed on a back that does not exist, and
        naming a correction that never happens is what the back-profile line already
        avoids."""
        cfg = base(sheet_faces=Faces.FRONTS, sheet_guides_back=True)
        assert sheet.plan([], cfg).json(cfg)["back_guides"] is None

    def test_a_side_that_draws_nothing_says_so(self) -> None:
        cfg = base(sheet_faces=Faces.DUPLEX)  # backs off by default
        assert sheet.plan([], cfg).json(cfg)["back_guides"] is None
