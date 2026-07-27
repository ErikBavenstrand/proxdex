"""What a library already holds, versus what proxdex writes today.

`doctor` earns a test for the same reason `flatten` does: every difference it
looks for is one nobody can see on screen. A transparent corner, a grayscale
master and a bordered file that is not the trim aspect all *look* like finished
cards in the viewer, and all three change what comes off the printer. So the two
halves worth pinning are that it finds each of them — and, just as much, that it
does not rewrite a file that is already right, since a repair that touches
everything is a repair nobody can run on a real library.
"""

from __future__ import annotations

from typing import Any, cast

import numpy as np
import pytest
from PIL import Image

from conftest import bordered_card, save
from proxdex import doctor
from proxdex.config import Config
from proxdex.doctor import Ailment
from proxdex.library import Card, Library, Stage

BORDER = (140, 170, 180)
#: what the two providers actually send — nowhere near the 63:88 trim, which is
#: the whole reason the border step exists
SOURCE_W, SOURCE_H = 600, 825


@pytest.fixture
def cfg() -> Config:
    """The stock 63×88mm trim; nothing here reads anything else off disk."""
    return Config()


def die_cut(w: int = 120, h: int = 168, corner: int = 12) -> Image.Image:
    """A card whose corners are transparent and hold a colour on the card nowhere."""
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[..., :3] = BORDER
    arr[..., 3] = 255
    for ys in (slice(0, corner), slice(h - corner, h)):
        for xs in (slice(0, corner), slice(w - corner, w)):
            arr[ys, xs] = (255, 0, 255, 0)
    return Image.fromarray(arr, mode="RGBA")


def one(card: Card, cfg: Config) -> list[doctor.Finding]:
    return doctor.examine([card], cfg).findings


class TestWhatItFinds:
    def test_a_clean_library_says_what_it_checked(
        self, card: Card, cfg: Config
    ) -> None:
        """ "No findings" over nothing checked is not the same answer, so the count
        of images examined is part of the report rather than a log line."""
        save(bordered_card(w=63, h=88), card.stage_path(Stage.ORIGINAL))
        save(bordered_card(w=63, h=88), card.stage_path(Stage.BORDERED))
        report = doctor.examine([card], cfg)
        assert report.clean
        assert (report.cards, report.images) == (1, 2)

    def test_a_transparent_corner(self, card: Card, cfg: Config) -> None:
        die_cut().save(card.stage_path(Stage.UPSCALED))
        found = one(card, cfg)
        assert [f.ailment for f in found] == [Ailment.ALPHA]
        assert found[0].stage is Stage.UPSCALED
        assert found[0].repairable

    def test_a_palette_with_transparency_is_the_same_finding(
        self, card: Card, cfg: Config
    ) -> None:
        """The case that turned this up on a real library was mode ``P`` with a
        transparency index, not RGBA."""
        path = card.stage_path(Stage.ORIGINAL)
        die_cut().convert("P", palette=Image.Palette.ADAPTIVE).save(
            path, transparency=0
        )
        assert [f.ailment for f in one(card, cfg)] == [Ailment.ALPHA]

    def test_a_grayscale_master(self, card: Card, cfg: Config) -> None:
        """`import` copies bytes, so a scan filed before this check stayed mode L —
        and every tool downstream then converts it its own way."""
        Image.fromarray(bordered_card(w=63, h=88)).convert("L").save(
            card.stage_path(Stage.ORIGINAL)
        )
        found = one(card, cfg)
        assert [f.ailment for f in found] == [Ailment.MODE]
        assert found[0].detail == "mode L"

    def test_one_file_gets_one_finding(self, card: Card, cfg: Config) -> None:
        """RGBA is both transparent *and* not RGB. Both are the same rewrite, so
        reporting it twice would offer to repair the same file twice."""
        die_cut().save(card.stage_path(Stage.ORIGINAL))
        assert len(one(card, cfg)) == 1

    def test_an_unreadable_stage_file(self, card: Card, cfg: Config) -> None:
        """The file is in place, so the step reads as done and a sheet run would
        fail on this card rather than pass over it."""
        card.stage_path(Stage.EDITED).write_bytes(b"not a png")
        found = one(card, cfg)
        assert [f.ailment for f in found] == [Ailment.UNREADABLE]
        assert not found[0].repairable


