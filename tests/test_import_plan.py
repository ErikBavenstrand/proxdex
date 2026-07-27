"""The import plan: what a folder of loose files would become.

This is the one part of ``import`` that is pure and has two consumers — the CLI's
``--dry-run`` table and the web UI's wizard — which is the same reason
``test_fit_parity`` exists. A wrong answer here is not a crash: it is a preview
that promised something else, or a file quietly filed over a card's real scan.

What it must get right is filename reading (the id a name starts with, the stage
an Upscayl output is, the side an ``_f2`` suffix means), the difference between a
*guessed* id and a confirmed one, and the two ways a destination is already
taken — by the library, and by another file in the same run.
"""

from __future__ import annotations

from conftest import put_stage
from proxdex import imports
from proxdex.games import GameId
from proxdex.imports import Disposition, Item, OnExisting
from proxdex.library import FRONT, Card, Library, Stage

BACK = 1


def plan_names(
    lib: Library, *names: str, on_existing: OnExisting = OnExisting.OVERWRITE
) -> imports.Run:
    """Plan a run of plain filenames — the wizard's ordinary case."""
    return imports.plan(lib, [Item(name=n) for n in names], on_existing=on_existing)


class TestReadingAFilename:
    def test_the_id_a_name_starts_with(self) -> None:
        assert imports.guess_id("ex3-90") == "ex3-90"
        assert imports.guess_id("ex3-90_upscayl_4x") == "ex3-90"
        assert imports.guess_id("neo-136 (1)") == "neo-136"
        # MTG collector numbers can carry a letter
        assert imports.guess_id("bw11-1a") == "bw11-1a"

    def test_a_name_with_no_id_in_it(self) -> None:
        assert imports.guess_id("mystery-scan") is None
        assert imports.guess_id("IMG_4021") is None

    def test_upscayl_output_is_an_upscale(self) -> None:
        assert imports.guess_stage("ex3-90_upscayl_4x_realesrgan") is Stage.UPSCALED
        assert imports.guess_stage("ex3-90") is Stage.ORIGINAL

    def test_proxdex_own_files_round_trip(self, card: Card) -> None:
        """A folder of proxdex's own stage files must go back where it came from —
        including the side, which is the part a hand-written reader would lose."""
        for stage in Stage:
            for face in (FRONT, BACK):
                stem = card.stage_path(stage, face).stem
                assert imports.guess_id(stem) == card.id
                assert imports.guess_stage(stem) is stage
                assert imports.guess_face(stem) == face

    def test_a_renamed_stage_number_is_not_a_stage_file(self) -> None:
        """``ex3-90_9_upscaled`` is somebody's rename, not a stage file — the
        number and the label have to agree or the name says nothing."""
        assert imports.guess_stage("ex3-90_9_upscaled") is Stage.ORIGINAL


class TestWhatHappensToAFile:
    def test_a_new_stage_for_a_filed_card(self, library: Library, card: Card) -> None:
        run = plan_names(library, "ex3-90.png")
        (only,) = run.items
        assert only.disposition is Disposition.NEW
        assert only.id == "ex3-90"
        assert only.guessed_id
        assert only.dest == card.stage_path(Stage.ORIGINAL).relative_to(library.root)
        assert run.ready == (only,)

    def test_an_existing_stage_is_replaced_by_default(
        self, library: Library, card: Card
    ) -> None:
        put_stage(card, Stage.ORIGINAL)
        (only,) = plan_names(library, "ex3-90.png").items
        assert only.disposition is Disposition.REPLACE

    def test_or_kept_when_the_run_says_so(self, library: Library, card: Card) -> None:
        put_stage(card, Stage.ORIGINAL)
        run = plan_names(library, "ex3-90.png", on_existing=OnExisting.SKIP)
        (only,) = run.items
        assert only.disposition is Disposition.SKIP
        assert not run.ready
        assert run.skipped == (only,)

    def test_a_replacement_names_what_it_invalidates(
        self, library: Library, card: Card
    ) -> None:
        """Replacing an upstream stage removes every later one, and a plan that
        did not say so would be a preview of a smaller change than happens."""
        put_stage(card, Stage.ORIGINAL)
        put_stage(card, Stage.UPSCALED)
        put_stage(card, Stage.EDITED)
        (only,) = plan_names(library, "ex3-90.png").items
        assert only.discards == (Stage.UPSCALED, Stage.EDITED)
        assert plan_names(library, "ex3-90.png").discards == 2

    def test_a_later_stage_invalidates_nothing_earlier(
        self, library: Library, card: Card
    ) -> None:
        put_stage(card, Stage.ORIGINAL)
        (only,) = plan_names(library, "ex3-90_upscayl.png").items
        assert only.stage is Stage.UPSCALED
        assert only.discards == ()

    def test_a_file_that_is_not_an_image(self, library: Library, card: Card) -> None:
        """A folder has a `.DS_Store` and a notes.txt in it, and copying one over
        a card's scan is worse than refusing it."""
        (only,) = plan_names(library, f"{card.id}.txt").items
        assert only.disposition is Disposition.NOT_IMAGE
        assert only.disposition.blocked


