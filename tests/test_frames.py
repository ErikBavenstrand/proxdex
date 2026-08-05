"""Which frame spec a card resolves to, and why.

This earns a place in a deliberately small suite for the same reason
``test_import_plan.py`` does: one pure function with several consumers (the border
step, ``frames check``, ``frames preview``, ``/api/frame``, the align ghost), and a
wrong answer that **nothing shows**. A card fitted to the wrong border widths looks
exactly like one fitted to the right ones until two of them are cut and laid side
by side, and by then the master has been upscaled and graded on top.

So what is pinned here is the order — all seven ways a spec can be chosen — plus
the two ways the whole thing can be *asked a question it cannot answer*: a rule
that needs traits a card does not have, and an id that no longer names a spec.
Both must be reported rather than rounded down to a plausible default.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from proxdex import frames, sources, specs
from proxdex.errors import ProxdexError
from proxdex.frames import GuideId
from proxdex.games import GameId
from proxdex.library import Card, Library, Stage
from proxdex.specs import Match, Via

POKEMON = GameId.POKEMON
MTG = GameId.MTG


def spec_of(found: specs.Resolution) -> str:
    """The resolved spec's id, asserting there is one.

    `Resolution.spec` is optional now — a printing nobody measured resolves to no spec
    at all — so a test that expects one has to say so rather than dereference blindly.
    """
    assert found.spec is not None, f"expected a spec, got none (via {found.via})"
    return found.spec.id


def add(root: Path, spec_id: str, game: GameId = POKEMON) -> None:
    """A library spec whose numbers are distinguishable from every shipped one."""
    specs.save(
        root,
        specs.spec(spec_id, spec_id.title(), game, (4.0, 4.0, 4.0, 4.0)),
    )


class TestRegistry:
    """The shipped specs, plus whatever a library has added on top of them."""

    def test_a_fresh_library_has_exactly_the_shipped_specs(
        self, library: Library
    ) -> None:
        reg = specs.load(library.root)
        assert sorted(reg.specs) == sorted(frames.SHIPPED)
        assert all(frames.is_shipped(s) for s in reg.specs)
        assert reg.rules == ()

    def test_a_stored_spec_joins_them(self, library: Library) -> None:
        add(library.root, "pokemon-swsh")
        spec = specs.load(library.root).get("pokemon-swsh")
        assert spec is not None
        assert not frames.is_shipped(spec.id)
        # 4mm of a real card, back out of the fractions it was stored as
        assert [round(v, 2) for v in spec.mm()] == [4.0, 4.0, 4.0, 4.0]

    def test_a_stored_spec_may_correct_a_shipped_one(self, library: Library) -> None:
        """Half the reason this exists is fixing a number we shipped wrong — and for
        the MTG specs it is the *expected* path, since what ships is provisional."""
        specs.save(
            library.root,
            specs.spec(GuideId.MTG_1993.value, "", MTG, (2.5, 2.5, 2.5, 2.5)),
        )
        spec = specs.load(library.root).get(GuideId.MTG_1993)
        assert spec is not None
        assert round(spec.mm()[0], 2) == 2.5
        # the file gave no name, so the shipped one is kept rather than showing an id
        assert spec.name == frames.SHIPPED[GuideId.MTG_1993.value].name

    def test_millimetres_are_of_the_real_card_not_the_trim(self) -> None:
        """One card size for both games, and it is the **card** rather than the trim.

        A Magic card and a Pokémon card are the same 2.5×3.5in stock, so there is no
        per-spec reference size to record — but which size it is still matters: proxdex
        *trims* to 63×88, and taking a caliper reading against that instead of the real
        63.5×88.9 is a 0.8% error in every border, on every card. So the constant is
        the card, and a measurement round-trips through it exactly.
        """
        assert frames.CARD_MM == (63.5, 88.9)
        made = specs.spec("x", "", MTG, (2.4, 2.4, 2.4, 2.4))
        assert [round(v, 3) for v in made.mm()] == [2.4, 2.4, 2.4, 2.4]
        # against the trim it would read wider than it is — the error being avoided
        assert made.mm(63.0, 88.0)[1] < 2.4

    def test_borderless_is_reserved(self, library: Library) -> None:
        """Code *returns* this id for a frameless printing, so it has to exist and
        it has to mean what proxdex means by it."""
        with pytest.raises(ProxdexError, match="reserved"):
            specs.save(
                library.root,
                specs.spec(GuideId.BORDERLESS.value, "", None, (1, 1, 1, 1)),
            )
        with pytest.raises(ProxdexError, match="reserved"):
            specs.delete(library.root, GuideId.BORDERLESS.value)

    def test_an_unreadable_spec_file_is_named_not_swallowed(
        self, library: Library
    ) -> None:
        folder = specs.specs_dir(library.root)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "junk.json").write_text("{not json", encoding="utf-8")
        reg = specs.load(library.root)
        assert reg.broken == ("junk.json",)
        assert "junk" not in reg.specs

    def test_a_spec_is_its_numbers_and_nothing_else(self) -> None:
        """There is deliberately no confidence level. Three of them existed once,
        and the middle one — "read off the publisher's scans" — graded a reading
        that inherits the scan's crop as trustworthy. A crop that trims 0.3mm inside
        the cut edge shrinks every border read from it by 0.3mm, and no sample size
        and no agreement between cards detects that, because it is systematic. So
        nothing on a spec ranks its numbers, and nothing on it explains them either:
        where a shipped number came from is prose in the repository, a card per row in
        `docs/measuring-frames.md`, which no screen can render as a verdict.

        `oversized` is the one flag that stays, and it is a different kind of thing: not
        a claim about the numbers but about **which card they are fractions of**, of
        which proxdex knows exactly two. It replaced a field holding an arbitrary
        millimetre pair, which was only ever answering this same question.
        """
        made = specs.spec("mtg-x", "", MTG, (3, 3, 3, 3))
        assert {f.name for f in fields(made)} == {
            "id",
            "name",
            "game",
            "inset",
            "oversized",
        }
        for gone in ("confidence", "origin", "note", "measured", "ref_mm"):
            assert not hasattr(made, gone), gone

    def test_an_oversized_spec_stores_a_different_fraction(self) -> None:
        """The same millimetres on a bigger card are a smaller fraction — which is the
        whole reason the flag exists, and it round-trips through a stored file."""
        big = specs.spec("mtg-big", "", MTG, (3.0, 3.0, 3.0, 3.0), oversized=True)
        small = specs.spec("mtg-small", "", MTG, (3.0, 3.0, 3.0, 3.0))
        assert big.inset[1] < small.inset[1]
        assert big.oversized
        # and each reads back as the millimetres that went in, of its own card
        assert [round(v, 2) for v in big.mm()] == [3.0, 3.0, 3.0, 3.0]
        assert [round(v, 2) for v in small.mm()] == [3.0, 3.0, 3.0, 3.0]
        assert frames.from_json(big.json()).oversized


class TestRules:
    """What a selector catches — and what it refuses to guess about."""

    def test_number_ranges_catch_the_tail_they_name(self) -> None:
        rule = specs.Rule("r1", POKEMON, "swsh4", Match.NUMBERS, "188-216", "x")
        assert rule.selects("swsh4-188", None)
        assert rule.selects("swsh4-200", None)
        assert rule.selects("swsh4-216", None)
        assert not rule.selects("swsh4-187", None)
        assert not rule.selects("swsh4-217", None)

    def test_a_range_never_crosses_a_number_prefix(self) -> None:
        """`TG1-TG30` and `1-30` are different cards in the same set."""
        tg = specs.Rule("r1", POKEMON, "swsh12", Match.NUMBERS, "TG1-TG30", "x")
        plain = specs.Rule("r2", POKEMON, "swsh12", Match.NUMBERS, "1-30", "x")
        assert tg.selects("swsh12-TG12", None)
        assert not tg.selects("swsh12-12", None)
        assert plain.selects("swsh12-12", None)
        assert not plain.selects("swsh12-TG12", None)

    def test_ranges_and_ids_need_no_metadata_at_all(self) -> None:
        """The offline half of the design: a library filed years ago still resolves."""
        assert not Match.NUMBERS.needs_traits
        assert not Match.IDS.needs_traits
        assert Match.RARITY.needs_traits
        assert Match.FRAME.needs_traits

    def test_an_id_list_is_exact(self) -> None:
        rule = specs.Rule("r1", POKEMON, "swsh4", Match.IDS, "swsh4-188,swsh4-190", "x")
        assert rule.selects("swsh4-188", None)
        assert not rule.selects("swsh4-189", None)

    def test_a_trait_rule_with_no_traits_is_undecided_not_false(self) -> None:
        """The single most important ``None`` in this module: "I don't know" is not
        "no". Rounding it down would border a secret rare as an ordinary card and
        say nothing."""
        rule = specs.Rule("r1", POKEMON, "swsh4", Match.RARITY, "Rare Secret", "x")
        assert rule.selects("swsh4-190", None) is None
        assert rule.selects("swsh4-190", {"rarity": "Rare Secret"}) is True
        assert rule.selects("swsh4-190", {"rarity": "Common"}) is False
        # recorded, but empty — the provider said nothing, which is still unknown
        assert rule.selects("swsh4-190", {"rarity": ""}) is None

    def test_subtype_and_frame_match_any_of_a_list(self) -> None:
        sub = specs.Rule("r1", MTG, "neo", Match.SUBTYPE, "human,wizard", "x")
        assert sub.selects("neo-1", {"subtypes": "Creature,Human"}) is True
        assert sub.selects("neo-1", {"subtypes": "Goblin"}) is False
        gen = specs.Rule("r2", MTG, "dmr", Match.FRAME, "1997", "x")
        assert gen.selects("dmr-1", {"frame": "1997"}) is True
        assert gen.selects("dmr-1", {"frame": "2015"}) is False


class TestAssign:
    """Storing rules, and the two shapes a rule is refused in."""

    def test_a_rule_is_stored_and_read_back(self, library: Library) -> None:
        add(library.root, "pokemon-swsh")
        rule = specs.assign(
            library.root, "pokemon-swsh", POKEMON, "SWSH4", Match.SET, ""
        )
        assert rule.id == "r1"
        reg = specs.load(library.root)
        assert [r.id for r in reg.rules] == ["r1"]
        # set codes are matched case-insensitively, and stored lowercase
        assert reg.rules[0].set_id == "swsh4"
        assert reg.for_set(POKEMON, "swsh4") == list(reg.rules)
        assert reg.for_set(MTG, "swsh4") == []

    def test_rule_ids_never_reuse_a_number(self, library: Library) -> None:
        """A note about ``r2`` must not come to mean a different rule later."""
        add(library.root, "pokemon-swsh")
        specs.assign(library.root, "pokemon-swsh", POKEMON, "a", Match.SET)
        specs.assign(library.root, "pokemon-swsh", POKEMON, "b", Match.SET)
        specs.unassign(library.root, "r2")
        third = specs.assign(library.root, "pokemon-swsh", POKEMON, "c", Match.SET)
        assert third.id == "r3"

    def test_an_exception_is_tried_before_the_set_default(
        self, library: Library
    ) -> None:
        """Order is not the user's problem: a default added later still goes last."""
        add(library.root, "pokemon-swsh")
        add(library.root, "pokemon-secret")
        specs.assign(
            library.root, "pokemon-secret", POKEMON, "swsh4", Match.NUMBERS, "188-216"
        )
        specs.assign(library.root, "pokemon-swsh", POKEMON, "swsh4", Match.SET)
        reg = specs.load(library.root)
        assert [r.match for r in reg.rules] == [Match.NUMBERS, Match.SET]

    def test_a_second_whole_set_rule_is_kept(self, library: Library) -> None:
        """It used to be *deleted* as unreachable. It is reachable now — by being
        picked — so both stand and `resolve` offers the one it did not use. See
        `TestMoreThanOneBorderCanApply`."""
        add(library.root, "pokemon-swsh")
        add(library.root, "pokemon-other")
        specs.assign(library.root, "pokemon-swsh", POKEMON, "swsh4", Match.SET)
        specs.assign(library.root, "pokemon-other", POKEMON, "swsh4", Match.SET)
        reg = specs.load(library.root)
        assert [r.spec for r in reg.rules] == ["pokemon-swsh", "pokemon-other"]

    def test_a_rule_cannot_name_a_spec_that_does_not_exist(
        self, library: Library
    ) -> None:
        with pytest.raises(ProxdexError, match="no frame spec"):
            specs.assign(library.root, "nope", POKEMON, "swsh4", Match.SET)

    def test_a_rule_cannot_cross_games(self, library: Library) -> None:
        with pytest.raises(ProxdexError, match="Magic"):
            specs.assign(
                library.root, GuideId.POKEMON_WOTC.value, MTG, "neo", Match.SET
            )

    def test_a_number_range_is_checked_when_it_is_typed(self, library: Library) -> None:
        add(library.root, "pokemon-swsh")
        with pytest.raises(ProxdexError, match="collector-number range"):
            specs.assign(
                library.root, "pokemon-swsh", POKEMON, "swsh4", Match.NUMBERS, "late"
            )


