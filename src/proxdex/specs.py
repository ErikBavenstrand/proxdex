"""Which frame spec a card is fitted to, and where that answer came from.

:mod:`proxdex.frames` holds the geometry and the specs proxdex ships. This module
holds everything a *library* decides:

**The registry** — the shipped specs, plus ``<root>/frames/<id>.json`` for the
ones this library measured. A stored file may correct a shipped one, and is
*expected* to: the shipped MTG numbers are working defaults waiting for calipers
(see :mod:`proxdex.frames`). ``borderless`` alone is reserved, because code
returns it and there has to be a spec by that name.

**The rules** — ``<root>/frames/rules.json``, an ordered list. One set can need
more than one spec: a modern set's secret-rare tail is a different frame from the
same set's ordinary cards. A rule is a *selector* → spec, and the selector is
either something a card id already says (a collector-number range, an explicit id
list) or something the printing says (its rarity, subtype, finish, full-art flag).
The first kind always works offline; the second is read from the card's own
``.traits`` marker, written at fetch alongside ``.layout`` — so nothing has to
call an API again to remember which spec a card needs.

**The resolution** — :func:`resolve`, which returns a :class:`Resolution` rather
than a bare spec: *which* spec, and **why** it was picked. That matters more here
than anywhere else in proxdex, because a wrong border is invisible until the card
is cut. The order, most specific first:

1. ``override`` — what the user typed for this run (``border --frame``)
2. ``pin`` — what the user chose *for this card*, and stored
3. ``printing`` — what the provider said about this printing (borderless)
4. the first matching **rule**, in file order
5. the set's default rule, if it has one
6. the shipped **baseline** — Pokémon's set-id eras, MTG's frame generations
7. the game's fallback spec

A predicate rule the card has no recorded traits for is *undecidable*, not false:
the resolution says so (:attr:`Resolution.undecided`) so the CLI and the UI can
name the card that needs re-fetching instead of quietly bordering it as ordinary.

**The audit** — :func:`audit`, the one list of frame warnings, shared by
``proxdex frames check`` and the UI. Every :class:`Fault` it reports is a broken
reference or a question nobody can answer from what is recorded; none of them is a
judgement about a spec's numbers. There used to be a coverage report that graded
every set that has ever printed, and it was worse than nothing: for MTG it called
1046 sets unmeasured while every card in them resolves exactly, because the border
follows the printing's frame and a set-level row has no printing to read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from proxdex import frames, games
from proxdex.errors import ProxdexError
from proxdex.frames import FrameGuide
from proxdex.games import GameId

#: where a library keeps its own specs and its rule list
DIR = "frames"
RULES = "rules.json"

#: trait keys a rule can match on. They are what the provider said about the
#: printing, recorded per card at fetch time — see :meth:`Card.write_traits`.
TRAIT_RARITY = "rarity"
TRAIT_SUBTYPES = "subtypes"
TRAIT_FINISHES = "finishes"
TRAIT_FULL_ART = "full_art"
TRAIT_FRAME = "frame"
TRAIT_BORDER = "border"
TRAIT_EFFECTS = "effects"

Traits = Mapping[str, str]


class Match(StrEnum):
    """How a rule selects cards out of its set."""

    #: every card in the set — the set's default spec
    SET = "set"
    #: collector numbers: ``188-216``, ``5``, ``182-216,220`` (``TG1-TG30`` too)
    NUMBERS = "numbers"
    #: explicit card ids, comma-separated
    IDS = "ids"
    #: the printing's rarity, any of a comma-separated list
    RARITY = "rarity"
    #: any of these subtypes (Pokémon ``VMAX``, ``ex``; MTG's own)
    SUBTYPE = "subtype"
    #: the provider flagged the printing as full-art
    FULL_ART = "full-art"
    #: the finish, any of a comma-separated list (``holofoil``, ``etched``)
    FINISH = "finish"
    #: the border *colour* the provider names. Colour is not geometry — white,
    #: silver and gold printings measure at their generation's width — but a
    #: decorative band is: Aetherdrift's yellow full-art box toppers measure
    #: 4.70mm against an ordinary 2.45. Too niche to ship a spec for, exactly the
    #: right size for a rule.
    BORDER = "border"
    #: MTG only: the frame generation Scryfall names (``1993``, ``1997``,
    #: ``2003``, ``2015``, ``future``). The most useful predicate there is, since
    #: it is the thing that actually changed the border — and one set code can
    #: hold two of them (a retro-frame bonus sheet inside a modern set).
    FRAME = "frame"
    #: MTG only: a *treatment* layered on the frame (``extendedart``,
    #: ``showcase``, ``legendary``, ``inverted``, …). Any of a comma-separated
    #: list, against a card that may carry several at once. Only two of the ~26
    #: move the border — `extendedart` and `fullart` — which is measured rather
    #: than assumed (`scripts/mtg-variants.py`); the rest exist here because a
    #: future treatment might, and a rule is how you say so without a release.
    EFFECT = "effect"

    @property
    def needs_traits(self) -> bool:
        """Does deciding this need what the provider said about the printing?

        A number range and an id list are read off the card id, so they answer
        offline and for a library filed years ago. The rest need ``.traits``.
        """
        return self in _TRAIT_MATCHES

    @property
    def label(self) -> str:
        return _MATCH_LABELS[self]

    @property
    def takes_value(self) -> bool:
        return self not in {Match.SET, Match.FULL_ART}


_TRAIT_MATCHES: frozenset[Match] = frozenset(
    {
        Match.RARITY,
        Match.SUBTYPE,
        Match.FULL_ART,
        Match.FINISH,
        Match.FRAME,
        Match.BORDER,
        Match.EFFECT,
    }
)

_MATCH_LABELS: dict[Match, str] = {
    Match.SET: "the whole set",
    Match.NUMBERS: "collector numbers",
    Match.IDS: "these card ids",
    Match.RARITY: "rarity",
    Match.SUBTYPE: "subtype",
    Match.FULL_ART: "full-art printings",
    Match.FINISH: "finish",
    Match.FRAME: "frame generation",
    Match.BORDER: "border colour",
    Match.EFFECT: "frame treatment",
}


class Via(StrEnum):
    """Where a resolution came from. Every surface reports this."""

    #: ``border --frame`` / the step's setting, for this run only
    OVERRIDE = "override"
    #: the card's stored pin — a decision someone made about this card
    PIN = "pin"
    #: what the provider said about this printing, recorded at fetch
    PRINTING = "printing"
    #: a rule of this library's own
    RULE = "rule"
    #: this set's default rule
    SET_DEFAULT = "set-default"
    #: the shipped baseline: Pokémon's set-id era, or MTG's frame generation
    ERA = "era"
    #: **no spec at all.** Nothing measured describes this printing, so there is
    #: nothing to fit against and :attr:`Resolution.spec` is ``None``. There used to
    #: be a per-game fallback spec here; it meant a card of an unmeasured frame was
    #: silently reshaped to somebody else's numbers, which looks perfect and is wrong
    #: on paper. Refusing is the honest answer.
    NONE = "none"

    @property
    def label(self) -> str:
        return _VIA_LABELS[self]


_VIA_LABELS: dict[Via, str] = {
    Via.OVERRIDE: "this run",
    Via.PIN: "pinned to this card",
    Via.PRINTING: "the printing",
    Via.RULE: "a rule",
    Via.SET_DEFAULT: "the set's default",
    Via.ERA: "its era/frame",
    Via.NONE: "no spec measured for this printing",
}


def parse_match(value: str | None) -> Match | None:
    try:
        return Match(str(value).strip().lower())
    except ValueError:
        return None


def parse_via(value: str | None) -> Via | None:
    try:
        return Via(str(value).strip().lower())
    except ValueError:
        return None


# ------------------------------------------------------------------- rules ----
_RULE_ID = re.compile(r"^r(\d+)$")
#: a collector number split into its alpha prefix and its digits, so ``TG12``
#: ranges inside ``TG1-TG30`` and never inside ``1-30``
_NUMBER = re.compile(r"^\s*([A-Za-z]*)0*(\d+)\s*([A-Za-z]*)\s*$")


@dataclass(frozen=True, slots=True)
class Rule:
    """One selector → spec, inside one set of one game."""

    id: str
    game: GameId
    set_id: str
    match: Match
    #: what the selector needs: a range list, an id list, a rarity list. Empty
    #: for the whole-set and full-art matches, which need nothing.
    value: str
    spec: str

    @property
    def is_default(self) -> bool:
        return self.match is Match.SET

    @property
    def is_global(self) -> bool:
        """Does this rule apply to every set of its game?

        A frame *treatment* is not a property of a set — ``extendedart`` runs the
        art to the card edges in every set that ever printed one — so a rule that
        could only name one set could not express it at all. An empty ``set_id``
        means "this game", and global rules are tried *after* the set-specific ones
        of the same kind (:meth:`Registry.for_set`), so a set can always overrule.
        """
        return not self.set_id

    @property
    def scope(self) -> str:
        return self.set_id or "every set"

    def covers(self, game: GameId, set_id: str) -> bool:
        if self.game is not game:
            return False
        return self.is_global or self.set_id.lower() == (set_id or "").lower()

    def selects(self, card_id: str, traits: Traits | None) -> bool | None:  # noqa: PLR0911
        """Does this rule claim ``card_id``? ``None`` = cannot tell yet.

        ``None`` is returned only for a trait predicate with no traits recorded —
        the honest answer for a card filed before proxdex kept them, and one the
        caller reports rather than rounds down to "no".
        """
        if self.match is Match.SET:
            return True
        if self.match is Match.IDS:
            return card_id.lower() in _list(self.value)
        if self.match is Match.NUMBERS:
            return _in_ranges(number_of(card_id), self.value)
        if traits is None:
            return None
        if self.match is Match.FULL_ART:
            return _flag(traits.get(TRAIT_FULL_ART))
        if self.match is Match.EFFECT:
            # An absent or empty value here means "this printing carries no frame
            # treatments", which is an *answer* and the commonest one — 93190 of
            # Magic's 116233 printings have none. Reporting it as undecidable would
            # put a warning on four cards in five for a question that was answered.
            # Same reading as `full_art`, whose absence has always meant `False`.
            return any(v in _list(traits.get(TRAIT_EFFECTS)) for v in _list(self.value))
        key = {
            Match.RARITY: TRAIT_RARITY,
            Match.SUBTYPE: TRAIT_SUBTYPES,
            Match.FINISH: TRAIT_FINISHES,
            Match.FRAME: TRAIT_FRAME,
            Match.BORDER: TRAIT_BORDER,
        }[self.match]
        have = _list(traits.get(key, ""))
        if not have:
            return None
        return any(v in have for v in _list(self.value))

    @property
    def describes(self) -> str:
        """One line naming what this rule catches, for a table cell."""
        if not self.match.takes_value:
            return self.match.label
        return f"{self.match.label} {self.value}"

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "game": self.game.value,
            "set": self.set_id,
            "match": self.match.value,
            "value": self.value,
            "spec": self.spec,
            "describes": self.describes,
            "scope": self.scope,
            "global": self.is_global,
        }


def number_of(card_id: str) -> str:
    """A card id's collector number — everything after the first ``-``.

    MTG's Alchemy rebalances are ``A-123`` inside set ``y22``, so the id splits
    on the *first* dash and the rest is the number, dashes and all.
    """
    _, _, number = (card_id or "").partition("-")
    return number


def _list(value: str | None) -> list[str]:
    return [p.strip().lower() for p in str(value or "").split(",") if p.strip()]


def _flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _key(number: str) -> tuple[str, int] | None:
    """``TG12`` → ``("tg", 12)``; anything unparseable → ``None``."""
    m = _NUMBER.match(number or "")
    if m is None:
        return None
    prefix = (m.group(1) or m.group(3) or "").lower()
    return (prefix, int(m.group(2)))


def _in_ranges(number: str, spec: str) -> bool:
    """Is ``number`` inside any of ``spec``'s comma-separated ranges?

    A range only matches numbers with the *same* alpha prefix, so ``TG1-TG30``
    never swallows card 12 and ``1-30`` never swallows ``TG12``.
    """
    want = _key(number)
    if want is None:
        return False
    for part in _list(spec):
        lo_text, _, hi_text = part.partition("-")
        lo = _key(lo_text)
        if lo is None:
            continue
        hi = _key(hi_text) if hi_text else lo
        if hi is None or hi[0] != lo[0] or want[0] != lo[0]:
            continue
        if lo[1] <= want[1] <= hi[1]:
            return True
    return False


# ---------------------------------------------------------------- resolution --
@dataclass(frozen=True, slots=True)
class Resolution:
    """The spec a fit will run against, and how it was chosen."""

    #: ``None`` when nothing measured describes this printing — see :attr:`Via.NONE`
    spec: FrameGuide | None
    via: Via
    #: the rule id, when ``via`` is a rule or a set default
    rule: str | None = None
    #: a spec id that was asked for and does not exist — a pin left behind by a
    #: removed spec, or a hand-edited marker. The fit falls back, and says so.
    missing: str | None = None
    #: rules that could not be decided because this card has no recorded traits
    undecided: tuple[str, ...] = ()

    @property
    def have(self) -> bool:
        """Is there a spec to fit against at all?"""
        return self.spec is not None

    @property
    def sure(self) -> bool:
        """Nothing to warn about: a spec exists, no dangling id, no undecidable rule.

        Deliberately **not** a judgement about the spec's numbers. A spec is four
        numbers and a note (see :mod:`proxdex.frames`); whether they are good is a
        question about a physical card that no field on this object can answer, and
        the earlier version that graded them said "trusted" about readings that
        inherited a scan's crop.
        """
        return self.spec is not None and self.missing is None and not self.undecided

    @property
    def note(self) -> str:
        """One line for the CLI and the UI — the same sentence on both."""
        if self.spec is None:
            return (
                "no frame spec measured for this printing — measure one "
                "(`proxdex frames set`) or pass a spec for this run"
            )
        parts = [f"{self.spec.name} ({self.via.label})"]
        if self.missing:
            parts.append(f"— spec '{self.missing}' no longer exists")
        if self.undecided:
            parts.append(
                f"— {len(self.undecided)} rule(s) need this printing's traits; "
                "re-fetch the card or pin a spec"
            )
        return " ".join(parts)

    def json(self) -> dict[str, Any]:
        return {
            "spec": self.spec.json() if self.spec else None,
            "shipped": self.spec is not None and frames.is_shipped(self.spec.id),
            "have": self.have,
            "via": self.via.value,
            "via_label": self.via.label,
            "rule": self.rule,
            "missing": self.missing,
            "undecided": list(self.undecided),
            "sure": self.sure,
            "note": self.note,
        }


@dataclass(slots=True)
class Registry:
    """Every spec this library can fit to, and the rules that pick between them."""

    specs: dict[str, FrameGuide] = field(default_factory=dict)
    rules: tuple[Rule, ...] = ()
    #: files that were unreadable, so a listing can say so instead of losing them
    broken: tuple[str, ...] = ()
    #: the next rule number, *persisted* — see :meth:`next_number`
    counter: int = 0

    def get(self, spec_id: str | None) -> FrameGuide | None:
        return self.specs.get(frames.clean_id(spec_id)) if spec_id else None

    def choices(self, game: GameId | None = None) -> list[FrameGuide]:
        """Specs selectable for ``game`` — its own, plus the game-agnostic ones."""
        return [
            s
            for s in self.specs.values()
            if s.game is None or game is None or s.game is game
        ]

    def for_set(self, game: GameId, set_id: str) -> list[Rule]:
        """Every rule that could claim a card of this set, **most specific first**.

        Four bands, in order: this set's exceptions, the game's exceptions, this
        set's default, the game's default. A stable sort, so file order still decides
        between two rules of the same band — that is what `assign` puts exceptions at
        the front of the file for.
        """
        return sorted(
            (r for r in self.rules if r.covers(game, set_id)),
            key=lambda r: (r.is_default, r.is_global),
        )

    def uses(self, spec_id: str) -> list[Rule]:
        return [r for r in self.rules if r.spec == spec_id]

    def next_number(self) -> int:
        """The next rule number. Numbering never reuses, which is why the counter
        is stored rather than derived from the rules present: a removed rule's id
        stays gone, so a note about ``r7`` never comes to mean a different rule."""
        highest = 0
        for rule in self.rules:
            m = _RULE_ID.match(rule.id)
            if m is not None:
                highest = max(highest, int(m.group(1)))
        return max(self.counter, highest + 1)


def resolve(
    reg: Registry,
    card_id: str,
    set_id: str,
    game: GameId = games.DEFAULT,
    *,
    override: str | None = None,
    pin: str | None = None,
    printing: str | None = None,
    traits: Traits | None = None,
) -> Resolution:
    """Which spec fits this card, and why. See the module docstring for the order.

    Every argument that names a spec is untrusted (a CLI flag, a marker file a
    hand edited, a pin whose spec was removed), so one that does not resolve is
    *reported* through :attr:`Resolution.missing` and skipped — never a traceback
    in the middle of a card walk, and never a silent fallback either.
    """
    missing: str | None = None
    for wanted, via in (
        (override, Via.OVERRIDE),
        (pin, Via.PIN),
        (printing, Via.PRINTING),
    ):
        if not wanted:
            continue
        spec = reg.get(wanted)
        if spec is not None:
            return Resolution(spec=spec, via=via)
        missing = missing or str(wanted)

    undecided: list[str] = []
    default: Rule | None = None
    for rule in reg.for_set(game, set_id):
        if rule.is_default:
            default = default or rule
            continue
        verdict = rule.selects(card_id, traits)
        if verdict is None:
            undecided.append(rule.id)
            continue
        if verdict:
            spec = reg.get(rule.spec)
            if spec is not None:
                return Resolution(
                    spec=spec,
                    via=Via.RULE,
                    rule=rule.id,
                    missing=missing,
                    undecided=tuple(undecided),
                )
            missing = missing or rule.spec

    if default is not None:
        spec = reg.get(default.spec)
        if spec is not None:
            return Resolution(
                spec=spec,
                via=Via.SET_DEFAULT,
                rule=default.id,
                missing=missing,
                undecided=tuple(undecided),
            )
        missing = missing or default.spec

    shipped = frames.baseline(set_id, game, traits)
    if shipped is not None and (spec := reg.get(shipped)) is not None:
        return Resolution(
            spec=spec, via=Via.ERA, missing=missing, undecided=tuple(undecided)
        )
    # nothing measured describes this printing. No spec, and no substitute: `border`
    # refuses, the reports name the card, and `--frame` is the escape hatch.
    return Resolution(
        spec=None, via=Via.NONE, missing=missing, undecided=tuple(undecided)
    )


# ----------------------------------------------------------------- storage ----
def specs_dir(root: Path) -> Path:
    return root / DIR


def path_for(root: Path, spec_id: str) -> Path:
    return specs_dir(root) / f"{spec_id}.json"


def rules_path(root: Path) -> Path:
    return specs_dir(root) / RULES


def load(root: Path) -> Registry:
    """The shipped specs, overlaid with this library's own, plus its rules."""
    specs: dict[str, FrameGuide] = dict(frames.SHIPPED)
    broken: list[str] = []
    folder = specs_dir(root)
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.name == RULES:
            continue
        data = _read(path)
        if data is None:
            broken.append(path.name)
            continue
        spec = frames.from_json({**data, "id": data.get("id") or path.stem})
        if not spec.id or spec.id in frames.RESERVED:
            # `borderless` is what code returns for a frameless printing; a file
            # may not redefine it, and a file with no usable id is not a spec
            broken.append(path.name)
            continue
        shipped = frames.SHIPPED.get(spec.id)
        specs[spec.id] = frames.merge(shipped, spec) if shipped else spec
    rules, counter = _load_rules(root)
    return Registry(specs=specs, rules=rules, broken=tuple(broken), counter=counter)


