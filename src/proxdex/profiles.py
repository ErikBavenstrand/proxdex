"""Print profiles: one per medium you actually print on.

A profile is everything proxdex needs to know about "matte 200g on the XP-15000
with colour management off": a name, **your notes**, the starting-point recipe it
began from, and the calibration rounds measured on it. One library holds as many
as you like, and `[print] profile` names the one a sheet uses by default.

Why a file per profile rather than settings in ``proxdex.toml``:

* a medium is a *thing you own*, not a preference — it wants a name and notes
  ("Canon matte 200g, plain-paper setting, no colour management") because six
  months later the notes are the only way to reproduce the print;
* its correction is measured data (a polynomial and every patch of every round),
  which does not belong in a hand-edited config file; and
* two media coexist — you print the same deck on paper and on foil — so one
  active set of numbers was always the wrong shape.

The correction is refitted from **all** rounds every time one is added or dropped,
so nothing is stored that cannot be rederived from the measurements; a profile
file is a record of what happened, not a cache.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from proxdex import calibrate
from proxdex.calibrate import GRID, Coef, Correction, Error, Patches, Slot
from proxdex.config import Config
from proxdex.errors import ProxdexError
from proxdex.media import Preset, Recipe, preset

#: where profiles live inside a library
DIR = "profiles"
#: a grid is exactly (cols, rows)
_PAIR = 2
#: the pre-0.5 single-shot calibrations, read once so nobody loses a measurement
LEGACY_DIR = "calibration"
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,47}$")


def slug(name: str) -> str:
    """A profile name as a filename — lowercase, dashes, nothing surprising."""
    clean = re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower()).strip("-.")
    if not clean or not _NAME_RE.match(clean):
        raise ProxdexError(
            f"profile name {name!r}: use letters, digits, dashes or dots "
            "(up to 48 characters)"
        )
    return clean


@dataclass(frozen=True, slots=True)
class Round:
    """One print-and-scan iteration: what was sent, and what came back.

    Both halves are kept because the fit needs the pair, and because they are the
    evidence — a round that went wrong can be read, judged and dropped instead of
    quietly poisoning a correction nobody can inspect.
    """

    n: int
    slot: Slot
    sent: Patches
    scanned: Patches
    date: str = ""
    scan: str = ""
    note: str = ""
    #: which chart version this round was printed from. A round is only comparable
    #: to its own target, but its (scanned, sent) pairs stay valid for the fit
    #: whatever chart produced them — so an old round is never wasted.
    chart: int = 1

    @property
    def error(self) -> Error:
        """How far this print landed from the target — the convergence measure."""
        return calibrate.error(self.scanned, calibrate.target(self.chart))

    def json(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "slot": self.slot.json(),
            "date": self.date,
            "scan": self.scan,
            "note": self.note,
            "chart": self.chart,
            "sent": _rows(self.sent),
            "scanned": _rows(self.scanned),
            "error": self.error.json(),
        }

    @classmethod
    def read(cls, data: object, n: int) -> Round | None:
        """One stored round, or None if it cannot be trusted.

        A file written before charts were versioned has no ``chart`` key and was
        measured on version 1, which is why that is the default rather than the
        current version.
        """
        if not isinstance(data, dict):
            return None
        raw: dict[str, Any] = data
        version = raw.get("chart")
        version = version if isinstance(version, int) else 1
        if version not in calibrate.CHARTS:
            return None
        sent = _patches(raw.get("sent"), version)
        scanned = _patches(raw.get("scanned"), version)
        if sent is None or scanned is None:
            return None
        return cls(
            n=int(raw.get("n", n)) if isinstance(raw.get("n"), int) else n,
            slot=Slot.read(raw.get("slot")),
            sent=sent,
            scanned=scanned,
            date=_text(raw.get("date")),
            scan=_text(raw.get("scan")),
            note=_text(raw.get("note")),
            chart=version,
        )


@dataclass(slots=True)
class Profile:
    """A named medium: its notes, its recipe, and its calibration history."""

    name: str
    medium: Preset = Preset.NONE
    notes: str = ""
    recipe: Recipe = field(default_factory=Recipe)
    grid: tuple[int, int] = GRID
    rounds: list[Round] = field(default_factory=list)
    #: the slot the last emitted chart was rendered into, so reading its scan
    #: back needs no arguments
    pending: Slot | None = None
    #: a correction inherited from a pre-0.5 calibration, with no rounds behind
    #: it. Used until the first real round replaces it.
    inherited: Correction | None = None
    #: False for a built-in preset that has never been saved
    stored: bool = False
    #: rounds in the file that could not be read back — a damaged entry, or one
    #: measured against a chart of a different size. Counted rather than dropped
    #: in silence: a calibration quietly losing half its evidence would leave the
    #: error trend lying about what it is made of.
    unreadable: int = 0

    # ---------------------------------------------------------------- state --
    @property
    def calibrated(self) -> bool:
        return bool(self.rounds) or self.inherited is not None

    @property
    def correction(self) -> Correction | None:
        """The measured correction, fitted over every round at once.

        None means nothing has been measured, and the recipe is all there is.
        """
        if not self.rounds:
            return self.inherited
        scanned = np.concatenate([r.scanned for r in self.rounds])
        sent = np.concatenate([r.sent for r in self.rounds])
        return calibrate.fit(scanned, sent)

    @property
    def residual(self) -> Error | None:
        """How true the most recent round printed — the number to watch fall."""
        return self.rounds[-1].error if self.rounds else None

    @property
    def used_slots(self) -> tuple[Slot, ...]:
        return tuple(r.slot for r in self.rounds)

    @property
    def free_slots(self) -> tuple[Slot, ...]:
        used = {(s.col, s.row) for s in self.used_slots}
        every = calibrate.slots(self.grid)
        return tuple(s for s in every if (s.col, s.row) not in used)

    @property
    def next_slot(self) -> Slot:
        """Where the next chart should print — the first slot still blank.

        When the sheet is full it wraps to the first slot, which is correct: you
        are starting a fresh sheet, and the round numbers say which is which.
        """
        free = self.free_slots
        return free[0] if free else Slot(0, 0)

    @property
    def sheet_full(self) -> bool:
        return not self.free_slots

    @property
    def charts(self) -> tuple[int, ...]:
        """Which chart versions this profile's rounds were measured on.

        More than one is fine for the *fit* — the pairs are all real measurements
        — but the per-round errors then come from different patch sets, so the
        trend is not strictly like-for-like and says so.
        """
        return tuple(sorted({r.chart for r in self.rounds}))

    def round(self, n: int) -> Round | None:
        return next((r for r in self.rounds if r.n == n), None)

    # ------------------------------------------------------------- the loop --
    def chart_label(self, slot: Slot | None = None) -> str:
        where = self.next_slot if slot is None else slot
        return f"{self.name}  ·  round {len(self.rounds) + 1}  ·  slot {where.text}"

    def add_round(
        self,
        scanned: Patches,
        sent: Patches,
        slot: Slot,
        *,
        scan: str = "",
        note: str = "",
    ) -> Round:
        """Record a measured round. The correction refits from all of them."""
        rnd = Round(
            n=len(self.rounds) + 1,
            slot=slot,
            sent=sent,
            scanned=scanned,
            date=date.today().isoformat(),
            scan=scan,
            note=note,
            chart=calibrate.CHART_VERSION,
        )
        self.rounds.append(rnd)
        self.pending = None
        # a real measurement supersedes an inherited correction, which had no
        # samples anyone can see
        self.inherited = None
        return rnd

    def drop_round(self, n: int) -> Round:
        """Remove one round and renumber the rest, so the history stays 1..N."""
        rnd = self.round(n)
        if rnd is None:
            raise ProxdexError(f"{self.name}: no round {n}")
        self.rounds = [
            Round(
                n=i + 1,
                slot=r.slot,
                sent=r.sent,
                scanned=r.scanned,
                date=r.date,
                scan=r.scan,
                note=r.note,
                chart=r.chart,
            )
            for i, r in enumerate(self.rounds)
            if r.n != n
        ]
        return rnd

    # ------------------------------------------------------------ transform --
    def coef(self) -> Coef | None:
        c = self.correction
        return None if c is None else c.coef

    def json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "medium": self.medium.value,
            "notes": self.notes,
            "recipe": self.recipe.json(),
            "grid": list(self.grid),
            "pending": None if self.pending is None else self.pending.json(),
            # a correction with no rounds behind it still has to survive a save,
            # or adopting a pre-0.5 calibration would quietly discard it
            "inherited": None if self.inherited is None else self.inherited.json(),
            "rounds": [r.json() for r in self.rounds],
        }

    def summary(self) -> dict[str, Any]:
        """What a list or a settings screen shows — no patch arrays."""
        residual = self.residual
        return {
            "name": self.name,
            "medium": self.medium.value,
            "medium_label": self.medium.label,
            "notes": self.notes,
            "recipe": self.recipe.json(),
            "rounds": len(self.rounds),
            "calibrated": self.calibrated,
            "inherited": self.inherited is not None,
            "residual": None if residual is None else residual.json(),
            "next_slot": self.next_slot.json(),
            "grid": list(self.grid),
            "stored": self.stored,
            "preset": preset(self.name) is not None,
            "unreadable": self.unreadable,
            "patches": len(calibrate.chart()),
            "chart": calibrate.CHART_VERSION,
            "charts": list(self.charts),
        }

    def detail(self) -> dict[str, Any]:
        """The whole profile for the print screen: history, and every patch pair."""
        out = self.summary()
        out["rounds_detail"] = [
            {
                "n": r.n,
                "slot": r.slot.json(),
                "slot_text": r.slot.text,
                "date": r.date,
                "scan": r.scan,
                "note": r.note,
                "error": r.error.json(),
                "chart": r.chart,
                "target": _rows(calibrate.target(r.chart)),
                "sent": _rows(r.sent),
                "scanned": _rows(r.scanned),
            }
            for r in self.rounds
        ]
        out["free_slots"] = [s.json() for s in self.free_slots]
        out["sheet_full"] = self.sheet_full
        out["pending"] = None if self.pending is None else self.pending.json()
        return out


# ------------------------------------------------------------------ storage ----
def profiles_dir(root: Path) -> Path:
    return root / DIR


def path_for(root: Path, name: str) -> Path:
    return profiles_dir(root) / f"{slug(name)}.json"


def exists(root: Path, name: str) -> bool:
    return path_for(root, name).exists()


def save(root: Path, profile: Profile) -> Path:
    dst = path_for(root, profile.name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(profile.json(), indent=2) + "\n")
    profile.stored = True
    return dst


def delete(root: Path, name: str) -> None:
    path = path_for(root, name)
    if not path.exists():
        raise ProxdexError(f"no profile named '{name}'")
    path.unlink()


def rename(root: Path, old: str, new: str) -> Profile:
    profile = read(root, old)
    if profile is None:
        raise ProxdexError(f"no profile named '{old}'")
    if exists(root, new):
        raise ProxdexError(f"a profile named '{slug(new)}' already exists")
    profile.name = slug(new)
    save(root, profile)
    path_for(root, old).unlink(missing_ok=True)
    return profile


def read(root: Path, name: str) -> Profile | None:
    """The stored profile, or None. Never raises on a damaged file's contents."""
    path = path_for(root, name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProxdexError(f"{path.name} is not readable: {exc}") from exc
    if not isinstance(data, dict):
        raise ProxdexError(f"{path.name} is not a profile")
    raw: dict[str, Any] = data
    rounds: list[Round] = []
    unreadable = 0
    for i, item in enumerate(raw.get("rounds") or []):
        rnd = Round.read(item, i + 1)
        if rnd is None:
            unreadable += 1
        else:
            rounds.append(rnd)
    pending = raw.get("pending")
    return Profile(
        name=slug(_text(raw.get("name")) or path.stem),
        medium=preset(_text(raw.get("medium"))) or Preset.NONE,
        notes=_text(raw.get("notes")),
        recipe=Recipe.read(raw.get("recipe")),
        grid=_grid(raw.get("grid")),
        rounds=rounds,
        pending=None if pending is None else Slot.read(pending),
        inherited=Correction.read(raw.get("inherited")),
        stored=True,
        unreadable=unreadable,
    )


def names(root: Path) -> list[str]:
    """Every stored profile, plus the built-in presets, without duplicates."""
    stored = sorted(p.stem for p in profiles_dir(root).glob("*.json"))
    return stored + [p.value for p in Preset if p.value not in stored]


def listing(root: Path) -> list[Profile]:
    """Every profile a sheet could use — stored ones first, then unsaved presets."""
    out: list[Profile] = []
    for name in names(root):
        profile = resolve(root, name)
        out.append(profile)
    return out


def resolve(root: Path, name: str) -> Profile:
    """The profile called ``name``: stored, or synthesized from a preset.

    A preset that has never been saved comes back with ``stored=False``, so the
    caller can offer to keep it rather than pretending it is already a profile.
    """
    stored = read(root, name)
    if stored is not None:
        return stored
    kind = preset(name)
    legacy = _legacy(root, name)
    if legacy is not None:
        # somebody printed and scanned for this; adopt it as a real profile rather
        # than leave it half-alive in a directory nothing reads any more
        return _adopt(root, name, kind, legacy)
    if kind is not None:
        return Profile(name=kind.value, medium=kind, notes="", recipe=kind.recipe)
    raise ProxdexError(
        f"no print profile named '{name}' — `proxdex profile list`, or "
        f"`proxdex profile new {name}`"
    )


def _adopt(
    root: Path, name: str, kind: Preset | None, correction: Correction
) -> Profile:
    """Turn a pre-0.5 ``calibration/<name>.json`` into a real profile, once.

    Its patch measurements were never stored, so it cannot join the round history
    — but a calibration somebody printed and scanned for is not something to throw
    away. It is carried as an *inherited* correction, used until the first real
    round replaces it, and the provenance goes in the notes where the user will
    read it.
    """
    profile = Profile(
        name=slug(name),
        medium=kind or Preset.NONE,
        notes=(
            f"Adopted from {LEGACY_DIR}/{name}.json (calibrated before 0.5).\n"
            "Its patch measurements were not kept, so it cannot be refined — "
            "`proxdex calibrate chart` starts a fresh loop, which supersedes it."
        ),
        recipe=(kind or Preset.NONE).recipe,
        inherited=correction,
    )
    save(root, profile)
    return profile


def active(root: Path, cfg: Config, override: str | None = None) -> Profile:
    """The profile this run prints through: the flag, else ``[print] profile``."""
    return resolve(root, override or cfg.print_profile or Preset.NONE.value)


def create(
    root: Path,
    name: str,
    *,
    medium: Preset = Preset.NONE,
    notes: str = "",
) -> Profile:
    if exists(root, name):
        raise ProxdexError(f"a profile named '{slug(name)}' already exists")
    profile = Profile(name=slug(name), medium=medium, notes=notes, recipe=medium.recipe)
    save(root, profile)
    return profile


def _legacy(root: Path, name: str) -> Correction | None:
    """A pre-0.5 ``calibration/<name>.json`` correction, if one is lying around.

    Its patch measurements were never stored, so it cannot join the round history
    — but throwing away a calibration somebody printed and scanned for would be
    rude, so it is carried as an inherited correction until a real round replaces
    it.
    """
    path = root / LEGACY_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return Correction.read(data.get("coef")) if isinstance(data, dict) else None


def _rows(arr: Patches) -> list[list[float]]:
    return [[round(float(v), 3) for v in row] for row in arr]


def _patches(data: object, version: int) -> Patches | None:
    if not isinstance(data, list) or not data:
        return None
    arr = np.asarray(data, dtype=np.float32)
    want = (len(calibrate.chart(version)), 3)
    if arr.shape != want or not np.isfinite(arr).all():
        return None
    return arr


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _grid(value: object) -> tuple[int, int]:
    if isinstance(value, list) and len(value) == 2:
        pair: list[object] = value
        if all(isinstance(v, int) and v >= 1 for v in pair):
            return (int(pair[0]), int(pair[1]))  # type: ignore[arg-type]
    return GRID