class TestDelete:
    """A spec nothing points at can go; one something points at cannot."""

    def test_a_spec_in_use_by_a_rule_is_refused(self, library: Library) -> None:
        add(library.root, "pokemon-swsh")
        specs.assign(library.root, "pokemon-swsh", POKEMON, "swsh4", Match.SET)
        with pytest.raises(ProxdexError, match="r1"):
            specs.delete(library.root, "pokemon-swsh")

    def test_a_pinned_spec_is_refused_and_names_the_cards(
        self, library: Library
    ) -> None:
        add(library.root, "pokemon-swsh")
        with pytest.raises(ProxdexError, match="swsh4-190"):
            specs.delete(library.root, "pokemon-swsh", pinned=["swsh4-190"])

    def test_an_unused_spec_goes(self, library: Library) -> None:
        add(library.root, "pokemon-swsh")
        specs.delete(library.root, "pokemon-swsh")
        assert "pokemon-swsh" not in specs.load(library.root).specs

    def test_a_shipped_spec_says_there_is_nothing_stored_to_remove(
        self, library: Library
    ) -> None:
        with pytest.raises(ProxdexError, match="shipped"):
            specs.delete(library.root, GuideId.POKEMON_WOTC.value)


class TestResolve:
    """The order, top to bottom. Every case here is a different picture on paper."""

    def test_a_printing_nobody_measured_resolves_to_no_spec(
        self, library: Library
    ) -> None:
        """The state that replaced a per-game fallback spec. A modern Pokémon set is
        past the WOTC era and nothing measured describes it, so there is **nothing**
        to fit against — where a fallback used to quietly hand over the vintage
        numbers, which is a different border and no warning."""
        found = specs.resolve(specs.load(library.root), "swsh4-1", "swsh4", POKEMON)
        assert found.via is Via.NONE
        assert found.spec is None
        assert not found.have
        assert not found.sure
        assert "no frame spec measured" in found.note

    def test_a_shipped_era_rule_answers_for_the_sets_it_covers(
        self, library: Library
    ) -> None:
        found = specs.resolve(specs.load(library.root), "base1-4", "base1", POKEMON)
        assert found.via is Via.ERA
        assert spec_of(found) == GuideId.POKEMON_WOTC.value

    def test_mtg_reads_the_printings_frame_not_the_set(self, library: Library) -> None:
        """A set code cannot answer which border a card has: a modern set holds
        retro-frame cards beside modern ones (`dmr-354` is frame 1997 inside a frame
        2015 set). So the baseline reads the *printing's* frame, and two cards of one
        set get two different specs — which is the whole reason this is not keyed on
        the set id. Confirmed on real cards: `sld-1664` is a 1997-frame Sol Ring in a
        Secret Lair and measures 40/36px, against `msc-211`'s 30/30."""
        reg = specs.load(library.root)
        old = specs.resolve(reg, "dmr-1", "dmr", MTG, traits={"frame": "1997"})
        modern = specs.resolve(reg, "dmr-2", "dmr", MTG, traits={"frame": "2015"})
        assert (spec_of(old), old.via) == (GuideId.MTG_1997.value, Via.ERA)
        assert (spec_of(modern), modern.via) == (GuideId.MTG_M15.value, Via.ERA)

    def test_every_frame_scryfall_documents_is_covered(self) -> None:
        """Scryfall documents exactly five `frame` values, and this is the list.

        If a sixth generation ships, this is what says so — rather than every card
        of it silently taking the fallback, which is a different border and no
        warning. The five are quoted from Scryfall's own docs:
        1993 (Alpha), 1997 (Mirage), 2003 (8th Edition), 2015 (Magic 2015), future.

        All five answer, and `1993` does so from its **band 2** entry — the ordinary
        border shared by 18 of its sets — with Alpha/Beta and Unlimited/Revised keyed by
        set on top of it (see `TestTheNineteenNinetyThreeSplit`). A sixth generation
        would resolve to nothing rather than take a stand-in, and this says so.
        """
        documented = {"1993", "1997", "2003", "2015", "future"}
        # the enum *is* the list, so a sixth value cannot arrive without an edit here
        assert {g.value for g in frames.Generation} == documented
        reg = specs.load(Path("/nonexistent"))
        for g in documented:
            found = specs.resolve(reg, "x-1", "x", MTG, traits={frames.FRAME_TRAIT: g})
            assert found.have, g
        # every generation is claimed by a generation entry
        covered = {g for e in frames.BASELINE[MTG] for g in e.frames}
        assert covered == set(frames.Generation)

    def test_a_generation_nobody_documents_is_not_an_answer(self) -> None:
        """The trait is written out of untyped provider JSON, so reading it is a
        coercion at the boundary: a value outside the closed set is **no answer**
        rather than a traceback in the middle of a card walk, and rather than a
        stringly-typed comparison falling through to a plausible spec."""
        assert frames.parse_generation("2015") is frames.Generation.F2015
        assert frames.parse_generation(" 2015 ") is frames.Generation.F2015
        for junk in ("2016", "", None, "retro", [], 0):
            assert frames.parse_generation(junk) is None, junk
        reg = specs.load(Path("/nonexistent"))
        found = specs.resolve(reg, "x-1", "x", MTG, traits={"frame": "2016"})
        assert found.via is Via.NONE
        assert found.spec is None

    def test_future_shares_the_2003_spec_because_it_measured_the_same(self) -> None:
        """Two generations, one spec — and that is a *measurement*, not an assumption:
        `c13-259` (2003) and `mb2-233` (future) both read 35px on every edge. It is
        recorded as an alias rather than a duplicated spec so there is one number to
        correct, and splitting them later is one new entry."""
        reg = specs.load(Path("/nonexistent"))
        pair = [
            specs.resolve(reg, "x-1", "x", MTG, traits={frames.FRAME_TRAIT: gen})
            for gen in ("2003", "future")
        ]
        assert {spec_of(f) for f in pair} == {GuideId.MTG_2003.value}

    def test_a_generations_spec_is_the_number_that_was_measured(self) -> None:
        """Each MTG spec is stored as the exact pixel fractions of the image it was
        read off, so it must read back as the millimetres in the log — a spec quietly
        rounded through millimetres is a spec nobody can check against the card."""
        expected = {  # spec id -> (top mm, sides mm) of a 63.5x88.9mm card
            GuideId.MTG_1993.value: (2.74, 2.47),
            GuideId.MTG_1993_ALPHA.value: (2.74, 1.96),
            GuideId.MTG_1993_UNLIMITED.value: (3.63, 2.98),
            GuideId.MTG_1997.value: (3.42, 3.07),
            GuideId.MTG_2003.value: (2.99, 2.98),
            GuideId.MTG_M15.value: (2.56, 2.56),
            GuideId.MTG_YELLOW_BAND.value: (3.76, 4.27),
        }
        for spec_id, (top, sides) in expected.items():
            got = frames.SHIPPED[spec_id].mm(63.5, 88.9)
            assert (round(got[0], 2), round(got[1], 2)) == (top, sides), spec_id
            # top and bottom, and left and right, are one number each by design: the
            # cutting error is cancelled rather than recorded
            assert round(got[0], 4) == round(got[2], 4), spec_id
            assert round(got[1], 4) == round(got[3], 4), spec_id

    def test_every_generation_that_maps_names_a_spec_that_exists(self) -> None:
        """A mapping to an id nothing defines would resolve to the id and then fail
        at the fit, which is a worse failure than resolving to nothing."""
        ids = {e.spec for game in frames.BASELINE.values() for e in game}
        assert ids
        assert all(i in frames.SHIPPED for i in ids)

    def test_a_baseline_entry_says_which_fact_about_a_card_decides(self) -> None:
        """One table, two typed keys, and an entry uses exactly one of them.

        Which key a *game* needs is not fixed, and that is the point of unifying the
        two tables that used to be here: Pokémon's yellow border ran for a list of
        **sets**, MTG's changed with the printing's **frame** — and then MTG turned out
        to need sets *as well*, because the 1993 frame is three geometries. An entry
        with neither key would claim nothing; one with both would answer two questions
        with one number.
        """
        for game, entries in frames.BASELINE.items():
            assert entries, game
            for entry in entries:
                assert bool(entry.sets) != bool(entry.frames), (game, entry.spec)
        assert all(e.sets and not e.frames for e in frames.BASELINE[POKEMON])
        # MTG uses both kinds, which is exactly why they are one table
        mtg = frames.BASELINE[MTG]
        assert any(e.sets for e in mtg)
        assert any(e.frames for e in mtg)

    def test_a_set_era_answers_before_a_frame_generation_does(self) -> None:
        """`baseline` runs two passes, sets first — so which kind of key wins is a
        property of the function and not of the order somebody listed the table in.
        `lea` reports `frame: 1993` *and* has a set entry, so it is the case that proves
        the order: it must come back Alpha's own band and not the generation's ordinary
        one, which is a full millimetre wider on the sides.
        """
        assert frames.baseline("lea", MTG, {frames.FRAME_TRAIT: "1993"}) == (
            GuideId.MTG_1993_ALPHA
        )
        # and a set with no entry of its own falls through to the generation
        assert frames.baseline("ice", MTG, {frames.FRAME_TRAIT: "1993"}) == (
            GuideId.MTG_1993
        )

    def test_a_border_colour_rule_catches_a_decorative_band(
        self, library: Library
    ) -> None:
        """Colour is not geometry — white, silver and gold measure at their
        generation's width — but a *band* is: Aetherdrift's yellow box toppers measure
        4.26mm on the sides against an ordinary 2.56. proxdex now ships that one
        (`sources.mtg_frame` reads it off the printing), so what this pins is the
        mechanism: a rule on a border colour beats the generation baseline for the
        cards it catches and leaves every other card of the set alone.
        """
        specs.save(
            library.root,
            specs.spec("mtg-band-local", "A band", MTG, (4.2, 4.7, 4.2, 4.7)),
        )
        specs.assign(library.root, "mtg-band-local", MTG, "dft", Match.BORDER, "yellow")
        reg = specs.load(library.root)
        topper = specs.resolve(
            reg, "dft-511", "dft", MTG, traits={"frame": "2015", "border": "yellow"}
        )
        plain = specs.resolve(
            reg, "dft-1", "dft", MTG, traits={"frame": "2015", "border": "black"}
        )
        assert (spec_of(topper), topper.via) == ("mtg-band-local", Via.RULE)
        # its ordinary neighbour keeps the generation's own spec: a rule adds an answer
        # for the cards it names, it does not displace the baseline for the rest
        assert (spec_of(plain), plain.via) == (GuideId.MTG_M15.value, Via.ERA)

    def test_an_mtg_card_with_no_recorded_frame_resolves_to_nothing(
        self, library: Library
    ) -> None:
        """A card filed before proxdex recorded traits has no frame to read. It used
        to take the commonest generation's spec as a least-wrong guess; now it takes
        none, `frames check` names it, and a re-fetch settles it."""
        found = specs.resolve(specs.load(library.root), "neo-136", "neo", MTG)
        assert found.via is Via.NONE
        assert found.spec is None

    def test_a_library_rule_beats_the_frame_generation(self, library: Library) -> None:
        specs.save(
            library.root, specs.spec("mtg-mine", "Mine", MTG, (2.0, 2.0, 2.0, 2.0))
        )
        specs.assign(library.root, "mtg-mine", MTG, "neo", Match.SET)
        found = specs.resolve(
            specs.load(library.root), "neo-136", "neo", MTG, traits={"frame": "2015"}
        )
        assert (spec_of(found), found.via) == ("mtg-mine", Via.SET_DEFAULT)

    def test_a_library_rule_beats_the_era(self, library: Library) -> None:
        add(library.root, "pokemon-vintage")
        specs.assign(library.root, "pokemon-vintage", POKEMON, "base1", Match.SET)
        found = specs.resolve(specs.load(library.root), "base1-4", "base1", POKEMON)
        assert found.via is Via.SET_DEFAULT
        assert spec_of(found) == "pokemon-vintage"
        assert found.rule == "r1"

    def test_an_exception_beats_the_sets_default(self, library: Library) -> None:
        add(library.root, "pokemon-swsh")
        add(library.root, "pokemon-secret")
        specs.assign(
            library.root, "pokemon-secret", POKEMON, "swsh4", Match.NUMBERS, "188-216"
        )
        specs.assign(library.root, "pokemon-swsh", POKEMON, "swsh4", Match.SET)
        reg = specs.load(library.root)
        ordinary = specs.resolve(reg, "swsh4-25", "swsh4", POKEMON)
        secret = specs.resolve(reg, "swsh4-190", "swsh4", POKEMON)
        assert (spec_of(ordinary), ordinary.via) == ("pokemon-swsh", Via.SET_DEFAULT)
        assert (spec_of(secret), secret.via) == ("pokemon-secret", Via.RULE)
        assert secret.rule == "r1"

    def test_the_printing_beats_a_rule_and_a_pin_beats_the_printing(
        self, library: Library
    ) -> None:
        """A borderless print has no frame whatever its set does — but a person who
        says otherwise about one card has the last word, and stored it."""
        add(library.root, "pokemon-swsh")
        specs.assign(library.root, "pokemon-swsh", POKEMON, "swsh4", Match.SET)
        reg = specs.load(library.root)
        printed = specs.resolve(
            reg, "swsh4-190", "swsh4", POKEMON, printing=GuideId.BORDERLESS.value
        )
        assert (printed.via, spec_of(printed)) == (Via.PRINTING, "borderless")
        pinned = specs.resolve(
            reg,
            "swsh4-190",
            "swsh4",
            POKEMON,
            printing=GuideId.BORDERLESS.value,
            pin="pokemon-swsh",
        )
        assert (pinned.via, spec_of(pinned)) == (Via.PIN, "pokemon-swsh")

    def test_an_override_beats_everything(self, library: Library) -> None:
        add(library.root, "pokemon-swsh")
        found = specs.resolve(
            specs.load(library.root),
            "base1-4",
            "base1",
            POKEMON,
            override="pokemon-swsh",
            pin=GuideId.BORDERLESS.value,
        )
        assert (found.via, spec_of(found)) == (Via.OVERRIDE, "pokemon-swsh")

    def test_a_pin_naming_a_removed_spec_is_reported_not_obeyed(
        self, library: Library
    ) -> None:
        """The failure mode of "support removing specs": the card would otherwise
        border off the fallback, silently, with a pin still sitting on it."""
        found = specs.resolve(
            specs.load(library.root), "base1-4", "base1", POKEMON, pin="gone-forever"
        )
        assert found.missing == "gone-forever"
        assert found.via is Via.ERA  # it carried on, at the next answer down
        assert not found.sure
        assert "no longer exists" in found.note

    def test_an_undecidable_rule_is_carried_into_the_resolution(
        self, library: Library
    ) -> None:
        add(library.root, "pokemon-swsh")
        add(library.root, "pokemon-secret")
        specs.assign(
            library.root,
            "pokemon-secret",
            POKEMON,
            "swsh4",
            Match.RARITY,
            "Rare Secret",
        )
        specs.assign(library.root, "pokemon-swsh", POKEMON, "swsh4", Match.SET)
        reg = specs.load(library.root)
        blind = specs.resolve(reg, "swsh4-190", "swsh4", POKEMON)
        assert blind.undecided == ("r1",)
        assert spec_of(blind) == "pokemon-swsh"  # it took the default meanwhile
        assert not blind.sure
        assert "traits" in blind.note
        seeing = specs.resolve(
            reg, "swsh4-190", "swsh4", POKEMON, traits={"rarity": "Rare Secret"}
        )
        assert (spec_of(seeing), seeing.via, seeing.undecided) == (
            "pokemon-secret",
            Via.RULE,
            (),
        )

    def test_set_codes_are_namespaced_by_game(self, library: Library) -> None:
        """MTG's ``neo`` is not Pokémon's ``neo1``, and a rule keyed on the code
        alone would cross them."""
        add(library.root, "pokemon-neo")
        specs.assign(library.root, "pokemon-neo", POKEMON, "neo", Match.SET)
        reg = specs.load(library.root)
        pokemon = specs.resolve(reg, "neo-1", "neo", POKEMON)
        assert pokemon.spec is not None
        assert pokemon.spec.id == "pokemon-neo"
        # MTG's `neo` is Kamigawa and the Pokémon rule must not reach it
        assert specs.resolve(reg, "neo-136", "neo", MTG).spec is None