class TestWhatBlocksAFile:
    def test_no_id_in_the_name(self, library: Library) -> None:
        (only,) = plan_names(library, "mystery-scan.png").items
        assert only.disposition is Disposition.UNMATCHED
        assert only.id is None

    def test_a_guessed_id_will_not_invent_a_card(self, library: Library) -> None:
        """A filename could be anything, so a card folder is never created from
        one — that needs the id to have been confirmed."""
        (only,) = plan_names(library, "ex3-90.png").items
        assert only.disposition is Disposition.MISSING
        assert "confirm the id" in only.reason

    def test_a_confirmed_id_creates_the_card(self, library: Library) -> None:
        run = imports.plan(library, [Item(name="scan.png", id="ex3-90")])
        (only,) = run.items
        assert only.disposition is Disposition.CREATE
        assert not only.guessed_id
        assert run.creates == ("ex3-90",)
        # where it lands is not knowable without the lookup the import will do
        assert only.dest is None

    def test_the_game_travels_with_a_created_card(self, library: Library) -> None:
        run = imports.plan(
            library, [Item(name="scan.png", id="neo-136", game=GameId.MTG)]
        )
        assert run.items[0].game is GameId.MTG

    def test_a_side_the_card_has_not_got(self, library: Library, card: Card) -> None:
        (only,) = plan_names(library, f"{card.id}_1_original_f2.png").items
        assert only.disposition is Disposition.NO_SIDE
        assert only.face == BACK
        assert "not 2" in only.reason

    def test_two_files_wanting_one_slot(self, library: Library, card: Card) -> None:
        """``art.png`` beside ``art (1).png``: the first keeps the slot and the
        second is named, rather than silently overwriting it."""
        run = plan_names(library, f"{card.id}.png", f"{card.id} (1).png")
        first, second = run.items
        assert first.disposition is Disposition.NEW
        assert second.disposition is Disposition.COLLIDE
        assert "file 1" in second.reason
        assert run.ready == (first,)

    def test_the_same_card_at_two_stages_is_not_a_collision(
        self, library: Library, card: Card
    ) -> None:
        run = plan_names(library, f"{card.id}.png", f"{card.id}_upscayl.png")
        assert [a.disposition for a in run.items] == [Disposition.NEW, Disposition.NEW]
        assert run.cards == (card.id,)


class TestTheRunAsAWhole:
    def test_counts_split_by_what_happens(self, library: Library, card: Card) -> None:
        put_stage(card, Stage.UPSCALED)
        run = plan_names(
            library,
            "ex3-90.png",  # new original
            "ex3-90_upscayl.png",  # keeps the upscale
            "mystery.png",  # no id
            "notes.txt",  # not an image
            on_existing=OnExisting.SKIP,
        )
        assert len(run.ready) == 1
        assert len(run.skipped) == 1
        assert len(run.blocked) == 2
        assert len(run.items) == 4

    def test_the_json_is_what_the_wizard_reads(
        self, library: Library, card: Card
    ) -> None:
        """The API hands this straight out, so the field names are part of the
        contract with `webui.html` — and the side is 1-based there, like `--face`."""
        put_stage(card, Stage.ORIGINAL)
        body = plan_names(library, "ex3-90_1_original.png").json()
        assert body["ready"] == 1
        assert body["on_existing"] == "overwrite"
        (item,) = body["items"]
        assert item["disposition"] == "replace"
        assert item["stage"] == "original"
        assert item["face"] == 1
        assert item["writes"] is True
        assert item["blocked"] is False
