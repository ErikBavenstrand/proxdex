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


def add(root: Path, spec_id: str, game: GameId = POKEMON) -> None:
    """A library spec whose numbers are distinguishable from every shipped one."""
    specs.save(
        root,
        specs.spec(
            spec_id, spec_id.title(), game, (4.0, 4.0, 4.0, 4.0), "for the test"
        ),
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
        # 4mm of a 63×88 card, back out of the fractions it was stored as
        assert [round(v, 2) for v in spec.mm()] == [4.0, 4.0, 4.0, 4.0]

    def test_a_stored_spec_may_correct_a_shipped_one(self, library: Library) -> None:
        """Half the reason this exists is fixing a number we shipped wrong — and for
        the MTG specs it is the *expected* path, since what ships is provisional."""
        specs.save(
            library.root,
            specs.spec(
                GuideId.MTG_2003.value,
                "",
                MTG,
                (2.5, 2.5, 2.5, 2.5),
                "calipers on a real card",
            ),
        )
        spec = specs.load(library.root).get(GuideId.MTG_2003)
        assert spec is not None
        assert round(spec.mm()[0], 2) == 2.5
        # the file gave no name, so the shipped one is kept rather than showing an id
        assert spec.name == frames.SHIPPED[GuideId.MTG_2003].name

    def test_a_measurement_records_the_card_it_was_taken_from(self) -> None:
        """A real card is 63.5×88.9mm, not the 63×88 proxdex trims to, so the same
        2.4mm border is a *different fraction* depending on what was measured.
        Getting this wrong is a 0.8% error in every border, on every card."""
        real = specs.spec("x", "", MTG, (2.4, 2.4, 2.4, 2.4), "", (63.5, 88.9))
        nominal = specs.spec("x", "", MTG, (2.4, 2.4, 2.4, 2.4))
        assert real.inset[1] < nominal.inset[1]
        # and it reads back as the millimetres that went in, of that card
        assert [round(v, 3) for v in real.mm()] == [2.4, 2.4, 2.4, 2.4]

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

    def test_a_spec_is_its_numbers_and_a_note_and_nothing_else(self) -> None:
        """There is deliberately no confidence level. Three of them existed once,
        and the middle one — "read off the publisher's scans" — graded a reading
        that inherits the scan's crop as trustworthy. A crop that trims 0.3mm inside
        the cut edge shrinks every border read from it by 0.3mm, and no sample size
        and no agreement between cards detects that, because it is systematic. So
        the prose says what happened and no field claims to rank it."""
        made = specs.spec("mtg-x", "", MTG, (3, 3, 3, 3), " calipers on mir-1 ")
        assert made.note == "calipers on mir-1"
        assert not hasattr(made, "confidence")
        assert not hasattr(made, "origin")


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

    def test_one_default_per_set(self, library: Library) -> None:
        add(library.root, "pokemon-swsh")
        add(library.root, "pokemon-other")
        specs.assign(library.root, "pokemon-swsh", POKEMON, "swsh4", Match.SET)
        specs.assign(library.root, "pokemon-other", POKEMON, "swsh4", Match.SET)
        reg = specs.load(library.root)
        assert [r.spec for r in reg.rules] == ["pokemon-other"]

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

    def test_a_set_nothing_knows_falls_back_and_says_so(self, library: Library) -> None:
        found = specs.resolve(specs.load(library.root), "swsh4-1", "swsh4", POKEMON)
        assert found.via is Via.FALLBACK
        assert found.spec.id == GuideId.POKEMON_GENERIC.value

    def test_a_shipped_era_rule_answers_for_the_sets_it_covers(
        self, library: Library
    ) -> None:
        found = specs.resolve(specs.load(library.root), "base1-4", "base1", POKEMON)
        assert found.via is Via.ERA
        assert found.spec.id == GuideId.POKEMON_WOTC.value

    def test_mtg_splits_on_the_frame_generation_not_the_set(
        self, library: Library
    ) -> None:
        """MTG's border narrowed by almost a millimetre with the M15 frame, and a
        modern set can hold retro-frame cards at the old width — so the shipped
        baseline reads the *printing's* frame, which a set code cannot answer."""
        reg = specs.load(library.root)
        modern = specs.resolve(reg, "neo-136", "neo", MTG, traits={"frame": "2015"})
        retro = specs.resolve(reg, "dmr-1", "dmr", MTG, traits={"frame": "1997"})
        assert modern.spec.id == GuideId.MTG_M15.value
        assert retro.spec.id == GuideId.MTG_1997.value
        assert modern.via is retro.via is Via.ERA
        # the whole point: two different numbers, and the gap is far bigger than
        # the measurement error either of them carries
        assert round(retro.spec.mm()[1] - modern.spec.mm()[1], 2) == 0.60

    def test_every_mtg_frame_generation_maps_to_its_own_spec(
        self, library: Library
    ) -> None:
        """Every documented generation maps to a spec that exists. `future` shares
        the 2003 one, and that is a measurement rather than a shortcut: surveyed over
        226 printings it reads 2.94 top / 2.95 sides / 2.99 bottom against 2003's
        2.96 / 2.92 / 2.94. Two specs carrying the same four numbers would be two
        things to measure for no difference on paper."""
        reg = specs.load(library.root)
        got = {
            gen: specs.resolve(reg, "x-1", "x", MTG, traits={"frame": gen}).spec.id
            for gen in ("1993", "1997", "2003", "2015", "future")
        }
        assert got == {
            "1993": GuideId.MTG_1993.value,
            "1997": GuideId.MTG_1997.value,
            "2003": GuideId.MTG_2003.value,
            "2015": GuideId.MTG_M15.value,
            "future": GuideId.MTG_2003.value,
        }

    def test_every_frame_scryfall_documents_is_covered(self) -> None:
        """Scryfall documents exactly five `frame` values, and this is the list.

        If a sixth generation ships, this is what says so — rather than every card
        of it silently taking the fallback, which is a different border and no
        warning. The five are quoted from Scryfall's own docs:
        1993 (Alpha), 1997 (Mirage), 2003 (8th Edition), 2015 (Magic 2015), future.
        """
        documented = {"1993", "1997", "2003", "2015", "future"}
        assert set(frames.FRAME_GENERATIONS[MTG]) == documented
        reg = specs.load(Path("/nonexistent"))
        for generation in documented:
            found = specs.resolve(
                reg, "x-1", "x", MTG, traits={frames.FRAME_TRAIT: generation}
            )
            # every one resolves to a real spec, by the baseline and not the fallback
            assert found.via is Via.ERA, generation
            assert found.spec.id in reg.specs, generation

    def test_every_frame_generation_maps_to_a_spec_that_exists(self) -> None:
        """One spec per documented generation, no aliasing. `future` used to share
        the 2003 spec, which meant a Future Sight timeshift silently claimed a
        border 0.07mm wider than the one it has."""
        ids = set(frames.FRAME_GENERATIONS[MTG].values())
        assert all(i in frames.SHIPPED for i in ids)

    def test_the_mtg_specs_carry_the_shape_the_survey_found(self) -> None:
        """The *shape* of the answer rather than the numbers, which is the part a
        scan survey can establish even though it cannot fix the absolute width: 1993
        and 1997 thicken the top and bottom over the sides, 2003's redesign made all
        four equal, and M15 took ~0.55mm off everything. Each is a visible difference
        on cut paper, so a caliper reading that contradicts one of these is worth a
        second look before it is stored."""
        reg = specs.load(Path("/nonexistent"))
        mm = {k: reg.specs[k].mm() for k in reg.specs}
        for old in (GuideId.MTG_1993.value, GuideId.MTG_1997.value):
            top, side, bottom, _ = mm[old]
            assert top == bottom, old  # symmetric top to bottom
            assert top - side > 0.3, old  # and thicker than the sides
        even = mm[GuideId.MTG_2003.value]
        assert max(even) - min(even) < 0.05  # the 2003 redesign: all four equal
        assert mm[GuideId.MTG_2003.value][1] - mm[GuideId.MTG_M15.value][1] > 0.5

    def test_every_shipped_spec_says_where_its_numbers_came_from(self) -> None:
        """The note is the only account a spec has of how much to trust it, now that
        there is no confidence grade — so a shipped spec without one would be four
        numbers from nowhere."""
        for spec in frames.SHIPPED.values():
            assert spec.note.strip(), spec.id

    def test_a_border_colour_rule_catches_a_decorative_band(
        self, library: Library
    ) -> None:
        """Colour is not geometry — white, silver and gold measure at their
        generation's width — but a *band* is: Aetherdrift's yellow full-art box
        toppers measure 4.70mm against an ordinary 2.45. Too niche to ship, exactly
        the right size for a rule."""
        specs.save(
            library.root,
            specs.spec("mtg-yellow-band", "Yellow band", MTG, (4.2, 4.7, 4.2, 4.7)),
        )
        specs.assign(
            library.root, "mtg-yellow-band", MTG, "dft", Match.BORDER, "yellow"
        )
        reg = specs.load(library.root)
        topper = specs.resolve(
            reg, "dft-511", "dft", MTG, traits={"frame": "2015", "border": "yellow"}
        )
        plain = specs.resolve(
            reg, "dft-1", "dft", MTG, traits={"frame": "2015", "border": "black"}
        )
        assert (topper.spec.id, topper.via) == ("mtg-yellow-band", Via.RULE)
        assert plain.spec.id == GuideId.MTG_M15.value

    def test_an_mtg_card_with_no_recorded_frame_takes_the_commonest_frame(
        self, library: Library
    ) -> None:
        """A card filed before proxdex recorded traits has no frame to read, so it
        takes the M15 spec: two thirds of all MTG prints carry that frame, which
        makes it the least-wrong answer to "no idea". It is still reported as a
        fallback, so `frames check` names the card and a re-fetch settles it."""
        found = specs.resolve(specs.load(library.root), "neo-136", "neo", MTG)
        assert found.via is Via.FALLBACK
        assert found.spec.id == GuideId.MTG_M15.value

    def test_a_library_rule_beats_the_frame_generation(self, library: Library) -> None:
        specs.save(
            library.root, specs.spec("mtg-mine", "Mine", MTG, (2.0, 2.0, 2.0, 2.0))
        )
        specs.assign(library.root, "mtg-mine", MTG, "neo", Match.SET)
        found = specs.resolve(
            specs.load(library.root), "neo-136", "neo", MTG, traits={"frame": "2015"}
        )
        assert (found.spec.id, found.via) == ("mtg-mine", Via.SET_DEFAULT)

    def test_a_library_rule_beats_the_era(self, library: Library) -> None:
        add(library.root, "pokemon-vintage")
        specs.assign(library.root, "pokemon-vintage", POKEMON, "base1", Match.SET)
        found = specs.resolve(specs.load(library.root), "base1-4", "base1", POKEMON)
        assert found.via is Via.SET_DEFAULT
        assert found.spec.id == "pokemon-vintage"
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
        assert (ordinary.spec.id, ordinary.via) == ("pokemon-swsh", Via.SET_DEFAULT)
        assert (secret.spec.id, secret.via) == ("pokemon-secret", Via.RULE)
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
        assert (printed.via, printed.spec.id) == (Via.PRINTING, "borderless")
        pinned = specs.resolve(
            reg,
            "swsh4-190",
            "swsh4",
            POKEMON,
            printing=GuideId.BORDERLESS.value,
            pin="pokemon-swsh",
        )
        assert (pinned.via, pinned.spec.id) == (Via.PIN, "pokemon-swsh")

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
        assert (found.via, found.spec.id) == (Via.OVERRIDE, "pokemon-swsh")

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
        assert blind.spec.id == "pokemon-swsh"  # it took the default meanwhile
        assert not blind.sure
        assert "traits" in blind.note
        seeing = specs.resolve(
            reg, "swsh4-190", "swsh4", POKEMON, traits={"rarity": "Rare Secret"}
        )
        assert (seeing.spec.id, seeing.via, seeing.undecided) == (
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
        assert specs.resolve(reg, "neo-1", "neo", POKEMON).spec.id == "pokemon-neo"
        assert (
            specs.resolve(reg, "neo-136", "neo", MTG).spec.id == GuideId.MTG_M15.value
        )


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

    def test_a_provisional_spec_is_not_a_warning(self, library: Library) -> None:
        """The whole point of dropping the confidence levels: every shipped MTG
        number is provisional, so grading them would warn about every card."""
        reg = specs.load(library.root)
        found = specs.resolve(reg, "m15-1", "m15", MTG, traits={"frame": "2015"})
        assert found.via is Via.ERA
        assert specs.audit(reg, [("m15-1", found)]) == []

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

    def test_a_printing_nothing_knows_is_reported_once(self, library: Library) -> None:
        """An MTG card filed before proxdex recorded traits: it still borders, off
        the commonest frame, and the report names it so a re-fetch settles it."""
        reg = specs.load(library.root)
        found = specs.resolve(reg, "neo-136", "neo", MTG)
        (issue,) = specs.audit(reg, [("neo-136", found)])
        assert issue.fault is specs.Fault.UNKNOWN
        assert GuideId.MTG_M15.value in issue.detail

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
            assert (found.spec.id, found.via) == ("mtg-extended", Via.RULE), set_id

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
        assert found.spec.id != "mtg-extended"

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
        assert specs.resolve(reg, "ltr-20", "ltr", MTG, traits=traits).spec.id == (
            "mtg-mine"
        )
        # and outside that range the global rule still answers
        assert specs.resolve(reg, "ltr-90", "ltr", MTG, traits=traits).spec.id == (
            "mtg-extended"
        )

    def test_a_sets_default_beats_a_game_wide_default(self, library: Library) -> None:
        add(library.root, "mtg-mine", MTG)
        specs.assign(library.root, "mtg-mine", MTG, "ltr", Match.SET)
        reg = specs.load(library.root)
        assert reg.for_set(MTG, "ltr")[0].set_id == "ltr"

    def test_a_whole_set_rule_with_no_set_is_refused(self, library: Library) -> None:
        """It would claim every card of the game, which is what the game's own
        fallback spec already is — a rule that restates the default only adds a
        place for the two to disagree."""
        add(library.root, "mtg-mine", MTG)
        with pytest.raises(ProxdexError, match="every card"):
            specs.assign(library.root, "mtg-mine", MTG, "", Match.SET)

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


class TestMeasuredVariants:
    """The two treatments that change the geometry, out of the ~26 that exist.

    `scripts/mtg-variants.py` measured all 54 (frame × border_color × frame_effects)
    combinations with 20+ printings. **31 came out at their own generation's border**
    — a legendary crown, an inverted text box, the Nyx enchantment treatment, an
    etched foil, `snow`, `devoid`, `miracle`, `fullart` — so they need no spec and no
    rule, and that is now measured rather than assumed. These two do not.
    """

    def test_extended_art_has_no_side_borders(self) -> None:
        """The shape no four-edge inset described before: the art runs off the left
        and right card edges while the top and bottom keep the generation's border.
        Left as an ordinary M15 card it printed a 2.45mm border where there is none.
        """
        spec = frames.SHIPPED[GuideId.MTG_EXTENDED_ART.value]
        top, right, bottom, left = spec.mm()
        assert (right, left) == (0.0, 0.0)
        assert top > 2.0
        assert bottom > 2.0
        # and it is *not* frameless — a frameless spec fits the aspect and nothing
        # else, which would throw away the top and bottom border it really has
        assert not spec.frameless

    def test_the_printing_settles_extended_art_and_the_yellow_band(self) -> None:
        """Read off the card object, like `borderless` — a fact the provider stated
        about this printing rather than a guess about its set, so it resolves at
        `Via.PRINTING`, above any rule and below a pin."""
        assert (
            sources.mtg_frame(
                {"border_color": "black", "frame_effects": ["extendedart"]}
            )
            == GuideId.MTG_EXTENDED_ART.value
        )
        assert (
            sources.mtg_frame({"border_color": "yellow", "frame_effects": ["inverted"]})
            == GuideId.MTG_YELLOW_BAND.value
        )

    def test_borderless_still_wins_over_both(self) -> None:
        """A borderless extended-art card has no border at all, so the treatment must
        not talk it into having a top and bottom one."""
        assert (
            sources.mtg_frame(
                {"border_color": "borderless", "frame_effects": ["extendedart"]}
            )
            == GuideId.BORDERLESS.value
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
        """White, gold and silver all measure at their generation's width. Yellow is
        the single exception, and it is a decorative *band*, not a border colour."""
        for colour in ("white", "gold", "silver", "black"):
            assert sources.mtg_frame({"border_color": colour}) is None