def _load_rules(root: Path) -> tuple[tuple[Rule, ...], int]:
    data = _read(rules_path(root))
    if data is None:
        return ((), 0)
    out: list[Rule] = []
    raw = data.get("rules")
    for item in raw if isinstance(raw, list) else []:
        rule = _rule_from_json(item)
        if rule is not None:
            out.append(rule)
    try:
        counter = int(data.get("next", 0))
    except (TypeError, ValueError):
        counter = 0
    return (tuple(out), counter)


def _rule_from_json(item: Any) -> Rule | None:
    if not isinstance(item, dict):
        return None
    entry: dict[str, Any] = item
    game = games.parse(entry.get("game"))
    match = parse_match(entry.get("match"))
    spec = frames.clean_id(entry.get("spec"))
    set_id = str(entry.get("set") or "").strip().lower()
    rule_id = str(entry.get("id") or "").strip()
    # an empty set is meaningful — the rule covers every set of its game — so it is
    # *not* one of the fields a missing value invalidates the rule over
    if game is None or match is None or not spec or not rule_id:
        return None
    return Rule(
        id=rule_id,
        game=game,
        set_id=set_id,
        match=match,
        value=str(entry.get("value") or "").strip(),
        spec=spec,
    )


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def save(root: Path, spec: FrameGuide) -> Path:
    """Store one spec. Shipped ids may be corrected; ``borderless`` may not."""
    if spec.id in frames.RESERVED:
        raise ProxdexError(
            f"'{spec.id}' is reserved — it is what proxdex uses for a printing "
            "with no frame at all, so its numbers cannot change"
        )
    if not frames.valid_id(spec.id):
        raise ProxdexError(
            f"'{spec.id}' is not a usable spec id — lowercase letters, digits "
            "and single dashes (it becomes a filename and a CLI value)"
        )
    dst = path_for(root, spec.id)
    _write(dst, spec.json())
    return dst


