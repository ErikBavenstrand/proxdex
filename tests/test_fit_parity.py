"""`solveFit` (webui.html) against `solve_fit` (cardbleed) — the same numbers.

The align panel draws a ghost of the trim the fit will produce, and the border
step then runs the fit in Python. Those are two implementations of one solver,
and a drift between them is invisible: the ghost simply lies about where the
card will land. So this runs the browser's copy in node over a table of cases
and asserts it agrees with the authority, edge for edge.

The JS is extracted from the page rather than copied here — a copy is a third
implementation, and it would be the one that stays right.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from proxdex.bleed import fit_plan
from proxdex.config import Config
from proxdex.frames import FrameGuide, GuideId
from proxdex.games import (
    CARD_H_MM,
    CARD_W_MM,
    OVERSIZED_H_MM,
    OVERSIZED_W_MM,
)

WEBUI = Path(__file__).resolve().parents[1] / "src" / "proxdex" / "webui.html"
EDGES = ("top", "right", "bottom", "left")

#: real guides, plus a lopsided one no era has — an asymmetric target is where a
#: transposed edge or a mis-split budget shows up
WOTC = (3.45 / CARD_H_MM, 3.15 / CARD_W_MM, 3.45 / CARD_H_MM, 3.15 / CARD_W_MM)
MTG = (0.045, 0.052, 0.045, 0.052)
LOPSIDED = (0.03, 0.07, 0.09, 0.04)

#: where the marks sit — as a scan really measures, and as a hand really drags
ORDINARY = (0.045, 0.052, 0.045, 0.052)
POKEMON = (0.041, 0.048, 0.043, 0.049)
THIN = (0.02, 0.02, 0.02, 0.02)
HEAVY = (0.09, 0.10, 0.09, 0.10)
SQUARE = (0.035, 0.035, 0.035, 0.035)
SKEW = (0.02, 0.08, 0.06, 0.03)
NOTHING = (0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class Case:
    """One fit: the scan's size, where its border sits, and what to aim at."""

    name: str
    w: int
    h: int
    marks: tuple[float, float, float, float]  # current border, image fractions
    guide: tuple[float, float, float, float]  # target border, card fractions
    stretch: bool = False


CASES = (
    Case("scryfall png, mtg target", 745, 1040, ORDINARY, MTG),
    Case("scryfall png, stretched", 745, 1040, ORDINARY, MTG, stretch=True),
    Case("pokemon scan, wotc target", 734, 1024, POKEMON, WOTC),
    Case("pokemon scan, stretched", 734, 1024, POKEMON, WOTC, stretch=True),
    Case("art too wide for the target", 900, 1040, THIN, MTG),
    Case("art too tall for the target", 600, 1100, THIN, MTG),
    Case("too wide, stretched", 900, 1040, THIN, MTG, stretch=True),
    Case("too tall, stretched", 600, 1100, THIN, MTG, stretch=True),
    Case("border already over target (shaves)", 745, 1040, HEAVY, MTG),
    Case("no border at all", 745, 1040, NOTHING, MTG),
    Case("lopsided target", 745, 1040, ORDINARY, LOPSIDED),
    Case("lopsided target, stretched", 745, 1040, ORDINARY, LOPSIDED, stretch=True),
    Case("lopsided marks", 745, 1040, SKEW, MTG),
    Case("oversized scan", 1050, 1500, SQUARE, WOTC),
    Case("tiny scan", 210, 293, ORDINARY, MTG),
    # A **zero target** — a full-bleed printing, or a game of your own whose cards
    # carry no border. Both stretch states, because they are genuinely different fits
    # there and `bleed.by_resize` reads this plan to decide whether anything is being
    # invented: stretched it must come out at the marked art's own size with the
    # extensions at nothing, unstretched it must extend to reach the aspect.
    Case("zero target, stretched", 600, 825, NOTHING, NOTHING, stretch=True),
    Case("zero target, not stretched", 600, 825, NOTHING, NOTHING),
    Case("zero target, marks inside it", 745, 1040, ORDINARY, NOTHING, stretch=True),
)


def guide_of(inset: tuple[float, float, float, float]) -> FrameGuide:
    return FrameGuide(
        id=GuideId.MTG_1993.value,
        name="test",
        game=None,
        inset=inset,
    )


def solve_fit_js() -> str:
    """The page's own `solveFit`, sliced out by brace matching.

    Reading the function out of the shipped HTML is the point: a pasted copy
    would pass this test forever while the page drifted.
    """
    src = WEBUI.read_text(encoding="utf-8")
    start = src.index("function solveFit(")
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start : j + 1]
    msg = "solveFit's braces never close — webui.html is unparseable"
    raise AssertionError(msg)


