"""A game a library defines: what it can do, and the four ways it must refuse.

This earns a file because making the game id an **open set** moved a whole class of
decision from the type system into code, and each of those decisions is invisible
until a card is on paper or a request goes to the wrong host:

* **A provider-less game must never fall through to Scryfall.** While there were two
  games every dispatch was ``if pokemon: … else: <the Magic one>``, total only by
  accident of the count. A third game reaching that ``else`` gets Scryfall's answer
  for an id Scryfall has never heard of — which is a 404 that reads exactly like a
  mistyped Magic card, so the report names the wrong problem. Worse, a *coincidental*
  hit files a Magic scan under a custom game's id and nothing ever mentions it again.
* **A game id is compared with ``==``, never ``is``.** As a ``StrEnum`` every id was
  an interned singleton, so identity comparisons worked and read fine; the moment an
  id comes out of a file they answer ``False`` for every custom game. Silently: a card
  simply never finds the set folder it belongs to.
* **A stored game may not shadow a shipped one**, or "my Pokémon cards stopped
  resolving" is the bug report.
* **A set is declared, and an undeclared one is refused at import.** With no provider
  to contradict a typo, ``tfcc-9`` would file happily into a brand-new set called
  ``tfcc`` and read as a clean import.

The reader over ``games/*.json`` is total for the reason every other reader here is:
it is asked in order to draw a screen or name a game in a table, long before anyone
has checked that the file still parses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from proxdex import games, imports, sources
from proxdex.cli import cli
from proxdex.config import Config
from proxdex.errors import NoProviderError
from proxdex.games import GameId, ProviderId
from proxdex.library import Library, Stage


def define(
    root: Path, game_id: str = "lorcana", name: str = "Disney Lorcana"
) -> games.Game:
    """Store a custom game the way ``proxdex game add`` does."""
    game = games.custom(game_id, name)
    games.store(root, game)
    return game


class TestWhatAGameIs:
    def test_the_two_shipped_games_have_providers(self) -> None:
        assert games.POKEMON.provider is ProviderId.POKEMONTCG
        assert games.MTG.provider is ProviderId.SCRYFALL
        assert not games.POKEMON.custom
        assert not games.MTG.custom

    def test_a_custom_game_has_none_and_says_so(self) -> None:
        mine = games.custom("lorcana", "Disney Lorcana")
        assert mine.provider is None
        assert mine.custom
        assert "import" in mine.source

    def test_the_closed_enum_is_only_the_shipped_ids(self) -> None:
        """`GameId` is what *code* names, not the type of a game id.

        If a member is ever added here without a provider and shipped frame specs
        written against it, `sources.provider` will raise for a game the enum claims
        proxdex supports.
        """
        assert {g.value for g in GameId} == {"pokemon", "mtg"}
        assert set(games.RESERVED) == {"pokemon", "mtg"}

    def test_the_id_example_comes_from_the_first_declared_set(self) -> None:
        """A card id is `<set>-<number>`, so a game-level example cannot be one.

        A fresh custom game read `e.g. lorcana-1`, which is not an id anything would
        accept once its sets are `tfc`, `rfb`, … — so it is derived, and only a stated
        example wins.
        """
        bare = games.custom("lorcana", "Disney Lorcana")
        assert bare.example == "lorcana-1"
        withset = games.with_set(bare, games.SetSpec("tfc", "The First Chapter"))
        assert withset.example == "tfc-1"
        stated = games.custom("lorcana", "Disney Lorcana", id_example="TFC/001")
        assert games.with_set(stated, games.SetSpec("tfc", "x")).example == "TFC/001"


class TestNothingFallsThroughToTheWrongProvider:
    """The single most expensive thing this feature could have got wrong."""

    def test_a_custom_game_raises_rather_than_asking_scryfall(self) -> None:
        with pytest.raises(NoProviderError) as caught:
            games.require_provider("lorcana")
        # the refusal has to name the way out, and the way out is not a retry
        assert "import" in str(caught.value)

    def test_the_shipped_games_still_resolve(self) -> None:
        assert games.require_provider("pokemon") is ProviderId.POKEMONTCG
        assert games.require_provider("mtg") is ProviderId.SCRYFALL

    def test_an_id_nothing_knows_is_refused_not_guessed(self) -> None:
        """The dangerous input: not a declared game, not a shipped one, just wrong.

        This is the case the ``else`` swallowed. It must never come back as a Magic
        lookup.
        """
        for wrong in ("mtgg", "poke", "", "yugioh"):
            with pytest.raises(NoProviderError):
                games.require_provider(wrong)

    def test_sources_dispatches_through_the_same_refusal(self) -> None:
        """`sources.provider` *is* `games.require_provider`, not a second copy.

        Two copies is the split that lets one of them grow an ``else`` back.
        """
        assert sources.provider is games.require_provider

    def test_every_provider_has_a_row_in_every_dispatch_table(self) -> None:
        """A missing row is a `KeyError` here rather than a silent Magic lookup.

        This is the completeness claim the ``if/else`` used to make implicitly, so it
        is asserted rather than trusted — adding a provider without wiring one of its
        five questions fails the suite.
        """
        assert sources.TABLES
        for table in sources.TABLES:
            assert set(table) == set(ProviderId)

    def test_looking_up_an_unknown_game_does_not_try_a_custom_one(
        self, tmp_path: Path
    ) -> None:
        """`lookup_any` walks only games something can be asked about.

        A custom game in that walk would collect an error about a request nobody can
        make, which then appears in the report beside the real ones.
        """
        define(tmp_path)
        order = games.load(tmp_path).provider_order("pokemon")
        assert order == ("pokemon", "mtg")
        assert "lorcana" in games.load(tmp_path).order("pokemon")


class TestTheRegistryIsPerLibrary:
    def test_no_root_is_the_shipped_games_alone(self) -> None:
        assert games.load().ids == ("pokemon", "mtg")

    def test_a_stored_game_joins_them(self, tmp_path: Path) -> None:
        define(tmp_path)
        known = games.load(tmp_path)
        assert known.ids == ("pokemon", "mtg", "lorcana")
        assert known.custom == (known.get("lorcana"),)
        assert known.provided == (games.POKEMON, games.MTG)

    def test_a_stored_game_may_not_shadow_a_shipped_one(self, tmp_path: Path) -> None:
        """Dropped as unreadable rather than allowed to win.

        The failure it prevents is not subtle in effect and completely silent in
        cause: `pokemon.json` taking over would strip every Pokémon card of its
        provider *and* its shipped frame specs, and the only symptom is that
        `border` starts refusing cards that worked yesterday.
        """
        folder = games.dir_for(tmp_path)
        folder.mkdir(parents=True)
        (folder / "pokemon.json").write_text(
            json.dumps({"name": "Not Pokémon"}), encoding="utf-8"
        )
        known = games.load(tmp_path)
        assert known.get("pokemon") is games.POKEMON
        assert known.unreadable == ("pokemon.json",)

    def test_store_refuses_a_shipped_id_outright(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="ships"):
            games.store(tmp_path, games.custom("mtg", "Mine"))

    def test_naming_a_game_never_raises(self, tmp_path: Path) -> None:
        """Total, because it is called to fill a table cell.

        An id nothing answers to reads as the id itself, which is exactly right for a
        card whose game was deleted: `lorcana` says "a game somebody removed", while
        "Pokémon" would be a lie about the card.
        """
        known = games.load(tmp_path)
        assert known.name_of("pokemon") == "Pokémon"
        assert known.name_of("lorcana") == "lorcana"
        assert known.get("lorcana") is None


class TestTheReaderIsTotal:
    """It reads JSON a person hand-edited, so nothing may raise into a screen."""

    def write(self, root: Path, name: str, text: str) -> games.Registry:
        folder = games.dir_for(root)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")
        return games.load(root)

    def test_unparseable_json_is_named_not_raised(self, tmp_path: Path) -> None:
        known = self.write(tmp_path, "lorcana.json", "{not json")
        assert known.unreadable == ("lorcana.json",)
        assert known.ids == ("pokemon", "mtg")

    def test_a_game_with_no_name_is_not_a_game(self, tmp_path: Path) -> None:
        assert self.write(tmp_path, "lorcana.json", "{}").unreadable == (
            "lorcana.json",
        )

    def test_a_file_whose_name_is_not_an_id_is_dropped(self, tmp_path: Path) -> None:
        known = self.write(tmp_path, "Not An Id.json", '{"name": "x"}')
        assert known.unreadable == ("Not An Id.json",)

    def test_the_id_comes_from_the_filename_not_the_body(self, tmp_path: Path) -> None:
        """A body free to disagree gives two answers to "which game is this".

        The filename is what every other reader here trusts (`profiles/` too), so a
        body claiming another id is simply ignored rather than honoured.
        """
        known = self.write(
            tmp_path, "lorcana.json", json.dumps({"id": "mtg", "name": "Mine"})
        )
        assert known.ids == ("pokemon", "mtg", "lorcana")
        assert known.name_of("mtg") == "Magic: The Gathering"

    def test_a_malformed_set_is_dropped_and_the_game_survives(
        self, tmp_path: Path
    ) -> None:
        known = self.write(
            tmp_path,
            "lorcana.json",
            json.dumps(
                {
                    "name": "Disney Lorcana",
                    "sets": [
                        {"id": "tfc", "name": "The First Chapter"},
                        {"id": "", "name": "no id"},
                        "not even an object",
                        {"id": "BAD ID", "name": "spaces"},
                    ],
                }
            ),
        )
        found = known.get("lorcana")
        assert found is not None
        assert [s.id for s in found.sets] == ["tfc"]

    def test_a_duplicate_set_id_keeps_the_first(self, tmp_path: Path) -> None:
        known = self.write(
            tmp_path,
            "lorcana.json",
            json.dumps(
                {
                    "name": "Disney Lorcana",
                    "sets": [
                        {"id": "tfc", "name": "First"},
                        {"id": "tfc", "name": "Second"},
                    ],
                }
            ),
        )
        found = known.get("lorcana")
        assert found is not None
        assert [(s.id, s.name) for s in found.sets] == [("tfc", "First")]

    def test_a_count_nobody_typed_stays_unknown(self, tmp_path: Path) -> None:
        """0 means "not stated", never "zero cards" — the same rule as every other
        number in proxdex, and why a negative or non-integer falls back to it."""
        known = self.write(
            tmp_path,
            "lorcana.json",
            json.dumps(
                {
                    "name": "Disney Lorcana",
                    "sets": [
                        {"id": "a", "name": "A", "total": -5},
                        {"id": "b", "name": "B", "total": "lots"},
                        {"id": "c", "name": "C", "total": 204},
                    ],
                }
            ),
        )
        found = known.get("lorcana")
        assert found is not None
        assert [s.total for s in found.sets] == [0, 0, 204]


class TestIdsAreComparedByValue:
    """The landmine the open set introduced, and the only defence is a test.

    `read_game` returns a plain string. Every comparison against it therefore has to
    be `==`: with the ids a `StrEnum` each was an interned singleton, so `is` worked
    and read perfectly well, and would have started answering `False` for every custom
    game the moment the id came out of a file — putting each card in a folder of its
    own instead of its set's, with nothing said about it.
    """

    def test_a_set_folder_is_reused_for_the_same_custom_game(
        self, library: Library
    ) -> None:
        first = library.set_dir("tfc", "The First Chapter", "lorcana")
        again = library.set_dir("tfc", "The First Chapter", "lorcana")
        assert first == again

    def test_two_games_sharing_a_set_code_get_two_folders(
        self, library: Library
    ) -> None:
        mine = library.set_dir("neo", "My Neo", "lorcana")
        theirs = library.set_dir("neo", "Kamigawa", "mtg")
        assert mine != theirs

    def test_the_library_listing_filters_by_game(self, library: Library) -> None:
        """``ls --game`` — the one comparison the first sweep of this missed, and the
        symptom was total: **"no cards match" for every game**, both built-in ones
        included, because a `str` read off disk is never the same object as the one
        click parsed. It read perfectly well as ``card.game is not game``, and it needed
        a filed card of a known game to see, which is why this class of bug is pinned
        rather than reviewed. Driven through the CLI, since the filter is the answer a
        person gets and not a function this module should reach into.
        """
        define(library.root)
        folder = library.set_dir("tfc", "The First Chapter", "lorcana")
        card = folder / "tfc-1_a-card"
        card.mkdir()
        (card / ".game").write_text("lorcana\n", encoding="utf-8", newline="\n")
        (card / "tfc-1_1_original.png").write_bytes(b"")

        def listed(*args: str) -> str:
            out = CliRunner().invoke(
                cli, ["--root", str(library.root), "ls", *args], catch_exceptions=False
            )
            assert out.exit_code == 0, out.output
            return out.output

        assert "tfc-1" in listed("--game", "lorcana")
        assert "tfc-1" in listed()
        assert "tfc-1" not in listed("--game", "pokemon")

    def test_the_marker_round_trips_a_custom_id(self, library: Library) -> None:
        from proxdex.library import read_game

        folder = library.set_dir("tfc", "The First Chapter", "lorcana")
        assert read_game(folder, "pokemon") == "lorcana"

    def test_a_marker_naming_a_deleted_game_is_not_coerced(
        self, library: Library
    ) -> None:
        """It describes a real card that really is not Pokémon.

        Rounding it down to the library default would file the card under another
        game's frame specs, which is the same defect as bordering a card against a
        spec nobody measured — it looks perfect and is wrong on paper. So it stays,
        resolves no spec, and `border` refuses it.
        """
        from proxdex.library import read_game

        folder = library.set_dir("tfc", "The First Chapter", "gone-game")
        assert read_game(folder, "pokemon") == "gone-game"
        assert games.load(library.root).get("gone-game") is None


class TestFilingACardWithNoProvider:
    def test_local_meta_carries_no_image_url(self) -> None:
        """The picture arrives through `import`, so there is nothing to download.

        It is still a `CardMeta` so that `_card_from_meta` names the folder and
        `write_kind` records the layout — nothing downstream has to know which of the
        three ways a card was described.
        """
        game = games.with_set(
            games.custom("lorcana", "Disney Lorcana"),
            games.SetSpec("tfc", "The First Chapter"),
        )
        meta = sources.local_meta("tfc-1", game, name="Elsa")
        assert meta.image_url == ""
        assert (meta.id, meta.name, meta.set_id) == ("tfc-1", "Elsa", "tfc")
        # the *declared* set's name, which is the whole reason to declare it — the
        # folder would otherwise be named after the id
        assert meta.set_name == "The First Chapter"
        assert meta.game == "lorcana"

    def test_an_unnamed_card_falls_back_to_its_id(self) -> None:
        game = games.custom("lorcana", "Disney Lorcana")
        assert sources.local_meta("tfc-1", game).name == "tfc-1"

    def test_two_sides_are_a_double_faced_card(self) -> None:
        game = games.custom("lorcana", "Disney Lorcana")
        two = sources.local_meta("tfc-1", game, faces=2)
        assert two.layout is games.Layout.DOUBLE
        assert len(two.faces) == 2
        one = sources.local_meta("tfc-1", game, faces=1)
        assert one.layout is games.Layout.SINGLE
        # a single-faced card's face carries no label anywhere in proxdex
        assert one.faces[0].name == ""

    def test_more_sides_than_proxdex_supports_are_clamped(self) -> None:
        game = games.custom("lorcana", "Disney Lorcana")
        assert len(sources.local_meta("tfc-1", game, faces=9).faces) == 2
        assert len(sources.local_meta("tfc-1", game, faces=0).faces) == 1


class TestTheDataSheetFallsBackToWhatIsKnown:
    """`show` printed **nothing at all** for a card of a custom game.

    Every line it draws is built from a `CardDetail`, so the one part that cannot
    work — asking a provider — took the whole command with it, for a card sitting in
    the library with its name and its stages on disk. The UI's panel had the same
    shape of problem one step milder: a red error box on every card of your own game,
    forever, for a *state* rather than a failure.

    So a provider-less card is described from what proxdex already knows, and the fact
    that it is a real `CardMeta` is what lets the header, the kind line and the local
    state render through exactly the same code as any other card.
    """

    def test_the_local_description_needs_no_provider(self) -> None:
        game = games.with_set(
            games.custom("lorcana", "Disney Lorcana"),
            games.SetSpec("tfc", "The First Chapter"),
        )
        detail = sources.CardDetail(
            meta=sources.local_meta("tfc-1", game, name="Elsa", faces=2),
            source=game.source,
        )
        assert detail.meta.name == "Elsa"
        assert detail.meta.set_name == "The First Chapter"
        assert detail.meta.layout is games.Layout.DOUBLE
        # the provider's half is empty rather than absent, so every reader that walks
        # groups and links simply draws nothing instead of branching
        assert detail.groups == []
        assert detail.links == []
        assert detail.related == ()


class TestAnUndeclaredSetIsRefused:
    """The one check that replaces a provider lookup.

    For the shipped games the lookup is what proves an id exists (and the placeholder
    check backs it up). A custom game has nothing that would ever object, so a typo
    would create a set of one card and read as a clean import — which is the grey
    placeholder's failure mode with a different cause.
    """

    def plan(self, library: Library, cid: str) -> imports.Assignment:
        define(library.root)
        games.store(
            library.root,
            games.with_set(
                games.custom("lorcana", "Disney Lorcana"),
                games.SetSpec("tfc", "The First Chapter"),
            ),
        )
        run = imports.plan(
            library,
            [
                imports.Item(
                    name="scan.png", id=cid, game="lorcana", stage=Stage.ORIGINAL
                )
            ],
        )
        return run.items[0]

    def test_a_declared_set_may_create_a_card(self, library: Library) -> None:
        found = self.plan(library, "tfc-1")
        assert found.disposition is imports.Disposition.CREATE
        assert found.disposition.writes

    def test_a_typo_is_blocked_and_names_what_is_declared(
        self, library: Library
    ) -> None:
        found = self.plan(library, "tfcc-9")
        assert found.disposition is imports.Disposition.UNKNOWN_SET
        assert found.disposition.blocked
        assert not found.disposition.writes
        # the report has to say which ids *are* real, or the fix is a guess
        assert "tfc" in found.reason

    def test_a_shipped_game_is_untouched_by_this_check(self, library: Library) -> None:
        """Pokémon and Magic keep no set list here, so the lookup still decides.

        Checking a declared list for them would refuse every card, since the list is
        empty by design.
        """
        run = imports.plan(
            library,
            [
                imports.Item(
                    name="scan.png", id="base1-4", game="pokemon", stage=Stage.ORIGINAL
                )
            ],
        )
        assert run.items[0].disposition is imports.Disposition.CREATE


class TestSetsAreEdited:
    def test_adding_a_set_sorts_and_replaces_by_id(self) -> None:
        game = games.custom("lorcana", "Disney Lorcana")
        game = games.with_set(game, games.SetSpec("rfb", "Rise of the Floodborn"))
        game = games.with_set(game, games.SetSpec("tfc", "The First Chapter"))
        assert [s.id for s in game.sets] == ["rfb", "tfc"]
        game = games.with_set(game, games.SetSpec("tfc", "Renamed", total=204))
        assert [(s.id, s.name) for s in game.sets] == [
            ("rfb", "Rise of the Floodborn"),
            ("tfc", "Renamed"),
        ]

    def test_removing_a_set_leaves_the_rest(self) -> None:
        game = games.with_set(
            games.with_set(
                games.custom("lorcana", "x"), games.SetSpec("tfc", "The First Chapter")
            ),
            games.SetSpec("rfb", "Rise of the Floodborn"),
        )
        assert [s.id for s in games.without_set(game, "tfc").sets] == ["rfb"]

    def test_a_set_name_falls_back_to_the_id(self) -> None:
        """Total on purpose: it is called to *name a folder*, and refusing there would
        block a card over a set nobody declared. The check that the set exists is
        `imports.plan`'s, before anything is written."""
        game = games.custom("lorcana", "x")
        assert game.set_name("tfc") == "tfc"

    def test_a_stored_game_round_trips(self, tmp_path: Path) -> None:
        game = games.with_set(
            games.custom("lorcana", "Disney Lorcana", notes="hand scans"),
            games.SetSpec("tfc", "The First Chapter", total=204, released="2023-08-18"),
        )
        games.store(tmp_path, game)
        back = games.load(tmp_path).get("lorcana")
        assert back == game


