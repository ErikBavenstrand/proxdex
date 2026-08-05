"""Finding a card: the set list, the groupings, the filter vocabulary and one query.

proxdex has always been able to *search* — a name, and the API's answer. What it
could not do is **browse**, which is how anyone actually looks for a proxy: you
know the set, or the era, and you want to see what is in it. That is a different
shape of question, and it needs facts the card endpoints do not carry — which
expansions exist, what they are grouped into, how many cards each holds, what its
symbol looks like.

**The scrydex API is not what this reads, and it does not need to be.** Its
endpoints (``/cards``, ``/expansions``, ``/sealed``, ``/listings``,
``/price-history``) are gated behind ``X-Api-Key`` + ``X-Team-ID``, so proxdex
cannot call them, and its *set pages* are a rendered site — scraping one would be
a fourth data path that breaks silently on a redesign and reports a plausible
wrong number in the meantime, which is the same failure mode as the deleted border
detector. Every fact its expansion pages show is already served, as documented
JSON, by the two APIs proxdex already talks to:

* **Pokémon** — pokemontcg.io ``/v2/sets`` carries ``series``, which *is* the
  grouping scrydex's Pokémon page shows (Scarlet & Violet, Sword & Shield, …),
  plus ``printedTotal``/``total``, ``releaseDate`` and ``images.logo``/
  ``images.symbol``. One request for all of it.
* **MTG** — Scryfall ``/sets`` carries ``set_type``, which *is* the grouping
  scrydex's Magic page shows (Expansion, Core, Commander, Masters, Draft
  Innovation, Masterpiece, Promo, …), plus ``card_count``, ``released_at``,
  ``block`` and ``icon_svg_uri``.

So the set art is the provider's own URL out of the same response, never a URL
guessed from an id pattern: a guessed URL is a 404 for every set whose id differs,
and it 404s as a blank tile rather than as an error. (scrydex's own set art *is*
reachable unauthenticated at ``images.scrydex.com/pokemon/<set>-logo/logo``, and
proxdex does use ``images.scrydex.com`` for the card scans themselves — see
``Config.scrydex_url`` — but for set art it would be the same picture behind a
guess.)

**The two groupings are not the same kind of thing**, which is why
:class:`Grouping` exists. A Pokémon series is an **era** — it has a date, and the
newest one belongs at the top. An MTG ``set_type`` is a **kind of product** — a
Commander deck is not later than a core set, so those are ordered by a curated
list of what a proxy printer reaches for, not by date.

**Browse and search are one query.** :class:`Query` carries the text *and* the
filters *and* the page, and browsing a set is simply a query with a set and no
text — the same call, the same pagination, the same result rows. There is no
second code path to keep in step, which is the argument that put
:func:`proxdex.imports.plan` and :func:`proxdex.sheet.plan` where they are.

This module is the **vocabulary and the plan**; :mod:`proxdex.sources` executes a
:class:`Query` against whichever provider answers for the game. Nothing here
imports :mod:`proxdex.sources`, so the split holds in both directions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Generic, TypeVar

from proxdex import games, net
from proxdex.errors import FileError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from proxdex.config import Config


# --------------------------------------------------------------- pagination ---
#: How many cards a page of results holds by default. Chosen against the two
#: providers rather than by taste: pokemontcg.io pages at any size up to 250 and
#: Scryfall pages at a fixed 175, so 60 divides neither — it is a *display* page
#: size, and :func:`proxdex.sources.search_page` is what reconciles it with
#: whatever the provider does.
PER_PAGE: Final = 60
#: The most a caller may ask for in one page. A page is a screen of thumbnails;
#: past this it is a way to make one request cost the provider fifty.
MAX_PER_PAGE: Final = 250

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """One page of results, and enough to say where it sits.

    ``total`` is the provider's own count of *everything* that matched, not the
    length of ``items`` — that is the whole point of paging, and it is what lets
    a screen say "60 of 553" before it has seen the other 493. It is ``-1`` when
    a provider will not say, so a caller can tell "none" from "unknown" instead
    of reading a confident 0.
    """

    items: tuple[T, ...]
    page: int = 1
    per_page: int = PER_PAGE
    total: int = -1

    @property
    def known(self) -> bool:
        """Whether ``total`` is a real count."""
        return self.total >= 0

    @property
    def pages(self) -> int:
        """How many pages there are, or ``-1`` when the total is unknown."""
        if not self.known:
            return -1
        return max(1, -(-self.total // self.per_page))

    @property
    def has_more(self) -> bool:
        """Whether another page exists.

        With no total to divide, a full page is assumed to have a successor and a
        short one to be the last — which is what the page itself proves.
        """
        if self.known:
            return self.page < self.pages
        return len(self.items) >= self.per_page

    @property
    def first(self) -> int:
        """1-based index of this page's first item, for a "showing 61-120" line."""
        return (self.page - 1) * self.per_page + 1 if self.items else 0

    @property
    def last(self) -> int:
        return self.first + len(self.items) - 1 if self.items else 0

    def of(self, items: Sequence[object]) -> Page[Any]:
        """This page's numbers carried onto a mapped ``items`` — so a caller that
        turns rows into JSON does not have to restate page/total and get one
        wrong."""
        return Page(
            items=tuple(items), page=self.page, per_page=self.per_page, total=self.total
        )

    def json(self) -> dict[str, Any]:
        """The envelope every paged endpoint serves. ``items`` is the caller's."""
        return {
            "page": self.page,
            "per_page": self.per_page,
            "total": self.total,
            "pages": self.pages,
            "has_more": self.has_more,
            "first": self.first,
            "last": self.last,
        }


