"""Finding cards: the grouping, the query and the paging arithmetic.

These earn a place for the reason `imports.plan` and `specs.resolve` do — one pure
function with several consumers that cannot be allowed to disagree — plus one that is
specific to paging: **a window that is off by one silently loses or repeats cards.**
Nothing on screen says so. A browse of a 553-card set is ten pages of thumbnails, and
if page 4 starts one row late you simply never see that card; if it starts one row
early you see one twice. Neither looks like a bug, so the arithmetic is pinned here.

The provider query strings are here for the same class of reason: an unquoted rarity
or a wrongly-spelled date filter comes back as an HTTP 400 or, worse, as *plausible
but different* cards. `set.releaseDate:[a TO b]` is a real 400 from pokemontcg.io and
`c:wu` really does mean "both colours" to Scryfall — both were found by asking the
APIs, and both are the kind of thing that gets re-broken by a tidy-up.

Nothing here touches the network. The grouping is tested through `browse.gather`,
which is pure for exactly this reason; the provider readers are tested against the
JSON shapes the two APIs actually return, copied down to the key names.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pytest
from PIL import Image

from proxdex import browse, sources
from proxdex.browse import Expansion, Facet, Grouping, Page, Query, Sort
from proxdex.config import Config
from proxdex.errors import ProxdexError
from proxdex.games import GameId


def _exp(
    set_id: str,
    *,
    game: GameId = GameId.POKEMON,
    group: str = "Base",
    released: str = "1999-01-09",
    total: int = 102,
) -> Expansion:
    return Expansion(
        id=set_id,
        name=set_id.title(),
        game=game,
        group=group,
        total=total,
        released=released,
    )


class TestHowSetsAreGrouped:
    def test_pokemon_groups_by_dated_era_newest_first(self) -> None:
        """A series *is* an era, so the newest one leads — Sword & Shield really does
        come after XY, and a curated order would go stale every release."""
        found = [
            _exp("base1", group="Base", released="1999-01-09"),
            _exp("sv1", group="Scarlet & Violet", released="2023-03-31"),
            _exp("swsh1", group="Sword & Shield", released="2020-02-07"),
        ]
        groups = browse.gather(GameId.POKEMON, found)
        assert [g.key for g in groups] == ["Scarlet & Violet", "Sword & Shield", "Base"]

    def test_mtg_groups_by_kind_in_a_fixed_order(self) -> None:
        """A Commander deck is not "later" than a core set, so date must not decide:
        the newest set here is the promo one, and it still sorts last."""
        found = [
            _exp("plst", game=GameId.MTG, group="promo", released="2026-01-01"),
            _exp("cmd", game=GameId.MTG, group="commander", released="2011-06-17"),
            _exp("dft", game=GameId.MTG, group="expansion", released="2025-02-14"),
        ]
        groups = browse.gather(GameId.MTG, found)
        assert [g.key for g in groups] == ["expansion", "commander", "promo"]

    def test_an_unknown_kind_is_shown_last_and_keeps_its_own_name(self) -> None:
        """Scryfall adds a set type every few years. Hiding a whole kind of product
        is worse than showing it in the wrong place with a titled name."""
        found = [
            _exp("x1", game=GameId.MTG, group="brand_new_thing"),
            _exp("dft", game=GameId.MTG, group="expansion"),
        ]
        groups = browse.gather(GameId.MTG, found)
        assert [g.key for g in groups] == ["expansion", "brand_new_thing"]
        assert groups[-1].label == "Brand New Thing"

    def test_within_a_group_the_newest_set_leads_in_both_games(self) -> None:
        for game in GameId:
            found = [
                _exp("old", game=game, group="g", released="1999-01-09"),
                _exp("new", game=game, group="g", released="2020-01-01"),
            ]
            (group,) = browse.gather(game, found)
            assert [e.id for e in group.expansions] == ["new", "old"], game

    def test_a_group_reports_its_span_and_its_card_total(self) -> None:
        found = [
            _exp("a", group="g", released="1999-01-09", total=102),
            _exp("b", group="g", released="2003-07-18", total=50),
        ]
        (group,) = browse.gather(GameId.POKEMON, found)
        assert group.span == "1999-2003"
        assert group.cards == 152

    def test_one_year_is_not_written_as_a_range(self) -> None:
        found = [_exp("a", group="g", released="1999-01-09")]
        (group,) = browse.gather(GameId.POKEMON, found)
        assert group.span == "1999"

    def test_the_grouping_a_game_uses_is_stated_once(self) -> None:
        assert browse.grouping(GameId.POKEMON) is Grouping.ERA
        assert browse.grouping(GameId.MTG) is Grouping.KIND
        # "series" is already plural, and a caller bolting on an `s` wrote "seriess"
        assert Grouping.ERA.plural == "series"


class TestWhatTheProvidersSay:
    """The two set responses, read into one shape — key names copied from the APIs."""

    def test_a_pokemon_set_carries_its_series_and_both_totals(self) -> None:
        exp = browse.read_expansion(
            GameId.POKEMON,
            {
                "id": "base1",
                "name": "Base",
                "series": "Base",
                "printedTotal": 102,
                "total": 102,
                "releaseDate": "1999/01/09",
                "images": {
                    "symbol": "https://images.pokemontcg.io/base1/symbol.png",
                    "logo": "https://images.pokemontcg.io/base1/logo.png",
                },
            },
        )
        assert exp.group == "Base"
        assert (exp.printed_total, exp.total) == (102, 102)
        assert exp.released == "1999-01-09"  # normalised off `1999/01/09`
        assert exp.logo_url.endswith("logo.png")
        assert exp.symbol_url.endswith("symbol.png")

    def test_a_magic_set_has_a_symbol_and_no_wordmark(self) -> None:
        """So a tile has to stand on the symbol alone — it cannot assume a logo."""
        exp = browse.read_expansion(
            GameId.MTG,
            {
                "code": "dft",
                "name": "Aetherdrift",
                "set_type": "expansion",
                "card_count": 553,
                "released_at": "2025-02-14",
                "digital": False,
                "icon_svg_uri": "https://svgs.scryfall.io/sets/dft.svg?1",
            },
        )
        assert exp.group == "expansion"
        assert exp.group_label == "Expansion"
        assert exp.logo_url == ""
        assert exp.symbol_url.endswith(".svg?1")
        assert exp.total == exp.printed_total == 553

    def test_a_digital_only_set_is_marked_rather_than_dropped(self) -> None:
        """An Alchemy card has no paper printing to proxy, which is worth *saying* —
        and somebody may still want the picture."""
        exp = browse.read_expansion(
            GameId.MTG,
            {"code": "y22", "name": "Alchemy", "set_type": "alchemy", "digital": True},
        )
        assert exp.digital is True

    def test_an_unreadable_date_sorts_to_the_end_not_to_1970(self) -> None:
        """Empty sorts last; 1970 would put an unreleased set among the oldest."""
        for bad in (None, "", "soon", "2025", "2025-13"):
            row = {
                "code": "x",
                "name": "X",
                "set_type": "expansion",
                "released_at": bad,
            }
            assert browse.read_expansion(GameId.MTG, row).released == ""

    def test_a_row_full_of_nulls_still_reads(self) -> None:
        """The JSON is untyped and one bad set of 1047 must not stop the screen."""
        exp = browse.read_expansion(
            GameId.MTG,
            {"code": "x", "name": None, "card_count": "nope", "icon_svg_uri": None},
        )
        assert (exp.id, exp.name, exp.total, exp.symbol_url) == ("x", "", 0, "")

    def test_a_pokemon_row_with_no_images_key_still_reads(self) -> None:
        exp = browse.read_expansion(GameId.POKEMON, {"id": "base1", "name": "Base"})
        assert (exp.logo_url, exp.symbol_url, exp.total) == ("", "", 0)


class TestWhatTheLibraryHolds:
    def test_cards_are_counted_per_set(self) -> None:
        assert browse.owned(["base1", "base1", "dft"]) == {"base1": 2, "dft": 1}

    def test_nothing_held_is_an_empty_map(self) -> None:
        assert browse.owned([]) == {}


class TestOnePageOfManY:
    def test_a_page_says_where_it_sits_in_the_whole_answer(self) -> None:
        page: Page[str] = Page(items=("a",) * 60, page=2, per_page=60, total=553)
        assert page.pages == 10
        assert (page.first, page.last) == (61, 120)
        assert page.has_more is True

    def test_the_last_page_is_short_and_offers_no_next(self) -> None:
        page: Page[str] = Page(items=("a",) * 13, page=10, per_page=60, total=553)
        assert page.first == 541
        assert page.last == 553
        assert page.has_more is False

    def test_an_unknown_total_is_minus_one_and_not_a_confident_zero(self) -> None:
        """So a caller can tell "none matched" from "the provider would not say"."""
        page: Page[str] = Page(items=("a",) * 60, page=1, per_page=60)
        assert page.known is False
        assert page.pages == -1
        # a full page is assumed to have a successor; the page itself proves it
        assert page.has_more is True
        assert Page(items=("a",), page=1, per_page=60).has_more is False

    def test_an_empty_page_reports_no_position(self) -> None:
        page: Page[str] = Page(items=(), page=3, per_page=60, total=0)
        assert (page.first, page.last) == (0, 0)

    def test_a_local_list_is_paged_by_slicing(self) -> None:
        rows = list(range(10))
        page = browse.slice_page(rows, page=2, per_page=4)
        assert page.items == (4, 5, 6, 7)
        assert page.total == 10
        assert page.pages == 3

    def test_a_page_number_below_one_is_the_first_page(self) -> None:
        assert browse.slice_page([1, 2, 3], page=0, per_page=2).page == 1
        assert browse.clamp_page(-7) == 1

    def test_a_page_size_is_bounded(self) -> None:
        """A page is a screen of thumbnails; past this it is a way to make one
        request cost the provider fifty."""
        assert browse.clamp_per_page(0) == browse.PER_PAGE
        assert browse.clamp_per_page(10_000) == browse.MAX_PER_PAGE

    def test_page_numbers_carry_onto_mapped_items(self) -> None:
        """So a caller turning rows into JSON cannot restate a total and get it
        wrong."""
        page: Page[int] = Page(items=(1, 2), page=4, per_page=2, total=99)
        moved = page.of(["a", "b"])
        assert moved.items == ("a", "b")
        assert (moved.page, moved.per_page, moved.total) == (4, 2, 99)


class TestWhatAQueryAsksFor:
    def test_an_empty_query_narrows_nothing(self) -> None:
        """Which is why `search_page` refuses it: a search box with nothing typed in
        it has not asked a question."""
        assert Query().narrowed is False

    def test_a_set_alone_is_a_query_which_is_what_browsing_is(self) -> None:
        assert Query(set_id="base1").narrowed is True

    def test_every_filter_reports_itself_as_a_removable_pair(self) -> None:
        wanted = Query(
            set_id="base1", rarity="Rare Holo", year="1999", types=("Fire", "Water")
        )
        assert wanted.filters == (
            (Facet.SET, "base1"),
            (Facet.RARITY, "Rare Holo"),
            (Facet.YEAR, "1999"),
            (Facet.TYPE, "Fire"),
            (Facet.TYPE, "Water"),
        )

    def test_removing_one_multi_value_keeps_the_others(self) -> None:
        wanted = Query(types=("Fire", "Water"), colors=("W", "U"))
        assert wanted.without(Facet.TYPE, "Fire").types == ("Water",)
        assert wanted.without(Facet.COLOR, "W").colors == ("U",)

    def test_removing_a_single_valued_filter_empties_it(self) -> None:
        assert Query(rarity="mythic").without(Facet.RARITY).rarity == ""

    def test_a_date_sort_runs_newest_first_and_the_others_do_not(self) -> None:
        """The newest set is what you are most likely reaching for; a reversed
        alphabet is not."""
        assert Query(sort=Sort.RELEASED).descending is True
        assert Query(sort=Sort.NAME).descending is False
        assert Query(sort=Sort.NUMBER).descending is False

    def test_the_direction_can_be_overridden_in_either_direction(self) -> None:
        assert Query(sort=Sort.RELEASED, desc=False).descending is False
        assert Query(sort=Sort.NAME, desc=True).descending is True

    def test_a_query_clamps_its_own_page_numbers(self) -> None:
        assert Query(page=0).page == 1
        assert Query(per_page=99_999).per_page == browse.MAX_PER_PAGE

    def test_params_drops_defaults_so_a_url_stays_readable(self) -> None:
        assert Query(game=GameId.MTG, text="bolt").params() == {
            "game": "mtg",
            "q": "bolt",
        }

    def test_params_spells_a_multi_value_as_a_comma_list(self) -> None:
        got = Query(game=GameId.MTG, colors=("W", "U")).params()
        assert got["color"] == "W,U"

    def test_params_carries_a_direction_only_when_it_is_not_the_default(self) -> None:
        assert "desc" not in Query(sort=Sort.RELEASED, desc=True).params()
        assert Query(sort=Sort.RELEASED, desc=False).params()["desc"] == "0"

    def test_an_unknown_sort_is_no_answer_rather_than_a_stringly_typed_one(
        self,
    ) -> None:
        assert browse.parse_sort("nonsense") is None
        assert browse.parse_sort("name") is Sort.NAME


class TestWhatIsSentToPokemontcgIo:
    def test_the_typed_words_become_one_term_with_wildcards_between(self) -> None:
        """**One term, never one per word**, and this is a bug that shipped.

        A space separates *terms* in this API's syntax, so one term per word made
        `Moo Moo` into `name:*Moo* name:*Moo*` — two identical substring tests, which
        any name holding "moo" once satisfies. It was therefore exactly equivalent to
        searching `Moo`: it answered with Amoonguss, Bloodmoon Ursaluna and Roaring
        Moon, and buried the card asked for under everything printed since.

        Joining with wildcards is also what copes with Pokémon's separators, which are
        not reliable: the card is **Moo-Moo Milk** in Neo and **Moomoo Milk** in
        HeartGold, and *neither* `name:"Moo Moo"` nor `name:"Moo Moo Milk"` matches
        anything at all — both measured at 0 results. A wildcard where the user typed a
        space matches a space, a hyphen, a dot or nothing.
        """
        assert sources.query_string(Query(text="Moo Moo")) == "name:*Moo*Moo*"
        assert sources.query_string(Query(text="dark charizard")) == (
            "name:*dark*charizard*"
        )

    def test_a_repeated_word_is_kept_because_it_narrows(self) -> None:
        """The opposite of the old behaviour, where it was noise. Joined, the second
        `Moo` is what distinguishes *Moomoo Milk* from *Amoonguss*."""
        assert sources.query_string(Query(text="Moo Moo")) != sources.query_string(
            Query(text="Moo")
        )

    def test_one_word_is_unchanged(self) -> None:
        assert sources.query_string(Query(text="charizard")) == "name:*charizard*"

    def test_no_text_asks_nothing_about_the_name(self) -> None:
        """A query that narrows nothing must not become `name:**`, which would ask the
        provider for every card ever printed."""
        assert "name:" not in sources.query_string(Query(rarity="Common"))

    def test_a_set_id_is_matched_exactly_and_a_set_name_by_substring(self) -> None:
        """The browse screen hands over an id; somebody typing may mean a name."""
        assert "set.id:base1" in sources.query_string(Query(set_id="base1"))
        assert "set.name:*Team Rocket*" in sources.query_string(
            Query(set_id="Team Rocket")
        )

    def test_catalog_values_are_quoted_because_they_are_exact(self) -> None:
        """They come from the provider's own `/v2/rarities` and friends, so the user
        picked one — and `Rare Holo` unquoted is two terms."""
        got = sources.query_string(
            Query(rarity="Rare Holo", supertype="Trainer", subtype="Stage 2")
        )
        assert 'rarity:"Rare Holo"' in got
        assert 'supertype:"Trainer"' in got
        # the field is plural on this API even though the filter picks one
        assert 'subtypes:"Stage 2"' in got

    def test_a_year_is_a_prefix_wildcard_because_a_range_is_a_400(self) -> None:
        """`set.releaseDate:[1999/01/01 TO 1999/12/31]` really is rejected by this
        API; the dates are `YYYY/MM/DD` strings, so a prefix is the filter it has."""
        got = sources.query_string(Query(year="1999"))
        assert got == "set.releaseDate:1999*"
        assert " TO " not in got

    def test_every_type_must_match(self) -> None:
        got = sources.query_string(Query(types=("Fire", "Water")))
        assert got == 'types:"Fire" types:"Water"'

    def test_every_sort_has_a_field_on_this_api(self) -> None:
        """A sort the UI offers and the provider rejects is a 400 mid-browse."""
        for sort in Sort:
            assert sources.provider_sort(GameId.POKEMON, sort)


class TestWhatIsSentToScryfall:
    """Every query here names its game: `query_string` dispatches on it, so a Query
    without one is spelled for Pokémon and every Magic-only filter silently vanishes.
    Which is the point of testing through the public call rather than the builder."""

    def test_a_name_word_is_quoted(self) -> None:
        got = sources.query_string(Query(game=GameId.MTG, text="lightning bolt"))
        assert got == 'name:"lightning" name:"bolt"'

    def test_the_filters_use_scryfall_s_own_short_keys(self) -> None:
        got = sources.query_string(
            Query(
                game=GameId.MTG,
                set_id="dft",
                rarity="mythic",
                year="2025",
                types=("Instant",),
            )
        )
        assert "set:dft" in got
        assert "r:mythic" in got
        assert "year:2025" in got
        assert 't:"Instant"' in got

    def test_colours_are_ored_because_a_filter_means_either(self) -> None:
        """Scryfall's bare `c:wu` means a card that is *both*, which for two colours
        is a handful of cards and reads as a broken filter."""
        got = sources.query_string(Query(game=GameId.MTG, colors=("W", "U")))
        assert got == "(c:w or c:u)"

    def test_one_colour_still_goes_through_the_same_spelling(self) -> None:
        assert sources.query_string(Query(game=GameId.MTG, colors=("R",))) == "(c:r)"

    def test_every_sort_has_an_order_on_this_api(self) -> None:
        for sort in Sort:
            assert sources.provider_sort(GameId.MTG, sort)

    def test_sorting_by_number_means_the_sets_own_order(self) -> None:
        """Scryfall has no `number` order; `set` is "by set, then collector number",
        which is what asking to sort by number means while browsing one set."""
        assert sources.provider_sort(GameId.MTG, Sort.NUMBER) == "set"


class TestCuttingAWindowOutOfScryfallsPages:
    """**Scryfall pages at a fixed 175 and will not be talked down**, so a display
    page of 60 straddles a boundary two times in three. This is the arithmetic that
    decides which of its pages have to be fetched — and an error here drops or
    repeats a card with nothing on screen to say so.
    """

    def test_a_page_inside_one_provider_page_needs_one_request(self) -> None:
        assert sources.mtg_page_span(0, 60) == 1  # rows 0-59
        assert sources.mtg_page_span(60, 60) == 1  # rows 60-119

    def test_a_page_straddling_the_boundary_needs_two(self) -> None:
        # rows 120-179, and 175 is where the provider's first page ends
        assert sources.mtg_page_span(120, 60) == 2

    def test_the_row_after_a_boundary_is_back_to_one(self) -> None:
        assert sources.mtg_page_span(175, 60) == 1

    def test_the_largest_display_page_can_touch_three(self) -> None:
        """250 is the maximum a caller may ask for, and 174 + 250 spans three."""
        assert sources.mtg_page_span(174, 250) == 3

    def test_the_offset_within_a_provider_page_is_the_slice_start(self) -> None:
        """The other half of the same sum: page 3 of 60 begins at row 120, which is
        row 120 of the provider's first page — and page 4 begins at row 5 of its
        second. Off by one here and the fourth screen of a set skips a card."""
        for page, per_page, want in ((1, 60, 0), (3, 60, 120), (4, 60, 5), (2, 175, 0)):
            offset = (page - 1) * per_page
            assert offset % sources.MTG_PAGE_SIZE == want, (page, per_page)


class TestTheVocabularyEverySurfaceShares:
    def test_a_facet_is_labelled_once(self) -> None:
        """The CLI's flags, the API's parameters and the UI's dropdowns all spell the
        enum's value, so a filter cannot mean one thing in Python and another in JS."""
        for facet in Facet:
            assert facet.label
        assert Facet.SUPERTYPE.label == "Card kind"

    def test_an_unknown_facet_is_no_answer(self) -> None:
        assert browse.parse_facet("colour") is None
        assert browse.parse_facet("color") is Facet.COLOR

    def test_magic_rarities_are_written_down_because_scryfall_serves_no_catalog(
        self,
    ) -> None:
        assert [r.value for r in browse.Rarity] == [
            "common",
            "uncommon",
            "rare",
            "mythic",
            "special",
            "bonus",
        ]

    def test_colourless_is_offered_even_though_it_is_not_a_colour(self) -> None:
        """It is not one in the rules, but it is one to look for, and Scryfall takes
        `c:c`."""
        assert browse.Color.COLORLESS.value == "C"
        assert browse.Color.COLORLESS.label == "Colourless"

    def test_the_browse_meta_names_every_sort_and_facet(self) -> None:
        """It is what a screen draws its controls from, so nothing may be missing."""
        meta = browse.meta(GameId.MTG)
        assert [s["id"] for s in meta["sorts"]] == [s.value for s in Sort]
        assert set(meta["facet_labels"]) == {f.value for f in Facet}
        assert meta["per_page"] == browse.PER_PAGE