def delete(root: Path, spec_id: str, *, pinned: Iterable[str] = ()) -> None:
    """Remove a library's own spec.

    Refused while anything still points at it: a rule naming a spec that does not
    exist, or a card pinned to one, borders that card off the *fallback* — a
    silently different picture. Nothing else in proxdex cleans up after a delete
    behind your back, and this does not either.
    """
    if spec_id in frames.RESERVED:
        raise ProxdexError(f"'{spec_id}' is reserved and cannot be removed")
    path = path_for(root, spec_id)
    if not path.exists():
        if spec_id in frames.SHIPPED:
            raise ProxdexError(
                f"'{spec_id}' is shipped with proxdex — there is nothing stored "
                "here to remove (a local correction would be)"
            )
        raise ProxdexError(f"no spec named '{spec_id}' in this library")
    reg = load(root)
    if used := reg.uses(spec_id):
        rules = ", ".join(r.id for r in used)
        raise ProxdexError(
            f"'{spec_id}' is still used by rule(s) {rules} — remove them first "
            "(`proxdex frames unassign <rule>`)"
        )
    if cards := sorted(pinned):
        shown = ", ".join(cards[:5]) + (
            f" +{len(cards) - 5} more" if len(cards) > 5 else ""
        )
        raise ProxdexError(
            f"'{spec_id}' is pinned to {len(cards)} card(s): {shown} — unpin them "
            "first (`proxdex frames unpin <id>`)"
        )
    path.unlink()