def clamp_page(page: int) -> int:
    """A page number from untrusted input — never below 1."""
    return max(1, page)


def clamp_per_page(per_page: int) -> int:
    """A page size from untrusted input, inside :data:`MAX_PER_PAGE`."""
    if per_page <= 0:
        return PER_PAGE
    return min(per_page, MAX_PER_PAGE)


def slice_page(items: Sequence[T], page: int, per_page: int) -> Page[T]:
    """Page a list proxdex already holds — the library, or a set list.

    Local data is paged by *slicing* rather than by asking a provider for a
    window: the whole answer is already in hand, so a request per page would be
    slower and could disagree with itself between two pages.
    """
    page, per_page = clamp_page(page), clamp_per_page(per_page)
    start = (page - 1) * per_page
    return Page(
        items=tuple(items[start : start + per_page]),
        page=page,
        per_page=per_page,
        total=len(items),
    )


# -------------------------------------------------------------------- query ---
class Sort(StrEnum):
    """How results are ordered. Both providers can do all four server-side.

    Deliberately a closed set: a sort spelled straight into a provider's ``order``
    or ``orderBy`` parameter is an unvalidated string reaching an API, and the one
    that is wrong comes back as an unexplained 400 halfway through a browse.
    """

    RELEASED = "released"
    NAME = "name"
    NUMBER = "number"
    RARITY = "rarity"

    @property
    def label(self) -> str:
        return _SORT_LABELS[self]

    @property
    def newest_first(self) -> bool:
        """Whether descending is the useful default for this sort.

        Only date is: the newest set is what you are most likely looking for,
        while a reversed alphabet or a set counted backwards from 102 is not.
        """
        return self is Sort.RELEASED


_SORT_LABELS: Final[dict[Sort, str]] = {
    Sort.RELEASED: "Release date",
    Sort.NAME: "Name",
    Sort.NUMBER: "Card number",
    Sort.RARITY: "Rarity",
}


