"""Card metadata and image downloads, one provider per game.

Pokémon comes from the Pokémon TCG API (metadata) plus scrydex (images); MTG
comes from Scryfall (both). The rest of proxdex only sees :class:`CardMeta` /
:class:`SearchResult`, which carry their own ``image_url`` — so adding a game
means adding a provider here and a :class:`proxdex.games.Game`, nothing else.

Every request goes through :mod:`proxdex.net`, which rate-limits, retries and
caches, so a provider having a bad day (scrydex 500s often) is survivable here
rather than something each call site has to think about.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np
from PIL import Image

from proxdex import games, net
from proxdex.config import Config
from proxdex.errors import FileError
from proxdex.frames import GuideId
from proxdex.games import GameId, Layout


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
    game: GameId
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
    game: GameId
    faces: tuple[FaceImage, ...]
    layout: Layout = Layout.SINGLE
    oversized: bool = False
    frame: str | None = None
    traits: dict[str, str] = field(default_factory=dict)

    @property
    def image_url(self) -> str:
        return self.faces[0].image_url if self.faces else ""

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
def lookup(cid: str, cfg: Config, game: GameId = games.DEFAULT) -> CardMeta:
    """Resolve a card id to its name, set and image URL in ``game``."""
    if game is GameId.POKEMON:
        return _pokemon_lookup(cid, cfg)
    return _mtg_lookup(cid, cfg)


def lookup_any(cid: str, cfg: Config, game: GameId | None = None) -> CardMeta:
    """Resolve a card id without knowing its game: try ``game`` if given, else
    the library's default game first and the others after."""
    errors: list[str] = []
    for candidate in (game,) if game else games.order(cfg.library_game):
        try:
            return lookup(cid, cfg, candidate)
        except FileError as exc:
            errors.append(str(exc))
    raise FileError("; ".join(errors) or f"{cid}: not found")


def details(cid: str, cfg: Config, game: GameId | None = None) -> CardDetail:
    """Everything the provider knows about ``cid`` — facts, links and raw JSON.

    Like :func:`lookup_any`, an unknown game means trying the library's default
    first and the others after.
    """
    errors: list[str] = []
    for candidate in (game,) if game else games.order(cfg.library_game):
        try:
            return _details(cid, cfg, candidate)
        except FileError as exc:
            errors.append(str(exc))
    raise FileError("; ".join(errors) or f"{cid}: not found")


def search(
    query: str,
    cfg: Config,
    game: GameId = games.DEFAULT,
    *,
    set_filter: str | None = None,
    rarity: str | None = None,
    year: str | None = None,
    limit: int = 100,
) -> list[SearchResult]:
    """Search ``game`` by name; each query word must appear in the card name.

    ``set_filter`` is pushed into the query where the API supports it (by set
    id/code, or a set-name substring for Pokémon); ``rarity`` and ``year`` are
    matched on the results, identically for every game.
    """
    provider = _pokemon_search if game is GameId.POKEMON else _mtg_search
    found = provider(query, cfg, set_filter, limit)
    return [r for r in found if _keep(r, rarity, year, set_filter)]


def set_cards(
    set_id: str, cfg: Config, game: GameId = games.DEFAULT
) -> list[CardBrief]:
    """Every card in one set, with the traits a frame rule matches on.

    Deliberately per-set and never "every card of every set": one set is a page or
    two, and the only thing this is for is showing which cards a rule catches
    *before* it is saved.
    """
    provider = _pokemon_set_cards if game is GameId.POKEMON else _mtg_set_cards
    return provider(set_id, cfg)


def download(meta: CardMeta, face: int = 0) -> Image.Image:
    """Download one face of a card's image, normalized to RGB."""
    if face >= len(meta.faces):
        raise FileError(f"{meta.id}: no face {face + 1}")
    url = meta.faces[face].image_url
    if not url:
        raise FileError(f"{meta.id}: this API offers no image for face {face + 1}")
    resp = _get(url, accept="image/*", cache=False)
    if not resp.ok:
        raise FileError(f"{meta.id}: {resp.status} for {url}")
    return flatten(Image.open(io.BytesIO(resp.body)))


def _details(cid: str, cfg: Config, game: GameId) -> CardDetail:
    source = games.get(game).source
    if game is GameId.POKEMON:
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
    resp = _get(cfg.api_url.format(id=cid))
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
        game=GameId.POKEMON,
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
    game = games.get(GameId.POKEMON)
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


def _pokemon_search(
    query: str, cfg: Config, set_filter: str | None, limit: int
) -> list[SearchResult]:
    parts = [f"name:*{token}*" for token in query.split() if token]
    if set_filter:
        if re.fullmatch(r"[a-z]+\d*", set_filter, re.IGNORECASE):
            parts.append(f"set.id:{set_filter}")
        else:
            parts.append(f"set.name:*{set_filter}*")
    base = cfg.api_url.replace("/{id}", "")
    params = {
        "q": " ".join(parts) or "*",
        "orderBy": "set.releaseDate",
        "pageSize": min(limit, 250),
        "select": "id,name,number,rarity,artist,set",
    }
    resp = _get(base, params=params, ttl=net.SEARCH_TTL)
    if not resp.ok:
        raise FileError(f"TCG API search failed ({resp.status}) for {query!r}")
    out: list[SearchResult] = []
    for data in resp.json().get("data", []):
        card_set = data.get("set", {})
        out.append(
            SearchResult(
                id=data["id"],
                name=data.get("name", ""),
                set_id=card_set.get("id", ""),
                set_name=card_set.get("name", ""),
                series=card_set.get("series", ""),
                year=str(card_set.get("releaseDate", "")).split("/")[0],
                number=str(data.get("number", "")),
                printed_total=str(card_set.get("printedTotal", "")),
                rarity=data.get("rarity") or "—",
                artist=data.get("artist") or "—",
                game=GameId.POKEMON,
                faces=one_face(cfg.scrydex_url.format(id=data["id"])),
            )
        )
    return out


