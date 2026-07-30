"""Print profiles: one per medium you actually print on.

A profile is everything proxdex needs to know about "matte 200g on the XP-15000
with colour management off": a name, **your notes**, and how it corrects — either
four numbers you set by hand or the calibration rounds measured on it. One library
holds as many as you like, and `[print] profile` names the one a sheet uses by
default.

Nothing ships pre-filled. There is one built-in name, ``none``, and it is the
identity: no correction at all. Every real profile is one you made, because a
medium is a thing you own and nobody else's numbers describe it.

Why a file per profile rather than settings in ``proxdex.toml``:

* a medium is a *thing you own*, not a preference — it wants a name and notes
  ("Canon matte 200g, plain-paper setting, no colour management") because six
  months later the notes are the only way to reproduce the print;
* its correction is measured data (a polynomial and every patch of every round),
  which does not belong in a hand-edited config file; and
* two media coexist — you print the same deck on paper and on foil — so one
  active set of numbers was always the wrong shape.

Rounds are never deleted, only switched off, and the correction is refitted from
the live ones on every read — so nothing is stored that cannot be rederived from
the measurements, and turning a round back on restores exactly what it was doing.
A profile file is a record of what happened, not a cache.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from proxdex import calibrate
from proxdex.calibrate import GRID, Coef, Correction, Error, Patches, Slot
from proxdex.config import Config
from proxdex.errors import ProxdexError
from proxdex.media import RECIPE_KEYS, Recipe

#: where profiles live inside a library
DIR = "profiles"
#: the one built-in name: the identity, correcting nothing. Reserved, so a real
#: profile can never be called it and quietly shadow "leave my cards alone".
NONE = "none"
#: a grid is exactly (cols, rows)
_PAIR = 2
#: how many rounds in a row have to stop buying anything before the loop is called
#: done. One round can come back worse for reasons that are not the loop's — a
#: slightly crooked scan, a sheet fed warm — so a single flat round is noise and
#: three in a row is a floor.
_FLAT_ROUNDS = 3
#: mean RGB per round below which another sheet is not worth printing. This is a
#: judgement about your paper and your afternoon, not a measurement error: read
#: noise barely shows in a mean over ~70 patches (one level of noise per patch moves
#: the scored mean by 0.1), so a round can be *measured* to have gained 0.3 and still
#: not be worth having gained it.
_FLAT_GAIN = 0.5


@dataclass(frozen=True, slots=True)
class Plateau:
    """A run of rounds at the end of a calibration that improved nothing."""

    first: int
    last: int
    #: mean RGB the best round in the run won over everything before it — below
    #: what :data:`_FLAT_GAIN` asks of that many rounds, and negative if the run
    #: came back worse than what it followed
    gain: float

    @property
    def rounds(self) -> int:
        return self.last - self.first + 1

    @property
    def per_round(self) -> float:
        return max(self.gain, 0.0) / self.rounds

    def json(self) -> dict[str, Any]:
        return {
            "first": self.first,
            "last": self.last,
            "gain": self.gain,
            "per_round": self.per_round,
        }

    @property
    def text(self) -> str:
        span = (
            f"round {self.first}"
            if self.first == self.last
            else f"rounds {self.first} to {self.last}"
        )
        return (
            f"{span} improved the fit by {max(self.gain, 0.0):.1f} RGB in total, "
            f"{self.per_round:.1f} a round"
        )


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
    #: whether this round feeds the fit. A round is never deleted — a bad one is
    #: switched off, so you can see what it was doing and put it back.
    enabled: bool = True

    def switched(self, *, on: bool) -> Round:
        return replace(self, enabled=on)

    def json(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "slot": self.slot.json(),
            "date": self.date,
            "scan": self.scan,
            "note": self.note,
            "enabled": self.enabled,
            "sent": _rows(self.sent),
            "scanned": _rows(self.scanned),
        }

    @classmethod
    def read(cls, data: object, n: int) -> Round | None:
        """One stored round, or None if its measurements cannot be trusted."""
        if not isinstance(data, dict):
            return None
        raw: dict[str, Any] = data
        sent = _read_patches(raw.get("sent"))
        scanned = _read_patches(raw.get("scanned"))
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
            enabled=raw.get("enabled") is not False,
        )


@dataclass(slots=True)
class Profile:
    """A named medium: its notes, and how it corrects — by hand, or measured."""

    name: str
    notes: str = ""
    recipe: Recipe = field(default_factory=Recipe)
    grid: tuple[int, int] = GRID
    rounds: list[Round] = field(default_factory=list)
    #: the slot the last emitted chart was rendered into, so reading its scan
    #: back needs no arguments
    pending: Slot | None = None
    #: False for the built-in identity, which is not a file
    stored: bool = False
    #: rounds in the file that could not be read back — a damaged entry, or one
    #: measured against a chart of a different size. Counted rather than dropped
    #: in silence: a calibration quietly losing half its evidence would leave the
    #: error trend lying about what it is made of.
    unreadable: int = 0

    # ---------------------------------------------------------------- state --
    @property
    def calibrated(self) -> bool:
        return bool(self.live)

    @property
    def how(self) -> str:
        """How this profile corrects, in one word — and it is one of three.

        ``measured`` beats ``by hand`` beats ``identity``: a scan is evidence, four
        numbers are a judgement, and neither is nothing.
        """
        if self.live:
            return "measured"
        return "by hand" if not self.recipe.neutral else "identity"

    @property
    def live(self) -> list[Round]:
        """The rounds that feed the fit. A switched-off round stays in the file."""
        return [r for r in self.rounds if r.enabled]

    @property
    def correction(self) -> Correction | None:
        """The measured correction, fitted over every live round at once.

        None means nothing usable has been measured, and the recipe is all there is.
        """
        return self._fit(self.live)

    def _fit(self, rounds: Sequence[Round]) -> Correction | None:
        if not rounds:
            return None
        scanned = np.concatenate([r.scanned for r in rounds])
        sent = np.concatenate([r.sent for r in rounds])
        return calibrate.fit(scanned, sent)

    def influence(self, n: int) -> float | None:
        """How much round ``n`` moves the correction — its weight in the answer.

        Refit without it and measure how differently the result maps the target,
        in mean RGB. That is the "with and without" a switch is for: a round
        pulling far harder than its neighbours is either the most informative
        measurement you have or an outlier, and either way you want to know.
        """
        rnd = self.round(n)
        if rnd is None or not rnd.enabled:
            return None
        with_it = self.correction
        without = self._fit([r for r in self.live if r.n != n])
        if with_it is None:
            return None
        goal = calibrate.target()
        base = with_it.apply(goal)
        other = goal if without is None else without.apply(goal)
        return float(np.sqrt(((base - other) ** 2).sum(axis=1)).mean())

    @property
    def gamut(self) -> NDArray[np.bool_]:
        """Which target patches this medium can print — one answer for the profile.

        A gamut belongs to the paper and the inks, not to one sheet, so it is read
        from every live round pooled (the same fit the correction uses). Scoring
        each round against its own scan instead made the trend compare means over
        different patch sets — 63 to 68 of 80 on a real matte — so the number moved
        when the set moved rather than when the print got better.
        """
        return calibrate.reachable(self.correction)

    def score(self, rnd: Round) -> Error:
        """How far that round's print landed from the target, over this gamut."""
        return calibrate.score(rnd.scanned, self.gamut)

    @property
    def residual(self) -> Error | None:
        """How true the most recent live round printed — the number to watch fall."""
        live = self.live
        return self.score(live[-1]) if live else None

    @property
    def plateau(self) -> Plateau | None:
        """The tail of rounds that stopped buying anything, if there is one.

        A loop that exists to be repeated has to say when repeating it is done.
        Past this point another chart costs a sheet of your paper and an hour and
        buys a fraction of a level: what is left is the medium's own gamut, and no
        amount of measuring puts ink in the printer that is not there.

        Judged on the *best* round either side rather than the last one, because a
        single round coming back worse is ordinary and should not read as progress
        having stopped, nor a single good one as progress continuing.
        """
        live = self.live
        if len(live) <= _FLAT_ROUNDS:
            return None
        head, tail = live[:-_FLAT_ROUNDS], live[-_FLAT_ROUNDS:]
        best_before = min(self.score(r).mean for r in head)
        best_after = min(self.score(r).mean for r in tail)
        gain = best_before - best_after
        if gain >= _FLAT_GAIN * len(tail):
            return None
        return Plateau(first=tail[0].n, last=tail[-1].n, gain=gain)

    @property
    def used_slots(self) -> tuple[Slot, ...]:
        """Every slot with ink on it — including a round that is switched off,
        because the paper does not care whether the fit uses it."""
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
        )
        self.rounds.append(rnd)
        self.pending = None
        return rnd

    def switch_round(self, n: int, *, on: bool) -> Round:
        """Include or exclude one round, keeping it and its number in the file.

        Nothing is ever deleted: a round you switch off can be switched back on,
        and its numbering never shifts under the round you were talking about.
        """
        rnd = self.round(n)
        if rnd is None:
            raise ProxdexError(f"{self.name}: no round {n}")
        if rnd.enabled == on:
            state = "already in the fit" if on else "already switched off"
            raise ProxdexError(f"{self.name}: round {n} is {state}")
        self.rounds = [r.switched(on=on) if r.n == n else r for r in self.rounds]
        return self.rounds[[r.n for r in self.rounds].index(n)]

    # ------------------------------------------------------------ transform --
    def coef(self) -> Coef | None:
        c = self.correction
        return None if c is None else c.coef

    def json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "notes": self.notes,
            "recipe": self.recipe.json(),
            "grid": list(self.grid),
            "pending": None if self.pending is None else self.pending.json(),
            "rounds": [r.json() for r in self.rounds],
        }

    def summary(self) -> dict[str, Any]:
        """What a list or a settings screen shows — no patch arrays."""
        residual = self.residual
        plateau = self.plateau
        return {
            "name": self.name,
            "notes": self.notes,
            "how": self.how,
            "recipe": self.recipe.json(),
            "rounds": len(self.rounds),
            "live": len(self.live),
            "calibrated": self.calibrated,
            "residual": None if residual is None else residual.json(),
            "plateau": None if plateau is None else plateau.json(),
            "next_slot": self.next_slot.json(),
            "grid": list(self.grid),
            "stored": self.stored,
            "identity": self.name == NONE,
            "recipe_keys": list(RECIPE_KEYS),
            "unreadable": self.unreadable,
            "patches": len(calibrate.chart()),
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
                "error": self.score(r).json(),
                "enabled": r.enabled,
                "influence": self.influence(r.n),
                "target": _rows(calibrate.target()),
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
    dst.write_text(
        json.dumps(profile.json(), indent=2) + "\n", encoding="utf-8", newline="\n"
    )
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
        data = json.loads(path.read_text(encoding="utf-8"))
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
        notes=_text(raw.get("notes")),
        recipe=Recipe.read(raw.get("recipe")),
        grid=_grid(raw.get("grid")),
        rounds=rounds,
        pending=None if pending is None else Slot.read(pending),
        stored=True,
        unreadable=unreadable,
    )