def parse_sort(value: str | None) -> Sort | None:
    """A sort from untrusted text (a query string, a CLI flag)."""
    try:
        return Sort(str(value).strip().lower())
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Query:
    """What to look for — one object for searching *and* browsing.

    Browsing a set is this with ``set_id`` set and ``text`` empty, which is why
    there is no second endpoint, no second result row and no second pagination.
    Every field is a *filter*: empty means "don't narrow by this", and an empty
    query is every card of the game, newest set first.
    """

    game: str = games.DEFAULT
    #: words that must appear in the card's name
    text: str = ""
    #: one expansion, by its id (``base1``, ``dft``)
    set_id: str = ""
    #: a rarity, as the game spells it (``Rare Holo``, ``mythic``)
    rarity: str = ""
    #: a four-digit release year
    year: str = ""
    #: Pokémon energy types / MTG card types — any of them matches
    types: tuple[str, ...] = ()
    #: Pokémon ``supertype`` (Pokémon / Trainer / Energy); no MTG equivalent
    supertype: str = ""
    #: Pokémon ``subtypes`` (Basic, VMAX, …)
    subtype: str = ""
    #: MTG colours, as single letters (``W``, ``U``, …); no Pokémon equivalent
    colors: tuple[str, ...] = ()
    sort: Sort = Sort.RELEASED
    #: which way ``sort`` runs; defaults to the sort's own useful direction
    desc: bool | None = None
    page: int = 1
    per_page: int = PER_PAGE

    def __post_init__(self) -> None:
        object.__setattr__(self, "page", clamp_page(self.page))
        object.__setattr__(self, "per_page", clamp_per_page(self.per_page))

    @property
    def descending(self) -> bool:
        """The direction actually used — the sort's own default unless overridden."""
        return self.sort.newest_first if self.desc is None else self.desc

    @property
    def words(self) -> tuple[str, ...]:
        """The name words, one per token."""
        return tuple(w for w in self.text.split() if w)

    @property
    def filters(self) -> tuple[tuple[Facet, str], ...]:
        """Every narrowing this query carries besides its text, as (facet, value)
        pairs — so a screen can draw one removable chip per filter without
        naming the fields itself, and a caller can ask *whether* it narrows at
        all without listing them again."""
        pairs: list[tuple[Facet, str]] = [
            (Facet.SET, self.set_id),
            (Facet.RARITY, self.rarity),
            (Facet.YEAR, self.year),
            (Facet.SUPERTYPE, self.supertype),
            (Facet.SUBTYPE, self.subtype),
            *((Facet.TYPE, t) for t in self.types),
            *((Facet.COLOR, c) for c in self.colors),
        ]
        return tuple((facet, value) for facet, value in pairs if value)

    @property
    def narrowed(self) -> bool:
        """Whether anything at all was asked for.

        An unnarrowed query is legal — it is what the Browse screen opens on —
        but a *search* box wants to know, so it can wait for a word instead of
        pulling the first page of every card ever printed.
        """
        return bool(self.words or self.filters)

    def at(self, page: int) -> Query:
        """The same query, another page."""
        return replace(self, page=page)

    def without(self, facet: Facet, value: str = "") -> Query:
        """The same query with one filter removed — what a chip's ✕ does.

        ``value`` matters only for the two multi-valued facets, where removing
        one type must not drop the others.
        """
        if facet is Facet.TYPE:
            return replace(self, types=tuple(t for t in self.types if t != value))
        if facet is Facet.COLOR:
            return replace(self, colors=tuple(c for c in self.colors if c != value))
        return replace(self, **{_FACET_FIELD[facet]: ""})

    def params(self) -> dict[str, str]:
        """This query as URL parameters, empties dropped — one spelling, used by
        the CLI's help examples, the web UI's address bar and the API alike."""
        out: dict[str, str] = {"game": self.game}
        if self.text:
            out["q"] = self.text
        for facet, value in self.filters:
            out[facet.value] = (
                ",".join(self.types)
                if facet is Facet.TYPE
                else ",".join(self.colors)
                if facet is Facet.COLOR
                else value
            )
        if self.sort is not Sort.RELEASED:
            out["sort"] = self.sort.value
        if self.desc is not None and self.desc is not self.sort.newest_first:
            out["desc"] = "1" if self.desc else "0"
        if self.page != 1:
            out["page"] = str(self.page)
        if self.per_page != PER_PAGE:
            out["per_page"] = str(self.per_page)
        return out


# ------------------------------------------------------------------- facets ---
class Facet(StrEnum):
    """A dimension results can be narrowed by.

    Closed, and shared by every surface: the CLI's flags, the API's query
    parameters, the UI's dropdowns and a filter chip's own label are all this
    enum's ``value``, so a filter cannot be spelled one way in Python and another
    in JS. Not every game has every facet — :func:`facets` reports which.
    """

    SET = "set"
    RARITY = "rarity"
    YEAR = "year"
    TYPE = "type"
    SUPERTYPE = "supertype"
    SUBTYPE = "subtype"
    COLOR = "color"

    @property
    def label(self) -> str:
        return _FACET_LABELS[self]


_FACET_LABELS: Final[dict[Facet, str]] = {
    Facet.SET: "Set",
    Facet.RARITY: "Rarity",
    Facet.YEAR: "Year",
    Facet.TYPE: "Type",
    Facet.SUPERTYPE: "Card kind",
    Facet.SUBTYPE: "Subtype",
    Facet.COLOR: "Colour",
}

