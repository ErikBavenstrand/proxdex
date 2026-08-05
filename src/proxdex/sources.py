"""Card metadata and image downloads, one provider per game.

Pokémon comes from the Pokémon TCG API (metadata) plus scrydex (images); MTG
comes from Scryfall (both). The rest of proxdex only sees :class:`CardMeta` /
:class:`SearchResult`, which carry their own ``image_url`` — so adding a game
means adding a provider here and a :class:`proxdex.games.Game`, nothing else.

**A game need not have a provider at all**, which is what a custom game
(``<root>/games/<id>.json``) is: you bring the pictures, ``import`` files them, and
nothing in this module is ever asked. That case is a *value* rather than a fallen-
through branch — see :func:`provider` and the dispatch tables at the foot of the
file — because the branch it replaced would have asked Scryfall about a card of a
game Scryfall has never heard of.

Every request goes through :mod:`proxdex.net`, which rate-limits, retries and
caches, so a provider having a bad day (scrydex 500s often) is survivable here
rather than something each call site has to think about.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np
from PIL import Image

from proxdex import games, net
from proxdex.browse import Facet, Page, Query, Sort
from proxdex.config import Config
from proxdex.errors import FileError
from proxdex.frames import GuideId
from proxdex.games import Layout


@dataclass(frozen=True, slots=True)
class FaceImage:
    """One printable side of a card.

    Most cards have exactly one. MTG's transform and modal double-faced cards
    have two, each with its own name and its own image — and proxdex treats them
    as two faces of one card, not two cards, because they share an id.
    """

    #: what the provider calls this side ("Delver of Secrets", "Insectile
    #: Aberration"); empty for a single-faced card, which needs no label
    name: str
    #: direct link to the highest-resolution image this provider offers
    image_url: str


class Relation(StrEnum):
    """How another card relates to this one, as the provider classified it."""

    MELD_PART = "meld-part"
    MELD_RESULT = "meld-result"
    TOKEN = "token"  # noqa: S105 (a card, not a credential)
    COMBO = "combo-piece"
    OTHER = "other"

    @property
    def label(self) -> str:
        return _RELATION_LABELS[self]


_RELATION_LABELS: dict[Relation, str] = {
    Relation.MELD_PART: "Melds with",
    Relation.MELD_RESULT: "Melds into",
    Relation.TOKEN: "Makes token",
    Relation.COMBO: "Goes with",
    Relation.OTHER: "Related",
}


@dataclass(frozen=True, slots=True)
class Related:
    """Another card this one is printed alongside — a meld partner, the melded
    card, a token it makes.

    A meld pair is three physical cards (both halves and the melded card), each
    with its own id and its own picture, so proxdex files them as three cards and
    records the relationship instead of pretending one card has three sides.
    """

    relation: Relation
    name: str
    #: the proxdex card id, resolved from the provider's own reference. Empty when
    #: the reference could not be resolved — a degraded API loses the link, not
    #: the fact that a related card exists.
    id: str = ""


@dataclass(slots=True)
class CardMeta:
    id: str
    name: str
    set_id: str
    set_name: str
    game: str
    #: every printable side, front first — always at least one entry
    faces: tuple[FaceImage, ...]
    #: what goes on paper: one side, two, or half of a meld pair
    layout: Layout = Layout.SINGLE
    #: printed at 89×127mm rather than 63×88 (planar, scheme, Vanguard)
    oversized: bool = False
    #: the frame spec this printing needs, when the provider says outright — a
    #: borderless print has no frame to fit against whatever its set's era says.
    #: Full-art is *not* one of these: its border is ordinary. None = the rules
    #: decide, which is the case for every card whose border is a normal one.
    frame: str | None = None
    #: what the provider said about this printing, as the facts a frame rule can
    #: match on (rarity, subtypes, finishes, full-art). Recorded per card at fetch
    #: so picking a spec never needs a second API call — see
    #: :meth:`proxdex.library.Card.write_traits`.
    traits: dict[str, str] = field(default_factory=dict)

    @property
    def image_url(self) -> str:
        """The front face's image — what a single-faced caller means."""
        return self.faces[0].image_url if self.faces else ""

    @property
    def face_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.faces)


def one_face(image_url: str) -> tuple[FaceImage, ...]:
    """A single-faced card's face tuple."""
    return (FaceImage("", image_url),)


@dataclass(slots=True)
class SearchResult:
    """A card returned by :func:`search`, with metadata for picking."""

    id: str
    name: str
    set_id: str
    set_name: str
    series: str
    year: str
    number: str
    printed_total: str
    rarity: str
    artist: str
    game: str
    faces: tuple[FaceImage, ...]
    layout: Layout = Layout.SINGLE
    oversized: bool = False
    frame: str | None = None
    traits: dict[str, str] = field(default_factory=dict)
    #: A *smaller* scan for the tile, where the provider publishes one. Empty means
    #: it does not, and :attr:`thumb` then falls back to the full image.
    #:
    #: A result row is looked at, not kept: 60 of them at 190px each. Pokémon's
    #: ``/small`` is 245px and ~30 KB against ``/large``'s 825 KB — a 25x saving on
    #: the fetch a browse page actually makes, which is the cost the art cache could
    #: not remove because it is what fills the cache in the first place. Kept a
    #: separate field rather than swapping a suffix at the point of use, so nothing
    #: downstream can *file* a thumbnail: :attr:`image_url` is what `fetch` downloads,
    #: and it is untouched.
    thumb_url: str = ""

    @property
    def image_url(self) -> str:
        return self.faces[0].image_url if self.faces else ""

    @property
    def thumb(self) -> str:
        """The picture to draw in a result tile — the small one where there is one."""
        return self.thumb_url or self.image_url

    def to_meta(self) -> CardMeta:
        return CardMeta(
            id=self.id,
            name=self.name,
            set_id=self.set_id,
            set_name=self.set_name,
            game=self.game,
            faces=self.faces,
            layout=self.layout,
            oversized=self.oversized,
            frame=self.frame,
            traits=dict(self.traits),
        )


@dataclass(frozen=True, slots=True)
class CardBrief:
    """One card of a set, reduced to what choosing a frame spec needs.

    Not a :class:`SearchResult`: no image, no artist, no prices. This exists so a
    rule can be *previewed* over a whole set cheaply, and it carries exactly the
    traits :func:`proxdex.specs.Rule.selects` reads.
    """

    id: str
    name: str
    number: str
    rarity: str
    traits: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Fact:
    """One label → value row of a card's data, as the provider stated it."""

    label: str
    value: str
    #: prose (rules, oracle text, flavour) — reads as a paragraph, not a row
    block: bool = False


@dataclass(frozen=True, slots=True)
class FactGroup:
    title: str
    facts: tuple[Fact, ...]


@dataclass(frozen=True, slots=True)
class Link:
    label: str
    url: str


@dataclass(slots=True)
class CardDetail:
    """Everything a provider says about one card, read into display order."""

    meta: CardMeta
    #: which API answered, for attribution in the UI
    source: str
    groups: list[FactGroup] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    #: other cards printed alongside this one — meld halves, the melded card,
    #: the tokens it makes. Each is a card you can fetch in its own right.
    related: tuple[Related, ...] = ()


# ------------------------------------------------------------------ public ---
#: which provider answers for a game, or a refusal naming ``import``. Declared in
#: :mod:`proxdex.games` because :mod:`proxdex.browse` needs the identical answer and
#: cannot import this module — one decision, so neither copy can grow an ``else``.
provider = games.require_provider


def lookup(cid: str, cfg: Config, game: str = games.DEFAULT) -> CardMeta:
    """Resolve a card id to its name, set and image URL in ``game``."""
    return _LOOKUP[provider(game)](cid, cfg)


