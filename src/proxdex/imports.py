"""Filing loose images into card stages — planned before anything is written.

``import`` is the one command whose input proxdex did not make: an Upscayl output
folder, a flatbed scan, somebody's dump of PNGs. What each file *means* has to be
read off its name, so the guess can be wrong — which is why the plan is a
separate, pixel-free step over **filenames alone**, and why both ``proxdex import
--dry-run`` and the web UI's import wizard ask this module instead of doing their
own arithmetic. Same reason :func:`proxdex.sheet.plan` exists: a preview and the
act it previews must not be able to disagree.

Planning from names is also what makes the wizard cheap. A folder of two hundred
files is two hundred *strings* on the wire; the thumbnails come from the
browser's own copies, and the bytes are only uploaded for the rows you keep.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Any

from proxdex.games import GameId
from proxdex.library import (
    FRONT,
    Library,
    Stage,
    face_suffix,
    parse_stage_file,
)

#: what proxdex will accept as a card image. Suffix only — the plan never opens a
#: file (the UI's copy is in the browser), so this catches ``.DS_Store`` and a
#: stray ``notes.txt`` rather than a PNG that happens to be truncated.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"})

#: Upscayl writes its scale into the filename ("…_upscayl_4x_realesrgan.png"), so
#: a name carrying it is an upscale output rather than a source scan.
_UPSCALED_HINT = "upscayl"


class Disposition(StrEnum):
    """What the plan says will happen to one file.

    Every member is a different thing the person has to do about it, which is why
    there are this many: "no id in the name" is fixed by typing an id, "no card
    folder" by confirming the id or fetching first, and "two files want the same
    slot" by dropping one of them.
    """

    NEW = "new"  # writes a stage that does not exist yet
    CREATE = "create"  # ditto, and the card folder is looked up and created
    REPLACE = "replace"  # overwrites the stage image already there
    SKIP = "skip"  # a stage image is there and this run keeps it
    COLLIDE = "collide"  # an earlier file in this run wants the same slot
    MISSING = "missing"  # the id was guessed and no such card is filed
    NO_SIDE = "no-side"  # the card has no such face
    UNMATCHED = "unmatched"  # no card id in the name and none assigned
    NOT_IMAGE = "not-image"  # not a card image by its suffix

    @property
    def writes(self) -> bool:
        """Whether this file gets filed."""
        return self in {Disposition.NEW, Disposition.CREATE, Disposition.REPLACE}

    @property
    def blocked(self) -> bool:
        """Whether something has to change before this file can be filed.

        A skip is not blocked — it is a decision this run already made.
        """
        return self in {
            Disposition.COLLIDE,
            Disposition.MISSING,
            Disposition.NO_SIDE,
            Disposition.UNMATCHED,
            Disposition.NOT_IMAGE,
        }


class OnExisting(StrEnum):
    """What to do when the destination stage image already exists.

    A per-run choice, like every page setting on a print run — never a config
    key. ``overwrite`` is the default because it is what ``import`` has always
    done; the difference now is that the plan says so before it happens.
    """

    OVERWRITE = "overwrite"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class Item:
    """One file offered for import, with whatever the caller decided about it.

    Everything but ``name`` is an override of what the filename says. ``id`` set
    means the caller *confirmed* an id (the CLI's ``--id``, a wizard row) — which
    is the difference between creating a card folder and refusing to invent one
    from a filename that might be anything.
    """

    name: str  # the file, as a path or a bare name
    id: str | None = None
    game: GameId | None = None
    stage: Stage | None = None
    face: int | None = None  # 0-based, like everywhere inside proxdex


@dataclass(frozen=True, slots=True)
class Assignment:
    """One file's planned destination, and why.

    ``dest`` is relative to the library root and is ``None`` when there is
    nowhere to put the file yet — either it is blocked, or the card folder does
    not exist and only a metadata lookup can say what it will be called.
    """

    item: Item
    disposition: Disposition
    stage: Stage
    face: int
    guessed_id: bool
    id: str | None = None
    card_name: str = ""
    game: GameId | None = None
    dest: Path | None = None
    discards: tuple[Stage, ...] = ()
    reason: str = ""

    @property
    def name(self) -> str:
        return Path(self.item.name).name

    def json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "disposition": self.disposition.value,
            "id": self.id,
            "card_name": self.card_name,
            "game": self.game.value if self.game else None,
            "stage": self.stage.label,
            "face": self.face + 1,  # 1-based at the boundary, like `--face`
            "guessed_id": self.guessed_id,
            "dest": str(self.dest) if self.dest else None,
            "discards": [s.label for s in self.discards],
            "writes": self.disposition.writes,
            "blocked": self.disposition.blocked,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class Run:
    """A planned import: every file, what happens to it, and what it costs.

    Worked out before a byte moves, so ``--dry-run`` and the wizard's review
    table cannot promise a different outcome than the import produces.
    """

    items: tuple[Assignment, ...]
    on_existing: OnExisting = OnExisting.OVERWRITE

    @property
    def ready(self) -> tuple[Assignment, ...]:
        return tuple(a for a in self.items if a.disposition.writes)

    @property
    def skipped(self) -> tuple[Assignment, ...]:
        return tuple(a for a in self.items if a.disposition is Disposition.SKIP)

    @property
    def blocked(self) -> tuple[Assignment, ...]:
        return tuple(a for a in self.items if a.disposition.blocked)

    @property
    def creates(self) -> tuple[str, ...]:
        """Card ids this run would look up and file for the first time."""
        seen: dict[str, None] = {}
        for a in self.items:
            if a.disposition is Disposition.CREATE and a.id:
                seen.setdefault(a.id, None)
        return tuple(seen)

    @property
    def discards(self) -> int:
        """How many later-stage images this run would invalidate."""
        return sum(len(a.discards) for a in self.ready)

    @property
    def cards(self) -> tuple[str, ...]:
        """The distinct cards this run touches, in the order they appear."""
        seen: dict[str, None] = {}
        for a in self.ready:
            if a.id:
                seen.setdefault(a.id, None)
        return tuple(seen)

    def json(self) -> dict[str, Any]:
        return {
            "on_existing": self.on_existing.value,
            "items": [a.json() for a in self.items],
            "ready": len(self.ready),
            "skipped": len(self.skipped),
            "blocked": len(self.blocked),
            "creates": list(self.creates),
            "discards": self.discards,
            "cards": list(self.cards),
        }


# ---- reading a filename ---------------------------------------------------
# Three guesses, each from the name alone and each overridable. They stay honest
# about being guesses: `Assignment.guessed_id` travels with the plan, and a
# guessed id is never enough on its own to create a card folder.

#: ``ex3-90``, ``neo-136``, ``bw11-1a`` — a set code then a collector number,
#: which MTG allows a letter suffix on. Anything stranger needs an explicit id.
_ID_IN_NAME = re.compile(r"[a-z]+\d*-\d+[a-z]?", re.IGNORECASE)


def guess_id(stem: str) -> str | None:
    """The card id a filename starts with, or ``None``."""
    own = parse_stage_file(stem)
    if own is not None:
        return own.id
    m = _ID_IN_NAME.match(stem)
    return m.group(0) if m else None


def guess_stage(stem: str) -> Stage:
    """Which stage a file is. proxdex's own filenames say outright; otherwise an
    Upscayl output is an upscale and everything else is a source scan."""
    own = parse_stage_file(stem)
    if own is not None:
        return own.stage
    return Stage.UPSCALED if _UPSCALED_HINT in stem.lower() else Stage.ORIGINAL


def guess_face(stem: str) -> int:
    """Which side a file is, from the ``_f<n>`` suffix proxdex itself writes.

    So a folder of proxdex's own files round-trips: ``xy1-1_3_upscaled_f2.png``
    goes back to the second side it came from rather than over the front.
    """
    own = parse_stage_file(stem)
    if own is not None:
        return own.face
    lower = stem.lower()
    # at most two sides are supported, and face 0's suffix is empty
    return next((f for f in (1, 2) if face_suffix(f) in lower), FRONT)


# ---- the plan --------------------------------------------------------------

#: a destination, spelled without a Path so it identifies a slot even before the
#: card folder exists: (card id, stage, face)
_Slot = tuple[str, int, int]


def plan(
    lib: Library,
    items: Sequence[Item],
    *,
    on_existing: OnExisting = OnExisting.OVERWRITE,
) -> Run:
    """Work out what importing ``items`` would do, touching nothing.

    No network: a card that is not filed yet is reported as ``create`` and looked
    up at import time, because drawing a preview of a two-hundred-file folder
    must not cost two hundred API calls.
    """
    out: list[Assignment] = []
    #: slots already taken by an earlier file in this run. Two files wanting one
    #: slot is a folder's most ordinary hazard (``art.png`` beside ``art (1).png``)
    #: and the loser used to be overwritten in silence.
    claimed: dict[_Slot, int] = {}
    for index, item in enumerate(items):
        found = _assign(lib, item, on_existing)
        slot: _Slot | None = (
            (found.id, int(found.stage), found.face)
            if found.disposition.writes and found.id is not None
            else None
        )
        if slot is not None and slot in claimed:
            first = claimed[slot] + 1  # 1-based: it is a position in a list
            found = replace(
                found,
                disposition=Disposition.COLLIDE,
                dest=None,
                discards=(),
                reason=f"file {first} in this run already fills that slot",
            )
        elif slot is not None:
            claimed[slot] = index
        out.append(found)
    return Run(items=tuple(out), on_existing=on_existing)


def _assign(  # noqa: PLR0911 (a decision table: one return per disposition)
    lib: Library, item: Item, on_existing: OnExisting
) -> Assignment:
    """One file's destination, ignoring the rest of the run."""
    stem = Path(item.name).stem
    cid = item.id or guess_id(stem)
    stage = item.stage or guess_stage(stem)
    face = item.face if item.face is not None else guess_face(stem)
    # `id` set means the caller *confirmed* it, which is the whole difference
    # between creating a card folder and refusing to invent one from a filename
    guessed = item.id is None
    at = partial(
        Assignment, item=item, id=cid, stage=stage, face=face, guessed_id=guessed
    )

    if Path(item.name).suffix.lower() not in IMAGE_SUFFIXES:
        return at(disposition=Disposition.NOT_IMAGE, reason="not a card image")
    if cid is None:
        return at(
            disposition=Disposition.UNMATCHED, reason="no card id in the filename"
        )

    card = lib.find(cid)
    if card is None:
        if guessed:
            return at(
                disposition=Disposition.MISSING,
                reason=f"no card folder for {cid} — confirm the id, or fetch it first",
            )
        # the id was given, so the import may look it up and make the folder;
        # what that folder is called is not knowable without the lookup
        return at(disposition=Disposition.CREATE, game=item.game or lib.default_game)

    if face not in card.faces:
        return at(
            disposition=Disposition.NO_SIDE,
            game=card.game,
            card_name=card.name,
            reason=f"{cid} has {len(card.faces)} side(s), not {face + 1}",
        )

    dest = card.stage_path(stage, face)
    filed = partial(at, game=card.game, card_name=card.name)
    if dest.exists() and on_existing is OnExisting.SKIP:
        return filed(
            disposition=Disposition.SKIP,
            reason=f"{cid} already has that {stage.label} image",
        )
    return filed(
        disposition=Disposition.REPLACE if dest.exists() else Disposition.NEW,
        dest=dest.relative_to(lib.root),
        discards=tuple(s for s in Stage if s > stage and card.has(s, face)),
    )