class TestAspect:
    """The bordered master is exactly the configured trim by construction — that
    is what the border step *is*. A file that misses it gets `cover`-cropped at
    sheet time, losing border off two edges with nothing said."""

    def test_the_trim_aspect_passes(self, card: Card, cfg: Config) -> None:
        save(bordered_card(w=630, h=880), card.stage_path(Stage.BORDERED))
        assert not one(card, cfg)

    def test_rounding_to_whole_pixels_is_not_a_finding(
        self, card: Card, cfg: Config
    ) -> None:
        """cardbleed fits the aspect and then rounds, so 745 wide wants 1040.3 —
        a slack under a pixel, not a defect."""
        save(bordered_card(w=745, h=1040), card.stage_path(Stage.BORDERED))
        assert not one(card, cfg)

    def test_a_source_aspect_is_a_finding(self, card: Card, cfg: Config) -> None:
        save(bordered_card(w=SOURCE_W, h=SOURCE_H), card.stage_path(Stage.BORDERED))
        found = one(card, cfg)
        assert [f.ailment for f in found] == [Ailment.ASPECT]
        assert not found[0].repairable
        # the detail has to be checkable by hand: what it is, and what it wants
        assert f"{SOURCE_W}×{SOURCE_H}px" in found[0].detail
        assert f"{SOURCE_W}×838" in found[0].detail

    def test_only_the_bordered_stage_is_measured(self, card: Card, cfg: Config) -> None:
        """A card whose border step was skipped keeps its source's aspect all the
        way to the master, which is expected — and a bordered card's later stages
        inherit the one aspect, so measuring them repeats one cause three times."""
        for stage in (Stage.ORIGINAL, Stage.UPSCALED, Stage.EDITED):
            save(bordered_card(w=SOURCE_W, h=SOURCE_H), card.stage_path(stage))
        assert not one(card, cfg)


class TestRepair:
    def test_it_fills_from_the_cards_own_border(self, card: Card, cfg: Config) -> None:
        """The same call every filing point makes, so a repaired file is what a
        fresh run would have written — not a fixed colour over the corner."""
        path = card.stage_path(Stage.ORIGINAL)
        die_cut().save(path)
        (finding,) = one(card, cfg)
        assert doctor.repair(finding)
        with Image.open(path) as im:
            assert im.mode == "RGB"
            corner = np.asarray(im)[:8, :8].reshape(-1, 3)
        assert np.allclose(corner.mean(axis=0), BORDER, atol=2)
        assert not one(card, cfg)  # and the finding is gone

    def test_a_grayscale_master_becomes_rgb(self, card: Card, cfg: Config) -> None:
        path = card.stage_path(Stage.ORIGINAL)
        Image.fromarray(bordered_card(w=63, h=88)).convert("L").save(path)
        (finding,) = one(card, cfg)
        assert doctor.repair(finding)
        with Image.open(path) as im:
            assert im.mode == "RGB"

    def test_it_leaves_every_later_stage_alone(self, card: Card, cfg: Config) -> None:
        """A corner fill does not change the picture, so throwing away an upscale
        over one would destroy work to repair a corner."""
        die_cut().save(card.stage_path(Stage.ORIGINAL))
        save(bordered_card(w=63, h=88), later := card.stage_path(Stage.UPSCALED))
        before = later.read_bytes()
        (finding,) = one(card, cfg)
        doctor.repair(finding)
        assert later.read_bytes() == before

    def test_what_it_cannot_repair_it_does_not_touch(
        self, card: Card, cfg: Config
    ) -> None:
        """A wrong aspect needs the align marks, which is a decision rather than a
        repair — so `--fix` has to leave the file exactly as it is."""
        path = card.stage_path(Stage.BORDERED)
        save(bordered_card(w=SOURCE_W, h=SOURCE_H), path)
        before = path.read_bytes()
        (finding,) = one(card, cfg)
        assert not doctor.repair(finding)
        assert path.read_bytes() == before

    def test_it_leaves_no_temp_file_behind(self, card: Card, cfg: Config) -> None:
        """The rewrite goes through a temp file beside the original and is moved
        over it, so an interrupted repair can never leave a half-written master."""
        die_cut().save(card.stage_path(Stage.ORIGINAL))
        (finding,) = one(card, cfg)
        doctor.repair(finding)
        assert sorted(p.name for p in card.dir.iterdir()) == [
            card.stage_path(Stage.ORIGINAL).name
        ]


