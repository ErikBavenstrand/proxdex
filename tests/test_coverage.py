"""What has a measured frame spec, and what nobody has read yet.

This earns a place in a deliberately small suite because **the number is the whole
report**, and a wrong one looks finished. "21 of 174 sets covered" is a sentence a
person acts on — it decides whether there is an afternoon of measuring left — and
nothing on the screen or the page can contradict it. That is the same property the
*deleted* coverage report had, and it is why that one shipped for as long as it did:
it graded every MTG set against the specs, reported 1046 of them unmeasured, and was
wrong about every single one, because MTG's border follows the printing's frame
generation rather than its set.

So what is pinned here is the thing that report got wrong — that each game is asked
the question **its own border followed** (:func:`proxdex.frames.keyed`) — plus the
count that follows from it. The first version of this code said "12 of 12 frame
generations covered" for a game that has five, by adding MTG's four set exceptions in
with its generations: two kinds of thing summed into one confident total.

Everything here is over :func:`proxdex.inventory.assess`, the pure half, so a
provider is never touched. That is deliberate and not just convenience: a test that
needed a network provider could not pin an exact count at all, since the set list
grows with every release.
"""

from __future__ import annotations

from pathlib import Path

from proxdex import browse, frames, inventory, specs
from proxdex.frames import Generation, GuideId
from proxdex.games import GameId
from proxdex.library import Library
from proxdex.specs import Match, Via

POKEMON = GameId.POKEMON
MTG = GameId.MTG


def sets(game: GameId, *ids: str) -> list[browse.Expansion]:
    """A set list as the provider would hand one over, minus everything unread."""
    return [
        browse.Expansion(
            id=set_id,
            name=set_id.title(),
            game=game,
            group="Era" if game is POKEMON else "expansion",
            released=f"{2000 + n}-01-01",
        )
        for n, set_id in enumerate(ids)
    ]


def row(found: inventory.Coverage, subject: str) -> inventory.Row:
    match = [r for r in found.rows if r.subject == subject]
    assert match, f"no row for {subject!r} in {[r.subject for r in found.rows]}"
    return match[0]


def add(root: Path, spec_id: str, game: GameId = POKEMON) -> None:
    specs.save(root, specs.spec(spec_id, spec_id.title(), game, (4.0, 4.0, 4.0, 4.0)))


class TestEachGameIsAskedItsOwnQuestion:
    """The one thing the deleted report got wrong, and the reason this is not it.

    A row is a **set** for a game whose border ran for known runs of sets, and a
    **frame generation** for a game whose border changed with the printing. Asking
    the second one per set is not a rougher answer, it is a wrong one: every modern
    MTG set holds retro-frame cards beside modern ones, so no single verdict about
    the set is true of the cards in it.
    """

    def test_pokemon_is_keyed_by_set_and_mtg_by_generation(self) -> None:
        assert frames.keyed(POKEMON) is frames.Key.SET
        assert frames.keyed(MTG) is frames.Key.GENERATION

    def test_a_set_keyed_game_gets_one_row_per_set(self, library: Library) -> None:
        found = inventory.assess(
            POKEMON, sets(POKEMON, "base1", "sv1"), specs.load(library.root)
        )
        # newest first, and grouped, because it is `browse.gather` doing the ordering
        # — the same order the Browse screen lists sets in, not a second one
        assert [r.subject for r in found.rows] == ["sv1", "base1"]
        assert {r.key for r in found.rows} == {frames.Key.SET}

    def test_a_generation_keyed_game_gets_no_per_set_verdict(
        self, library: Library
    ) -> None:
        """`dft` is not in the report at all, and that is the point: nothing here may
        say a *set* of MTG is covered or uncovered, because neither is true of it."""
        found = inventory.assess(
            MTG, sets(MTG, "dft", "cmr", "lea"), specs.load(library.root)
        )
        listed = {r.subject for r in found.rows}
        assert "dft" not in listed
        assert "cmr" not in listed
        # `lea` is listed, because the *baseline* really keys it by set: it is an
        # exception to a generation, not a set-level scheme of its own
        assert "lea" in listed
        assert row(found, "lea").key is frames.Key.SET
        # and the sets it declined to judge are counted rather than dropped in silence
        assert found.per_printing == 2

    def test_the_note_says_which_question_was_asked(self, library: Library) -> None:
        """The reason travels with the number. A reader who does not know MTG resolves
        per printing reads five rows as five sets and concludes the opposite of the
        truth, so the sentence is on the report — one text, CLI and UI alike."""
        reg = specs.load(library.root)
        assert "frame generation" in inventory.assess(MTG, sets(MTG, "dft"), reg).note
        assert (
            "runs of sets" in inventory.assess(POKEMON, sets(POKEMON, "sv1"), reg).note
        )