#: which :class:`Query` field each facet narrows, so ``without`` names it once
_FACET_FIELD: Final[dict[Facet, str]] = {
    Facet.SET: "set_id",
    Facet.RARITY: "rarity",
    Facet.YEAR: "year",
    Facet.TYPE: "types",
    Facet.SUPERTYPE: "supertype",
    Facet.SUBTYPE: "subtype",
    Facet.COLOR: "colors",
}


def parse_facet(value: str | None) -> Facet | None:
    try:
        return Facet(str(value).strip().lower())
    except ValueError:
        return None


class Rarity(StrEnum):
    """MTG's rarities. Scryfall serves no catalog of them, and they are a closed
    set that has not changed in years — so they are written down here rather than
    left as whatever string a URL carried."""

    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    MYTHIC = "mythic"
    SPECIAL = "special"
    BONUS = "bonus"


class Color(StrEnum):
    """MTG's colours, as Scryfall's single letters. Colourless is not a colour in
    the rules but it is one to look for, and Scryfall accepts ``c``."""

    WHITE = "W"
    BLUE = "U"
    BLACK = "B"
    RED = "R"
    GREEN = "G"
    COLORLESS = "C"

    @property
    def label(self) -> str:
        return _COLOR_LABELS[self]


_COLOR_LABELS: Final[dict[Color, str]] = {
    Color.WHITE: "White",
    Color.BLUE: "Blue",
    Color.BLACK: "Black",
    Color.RED: "Red",
    Color.GREEN: "Green",
    Color.COLORLESS: "Colourless",
}


@dataclass(frozen=True, slots=True)
class Choices:
    """One facet and the values this game offers for it."""

    facet: Facet
    values: tuple[str, ...]
    #: whether more than one value can be picked at once
    multi: bool = False

    @property
    def label(self) -> str:
        return self.facet.label

    def json(self) -> dict[str, Any]:
        return {
            "facet": self.facet.value,
            "label": self.label,
            "multi": self.multi,
            "values": list(self.values),
        }


def facets(game: str, cfg: Config) -> tuple[Choices, ...]:
    """Which facets ``game`` can be filtered by, and the values each offers.

    The values come from the provider where it publishes them — pokemontcg.io
    has ``/v2/rarities``, ``/v2/types``, ``/v2/subtypes`` and ``/v2/supertypes``,
    which is exactly this question already answered — and from an enum here where
    it does not. **A facet with no values is dropped**, because a dropdown with
    nothing in it is worse than an absent one: it reads as "this game has no
    rarities" rather than "the catalog request failed".

    ``set`` and ``year`` are never listed: their values are the expansion list
    (:func:`groups`) and a number, both of which a screen already has.
    """
    found = games.provider_of(game)
    if found is None:
        # a custom game has no catalog endpoints to ask, and an empty dropdown reads
        # as "this game has no rarities" — so it offers no filters at all, which is
        # the same reason a facet whose request failed is dropped rather than shown
        return ()
    return _FACETS[found](cfg)


def _pokemon_facets(cfg: Config) -> tuple[Choices, ...]:
    wanted = (
        (Facet.RARITY, "rarities", False),
        (Facet.SUPERTYPE, "supertypes", False),
        (Facet.TYPE, "types", True),
        (Facet.SUBTYPE, "subtypes", False),
    )
    out: list[Choices] = []
    for facet, endpoint, multi in wanted:
        values = _catalog(f"{_pokemon_root(cfg)}/{endpoint}")
        if values:
            out.append(Choices(facet=facet, values=values, multi=multi))
    return tuple(out)


def _mtg_facets(cfg: Config) -> tuple[Choices, ...]:
    out = [
        Choices(facet=Facet.RARITY, values=tuple(r.value for r in Rarity)),
        Choices(facet=Facet.COLOR, values=tuple(c.value for c in Color), multi=True),
    ]
    # Scryfall's catalog of every creature/artifact/… type is 1500 entries long,
    # which is a search box rather than a dropdown — the *card* types are the
    # short list a filter can show, and they are the ones a proxy printer sorts by
    types = _catalog(f"{cfg.mtg_api_url.rstrip('/')}/catalog/card-types")
    if types:
        out.append(Choices(facet=Facet.TYPE, values=types, multi=True))
    return tuple(out)