def names(root: Path) -> list[str]:
    """Every stored profile, plus the identity, without duplicates."""
    stored = sorted(p.stem for p in profiles_dir(root).glob("*.json"))
    return stored + ([NONE] if NONE not in stored else [])


def listing(root: Path) -> list[Profile]:
    """Every profile a sheet could use — the ones you made, then the identity."""
    out: list[Profile] = []
    for name in names(root):
        profile = resolve(root, name)
        out.append(profile)
    return out


def resolve(root: Path, name: str) -> Profile:
    """The profile called ``name``, or the identity.

    ``none`` is the only name that resolves without a file, and it corrects
    nothing. Everything else has to have been made, because proxdex has no numbers
    of its own to offer for your paper.
    """
    stored = read(root, name)
    if stored is not None:
        return stored
    if slug(name) == NONE:
        return Profile(name=NONE, notes="", recipe=Recipe())
    raise ProxdexError(
        f"no print profile named '{name}' — `proxdex profile list`, or "
        f"`proxdex profile new {name}`"
    )


def named(root: Path, name: str) -> str | None:
    """What ``name`` refers to, as :func:`listing` spells it — or None if nothing
    in this library answers to it.

    An empty name means ``none``, because that is what :func:`active` resolves it
    to. A name that is not a legal profile name at all is None rather than an
    error: this is asked in order to *report*, and a `[print] profile` somebody
    typed by hand can be anything.
    """
    try:
        want = slug(name or NONE)
    except ProxdexError:
        return None
    return want if want == NONE or exists(root, want) else None


