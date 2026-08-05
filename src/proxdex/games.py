"""The card games proxdex knows how to fetch, frame and print — and the ones you add.

A :class:`Game` is the small bundle of facts that differ per game: what its card
ids look like, which provider answers for them, whether a card back can be
downloaded, and — for a game you define yourself — which sets it has.

**A game id is an open set, and that is the whole of this module's shape.** It is
the same arrangement :mod:`proxdex.frames` already has for frame specs, for the
same reason: proxdex ships the ones it can speak to an API about, and a library
adds its own. So :class:`GameId` survives as the closed set of ids *code* names —
the two built-ins, which have providers, id shapes and shipped frame specs written
against them — while a game id in general is a plain validated string, and
``<root>/games/<id>.json`` is where a custom one lives. :class:`Registry` is the
per-library answer to "what games are there", exactly as
:class:`proxdex.specs.Registry` is for specs.

**A custom game has no provider, and that is a fact with teeth.** There is no API
to look a card up in, so ``fetch``, ``search`` and ``browse`` refuse for it and
``import`` is the whole intake path: you bring the pictures, proxdex files them,
borders them against specs you measured and imposes them. Everything downstream of
the original image never knew which API answered, so all of it works unchanged.

The dangerous version of this would have been a provider dispatch with an
``else``. Two games meant ``if pokemon: … else: scryfall``, and a third game
reaching that ``else`` would silently ask Scryfall about a card it has never heard
of and file whatever came back. So the provider is a **value on the game**
(:class:`ProviderId`, ``None`` for a custom one) and the dispatch is a total
mapping — see :func:`proxdex.sources.provider`.

Which game a card belongs to is *filesystem state* like everything else: a
``.game`` file in the card folder (see :mod:`proxdex.library`). Set ids alone can't
tell you — Pokémon and MTG both use short lowercase codes, and a custom game may
use anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from proxdex.errors import NoProviderError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from proxdex.config import Config


class GameId(StrEnum):
    """The games proxdex ships — the ids *code* names.

    Closed on purpose, and not the type of a game id in general: these two have a
    provider, an id shape and shipped frame specs written against them, so code
    refers to them by name. A library's own games are plain ids validated by
    :func:`valid_id`.
    """

    POKEMON = "pokemon"
    MTG = "mtg"


class ProviderId(StrEnum):
    """Which set of provider calls answers for a game.

    A *value* rather than a branch, because the branch had an ``else`` in it: with
    two games ``if pokemon … else scryfall`` was total, and the moment a third game
    exists that ``else`` files a Magic answer for a card of a game Scryfall has
    never heard of. ``None`` — no provider — is a real and expected state now, so
    every dispatch has to say what it does about it.
    """

    #: pokemontcg.io for metadata, scrydex for the scans
    POKEMONTCG = "pokemontcg"
    #: Scryfall for both
    SCRYFALL = "scryfall"


#: used when nothing on disk or in config says otherwise
DEFAULT = GameId.POKEMON

#: the built-in ids, which a custom game may neither shadow nor replace. Same
#: argument as :data:`proxdex.frames.RESERVED`: code names these, so there has to
#: be a game by each name whatever a library has done to the rest.
RESERVED: frozenset[str] = frozenset(g.value for g in GameId)

#: where a library's own games live, beside ``frames/`` and ``profiles/``
DIRNAME = "games"

#: what a game id may look like. It is a filename, a CLI value, a URL segment and
#: the contents of a ``.game`` marker, so keep it to the shape every one of those
#: carries without quoting — the same rule a frame spec id follows.
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ID_MAX = 32
_NAME_MAX = 60
#: a set id of a custom game. Looser than a game id — it is half of a card id, and
#: card ids are conventionally lowercase alphanumeric — but still a path segment.
_SET_ID = re.compile(r"^[a-z0-9]+(?:[-.][a-z0-9]+)*$")
_SET_ID_MAX = 24


def valid_id(value: str) -> bool:
    """Whether ``value`` is a usable game id (shape only — see :meth:`Registry.has`)."""
    return bool(value) and len(value) <= _ID_MAX and bool(_ID.match(value))


def valid_set_id(value: str) -> bool:
    return bool(value) and len(value) <= _SET_ID_MAX and bool(_SET_ID.match(value))


def valid_name(value: str) -> bool:
    return bool(value.strip()) and len(value.strip()) <= _NAME_MAX


class Layout(StrEnum):
    """How a card is physically printed.

    Providers name two dozen layouts (``saga``, ``adventure``, ``prototype``,
    ``leveler``, …) but almost all of them are one ordinary picture on one side
    of one card — a difference in *rules text*, not in ink. proxdex only needs
    the cases that change what goes on paper, so the provider's own word travels
    on as a readable fact and this enum carries the print consequence.
    """

    #: one printed side (the overwhelming majority, and every Pokémon card)
    SINGLE = "single"
    #: two printed sides, each its own picture — transform, modal DFC,
    #: reversible and art-series cards, and double-faced tokens
    DOUBLE = "double"
    #: half of a meld pair: prints as a normal card, and its real reverse is
    #: half of the melded card, which proxdex files as its own card instead
    MELD_PART = "meld-part"
    #: the melded card — a third card with its own id and its own image
    MELD_RESULT = "meld-result"

    @property
    def label(self) -> str:
        return _LAYOUT_LABELS[self]

    @property
    def note(self) -> str:
        """One line on what this means for printing, for the CLI and the UI."""
        return _LAYOUT_NOTES[self]

    @property
    def sides(self) -> int:
        """How many printed sides a card of this layout has."""
        return 2 if self is Layout.DOUBLE else 1


_LAYOUT_LABELS: dict[Layout, str] = {
    Layout.SINGLE: "Single-sided",
    Layout.DOUBLE: "Double-faced",
    Layout.MELD_PART: "Meld part",
    Layout.MELD_RESULT: "Meld result",
}

_LAYOUT_NOTES: dict[Layout, str] = {
    Layout.SINGLE: "One printed side; the reverse is the shared card back.",
    Layout.DOUBLE: "Two printed sides, each with its own pipeline. Print duplex, "
    "or flip which side goes on the paper.",
    Layout.MELD_PART: "Melds with its partner into a third card. All three print "
    "as ordinary cards — fetch the related ones too.",
    Layout.MELD_RESULT: "The melded card, printed as an ordinary card of its own.",
}

#: **The card both built-in games print on, and the one place it is written down.**
#:
#: 2.5 × 3.5 inches — the "poker size" playing-card standard — which is 63.5 × 88.9mm.
#: Wizards states it for Magic, The Pokémon Company states the same 2.5 × 3.5in for
#: Pokémon, and sleeve makers design to it. **The two games are identical**, so this is
#: one constant and not one per game.
#:
#: Deliberately the **published spec and not a measured card.** Calipers on real cards
#: read a little under (one reported 63 × 87.9), and 63 × 88 is widely quoted as a
#: rounded metric figure — but a caliper reading is one card off one print run, inside a
#: ±0.5mm cutting tolerance, and picking it would pin proxdex to somebody's off-cut. The
#: published number is stable, citable and the same for both games.
#:
#: It is also **one** number on purpose. It was briefly two — this trim, and a separate
#: "real card" in `frames` that a spec's millimetres were fractions of — and that made
#: `frames show` report a width 0.8% off the one being printed. With one, a caliper
#: reading of a 3.45mm border becomes a printed border of 3.45mm.
#:
#: A **custom** game prints at this size too, because the trim is a property of the
#: library (``[card] card_w_mm``) rather than of the game: one sheet of paper carries
#: one grid, and a library that mixed two card sizes would be imposing two runs. A
#: game whose cards are a different size is therefore a different *library*, with its
#: own trim in its own ``proxdex.toml`` — which also keeps a spec's millimetres
#: meaningful, since they are fractions of the card being printed.
CARD_W_MM = 63.5
CARD_H_MM = 88.9

#: planar, scheme and Vanguard cards are printed at this size, not 63×88 — so a
#: sheet imposed at the standard trim would print them too small. proxdex says
#: so rather than silently shrinking them.
OVERSIZED_W_MM = 88.9
OVERSIZED_H_MM = 127.0


def parse_layout(value: str | None) -> Layout | None:
    """A layout from untrusted text (a marker file, a request body)."""
    try:
        return Layout(str(value).strip().lower())
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class SetSpec:
    """One set of a custom game, as its owner declared it.

    A custom game's sets are **declared rather than discovered**, because there is
    no provider to ask and the alternative is worse: with sets derived from
    whatever folders exist, a mistyped set id in an ``import`` silently becomes a
    new set, and nothing can list a set before its first card is filed. Declared,
    the id is checkable (:meth:`Registry.set_of`), the folder gets a real name, and
    the frame coverage report has rows to put a spec against.

    ``total`` and ``released`` are optional and only ever *reported* — a count
    nobody typed stays 0 and reads as unknown, never as zero cards.
    """

    id: str
    name: str
    #: how many cards the set holds; 0 = not stated
    total: int = 0
    #: ISO date, or "" — free text is not parsed into a fake date
    released: str = ""

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "total": self.total,
            "released": self.released,
        }


def read_set(entry: Any) -> SetSpec | None:
    """One :class:`SetSpec` from untrusted JSON, or ``None`` if it is not one.

    Total like every other reader over stored JSON: a malformed entry is dropped
    rather than raising into whatever screen asked for the game.
    """
    if not isinstance(entry, dict):
        return None
    sid = str(entry.get("id", "")).strip().lower()
    name = str(entry.get("name", "")).strip()
    if not valid_set_id(sid) or not valid_name(name):
        return None
    raw = entry.get("total", 0)
    total = raw if isinstance(raw, int) and raw >= 0 else 0
    return SetSpec(
        id=sid,
        name=name[:_NAME_MAX],
        total=total,
        released=str(entry.get("released", "")).strip()[:32],
    )


@dataclass(frozen=True, slots=True)
class Game:
    """One card game: how its ids look, who answers for it, what sets it has."""

    id: str
    name: str
    #: shape of a canonical card id, for help text and error messages
    id_example: str
    #: one line on where the metadata comes from
    source: str
    #: which provider answers for this game. **None = a custom game**: nothing to
    #: look a card up in, so ``import`` is the only way in and every provider-backed
    #: verb refuses rather than guessing at an API.
    provider: ProviderId | None = None
    #: where a card back can be downloaded from; None = no reliable source, and
    #: ``proxdex back --file`` is how one gets set (which is every custom game)
    back_url: str | None = None
    #: a public page for one card, as ``{set}``/``{number}`` — used when the
    #: provider's own response carries no link to it. None = the API links out
    #: itself (Scryfall does), or no stable per-card URL exists.
    card_page: str | None = None
    #: what ``card_page`` is, for the link's label
    card_page_name: str = ""
    #: this game's sets. Empty for the built-ins, whose sets come from their
    #: provider's own set list (see :func:`proxdex.browse.expansions`).
    sets: tuple[SetSpec, ...] = ()
    #: free text from whoever defined it — what these cards are, where the scans
    #: came from. Never parsed; a custom game is a thing you own, like a print
    #: profile, and the same argument applies for letting it carry notes.
    notes: str = ""

    @property
    def custom(self) -> bool:
        """Whether this game is one a library defined, rather than one proxdex ships."""
        return self.provider is None

    @property
    def example(self) -> str:
        """A card id of this game, for help text and error messages.

        Derived from the **first declared set** rather than left at whatever was
        typed, because a card id is ``<set>-<number>`` and a game-level example
        cannot be one: a fresh custom game read ``e.g. lorcana-1``, which is not an
        id anything would accept once its sets are `tfc`, `rfb`, … A stated
        ``id_example`` still wins, since a game whose numbering is stranger than
        ``<set>-<n>`` can say so.
        """
        if self.id_example:
            return self.id_example
        return f"{self.sets[0].id}-1" if self.sets else f"{self.id}-1"

    def set_of(self, set_id: str) -> SetSpec | None:
        want = set_id.strip().lower()
        return next((s for s in self.sets if s.id == want), None)

    def set_name(self, set_id: str) -> str:
        """A set's name, falling back to its id so a folder is never named "".

        Total on purpose: it is called to *name a folder*, and refusing there would
        block a card over a set nobody declared. The check that the set exists is
        ``import``'s, before anything is written.
        """
        found = self.set_of(set_id)
        return found.name if found is not None else set_id

    def json(self) -> dict[str, Any]:
        """Everything a screen or a CLI table needs. Not the on-disk form."""
        return {
            "id": self.id,
            "name": self.name,
            "id_example": self.example,
            "source": self.source,
            "provider": self.provider.value if self.provider else None,
            "custom": self.custom,
            "back_url": self.back_url,
            "sets": [s.json() for s in self.sets],
            "notes": self.notes,
        }

    def stored(self) -> dict[str, Any]:
        """The ``games/<id>.json`` form: what a person typed, and nothing derived."""
        return {
            "id": self.id,
            "name": self.name,
            "id_example": self.id_example,
            "notes": self.notes,
            "sets": [s.json() for s in self.sets],
        }


def custom(
    game_id: str,
    name: str,
    *,
    id_example: str = "",
    notes: str = "",
    sets: tuple[SetSpec, ...] = (),
) -> Game:
    """A game a library defined: no provider, and a source line that says so."""
    return Game(
        id=game_id,
        name=name,
        # deliberately left empty when unstated: `Game.example` derives one from the
        # first declared set, which is the only place a real card id can come from
        id_example=id_example,
        source="your own files (no provider — import the images)",
        provider=None,
        back_url=None,
        sets=sets,
        notes=notes,
    )


POKEMON = Game(
    id=GameId.POKEMON.value,
    name="Pokémon",
    id_example="ex3-90",
    source="pokemontcg.io (metadata) + scrydex (images)",
    provider=ProviderId.POKEMONTCG,
    # the back is a single image owned by TPC — no API serves it
    back_url=None,
    # pokemon.com's own card database has no stable per-card URL (it addresses
    # cards through a search form), so the card page links to Limitless, which
    # keys off the same <set>/<number> pokemontcg.io ids do.
    card_page="https://limitlesstcg.com/cards/{set}/{number}",
    card_page_name="Limitless TCG",
)

MTG = Game(
    id=GameId.MTG.value,
    name="Magic: The Gathering",
    id_example="neo-136",
    source="Scryfall (metadata + images)",
    provider=ProviderId.SCRYFALL,
    back_url="https://cards.scryfall.io/back.png",
)

#: the games proxdex ships, in the order they are offered
BUILTIN: tuple[Game, ...] = (POKEMON, MTG)

GAMES: dict[str, Game] = {g.id: g for g in BUILTIN}


def parse(value: str | None) -> str | None:
    """A game id from untrusted text (TOML, a marker file, a query param).

    Shape only — whether a game by that id *exists* is
    :meth:`Registry.has`, because that depends on the library. Deliberately not
    folded together: a ``.game`` marker naming a game whose definition was deleted
    describes a real card that really is not Pokémon, and coercing it to the
    default would quietly file it under the wrong game's frame specs.
    """
    text = str(value or "").strip().lower()
    return text if valid_id(text) else None


def coerce(value: str | None, fallback: str = DEFAULT) -> str:
    """Like :func:`parse`, but never fails — unmeanable text means ``fallback``."""
    return parse(value) or fallback


def builtin(game_id: str) -> Game | None:
    """One of the two shipped games, by id. ``None`` for anything else.

    What :mod:`proxdex.sources` uses, so provider code needs no library root: a
    custom id can never be one of these (see :data:`RESERVED`), so "not built-in"
    and "no provider" are the same answer.
    """
    return GAMES.get(game_id)


def provider_of(game_id: str) -> ProviderId | None:
    found = builtin(game_id)
    return found.provider if found is not None else None


def require_provider(game_id: str) -> ProviderId:
    """Which provider answers for ``game_id``, or refuse to guess.

    **The one place a game becomes an API.** It lives here rather than in
    :mod:`proxdex.sources` because :mod:`proxdex.browse` needs the same answer and
    cannot import sources, and two copies of this decision is exactly the split that
    would let one of them keep an ``else``.

    Which is the point: with two games every dispatch read ``if pokemon: … else:
    <the Magic one>``, total only because there were two. A third game's id reaching
    that ``else`` asks Scryfall about a card it has never heard of — and Scryfall's
    answer for an unknown id is a 404 that reads exactly like a mistyped Magic card,
    so the report would name the wrong problem entirely.

    Raising is not a limitation but the feature: a custom game's pictures come from
    ``import``, so every caller of this is asking a question that has no answer for
    it, and saying so once is better than each surface testing for it.
    """
    found = provider_of(game_id)
    if found is None:
        name = load().name_of(game_id)
        msg = (
            f"{name} has no card provider, so there is nothing to look up — "
            f"bring the images yourself with `proxdex import`"
        )
        raise NoProviderError(msg)
    return found


@dataclass(frozen=True, slots=True)
class Registry:
    """Every game this library has: the two shipped, plus its own.

    The per-library answer, like :class:`proxdex.specs.Registry` — and total for
    the same reasons. :meth:`get` answers ``None`` rather than raising, because it
    is asked in order to *draw a screen* or *name a game in a table*, long before
    anybody has checked that the id in a marker file still means something.
    """

    games: tuple[Game, ...]
    #: ``games/*.json`` files that would not parse, so they are not being used.
    #: Named rather than swallowed: a game silently absent takes its cards' frame
    #: specs with it, and the cards then refuse to border for no stated reason.
    unreadable: tuple[str, ...] = ()

    def get(self, game_id: str) -> Game | None:
        return next((g for g in self.games if g.id == game_id), None)

    def has(self, game_id: str) -> bool:
        return self.get(game_id) is not None

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(g.id for g in self.games)

    @property
    def custom(self) -> tuple[Game, ...]:
        return tuple(g for g in self.games if g.custom)

    @property
    def provided(self) -> tuple[Game, ...]:
        """The games a provider answers for — what ``search`` and ``browse`` mean."""
        return tuple(g for g in self.games if not g.custom)

    def name_of(self, game_id: str) -> str:
        """A game's display name, falling back to the id itself.

        Total because it is used in tables and error messages, where an id nothing
        answers to is exactly the thing worth showing: ``yugioh`` reads as a game
        somebody removed, while "Pokémon" would be a lie about the card.
        """
        found = self.get(game_id)
        return found.name if found is not None else game_id

    def order(self, preferred: str = DEFAULT) -> tuple[str, ...]:
        """Game ids to try, preferred first — used when an id's game is unknown."""
        return (preferred, *(g for g in self.ids if g != preferred))

    def provider_order(self, preferred: str = DEFAULT) -> tuple[str, ...]:
        """Like :meth:`order`, but only games something can be looked up in.

        What ``fetch <id>`` with no ``--game`` walks. A custom game has no API to
        try, so including it would be a request that cannot be made.
        """
        return tuple(
            g
            for g in self.order(preferred)
            if (found := self.get(g)) is not None and not found.custom
        )