def _catalog(url: str) -> tuple[str, ...]:
    """A provider's list-of-strings endpoint, or nothing.

    Total by design: this is asked in order to *draw a control*, long before
    anyone has searched, so a degraded API must cost a dropdown and not the
    screen. Same call :func:`proxdex.upscale.availability` makes for the same
    reason.
    """
    try:
        resp = net.get(url, cache=True, ttl=_CATALOG_TTL)
    except net.NetworkError:
        return ()
    if not resp.ok:
        return ()
    body = resp.json()
    values = body.get("data") if isinstance(body, dict) else None
    if not isinstance(values, list):
        return ()
    return tuple(str(v) for v in values if isinstance(v, str) and v)


#: rarities and type names change when a set introduces one — a week is plenty,
#: and this is the request most likely to be made when nothing else is
_CATALOG_TTL: Final = 7 * 24 * 3600.0
#: the set list gains an entry every few weeks; a day-old copy misses at most the
#: newest preview set, and `where --clear-cache` is the escape hatch
_SETS_TTL: Final = 24 * 3600.0


# ---------------------------------------------------------------- expansions ---
class Grouping(StrEnum):
    """What a game's expansions are grouped *into*, and how the groups order.

    The two are not the same kind of fact, and treating them as one is how a
    grouping gets sorted wrongly. A Pokémon series is an **era**: it has a date,
    and Sword & Shield genuinely comes after XY, so the groups sort by date with
    the newest first. An MTG ``set_type`` is a **kind of product**: a Commander
    deck is not "later" than a core set, so those follow a curated order and a
    date would shuffle them meaninglessly every release.
    """

    #: dated eras, newest first (Pokémon's ``series``)
    ERA = "era"
    #: kinds of product, in a fixed curated order (MTG's ``set_type``)
    KIND = "kind"
    #: a custom game's own sets — one heading, because nobody declared a grouping
    #: and inventing eras for somebody else's game would be making it up
    OWN = "own"

    @property
    def label(self) -> str:
        return _GROUPING_LABELS[self]

    @property
    def plural(self) -> str:
        """Both labels, pluralised — "series" already is, and an ``s`` bolted on
        by the caller made it "seriess"."""
        return _GROUPING_PLURALS[self]


_GROUPING_LABELS: dict[Grouping, str] = {
    Grouping.ERA: "Series",
    Grouping.KIND: "Kind",
    Grouping.OWN: "Sets",
}

_GROUPING_PLURALS: dict[Grouping, str] = {
    Grouping.ERA: "series",
    Grouping.KIND: "kinds",
    Grouping.OWN: "sets",
}

#: how each provider groups its sets. A game with **no** provider groups its sets
#: under one heading: the two built-in groupings are each a fact about a publisher's
#: release history (an era, a kind of product), and proxdex knows neither about a
#: game somebody defined this afternoon — so it says "Sets" rather than inventing a
#: taxonomy and sorting by it.
_GROUPINGS: dict[games.ProviderId, Grouping] = {
    games.ProviderId.POKEMONTCG: Grouping.ERA,
    games.ProviderId.SCRYFALL: Grouping.KIND,
}


def grouping(game: str) -> Grouping:
    found = games.provider_of(game)
    return _GROUPINGS[found] if found is not None else Grouping.OWN


@dataclass(frozen=True, slots=True)
class Expansion:
    """One set, as much as a browse screen needs to show it.

    ``logo_url`` is empty for a game whose provider serves only a symbol —
    Scryfall has an ``icon_svg_uri`` and no wordmark — so a tile has to be able
    to stand on the symbol alone rather than assuming both exist.
    """

    id: str
    name: str
    game: str
    #: the raw group key as the provider spells it (``Sword & Shield``,
    #: ``draft_innovation``)
    group: str
    #: how many cards the set holds, as the provider counts them
    total: int = 0
    #: the number printed on the cards, where it differs from ``total`` — the
    #: secret rares past 102/102 are why a Pokémon set has two counts
    printed_total: int = 0
    #: ISO ``YYYY-MM-DD``; pokemontcg.io writes ``YYYY/MM/DD`` and is normalised
    released: str = ""
    logo_url: str = ""
    symbol_url: str = ""
    #: a set that was never printed on card stock. Kept and marked rather than
    #: dropped: an Alchemy card has no paper printing to proxy, which is worth
    #: *saying* on the tile, and somebody may still want the picture.
    digital: bool = False
    #: the set this one is a supplement to (Scryfall's ``parent_set_code``) —
    #: promos and tokens hang off their parent
    parent: str | None = None

    @property
    def group_label(self) -> str:
        """The group's display name — a ``set_type`` slug spelled for a human."""
        return group_label(self.game, self.group)

    @property
    def year(self) -> str:
        return self.released[:4]

    def json(self, owned: int = 0) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "game": self.game,
            "group": self.group,
            "group_label": self.group_label,
            "total": self.total,
            "printed_total": self.printed_total,
            "released": self.released,
            "year": self.year,
            "logo": self.logo_url,
            "symbol": self.symbol_url,
            "digital": self.digital,
            "parent": self.parent,
            # how many cards of this set the library already holds — the reason
            # a browse screen is worth opening twice
            "owned": owned,
        }