def lookup_any(cid: str, cfg: Config, game: str | None = None) -> CardMeta:
    """Resolve a card id without knowing its game: try ``game`` if given, else
    the library's default game first and the others after.

    Only games with a provider are tried when the game is unknown
    (:meth:`~proxdex.games.Registry.provider_order`) — a custom game has no API to
    ask, so including it would collect an error about a request nobody can make.
    """
    errors: list[str] = []
    for candidate in (game,) if game else games.load().provider_order(cfg.library_game):
        try:
            return lookup(cid, cfg, candidate)
        except FileError as exc:
            errors.append(str(exc))
    raise FileError("; ".join(errors) or f"{cid}: not found")


def details(cid: str, cfg: Config, game: str | None = None) -> CardDetail:
    """Everything the provider knows about ``cid`` — facts, links and raw JSON.

    Like :func:`lookup_any`, an unknown game means trying the library's default
    first and the others after.
    """
    errors: list[str] = []
    for candidate in (game,) if game else games.load().provider_order(cfg.library_game):
        try:
            return _details(cid, cfg, candidate)
        except FileError as exc:
            errors.append(str(exc))
    raise FileError("; ".join(errors) or f"{cid}: not found")


def query_string(query: Query) -> str:
    """``query`` as the search string the provider for its game understands.

    Public because it is the translation nothing else can check: pokemontcg.io and
    Scryfall share no syntax, and a filter spelled slightly wrong does not fail — it
    returns *plausible but different* cards. `set.releaseDate:[a TO b]` is a real 400
    from one and `c:wu` really means "both colours" to the other, so the spellings
    are pinned by :mod:`tests.test_browse` rather than trusted.
    """
    return _QUERY[provider(query.game)](query)


def provider_sort(game: str, sort: Sort) -> str:
    """How ``game``'s provider spells ``sort``.

    Every :class:`proxdex.browse.Sort` must have an answer for every game — a sort
    the UI offers and the provider rejects is a 400 halfway through a browse — which
    is a completeness claim, so it is checked rather than assumed.
    """
    return _ORDER[provider(game)][sort]


def search_page(query: Query, cfg: Config) -> Page[SearchResult]:
    """One page of the cards matching ``query``, and how many there are in all.

    **Every filter is pushed to the provider**, which is the whole reason this
    replaced a function that fetched a hundred rows and sieved them locally. A
    local sieve cannot count: it knows how many of *its* hundred matched and
    nothing about the rest, so a screen could only ever say "100 results" for a
    set of 553 — and page 2 would re-fetch the same hundred and filter it again.
    Both APIs can filter and count server-side, so both are asked to.

    A query that narrows nothing returns an empty page rather than the first
    screenful of every card ever printed (:attr:`Query.narrowed`): a search box
    with nothing typed in it has not asked a question, and *browsing* always
    carries at least a set.
    """
    if not query.narrowed:
        return Page(items=(), page=query.page, per_page=query.per_page, total=0)
    return _SEARCH_PAGE[provider(query.game)](query, cfg)


def set_cards(set_id: str, cfg: Config, game: str = games.DEFAULT) -> list[CardBrief]:
    """Every card in one set, with the traits a frame rule matches on.

    Deliberately per-set and never "every card of every set": one set is a page or
    two, and the only thing this is for is showing which cards a rule catches
    *before* it is saved.
    """
    return _SET_CARDS[provider(game)](set_id, cfg)


#: **The image host answers 200 for a card it does not have, with a placeholder.**
#: ``images.scrydex.com/pokemon/<id>/large`` serves a grey "no image" card for any id
#: it does not know — verified with ``base1-999`` and ``zzzz-1``, which return the
#: byte-identical file — so the HTTP status cannot tell a hit from a miss. That is
#: the whole reason a Pokémon fetch resolves the id against the metadata API first,
#: even though the image URL needs nothing but the id.
#:
#: It is identifiable, though: the placeholder is **640×892 in palette mode**, and no
#: real scan is either of those — they arrive 600×825 RGB (the older sets) or
#: ~734×1024 RGBA (the modern ones). Both conditions are required, so a real card can
#: never be refused by this; the cost of the check being out of date one day is a
#: placeholder filed as it would have been anyway, not a good card rejected.
_PLACEHOLDER_SIZE = (640, 892)
_PLACEHOLDER_MODE = "P"


def is_placeholder(im: Image.Image) -> bool:
    """Whether this is the image host's "no image for that id" card."""
    return im.size == _PLACEHOLDER_SIZE and im.mode == _PLACEHOLDER_MODE


def known_meta(
    cid: str,
    cfg: Config,
    *,
    name: str,
    set_name: str = "",
    rarity: str = "",
    subtypes: str = "",
) -> CardMeta:
    """A Pokémon card's metadata from what a caller already read, no request at all.

    **Pokémon only, and the reason is the image URL.** A Pokémon card's picture is
    ``Config.scrydex_url.format(id=...)`` — derivable from the id — so a caller that
    already knows the card's name and set (a search result, a browse row) has
    everything needed to file it. Scryfall's image URLs are UUID paths that only its
    own response carries, so an MTG card cannot be filed without asking, and this
    refuses rather than pretending.

    Safe because :func:`download` refuses the image host's placeholder. Skipping the
    lookup means skipping the check that the id *exists*, and the host answers 200 with
    a grey card for one that does not — so the placeholder check is what makes this path
    honest rather than merely fast.
    """
    if not name.strip():
        raise FileError(f"{cid}: cannot file a card with no name")
    return CardMeta(
        id=cid,
        name=name.strip(),
        # `<set>-<number>`, and a Pokémon collector number never contains a hyphen —
        # so the set is everything before the last one
        set_id=cid.rsplit("-", 1)[0],
        set_name=set_name.strip() or cid.rsplit("-", 1)[0],
        game=games.GameId.POKEMON.value,
        faces=one_face(cfg.scrydex_url.format(id=cid)),
        traits=_pokemon_traits({"rarity": rarity, "subtypes": subtypes.split(",")}),
    )


def local_meta(
    cid: str,
    game: games.Game,
    *,
    name: str = "",
    faces: int = 1,
) -> CardMeta:
    """A custom game's card, described from what the person filing it said.

    The third way a :class:`CardMeta` comes into being, beside a provider answer and
    :func:`known_meta`, and the only one with **no image URL at all**: a custom game
    has no host to download from, so the picture arrives through ``import`` and the
    faces here are placeholders that exist to say how many sides the card has.

    It is a :class:`CardMeta` rather than a new shape precisely so nothing
    downstream has to care: ``_card_from_meta`` names the folder, ``write_kind``
    records the layout, and every step after the original stage never knew which
    API answered.

    The set id is read off the card id the same way :func:`known_meta` reads it, and
    the set's *name* comes from the game's own declaration — which is what makes
    declaring sets worth doing: without it the folder would be named after the id.
    """
    set_id = cid.rsplit("-", 1)[0]
    sides = max(1, min(faces, _MAX_FACES))
    return CardMeta(
        id=cid,
        name=name.strip() or cid,
        set_id=set_id,
        set_name=game.set_name(set_id),
        game=game.id,
        # named sides only when there are two, matching what a provider gives us:
        # a single-faced card's face carries no label anywhere in proxdex
        faces=tuple(
            FaceImage("" if sides == 1 else f"Side {n + 1}", "") for n in range(sides)
        ),
        layout=Layout.DOUBLE if sides > 1 else Layout.SINGLE,
    )