class TestTheImageHostCannotValidateAnId:
    """**Why a Pokémon fetch resolves the id against the metadata API first**, even
    though the image URL needs nothing but the id.

    ``images.scrydex.com/pokemon/<id>/large`` answers **HTTP 200 for an id it does not
    have**, with a byte-identical grey placeholder — verified against `base1-999` and
    `zzzz-1`. So the status code cannot tell a hit from a miss, and a mistyped id
    would file a grey rectangle that every later stage happily reshapes, upscales and
    imposes. It is only discovered on paper.

    The placeholder *is* identifiable, and this pins the two conditions that identify
    it. Both are required on purpose: a real scan must never be refused, so being out
    of date one day costs a placeholder filed as it would have been anyway rather than
    a good card rejected.
    """

    def _im(self, size: tuple[int, int], mode: str) -> Image.Image:
        return Image.new(mode, size)

    def test_the_placeholder_is_refused(self) -> None:
        assert sources.is_placeholder(self._im((640, 892), "P"))

    def test_a_real_older_scan_is_not(self) -> None:
        """The 1999-2010 sets arrive 600x825 RGB."""
        assert not sources.is_placeholder(self._im((600, 825), "RGB"))

    def test_a_real_modern_scan_is_not(self) -> None:
        """The current sets arrive ~734x1024 RGBA — die-cut corners included."""
        assert not sources.is_placeholder(self._im((734, 1024), "RGBA"))
        assert not sources.is_placeholder(self._im((733, 1024), "RGBA"))

    def test_both_conditions_are_required(self) -> None:
        """So nothing that merely shares one of them can be refused."""
        assert not sources.is_placeholder(self._im((640, 892), "RGB"))
        assert not sources.is_placeholder(self._im((600, 825), "P"))