def run_js(
    cases: tuple[Case, ...], tmp_path: Path, card: tuple[float, float]
) -> list[dict[str, object] | None]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed — the UI half of the parity check")
    # the card size is passed in — the same trim the Python half is given. Hardcoding
    # it here once meant the two halves were handed different cards the moment the
    # trim changed, and the parity test failed on its own harness. It is a parameter
    # rather than the config because it is per card: an oversized printing is fitted
    # to 88.9×127mm, and `/api/frame` hands the page that size for those cards.
    payload = [
        {
            "m": {"w": c.w, "h": c.h, "card_w_mm": card[0], "card_h_mm": card[1]},
            "b": dict(zip("trbl", c.marks, strict=True)),
            "guide": {"inset": list(c.guide)},
            "stretch": c.stretch,
        }
        for c in cases
    ]
    script = tmp_path / "parity.js"
    script.write_text(
        solve_fit_js()
        + "\nconst cases = JSON.parse(process.argv[2]);\n"
        + "console.log(JSON.stringify(cases.map("
        + "c => solveFit(c.m, c.b, c.stretch, c.guide))));\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        [node, str(script), json.dumps(payload)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config()


@pytest.fixture(scope="module")
def trim(cfg: Config) -> tuple[float, float]:
    """What an ordinary card prints at — `sheet.trim_mm` for anything not oversized."""
    return (cfg.card_w_mm, cfg.card_h_mm)


@pytest.fixture(scope="module")
def js_results(
    tmp_path_factory: pytest.TempPathFactory, trim: tuple[float, float]
) -> list[dict[str, object]]:
    """Every case solved once, in node — the subprocess is the slow part."""
    out = run_js(CASES, tmp_path_factory.mktemp("js"), trim)
    assert all(r is not None for r in out), "solveFit refused a case it should solve"
    return [r for r in out if r is not None]


@pytest.mark.parametrize("index", range(len(CASES)), ids=[c.name for c in CASES])
def test_the_ui_and_cardbleed_agree(
    index: int, js_results: list[dict[str, object]], trim: tuple[float, float]
) -> None:
    case, js = CASES[index], js_results[index]
    plan = fit_plan(
        case.w, case.h, guide_of(case.guide), case.marks, trim, stretch=case.stretch
    )

    assert js["tw"] == round(plan.trim_w)
    assert js["th"] == round(plan.trim_h)
    js_borders = js["borders"]
    js_ext = js["ext"]
    assert isinstance(js_borders, dict)
    assert isinstance(js_ext, dict)
    for edge in EDGES:
        assert js_borders[edge] == pytest.approx(plan.borders[edge], rel=1e-12)
        assert js_ext[edge] == pytest.approx(plan.ext[edge], abs=1e-9)
    assert tuple(js["cropped"]) == plan.cropped  # pyright: ignore[reportArgumentType]


def test_stretch_hits_the_target_exactly(trim: tuple[float, float]) -> None:
    """What stretch is *for*: the resulting borders are the guide, not near it."""
    plan = fit_plan(
        745,
        1040,
        guide_of(MTG),
        (0.045, 0.052, 0.045, 0.052),
        trim,
        stretch=True,
    )
    got = tuple(plan.borders[e] for e in EDGES)
    assert got == pytest.approx(MTG, abs=1e-9)


def test_the_trim_is_the_card_aspect(trim: tuple[float, float]) -> None:
    """The border master is exactly the card's aspect by construction — which is why
    `sheet` must never stretch. Read from the config rather than written out, so the
    property is pinned and not one particular trim."""
    for case in CASES:
        plan = fit_plan(
            case.w,
            case.h,
            guide_of(case.guide),
            case.marks,
            trim,
            stretch=case.stretch,
        )
        assert plan.trim_w / plan.trim_h == pytest.approx(trim[0] / trim[1], rel=1e-12)


def test_an_oversized_card_is_fitted_to_its_own_size(tmp_path: Path) -> None:
    """Both halves take the trim they are handed, so a planar card is reshaped to
    88.9×127mm and not to the library's 63.5×88.9.

    This is the one that was wrong: `bleed` read the size out of `Config`, so an
    oversized master came out 63.5:88.9 and `sheet` — which does group it by its own
    trim — then `cover`-cropped 1.0% off each side to fit it into an 88.9×127 cell.
    The border landed at 2.07/2.12mm where `mtg-oversized` asks for 2.98/2.99, and
    nothing on screen said so, because the overlay is drawn in fractions too.
    """
    over = (OVERSIZED_W_MM, OVERSIZED_H_MM)
    guide = guide_of(
        (35 / 1490, 35 / 1040, 35 / 1490, 35 / 1040)  # mtg-oversized, as shipped
    )
    case = Case("oarc scan", 1040, 1490, SQUARE, guide.inset, stretch=True)
    plan = fit_plan(case.w, case.h, guide, case.marks, over, stretch=case.stretch)
    assert plan.trim_w / plan.trim_h == pytest.approx(over[0] / over[1], rel=1e-12)
    # and the borders it aims at are the spec's, in millimetres of *this* card
    got = tuple(plan.borders[e] for e in EDGES)
    assert got == pytest.approx(guide.inset, abs=1e-9)
    assert got[0] * over[1] == pytest.approx(2.98, abs=0.01)
    assert got[1] * over[0] == pytest.approx(2.99, abs=0.01)

    (js,) = run_js((case,), tmp_path, over)
    assert js is not None
    assert js["tw"] == round(plan.trim_w)
    assert js["th"] == round(plan.trim_h)


def test_marks_that_leave_no_inner_frame_are_refused(
    trim: tuple[float, float], tmp_path: Path
) -> None:
    """Both halves say no rather than inventing a fit: the UI returns null (and
    the panel says the marks leave no inner frame), Python raises."""
    impossible = Case("impossible", 745, 1040, (0.6, 0.1, 0.6, 0.1), MTG)
    assert run_js((impossible,), tmp_path, trim) == [None]
    with pytest.raises(Exception, match="no inner frame"):
        fit_plan(
            impossible.w,
            impossible.h,
            guide_of(impossible.guide),
            impossible.marks,
            trim,
            stretch=False,
        )
