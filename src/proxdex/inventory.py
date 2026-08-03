"""Which spec every card of one set resolves to — the rule table, made checkable.

A rule that matches on a rarity, a subtype or a frame generation is *hopeful*
until you can see which cards it actually catches: the answer depends on data that
is not in the card id. So this reads one set's cards from the provider and runs the
real :func:`proxdex.specs.resolve` over each, which is what makes such a rule
trustworthy rather than plausible.

**One set at a time, and only when asked.** ``frames preview <set>`` and
``/api/frames/preview`` are the only callers. Walking every card of every set is
minutes of API traffic to answer a question nobody asked.

:func:`coverage` is the other question — *what has nobody measured yet?* — and it is
deliberately not the report this module used to hold. That one graded **every set of
both games** against the specs, and it could not work in principle: MTG's border
follows the *printing's* frame generation, so a set-level row has no printing to read,
and it called 1046 sets unmeasured while every card in them resolved exactly. What is
here instead asks each game the question **its own border followed**
(:func:`proxdex.frames.keyed`) — a row per set for Pokémon, a row per frame generation
for MTG — so there is no set-level verdict for a game that has none to give. It reads
the provider's set list rather than the library (one cached request per game) because
the sets nobody has measured are mostly sets you do not own yet, which is the whole
point of asking. The other frame report is :func:`proxdex.specs.audit`, over the cards
a library actually holds: that one is broken references and unanswerable questions,
this one is gaps.

The provider reads are cached in :func:`proxdex.net.cache_dir`, never in the
library — the answer is rederivable from the API by definition, which is the same
argument that puts the JSON cache there, and ``proxdex where --clear-cache``
empties it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from proxdex import browse, frames, games, net, sources, specs
from proxdex.games import GameId
from proxdex.specs import Registry, Via

if TYPE_CHECKING:
    from collections.abc import Sequence

    from proxdex.config import Config
    from proxdex.sources import CardBrief


@dataclass(frozen=True, slots=True)
class Assignment:
    """One card of a set, and the spec this library's rules give it."""

    card: CardBrief
    spec: str
    via: Via
    rule: str | None
    undecided: tuple[str, ...] = ()

    def json(self) -> dict[str, Any]:
        return {
            "id": self.card.id,
            "name": self.card.name,
            "number": self.card.number,
            "rarity": self.card.rarity,
            "spec": self.spec,
            "via": self.via.value,
            "via_label": self.via.label,
            "rule": self.rule,
            "undecided": list(self.undecided),
        }


@dataclass(slots=True)
class Preview:
    """What every card of one set resolves to."""

    set_id: str
    game: GameId
    rows: list[Assignment]

    def tally(self) -> dict[str, int]:
        """Cards per spec, in the order they first appear."""
        out: dict[str, int] = {}
        for row in self.rows:
            out[row.spec] = out.get(row.spec, 0) + 1
        return out

    def json(self) -> dict[str, Any]:
        return {
            "set": self.set_id,
            "game": self.game.value,
            "cards": len(self.rows),
            "tally": self.tally(),
            "rows": [r.json() for r in self.rows],
        }


def preview(
    set_id: str,
    cfg: Config,
    reg: Registry,
    game: GameId = games.DEFAULT,
) -> Preview:
    """Which spec every card of one set gets, and which rule decided it.

    The traits come from the provider, so the answer is the one a fetched card
    will get — not an approximation of it.
    """
    rows = [
        _assign(brief, set_id, game, reg)
        for brief in sources.set_cards(set_id, cfg, game)
    ]
    return Preview(set_id=set_id, game=game, rows=rows)


def _assign(brief: CardBrief, set_id: str, game: GameId, reg: Registry) -> Assignment:
    found = specs.resolve(reg, brief.id, set_id, game, traits=brief.traits)
    return Assignment(
        card=brief,
        spec=found.spec.id if found.spec else "",
        via=found.via,
        rule=found.rule,
        undecided=found.undecided,
    )


def clear() -> int:
    """Forget the cached provider reads. Same call ``where --clear-cache`` makes."""
    return net.clear_cache()


# ---------------------------------------------------------------- coverage ----
@dataclass(frozen=True, slots=True)
class Answer:
    """One spec that answers for a row, and how it was reached."""

    spec: str
    via: Via
    rule: str | None = None

    def json(self) -> dict[str, Any]:
        return {
            "spec": self.spec,
            "via": self.via.value,
            "via_label": self.via.label,
            "rule": self.rule,
        }