def dir_for(root: Path) -> Path:
    return root / DIRNAME


def path(root: Path, game_id: str) -> Path:
    return dir_for(root) / f"{game_id}.json"


def load(root: Path | None = None) -> Registry:
    """The shipped games plus ``<root>/games/*.json``.

    ``root=None`` is the built-ins alone, for the callers that have no library in
    hand. A stored game may not take a built-in id (:data:`RESERVED`) and may not
    take another stored game's id; both are dropped as unreadable rather than
    quietly shadowing, because "my Pokémon cards stopped resolving" is a much
    worse report than "that file was ignored".
    """
    found: list[Game] = list(BUILTIN)
    bad: list[str] = []
    if root is None:
        return Registry(games=tuple(found))
    folder = dir_for(root)
    if not folder.is_dir():
        return Registry(games=tuple(found))
    seen = set(RESERVED)
    for file in sorted(folder.glob("*.json")):
        game = _read(file)
        if game is None or game.id in seen:
            bad.append(file.name)
            continue
        seen.add(game.id)
        found.append(game)
    return Registry(games=tuple(found), unreadable=tuple(bad))


def _read(file: Path) -> Game | None:
    """One stored game, or ``None`` if the file is not one.

    The id comes from the **filename** and the body may only agree with it. A body
    free to disagree gives two answers to "which game is this", and the filename is
    the one every other reader here trusts (the same rule ``profiles/`` follows).
    """
    game_id = file.stem.strip().lower()
    if not valid_id(game_id) or game_id in RESERVED:
        return None
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "")).strip()
    if not valid_name(name):
        return None
    entries = raw.get("sets")
    # one set per id: a duplicate is a typo, and the first one wins so the file's
    # own order decides rather than whichever came last
    unique: dict[str, SetSpec] = {}
    for entry in entries if isinstance(entries, list) else []:
        one = read_set(entry)
        if one is not None:
            unique.setdefault(one.id, one)
    return custom(
        game_id,
        name[:_NAME_MAX],
        id_example=str(raw.get("id_example", "")).strip()[: _ID_MAX + 16],
        notes=str(raw.get("notes", "")).strip(),
        sets=tuple(unique.values()),
    )