def download(meta: CardMeta, face: int = 0) -> Image.Image:
    """Download one face of a card's image, normalized to RGB."""
    if face >= len(meta.faces):
        raise FileError(f"{meta.id}: no face {face + 1}")
    url = meta.faces[face].image_url
    if not url:
        raise FileError(f"{meta.id}: this API offers no image for face {face + 1}")
    resp = _get(
        url, accept="image/*", cache=False, what=meta.id, attempts=net.PATIENT_ATTEMPTS
    )
    if not resp.ok:
        raise FileError(f"{meta.id}: {resp.status} for {url}")
    im = Image.open(io.BytesIO(resp.body))
    if is_placeholder(im):
        # refused rather than filed: a grey rectangle is indistinguishable from a
        # card until it reaches paper, and every later stage would happily reshape,
        # upscale and impose it
        raise FileError(
            f"{meta.id}: the image host has no scan for this card — it answered with "
            f"its placeholder ({url})"
        )
    return flatten(im)


def _details(cid: str, cfg: Config, game: str) -> CardDetail:
    which = provider(game)
    source = games.load().name_of(game)
    found = games.builtin(game)
    source = found.source if found is not None else source
    if which is games.ProviderId.POKEMONTCG:
        data = _pokemon_data(cid, cfg)
        return CardDetail(
            meta=_pokemon_meta(data, cfg),
            source=source,
            groups=_pokemon_groups(data),
            links=_pokemon_links(data),
        )
    data = _mtg_data(cid, cfg)
    return CardDetail(
        meta=_mtg_meta(data),
        source=source,
        groups=_mtg_groups(data),
        links=_mtg_links(data),
        related=_mtg_related(data),
    )


# ------------------------------------------------------------------ pokemon --
def _pokemon_lookup(cid: str, cfg: Config) -> CardMeta:
    return _pokemon_meta(_pokemon_data(cid, cfg), cfg)


def _pokemon_data(cid: str, cfg: Config) -> dict[str, Any]:
    resp = _get(cfg.api_url.format(id=cid), what=cid, attempts=net.PATIENT_ATTEMPTS)
    if resp.status == 404:
        raise FileError(f"{cid}: not found in the Pokémon TCG API")
    if not resp.ok:
        raise FileError(f"{cid}: TCG API returned {resp.status}")
    return _obj(resp.json().get("data"))


def _pokemon_meta(data: dict[str, Any], cfg: Config) -> CardMeta:
    return CardMeta(
        id=data["id"],
        name=data["name"],
        set_id=data["set"]["id"],
        set_name=data["set"]["name"],
        game=games.GameId.POKEMON.value,
        # Pokémon cards are printed on one side; the back is the shared TPC back
        faces=one_face(cfg.scrydex_url.format(id=data["id"])),
        traits=_pokemon_traits(data),
    )


def _pokemon_base(cfg: Config) -> str:
    """The API root behind the configured card URL — ``…/v2`` out of
    ``…/v2/cards/{id}``, so the sets endpoint needs no second setting to drift."""
    return cfg.api_url.split("/cards")[0].rstrip("/")


#: pokemontcg.io's maximum, and roughly the number of sets that exist — so the
#: whole list is one request in practice, with the loop there for the year it isn't
_PAGE = 250
#: how many pages a paged read will ever ask for. A set with more than 1000 cards
#: does not exist; a provider looping forever does.
_MAX_PAGES = 4


def _pokemon_set_cards(set_id: str, cfg: Config) -> list[CardBrief]:
    out: list[CardBrief] = []
    for page in range(1, _MAX_PAGES + 1):
        resp = _get(
            f"{_pokemon_base(cfg)}/cards",
            what=set_id,
            params={
                "q": f"set.id:{set_id}",
                "page": page,
                "pageSize": _PAGE,
                "orderBy": "number",
                "select": "id,name,number,rarity,subtypes",
            },
        )
        if not resp.ok:
            raise FileError(f"Pokémon TCG API returned {resp.status} listing {set_id}")
        rows = _objs(resp.json().get("data"))
        out += [
            CardBrief(
                id=str(row.get("id") or ""),
                name=str(row.get("name") or ""),
                number=str(row.get("number") or ""),
                rarity=str(row.get("rarity") or ""),
                traits=_pokemon_traits(row),
            )
            for row in rows
            if row.get("id")
        ]
        if len(rows) < _PAGE:
            break
    return out


def _pokemon_traits(data: dict[str, Any]) -> dict[str, str]:
    """The facts a frame rule can match a Pokémon printing on.

    Rarity and subtypes only: pokemontcg.io has no full-art or finish flag, and a
    trait proxdex invents is a trait a rule would silently mismatch on. What is
    *not* recorded is as much the point as what is — a rule needing a finish then
    reports that it cannot decide rather than quietly not matching.
    """
    return {
        "rarity": str(data.get("rarity") or ""),
        "subtypes": ",".join(_strs(data.get("subtypes"))),
    }


def _pokemon_groups(data: dict[str, Any]) -> list[FactGroup]:
    card_set = _obj(data.get("set"))
    number = _fact("Number", data.get("number"))
    total = str(card_set.get("printedTotal") or "")
    return _groups(
        _group(
            "Card",
            [
                _fact("Kind", [data.get("supertype"), *_strs(data.get("subtypes"))]),
                _fact("HP", data.get("hp")),
                _fact("Energy", _strs(data.get("types"))),
                _fact("Evolves from", data.get("evolvesFrom")),
                _fact("Evolves to", _strs(data.get("evolvesTo"))),
                _fact("Level", data.get("level")),
                _fact("Pokédex", _strs(data.get("nationalPokedexNumbers")), sep=", "),
                _fact("Regulation", data.get("regulationMark")),
                *(_fact("Rule", r, block=True) for r in _strs(data.get("rules"))),
            ],
        ),
        _group(
            "Attacks & abilities",
            [
                *(
                    _fact(
                        _join([a.get("type"), a.get("name")], " · ") or "Ability",
                        a.get("text"),
                        block=True,
                    )
                    for a in _objs(data.get("abilities"))
                ),
                *(
                    _fact(
                        str(a.get("name") or "Attack"),
                        [
                            _energy(a.get("cost")),
                            _damage(a.get("damage")),
                            a.get("text"),
                        ],
                        block=True,
                    )
                    for a in _objs(data.get("attacks"))
                ),
                _fact("Weakness", _typed(data.get("weaknesses"))),
                _fact("Resistance", _typed(data.get("resistances"))),
                # a trainer/energy card has no retreat cost at all; one that
                # costs nothing to retreat says so
                _fact(
                    "Retreat",
                    _energy(data.get("retreatCost")) or "none"
                    if "retreatCost" in data
                    else None,
                ),
            ],
        ),
        _group(
            "Print",
            [
                Fact(
                    number.label, f"{number.value}/{total}" if total else number.value
                ),
                _fact("Rarity", data.get("rarity")),
                _fact("Artist", data.get("artist")),
                _fact("Flavour", data.get("flavorText"), block=True),
            ],
        ),
        _group(
            "Set",
            [
                _fact("Set", card_set.get("name")),
                _fact("Code", card_set.get("id")),
                _fact("Series", card_set.get("series")),
                _fact("Released", card_set.get("releaseDate")),
                _fact("Cards", card_set.get("total")),
                _fact("PTCGO", card_set.get("ptcgoCode")),
            ],
        ),
        _group("Legality", _legal(data.get("legalities"))),
        _group(
            "Prices",
            [
                *(
                    _fact(f"TCGplayer {variant}", _money(_obj(p).get("market"), "$"))
                    for variant, p in _prices(data.get("tcgplayer")).items()
                ),
                _fact(
                    "Cardmarket trend",
                    _money(_prices(data.get("cardmarket")).get("trendPrice"), "€"),
                ),
            ],
        ),
    )


