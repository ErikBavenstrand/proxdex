"""The faces model: filenames, per-face state, rollup and which side prints.

These were checked by hand until now, and they are the part of the library model
where a mistake is expensive: a wrong suffix silently migrates nobody's library,
a shared state path silently overwrites the other side's picture, and a rollup
that counts one side reports a half-finished card as done.
"""

from __future__ import annotations

from conftest import pngs, put_stage
from proxdex.games import Layout
from proxdex.library import FRONT, Card, Stage, Status, Step, face_suffix

BACK = 1


class TestFilenames:
    def test_front_keeps_the_plain_names(self, card: Card) -> None:
        """Face 0 has no suffix, so no existing library needs migrating."""
        assert face_suffix(FRONT) == ""
        assert card.stage_path(Stage.BORDERED).name == "ex3-90_2_bordered.png"
        assert card.skip_marker(Stage.BORDERED).name == ".skip-bordered"

    def test_later_faces_are_one_based_in_the_name(self, card: Card) -> None:
        """Face 1 is the card's *second* side, so it is spelled `_f2`."""
        assert face_suffix(BACK) == "_f2"
        assert card.stage_path(Stage.EDITED, BACK).name == "ex3-90_4_edited_f2.png"
        assert card.skip_marker(Stage.EDITED, BACK).name == ".skip-edited_f2"

    def test_every_stage_and_face_has_its_own_path(self, card: Card) -> None:
        paths = {
            card.stage_path(stage, face) for stage in Stage for face in (FRONT, BACK)
        }
        assert len(paths) == len(Stage) * 2

    def test_a_step_names_the_stage_it_produces(self) -> None:
        assert Step.BORDER.stage is Stage.BORDERED
        assert Step.UPSCALE.stage is Stage.UPSCALED
        assert Step.GRADE.stage is Stage.EDITED


class TestPerFaceState:
    def test_state_defaults_to_the_front(self, card: Card) -> None:
        put_stage(card, Stage.BORDERED)
        assert card.has(Stage.BORDERED)
        assert card.status(Stage.BORDERED) is Status.DONE
        assert card.status(Stage.BORDERED, FRONT) is card.status(Stage.BORDERED)

    def test_the_two_sides_do_not_see_each_other(self, card: Card) -> None:
        put_stage(card, Stage.BORDERED, FRONT)
        card.mark_skip(Stage.BORDERED, BACK)
        assert card.status(Stage.BORDERED, FRONT) is Status.DONE
        assert card.status(Stage.BORDERED, BACK) is Status.SKIPPED
        assert card.status(Stage.UPSCALED, BACK) is Status.PENDING

    def test_skip_removes_the_output_and_reset_clears_both(self, card: Card) -> None:
        put_stage(card, Stage.EDITED, BACK)
        card.mark_skip(Stage.EDITED, BACK)
        assert not card.has(Stage.EDITED, BACK)
        assert card.status(Stage.EDITED, BACK) is Status.SKIPPED

        card.reset(Stage.EDITED, BACK)
        assert card.status(Stage.EDITED, BACK) is Status.PENDING
        assert not card.skip_marker(Stage.EDITED, BACK).exists()

    def test_an_output_beats_a_stale_skip_marker(self, card: Card) -> None:
        """Running a step clears the skip; if both somehow exist, done wins —
        there are pixels on disk."""
        card.mark_skip(Stage.UPSCALED)
        put_stage(card, Stage.UPSCALED)
        assert card.status(Stage.UPSCALED) is Status.DONE
        card.clear_skip(Stage.UPSCALED)
        assert card.status(Stage.UPSCALED) is Status.DONE

    def test_best_prefers_the_later_stage_of_that_face(self, card: Card) -> None:
        put_stage(card, Stage.ORIGINAL, BACK)
        put_stage(card, Stage.UPSCALED, FRONT)
        order = (Stage.EDITED, Stage.UPSCALED, Stage.BORDERED, Stage.ORIGINAL)
        assert card.best(*order) == card.stage_path(Stage.UPSCALED)
        assert card.best(*order, face=BACK) == card.stage_path(Stage.ORIGINAL, BACK)