@dataclass(frozen=True, slots=True)
class Row:
    """One thing a border followed, and every spec that answers for it.

    ``subject`` is a set id or a frame generation, per :func:`proxdex.frames.keyed`.
    More than one answer is a *state* and not a fault — the e-Card sets hold two
    measured frames and a person picks per card — so this counts as covered.
    """

    subject: str
    name: str
    key: frames.Key
    answers: tuple[Answer, ...] = ()
    #: ISO release date, for a set row; empty for a generation
    released: str = ""
    #: how many cards of it this library holds, so a gap can be read by urgency
    owned: int = 0

    @property
    def covered(self) -> bool:
        return bool(self.answers)

    @property
    def year(self) -> str:
        return self.released[:4]

    def json(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "name": self.name,
            "key": self.key.value,
            "covered": self.covered,
            "answers": [a.json() for a in self.answers],
            "released": self.released,
            "owned": self.owned,
        }


@dataclass(frozen=True, slots=True)
class Band:
    """A run of rows under one heading — an era, or a kind of key."""

    key: str
    label: str
    rows: tuple[Row, ...] = ()

    @property
    def covered(self) -> int:
        return sum(1 for r in self.rows if r.covered)

    @property
    def owned(self) -> int:
        return sum(r.owned for r in self.rows)

    def json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "rows": [r.json() for r in self.rows],
            "total": len(self.rows),
            "covered": self.covered,
            "owned": self.owned,
        }


@dataclass(frozen=True, slots=True)
class Coverage:
    """What has a measured frame spec for one game, and what has not."""

    game: GameId
    key: frames.Key
    bands: tuple[Band, ...] = ()
    #: for a generation-keyed game, how many of its sets this report deliberately
    #: gives **no** per-set verdict for — see :attr:`note`
    per_printing: int = 0

    @property
    def rows(self) -> tuple[Row, ...]:
        return tuple(r for b in self.bands for r in b.rows)

    @property
    def primary(self) -> tuple[Row, ...]:
        """The rows keyed the way this game's border was — what the headline counts.

        A generation-keyed game also carries *set* rows (MTG's three 1993 bands and
        `4bb`, exceptions to a generation rather than a scheme of their own), and
        counting those in with the generations gives a total that is two kinds of
        thing added together: "12 of 12 frame generations" for a game that has five.
        They are still rows, still listed and still counted as gaps if nothing
        answers for them — they are just not the unit the headline is in.
        """
        return tuple(r for r in self.rows if r.key is self.key)

    @property
    def total(self) -> int:
        return len(self.primary)

    @property
    def covered(self) -> int:
        return sum(1 for r in self.primary if r.covered)

    @property
    def gaps(self) -> tuple[Row, ...]:
        """The rows nothing measured answers for — what somebody has to go and read."""
        return tuple(r for r in self.rows if not r.covered)

    @property
    def complete(self) -> bool:
        return self.total > 0 and not self.gaps

    @property
    def owned_gaps(self) -> int:
        """Cards this library already holds that no spec answers for."""
        return sum(r.owned for r in self.gaps)

    @property
    def note(self) -> str:
        """Why this game is counted this way — the same sentence in the CLI and the UI.

        It is on the report rather than in either surface because the *reason* is the
        load-bearing part: a reader who does not know MTG resolves per printing will
        read "5 rows" as "5 sets covered" and conclude the opposite of the truth.
        """
        name = games.get(self.game).name
        if self.key is frames.Key.SET:
            return (
                f"{name}'s border ran for known runs of sets, so a set is the thing a "
                "spec covers and every set of the game is asked about."
            )
        return (
            f"{name}'s border followed the printing's frame generation, not "
            f"its set — a modern set holds retro-frame cards beside modern ones. So "
            f"the rows are generations, and the {self.per_printing} set(s) in the "
            "index resolve per card rather than per set. A generation nobody measured "
            "would appear here as a gap; one Scryfall has not documented yet resolves "
            "to no spec and says so."
        )

    def json(self) -> dict[str, Any]:
        return {
            "game": self.game.value,
            "key": self.key.value,
            "key_label": self.key.label,
            "bands": [b.json() for b in self.bands],
            "total": self.total,
            "covered": self.covered,
            "gaps": [r.json() for r in self.gaps],
            "complete": self.complete,
            "owned_gaps": self.owned_gaps,
            "per_printing": self.per_printing,
            "note": self.note,
        }


def coverage(
    game: GameId,
    cfg: Config,
    reg: Registry,
    owned: dict[str, int] | None = None,
) -> Coverage:
    """Which printings of ``game`` have a measured frame spec, and which do not.

    One provider request (the set list, cached a day — the same read Browse makes),
    and no card reads at all: whether a spec *exists* for a set or a generation is
    answered by the shipped baseline and this library's own rules, both of which are
    local. :func:`assess` is the pure half, so the ordering and the verdicts are
    testable without a provider.
    """
    return assess(game, browse.expansions(game, cfg), reg, owned)