def _pokemon_links(data: dict[str, Any]) -> list[Link]:
    game = games.POKEMON
    card_set = _obj(data.get("set"))
    links: list[Link] = []
    if game.card_page and card_set.get("id") and data.get("number"):
        links.append(
            Link(
                game.card_page_name,
                game.card_page.format(
                    set=card_set["id"], number=str(data["number"]).lstrip("0") or "0"
                ),
            )
        )
    for key, label in (("tcgplayer", "TCGplayer"), ("cardmarket", "Cardmarket")):
        url = str(_obj(data.get(key)).get("url") or "")
        if url:
            links.append(Link(label, url))
    return links


#: how pokemontcg.io spells each :class:`Sort`. Its ``number`` is a *string*
#: field, so that ordering is lexicographic (11 before 2) — which is the API's
#: answer, not something to paper over locally: a local re-sort would only reorder
#: the page in hand and lie about the rest.
_POKEMON_ORDER: dict[Sort, str] = {
    Sort.RELEASED: "set.releaseDate",
    Sort.NAME: "name",
    Sort.NUMBER: "number",
    Sort.RARITY: "rarity",
}


def _pokemon_query(query: Query) -> str:
    """``query`` as one pokemontcg.io ``q`` string.

    Values are **quoted**, not wildcarded: every one of them comes from the
    provider's own catalog (``/v2/rarities`` and friends, via
    :func:`proxdex.browse.facets`), so an exact match is what the user picked. The
    card *name* is the exception — that is typed, so it is wildcarded.

    **The typed words are joined into one term with wildcards between them**, not
    emitted one term each. One term per word was a real bug and a subtle one: a space
    separates *terms* in this API's syntax, so ``Moo Moo`` became
    ``name:*Moo* name:*Moo*`` — two identical substring tests, which any name holding
    "moo" once satisfies. So it was exactly equivalent to searching ``Moo``, returned
    Amoonguss, Bloodmoon Ursaluna and Roaring Moon, and buried the card asked for
    under everything newer. ``name:*Moo*Moo*`` returns the three cards that exist.

    Joining also handles the thing that makes Pokémon names hard, which is that the
    separator is not reliable: the card is **Moo-Moo Milk** in Neo and **Moomoo Milk**
    in HeartGold, so neither ``name:"Moo Moo"`` nor ``name:"Moo Moo Milk"`` matches
    anything at all (measured: both 0 results). A wildcard where the user typed a space
    matches a space, a hyphen, a dot or nothing — which is why ``mr mime`` finds
    *Mr. Mime* (35) and ``char zard`` finds *Charizard* (108).
    """
    parts: list[str] = []
    if query.words:
        # one term, wildcards between the words — never one term per word
        parts.append("name:*" + "*".join(query.words) + "*")
    if query.set_id:
        # a set the browse screen chose is an id; a set somebody typed may be a
        # name, and pokemontcg.io can match either
        if re.fullmatch(r"[a-z]+[a-z0-9.]*", query.set_id, re.IGNORECASE):
            parts.append(f"set.id:{query.set_id}")
        else:
            parts.append(f"set.name:*{query.set_id}*")
    for facet, value in (
        (Facet.RARITY, query.rarity),
        (Facet.SUPERTYPE, query.supertype),
        (Facet.SUBTYPE, query.subtype),
    ):
        if value:
            parts.append(f'{_POKEMON_FIELD[facet]}:"{value}"')
    parts += [f'types:"{t}"' for t in query.types]
    if query.year:
        # `set.releaseDate:[a TO b]` is a 400 from this API; the dates are
        # `YYYY/MM/DD` strings, so a prefix wildcard is the year filter it does
        # support — and it is exact, not a range that might drift
        parts.append(f"set.releaseDate:{query.year}*")
    return " ".join(parts)


_POKEMON_FIELD: dict[Facet, str] = {
    Facet.RARITY: "rarity",
    Facet.SUPERTYPE: "supertype",
    Facet.SUBTYPE: "subtypes",
}


def _pokemon_page(query: Query, cfg: Config) -> Page[SearchResult]:
    base = cfg.api_url.replace("/{id}", "")
    order = provider_sort(query.game, query.sort)
    params = {
        "q": query_string(query),
        "orderBy": f"-{order}" if query.descending else order,
        "page": query.page,
        "pageSize": query.per_page,
        # `subtypes` earns its 5% of response size: without it a card added from a
        # search carried *no* traits at all while the same card added by `fetch`
        # carried rarity and subtypes — so a frame rule matching a subtype answered
        # differently depending on which verb filed the card
        "select": "id,name,number,rarity,artist,subtypes,set",
    }
    resp = _get(base, params=params, ttl=net.SEARCH_TTL)
    if not resp.ok:
        raise FileError(f"TCG API search failed ({resp.status}) for {query.text!r}")
    body = resp.json()
    rows = _objs(body.get("data"))
    return Page(
        items=tuple(_pokemon_result(row, cfg) for row in rows if row.get("id")),
        page=query.page,
        per_page=query.per_page,
        # the API counts every match, not just this page — which is what lets a
        # screen say "60 of 553" and offer a last page to jump to
        total=_int(body.get("totalCount")),
    )


def _pokemon_result(data: dict[str, Any], cfg: Config) -> SearchResult:
    card_set = _obj(data.get("set"))
    return SearchResult(
        id=str(data["id"]),
        name=str(data.get("name") or ""),
        set_id=str(card_set.get("id") or ""),
        set_name=str(card_set.get("name") or ""),
        series=str(card_set.get("series") or ""),
        year=str(card_set.get("releaseDate") or "").split("/")[0],
        number=str(data.get("number") or ""),
        printed_total=str(card_set.get("printedTotal") or ""),
        rarity=str(data.get("rarity") or "—"),
        artist=str(data.get("artist") or "—"),
        game=games.GameId.POKEMON.value,
        faces=one_face(cfg.scrydex_url.format(id=data["id"])),
        # the tile's picture only — `faces` above is still what `fetch` files
        thumb_url=cfg.scrydex_thumb_url.format(id=data["id"]),
        traits=_pokemon_traits(data),
    )


# ---------------------------------------------------------------------- mtg --
def _mtg_lookup(cid: str, cfg: Config) -> CardMeta:
    return _mtg_meta(_mtg_data(cid, cfg))


def _mtg_data(cid: str, cfg: Config) -> dict[str, Any]:
    # <set>-<collector number>; MTG set codes never contain '-', but collector
    # numbers do (Alchemy rebalances are "A-123"), so split on the first one.
    set_code, _, number = cid.partition("-")
    if not set_code or not number:
        raise FileError(f"{cid}: not an MTG card id (expected e.g. neo-136)")
    resp = _get(
        f"{cfg.mtg_api_url.rstrip('/')}/cards/{set_code}/{number}",
        what=cid,
        attempts=net.PATIENT_ATTEMPTS,
    )
    if resp.status == 404:
        raise FileError(f"{cid}: not found on Scryfall")
    if not resp.ok:
        raise FileError(f"{cid}: Scryfall returned {resp.status}")
    return _obj(resp.json())


#: **Scryfall pages at a fixed 175 and will not be talked down.** proxdex's page
#: size is a screenful, so the two do not line up and the window has to be cut out
#: of whichever Scryfall pages contain it — see :func:`_mtg_window`.
MTG_PAGE_SIZE = 175

#: how Scryfall spells each :class:`Sort`. ``number`` is its ``set`` order, which
#: is "by set, then collector number" — the order a set's own cards are in, which
#: is what asking to sort by number means while browsing one.
_MTG_ORDER: dict[Sort, str] = {
    Sort.RELEASED: "released",
    Sort.NAME: "name",
    Sort.NUMBER: "set",
    Sort.RARITY: "rarity",
}