@dataclass(frozen=True, slots=True)
class Group:
    """A run of expansions under one heading."""

    key: str
    label: str
    expansions: tuple[Expansion, ...] = ()

    @property
    def cards(self) -> int:
        return sum(e.total for e in self.expansions)

    @property
    def span(self) -> str:
        """The years this group covers — ``1999`` or ``1999-2003``."""
        years = sorted({e.year for e in self.expansions if e.year})
        if not years:
            return ""
        return years[0] if years[0] == years[-1] else f"{years[0]}-{years[-1]}"

    def json(self, owned: dict[str, int] | None = None) -> dict[str, Any]:
        counts = owned or {}
        return {
            "key": self.key,
            "label": self.label,
            "sets": len(self.expansions),
            "cards": self.cards,
            "span": self.span,
            "owned": sum(counts.get(e.id, 0) for e in self.expansions),
            "expansions": [e.json(counts.get(e.id, 0)) for e in self.expansions],
        }


def expansions(game: str, cfg: Config) -> tuple[Expansion, ...]:
    """Every expansion of ``game``, newest first.

    One request per game (both providers serve the whole list at once), cached for
    a day. This is the read the deleted coverage report used to make — and it was
    deleted for what it *did* with the list, not for reading it: grading each set
    against the frame specs could not work, because MTG's border follows the
    printing rather than the set. Listing the sets so somebody can pick one is the
    honest use, and it is one request rather than one per set.
    """
    return _EXPANSIONS[games.require_provider(game)](cfg)


def groups(game: str, cfg: Config) -> tuple[Group, ...]:
    """:func:`expansions`, gathered under headings in display order."""
    return gather(game, expansions(game, cfg))


def gather(game: str, found: Sequence[Expansion]) -> tuple[Group, ...]:
    """Group and order expansions — pure, so the ordering is testable without a
    provider.

    Within a group it is always newest first: that is the set you are most likely
    reaching for, in both games. Between groups it depends on what a group *is*
    (see :class:`Grouping`).
    """
    buckets: dict[str, list[Expansion]] = {}
    for exp in found:
        buckets.setdefault(exp.group, []).append(exp)
    for bucket in buckets.values():
        bucket.sort(key=lambda e: (e.released, e.id), reverse=True)

    if grouping(game) is Grouping.ERA:
        # an era is dated, so the newest era leads. Its date is its newest set's,
        # which is already this bucket's first entry.
        keys = sorted(buckets, key=lambda k: (buckets[k][0].released, k), reverse=True)
    else:
        order = _KIND_ORDER
        keys = sorted(buckets, key=lambda k: (order.get(k, len(order)), k))
    return tuple(
        Group(key=key, label=group_label(game, key), expansions=tuple(buckets[key]))
        for key in keys
    )


def read_expansion(game: str, row: dict[str, Any]) -> Expansion:
    """One expansion out of a provider's untyped JSON.

    Public because it *is* the boundary: the two APIs agree on almost no key name
    (``series`` vs ``set_type``, ``releaseDate`` vs ``released_at``,
    ``printedTotal`` vs nothing at all) and this is where that becomes one shape.
    Total, like every reader in :mod:`proxdex.sources` — a missing count is 0 and an
    unreadable date is empty, because the alternative is a browse screen that will
    not draw because one set of 1047 has a null in it.
    """
    return _READ[games.require_provider(game)](row)


def find(game: str, set_id: str, cfg: Config) -> Expansion | None:
    """One expansion by id, or None. Off the same cached list — a browse screen
    that has already drawn the index must not spend a request to name the set it
    just navigated into."""
    want = set_id.strip().lower()
    return next((e for e in expansions(game, cfg) if e.id.lower() == want), None)