class TestValidation:
    @pytest.mark.parametrize("ok", ["lorcana", "yugioh", "a", "one-piece", "swu2"])
    def test_a_usable_id(self, ok: str) -> None:
        assert games.valid_id(ok)

    @pytest.mark.parametrize(
        "bad", ["", "Lorcana", "one--piece", "-x", "x-", "a b", "a/b", "..", "a" * 33]
    )
    def test_an_unusable_id(self, bad: str) -> None:
        """It becomes a filename, a CLI value, a URL segment and a marker's contents,
        so it is held to the shape every one of those carries without quoting."""
        assert not games.valid_id(bad)

    def test_parse_validates_shape_and_does_not_coerce(self) -> None:
        assert games.parse("lorcana") == "lorcana"
        assert games.parse(" MTG ") == "mtg"
        assert games.parse("a b") is None
        assert games.parse(None) is None

    def test_coerce_never_fails(self) -> None:
        assert games.coerce("a b") == games.DEFAULT
        assert games.coerce(None, "mtg") == "mtg"
        assert games.coerce("lorcana", "mtg") == "lorcana"


class TestADanglingDefaultIsReported:
    """`[library] game` is a name in a text file, exactly like `[print] profile`.

    Deleting the game it names leaves a config that reads fine and a bare `fetch`
    that refers to nothing, so it is *reported* — `proxdex where` and `game list` —
    rather than raised at the moment somebody runs a command.
    """

    def test_a_name_nothing_answers_to(self, tmp_path: Path) -> None:
        cfg = Config()
        cfg.library_game = "lorcana"
        assert games.dangling(tmp_path, cfg) == "lorcana"

    def test_a_name_something_answers_to_is_silent(self, tmp_path: Path) -> None:
        define(tmp_path)
        cfg = Config()
        cfg.library_game = "lorcana"
        assert games.dangling(tmp_path, cfg) is None

    def test_a_shipped_game_never_dangles(self, tmp_path: Path) -> None:
        assert games.dangling(tmp_path, Config()) is None