class TestTheHeadlineCountsOneKindOfThing:
    """MTG is *5 of 5 frame generations*, never 12 of 12.

    The first version summed the generation rows and the set-exception rows, which is
    two units added together — and the total it produced was not merely imprecise, it
    named a quantity of frame generations that does not exist. The exception rows are
    still rows: listed, and still gaps if nothing answers for them.
    """

    def test_only_the_game_s_own_unit_is_counted(self, library: Library) -> None:
        found = inventory.assess(
            MTG, sets(MTG, "lea", "2ed", "dft"), specs.load(library.root)
        )
        assert found.key is frames.Key.GENERATION
        assert found.total == len(Generation)
        assert found.covered == len(Generation)
        assert found.complete
        # the exceptions are present, and outside the count
        assert len(found.rows) > found.total

    def test_a_set_keyed_game_counts_every_set(self, library: Library) -> None:
        found = inventory.assess(
            POKEMON, sets(POKEMON, "base1", "ecard1", "sv1"), specs.load(library.root)
        )
        assert (found.covered, found.total) == (2, 3)
        assert not found.complete
        assert [r.subject for r in found.gaps] == ["sv1"]


class TestWhatCountsAsCovered:
    """Covered means *something measured answers for the whole of it*."""

    def test_the_shipped_baseline_covers_the_sets_it_names(
        self, library: Library
    ) -> None:
        found = inventory.assess(
            POKEMON, sets(POKEMON, "base1"), specs.load(library.root)
        )
        answer = row(found, "base1")
        assert answer.covered
        assert [a.spec for a in answer.answers] == [GuideId.POKEMON_WOTC]
        assert answer.answers[0].via is Via.ERA

    def test_two_measured_answers_is_covered_and_not_a_fault(
        self, library: Library
    ) -> None:
        """The e-Card sets hold two frames whose tops differ by 6mm and nothing in the
        metadata says which a card is, so a person picks per card. That is a question
        with two good answers, which is a state — the same reason `frames check` does
        not report an ambiguous resolution as a fault."""
        found = inventory.assess(
            POKEMON, sets(POKEMON, "ecard1"), specs.load(library.root)
        )
        answer = row(found, "ecard1")
        assert answer.covered
        assert [a.spec for a in answer.answers] == [
            GuideId.POKEMON_ECARD,
            GuideId.POKEMON_ECARD_DEEP_TOP,
        ]

    def test_the_ex_era_is_covered_and_diamond_and_pearl_is_not(
        self, library: Library
    ) -> None:
        """Where the answers stop today. The ex series answers to `ex16` — `ex5` on by
        an inherited number rather than a reading, which is a decision recorded in
        `docs/measuring-frames.md` — and Diamond & Pearl (2007-05) onward resolves to
        nothing and refuses to border.

        **This report cannot tell you which of those two it is**, deliberately: it says
        whether a spec exists, never how it was arrived at. Provenance is prose in the
        repository, because a per-row "inherited" badge is a confidence grade growing
        back — the thing this area deleted twice.
        """
        found = inventory.assess(
            POKEMON,
            sets(POKEMON, "ex4", "ex5", "ex16", "tk2b", "dp1", "sv1"),
            specs.load(library.root),
        )
        for set_id in ("ex4", "ex5", "ex16", "tk2b"):
            assert row(found, set_id).covered, set_id
        assert not row(found, "dp1").covered
        assert not row(found, "sv1").covered

    def test_a_whole_set_rule_of_this_library_covers_its_set(
        self, library: Library
    ) -> None:
        add(library.root, "pokemon-sv")
        specs.assign(library.root, "pokemon-sv", POKEMON, "sv1", Match.SET)
        found = inventory.assess(
            POKEMON, sets(POKEMON, "sv1"), specs.load(library.root)
        )
        answer = row(found, "sv1")
        assert answer.covered
        assert answer.answers[0].via is Via.SET_DEFAULT
        assert answer.answers[0].rule

    def test_a_rule_that_claims_only_some_cards_does_not_cover_the_set(
        self, library: Library
    ) -> None:
        """A rule on a number range or a rarity is real coverage of *those* cards and
        of nothing else, so counting it would report a set as answered while the
        ordinary cards in it still resolve to nothing and refuse to be bordered. A
        number that looks finished is the failure this whole area guards against;
        `frames preview` is where such a rule is judged, card by card."""
        add(library.root, "pokemon-secret")
        specs.assign(
            library.root, "pokemon-secret", POKEMON, "sv1", Match.NUMBERS, "188-216"
        )
        found = inventory.assess(
            POKEMON, sets(POKEMON, "sv1"), specs.load(library.root)
        )
        assert not row(found, "sv1").covered

    def test_a_rule_naming_a_spec_that_is_gone_is_not_coverage(
        self, library: Library
    ) -> None:
        """The same broken reference `frames check` reports as `Fault.MISSING`. The fit
        falls through it, so coverage must too — otherwise a deleted spec leaves a set
        looking answered by a rule that resolves to nothing."""
        add(library.root, "pokemon-sv")
        specs.assign(library.root, "pokemon-sv", POKEMON, "sv1", Match.SET)
        (library.root / "frames" / "pokemon-sv.json").unlink()
        found = inventory.assess(
            POKEMON, sets(POKEMON, "sv1"), specs.load(library.root)
        )
        assert not row(found, "sv1").covered

    def test_a_game_wide_treatment_rule_is_not_coverage_either(
        self, library: Library
    ) -> None:
        """A game-wide rule is how a frame *treatment* is said — `extendedart` runs the
        art to the card edges in every set that ever printed one. It still claims only
        the cards carrying that treatment, so it answers for those and not for the
        generation, which keeps the baseline as the generation's only answer."""
        add(library.root, "mtg-mine", MTG)
        specs.assign(library.root, "mtg-mine", MTG, "", Match.FULL_ART)
        found = inventory.assess(MTG, sets(MTG, "dft"), specs.load(library.root))
        answers = row(found, str(Generation.F2015)).answers
        assert [a.spec for a in answers] == [GuideId.MTG_M15]


class TestGapsYouAlreadyOwn:
    """A gap matters more when there are cards sitting in it."""

    def test_held_cards_in_an_uncovered_set_are_counted(self, library: Library) -> None:
        found = inventory.assess(
            POKEMON,
            sets(POKEMON, "base1", "sv1"),
            specs.load(library.root),
            {"base1": 4, "sv1": 3},
        )
        assert row(found, "sv1").owned == 3
        # only the gaps: cards in a covered set border fine and are not a warning
        assert found.owned_gaps == 3

    def test_nothing_held_is_not_a_warning(self, library: Library) -> None:
        found = inventory.assess(
            POKEMON, sets(POKEMON, "sv1"), specs.load(library.root)
        )
        assert found.gaps
        assert found.owned_gaps == 0