def _mtg_query(query: Query) -> str:
    """``query`` as one Scryfall search string."""
    parts = [f'name:"{word}"' for word in query.text.replace('"', "").split() if word]
    if query.set_id:
        parts.append(f"set:{query.set_id}")
    if query.rarity:
        parts.append(f"r:{query.rarity}")
    if query.year:
        parts.append(f"year:{query.year}")
    parts += [f't:"{t}"' for t in query.types]
    if query.colors:
        # ORed, not ANDed: picking White and Blue in a filter means "either",
        # while Scryfall's bare `c:wu` means a card that is *both*, which for two
        # colours is a handful of cards and reads as a broken filter
        parts.append("(" + " or ".join(f"c:{c.lower()}" for c in query.colors) + ")")
    return " ".join(parts)


def _mtg_page(query: Query, cfg: Config) -> Page[SearchResult]:
    """One display page, cut out of Scryfall's 175-card pages.

    A display page of 60 straddles a Scryfall page boundary two times in three, so
    this fetches the Scryfall pages the window covers and slices. It is at most
    two requests for a 60-card page (three at the 250 maximum), both of them
    cached — and it is the alternative to paging the *whole* result set locally,
    which for a 553-card set would be four requests to draw the first screen.
    """
    order = provider_sort(query.game, query.sort)
    url = f"{cfg.mtg_api_url.rstrip('/')}/cards/search"
    text = query_string(query)
    offset = (query.page - 1) * query.per_page
    first = offset // MTG_PAGE_SIZE + 1
    rows: list[dict[str, Any]] = []
    total = -1
    for provider_page in range(first, first + mtg_page_span(offset, query.per_page)):
        resp = _get(
            url,
            params={
                "q": text,
                "unique": "prints",
                "order": order,
                "dir": "desc" if query.descending else "asc",
                "page": provider_page,
            },
            ttl=net.SEARCH_TTL,
        )
        if resp.status == 404:  # Scryfall's "no cards matched"
            return Page(items=(), page=query.page, per_page=query.per_page, total=0)
        if not resp.ok:
            raise FileError(f"Scryfall search failed ({resp.status}) for {text!r}")
        body = resp.json()
        total = _int(body.get("total_cards"))
        rows += _objs(body.get("data"))
        if not body.get("has_more"):
            break
    window = rows[offset % MTG_PAGE_SIZE :][: query.per_page]
    return Page(
        items=tuple(
            _mtg_result(row)
            for row in window
            if row.get("set") and row.get("collector_number")
        ),
        page=query.page,
        per_page=query.per_page,
        total=total,
    )


def mtg_page_span(offset: int, per_page: int) -> int:
    """How many of Scryfall's fixed 175-card pages a display page can touch.

    Public for the same reason :func:`query_string` is: an error here drops or repeats
    a card, and neither looks like a bug on a screen of thumbnails.
    """
    start = offset // MTG_PAGE_SIZE
    end = (offset + per_page - 1) // MTG_PAGE_SIZE
    return end - start + 1


def _mtg_meta(data: dict[str, Any]) -> CardMeta:
    return CardMeta(
        id=f"{data['set']}-{data['collector_number']}",
        name=str(data.get("name", "")),
        set_id=str(data.get("set", "")),
        set_name=str(data.get("set_name", "")),
        game=games.GameId.MTG.value,
        faces=_mtg_faces(data),
        layout=_mtg_layout(data),
        oversized=data.get("oversized") is True,
        frame=mtg_frame(data),
        traits=mtg_traits(data),
    )


def _mtg_result(data: dict[str, Any]) -> SearchResult:
    return SearchResult(
        id=f"{data['set']}-{data['collector_number']}",
        name=str(data.get("name", "")),
        set_id=str(data.get("set", "")),
        set_name=str(data.get("set_name", "")),
        series=str(data.get("set_type", "")),
        year=str(data.get("released_at", ""))[:4],
        number=str(data.get("collector_number", "")),
        # Scryfall's card object carries no set total; the number stands alone
        printed_total="",
        rarity=str(data.get("rarity") or "—").title(),
        artist=str(data.get("artist") or "—"),
        game=games.GameId.MTG.value,
        faces=_mtg_faces(data),
        # the tile's picture only — `faces` is still the lossless PNG `fetch` files
        thumb_url=_mtg_thumb(_mtg_face_uris(data)),
        layout=_mtg_layout(data),
        oversized=data.get("oversized") is True,
        frame=mtg_frame(data),
        traits=mtg_traits(data),
    )


def _mtg_groups(data: dict[str, Any]) -> list[FactGroup]:
    faces = _objs(data.get("card_faces"))
    return _groups(
        _group(
            "Card",
            [
                _fact("Cost", data.get("mana_cost")),
                _fact("Mana value", _number(data.get("cmc"))),
                _fact("Type", data.get("type_line")),
                _fact("Stats", _stats(data)),
                _fact("Colours", _strs(data.get("colors"))),
                _fact("Identity", _strs(data.get("color_identity"))),
                _fact("Keywords", _strs(data.get("keywords"))),
                _fact("Oracle", data.get("oracle_text"), block=True),
                _fact("Flavour", data.get("flavor_text"), block=True),
            ],
        ),
        # a double-faced card's rules live on the faces; proxdex prints the
        # front, but both are worth reading before you commit ink
        _group(
            "Faces",
            [
                _fact(
                    _join([f.get("name"), f.get("mana_cost")], " "),
                    _join([f.get("type_line"), _stats(f), f.get("oracle_text")], " · "),
                    block=True,
                )
                for f in faces
            ],
        ),
        _group(
            "Print",
            [
                _fact("Number", data.get("collector_number")),
                _fact("Rarity", str(data.get("rarity") or "").title()),
                _fact("Artist", data.get("artist")),
                _fact("Released", data.get("released_at")),
                _fact("Frame", [data.get("frame"), data.get("border_color")]),
                _fact("Layout", data.get("layout")),
                _fact("Finishes", _strs(data.get("finishes"))),
                _fact("Language", data.get("lang")),
                _fact("Traits", _flags(data)),
            ],
        ),
        _group(
            "Set",
            [
                _fact("Set", data.get("set_name")),
                _fact("Code", str(data.get("set") or "").upper()),
                _fact("Kind", str(data.get("set_type") or "").replace("_", " ")),
                _fact("Playable in", _strs(data.get("games"))),
            ],
        ),
        _group("Legality", _legal(data.get("legalities"))),
        _group(
            "Prices",
            [
                _fact(label, _money(_obj(data.get("prices")).get(key), sign))
                for key, label, sign in (
                    ("usd", "USD", "$"),
                    ("usd_foil", "USD foil", "$"),
                    ("usd_etched", "USD etched", "$"),
                    ("eur", "EUR", "€"),
                    ("eur_foil", "EUR foil", "€"),
                    ("tix", "MTGO tix", ""),
                )
            ],
        ),
        _group(
            "Ranked",
            [
                _fact("EDHREC", _number(data.get("edhrec_rank"))),
                _fact("Penny", _number(data.get("penny_rank"))),
            ],
        ),
    )


def _mtg_links(data: dict[str, Any]) -> list[Link]:
    """Scryfall's own page plus every link it hands out (Gatherer is official).

    The two ``tcgplayer_infinite_*`` entries are article/deck searches, not this
    card — everything else the API returns is passed through as it comes.
    """
    links: list[Link] = []
    if data.get("scryfall_uri"):
        links.append(Link("Scryfall", str(data["scryfall_uri"])))
    for group, labels in (
        ("related_uris", {"gatherer": "Gatherer (official)", "edhrec": "EDHREC"}),
        ("purchase_uris", {"tcgplayer": "TCGplayer", "cardmarket": "Cardmarket"}),
    ):
        for key, url in _obj(data.get(group)).items():
            if not url or key.startswith("tcgplayer_infinite"):
                continue
            links.append(Link(labels.get(key, key.replace("_", " ").title()), str(url)))
    return links