class TestWhatTheProviderSays:
    """`sources.mtg_frame` — the one place a *printing* overrides its card's rules.

    Called directly because the alternative is a network round trip, and what is
    being pinned is a reading of one JSON object. It earns its place because it got
    this wrong: `full_art` reads as if it meant "no border" and does not.
    """

    def test_borderless_printings_are_frameless(self) -> None:
        assert (
            sources.mtg_frame({"border_color": "borderless"})
            == GuideId.BORDERLESS.value
        )
        assert sources.mtg_frame({"layout": "art_series"}) == (GuideId.BORDERLESS.value)

    def test_a_full_art_card_is_not_borderless(self) -> None:
        """A full-art card's *art* fills the frame; the black border is still there
        at its era's normal width. Measured on Scryfall's own scans: a ZNR full-art
        land carries 2.28-2.45mm, an Unhinged one 2.88-3.05mm — the same as their
        ordinary neighbours. Calling them borderless reshaped them to pure aspect
        and ran the art into the cut line, which no screen would ever show.
        """
        assert (
            sources.mtg_frame(
                {"border_color": "black", "full_art": True, "frame": "2015"}
            )
            is None
        )

    def test_the_ordinary_case_leaves_it_to_the_rules(self) -> None:
        assert sources.mtg_frame({"border_color": "black"}) is None
        assert sources.mtg_frame({"border_color": "silver"}) is None
        assert sources.mtg_frame({"border_color": "white"}) is None

    def test_the_frame_generation_travels_as_a_trait(self) -> None:
        """Which is what lets the shipped baseline answer per printing."""
        traits = sources.mtg_traits({"frame": "2015", "rarity": "rare"})
        assert traits[frames.FRAME_TRAIT] == "2015"


class TestCardState:
    """The card side of it: a pin is a decision, the rest is derived."""

    def test_a_pin_and_the_printing_are_different_markers(self, card: Card) -> None:
        card.write_kind(card.layout, frame=GuideId.BORDERLESS.value)
        card.set_pin("pokemon-swsh")
        assert card.printing_frame == GuideId.BORDERLESS.value
        assert card.pin == "pokemon-swsh"
        # a re-fetch rewrites everything derived — and must not touch the decision
        card.write_kind(card.layout, frame=None)
        assert card.printing_frame is None
        assert card.pin == "pokemon-swsh"

    def test_a_pin_survives_being_unset(self, card: Card) -> None:
        card.set_pin("pokemon-swsh")
        card.set_pin(None)
        assert card.pin is None

    def test_traits_round_trip_and_absent_means_unknown(self, card: Card) -> None:
        assert card.traits is None
        card.write_traits({"rarity": "Rare Secret", "subtypes": "VMAX", "blank": ""})
        assert card.traits == {"rarity": "Rare Secret", "subtypes": "VMAX"}
        card.write_traits({})
        assert card.traits is None

    def test_a_recorded_fit_compares_against_what_the_spec_says_now(
        self, card: Card
    ) -> None:
        card.write_fit(Stage.BORDERED, 0, "pokemon-swsh", (0.01, 0.02, 0.03, 0.04))
        fit = card.fit(Stage.BORDERED, 0)
        assert fit is not None
        # the same numbers, through six decimals of rounding, are not a finding
        assert fit.matches("pokemon-swsh", (0.01, 0.02, 0.03, 0.04))
        assert not fit.matches("pokemon-swsh", (0.011, 0.02, 0.03, 0.04))
        assert not fit.matches("pokemon-other", (0.01, 0.02, 0.03, 0.04))

    def test_an_unrecorded_fit_is_unknown_rather_than_stale(self, card: Card) -> None:
        assert card.fit(Stage.BORDERED, 0) is None