def declared(game: games.Game) -> tuple[Expansion, ...]:
    """A custom game's own sets, as the expansions every other reader here expects.

    The counterpart to :func:`expansions`, which needs a provider. A custom game's
    sets are **declared** (``games/<id>.json``) rather than discovered, so this is a
    pure conversion with no request in it — and it exists so the frame coverage
    report can ask a custom game the same question it asks Pokémon without knowing
    where the list came from.

    ``logo_url`` and ``symbol_url`` are empty because nobody published set art for
    your game; a tile stands on its name, which :class:`Expansion` already allows for
    a game whose provider serves no wordmark.
    """
    return tuple(
        Expansion(
            id=one.id,
            name=one.name,
            game=game.id,
            group="",
            total=one.total,
            printed_total=one.total,
            released=one.released,
        )
        for one in game.sets
    )


def owned(set_ids: Sequence[str]) -> dict[str, int]:
    """How many cards the library holds per set, from the ids it holds.

    Takes ids rather than cards so this module never imports
    :mod:`proxdex.library` — the same reason :func:`proxdex.specs.audit` takes
    ``(card_id, Resolution)`` pairs.
    """
    counts: dict[str, int] = {}
    for set_id in set_ids:
        counts[set_id] = counts.get(set_id, 0) + 1
    return counts


def group_label(game: str, key: str) -> str:
    """A group key spelled for a person.

    A Pokémon series already is one (``Sword & Shield``). An MTG ``set_type`` is a
    slug, and an unknown one is *titled* rather than dropped: Scryfall adds a set
    type every few years, and a browse screen that silently hides a whole kind of
    product is worse than one showing ``Treasure Chest`` in the wrong place.
    """
    if games.provider_of(game) is not games.ProviderId.SCRYFALL:
        # Pokémon's series names and a custom game's own group keys are already
        # display text; only Scryfall's `set_type` slugs need translating
        return key or ("Sets" if grouping(game) is Grouping.OWN else "Other")
    return _KIND_LABELS.get(key) or key.replace("_", " ").title() or "Other"


#: MTG's ``set_type`` values in the order a browse screen shows them: the sets
#: somebody prints proxies from first, the curiosities last. Scryfall serves 24
#: and this names them all, but the list is not a gate — an unlisted one sorts to
#: the end and keeps its own name (see :func:`group_label`).
_KIND_LABELS: Final[dict[str, str]] = {
    "expansion": "Expansion",
    "core": "Core set",
    "commander": "Commander",
    "masters": "Masters",
    "draft_innovation": "Draft innovation",
    "masterpiece": "Masterpiece",
    "arsenal": "Arsenal",
    "from_the_vault": "From the Vault",
    "spellbook": "Spellbook",
    "premium_deck": "Premium deck",
    "duel_deck": "Duel deck",
    "starter": "Starter",
    "box": "Box set",
    "alchemy": "Alchemy",
    "archenemy": "Archenemy",
    "planechase": "Planechase",
    "vanguard": "Vanguard",
    "funny": "Un-set",
    "promo": "Promo",
    "token": "Token",
    "memorabilia": "Memorabilia",
    "minigame": "Minigame",
    "treasure_chest": "Treasure chest",
    "eternal": "Eternal",
}

_KIND_ORDER: Final[dict[str, int]] = {key: i for i, key in enumerate(_KIND_LABELS)}


# ------------------------------------------------------------------ pokemon ---
def _pokemon_root(cfg: Config) -> str:
    """pokemontcg.io's ``/v2``, from the configured card URL.

    The config names the *card* endpoint (``…/v2/cards/{id}``) because that is
    what every other read needs, so the sibling endpoints are derived from it
    rather than adding four settings that must agree.
    """
    base = cfg.api_url.split("/cards")[0].rstrip("/")
    return base or "https://api.pokemontcg.io/v2"


def _pokemon_expansions(cfg: Config) -> tuple[Expansion, ...]:
    resp = net.get(
        f"{_pokemon_root(cfg)}/sets",
        params={"pageSize": 250, "orderBy": "-releaseDate"},
        cache=True,
        ttl=_SETS_TTL,
    )
    if not resp.ok:
        raise FileError(f"TCG API returned {resp.status} listing Pokémon expansions")
    body = resp.json()
    rows = body.get("data") if isinstance(body, dict) else None
    return tuple(
        read_expansion(games.GameId.POKEMON.value, row)
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and row.get("id")
    )