class TestMetadataYouAlreadyHave:
    """`sources.known_meta` — filing a Pokémon card without asking the API again.

    The row a client clicked was described by the provider a moment ago, and `fetch`
    was asking for all of it a second time; that request is the one that fails when
    pokemontcg.io is having a bad afternoon. This is safe only because
    :func:`sources.download` refuses the image host's placeholder — skipping the lookup
    means skipping the proof that the id exists.
    """

    def test_the_set_comes_off_the_id(self) -> None:
        """`<set>-<number>`, and a Pokémon collector number carries no hyphen."""
        meta = sources.known_meta("base1-16", Config(), name="Zapdos", set_name="Base")
        assert meta.set_id == "base1"
        assert meta.id == "base1-16"

    def test_a_multipart_set_code_survives(self) -> None:
        meta = sources.known_meta("swsh12pt5gg-1", Config(), name="X")
        assert meta.set_id == "swsh12pt5gg"

    def test_the_image_url_is_derived_from_the_id(self) -> None:
        """Which is the whole reason this can work at all — no request is involved."""
        meta = sources.known_meta("base1-16", Config(), name="Zapdos")
        assert meta.image_url.endswith("/base1-16/large")

    def test_it_carries_the_same_traits_a_lookup_would(self) -> None:
        """Or a frame rule would answer differently depending on which verb filed the
        card — the inconsistency this replaced."""
        meta = sources.known_meta(
            "base1-16", Config(), name="Zapdos", rarity="Rare Holo", subtypes="Basic"
        )
        assert meta.traits == {"rarity": "Rare Holo", "subtypes": "Basic"}

    def test_no_traits_given_records_none(self) -> None:
        """An empty mapping removes the marker, so the card reads as "nothing recorded"
        and a rule needing traits reports that it cannot decide."""
        meta = sources.known_meta("base1-16", Config(), name="Zapdos")
        assert not any(meta.traits.values())

    def test_a_nameless_card_is_refused(self) -> None:
        """The name becomes a folder name; there is nothing to file it as."""
        with pytest.raises(ProxdexError):
            sources.known_meta("base1-16", Config(), name="   ")