# ---------------------------------------------------------------------- mtg --
def _mtg_lookup(cid: str, cfg: Config) -> CardMeta:
    return _mtg_meta(_mtg_data(cid, cfg))


def _mtg_data(cid: str, cfg: Config) -> dict[str, Any]:
    # <set>-<collector number>; MTG set codes never contain '-', but collector
    # numbers do (Alchemy rebalances are "A-123"), so split on the first one.
    set_code, _, number = cid.partition("-")
    if not set_code or not number:
        raise FileError(f"{cid}: not an MTG card id (expected e.g. neo-136)")
    resp = _get(f"{cfg.mtg_api_url.rstrip('/')}/cards/{set_code}/{number}")
    if resp.status == 404:
        raise FileError(f"{cid}: not found on Scryfall")
    if not resp.ok:
        raise FileError(f"{cid}: Scryfall returned {resp.status}")
    return _obj(resp.json())


def _mtg_search(
    query: str, cfg: Config, set_filter: str | None, limit: int
) -> list[SearchResult]:
    parts = [f'name:"{token}"' for token in query.replace('"', "").split() if token]
    if set_filter and re.fullmatch(r"[a-z0-9]{2,6}", set_filter, re.IGNORECASE):
        parts.append(f"set:{set_filter}")
    params = {
        "q": " ".join(parts) or "*",
        "unique": "prints",
        "order": "released",
        "dir": "asc",
    }
    resp = _get(
        f"{cfg.mtg_api_url.rstrip('/')}/cards/search", params=params, ttl=net.SEARCH_TTL
    )
    if resp.status == 404:  # Scryfall's "no cards matched"
        return []
    if not resp.ok:
        raise FileError(f"Scryfall search failed ({resp.status}) for {query!r}")
    return [_mtg_result(data) for data in resp.json().get("data", [])[:limit]]


def _mtg_meta(data: dict[str, Any]) -> CardMeta:
    return CardMeta(
        id=f"{data['set']}-{data['collector_number']}",
        name=str(data.get("name", "")),
        set_id=str(data.get("set", "")),
        set_name=str(data.get("set_name", "")),
        game=GameId.MTG,
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
        game=GameId.MTG,
        faces=_mtg_faces(data),
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
#: layouts that carry no printed frame whatever their set's era says
_FRAMELESS_LAYOUTS = frozenset({"art_series"})


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
    against, only the card aspect. Anything else returns ``None`` and the card's
    rules decide.

    **``full_art`` is deliberately not consulted.** It reads as if it meant "no
    border" and it does not: a full-art card's *art* fills the frame area, and the
    black border is still there at its era's normal width. Measured off Scryfall's
    own scans, a ZNR full-art land carries 2.28-2.45mm and an Unhinged one (2003
    frame) 2.88-3.05mm — the same as their ordinary neighbours. Treating them as
    borderless reshaped them to pure aspect and printed the art into the cut line.
    """
    border = str(data.get("border_color") or "").strip().lower()
    if (
        border == "borderless"
        or str(data.get("layout") or "").strip().lower() in _FRAMELESS_LAYOUTS
    ):
        return GuideId.BORDERLESS.value
    # Two more the printing settles outright, both measured over every combination
    # of Scryfall's three frame fields (`scripts/mtg-variants.py`). Answered here
    # rather than by a shipped rule for the same reason `borderless` is: it is a fact
    # the provider stated about this printing, not a guess about a set — so it lands
    # at `Via.PRINTING`, above any rule and below a pin.
    if border == "yellow":
        # a decorative band, 4.70mm against an ordinary 2.45. Colour is otherwise
        # never geometry: white, gold and silver all measure at generation width.
        return GuideId.MTG_YELLOW_BAND.value
    effects = {e.strip().lower() for e in _strs(data.get("frame_effects"))}
    if "extendedart" in effects:
        # the art runs off the left and right card edges, so those borders do not
        # exist — the one treatment of ~26 that changes the geometry
        return GuideId.MTG_EXTENDED_ART.value
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
def _keep(
    result: SearchResult, rarity: str | None, year: str | None, set_filter: str | None
) -> bool:
    if rarity and rarity.lower() not in result.rarity.lower():
        return False
    if year and str(year) != result.year:
        return False
    # a set filter the API couldn't take (a name, not a code) is applied here
    return not (
        set_filter
        and set_filter.lower() not in result.set_id.lower()
        and set_filter.lower() not in result.set_name.lower()
    )


def _get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str = "",
    cache: bool = True,
    ttl: float = net.CACHE_TTL,
) -> net.Reply:
    """GET via :mod:`proxdex.net` — rate-limited, retried, and (JSON) cached.

    Every failure the caller cannot act on becomes a :class:`FileError`, so the
    CLI and UI report a flaky API the same way they report a missing card.
    """
    try:
        return net.get(url, params=params, accept=accept, cache=cache, ttl=ttl)
    except net.NetworkError as exc:
        raise FileError(str(exc)) from exc


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