def _stats(data: dict[str, Any]) -> str:
    """Power/toughness, loyalty or defence — whichever this card has."""
    if data.get("power") is not None or data.get("toughness") is not None:
        return f"{data.get('power', '?')}/{data.get('toughness', '?')}"
    for key, label in (("loyalty", "loyalty"), ("defense", "defence")):
        if data.get(key) is not None:
            return f"{data[key]} {label}"
    return ""


def _flags(data: dict[str, Any]) -> list[str]:
    """The boolean print traits that are true — full art, promo, reprint, …"""
    names = (
        "full_art",
        "textless",
        "oversized",
        "promo",
        "reprint",
        "variation",
        "digital",
        "reserved",
        "story_spotlight",
    )
    return [n.replace("_", " ") for n in names if data.get(n) is True]


#: a card is printed on two sides at most, so that is all proxdex reads. Split
#: and adventure cards *list* two faces but print one image, and Scryfall says
#: so by keeping ``image_uris`` on the card instead of on the faces.
_MAX_FACES = 2


def _mtg_image(uris: dict[str, Any]) -> str:
    """Scryfall's ``png`` (745×1040) — its largest, and the only lossless one."""
    return str(uris.get("png") or uris.get("large") or uris.get("normal") or "")


#: Which of Scryfall's published sizes a *tile* takes, most wanted first.
#:
#: Not a setting, unlike the Pokémon equivalent, and the difference follows the two
#: providers rather than taste: a Pokémon image URL is derivable from the card id, so it
#: is a configurable template; Scryfall publishes its sizes as keys in the card's own
#: response, so the only choice is which key to read.
#:
#: Measured over 32 cards across four sets: ``png`` — right to *file*, being Scryfall's
#: only lossless size — has a median of **1657 KB** and a range of 331 KB to 2206 KB,
#: because an old card's scan is far heavier than a modern one's (`dft` runs ~377 KB,
#: `lea` ~2182). ``normal`` is 488×680 and **120 KB, range 78-146**. So the saving is
#: about 14x at the median and 3x on the lightest set — but the better property is
#: that the thumbnail is *flat*: a page of Alpha now costs what a page of Aetherdrift
#: costs, where before it was 102 MB against 23.
#:
#: ``normal`` rather than ``small`` (146×204, ~12 KB) because a 146px picture is softer
#: than the ~190px tile it fills; 488px is sharper than Pokémon's 245px ``/small`` and
#: still nothing next to the PNG.
_MTG_THUMB_KEYS: tuple[str, ...] = ("normal", "small", "large")


def _mtg_thumb(uris: dict[str, Any]) -> str:
    """The tile-sized picture, or ``""`` — in which case `SearchResult.thumb` falls
    back to the full image, so a response missing every size still draws."""
    return next((str(uris[k]) for k in _MTG_THUMB_KEYS if uris.get(k)), "")


def _mtg_face_uris(data: dict[str, Any]) -> dict[str, Any]:
    """Where the *front's* sizes live — on the card, or on its first face.

    The same distinction :func:`_mtg_faces` turns on, and a tile only ever shows the
    front, so this answers for one side rather than all of them.
    """
    shared = _obj(data.get("image_uris"))
    if shared:
        return shared
    faces = _objs(data.get("card_faces"))
    return _obj(faces[0].get("image_uris")) if faces else {}


def _mtg_faces(data: dict[str, Any]) -> tuple[FaceImage, ...]:
    """Every printable side of a Scryfall card, front first.

    A transform / modal double-faced card carries its images *on* the faces and
    has none at the top level — that is exactly what makes it two-sided. A split
    or adventure card lists faces too but keeps one shared image, so the
    top-level ``image_uris`` is checked first and such a card stays single-faced.
    """
    shared = _obj(data.get("image_uris"))
    if shared:
        return one_face(_mtg_image(shared))
    faces: list[FaceImage] = []
    for i, face in enumerate(_objs(data.get("card_faces"))[:_MAX_FACES]):
        url = _mtg_image(_obj(face.get("image_uris")))
        if url:
            faces.append(FaceImage(str(face.get("name") or f"Face {i + 1}"), url))
    return tuple(faces) or one_face("")


#: Scryfall's ``layout`` names that mean "half of a meld pair, or the melded card"
_MELD = "meld"
#: layouts that carry no printed frame whatever their set's era says. A **plane** is
#: here because it could not be read: its art runs to the edges and what border there is
#: comes out uneven, so there is no number to take — and it is 89×127mm, which means the
#: frame generation it reports would otherwise apply a 63.5mm card's *fraction* to it
#: and ask for 1.2mm more border than any of these cards have. `--frame` overrides
#: either way.
_FRAMELESS_LAYOUTS = frozenset({"art_series"})

#: layouts printed at 89×127mm whose border **was** readable, and is its own fraction of
#: that larger card rather than of a 63.5×88.9 one. Read from the printing rather than
#: left to a rule, for the same reason the yellow band is: the layout settles the
#: geometry.
_OVERSIZED_FRAMES: dict[str, str] = {
    # a plane shares the scheme's numbers: same product line, same 89×127mm stock, same
    # era, and a scheme measured 2.98/3.00mm — the *same physical border* an ordinary
    # 2003-frame card carries. A plane could not be read directly (its art runs to the
    # edges and what border there is comes out uneven), so it takes the number measured
    # off the same stock rather than being called borderless, which would have thrown a
    # real border fit away and looked perfect doing it.
    "planar": GuideId.MTG_OVERSIZED.value,
    "scheme": GuideId.MTG_OVERSIZED.value,
    "vanguard": GuideId.MTG_VANGUARD.value,
}


def _mtg_layout(data: dict[str, Any]) -> Layout:
    """What this printing puts on paper.

    Scryfall names two dozen layouts, but only two of them change the ink:
    a two-sided card (which :func:`_mtg_faces` already recognises by where the
    images hang) and meld. Everything else — saga, adventure, prototype,
    leveler, battle — is one picture on one side.
    """
    if str(data.get("layout") or "").strip().lower() == _MELD:
        return Layout.MELD_RESULT if _is_meld_result(data) else Layout.MELD_PART
    return Layout.DOUBLE if len(_mtg_faces(data)) > 1 else Layout.SINGLE


def _is_meld_result(data: dict[str, Any]) -> bool:
    """Is *this* card the melded one, rather than one of the two halves?

    All three cards of a meld share the ``meld`` layout and list each other in
    ``all_parts``; the only thing distinguishing them is which entry the card's
    own name matches.
    """
    name = str(data.get("name") or "")
    return any(
        str(part.get("component") or "") == "meld_result"
        and str(part.get("name") or "") == name
        for part in _objs(data.get("all_parts"))
    )