class TestAudit:
    """The warnings, which replaced a coverage report that could not work.

    Every fault here is a broken reference or a question nothing recorded can
    answer. What is pinned hardest is the *absence* of the old kind of complaint:
    a card resolving to its game's default because its printing said so is the
    system working, and a report that flagged that called 1046 MTG sets unmeasured
    while every card in them resolved exactly.
    """

    def test_a_clean_library_has_nothing_to_decide(self, library: Library) -> None:
        reg = specs.load(library.root)
        found = specs.resolve(reg, "base1-4", "base1", POKEMON)
        assert specs.audit(reg, [("base1-4", found)]) == []

    def test_a_measured_spec_is_not_a_warning(self, library: Library) -> None:
        """A card that resolves is silent, however little anybody trusts the four
        numbers — the audit reports broken references and unanswerable questions,
        never an opinion about a spec's quality."""
        reg = specs.load(library.root)
        found = specs.resolve(reg, "leb-270", "leb", MTG, traits={"frame": "1993"})
        assert found.via is Via.ERA
        assert specs.audit(reg, [("leb-270", found)]) == []

    def test_a_pin_to_a_removed_spec_is_reported(self, library: Library) -> None:
        reg = specs.load(library.root)
        found = specs.resolve(reg, "base1-4", "base1", POKEMON, pin="gone")
        (issue,) = specs.audit(reg, [("base1-4", found)])
        assert issue.fault is specs.Fault.MISSING
        assert issue.subject == "base1-4"
        assert "gone" in issue.detail

    def test_a_rule_that_cannot_be_decided_is_reported(self, library: Library) -> None:
        add(library.root, "pokemon-secret")
        specs.assign(
            library.root,
            "pokemon-secret",
            POKEMON,
            "swsh4",
            Match.RARITY,
            "Rare Secret",
        )
        reg = specs.load(library.root)
        found = specs.resolve(reg, "swsh4-190", "swsh4", POKEMON)
        (issue,) = specs.audit(reg, [("swsh4-190", found)])
        assert issue.fault is specs.Fault.UNDECIDED
        assert "r1" in issue.detail

    def test_a_printing_with_no_spec_is_reported_once(self, library: Library) -> None:
        """The card `border` will refuse. Reported once, with a hint naming the verb
        that fixes it, rather than once per side or once per stage."""
        reg = specs.load(library.root)
        found = specs.resolve(reg, "neo-136", "neo", MTG)
        (issue,) = specs.audit(reg, [("neo-136", found)])
        assert issue.fault is specs.Fault.UNKNOWN
        assert "frames set" in issue.hint

    def test_an_unreadable_file_and_a_dangling_rule_are_both_reported(
        self, library: Library
    ) -> None:
        folder = specs.specs_dir(library.root)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "junk.json").write_text("{not json", encoding="utf-8")
        add(library.root, "pokemon-swsh")
        specs.assign(library.root, "pokemon-swsh", POKEMON, "swsh4", Match.SET)
        specs.path_for(library.root, "pokemon-swsh").unlink()
        faults = {i.fault for i in specs.audit(specs.load(library.root), [])}
        assert faults == {specs.Fault.UNREADABLE, specs.Fault.MISSING}

    def test_every_fault_carries_its_own_hint(self) -> None:
        """The CLI prints these and the UI puts them in a column, so a fault with
        no hint is a blank cell telling somebody nothing."""
        for fault in specs.Fault:
            assert specs.Issue(fault=fault, subject="x").hint.strip()


class TestGameWideRules:
    """A rule with no set, which is the only way to express a frame *treatment*.

    `extendedart` runs the art to the left and right card edges in every set that
    ever printed one, so a selector that could only name one set could not describe
    it at all — and enumerating the sets would be a list that goes stale every
    release. What has to stay true is the **specificity order**: a set's own rule
    beats a game-wide one, so a global default can be added without burying anything.
    """

    def test_a_rule_with_no_set_covers_every_set_of_its_game(
        self, library: Library
    ) -> None:
        add(library.root, "mtg-extended", MTG)
        specs.assign(library.root, "mtg-extended", MTG, "", Match.EFFECT, "extendedart")
        reg = specs.load(library.root)
        traits = {"frame": "2015", "effects": "extendedart,legendary"}
        for set_id in ("neo", "m15", "ltr", "brandnewset"):
            found = specs.resolve(reg, f"{set_id}-1", set_id, MTG, traits=traits)
            assert (spec_of(found), found.via) == ("mtg-extended", Via.RULE), set_id

    def test_it_does_not_leak_into_the_other_game(self, library: Library) -> None:
        add(library.root, "mtg-extended", MTG)
        specs.assign(library.root, "mtg-extended", MTG, "", Match.EFFECT, "extendedart")
        found = specs.resolve(
            specs.load(library.root),
            "swsh4-1",
            "swsh4",
            POKEMON,
            traits={"effects": "extendedart"},
        )
        assert found.spec is None

    def test_a_sets_own_rule_beats_a_game_wide_one(self, library: Library) -> None:
        """Specificity, not file order — the global rule is added *second* here, so
        a naive first-match-wins over the file would still get this right; the
        `for_set` sort is what makes it right when it is added first."""
        add(library.root, "mtg-mine", MTG)
        add(library.root, "mtg-extended", MTG)
        specs.assign(library.root, "mtg-extended", MTG, "", Match.EFFECT, "extendedart")
        specs.assign(library.root, "mtg-mine", MTG, "ltr", Match.NUMBERS, "1-50")
        reg = specs.load(library.root)
        traits = {"frame": "2015", "effects": "extendedart"}
        assert spec_of(specs.resolve(reg, "ltr-20", "ltr", MTG, traits=traits)) == (
            "mtg-mine"
        )
        # and outside that range the global rule still answers
        assert spec_of(specs.resolve(reg, "ltr-90", "ltr", MTG, traits=traits)) == (
            "mtg-extended"
        )

    def test_a_sets_default_beats_a_game_wide_default(self, library: Library) -> None:
        add(library.root, "mtg-mine", MTG)
        specs.assign(library.root, "mtg-mine", MTG, "ltr", Match.SET)
        reg = specs.load(library.root)
        assert reg.for_set(MTG, "ltr")[0].set_id == "ltr"

    def test_a_whole_set_rule_with_no_set_is_one_border_for_the_game(
        self, library: Library
    ) -> None:
        """**One border for a whole game**, and it used to be refused.

        The refusal said such a rule "would claim every card of the game, which is
        what the game's own default spec already is", and both halves stopped being
        true: the per-game fallback spec was deleted (an unmeasured printing resolves
        to `Via.NONE` and refuses to be bordered), and a game a *library* defines has
        one border and any number of sets declared over time — so the refusal left the
        commonest case to be written one set at a time and silently unanswered on the
        next set added.

        A set the rule has never heard of is the case that matters: `wild-9` resolves
        here without anybody having mentioned `wild`, which is exactly what a rule per
        set cannot do.
        """
        add(library.root, "mtg-mine", MTG)
        rule = specs.assign(library.root, "mtg-mine", MTG, "", Match.SET)
        assert rule.is_global
        assert rule.is_default
        reg = specs.load(library.root)
        assert spec_of(specs.resolve(reg, "wild-9", "wild", MTG)) == "mtg-mine"

    def test_a_game_wide_default_beats_the_shipped_baseline(
        self, library: Library
    ) -> None:
        """A rule is more specific than the baseline, whichever band it is in —
        every rule is tried before `frames.baselines`. Without this, a library saying
        "all my Magic cards take these numbers" would still be overruled on the one
        era proxdex happens to ship."""
        add(library.root, "mtg-mine", MTG)
        specs.assign(library.root, "mtg-mine", MTG, "", Match.SET)
        found = specs.resolve(
            specs.load(library.root), "lea-1", "lea", MTG, traits={"frame": "1993"}
        )
        assert spec_of(found) == "mtg-mine"
        assert found.via is specs.Via.SET_DEFAULT

    def test_adding_a_set_default_does_not_delete_a_global_one(
        self, library: Library
    ) -> None:
        """`assign` removes the *existing default for that set* so a second can
        never shadow it. A global rule answers `covers` for every set, so comparing
        through `covers` would have deleted it here."""
        add(library.root, "mtg-extended", MTG)
        add(library.root, "mtg-mine", MTG)
        specs.assign(library.root, "mtg-extended", MTG, "", Match.EFFECT, "extendedart")
        specs.assign(library.root, "mtg-mine", MTG, "ltr", Match.SET)
        assert len(specs.load(library.root).rules) == 2


class TestTheShippedRulesAreVisible:
    """`specs.shipped_rules` — the baseline, rendered as the rules it is.

    `frames.BASELINE` decides the border of thirteen Pokémon sets and five Magic frame
    generations, and it was the one input to a fit that no screen showed: the specs are
    listed, a library's own rules are listed, the resolution names its `Via`, and an
    empty Rules tab therefore read as "nothing decides these borders". It is *shown*
    rather than copied into `frames/rules.json` — a copy would be frozen at the version
    that wrote it, and would not learn the next era somebody measures.

    So what has to hold is that the rows describe the same answer `resolve` gives, and
    that the override each row offers really does win.
    """

    def test_every_row_names_a_spec_a_fresh_library_has(self, library: Library) -> None:
        """These are offered as the spec of a pre-filled rule, so a row naming
        something not in the registry would offer a rule that cannot be saved."""
        reg = specs.load(library.root)
        assert specs.shipped_rules()
        for row in specs.shipped_rules():
            assert reg.get(row.spec) is not None, row

    def test_nothing_is_stored_in_the_library(self, library: Library) -> None:
        """The whole point of showing rather than materializing."""
        assert specs.shipped_rules()
        assert specs.load(library.root).rules == ()
        assert not specs.rules_path(library.root).exists()

    def test_a_row_is_one_baseline_entry_keyed_one_way(self) -> None:
        """One row per entry, not per set: `pokemon-wotc` covers thirteen sets and is
        one measurement, so thirteen rows would print one fact thirteen times."""
        entries = sum(len(v) for v in frames.BASELINE.values())
        rows = specs.shipped_rules()
        assert len(rows) == entries  # every entry keys exactly one way — pinned above
        wotc = next(r for r in rows if r.spec == "pokemon-wotc")
        assert wotc.key is frames.Key.SET
        assert "base1" in wotc.subjects
        assert len(wotc.subjects) > 1

    def test_the_first_row_for_a_set_is_the_spec_a_card_of_it_gets(self) -> None:
        """The order is `BASELINE`'s, which is the order `baselines` offers in — so
        the row a reader sees first is the one their card is really fitted to, and the
        e-Card rows do not read as two contradictory claims."""
        rows = [
            r
            for r in specs.shipped_rules(POKEMON)
            if r.key is frames.Key.SET and "ecard1" in r.subjects
        ]
        assert [r.spec for r in rows] == list(frames.baselines("ecard1", POKEMON))

    def test_the_override_it_offers_is_the_one_that_wins(
        self, library: Library
    ) -> None:
        """`Shipped.match` is what the UI's Override button fills the form with, so a
        rule of that kind has to beat the row it came from. A set-keyed row is
        overridden by that set's own default and a generation-keyed row by a game-wide
        `frame` rule — both are rules, and every rule is tried before the baseline."""
        add(library.root, "mtg-mine", MTG)
        by_generation = next(
            r
            for r in specs.shipped_rules(MTG)
            if r.key is frames.Key.GENERATION and "1993" in r.subjects
        )
        assert by_generation.match is Match.FRAME
        specs.assign(library.root, "mtg-mine", MTG, "", by_generation.match, "1993")
        found = specs.resolve(
            specs.load(library.root), "arn-1", "arn", MTG, traits={"frame": "1993"}
        )
        assert spec_of(found) == "mtg-mine"
        # and the row it overrode is still offered, so the choice stays visible
        assert by_generation.spec in {c.spec.id for c in found.alternatives}


class TestFrameTreatments:
    """`Match.EFFECT`, and the one thing it must not do: warn about ordinary cards.

    93190 of Magic's 116233 printings carry **no** frame effects. That is an answer,
    not a gap, so an empty value has to read as "no" — the generic trait path returns
    `None` (undecidable) for an empty value, which would have put a warning on four
    cards in five the moment a global treatment rule existed.
    """

    def rule(self) -> specs.Rule:
        return specs.Rule("r1", MTG, "", Match.EFFECT, "extendedart", "mtg-extended")

    def test_it_matches_one_effect_among_several(self) -> None:
        assert self.rule().selects("ltr-1", {"effects": "extendedart,legendary"})

    def test_a_printing_with_no_effects_is_a_no_not_a_maybe(self) -> None:
        assert self.rule().selects("ltr-1", {"frame": "2015", "effects": ""}) is False
        # and the same when the key was never written, which is every card fetched
        # before proxdex recorded effects at all
        assert self.rule().selects("ltr-1", {"frame": "2015"}) is False

    def test_only_a_card_with_no_traits_at_all_is_undecidable(self) -> None:
        assert self.rule().selects("ltr-1", None) is None

    def test_effects_are_recorded_off_the_printing(self) -> None:
        """Read straight off the provider's card object, like every other trait, so
        choosing a spec never costs a second API call."""
        traits = sources.mtg_traits(
            {"frame": "2015", "border_color": "black", "frame_effects": ["extendedart"]}
        )
        assert traits[specs.TRAIT_EFFECTS] == "extendedart"
        assert sources.mtg_traits({"frame": "2015"})[specs.TRAIT_EFFECTS] == ""