def _pokemon_expansion(row: dict[str, Any]) -> Expansion:
    images = row.get("images") if isinstance(row.get("images"), dict) else {}
    return Expansion(
        id=str(row.get("id") or ""),
        name=str(row.get("name") or ""),
        game=games.GameId.POKEMON.value,
        group=str(row.get("series") or ""),
        total=_count(row.get("total")),
        printed_total=_count(row.get("printedTotal")),
        released=_iso(row.get("releaseDate")),
        logo_url=str(images.get("logo") or "") if images else "",
        symbol_url=str(images.get("symbol") or "") if images else "",
    )


# ---------------------------------------------------------------------- mtg ---
def _mtg_expansions(cfg: Config) -> tuple[Expansion, ...]:
    resp = net.get(f"{cfg.mtg_api_url.rstrip('/')}/sets", cache=True, ttl=_SETS_TTL)
    if not resp.ok:
        raise FileError(f"Scryfall returned {resp.status} listing Magic sets")
    body = resp.json()
    rows = body.get("data") if isinstance(body, dict) else None
    return tuple(
        read_expansion(games.GameId.MTG.value, row)
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and row.get("code")
    )


def _mtg_expansion(row: dict[str, Any]) -> Expansion:
    count = _count(row.get("card_count"))
    return Expansion(
        id=str(row.get("code") or ""),
        name=str(row.get("name") or ""),
        game=games.GameId.MTG.value,
        group=str(row.get("set_type") or ""),
        total=count,
        # Scryfall counts once; a Magic set has no second "printed" total
        printed_total=count,
        released=_iso(row.get("released_at")),
        # Scryfall serves a set symbol and no wordmark, so there is no logo to
        # carry — a tile stands on the symbol
        symbol_url=str(row.get("icon_svg_uri") or ""),
        digital=row.get("digital") is True,
        parent=str(row["parent_set_code"]) if row.get("parent_set_code") else None,
    )


# ------------------------------------------------------------------ helpers ---
def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _iso(value: Any) -> str:
    """A release date as ``YYYY-MM-DD``.

    pokemontcg.io writes ``1999/01/09`` and Scryfall writes ``1999-01-09``, and
    the two have to sort against each other in one list — so both are normalised
    here rather than at four call sites. Anything unparseable becomes empty,
    which sorts to the end rather than to 1970.
    """
    text = str(value or "").strip().replace("/", "-")
    parts = text.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return ""
    year, month, day = parts
    return f"{year.zfill(4)}-{month.zfill(2)}-{day.zfill(2)}"


#: exported for the CLI/API so neither restates the default page size
DEFAULTS: Final = {"per_page": PER_PAGE, "max_per_page": MAX_PER_PAGE}


def meta(game: str) -> dict[str, Any]:
    """What a screen needs to draw browse controls for ``game``, minus the
    provider reads — the sorts, the grouping and the page sizes, declared once."""
    return {
        "game": game,
        "grouping": grouping(game).value,
        "grouping_label": grouping(game).label,
        "sorts": [
            {"id": s.value, "label": s.label, "newest_first": s.newest_first}
            for s in Sort
        ],
        "facet_labels": {f.value: f.label for f in Facet},
        **DEFAULTS,
    }


# ---------------------------------------------------------------- dispatch ---
# One table per question a provider answers. Same argument as the tables at the
# foot of :mod:`proxdex.sources`: the ``if pokemon … else`` these replaced was
# total only while there were exactly two games, and a custom game reaching that
# ``else`` would have been handed Scryfall's answer for a set it never listed.
_FACETS: dict[games.ProviderId, Callable[[Config], tuple[Choices, ...]]] = {
    games.ProviderId.POKEMONTCG: _pokemon_facets,
    games.ProviderId.SCRYFALL: _mtg_facets,
}

_EXPANSIONS: dict[games.ProviderId, Callable[[Config], tuple[Expansion, ...]]] = {
    games.ProviderId.POKEMONTCG: _pokemon_expansions,
    games.ProviderId.SCRYFALL: _mtg_expansions,
}

_READ: dict[games.ProviderId, Callable[[dict[str, Any]], Expansion]] = {
    games.ProviderId.POKEMONTCG: _pokemon_expansion,
    games.ProviderId.SCRYFALL: _mtg_expansion,
}
