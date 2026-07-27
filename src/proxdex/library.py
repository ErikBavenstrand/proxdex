"""Library model: root discovery, cards, faces and pipeline stages.

All card state is derived from the filesystem — which ``<id>_<n>_<stage>.png``
files exist, plus the marker files ``.skip-<stage>``, ``.game``, ``.faces``,
``.front`` (which side prints on the front) and, for what the *printing* is,
``.layout`` / ``.oversized`` / ``.frame``. There is no database, so nothing can
drift out of sync with the images on disk, and nothing has to call an API again to
remember that a card is a meld half or has no printed border.

A card can have more than one *face* (MTG's transform/modal cards). Face 0 is
the front and keeps the plain filenames a single-faced card has always had, so
an existing library needs no migration; every later face carries an ``_f<n>``
suffix and has its own pipeline state, because a back face is a different
picture that needs its own border fit.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path

from proxdex import games
from proxdex.config import MARKER, Config
from proxdex.errors import LibraryError
from proxdex.frames import GuideId
from proxdex.games import GameId, Layout

ENV_ROOT = "PROXDEX_ROOT"
#: which TCG a card (or a whole set folder) belongs to — filesystem state, like
#: everything else. Written when the folder is created; set ids alone can't say.
GAME_MARKER = ".game"
#: the card's face names, one per line, front first. Absent = one unnamed face.
FACES_MARKER = ".faces"
#: what this printing puts on paper (a :class:`~proxdex.games.Layout`) — written
#: at fetch, so the card page can say "meld part" without calling the API again
LAYOUT_MARKER = ".layout"
#: printed at 89×127mm rather than 63×88 (planar, scheme, Vanguard). A flag file:
#: present means oversized, and `sheet` says so instead of shrinking it silently.
OVERSIZED_MARKER = ".oversized"
#: the frame guide this card needs, overriding its set's era — written at fetch
#: when the provider said the printing is borderless or full-art
FRAME_MARKER = ".frame"
#: the front face — its files carry no suffix, so old libraries just work
FRONT = 0


class Stage(IntEnum):
    ORIGINAL = 1  # source scan, downloaded from the game's image source
    BORDERED = 2  # frame expanded to correct trim proportions (optional)
    UPSCALED = 3  # Upscayl, after any border fix
    EDITED = 4  # graded — the trim-size master (no cut bleed)

    @property
    def label(self) -> str:
        return _STAGE_LABELS[self]


_STAGE_LABELS: dict[Stage, str] = {
    Stage.ORIGINAL: "original",
    Stage.BORDERED: "bordered",
    Stage.UPSCALED: "upscaled",
    Stage.EDITED: "edited",
}
STAGE_BY_LABEL: dict[str, Stage] = {v: k for k, v in _STAGE_LABELS.items()}


class Status(StrEnum):
    """A step's persisted 3-state. Run clears skip, skip removes the output,
    reset returns to pending."""

    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"


class Step(StrEnum):
    """The optional, skippable processing steps, by the name you type.

    ``original`` is the source, not a step, so it is not one of these.
    """

    BORDER = "border"
    UPSCALE = "upscale"
    GRADE = "grade"

    @property
    def stage(self) -> Stage:
        return _STEP_STAGES[self]


_STEP_STAGES: dict[Step, Stage] = {
    Step.BORDER: Stage.BORDERED,
    Step.UPSCALE: Stage.UPSCALED,
    Step.GRADE: Stage.EDITED,
}


#: which face prints as the sheet's front (a face index). Absent = the front.
FRONT_MARKER = ".front"
#: ``<id>_<stage>_<label>`` plus an optional ``_f<n>`` face suffix
_STAGE_FILE = re.compile(
    r"^(?P<id>.+)_(?P<n>\d+)_(?P<label>[a-z]+)(?:_f(?P<face>\d+))?$"
)


def face_suffix(face: int) -> str:
    """The filename suffix for a face. Face 0 has none, so a single-faced card's
    files are spelled exactly as they always were."""
    return "" if face <= FRONT else f"_f{face + 1}"


@dataclass(frozen=True, slots=True)
class StageFile:
    """What a stage filename says it is: card id, stage, face."""

    id: str
    stage: Stage
    face: int


def parse_stage_file(stem: str) -> StageFile | None:
    """Read a stage filename back — the inverse of :meth:`Card.stage_path`.

    ``None`` for anything that is not one of proxdex's own stage files, so an
    arbitrary scan being imported is never mistaken for one. The stage number and
    its label have to agree: ``ex3-90_3_upscaled`` is a stage file, and a
    hand-renamed ``ex3-90_9_upscaled`` is not.
    """
    m = _STAGE_FILE.match(stem)
    if m is None:
        return None
    stage = STAGE_BY_LABEL.get(m["label"])
    if stage is None or stage.value != int(m["n"]):
        return None
    face = int(m["face"]) - 1 if m["face"] else FRONT
    return StageFile(id=m["id"], stage=stage, face=face)


def slugify(text: str) -> str:
    text = text.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def read_game(folder: Path, default: GameId = games.DEFAULT) -> GameId:
    """The game a card or set folder belongs to: its own marker, then its set
    folder's, then ``default`` (libraries predating markers are all one game)."""
    for candidate in (folder, folder.parent):
        marker = candidate / GAME_MARKER
        if marker.is_file():
            found = games.parse(marker.read_text(encoding="utf-8"))
            if found is not None:
                return found
    return default


