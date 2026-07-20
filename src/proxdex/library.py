"""Library model: root discovery, cards, and pipeline stages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from .errors import LibraryError

MARKER = "proxdex.toml"


class Stage(IntEnum):
    ORIGINAL = 1
    UPSCALED = 2
    EDITED = 3
    PRINT = 4

    @property
    def label(self) -> str:
        return _STAGE_LABELS[self]


_STAGE_LABELS = {
    Stage.ORIGINAL: "original",
    Stage.UPSCALED: "upscaled",
    Stage.EDITED: "edited",
    Stage.PRINT: "print",
}


def slugify(text: str) -> str:
    text = text.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


@dataclass(slots=True)
class Card:
    """One card and its per-stage assets, living in a single folder."""

    id: str  # canonical TCG id, e.g. "ex3-90"
    dir: Path
    set_id: str  # "ex3"

    @property
    def name(self) -> str:
        _, _, tail = self.dir.name.partition("_")
        return tail.replace("-", " ")

    def stage_path(self, stage: Stage) -> Path:
        return self.dir / f"{self.id}_{stage.value}_{stage.label}.png"

    def has(self, stage: Stage) -> bool:
        return self.stage_path(stage).exists()

    def best(self, *prefer: Stage) -> Path | None:
        """Highest-priority stage image that exists (first match wins)."""
        for stage in prefer:
            if self.has(stage):
                return self.stage_path(stage)
        return None


@dataclass(slots=True)
class Library:
    """A proxdex library rooted at a directory containing ``proxdex.toml``."""

    root: Path

    @classmethod
    def discover(
        cls, start: Path | None = None, explicit: Path | None = None
    ) -> Library:
        if explicit is not None:
            root = explicit.resolve()
            if not (root / MARKER).exists():
                raise LibraryError(f"{root} has no {MARKER} — run `proxdex init` there")
            return cls(root)
        current = (start or Path.cwd()).resolve()
        for candidate in (current, *current.parents):
            if (candidate / MARKER).exists():
                return cls(candidate)
        raise LibraryError(
            f"no proxdex library here or in any parent (looking for {MARKER}).\n"
            "run `proxdex init` in your library folder, or pass --root PATH."
        )

    @property
    def cards_dir(self) -> Path:
        return self.root / "cards"

    @property
    def batches_dir(self) -> Path:
        return self.root / "print-batches"

    def cards(self) -> list[Card]:
        out: list[Card] = []
        for d in sorted(self.cards_dir.glob("*/*")):
            if d.is_dir():
                out.append(self._card(d))
        return out

    def find(self, cid: str) -> Card | None:
        for d in sorted(self.cards_dir.glob(f"*/{cid}_*")):
            if d.is_dir():
                return self._card(d)
        return None

    def set_dir(self, set_id: str, set_name: str) -> Path:
        for d in sorted(self.cards_dir.glob(f"{set_id}-*")):
            if d.is_dir():
                return d
        d = self.cards_dir / f"{set_id}-{slugify(set_name)}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def select(self, ids: tuple[str, ...]) -> list[Card]:
        """Resolve card ids to cards; empty or ('all',) means every card."""
        if not ids or ids == ("all",):
            return self.cards()
        out: list[Card] = []
        for cid in ids:
            card = self.find(cid)
            if card is not None:
                out.append(card)
        return out

    @staticmethod
    def _card(d: Path) -> Card:
        return Card(
            id=d.name.split("_", 1)[0],
            dir=d,
            set_id=d.parent.name.split("-", 1)[0],
        )