class PrintSetting(StrEnum):
    """The two ``[print]`` keys that name a profile."""

    PROFILE = "profile"
    BACK_PROFILE = "back_profile"

    @property
    def label(self) -> str:
        return f"[print] {self.value}"

    @property
    def prints(self) -> str:
        return "fronts" if self is PrintSetting.PROFILE else "backs"


@dataclass(frozen=True, slots=True)
class Dangling:
    """A ``[print]`` setting naming a profile that is not there."""

    setting: PrintSetting
    name: str

    @property
    def message(self) -> str:
        return (
            f"{self.setting.label} names '{self.name}', which is not a profile in "
            f"this library — every sheet run refuses until it is changed"
        )

    @property
    def hint(self) -> str:
        # deliberately not "`profile list`" — one of the two places this is
        # printed *is* that list, and it has just shown you the names
        return (
            f"`proxdex profile use <name>`, or `proxdex profile new {self.name}` "
            f"if that is the medium you meant"
        )

    def json(self) -> dict[str, Any]:
        return {
            "setting": self.setting.value,
            "name": self.name,
            "prints": self.setting.prints,
            "message": self.message,
            "hint": self.hint,
        }


def dangling(root: Path, cfg: Config) -> tuple[Dangling, ...]:
    """Every ``[print]`` profile setting that names nothing.

    A profile name in ``proxdex.toml`` outlives the profile: the real library
    carried ``[print] profile = "foil"`` from the deleted built-in presets, so
    every `sheet` run died with *no print profile named 'foil'* and nothing before
    that moment said so — not `where`, not `profile list`, which is the one place
    an absent marker was already the symptom. So it is asked here, once, by
    everything that draws a profile: it is the same broken reference `frames
    check` reports as :data:`specs.Fault.MISSING`.

    Only a *set* key can dangle. Unset means "the identity" for the fronts and
    "the same medium as the fronts" for the backs, and both are answers.
    """
    return tuple(
        Dangling(setting=setting, name=value)
        for setting, value in (
            (PrintSetting.PROFILE, cfg.print_profile),
            (PrintSetting.BACK_PROFILE, cfg.print_back_profile),
        )
        if value and named(root, value) is None
    )