class TestInvalidation:
    def test_only_later_stages_of_that_face_go(self, card: Card) -> None:
        for face in (FRONT, BACK):
            for stage in Stage:
                put_stage(card, stage, face)

        removed = card.invalidate_downstream(Stage.BORDERED, FRONT)

        assert removed == [Stage.UPSCALED, Stage.EDITED]
        assert pngs(card) == {
            card.stage_path(Stage.ORIGINAL).name,
            card.stage_path(Stage.BORDERED).name,
            *(card.stage_path(s, BACK).name for s in Stage),
        }

    def test_skip_markers_are_intent_and_stay(self, card: Card) -> None:
        put_stage(card, Stage.ORIGINAL)
        card.mark_skip(Stage.UPSCALED)
        assert card.invalidate_downstream(Stage.ORIGINAL) == []
        assert card.status(Stage.UPSCALED) is Status.SKIPPED

    def test_nothing_upstream_is_touched(self, card: Card) -> None:
        put_stage(card, Stage.ORIGINAL)
        put_stage(card, Stage.EDITED)
        assert card.invalidate_downstream(Stage.UPSCALED) == [Stage.EDITED]
        assert card.has(Stage.ORIGINAL)


class TestRollup:
    def test_done_only_when_every_side_is(self, card: Card) -> None:
        card.write_faces(["Delver of Secrets", "Insectile Aberration"])
        put_stage(card, Stage.EDITED, FRONT)
        assert card.rollup(Stage.EDITED) is Status.PENDING

        put_stage(card, Stage.EDITED, BACK)
        assert card.rollup(Stage.EDITED) is Status.DONE

    def test_settled_but_not_all_done_reads_as_skipped(self, card: Card) -> None:
        card.write_faces(["front", "back"])
        put_stage(card, Stage.EDITED, FRONT)
        card.mark_skip(Stage.EDITED, BACK)
        assert card.rollup(Stage.EDITED) is Status.SKIPPED

    def test_a_single_faced_card_rolls_up_to_its_one_side(self, card: Card) -> None:
        put_stage(card, Stage.UPSCALED)
        assert card.faces == (FRONT,)
        assert card.rollup(Stage.UPSCALED) is Status.DONE


class TestFaceNames:
    def test_one_face_records_nothing(self, card: Card) -> None:
        card.write_faces(["Charizard"])
        assert card.face_names() == ("",)
        assert card.faces == (FRONT,)

    def test_a_second_side_is_known_before_its_image_exists(self, card: Card) -> None:
        """`.faces` is written at fetch, so the UI can offer the side tab while
        the download of that side is still pending."""
        card.write_faces(["Delver of Secrets", "Insectile Aberration"])
        assert card.face_names() == ("Delver of Secrets", "Insectile Aberration")
        assert card.faces == (FRONT, BACK)
        assert not card.has(Stage.ORIGINAL, BACK)

    def test_a_hand_placed_second_image_still_counts(self, card: Card) -> None:
        put_stage(card, Stage.ORIGINAL, BACK)
        assert card.faces == (FRONT, BACK)
        assert card.face_names() == ("", "")

    def test_another_card_id_in_the_folder_is_not_a_face(self, card: Card) -> None:
        (card.dir / "ex3-91_1_original_f2.png").write_bytes(b"")
        assert card.faces == (FRONT,)


class TestWhichSidePrints:
    def test_a_single_faced_card_has_no_back_of_its_own(self, card: Card) -> None:
        assert card.front_face == FRONT
        assert card.back_face is None

    def test_flipping_swaps_front_and_back(self, card: Card) -> None:
        card.write_faces(["front", "back"])
        assert (card.front_face, card.back_face) == (FRONT, BACK)

        card.set_front_face(BACK)
        assert (card.front_face, card.back_face) == (BACK, FRONT)

        card.set_front_face(FRONT)
        assert (card.front_face, card.back_face) == (FRONT, BACK)
        assert not (card.dir / ".front").exists()

    def test_an_unusable_front_marker_falls_back(self, card: Card) -> None:
        card.write_faces(["front", "back"])
        for text in ("", "banana", "7", "-1"):
            (card.dir / ".front").write_text(text)
            assert card.front_face == FRONT


class TestLayoutFallback:
    def test_two_faces_on_disk_read_as_double(self, card: Card) -> None:
        card.write_faces(["front", "back"])
        assert card.layout is Layout.DOUBLE

    def test_the_recorded_layout_wins(self, card: Card) -> None:
        card.write_faces(["front", "back"])
        card.write_kind(Layout.MELD_PART)
        assert card.layout is Layout.MELD_PART

    def test_one_face_is_single(self, card: Card) -> None:
        assert card.layout is Layout.SINGLE