class TestEveryCheckIsExplained:
    def test_each_ailment_has_a_declared_check(self) -> None:
        """The CLI and the UI both print `check.why` for whatever came back, so an
        ailment with no entry would report a finding it cannot explain."""
        assert {c.id for c in doctor.CHECKS} == set(Ailment)

    def test_what_cannot_be_repaired_says_what_to_do(self) -> None:
        for check in doctor.CHECKS:
            assert check.repairable or check.hint, check.id


class TestTheReport:
    def test_it_splits_the_repairable_from_the_stuck(
        self, card: Card, cfg: Config
    ) -> None:
        die_cut().save(card.stage_path(Stage.ORIGINAL))
        save(bordered_card(w=SOURCE_W, h=SOURCE_H), card.stage_path(Stage.BORDERED))
        report = doctor.examine([card], cfg)
        assert [f.ailment for f in report.repairable] == [Ailment.ALPHA]
        assert [f.ailment for f in report.stuck] == [Ailment.ASPECT]
        assert report.counts() == {Ailment.ALPHA: 1, Ailment.ASPECT: 1}

    def test_the_json_the_ui_reads_carries_the_words_too(
        self, card: Card, cfg: Config
    ) -> None:
        """The UI spells no finding itself — the label, the why and the hint come
        from `doctor.CHECKS`, so both surfaces explain a defect the same way."""
        die_cut().save(card.stage_path(Stage.ORIGINAL))
        payload = doctor.json_report(doctor.examine([card], cfg))
        assert payload["repairable"] == 1
        checks = cast("list[dict[str, Any]]", payload["checks"])
        assert {c["id"] for c in checks} == {a.value for a in Ailment}
        assert all(c["label"] and c["why"] for c in checks)
        (row,) = cast("list[dict[str, Any]]", payload["findings"])
        assert row["ailment"] == Ailment.ALPHA.value
        assert row["face"] == 1  # sides are 1-based everywhere but the filenames


class TestFaces:
    def test_a_back_face_is_examined_too(self, card: Card, cfg: Config) -> None:
        """A back face is a different picture with its own files, so it can hold a
        defect the front does not."""
        (card.dir / ".faces").write_text("Front\nBack\n", encoding="utf-8")
        save(bordered_card(w=63, h=88), card.stage_path(Stage.ORIGINAL, 0))
        die_cut().save(card.stage_path(Stage.ORIGINAL, 1))
        (finding,) = one(card, cfg)
        assert finding.face == 1
        assert finding.path == card.stage_path(Stage.ORIGINAL, 1)


def test_it_walks_a_library(library: Library, cfg: Config) -> None:
    """`doctor` with no ids is the whole library, which is how anyone runs it."""
    folder = library.cards_dir / "ex3-skyridge" / "ex3-90_charizard"
    folder.mkdir(parents=True)
    die_cut().save(folder / "ex3-90_1_original.png")
    report = doctor.examine(library.cards(), cfg)
    assert [f.id for f in report.findings] == ["ex3-90"]