def active(root: Path, cfg: Config, override: str | None = None) -> Profile:
    """The profile card *fronts* print through: the flag, else ``[print] profile``."""
    return resolve(root, override or cfg.print_profile or NONE)


def active_back(
    root: Path,
    cfg: Config,
    override: str | None = None,
    front: Profile | None = None,
) -> Profile:
    """The profile card *backs* print through.

    Unset means "the same medium as the fronts", which is the ordinary case — a
    duplex sheet is one piece of paper. It is worth a setting because it is not
    *always* one medium: the reverse of a one-sided glossy stock is a different
    surface, and a backs-only run often goes on different paper entirely.
    """
    name = override or cfg.print_back_profile
    if not name:
        return front if front is not None else active(root, cfg)
    return resolve(root, name)


def create(root: Path, name: str, *, notes: str = "") -> Profile:
    """A new profile at identity: it changes nothing until you say what it does.

    From here there are two honest routes — measure it with the chart loop, or set
    the four numbers by hand and judge them off a test print.
    """
    if slug(name) == NONE:
        raise ProxdexError(
            f"'{NONE}' is reserved for no correction at all — name the medium you "
            "are actually printing on"
        )
    if exists(root, name):
        raise ProxdexError(f"a profile named '{slug(name)}' already exists")
    profile = Profile(name=slug(name), notes=notes, recipe=Recipe())
    save(root, profile)
    return profile


def _rows(arr: Patches) -> list[list[float]]:
    return [[round(float(v), 3) for v in row] for row in arr]


def _read_patches(data: object) -> Patches | None:
    if not isinstance(data, list) or not data:
        return None
    arr = np.asarray(data, dtype=np.float32)
    want = (len(calibrate.chart()), 3)
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