def store(root: Path, game: Game) -> Path:
    """Write a custom game to ``<root>/games/<id>.json`` and return the file."""
    if game.id in RESERVED:
        msg = f"{game.id!r} is a game proxdex ships, so it cannot be redefined"
        raise ValueError(msg)
    if not valid_id(game.id):
        msg = f"{game.id!r} is not a usable game id (lowercase letters, digits, -)"
        raise ValueError(msg)
    target = path(root, game.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(game.stored(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def remove(root: Path, game_id: str) -> bool:
    """Delete a custom game's definition. Returns whether there was one."""
    target = path(root, game_id)
    if not target.is_file():
        return False
    target.unlink()
    return True


def with_set(game: Game, entry: SetSpec) -> Game:
    """``game`` with ``entry`` added, or replacing the set of the same id."""
    kept = [s for s in game.sets if s.id != entry.id]
    kept.append(entry)
    return replace(game, sets=tuple(sorted(kept, key=lambda s: s.id)))


def without_set(game: Game, set_id: str) -> Game:
    return replace(game, sets=tuple(s for s in game.sets if s.id != set_id))


def dangling(root: Path, cfg: Config) -> str | None:
    """The configured default game, if nothing answers to it any more.

    The same broken reference :func:`proxdex.profiles.dangling` reports, for the
    same reason and answered the same way: ``[library] game`` is a *name* in a text
    file, and deleting the game it names leaves a config that reads fine and a
    library whose bare ``fetch`` refers to nothing. Reported by ``proxdex where``
    rather than raised, because it is asked in order to tell you.
    """
    want = str(cfg.library_game or "").strip()
    if not want:
        return None
    return None if load(root).has(want) else want