class TestWhatThePrintingSettles:
    """`sources.mtg_frame` — the one place a *printing* overrides its card's rules.

    It returns `borderless` and nothing else now. Extended art and the yellow
    box-topper band really are their own geometries — `scripts/mtg-variants.py` is
    clear about that — but the specs describing them were read off scans and went out
    with the rest, so those cards resolve to no spec like every other unmeasured
    printing rather than to a number nobody took.
    """

    def test_borderless_is_settled_by_the_printing(self) -> None:
        assert (
            sources.mtg_frame({"border_color": "borderless"})
            == GuideId.BORDERLESS.value
        )

    def test_a_yellow_border_is_a_band_and_the_printing_settles_it(self) -> None:
        """The one case where `border_color` decides the *geometry*: all 79 yellow
        printings are Aetherdrift box-toppers, 1.7mm wider on the sides than the
        generation they sit in. Measured on `dft-501`."""
        assert (
            sources.mtg_frame({"border_color": "yellow", "frame_effects": ["inverted"]})
            == GuideId.MTG_YELLOW_BAND.value
        )

    def test_extended_art_keeps_its_generations_border(self) -> None:
        """A correction, and the reason it is pinned: the scan survey reported extended
        art's sides at 0 — "the art runs off the card" — and that was the old
        auto-detector failing on dark art. Measured over 240 rows of `cmr-700`, the
        black border is
        27-28px on both sides against a plain card's 29-30. Same border, so no spec of
        its own, so nothing here may start returning one."""
        assert (
            sources.mtg_frame(
                {"border_color": "black", "frame_effects": ["extendedart"]}
            )
            is None
        )

    def test_the_treatments_that_change_nothing_change_nothing(self) -> None:
        """The survey's most useful result, guarded: none of these may start
        returning a spec of its own, because each was measured at its generation's
        border over hundreds to thousands of printings."""
        for effect in (
            "legendary",
            "inverted",
            "enchantment",
            "etched",
            "showcase",
            "snow",
            "devoid",
            "miracle",
            "companion",
            "draft",
            "spree",
            "colorshifted",
            "tombstone",
            "fullart",
        ):
            got = sources.mtg_frame(
                {"border_color": "black", "frame_effects": [effect]}
            )
            assert got is None, effect

    def test_a_colour_that_is_not_a_geometry_is_left_alone(self) -> None:
        """White, gold and silver all measure at their generation's width — white on
        `8ed-274` at the same 35px as the black `c13-259`, which is the cleanest
        demonstration there is that colour is not geometry. Yellow is the single
        exception, and it is a decorative *band* rather than a border colour."""
        for colour in ("white", "gold", "silver", "black"):
            assert sources.mtg_frame({"border_color": colour}) is None


class TestTheNineteenNinetyThreeSplit:
    """One frame generation, three geometries, 1mm apart — and keyed by set.

    Scryfall calls Alpha, Unlimited and 4th Edition all `frame: 1993`, and the design
    really is the same; the *printings* are not. Measured off **Lightning Bolt**, the
    one card printed in all five of those sets, so the art is identical and nothing but
    the border can differ:

        Alpha `lea-161`, Beta `leb-162`          32px t/b, 23px sides
        Unlimited `2ed-162`, Revised `3ed-162`   42.5px, 35px
        4th Edition `4ed-208`                    33px, 30px

    Sol Ring agrees exactly wherever both exist, an independent run-length scan
    reproduces the white-bordered trio to the pixel, and a withdrawn colour survey had
    landed on the same three groupings. This is pinned because it is the largest spread
    in the project and because it is *invisible*: a card fitted to a neighbour's number
    looks perfect until two of them are cut and laid side by side.
    """

    def test_the_three_printings_get_three_different_specs(self) -> None:
        reg = specs.load(Path("/nonexistent"))
        got = {
            s: spec_of(
                specs.resolve(
                    reg, f"{s}-1", s, MTG, traits={frames.FRAME_TRAIT: "1993"}
                )
            )
            for s in (
                "lea",
                "leb",
                "ced",
                "cei",
                "2ed",
                "3ed",
                "4ed",
                "ice",
                "arn",
                "4bb",
            )
        }
        assert got == {
            # band 1 — Alpha, Beta, and the Beta-derived Collectors' Editions
            "lea": GuideId.MTG_1993_ALPHA.value,
            "leb": GuideId.MTG_1993_ALPHA.value,
            "ced": GuideId.MTG_1993_ALPHA.value,
            "cei": GuideId.MTG_1993_ALPHA.value,
            # band 3 — the widest border of the decade
            "2ed": GuideId.MTG_1993_UNLIMITED.value,
            "3ed": GuideId.MTG_1993_UNLIMITED.value,
            # band 2 — the ordinary border, reached through the *generation*
            "4ed": GuideId.MTG_1993.value,
            "ice": GuideId.MTG_1993.value,
            "arn": GuideId.MTG_1993.value,
            # and the one printing that measured as another frame entirely
            "4bb": GuideId.MTG_1997.value,
        }

    def test_the_gap_between_them_is_real_and_large(self) -> None:
        """A full millimetre on the sides between Alpha and Revised. If a refactor ever
        collapses these back into one spec, this is the assertion that fails."""
        sides = {
            i: round(frames.SHIPPED[i].mm()[1], 2)
            for i in (
                GuideId.MTG_1993_ALPHA.value,
                GuideId.MTG_1993.value,
                GuideId.MTG_1993_UNLIMITED.value,
            )
        }
        assert sides == {
            "mtg-1993-alpha": 1.96,
            "mtg-1993": 2.47,
            "mtg-1993-unlimited": 2.98,
        }
        assert len(set(sides.values())) == 3
        assert max(sides.values()) - min(sides.values()) > 1.0

    def test_colour_is_still_not_geometry(self) -> None:
        """Revised and 4th Edition are **both white-bordered 1993-frame cards** and they
        differ by 5px on the sides; `fbb` and `4bb` are both black-bordered Belgian
        printings and differ by 9px, in opposite directions. So there is no colour rule
        and no "foreign" rule to write, and `mtg_frame` must go on refusing to read
        geometry out of `border_color`."""
        white = (GuideId.MTG_1993_UNLIMITED.value, GuideId.MTG_1993.value)
        assert frames.SHIPPED[white[0]].inset != frames.SHIPPED[white[1]].inset
        assert sources.mtg_frame({"border_color": "white", "frame": "1993"}) is None

    def test_the_ordinary_band_is_a_measurement_not_a_fallback(self) -> None:
        """Band 2 answers for every 1993 set that is not a named exception, and it does
        so because **18 of them were read and landed inside 0.43mm of each other** — not
        because something had to be picked. Briefly, with only Alpha and Revised read,
        these resolved to nothing; a generation-wide number would then have been a coin
        flip across a 1mm spread. Eighteen readings is what changed that.
        """
        reg = specs.load(Path("/nonexistent"))
        for ordinary in ("ice", "arn", "atq", "leg", "chr", "hml", "all", "fem", "drk"):
            found = specs.resolve(
                reg,
                f"{ordinary}-1",
                ordinary,
                MTG,
                traits={frames.FRAME_TRAIT: "1993"},
            )
            assert found.via is Via.ERA, ordinary
            assert spec_of(found) == GuideId.MTG_1993.value, ordinary

    def test_a_1993_card_with_no_traits_still_resolves_by_set(self) -> None:
        """The band-1 and band-3 entries are keyed on the *set id*, which a library
        filed years ago always has — so those answer even when nothing was recorded
        about the printing. Only band 2 needs the generation trait."""
        reg = specs.load(Path("/nonexistent"))
        assert spec_of(specs.resolve(reg, "lea-1", "lea", MTG)) == (
            GuideId.MTG_1993_ALPHA.value
        )
        assert spec_of(specs.resolve(reg, "2ed-1", "2ed", MTG)) == (
            GuideId.MTG_1993_UNLIMITED.value
        )


class TestOversized:
    """An oversized card needs its own spec **even when its border is the same width**.

    An Archenemy scheme measures 2.98 / 3.00mm — physically identical to an ordinary
    2003-frame card's 2.99 / 2.98. But a spec is a *fraction*, and the card is 89×127mm,
    so the same millimetres are a different fraction entirely. Resolving a scheme to
    `mtg-2003` (which is what its frame generation did before these existed) asks for
    4.27 / 4.18mm on a card whose border is 2.98 / 3.00 — 1.2mm too wide on every edge,
    and it looks right on screen because the overlay is drawn in fractions too.
    """

    def test_the_layout_settles_it_not_the_generation(self) -> None:
        assert sources.mtg_frame({"layout": "scheme"}) == GuideId.MTG_OVERSIZED.value
        assert sources.mtg_frame({"layout": "vanguard"}) == GuideId.MTG_VANGUARD.value
        # a vanguard reports frame 1993, which would otherwise hand it Alpha's 1.96mm
        assert (
            sources.mtg_frame({"layout": "vanguard", "frame": "1993"})
            == GuideId.MTG_VANGUARD.value
        )

    def test_the_same_millimetres_are_a_different_fraction(self) -> None:
        """The whole reason these exist, stated as numbers: equal in mm on their own
        card, unequal as fractions — so one spec cannot serve both sizes."""
        scheme = frames.SHIPPED[GuideId.MTG_OVERSIZED.value]
        ordinary = frames.SHIPPED[GuideId.MTG_2003.value]
        assert scheme.inset != ordinary.inset
        # each reports the card it is actually about, and on those cards they agree to
        # 0.02mm — the same physical border, which is what makes the fraction the story
        assert scheme.oversized
        assert not ordinary.oversized
        assert abs(scheme.mm()[1] - ordinary.mm()[1]) < 0.02
        # and asking `mtg-2003` for an oversized card is where the 1.2mm comes from
        assert ordinary.mm(*scheme.card_mm)[1] - scheme.mm()[1] > 1.0
        # and using the wrong spec asks for well over a millimetre too much

    def test_vanguard_is_its_own_size_and_its_own_border(self) -> None:
        """Read at 1060x1510 where planes and schemes are 1040x1490, and genuinely
        thicker: 5.30 / 4.03mm, nearly twice an ordinary card's."""
        vanguard = frames.SHIPPED[GuideId.MTG_VANGUARD.value]
        assert vanguard.oversized
        top, side, *_ = vanguard.mm()
        assert (round(top, 2), round(side, 2)) == (5.30, 4.03)
        # and it is nearly twice an ordinary card's, so it cannot share a spec
        assert side > frames.SHIPPED[GuideId.MTG_M15.value].mm()[1] * 1.5

    def test_a_plane_shares_the_number_measured_off_the_same_stock(self) -> None:
        """A plane could not be read — art to the edges, uneven border — so it takes the
        scheme's number rather than being called borderless.

        That is the safe direction and it is not a guess about geometry: same product
        line, same 89×127mm stock, same era, and the scheme measured 2.98/3.00mm, which
        is the *same physical border* an ordinary 2003-frame card carries. Calling a
        bordered card borderless throws its fit away and looks perfect, which is the one
        error this project treats as unacceptable.
        """
        assert sources.mtg_frame({"layout": "planar"}) == GuideId.MTG_OVERSIZED.value
        assert sources.mtg_frame({"layout": "planar", "frame": "2003"}) == (
            GuideId.MTG_OVERSIZED.value
        )
        # phenomena share the planar layout, so they are covered by the same entry
        assert sources.mtg_frame({"layout": "planar", "frame": "2015"}) == (
            GuideId.MTG_OVERSIZED.value
        )


