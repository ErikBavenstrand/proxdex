"""Which spec every card of one set resolves to — the rule table, made checkable.

A rule that matches on a rarity, a subtype or a frame generation is *hopeful*
until you can see which cards it actually catches: the answer depends on data that
is not in the card id. So this reads one set's cards from the provider and runs the
real :func:`proxdex.specs.resolve` over each, which is what makes such a rule
trustworthy rather than plausible.

**One set at a time, and only when asked.** ``frames preview <set>`` and
``/api/frames/preview`` are the only callers. Walking every card of every set is
minutes of API traffic to answer a question nobody asked, and this module used to
do something close to it: a coverage report over the provider's whole set list,
grading each set against the specs. That is gone. It could not work in principle —
MTG's border follows the *printing's* frame generation, so a set-level row has no
printing to read and called 1046 sets unmeasured while every card in them resolves
exactly. The warnings that replaced it are :func:`proxdex.specs.audit`, over the
cards a library actually holds.

The provider reads are cached in :func:`proxdex.net.cache_dir`, never in the
library — the answer is rederivable from the API by definition, which is the same
argument that puts the JSON cache there, and ``proxdex where --clear-cache``
empties it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from proxdex import games, net, sources, specs
from proxdex.games import GameId
from proxdex.specs import Registry, Via

if TYPE_CHECKING:
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
        spec=found.spec.id,
        via=found.via,
        rule=found.rule,
        undecided=found.undecided,
    )


def clear() -> int:
    """Forget the cached provider reads. Same call ``where --clear-cache`` makes."""
    return net.clear_cache()