def write_rules(root: Path, rules: Iterable[Rule], *, counter: int = 0) -> Path:
    """Store the rule list and the *next* number to hand out.

    ``counter`` is written even though it could be derived from the ids present,
    because that derivation is exactly what would reuse a removed rule's number.
    """
    kept = list(rules)
    _write(
        dst := rules_path(root),
        {
            "version": 1,
            "next": max(counter, len(kept) + 1),
            "rules": [r.json() for r in kept],
        },
    )
    return dst


def assign(
    root: Path,
    spec_id: str,
    game: GameId,
    set_id: str,
    match: Match,
    value: str = "",
) -> Rule:
    """Add a rule. The most specific selectors are kept *first* in the file, so
    a set default can be added at any time without burying the exceptions.

    An empty ``set_id`` makes it a **game-wide** rule. That is the only way to say
    something true of a frame *treatment*: ``extendedart`` runs the art to the card
    edges in every set that ever printed one, and enumerating those sets would be a
    list that goes stale with every release. Global rules lose to set-specific ones
    of the same kind, so a set can always overrule.
    """
    reg = load(root)
    spec = reg.get(spec_id)
    if spec is None:
        known = ", ".join(sorted(reg.specs)) or "none"
        raise ProxdexError(f"no frame spec named '{spec_id}'. Known: {known}")
    if spec.game is not None and spec.game is not game:
        raise ProxdexError(
            f"'{spec.id}' describes {games.get(spec.game).name} frames, so it "
            f"cannot be assigned to a {games.get(game).name} set"
        )
    sid = set_id.strip().lower()
    if not sid and match is Match.SET:
        raise ProxdexError(
            "a whole-set rule with no set would claim every card of "
            f"{games.get(game).name}, which is what the game's own default spec "
            "already is. Name a set, or match on something narrower"
        )
    if match.takes_value and not value.strip():
        raise ProxdexError(f"matching by {match.label} needs a value (e.g. 188-216)")
    if match is Match.NUMBERS and not _valid_ranges(value):
        raise ProxdexError(
            f"'{value}' is not a collector-number range — try 188-216, 5, or "
            "TG1-TG30, comma-separated"
        )
    number = reg.next_number()
    rule = Rule(
        id=f"r{number}",
        game=game,
        set_id=sid,
        match=match,
        value=value.strip(),
        spec=spec.id,
    )
    existing = list(reg.rules)
    if match is Match.SET:
        for old in [
            r
            for r in existing
            if r.is_default and r.game is game and r.set_id.lower() == sid
        ]:
            # one default per set: a second would never be reached, and a rule
            # that can never fire is worse than no rule. Compared on the set id
            # itself rather than through `covers`, which a global rule answers
            # `True` to for every set and would have deleted them all.
            existing.remove(old)
    write_rules(
        root,
        [*existing, rule] if match is Match.SET else [rule, *existing],
        counter=number + 1,
    )
    return rule