class TestTokensNeedNoSpec:
    """Measured, and they match their generation exactly — so nothing was added.

    A token's layout is bespoke (no mana cost, larger art), so "same stock, same die"
    was not enough to assume it. Read by hand: `tmsh-3` (M15 token) and `tdft-13`
    (emblem) are 30px on every edge, which is `mtg-m15` to the pixel; `p03-6` and
    `pcsp-1` (2003-frame tokens) are 35px, which is `mtg-2003`. A double-faced
    punchcard token has no border at all and its layout already says so.
    """

    def test_a_token_takes_its_generations_spec(self) -> None:
        reg = specs.load(Path("/nonexistent"))
        pairs = (
            ("2015", GuideId.MTG_M15.value),
            ("2003", GuideId.MTG_2003.value),
        )
        for gen, want in pairs:
            found = specs.resolve(
                reg, "t-1", "t", MTG, traits={frames.FRAME_TRAIT: gen}
            )
            assert spec_of(found) == want, gen

    def test_the_measured_token_numbers_are_the_generations_numbers(self) -> None:
        """`tmsh-3` read 30px at 744x1040 and `mtg-m15` *is* 30/744 — so a token spec
        would have been a duplicate of a number already here."""
        m15 = frames.SHIPPED[GuideId.MTG_M15.value]
        assert m15.inset == (30 / 1040, 30 / 744, 30 / 1040, 30 / 744)
        c2003 = frames.SHIPPED[GuideId.MTG_2003.value]
        assert c2003.inset == (35 / 1040, 35 / 745, 35 / 1040, 35 / 745)

    def test_no_shipped_spec_is_about_tokens(self) -> None:
        """The result of the measurement, stated so a future reader does not re-open
        it: there is deliberately no token spec, because there is nothing to add."""
        assert not [i for i in frames.SHIPPED if "token" in i or "emblem" in i]


class TestTheECardFrameIsAsymmetric:
    """The e-Card series is the one spec whose *shape* is the finding.

    Expedition, Aquapolis and Skyridge (2002-03) carry the Nintendo e-Reader dot-code
    strip down the left edge and along the bottom, so two of the four edges are about
    twice the other two. Every other spec here collapses opposite edges deliberately —
    that is how a cutting error cancels — and doing it here would split the difference
    on all four, asking for ~2.5mm too much border on two edges and ~2.5mm too little
    on the others. It would look plausible on screen, because the overlay is drawn in
    fractions too.

    Read by hand off two cards at 337x467 and 737x1036 (`docs/measuring-frames.md`).
    The reason to believe the asymmetry belongs to the *card* is that it reproduced:
    a crop shifts the two opposite edges against each other, so it cannot yield the
    same lopsided reading twice at scales 2.2x apart.
    """

    #: the two hand readings, as (w, h, top, right, bottom, left) in pixels
    READINGS = (
        (337, 467, 17, 17, 35, 38),
        (737, 1036, 35, 38, 80, 83),
    )

    def test_the_shipped_inset_is_the_average_of_the_two_readings(self) -> None:
        """Pinned against the pixels rather than restating the stored decimals, so a
        number nobody measured cannot be edited in and still look like this."""
        per_edge = [
            [t / h, r / w, b / h, left / w] for w, h, t, r, b, left in self.READINGS
        ]
        want = tuple((a + b) / 2 for a, b in zip(per_edge[0], per_edge[1], strict=True))
        spec = frames.SHIPPED[GuideId.POKEMON_ECARD.value]
        for got, expected in zip(spec.inset, want, strict=True):
            assert abs(got - expected) < 5e-7

    def test_the_two_readings_agree_per_edge(self) -> None:
        """The whole basis of the spec: every edge inside 0.27pp across a 2.2x scale
        difference, and the widest edge — the one an off-centre crop would distort
        most — inside 0.015pp."""
        first, second = self.READINGS
        spreads = [
            abs(a / d1 - b / d2)
            for a, b, d1, d2 in zip(
                first[2:],
                second[2:],
                (first[1], first[0], first[1], first[0]),
                (second[1], second[0], second[1], second[0]),
                strict=True,
            )
        ]
        assert max(spreads) < 0.0027
        assert spreads[3] < 0.00015  # left, the dot-code edge

    def test_the_wide_edges_are_the_left_and_the_bottom(self) -> None:
        """Which edges, not just that two of them differ — the strip is on the left
        and the bottom, and a spec with them on the right and the top would fit every
        card mirrored. Confirmed against the printed card: `ecard1-1`'s dot code runs
        down the left edge and along the bottom."""
        top, right, bottom, left = frames.SHIPPED[GuideId.POKEMON_ECARD.value].inset
        assert left > 2 * right
        assert bottom > 2 * top

    def test_the_two_ordinary_edges_land_on_the_wotc_border(self) -> None:
        """The corroboration worth having: the same operation printed the same border
        and added a strip. Top 3.12mm against WOTC's 3.45 and right 3.24 against 3.15
        — within a third of a millimetre, on numbers taken by different methods
        (calipers there, pixels here) from cards a year apart."""
        ecard = frames.SHIPPED[GuideId.POKEMON_ECARD.value].mm()
        wotc = frames.SHIPPED[GuideId.POKEMON_WOTC.value].mm()
        assert abs(ecard[0] - wotc[0]) < 0.4  # top
        assert abs(ecard[1] - wotc[1]) < 0.4  # right

    def test_all_three_e_card_sets_resolve_to_it(self) -> None:
        for set_id in ("ecard1", "ecard2", "ecard3"):
            assert frames.baseline(set_id, POKEMON) is GuideId.POKEMON_ECARD

    def test_it_did_not_take_the_wotc_sets_with_it(self) -> None:
        """Adding an era must leave the one already there answering for exactly what it
        did — every set of it, and no more."""
        for set_id in ("base1", "base6", "gym1", "gym2", "neo1", "neo4"):
            assert frames.baseline(set_id, POKEMON) is GuideId.POKEMON_WOTC

    def test_the_wizards_promos_keep_the_wotc_border(self) -> None:
        """`basep` is the one id in `BASELINE` that was not separately read. It is there
        because the old `"base"` *prefix* was already claiming it, and dropping it would
        stop a card that borders today — the same operation and the same yellow border,
        recorded as inherited rather than measured in `docs/measuring-frames.md`. Pinned
        so that if it is ever dropped, that is a decision somebody made on purpose."""
        assert frames.baseline("basep", POKEMON) is GuideId.POKEMON_WOTC

    def test_no_unmeasured_pokemon_set_is_claimed_by_a_near_id(self) -> None:
        """The whole class of bug exact matching removes. `si1` (Southern Islands, 2001)
        sits between the WOTC sets in time and was never read; nothing may answer for it
        because its id happens to start with a letter something else claims."""
        for set_id in ("si1", "base", "gym", "neo", "ecard", "basep1"):
            assert frames.baselines(set_id, POKEMON) == (), set_id

    def test_diamond_and_pearl_onward_still_refuses(self) -> None:
        """Adding an era is purely additive, and the remaining gap is still a gap: a
        printing nobody has read must resolve to nothing and refuse to be bordered,
        not borrow the e-Card numbers because they are the nearest in time.

        The ex series is deliberately **not** in this list any more — `ex5` on inherit
        the era's plain border by an explicit decision (see
        `TestTheRestOfTheExSeriesInheritsThePlainBorder`). Everything from Diamond &
        Pearl is where refusing starts.
        """
        for set_id in ("dp1", "hgss1", "bw1", "xy1", "sm1", "swsh1", "sv1", "pop1"):
            assert frames.baseline(set_id, POKEMON) is None

    def test_it_resolves_by_era_with_a_note_nobody_has_to_read(self) -> None:
        reg = specs.load(Path("/nonexistent"))
        found = specs.resolve(reg, "ecard1-1", "ecard1", POKEMON)
        assert found.via is Via.ERA
        assert spec_of(found) == GuideId.POKEMON_ECARD.value
        assert found.have


class TestMoreThanOneBorderCanApply:
    """One set can hold two borders, and only a person can say which a card is.

    Pokémon's e-Card sets are the case that forced this: the same set printed Pokémon
    cards and Trainer/Energy cards whose frames differ, and nothing in the metadata
    says which in terms anybody has measured. The old answer was to *delete* a second
    whole-set rule as unreachable; the answer now is to reach it — `resolve` collects
    every applicable spec, uses the most specific, and **offers** the rest.

    This earns tests for two reasons. It is a change to the one function every fitting
    surface goes through, so the winner must be exactly what it was before; and the
    offer is invisible until two cards are cut side by side, which is the same argument
    the rest of this file rests on.
    """

    def test_two_whole_set_rules_both_survive(self, library: Library) -> None:
        """The old code removed the first when the second was added. A rule silently
        deleted is worse than a rule that loses, now that losing means "offered"."""
        add(library.root, "pkm-trainer")
        add(library.root, "pkm-mon")
        specs.assign(library.root, "pkm-trainer", POKEMON, "base1", Match.SET)
        specs.assign(library.root, "pkm-mon", POKEMON, "base1", Match.SET)
        reg = specs.load(library.root)
        assert {r.spec for r in reg.rules} == {"pkm-trainer", "pkm-mon"}

    def test_both_are_offered_and_the_first_still_wins(self, library: Library) -> None:
        add(library.root, "pkm-trainer")
        add(library.root, "pkm-mon")
        specs.assign(library.root, "pkm-trainer", POKEMON, "base1", Match.SET)
        specs.assign(library.root, "pkm-mon", POKEMON, "base1", Match.SET)
        found = specs.resolve(specs.load(library.root), "base1-4", "base1", POKEMON)
        assert found.via is Via.SET_DEFAULT
        assert spec_of(found) == "pkm-trainer"
        assert found.ambiguous
        # the second rule *and* the shipped era: three measured answers really do
        # describe this printing, and the offer names every one it did not take
        assert [c.spec.id for c in found.alternatives] == [
            "pkm-mon",
            GuideId.POKEMON_WOTC.value,
        ]

    def test_the_shipped_era_is_offered_beside_a_set_rule(
        self, library: Library
    ) -> None:
        """The commonest real shape: a library adds one spec for a set the shipped
        baseline already answers, and both are true of the printing."""
        add(library.root, "pkm-trainer")
        specs.assign(library.root, "pkm-trainer", POKEMON, "base1", Match.SET)
        found = specs.resolve(specs.load(library.root), "base1-4", "base1", POKEMON)
        assert spec_of(found) == "pkm-trainer"
        assert [(c.spec.id, c.via) for c in found.alternatives] == [
            (GuideId.POKEMON_WOTC.value, Via.ERA)
        ]

    def test_picking_one_is_a_pin_and_the_other_is_still_offered(
        self, library: Library
    ) -> None:
        """The offer is symmetric, which is what makes it a choice rather than a
        one-way door: pinning the alternative leaves the rule's answer offered."""
        add(library.root, "pkm-trainer")
        specs.assign(library.root, "pkm-trainer", POKEMON, "base1", Match.SET)
        reg = specs.load(library.root)
        found = specs.resolve(
            reg, "base1-4", "base1", POKEMON, pin=GuideId.POKEMON_WOTC.value
        )
        assert (spec_of(found), found.via) == (GuideId.POKEMON_WOTC.value, Via.PIN)
        assert [c.spec.id for c in found.alternatives] == ["pkm-trainer"]

    def test_one_spec_reached_twice_is_one_choice(self, library: Library) -> None:
        """Deduped by spec id: the same four numbers arrived at two ways is not two
        borders, and offering it as one would ask a question with the same answer
        twice. The way with the most precedence names it."""
        add(library.root, "pkm-both")
        specs.assign(library.root, "pkm-both", POKEMON, "base1", Match.SET)
        found = specs.resolve(
            specs.load(library.root), "base1-4", "base1", POKEMON, pin="pkm-both"
        )
        assert (spec_of(found), found.via) == ("pkm-both", Via.PIN)
        assert [c.spec.id for c in found.alternatives] == [GuideId.POKEMON_WOTC.value]

    def test_an_ordinary_card_is_not_ambiguous(self, library: Library) -> None:
        """The property that matters most: almost every card has exactly one answer,
        and nothing new appears on it."""
        found = specs.resolve(specs.load(library.root), "base1-4", "base1", POKEMON)
        assert not found.ambiguous
        assert found.alternatives == ()

    def test_a_rule_saying_the_same_thing_twice_is_refused(
        self, library: Library
    ) -> None:
        """A second *identical* selector for the same spec adds nothing and would
        show up as an alternative to itself if it were not deduped. Refused at the
        point of writing, where the message can name the rule that already says it."""
        add(library.root, "pkm-trainer")
        specs.assign(library.root, "pkm-trainer", POKEMON, "base1", Match.SET)
        with pytest.raises(ProxdexError, match="already says exactly that"):
            specs.assign(library.root, "pkm-trainer", POKEMON, "base1", Match.SET)

    def test_an_undecidable_rule_below_the_winner_is_still_not_reported(
        self, library: Library
    ) -> None:
        """The walk no longer stops at the winner, so this is the thing that could
        have regressed: a pinned card must not start warning about a trait rule the
        pin already settled."""
        add(library.root, "pkm-secret")
        add(library.root, "pkm-pinned")
        specs.assign(
            library.root, "pkm-secret", POKEMON, "base1", Match.RARITY, "Rare Secret"
        )
        found = specs.resolve(
            specs.load(library.root), "base1-4", "base1", POKEMON, pin="pkm-pinned"
        )
        assert (spec_of(found), found.via) == ("pkm-pinned", Via.PIN)
        assert found.undecided == ()
        assert found.sure

    def test_a_missing_spec_below_the_winner_is_still_not_reported(
        self, library: Library
    ) -> None:
        add(library.root, "pkm-pinned")
        # written directly: `assign` refuses a spec that does not exist, and what this
        # needs is the state a *removed* one leaves behind — a rule pointing at nothing
        specs.write_rules(
            library.root,
            [
                specs.Rule(
                    id="r1",
                    game=POKEMON,
                    set_id="ecard3",
                    match=Match.SET,
                    value="",
                    spec="gone",
                )
            ],
            counter=2,
        )
        found = specs.resolve(
            specs.load(library.root), "ecard3-1", "ecard3", POKEMON, pin="pkm-pinned"
        )
        assert (spec_of(found), found.via) == ("pkm-pinned", Via.PIN)
        assert found.missing is None
        assert found.sure