def assess(
    game: GameId,
    found: Sequence[browse.Expansion],
    reg: Registry,
    owned: dict[str, int] | None = None,
) -> Coverage:
    """:func:`coverage` over a set list already in hand — pure."""
    key = frames.keyed(game)
    held = owned or {}
    if key is frames.Key.SET:
        bands = tuple(
            Band(
                key=group.key,
                label=group.label,
                rows=tuple(_set_row(exp, game, reg, held) for exp in group.expansions),
            )
            for group in browse.gather(game, found)
        )
        return Coverage(game=game, key=key, bands=bands)

    # A generation-keyed game: the rows are the generations, and the only sets worth
    # a row of their own are the ones the baseline really keys by set — MTG's three
    # 1993 bands and `4bb`, which are exceptions *to* a generation rather than a
    # scheme of their own. Every other set is deliberately unlisted: a per-set verdict
    # is the answer this game does not have, and inventing one is what the deleted
    # report did.
    exceptions = frames.set_keys(game)
    named = {exp.id: exp for exp in found}
    bands = (
        Band(
            key="generation",
            label="Frame generations",
            rows=tuple(
                _generation_row(generation, game, reg)
                for generation in frames.Generation
            ),
        ),
        Band(
            key="exception",
            label="Sets keyed away from their generation",
            rows=tuple(
                _set_row(named[set_id], game, reg, held)
                if set_id in named
                else Row(
                    subject=set_id,
                    name=set_id,
                    key=frames.Key.SET,
                    answers=_answers_for_set(set_id, game, reg),
                    owned=held.get(set_id, 0),
                )
                for set_id in sorted(exceptions)
            ),
        ),
    )
    return Coverage(
        game=game,
        key=key,
        bands=bands,
        per_printing=sum(1 for exp in found if exp.id not in exceptions),
    )


def _set_row(
    exp: browse.Expansion, game: GameId, reg: Registry, held: dict[str, int]
) -> Row:
    return Row(
        subject=exp.id,
        name=exp.name,
        key=frames.Key.SET,
        answers=_answers_for_set(exp.id, game, reg),
        released=exp.released,
        owned=held.get(exp.id, 0),
    )


def _answers_for_set(set_id: str, game: GameId, reg: Registry) -> tuple[Answer, ...]:
    """Every spec that answers for a whole set, in :func:`proxdex.specs.resolve`'s
    own order — a library's whole-set rule first, then the shipped baseline.

    Only a **whole-set** rule counts. A rule matching a rarity, a number range or a
    trait claims *some* cards of the set, so counting it as coverage would report a
    set as answered when the ordinary cards in it still resolve to nothing — a
    number that looks finished, which is the failure mode this whole area is
    careful about. ``frames preview`` is where such a rule is judged.
    """
    out: list[Answer] = []
    seen: set[str] = set()

    def offer(spec_id: str, via: Via, rule: str | None = None) -> None:
        if spec_id in seen or reg.get(spec_id) is None:
            return
        seen.add(spec_id)
        out.append(Answer(spec=spec_id, via=via, rule=rule))

    for rule in reg.for_set(game, set_id):
        if rule.is_default:
            offer(rule.spec, Via.SET_DEFAULT, rule.id)
    # no traits, so this is the set-id pass of `baselines` alone — which is the
    # question being asked: does anything answer for the *set*?
    for shipped in frames.baselines(set_id, game):
        offer(shipped, Via.ERA)
    return tuple(out)


def _generation_row(generation: frames.Generation, game: GameId, reg: Registry) -> Row:
    out: list[Answer] = []
    seen: set[str] = set()
    # a whole-set rule with no set covers every card of the game, whatever its frame
    for rule in reg.for_set(game, ""):
        if rule.is_default and rule.spec not in seen and reg.get(rule.spec) is not None:
            seen.add(rule.spec)
            out.append(Answer(spec=rule.spec, via=Via.SET_DEFAULT, rule=rule.id))
    for shipped in frames.generation_keys(game).get(generation, ()):
        if shipped not in seen and reg.get(shipped) is not None:
            seen.add(shipped)
            out.append(Answer(spec=shipped, via=Via.ERA))
    return Row(
        subject=generation.value,
        name=f"{generation.value} frame",
        key=frames.Key.GENERATION,
        answers=tuple(out),
    )