def mtg_frame(data: dict[str, Any]) -> str | None:
    """The frame spec this printing needs, when Scryfall says outright.

    Public, unlike its neighbours, because it is a *reading* rather than a fetch:
    what one card object means for the border. `tests/test_frames.py` holds it to
    that reading without a network round trip.

    A modern set mixes bordered and borderless prints under one set code, so the
    set id cannot answer this — but the card object can: ``border_color`` is
    ``"borderless"``, and there is then no printed frame to fit the border
    against, only the card aspect. A **yellow** ``border_color`` is the other: it is
    not an ink colour but Aetherdrift's box-topper band, a geometry of its own.
    Anything else returns ``None`` and the card's rules decide.

    **``full_art`` is deliberately not consulted.** It reads as if it meant "no
    border" and it does not: a full-art card's *art* fills the frame area, and the
    black border is still there at its era's normal width. Measured off Scryfall's
    own scans, a ZNR full-art land carries 2.28-2.45mm and an Unhinged one (2003
    frame) 2.88-3.05mm — the same as their ordinary neighbours. Treating them as
    borderless reshaped them to pure aspect and printed the art into the cut line.
    """
    border = str(data.get("border_color") or "").strip().lower()
    layout = str(data.get("layout") or "").strip().lower()
    if border == "borderless" or layout in _FRAMELESS_LAYOUTS:
        return GuideId.BORDERLESS.value
    # An **oversized** printing is 89×127mm, so its border is a different *fraction* of
    # the card even where the millimetres match — a scheme measures 2.98/3.00mm, the
    # same as an ordinary 2003-frame card, at 2.35%/3.37% against that card's
    # 3.37%/4.70%. Read off the layout here rather than left to the generation, which
    # would ask for 1.2mm too much on every edge and look perfect doing it.
    if layout in _OVERSIZED_FRAMES:
        return _OVERSIZED_FRAMES[layout]
    # A **yellow** border is not a colour, it is Aetherdrift's box-topper band: a wide
    # flat frame 1.7mm wider on the sides than the generation it sits in. All 79 such
    # printings carry it, which makes it the one case where `border_color` settles the
    # geometry rather than just the ink, so it is read here rather than left to a rule.
    if border == "yellow":
        return GuideId.MTG_YELLOW_BAND.value
    # **Extended art needs nothing**, and that is a correction: the survey reported its
    # sides at 0 ("the art runs off the card") and that was the old auto-detector
    # failing on dark art, not the card. Measured over 240 rows of `cmr-700`, the
    # black border is
    # 27-28px on both sides against a plain card's 29-30 — the same border, so the same
    # spec. Its own reading is in `docs/measuring-frames.md`.
    return None


def mtg_traits(data: dict[str, Any]) -> dict[str, str]:
    """The facts a frame rule can match an MTG printing on.

    Public for the same reason as :func:`mtg_frame`: it is a reading of one card
    object, and the frame-spec tests pin it.

    ``frame`` is Scryfall's own frame generation (``1993``, ``1997``, ``2003``,
    ``2015``, ``future``) and is the most useful of these by a distance: it is the
    thing that actually changes the border width, and one set code can carry more
    than one of them (a retro-frame bonus sheet inside a modern set).
    """
    return {
        "rarity": str(data.get("rarity") or ""),
        "subtypes": _mtg_subtypes(data),
        "finishes": ",".join(_strs(data.get("finishes"))),
        "full_art": "1" if data.get("full_art") is True else "0",
        "frame": str(data.get("frame") or ""),
        "border": str(data.get("border_color") or ""),
        # the treatments layered on the frame. Recorded because two of the ~26 do
        # change the geometry — `extendedart` runs the art to the left and right card
        # edges, `fullart` fills the frame area — while the rest (a legendary crown,
        # an inverted text box, the Nyx enchantment treatment, an etched foil) leave
        # the border exactly where its generation puts it. Measured, not assumed:
        # `scripts/mtg-variants.py`.
        "effects": ",".join(_strs(data.get("frame_effects"))),
    }


def _mtg_set_cards(set_id: str, cfg: Config) -> list[CardBrief]:
    out: list[CardBrief] = []
    url = f"{cfg.mtg_api_url.rstrip('/')}/cards/search"
    params: dict[str, Any] | None = {
        "q": f"set:{set_id}",
        "unique": "prints",
        "order": "set",
    }
    for _ in range(_MAX_PAGES * 2):  # Scryfall pages 175 at a time
        resp = _get(url, params=params)
        if resp.status == 404:  # Scryfall's "no cards matched"
            return out
        if not resp.ok:
            raise FileError(f"Scryfall returned {resp.status} listing {set_id}")
        body = resp.json()
        out += [
            CardBrief(
                id=f"{row.get('set')}-{row.get('collector_number')}",
                name=str(row.get("name") or ""),
                number=str(row.get("collector_number") or ""),
                rarity=str(row.get("rarity") or "").title(),
                traits=mtg_traits(row),
            )
            for row in _objs(body.get("data"))
            if row.get("set") and row.get("collector_number")
        ]
        # Scryfall hands back the whole next URL, query included, so it is
        # followed as-is rather than rebuilt from a page number
        if not body.get("has_more") or not body.get("next_page"):
            break
        url, params = str(body["next_page"]), None
    return out


def _mtg_subtypes(data: dict[str, Any]) -> str:
    """The subtypes out of a type line — everything after the em dash.

    Scryfall spells them only inside ``type_line`` ("Creature — Human Wizard"),
    and it is a real dash rather than a hyphen, so both are accepted.
    """
    line = str(data.get("type_line") or "")
    for dash in ("—", "-"):
        if dash in line:
            tail = line.split(dash, 1)[1]
            return ",".join(part for part in tail.split() if part)
    return ""


#: how many related cards to resolve — each costs one (cached) API call, and no
#: real card is printed alongside more than a handful
_MAX_RELATED = 8

_RELATIONS: dict[str, Relation] = {
    "meld_part": Relation.MELD_PART,
    "meld_result": Relation.MELD_RESULT,
    "token": Relation.TOKEN,
    "combo_piece": Relation.COMBO,
}


def _mtg_related(data: dict[str, Any]) -> tuple[Related, ...]:
    """The other cards printed alongside this one, as proxdex card ids.

    Scryfall's ``all_parts`` references each part by its own uuid, which is not
    a proxdex id, so each is resolved to ``<set>-<number>`` — one extra cached
    request per part. A part that cannot be resolved keeps its name and loses
    only its link, because "there is a second meld half" is worth saying even
    when the API is having a bad minute.
    """
    own_id = f"{data.get('set')}-{data.get('collector_number')}"
    own_name = str(data.get("name") or "")
    out: list[Related] = []
    for part in _objs(data.get("all_parts"))[:_MAX_RELATED]:
        name = str(part.get("name") or "")
        if not name or name == own_name:
            continue
        cid = _mtg_ref_id(str(part.get("uri") or ""))
        if cid == own_id:
            continue
        relation = _RELATIONS.get(str(part.get("component") or ""), Relation.OTHER)
        out.append(Related(relation, name, cid))
    return tuple(out)


def _mtg_ref_id(uri: str) -> str:
    """A Scryfall card URI → the proxdex id for that printing, or ``""``.

    Display-only, so every failure is empty rather than fatal — a related card
    whose id we could not read still shows up by name.
    """
    if not uri:
        return ""
    try:
        resp = _get(uri)
    except FileError:
        return ""
    if not resp.ok:
        return ""
    part = _obj(resp.json())
    set_id, number = part.get("set"), part.get("collector_number")
    return f"{set_id}-{number}" if set_id and number else ""


# -------------------------------------------------------------- fact plumbing --
# Providers answer with untyped JSON, so every reader below is total: a missing,
# null or wrong-shaped field becomes an empty string, and an empty fact (or a
# group of nothing but empty facts) is dropped instead of rendering a blank row.
def _fact(label: str, value: Any, *, block: bool = False, sep: str = " · ") -> Fact:
    if value is None or value is False:
        text = ""
    elif isinstance(value, (list, tuple)):
        text = sep.join(str(v).strip() for v in value if v not in (None, "", False))
    else:
        text = str(value).strip()
    return Fact(label, text, block=block)


def _group(title: str, facts: Iterable[Fact]) -> FactGroup | None:
    kept = tuple(f for f in facts if f.value)
    return FactGroup(title, kept) if kept else None