class TestTheECardSetsShipTwoFrames:
    """`pokemon-ecard-deep-top`, and the substitution that produced its numbers.

    The reading was one card, 468×650: left 52px, right 20px, top 67px, bottom 49px.
    Left and bottom agreed with `pokemon-ecard` to 0.158pp and 0.070pp, so those are
    *shared* — two specs differing by a tenth of a millimetre on an edge would be two
    answers to one question — and top and right were re-derived to hold the reading's
    **sums** rather than its individual edges.

    Every one of those numbers is now four decimal places in a source file, which is
    exactly the kind of thing that gets edited later by somebody who does not have the
    reading in front of them. So the arithmetic is asserted from the raw pixel counts.
    """

    #: the reading, as given
    W, H = 468, 650
    LEFT_PX, RIGHT_PX, TOP_PX, BOTTOM_PX = 52, 20, 67, 49

    def test_left_and_bottom_are_the_existing_spec_s_exactly(self) -> None:
        """Not "close to": the same float. An e-Card's dot-code strip is in the same
        place on both frames, and a fit has to put it in the same place too."""
        ecard = frames.SHIPPED[GuideId.POKEMON_ECARD.value]
        deep = frames.SHIPPED[GuideId.POKEMON_ECARD_DEEP_TOP.value]
        assert deep.inset[3] == ecard.inset[3]  # left
        assert deep.inset[2] == ecard.inset[2]  # bottom

    def test_the_substituted_edges_were_close_enough_to_substitute(self) -> None:
        """The premise of sharing them. 0.158pp and 0.070pp — well inside the 0.27pp
        the existing spec's own two readings agreed to across a 2.2x scale gap."""
        ecard = frames.SHIPPED[GuideId.POKEMON_ECARD.value]
        assert abs(self.LEFT_PX / self.W - ecard.inset[3]) * 100 < 0.2
        assert abs(self.BOTTOM_PX / self.H - ecard.inset[2]) * 100 < 0.1

    def test_the_vertical_sum_is_the_one_that_was_measured(self) -> None:
        """The compensation, and the whole reason `top` is not the 10.308% read off the
        card: substituting a bottom 0.45px different would otherwise have moved the art
        panel. Held to the reading's own sum instead."""
        deep = frames.SHIPPED[GuideId.POKEMON_ECARD_DEEP_TOP.value]
        measured = (self.TOP_PX + self.BOTTOM_PX) / self.H
        assert deep.inset[0] + deep.inset[2] == pytest.approx(measured, abs=5e-6)

    def test_the_horizontal_sum_is_the_one_that_was_measured(self) -> None:
        """Same operation on the other axis, because `left` was substituted too."""
        deep = frames.SHIPPED[GuideId.POKEMON_ECARD_DEEP_TOP.value]
        measured = (self.LEFT_PX + self.RIGHT_PX) / self.W
        assert deep.inset[1] + deep.inset[3] == pytest.approx(measured, abs=5e-6)

    def test_the_top_is_the_finding(self) -> None:
        """~6mm deeper than the other e-Card frame. The raw reading was +6.04mm and
        the shipped number is +5.98mm, the difference being the 0.45px the vertical
        compensation moved the top by — worth pinning as the *shipped* figure, since
        that is the one a card is fitted to."""
        ecard = frames.SHIPPED[GuideId.POKEMON_ECARD.value]
        deep = frames.SHIPPED[GuideId.POKEMON_ECARD_DEEP_TOP.value]
        assert deep.mm()[0] - ecard.mm()[0] == pytest.approx(5.98, abs=0.02)

    def test_right_was_not_substituted(self) -> None:
        """0.827pp / 0.53mm apart — seven times the reproducibility the existing spec
        showed on that edge, and past the cutting tolerance. Substituting it would also
        have forced `left` to 10.28% to hold the sum, contradicting left being close."""
        ecard = frames.SHIPPED[GuideId.POKEMON_ECARD.value]
        deep = frames.SHIPPED[GuideId.POKEMON_ECARD_DEEP_TOP.value]
        assert deep.inset[1] != ecard.inset[1]

    def test_both_frames_are_shipped_for_every_ecard_set(self) -> None:
        for set_id in ("ecard1", "ecard2", "ecard3"):
            assert frames.baselines(set_id, POKEMON) == (
                GuideId.POKEMON_ECARD,
                GuideId.POKEMON_ECARD_DEEP_TOP,
            ), set_id

    def test_the_common_frame_is_the_one_a_fit_uses(self, library: Library) -> None:
        """Most e-Card cards take the shallow top, so that is what `border` runs
        against with nothing chosen — the other is the offer."""
        found = specs.resolve(specs.load(library.root), "ecard3-1", "ecard3", POKEMON)
        assert spec_of(found) == GuideId.POKEMON_ECARD.value
        assert found.via is Via.ERA

    def test_the_deep_one_is_offered_on_every_ecard_card(
        self, library: Library
    ) -> None:
        """Which is how anybody finds it: nothing in the metadata says which frame a
        card has, so the only workable answer is to name both and let a person pick."""
        found = specs.resolve(specs.load(library.root), "ecard3-1", "ecard3", POKEMON)
        assert [c.spec.id for c in found.alternatives] == [
            GuideId.POKEMON_ECARD_DEEP_TOP.value
        ]

    def test_picking_it_fits_against_it(self, library: Library) -> None:
        found = specs.resolve(
            specs.load(library.root),
            "ecard3-1",
            "ecard3",
            POKEMON,
            pin=GuideId.POKEMON_ECARD_DEEP_TOP.value,
        )
        assert (spec_of(found), found.via) == (
            GuideId.POKEMON_ECARD_DEEP_TOP.value,
            Via.PIN,
        )
        assert [c.spec.id for c in found.alternatives] == [GuideId.POKEMON_ECARD.value]