def unassign(root: Path, rule_id: str) -> Rule:
    reg = load(root)
    found = next((r for r in reg.rules if r.id == rule_id), None)
    if found is None:
        raise ProxdexError(f"no rule '{rule_id}' — `proxdex frames rules` lists them")
    write_rules(
        root, [r for r in reg.rules if r.id != rule_id], counter=reg.next_number()
    )
    return found


def _valid_ranges(value: str) -> bool:
    parts = _list(value)
    if not parts:
        return False
    for part in parts:
        lo, _, hi = part.partition("-")
        if _key(lo) is None or (hi and _key(hi) is None):
            return False
    return True


def spec(
    spec_id: str,
    name: str,
    game: GameId | None,
    mm: tuple[float, float, float, float],
    note: str = "",
    ref_mm: tuple[float, float] = (63.0, 88.0),
) -> FrameGuide:
    """A spec from per-edge millimetres. **One constructor, no grades.**

    There used to be three of these — ``measured``, ``scanned``, ``estimated`` —
    and the middle one was the mistake: it graded a border read off the
    publisher's scan as trustworthy, when a scan's crop shifts every such reading
    by the same unknown amount. The four numbers are the four numbers however they
    were arrived at; ``note`` is where you say which card, which calipers, or that
    you typed it.
    """
    return FrameGuide(
        id=frames.clean_id(spec_id),
        name=name.strip() or spec_id,
        game=game,
        inset=frames.mm_to_inset(*mm, w=ref_mm[0], h=ref_mm[1]),
        note=note.strip(),
        ref_mm=ref_mm,
    )