def _groups(*maybe: FactGroup | None) -> list[FactGroup]:
    return [g for g in maybe if g is not None]


def _legal(value: Any) -> list[Fact]:
    """A ``{format: status}`` object as one row per *status*, not per format.

    Scryfall answers with twenty formats and Pokémon with three; grouping by
    status keeps a long list from burying the rest of the card, and states the
    same thing either game's API said.
    """
    by_status: dict[str, list[str]] = {}
    for fmt, status in _obj(value).items():
        key = str(status).strip().lower()
        if key in {"", "not_legal"}:
            continue
        by_status.setdefault(key, []).append(str(fmt).replace("_", " ").title())
    return [
        _fact(status.replace("_", " ").title() + " in", fmts, block=True)
        for status, fmts in by_status.items()
    ]


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _objs(value: Any) -> list[dict[str, Any]]:
    return [_obj(v) for v in value] if isinstance(value, list) else []


def _int(value: Any) -> int:
    """A count from an untyped field — 0 for anything that is not one."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _strs(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)] if value not in (None, "") else []


def _join(parts: Iterable[Any], sep: str) -> str:
    return sep.join(str(p).strip() for p in parts if p not in (None, "", False))


def _number(value: Any) -> str:
    """A JSON number without a trailing ``.0`` (Scryfall's cmc is a float)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value)) if float(value).is_integer() else str(value)
    return ""


def _money(value: Any, sign: str) -> str:
    return f"{sign}{value}" if value not in (None, "") else ""


def _prices(value: Any) -> dict[str, Any]:
    return _obj(_obj(value).get("prices"))


def _energy(cost: Any) -> str:
    """Pokémon energy cost, deduplicated: ``["Fire"]*4`` → ``Fire ×4``."""
    counts = Counter(_strs(cost))
    return " ".join(f"{kind} ×{n}" if n > 1 else kind for kind, n in counts.items())


def _damage(value: Any) -> str:
    text = str(value or "").strip()
    return f"{text} dmg" if text else ""


def _typed(value: Any) -> str:
    """Pokémon weaknesses/resistances: ``[{type, value}]`` → ``Water ×2``."""
    return " · ".join(_join([e.get("type"), e.get("value")], " ") for e in _objs(value))


# ------------------------------------------------------------------ helpers --
def _get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str = "",
    cache: bool = True,
    ttl: float = net.CACHE_TTL,
    what: str = "",
    attempts: int = net.MAX_ATTEMPTS,
) -> net.Reply:
    """GET via :mod:`proxdex.net` — rate-limited, retried, and (JSON) cached.

    Every failure the caller cannot act on becomes a :class:`FileError`, so the
    CLI and UI report a flaky API the same way they report a missing card.

    ``what`` is the thing being fetched — a card id, usually. Without it the message
    is the *host's* (``api.pokemontcg.io: HTTP 500 after 4 tries``), which reads as
    ``SKIPPED api.pokemontcg.io`` in a batch and leaves you unable to tell which card
    to try again.
    """
    try:
        return net.get(
            url,
            params=params,
            accept=accept,
            cache=cache,
            ttl=ttl,
            attempts=attempts,
        )
    except net.NetworkError as exc:
        raise FileError(f"{what}: {exc}" if what else str(exc)) from exc


def transparent(im: Image.Image) -> bool:
    """Whether this image carries transparency that has to be composited away.

    Public because a card can enter the library two ways: ``fetch`` downloads it
    and ``import`` copies a file in. Both have to end up with the same picture, so
    both ask this the same question.
    """
    return im.mode in {"RGBA", "LA", "P"} or "transparency" in im.info


def flatten(im: Image.Image) -> Image.Image:
    """RGB, compositing any transparency onto the card's own border colour.

    Both providers ship PNGs whose die-cut corners are transparent, and those
    corners have to become *something* before the border step measures the
    frame. A fixed colour is wrong for one game or the other — black swallows a
    Pokémon card's yellow corner, yellow would ring an MTG card — so the filler
    is read off the card itself: the median colour of its opaque outer ring. A
    future game, or a silver-bordered oddity, needs no configuration.
    """
    if not transparent(im):
        return im.convert("RGB")
    rgba = im.convert("RGBA")
    base = Image.new("RGB", rgba.size, _edge_color(rgba))
    base.paste(rgba, mask=rgba.getchannel("A"))
    return base


#: how far in from the edge to sample the frame, as a fraction of the short side
#: — inside the rounded corners, still well outside the art
_EDGE_INSET = 0.02


def _edge_color(rgba: Image.Image) -> tuple[int, int, int]:
    """Median colour of the card's opaque outer ring — its border colour."""
    arr = np.asarray(rgba, dtype=np.uint8)
    h, w = arr.shape[:2]
    inset = max(1, round(min(w, h) * _EDGE_INSET))
    ring = np.concatenate(
        [
            arr[inset, inset:-inset],  # top edge
            arr[-1 - inset, inset:-inset],  # bottom
            arr[inset:-inset, inset],  # left
            arr[inset:-inset, -1 - inset],  # right
        ]
    )
    opaque = ring[ring[:, 3] > 250][:, :3]
    if not len(opaque):  # a fully transparent ring says nothing; stay neutral
        return (0, 0, 0)
    median = np.median(opaque, axis=0)
    return (int(median[0]), int(median[1]), int(median[2]))


# --------------------------------------------------------------- dispatch ---
# **One table per question a provider answers, keyed by provider.**
#
# These replaced four ``if game is POKEMON: … else: <the Magic one>`` branches.
# That shape was total only while there were exactly two games, and it failed in
# the worst available direction: a third game's id fell into the ``else`` and was
# asked of Scryfall, which answers a 404 that reads precisely like a mistyped
# Magic card. So the branch is gone and :func:`provider` is the only way in — a
# game with no provider raises there, once, with a sentence naming ``import``.
#
# Declared at the bottom because the functions are defined above; a ``dict``
# rather than a ``match`` so that adding a provider is one row per question and a
# missing row is a ``KeyError`` here rather than a silent Magic lookup.
_LOOKUP: dict[games.ProviderId, Callable[[str, Config], CardMeta]] = {
    games.ProviderId.POKEMONTCG: _pokemon_lookup,
    games.ProviderId.SCRYFALL: _mtg_lookup,
}

_QUERY: dict[games.ProviderId, Callable[[Query], str]] = {
    games.ProviderId.POKEMONTCG: _pokemon_query,
    games.ProviderId.SCRYFALL: _mtg_query,
}

_ORDER: dict[games.ProviderId, dict[Sort, str]] = {
    games.ProviderId.POKEMONTCG: _POKEMON_ORDER,
    games.ProviderId.SCRYFALL: _MTG_ORDER,
}

_SEARCH_PAGE: dict[games.ProviderId, Callable[[Query, Config], Page[SearchResult]]] = {
    games.ProviderId.POKEMONTCG: _pokemon_page,
    games.ProviderId.SCRYFALL: _mtg_page,
}

_SET_CARDS: dict[games.ProviderId, Callable[[str, Config], list[CardBrief]]] = {
    games.ProviderId.POKEMONTCG: _pokemon_set_cards,
    games.ProviderId.SCRYFALL: _mtg_set_cards,
}

#: every table above, so the completeness the ``if/else`` used to claim by accident
#: can be *asserted*: each provider must answer each question, and a missing row is a
#: `KeyError` at the call rather than a silent Magic lookup. Public only for
#: :mod:`tests.test_games`, which is why it carries no other use.
TABLES: tuple[Mapping[games.ProviderId, object], ...] = (
    _LOOKUP,
    _QUERY,
    _ORDER,
    _SEARCH_PAGE,
    _SET_CARDS,
)