@dataclass(slots=True)
class Card:
    """One card and its per-stage assets, living in a single folder."""

    id: str  # canonical TCG id, e.g. "ex3-90" (pokemon) or "neo-136" (mtg)
    dir: Path
    set_id: str  # "ex3"
    game: GameId = games.DEFAULT

    def write_game(self, game: GameId) -> None:
        (self.dir / GAME_MARKER).write_text(
            game.value + "\n", encoding="utf-8", newline="\n"
        )
        self.game = game

    @property
    def name(self) -> str:
        _, _, tail = self.dir.name.partition("_")
        return tail.replace("-", " ")

    # -- what this printing is: layout, size, frame ---------------------------
    # Three facts the provider knows and the filesystem then remembers, so the
    # card page and `sheet` can act on them without another API call.
    def write_kind(
        self, layout: Layout, *, oversized: bool = False, frame: GuideId | None = None
    ) -> None:
        """Record the print kind, as the provider stated it at fetch time."""
        (self.dir / LAYOUT_MARKER).write_text(
            layout.value + "\n", encoding="utf-8", newline="\n"
        )
        flag = self.dir / OVERSIZED_MARKER
        if oversized:
            flag.touch()
        else:
            flag.unlink(missing_ok=True)
        marker = self.dir / FRAME_MARKER
        if frame is not None:
            marker.write_text(frame.value + "\n", encoding="utf-8", newline="\n")
        else:
            marker.unlink(missing_ok=True)

    @property
    def layout(self) -> Layout:
        """What this card puts on paper. Falls back to the number of faces on
        disk, so a library filed before layouts were recorded still reads right."""
        marker = self.dir / LAYOUT_MARKER
        if marker.is_file():
            found = games.parse_layout(marker.read_text(encoding="utf-8"))
            if found is not None:
                return found
        return Layout.DOUBLE if len(self.faces) > 1 else Layout.SINGLE

    @property
    def oversized(self) -> bool:
        return (self.dir / OVERSIZED_MARKER).exists()

    @property
    def frame(self) -> GuideId | None:
        """The frame guide this card overrides its set's era with, if any."""
        marker = self.dir / FRAME_MARKER
        if not marker.is_file():
            return None
        try:
            return GuideId(marker.read_text(encoding="utf-8").strip().lower())
        except ValueError:
            return None

    # -- faces ---------------------------------------------------------------
    def write_faces(self, names: Sequence[str]) -> None:
        """Record the card's faces, front first — what the provider called them.

        Written at fetch time so proxdex knows a second face *exists* before its
        image has been downloaded; a single-faced card writes nothing.
        """
        if len(names) > 1:
            (self.dir / FACES_MARKER).write_text(
                "\n".join(names) + "\n", encoding="utf-8", newline="\n"
            )

    def face_names(self) -> tuple[str, ...]:
        """One name per face, front first. Always at least one entry."""
        marker = self.dir / FACES_MARKER
        if marker.is_file():
            names = [
                ln.strip()
                for ln in marker.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            if names:
                return tuple(names)
        # no marker: fall back to whatever face files are actually on disk, so a
        # hand-placed `_f2` image still shows up as a second face
        return tuple("" for _ in range(self._faces_on_disk()))

    @property
    def faces(self) -> tuple[int, ...]:
        return tuple(range(len(self.face_names())))

    def _faces_on_disk(self) -> int:
        highest = FRONT
        for path in self.dir.glob(f"{self.id}_*.png"):
            found = parse_stage_file(path.stem)
            if found is not None:
                highest = max(highest, found.face)
        return highest + 1

    @property
    def front_face(self) -> int:
        """The face imposed on the *front* of a sheet — flippable per card, so
        you can print the reverse of a transform card instead of its front."""
        marker = self.dir / FRONT_MARKER
        if marker.is_file():
            text = marker.read_text(encoding="utf-8").strip()
            if text.isdigit() and int(text) in self.faces:
                return int(text)
        return FRONT

    def set_front_face(self, face: int) -> None:
        marker = self.dir / FRONT_MARKER
        if face == FRONT:
            marker.unlink(missing_ok=True)
        else:
            marker.write_text(f"{face}\n", encoding="utf-8", newline="\n")

    @property
    def back_face(self) -> int | None:
        """The face printed on the reverse in a duplex sheet: the one that isn't
        the front. ``None`` for a single-faced card — it takes the game's back."""
        others = [f for f in self.faces if f != self.front_face]
        return others[0] if others else None

    # -- per-face, per-step state: pending → done | skipped -------------------
    def stage_path(self, stage: Stage, face: int = FRONT) -> Path:
        return (
            self.dir / f"{self.id}_{stage.value}_{stage.label}{face_suffix(face)}.png"
        )

    def has(self, stage: Stage, face: int = FRONT) -> bool:
        return self.stage_path(stage, face).exists()

    def best(self, *prefer: Stage, face: int = FRONT) -> Path | None:
        """Highest-priority stage image that exists (first match wins)."""
        for stage in prefer:
            if self.has(stage, face):
                return self.stage_path(stage, face)
        return None

    def skip_marker(self, stage: Stage, face: int = FRONT) -> Path:
        return self.dir / f".skip-{stage.label}{face_suffix(face)}"

    def skipped(self, stage: Stage, face: int = FRONT) -> bool:
        return self.skip_marker(stage, face).exists()

    def status(self, stage: Stage, face: int = FRONT) -> Status:
        if self.has(stage, face):
            return Status.DONE
        if self.skipped(stage, face):
            return Status.SKIPPED
        return Status.PENDING

    def rollup(self, stage: Stage) -> Status:
        """One status for the whole card: done only when every face is settled.

        The contact sheet shows a card, not a face, so a card whose front is
        graded and whose back is not must not read as finished.
        """
        states = [self.status(stage, f) for f in self.faces]
        if all(s is Status.DONE for s in states):
            return Status.DONE
        if all(s is not Status.PENDING for s in states):
            return Status.SKIPPED
        return Status.PENDING

    def mark_skip(self, stage: Stage, face: int = FRONT) -> None:
        """Bypass this step: drop any output and record the skip."""
        self.stage_path(stage, face).unlink(missing_ok=True)
        self.skip_marker(stage, face).touch()

    def clear_skip(self, stage: Stage, face: int = FRONT) -> None:
        self.skip_marker(stage, face).unlink(missing_ok=True)

    def reset(self, stage: Stage, face: int = FRONT) -> None:
        """Back to pending: remove the output and any skip marker."""
        self.stage_path(stage, face).unlink(missing_ok=True)
        self.clear_skip(stage, face)

    def invalidate_downstream(self, stage: Stage, face: int = FRONT) -> list[Stage]:
        """Remove every output derived from ``stage`` (all later stages of this
        face), which went stale when ``stage`` changed. Skip markers — which
        record intent, not derived pixels — are left in place. Returns the
        stages removed."""
        removed = [s for s in Stage if s > stage and self.has(s, face)]
        for s in removed:
            self.stage_path(s, face).unlink(missing_ok=True)
        return removed


@dataclass(slots=True)
class Library:
    """A proxdex library rooted at a directory containing ``proxdex.toml``."""

    root: Path
    _game: GameId | None = field(default=None, repr=False)

    @property
    def default_game(self) -> GameId:
        """The game an unmarked folder belongs to — ``[library] game``."""
        if self._game is None:
            self._game = Config.load(self.root).library_game
        return self._game

    @classmethod
    def discover(
        cls, start: Path | None = None, explicit: Path | None = None
    ) -> Library:
        if explicit is not None:
            # a quoted "~/…" reaches us unexpanded (the shell only expands it
            # bare), and a literal ~ dir is never what anyone means
            root = explicit.expanduser().resolve()
            if not (root / MARKER).exists():
                raise LibraryError(f"{root} has no {MARKER} — run `proxdex init` there")
            return cls(root)
        current = (start or Path.cwd()).resolve()
        for candidate in (current, *current.parents):
            if (candidate / MARKER).exists():
                return cls(candidate)
        # fall back to a default library set for a global install
        if env := os.environ.get(ENV_ROOT):
            root = Path(env).expanduser().resolve()
            if (root / MARKER).exists():
                return cls(root)
            raise LibraryError(f"{ENV_ROOT}={env} has no {MARKER}")
        raise LibraryError(
            f"no proxdex library here or in any parent (looking for {MARKER}).\n"
            f"run `proxdex init` here, pass `--root PATH` (before or after the "
            f"command), or set {ENV_ROOT}=PATH."
        )

    @property
    def cards_dir(self) -> Path:
        return self.root / "cards"

    @property
    def batches_dir(self) -> Path:
        return self.root / "print-batches"

    def cards(self) -> list[Card]:
        return [self._card(d) for d in sorted(self.cards_dir.glob("*/*")) if d.is_dir()]

    def find(self, cid: str) -> Card | None:
        for d in sorted(self.cards_dir.glob(f"*/{cid}_*")):
            if d.is_dir():
                return self._card(d)
        return None

    def set_dir(self, set_id: str, set_name: str, game: GameId = games.DEFAULT) -> Path:
        """The folder for a set, created if needed.

        Set codes are only unique *within* a game (MTG's ``neo`` is not
        Pokémon's ``neo1``), so a folder is only reused when it belongs to the
        same game; otherwise the game is appended to keep the two apart.
        """
        for d in sorted(self.cards_dir.glob(f"{set_id}-*")):
            if d.is_dir() and read_game(d, self.default_game) is game:
                return d
        d = self.cards_dir / f"{set_id}-{slugify(set_name)}"
        if d.exists() and read_game(d, self.default_game) is not game:
            d = self.cards_dir / f"{set_id}-{slugify(set_name)}-{game.value}"
        d.mkdir(parents=True, exist_ok=True)
        (d / GAME_MARKER).write_text(game.value + "\n", encoding="utf-8", newline="\n")
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

    def _card(self, d: Path) -> Card:
        return Card(
            id=d.name.split("_", 1)[0],
            dir=d,
            set_id=d.parent.name.split("-", 1)[0],
            game=read_game(d, self.default_game),
        )