# ------------------------------------------------------------------- audit ----
class Fault(StrEnum):
    """Something wrong that a person has to decide about.

    These are the *only* frame warnings there are, and every one of them is a
    broken reference or an unanswerable question — never an opinion about how good
    a spec's numbers are. A set that resolves to the game's default is not listed:
    that is the system working, and a report that flagged it said "unmeasured"
    about 1046 MTG sets whose border is known per card.
    """

    #: a `frames/*.json` that could not be read, so it is not being used
    UNREADABLE = "unreadable"
    #: something names a spec that does not exist — a pin left behind by a removed
    #: spec, or a rule pointing at one. The fit silently falls back.
    MISSING = "missing"
    #: a trait rule on a card with no recorded traits: undecidable, not false
    UNDECIDED = "undecided"
    #: nothing measured describes this printing, so there is no spec to fit against
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return _FAULT_LABELS[self]


_FAULT_LABELS: dict[Fault, str] = {
    Fault.UNREADABLE: "unreadable spec file",
    Fault.MISSING: "names a spec that does not exist",
    Fault.UNDECIDED: "needs this printing's traits",
    Fault.UNKNOWN: "no frame spec measured for this printing",
}

_FAULT_HINTS: dict[Fault, str] = {
    Fault.UNREADABLE: "Fix the JSON or delete the file; it is being ignored.",
    Fault.MISSING: (
        "Point it at a spec that exists, or drop it — `proxdex frames list` shows "
        "what this library has."
    ),
    Fault.UNDECIDED: (
        "Re-fetch the card so its traits are recorded, or pin a spec to it "
        "(`proxdex frames pin`)."
    ),
    Fault.UNKNOWN: (
        "Measure a real card and record it (`proxdex frames set`), then assign it — "
        "docs/measuring-frames.md says how. `border` refuses this card until then, "
        "rather than fitting it to somebody else's numbers. Re-fetching may also "
        "help, if its frame was never recorded."
    ),
}