class TestATileTakesTheSmallScan:
    """A result row is *looked at*; only a fetched card is kept.

    Sixty tiles a page at 190px each were pulling Pokémon's full 825 KB scan, which the
    art cache could shrink for the *browser* but not for the fetch that fills it — so
    the first visit stayed the slow one. `/small` is 245px and ~30 KB, a 25x saving on
    the only request left.

    The reason the thumbnail is its own field rather than a suffix swapped at the point
    of use: nothing downstream may accidentally **file** one. A 245px master would
    border, upscale, grade and impose without complaint and be wrong only on paper —
    the same shape of defect as the grey placeholder, and just as invisible. So the
    filing paths are asserted to still hand over the full scan.
    """

    @staticmethod
    def _page(monkeypatch: pytest.MonkeyPatch, cfg: Config) -> browse.Page[Any]:
        """One page of Pokémon results, through the public path with the API stubbed."""
        body = {
            "data": [{"id": "base1-4", "name": "Charizard", "set": {"id": "base1"}}],
            "totalCount": 1,
        }

        def answer(*_a: object, **_k: object) -> sources.net.Reply:
            return sources.net.Reply(200, json.dumps(body).encode())

        monkeypatch.setattr(sources.net, "get", answer)
        query = browse.Query(game=GameId.POKEMON, text="charizard")
        return sources.search_page(query, cfg)

    def test_a_search_hit_carries_both(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = self._page(monkeypatch, Config()).items[0]
        assert row.image_url.endswith("/base1-4/large")
        assert row.thumb_url.endswith("/base1-4/small")
        assert row.thumb == row.thumb_url

    def test_what_gets_filed_is_still_the_full_scan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`to_meta` is the bridge from a row to a download, and a `CardMeta`'s faces
        are what `fetch` writes to disk — so it must not learn about the thumbnail."""
        row = self._page(monkeypatch, Config()).items[0]
        assert row.to_meta().image_url.endswith("/large")

    def test_the_two_urls_are_configured_as_a_pair(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A library repointed at a mirror must move both, or its tiles would come from
        one host and its files from another — and `art.hosts` allows only what the
        card URL names."""
        cfg = Config(
            scrydex_url="https://mirror.test/p/{id}/large",
            scrydex_thumb_url="https://mirror.test/p/{id}/small",
        )
        row = self._page(monkeypatch, cfg).items[0]
        assert row.thumb == "https://mirror.test/p/base1-4/small"
        assert row.image_url == "https://mirror.test/p/base1-4/large"

    def test_the_thumb_falls_back_to_the_full_image(self) -> None:
        """A provider that publishes only one size must still draw a tile — the empty
        `thumb_url` is an answer, not a gap."""
        row = sources.SearchResult(
            id="dft-1",
            name="X",
            set_id="dft",
            set_name="D",
            series="",
            year="2025",
            number="1",
            printed_total="",
            rarity="",
            artist="",
            game=GameId.MTG,
            faces=sources.one_face("https://cards.scryfall.io/png/x.png"),
        )
        assert not row.thumb_url
        assert row.thumb == row.image_url


class TestTheMtgTileTakesASmallerSizeToo:
    """Scryfall publishes its sizes as keys, so the choice is which key to read.

    Measured over 32 cards across four sets: `png` — right to *file*, being the only
    lossless size — is **1657 KB** at the median and ranges 331 KB to 2206 KB, since an
    old card's scan is much heavier than a modern one's. `normal` is 488x680 and **120
    KB, range 78-146**. So ~14x at the median, 3x on the lightest set, and — the better
    property — a *flat* cost: a page of Alpha now costs what a page of Aetherdrift does,
    where before it was 102 MB against 23.

    `normal` rather than `small` (146x204) because 146px is softer than the ~190px tile
    it fills. Not a setting, unlike the Pokémon URL, because there is no template to
    configure — only a key in the card's own response.
    """

    #: a Scryfall card object trimmed to what these readers touch
    CARD: ClassVar[dict[str, Any]] = {
        "set": "c13",
        "collector_number": "259",
        "name": "Sol Ring",
        "image_uris": {
            "small": "https://cards.scryfall.io/small/a.jpg",
            "normal": "https://cards.scryfall.io/normal/a.jpg",
            "large": "https://cards.scryfall.io/large/a.jpg",
            "png": "https://cards.scryfall.io/png/a.png",
        },
    }

    @staticmethod
    def _row(
        card: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> sources.SearchResult:
        """One row, through the public `search_page` with Scryfall stubbed."""
        body = {"data": [card], "total_cards": 1, "has_more": False}

        def answer(*_a: object, **_k: object) -> sources.net.Reply:
            return sources.net.Reply(200, json.dumps(body).encode())

        monkeypatch.setattr(sources.net, "get", answer)
        query = browse.Query(game=GameId.MTG, text="sol ring")
        return sources.search_page(query, Config()).items[0]

    def test_the_tile_takes_normal_and_the_file_stays_png(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = self._row(self.CARD, monkeypatch)
        assert row.thumb.endswith("/normal/a.jpg")
        assert row.image_url.endswith("/png/a.png")
        assert row.to_meta().image_url.endswith("/png/a.png")

    def test_it_settles_for_small_when_normal_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        card = {**self.CARD, "image_uris": {"small": "s", "png": "p"}}
        assert self._row(card, monkeypatch).thumb == "s"

    def test_no_published_size_falls_back_to_the_full_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Total like every other reader here: the JSON is untyped, and a tile that
        cannot find a small picture must draw the big one rather than nothing."""
        card = {**self.CARD, "image_uris": {"png": "p"}}
        row = self._row(card, monkeypatch)
        assert not row.thumb_url
        assert row.thumb == "p"

    def test_a_two_sided_card_thumbnails_its_front(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transform card keeps its sizes *on* the faces and none on the card — the
        same fact that makes it two-sided — and a tile only ever shows the front."""
        card = {
            "set": "isd",
            "collector_number": "51",
            "name": "Delver of Secrets // Insectile Aberration",
            "card_faces": [
                {
                    "name": "Delver of Secrets",
                    "image_uris": {"normal": "f1", "png": "p1"},
                },
                {
                    "name": "Insectile Aberration",
                    "image_uris": {"normal": "f2", "png": "p2"},
                },
            ],
        }
        row = self._row(card, monkeypatch)
        assert row.thumb == "f1"
        assert row.image_url == "p1"

    def test_a_split_card_uses_its_one_shared_picture(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A split or adventure card lists faces but prints one image, and Scryfall
        says so by keeping `image_uris` on the card — so the shared one wins."""
        card = {
            **self.CARD,
            "card_faces": [
                {"name": "A", "image_uris": {"normal": "wrong"}},
                {"name": "B", "image_uris": {"normal": "wrong"}},
            ],
        }
        assert self._row(card, monkeypatch).thumb.endswith("/normal/a.jpg")

    def test_a_lookup_from_known_metadata_files_the_full_scan(self) -> None:
        """The other way a card is filed derives its image from `scrydex_url` too."""
        meta = sources.known_meta("base1-4", Config(), name="Charizard")
        assert meta.image_url.endswith("/large")