class TestTheExEraStripRunsAlongTheBottom:
    """`pokemon-ecard-ex`, and why it is a shape rather than a renumbering.

    Two cards, read by hand: 747×1040 (left 32, right 27, top 39, bottom 71 px) and
    455×642 (left 20, right 17, top 23, bottom 43). The e-Reader strip is on the
    **bottom alone** here, where the e-Card sets carry it down the left as well — so
    resolving one of these to `pokemon-ecard` would ask for 7.16mm of left border on a
    card that has 2.76, which is invisible on screen because the overlay is drawn in
    fractions too.

    The numbers are the per-edge average of the two readings, so the arithmetic is
    asserted from the raw pixel counts: four floats in a source file are exactly what
    gets edited later by somebody without the reading in front of them.
    """

    #: the two readings, as given
    A = (747, 1040, {"left": 32, "right": 27, "top": 39, "bottom": 71})
    B = (455, 642, {"left": 20, "right": 17, "top": 23, "bottom": 43})

    @classmethod
    def _fracs(cls, edge: str) -> tuple[float, float]:
        return tuple(  # type: ignore[return-value]
            px[edge] / (h if edge in ("top", "bottom") else w)
            for w, h, px in (cls.A, cls.B)
        )

    @classmethod
    def _avg(cls, edge: str) -> float:
        a, b = cls._fracs(edge)
        return (a + b) / 2

    @property
    def spec(self) -> frames.FrameGuide:
        return frames.SHIPPED[GuideId.POKEMON_ECARD_EX.value]

    @pytest.mark.parametrize(
        ("edge", "index"),
        [("top", 0), ("right", 1), ("bottom", 2), ("left", 3)],
    )
    def test_every_edge_is_the_average_of_the_two_readings(
        self, edge: str, index: int
    ) -> None:
        assert self.spec.inset[index] == pytest.approx(self._avg(edge), abs=5e-6)

    @pytest.mark.parametrize("edge", ["top", "right", "bottom", "left"])
    def test_the_two_readings_reproduce(self, edge: str) -> None:
        """The premise of averaging them rather than picking one: every edge agrees
        within 0.17pp across images whose widths differ by 1.64x, which is the same
        argument the e-Card asymmetry rests on."""
        a, b = self._fracs(edge)
        assert abs(a - b) * 100 < 0.17

    def test_the_stated_totals_corroborate_the_edges(self) -> None:
        """Three of the four were given independently and land exactly: 59px of 747
        and 37px of 455 across, 110px of 1040 down. (The fourth, card 2's vertical
        total, is given as 65 where its own edges sum to 66 — a 1px slip worth 0.14mm,
        which is why the *edges* are what is stored.)"""
        assert self.A[2]["left"] + self.A[2]["right"] == 59
        assert self.B[2]["left"] + self.B[2]["right"] == 37
        assert self.A[2]["top"] + self.A[2]["bottom"] == 110

    def test_only_the_bottom_is_deep(self) -> None:
        """The finding. Three ordinary edges and one strip — so this cannot be the
        e-Card spec with a different top, and a fit against that one would be wrong on
        the left by more than the border it is placing."""
        top, right, bottom, left = self.spec.mm()
        assert bottom == pytest.approx(6.01, abs=0.05)
        assert max(top, right, left) < 3.4
        ecard = frames.SHIPPED[GuideId.POKEMON_ECARD.value]
        assert ecard.mm()[3] - left > 4.0

    def test_the_sides_are_not_collapsed(self) -> None:
        """Most specs here average opposite edges, because that is what makes a
        cutting error cancel. Both cards read `left` wider than `right` by the same
        0.67pp, and a difference that reproduces in one direction at two scales is not
        a cutting error."""
        assert self.spec.inset[3] > self.spec.inset[1]
        for w, _h, px in (self.A, self.B):
            assert (px["left"] - px["right"]) / w * 100 == pytest.approx(0.67, abs=0.05)

    def test_the_black_star_promos_resolve_to_it(self, library: Library) -> None:
        found = specs.resolve(specs.load(library.root), "np-9", "np", POKEMON)
        assert (spec_of(found), found.via) == (GuideId.POKEMON_ECARD_EX.value, Via.ERA)

    def test_it_is_what_a_fit_uses_with_nothing_chosen(self, library: Library) -> None:
        """The set holds a second frame — the cards printed with no dot code — so this
        is a default rather than the only answer. It is first on weight of evidence
        (two cards against one), not on a claim about which is commoner."""
        found = specs.resolve(specs.load(library.root), "np-9", "np", POKEMON)
        assert spec_of(found) == GuideId.POKEMON_ECARD_EX.value
        assert [c.spec.id for c in found.alternatives] == [
            GuideId.POKEMON_EX_PLAIN.value
        ]

    #: the five sets that carry the dot code, or carried it on some cards: Ruby &
    #: Sapphire, Sandstorm, Dragon, Team Magma vs Team Aqua, and the promos beside them
    DOT_CODE_SETS = ("ex1", "ex2", "ex3", "ex4", "np")

    def test_it_covers_the_five_dot_code_sets(self) -> None:
        """2003-07 to 2004-03. The strip outlived the e-Card sets by four sets, and both
        ex-era shapes apply to every one of them, because a set printed cards with the
        strip and cards without it."""
        for set_id in self.DOT_CODE_SETS:
            assert frames.baselines(set_id, POKEMON) == (
                GuideId.POKEMON_ECARD_EX,
                GuideId.POKEMON_EX_PLAIN,
            ), set_id

    def test_the_ecard_sets_keep_their_own_two_frames(self) -> None:
        """The ex-era shapes are not offered there: those sets carry the strip down the
        left as well, which is a third and fourth shape, already measured."""
        assert frames.baselines("ecard3", POKEMON) == (
            GuideId.POKEMON_ECARD,
            GuideId.POKEMON_ECARD_DEEP_TOP,
        )

    def test_the_strip_is_a_fact_about_these_five_printings_only(self) -> None:
        """`pokemon-ecard-ex` must not reach past `ex4`. The rest of the series answers
        (with the plain border, below), but never with the dot-code shape — fitting a
        plain card to it would ask 6.01mm of bottom border where the card has 2.66."""
        for set_id in ("ex5", "ex9", "ex16", "tk1a", "pop1"):
            assert GuideId.POKEMON_ECARD_EX not in frames.baselines(set_id, POKEMON)

    def test_a_set_id_is_matched_exactly_and_not_by_prefix(self) -> None:
        """The reason `Baseline.sets` is no longer a prefix list, pinned on an era where
        the answer really does stop.

        `ex10`-`ex16` are claimed now — deliberately, by sixteen strings somebody wrote
        down — so the prefix trap is pinned one era earlier instead, where nothing may
        answer: `neo1` is read, `neo1x` is not a set at all. That substitution is the
        point rather than a weakening; what a prefix key cost was the *visibility* of a
        claim, and `pop1` sitting beside `ex16` in the table is what shows it.
        """
        assert frames.baselines("neo1", POKEMON) == (GuideId.POKEMON_WOTC,)
        assert frames.baselines("neo1x", POKEMON) == ()
        assert frames.baselines("ex16", POKEMON)
        assert frames.baselines("ex16x", POKEMON) == ()
        # POP series ids also begin with a claimed letter run and were never read
        assert frames.baselines("pop1", POKEMON) == ()

    def test_both_shapes_are_offered_on_an_ex_card(self, library: Library) -> None:
        """Which is the whole point of covering these sets with two specs: a Ruby &
        Sapphire card may or may not carry the strip, and only a person can say."""
        found = specs.resolve(specs.load(library.root), "ex1-1", "ex1", POKEMON)
        assert found.ambiguous
        assert [spec_of(found), *(c.spec.id for c in found.alternatives)] == [
            GuideId.POKEMON_ECARD_EX.value,
            GuideId.POKEMON_EX_PLAIN.value,
        ]


class TestThePromoSetAlsoHoldsASquareBorder:
    """`pokemon-ex-plain` — the plainest spec here, and the fourth e-Reader-era frame.

    One card, 554×769, **23px on all four edges**. No dot code, so nothing to be
    asymmetric about: `np` holds cards printed with the strip and cards printed without
    it, which is a difference in what was printed rather than in how it was cut.
    """

    W, H, PX = 554, 769, 23

    @property
    def spec(self) -> frames.FrameGuide:
        return frames.SHIPPED[GuideId.POKEMON_EX_PLAIN.value]

    def test_it_is_the_reading_divided_by_its_own_file(self) -> None:
        """The house rule: a spec is the pixel count over the width of the image it was
        read off, never rounded through a millimetre."""
        top, right, bottom, left = self.spec.inset
        assert top == pytest.approx(self.PX / self.H, abs=5e-7)
        assert bottom == pytest.approx(self.PX / self.H, abs=5e-7)
        assert right == pytest.approx(self.PX / self.W, abs=5e-7)
        assert left == pytest.approx(self.PX / self.W, abs=5e-7)

    def test_opposite_edges_are_the_same_number(self) -> None:
        """Not "within a tolerance of": the same float, because one number was read
        for each axis — the collapse every spec does except the e-Reader ones."""
        top, right, bottom, left = self.spec.inset
        assert top == bottom
        assert right == left

    def test_the_border_is_square_in_millimetres_too(self) -> None:
        """Worth pinning because it does not follow from the above: 2.991% and 4.152%
        are different fractions and only land on one width once each is taken of its own
        axis. The 0.02mm between them is this file's aspect sitting 0.85% wide of the
        card's, which is what a genuinely square border read off it looks like."""
        top, right, bottom, left = self.spec.mm()
        assert max(top, right, bottom, left) - min(top, right, bottom, left) < 0.03
        assert top == pytest.approx(2.66, abs=0.01)
        assert right == pytest.approx(2.64, abs=0.01)

    def test_it_is_thinner_than_every_other_pokemon_spec(self) -> None:
        """WOTC's yellow is 3.45/3.15 and the strip specs are deeper still; this is
        nearer `mtg-m15`. A card of it fitted to `pokemon-wotc` would gain ~0.5mm of
        border on every edge."""
        mine = max(self.spec.mm())
        others = [
            max(frames.SHIPPED[g.value].mm())
            for g in (
                GuideId.POKEMON_WOTC,
                GuideId.POKEMON_ECARD,
                GuideId.POKEMON_ECARD_DEEP_TOP,
                GuideId.POKEMON_ECARD_EX,
            )
        ]
        assert mine < min(others)

    def test_it_has_no_strip(self) -> None:
        """The distinguishing fact. Every other Pokémon spec measured since the e-Card
        sets carries one edge far deeper than the rest; here no edge is 1.5x another in
        millimetres, and the same set's strip spec has a bottom twice this one's."""
        mm = self.spec.mm()
        assert max(mm) / min(mm) < 1.5
        strip = frames.SHIPPED[GuideId.POKEMON_ECARD_EX.value]
        assert strip.mm()[2] / mm[2] > 2.0

    def test_both_promo_frames_are_offered(self, library: Library) -> None:
        """Nothing in the metadata says whether a promo carries the dot code, so the
        only workable answer is to name both — the e-Card shape exactly."""
        found = specs.resolve(specs.load(library.root), "np-9", "np", POKEMON)
        assert found.ambiguous
        assert [spec_of(found), *(c.spec.id for c in found.alternatives)] == [
            GuideId.POKEMON_ECARD_EX.value,
            GuideId.POKEMON_EX_PLAIN.value,
        ]

    def test_picking_it_fits_against_it(self, library: Library) -> None:
        found = specs.resolve(
            specs.load(library.root),
            "np-9",
            "np",
            POKEMON,
            pin=GuideId.POKEMON_EX_PLAIN.value,
        )
        assert (spec_of(found), found.via) == (GuideId.POKEMON_EX_PLAIN.value, Via.PIN)
        assert [c.spec.id for c in found.alternatives] == [
            GuideId.POKEMON_ECARD_EX.value
        ]


class TestTheRestOfTheExSeriesInheritsThePlainBorder:
    """`ex5`-`ex16` and the four Trainer Kits take `pokemon-ex-plain` — and only it.

    The dot code stops after `ex4`, so from Hidden Legends (2005-06) to Power Keepers
    (2007-05) there is one shape and nothing to pick between. That is the fact worth
    pinning twice over, because it is **not** a measurement: sixteen set ids rest on
    the one `np` card, inherited on the grounds of same era, same operation, same border
    with the strip left off — the standing `basep` has on `pokemon-wotc`, recorded as
    inherited in `docs/measuring-frames.md` rather than passing for a reading.

    So the test is about the *decision* rather than about numbers: which sets it claims,
    that it claims them **alone** (offering the dot-code shape here would ask 6.01mm of
    bottom border where the card has 2.66), and that it stops where the reading's
    grounds stop — Diamond & Pearl still refuses.
    """

    INHERITED = (
        "ex5",
        "ex6",
        "ex7",
        "ex8",
        "ex9",
        "ex10",
        "ex11",
        "ex12",
        "ex13",
        "ex14",
        "ex15",
        "ex16",
        "tk1a",
        "tk1b",
        "tk2a",
        "tk2b",
    )

    def test_every_one_of_them_answers_with_the_plain_border(self) -> None:
        for set_id in self.INHERITED:
            assert frames.baselines(set_id, POKEMON) == (GuideId.POKEMON_EX_PLAIN,), (
                set_id
            )

    def test_it_is_their_only_candidate(self, library: Library) -> None:
        """Unlike `ex1`-`ex4` and `np`, where two borders genuinely coexist and a person
        picks. Here there is nothing to choose, so nothing is offered — and a card of
        these sets borders without a decision."""
        for set_id in self.INHERITED:
            found = specs.resolve(
                specs.load(library.root), f"{set_id}-1", set_id, POKEMON
            )
            assert spec_of(found) == GuideId.POKEMON_EX_PLAIN.value, set_id
            assert not found.ambiguous, set_id
            assert found.sure, set_id

    def test_it_is_the_same_spec_np_was_read_from(self) -> None:
        """One reading, not a copy of it. A second entry with the same numbers typed
        again is one a later correction updates in only one place."""
        assert GuideId.POKEMON_EX_PLAIN in frames.baselines("np", POKEMON)

    def test_it_stops_where_the_reading_s_grounds_stop(self) -> None:
        """The claim is "the ex era, with the strip left off", so it may not run on
        into the era after it. Diamond & Pearl (2007-05) is a different frame nobody has
        read, and inheriting *again* from here would be a guess built on a guess."""
        for set_id in ("dp1", "dp2", "pop1", "pop5", "hgss1"):
            assert frames.baselines(set_id, POKEMON) == (), set_id