@dataclass(frozen=True, slots=True)
class Issue:
    """One warning: what is wrong, about what, and what to do."""

    fault: Fault
    #: the card id, rule id or filename this is about
    subject: str
    detail: str = ""

    @property
    def hint(self) -> str:
        return _FAULT_HINTS[self.fault]

    def json(self) -> dict[str, Any]:
        return {
            "fault": self.fault.value,
            "label": self.fault.label,
            "subject": self.subject,
            "detail": self.detail,
            "hint": self.hint,
        }


def audit(reg: Registry, resolved: Iterable[tuple[str, Resolution]]) -> list[Issue]:
    """Every frame warning this library has, in the order worth reading.

    ``resolved`` is (card id, resolution) pairs — the caller resolves, because it
    is the thing holding the cards, and this module must not import the library.
    The CLI's `frames check` and the UI's warnings panel both call it, so the two
    cannot disagree about what counts as a problem.
    """
    out = [Issue(fault=Fault.UNREADABLE, subject=name) for name in sorted(reg.broken)]
    out += [
        Issue(
            fault=Fault.MISSING,
            subject=rule.id,
            detail=f"{rule.set_id} · {rule.describes} → '{rule.spec}'",
        )
        for rule in reg.rules
        if reg.get(rule.spec) is None
    ]
    for card_id, found in resolved:
        if found.missing is not None:
            landed = found.spec.id if found.spec else "no spec at all"
            out.append(
                Issue(
                    fault=Fault.MISSING,
                    subject=card_id,
                    detail=f"'{found.missing}' — fitting to {landed} instead",
                )
            )
        if found.undecided:
            out.append(
                Issue(
                    fault=Fault.UNDECIDED,
                    subject=card_id,
                    detail=f"rule(s) {', '.join(found.undecided)}",
                )
            )
        elif found.spec is None:
            out.append(Issue(fault=Fault.UNKNOWN, subject=card_id))
    return out


def json_registry(reg: Registry) -> dict[str, Any]:
    """The registry as the web UI reads it — one shape, served and rendered once."""
    return {
        "specs": [
            {
                **s.json(),
                "shipped": frames.is_shipped(s.id),
                "mm": list(s.mm()),
            }
            for s in reg.specs.values()
        ],
        "rules": [r.json() for r in reg.rules],
        "matches": [
            {
                "id": m.value,
                "label": m.label,
                "takes_value": m.takes_value,
                "needs_traits": m.needs_traits,
            }
            for m in Match
        ],
        "broken": list(reg.broken),
        "reserved": sorted(frames.RESERVED),
    }
