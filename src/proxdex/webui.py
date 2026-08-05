"""Local web UI: a FastAPI app with full parity to the CLI.

Display + light queries (cards, search, frame, config) are computed in-process
from the library; mutating actions (fetch, border/upscale/grade, skip/reset, sheet,
back, import, printed, calibrate) shell out to the real ``proxdex`` CLI so the
UI and terminal share exactly one implementation. Served on localhost only.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import (
    Annotated,
    Any,
    Final,
    Literal,
    Self,
    get_args,
    get_origin,
    get_type_hints,
)

import requests
import tomlkit
from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from proxdex import (
    art,
    bleed,
    browse,
    calibrate,
    config,
    doctor,
    frames,
    games,
    imports,
    inventory,
    media,
    net,
    profiles,
    progress,
    report,
    scratch,
    sources,
    specs,
    steps,
)
from proxdex import sheet as sheet_mod
from proxdex.config import Config, Faces
from proxdex.errors import ConfigError, FileError, ProxdexError
from proxdex.library import FRONT, STAGE_BY_LABEL, Card, Library, Stage, Step

_STAGES = steps.STAGES
_BEST = steps.BEST
_HTML_PATH = Path(__file__).parent / "webui.html"
_STATIC_DIR = Path(__file__).parent / "static"
#: contact-sheet tile size, and the card-page proof — large enough that the
#: viewer never upsamples on a big display
_THUMB_BOX = (360, 504)
_VIEW_BOX = (1400, 1960)
#: encoded JPEGs by (path, mtime, box, quality) — bounded, newest kept
_JPEG_CACHE: dict[tuple[str, int, int, int], bytes] = {}
_JPEG_CACHE_MAX = 64
#: the client routes are real URLs, so a deep link has to reach the SPA shell
_SPA_ROUTES = (
    "library",
    "card",
    "search",
    "browse",
    "import",
    "settings",
    "sheet",
    "print",
    "frames",
)
# card ids are <set>-<number>, and MTG collector numbers can carry their own
# hyphen ("ymid-A-123"). No dots or slashes: these reach the CLI as argv.
_ID_OK = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+){1,2}$")


#: a playset is four; `--copies 400` is a typo, not a plan (matches the CLI)
_MAX_COPIES = 99

#: a card is printed on two sides at most, and both CLI and API number them
#: from 1 — so a side is `Annotated[int, Field(ge=1, le=_MAX_FACE)]` everywhere
_MAX_FACE = 2

#: one card id, validated by pattern rather than by a hand-written check: these
#: reach the CLI as argv, so nothing but `<set>-<number>` may pass
CardId = Annotated[str, Field(pattern=_ID_OK.pattern)]
#: a profile name — it reaches the CLI as argv and becomes a filename, so it is
#: pattern-checked here rather than sanitized later
ProfileName = Annotated[str, PathParam(pattern=r"^[a-z0-9][a-z0-9._-]{0,47}$")]
#: a frame spec id. Open where an enum used to be — a library measures its own
#: specs — so the *shape* is pinned here instead, because it reaches the CLI as
#: argv and becomes a filename. The same pattern `frames.valid_id` enforces.
SpecName = Annotated[
    str, PathParam(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=48)
]
#: a rule id, as `specs.Rule` numbers them
RuleName = Annotated[str, PathParam(pattern=r"^r\d{1,6}$")]
#: a game id. Open for the same reason `SpecName` is — a library defines its own
#: games — so what is pinned here is the *shape*, since it reaches the CLI as argv
#: and is written into a `.game` marker. Whether a game by that id exists is the
#: CLI's answer (`cli._game`), which is the one place that list is read, so the API
#: cannot come to a different verdict than the command it shells out to.
GameName = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=32)]
#: a set id of a custom game. Looser than a game id (it is half of a card id) and
#: pinned here for the same reason: it reaches the CLI as argv and names a folder.
SetName = Annotated[
    str, PathParam(pattern=r"^[a-z0-9]+(?:[-.][a-z0-9]+)*$", max_length=24)
]
Side = Annotated[int, Field(ge=1, le=_MAX_FACE)]
#: a stage by the name every other surface spells it with. :class:`Stage` is an
#: IntEnum, so the closed set of *labels* has to be written out for a request
#: boundary to refuse a typo — and checked against the enum right below, because
#: two lists of the same thing is exactly what this codebase does not keep.
StageLabel = Literal["original", "bordered", "upscaled", "edited"]
if set(get_args(StageLabel)) != set(STAGE_BY_LABEL):  # pragma: no cover
    raise RuntimeError("StageLabel has drifted from library.Stage")
#: a value a step setting may hold. Deliberately not `Any`: the settings schema
#: only ever declares booleans, numbers and closed sets of strings.
SettingValue = bool | int | float | str


class Body(BaseModel):
    """Base for every request body: unknown keys are refused, not ignored.

    A typo in the client should be a 422 naming the field, not a silently
    dropped option that makes a step run with the wrong settings.
    """

    model_config = ConfigDict(extra="forbid")


class Edges(Body):
    """Per-edge fractions of the image — the align marks, or a border to grow."""

    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)
    left: float = Field(ge=0, le=1)


class StepBody(Body):
    """Run, skip, unskip or reset one step over some cards.

    ``settings`` holds the focused step's own options and is checked against that
    step's declared schema (:mod:`proxdex.steps`) before anything reaches argv;
    ``step`` is only meaningful for the pipeline-state verbs.
    """

    cmd: str = Field(min_length=1, max_length=32)
    ids: list[CardId] = Field(min_length=1, max_length=512)
    face: Side | None = None
    force: bool = False
    step: Step | None = None
    settings: dict[str, SettingValue] = Field(default_factory=dict)
    #: where the card's inner border currently sits, for the spec-based fit
    inner: Edges | None = None
    #: plain per-edge growth in mm, for the no-fit path
    grow: Edges | None = None
    #: how the *added* border is filled — cardbleed's synthesis settings, validated
    #: against `bleed.KNOBS` before they become `--tune` flags. `None` means "leave
    #: whatever this card already has"; `{}` means "back to the defaults", which is a
    #: different request and has to be tellable from it.
    tune: dict[str, SettingValue] | None = None


class KnownCard(Body):
    """A card's description as the client already read it, so `fetch` need not ask.

    **Every field is bounded and pattern-checked**, because these reach the CLI as
    argv and become folder names on disk. Nothing here can be trusted to be *true* —
    a client could send any name for any id — so it is deliberately limited to what
    is cosmetic or already reported: the name and set name become folder names, and
    the traits feed frame rules that report themselves (a wrong rarity resolves to a
    spec the align panel names, and `frames check` lists it). What it cannot do is
    fake a *picture*: the image URL is derived by the server from the id, and
    :func:`proxdex.sources.download` refuses the image host's placeholder — so an id
    that does not exist fails rather than filing a grey card.
    """

    id: CardId
    name: str = Field(min_length=1, max_length=120)
    set_name: str = Field(default="", max_length=120)
    rarity: str = Field(default="", max_length=60)
    subtypes: str = Field(default="", max_length=120)


class FetchBody(Body):
    ids: list[CardId] = Field(min_length=1, max_length=512)
    game: GameName | None = None
    face: Side | None = None
    #: also fetch the cards these are printed alongside — both meld halves and
    #: the melded card, the tokens they make
    related: bool = False
    #: what the client already knows about these cards, from the search row it drew
    #: them from. **Pokémon only** — a Magic card's image URL is a uuid path only
    #: Scryfall's answer carries, so it has to be looked up. Ids not listed here are
    #: looked up as before.
    known: list[KnownCard] = Field(default_factory=list, max_length=512)


class FlipBody(Body):
    ids: list[CardId] = Field(min_length=1, max_length=512)
    face: Side | None = None


class SpecBody(Body):
    """One frame spec, as the frames screen states it.

    The four numbers are millimetres of a real card (63.5×88.9mm, both games), like
    the CLI's — a border is a physical width, and nobody measures a fraction with
    calipers. There is no confidence field: a spec is its numbers, and the grade that
    used to sit here called a border read off a publisher's scan trustworthy (see
    :mod:`proxdex.frames`).
    """

    id: SpecName
    name: str = Field(default="", max_length=80)
    game: GameName | None = None
    top: Annotated[float, Field(ge=0, le=20)]
    right: Annotated[float, Field(ge=0, le=20)]
    bottom: Annotated[float, Field(ge=0, le=20)]
    left: Annotated[float, Field(ge=0, le=20)]
    #: the millimetres are of an 89×127mm plane, scheme or Vanguard card
    oversized: bool = False

    def argv(self) -> list[str]:
        """This spec as CLI arguments — the one place the flags are spelled."""
        args = [
            "frames",
            "set",
            self.id,
            "--top",
            f"{self.top:g}",
            "--right",
            f"{self.right:g}",
            "--bottom",
            f"{self.bottom:g}",
            "--left",
            f"{self.left:g}",
        ]
        if self.name:
            args += ["--name", self.name]
        if self.game is not None:
            args += ["--game", self.game]
        if self.oversized:
            args.append("--oversized")
        return args


class RuleBody(Body):
    """One rule: which cards of which set take which spec."""

    spec: SpecName
    #: empty means every set of the game — the only way to express a frame
    #: treatment, which is not a property of any one set. Not `min_length=1`, and
    #: the pattern still has to reject anything that is not a set code, because
    #: this becomes argv.
    set: str = Field(default="", max_length=16, pattern=r"^[A-Za-z0-9]*$")
    match: specs.Match = specs.Match.SET
    value: str = Field(default="", max_length=200)
    game: GameName | None = None

    def argv(self) -> list[str]:
        args = [
            "frames",
            "assign",
            self.spec,
            "--match",
            self.match.value,
        ]
        if self.set:
            args += ["--set", self.set]
        if self.value:
            args += ["--value", self.value]
        if self.game is not None:
            args += ["--game", self.game]
        return args


class PinBody(Body):
    """Pin these cards to a spec, or (``spec = None``) drop their pins."""

    ids: list[CardId] = Field(min_length=1, max_length=512)
    spec: SpecName | None = None


class SheetCard(Body):
    """One card in a print run, and how many copies of it to print."""

    id: CardId
    copies: Annotated[int, Field(ge=1, le=_MAX_COPIES)] = 1

    @property
    def argv(self) -> str:
        """``ex3-90:4`` — the CLI's own spelling for copies."""
        return f"{self.id}:{self.copies}" if self.copies > 1 else self.id


class SheetBody(Body):
    """A print run. Every page setting is a per-run override, never a config edit.

    The same knobs `proxdex sheet` takes, because a run is this paper on this
    printer today — the sheet builder chooses them, the library keeps its own
    defaults.
    """

    name: str = Field(default="deck", min_length=1, max_length=64)
    #: which cards to impose, with copies. Empty means every card that is ready,
    #: exactly as `proxdex sheet <name>` with no ids does.
    cards: list[SheetCard] = Field(default_factory=list, max_length=512)
    #: every page setting, keyed by its **config field name** and checked against
    #: `Config.run_options(Run.SHEET)` — see :func:`_overrides_for`. One field rather
    #: than one per setting, because there were eight named ones and twenty settings a
    #: print run reads that no request could reach; a dict derived from the declaration
    #: cannot fall behind it. An absent key means "use the library's setting", which is
    #: the whole point: a run changes one number, it does not restate the config.
    overrides: dict[str, str | float | bool | None] = Field(
        default_factory=dict, max_length=64
    )
    profile: str | None = Field(default=None, max_length=64)
    #: card backs, when they land on a different medium than the fronts. Empty or
    #: absent means "the same profile", which is the ordinary case.
    back_profile: str | None = Field(default=None, max_length=64)
    notes: str = Field(default="", max_length=2000)

    def argv(self) -> list[str]:
        """This run as CLI arguments — the one place the flags are spelled.

        Spelled from `RunOption.flag`, so the browser cannot send an option the CLI
        does not have. That is not a theoretical worry: it is the bug the step panels
        already had, where a multi-word key was dashed in one place and not the other.
        """
        args = [c.argv for c in self.cards]
        if self.profile:
            args += ["--profile", self.profile]
        if self.back_profile:
            args += ["--back-profile", self.back_profile]
        for opt in Config.run_options(config.Run.SHEET):
            value = self.overrides.get(opt.key)
            if value is None or value == "":
                continue
            if opt.kind is config.OptKind.BOOL:
                args.append(f"--{opt.flag}" if value else f"--no-{opt.flag}")
            else:
                args += [f"--{opt.flag}", _flag_value(value)]
        if self.notes:
            args += ["--notes", self.notes]
        return args


class ImportItem(Body):
    """One file the wizard is holding, and what the user decided about it.

    ``name`` is a *filename*, never a path: the browser has no path to give (and
    would be talking about the wrong machine's filesystem if it did). Everything
    else overrides what that name implies, and an unset ``id`` means "whatever
    the name says", which is exactly the CLI's no-``--id`` case.
    """

    name: str = Field(min_length=1, max_length=255)
    id: CardId | None = None
    game: GameName | None = None
    stage: StageLabel | None = None
    face: Side | None = None
    #: what to call the card, for a game with no provider. Ignored for the built-in
    #: games, whose names come from the lookup that proves the id exists — so it is
    #: sent for every row and only *means* anything for a custom one.
    card_name: str = Field(default="", max_length=120)
    #: how many printed sides a card this run creates has (a custom game only)
    faces: Side = 1

    def item(self) -> imports.Item:
        return imports.Item(
            name=self.name,
            id=self.id,
            game=self.game,
            card_name=self.card_name,
            faces=self.faces,
            stage=STAGE_BY_LABEL[self.stage] if self.stage else None,
            face=self.face - 1 if self.face is not None else None,
        )


class ImportPlanBody(Body):
    """What a folder of files would do to the library — names only, no bytes.

    The plan is worked out from filenames, so the wizard can review two hundred
    files without uploading one: the thumbnails are the browser's own copies, and
    only the rows you keep are ever sent.
    """

    items: list[ImportItem] = Field(min_length=1, max_length=1024)
    on_existing: imports.OnExisting = imports.OnExisting.OVERWRITE


class ProfileBody(Body):
    """Create or edit a print profile. Every field is optional on an edit."""

    notes: str | None = Field(default=None, max_length=8000)
    saturation: Annotated[float, Field(ge=0, le=3)] | None = None
    contrast: Annotated[float, Field(ge=0, le=3)] | None = None
    brightness: Annotated[float, Field(ge=0, le=3)] | None = None
    gamma: Annotated[float, Field(ge=0, le=3)] | None = None
    #: how many charts one calibration sheet holds
    cols: Annotated[int, Field(ge=1, le=6)] | None = None
    rows: Annotated[int, Field(ge=1, le=6)] | None = None

    def argv(self) -> list[str]:
        args: list[str] = []
        if self.notes is not None:
            args += ["--notes", self.notes]
        for key in media.RECIPE_KEYS:
            value = getattr(self, key)
            if value is not None:
                args += [f"--{key}", f"{value:g}"]
        if self.cols is not None and self.rows is not None:
            args += ["--grid", f"{self.cols}x{self.rows}"]
        return args


class RenameBody(Body):
    name: str = Field(min_length=1, max_length=48)


class GameBody(Body):
    """Define or edit a game of your own. Every field is optional on an edit."""

    name: str | None = Field(default=None, min_length=1, max_length=60)
    id_example: str | None = Field(default=None, max_length=48)
    notes: str | None = Field(default=None, max_length=8000)

    def argv(self) -> list[str]:
        args: list[str] = []
        if self.name is not None:
            args += ["--name", self.name]
        if self.id_example is not None:
            args += ["--id-example", self.id_example]
        if self.notes is not None:
            args += ["--notes", self.notes]
        return args


class GameSetBody(Body):
    """One set of a custom game, as its owner declares it."""

    name: str = Field(min_length=1, max_length=60)
    total: Annotated[int, Field(ge=0, le=100_000)] = 0
    #: free text, and deliberately not a `date`: a set whose release nobody recorded
    #: keeps an empty string rather than acquiring a date pydantic invented
    released: str = Field(default="", max_length=32)

    def argv(self) -> list[str]:
        args = ["--name", self.name]
        if self.total:
            args += ["--total", str(self.total)]
        if self.released:
            args += ["--released", self.released]
        return args


class RoundBody(Body):
    """Whether a calibration round feeds the fit."""

    enabled: bool


class BackBody(Body):
    game: GameName | None = None
    url: str | None = Field(default=None, max_length=2048)


class DoctorBody(Body):
    """Which cards to repair. Empty means the whole library, as the CLI's does."""

    ids: list[CardId] = Field(default_factory=list, max_length=512)


class ConfigBody(Body):
    """A settings save: whole ``[section]`` tables of values.

    The keys are whatever ``proxdex.toml`` holds, so they cannot be enumerated in
    a model — but every value is coerced through :class:`Config`'s own annotations
    before being written, and a bad one is a 400 naming the valid options.
    """

    sections: dict[str, dict[str, SettingValue | list[SettingValue] | None]]


def _rev(card: Card) -> str:
    """A cheap version token for a card's pixels: which stages of which faces
    exist, and the newest one's mtime.

    Every image URL carries it, so a file that changed gets a *new* URL and the
    old one can be cached forever — no revalidation round-trip per thumbnail.
    The stage bitmask matters as much as the mtime: *removing* a later stage
    (skip, reset, downstream invalidation) changes which file a thumbnail comes
    from while leaving every remaining mtime untouched.
    """
    bits = 0
    stamps: list[int] = []
    bit = 0
    for face in card.faces:
        for stage in _STAGES:
            path = card.stage_path(stage, face)
            if path.exists():
                bits |= 1 << bit
                stamps.append(path.stat().st_mtime_ns)
            bit += 1
    return f"{bits:x}-{max(stamps):x}" if stamps else "0"


def _cache_control(rev: str | None) -> str:
    """A ``rev``-stamped URL is immutable; a bare one must be revalidated."""
    return "private, max-age=31536000, immutable" if rev else "no-cache"


def _encode(src: Path, box: tuple[int, int], quality: int) -> bytes:
    im = Image.open(src).convert("RGB")
    im.thumbnail(box)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _derived(
    request: Request, src: Path, box: tuple[int, int], quality: int, rev: str | None
) -> Response:
    """A downscaled JPEG of ``src``, memoized and conditionally served.

    Re-encoding an upscaled master is the slowest thing this server does, so a
    given (file, mtime, box) is encoded once per process, and a browser that
    already has it gets a 304 without any image work at all.
    """
    stat = src.stat()
    key = (str(src), stat.st_mtime_ns, box[0], quality)
    # the path is part of the identity — a thumbnail's source moves between
    # stages as steps run and are undone — and the digest must be stable across
    # restarts (str.__hash__ is salted per process), or --reload re-sends
    # everything the browser already holds
    digest = hashlib.blake2b(repr(key).encode(), digest_size=8).hexdigest()
    etag = f'"{digest}"'
    headers = {"ETag": etag, "Cache-Control": _cache_control(rev)}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    body = _JPEG_CACHE.get(key)
    if body is None:
        body = _encode(src, box, quality)
        _JPEG_CACHE[key] = body
        while len(_JPEG_CACHE) > _JPEG_CACHE_MAX:
            del _JPEG_CACHE[next(iter(_JPEG_CACHE))]
    return Response(body, media_type="image/jpeg", headers=headers)


# ---- what is running ---------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Job:
    """One CLI subprocess the browser may be waiting on."""

    #: the verb, for a label — "fetch", "upscale", "sheet"
    command: str
    #: where that process is writing its count (see :mod:`proxdex.progress`)
    path: Path
    at: float


_jobs: list[_Job] = []
_jobs_lock: Final = Lock()


class _Watched:
    """Registers a job for the length of a ``run_cli`` call.

    A context manager rather than bookkeeping at both ends, because a command that
    raises must still leave the list empty — a job that never clears would leave
    the UI showing a bar for work that stopped.
    """

    def __init__(self, args: list[str]) -> None:
        self.job = _Job(args[0] if args else "", scratch.file(".json"), time.time())
        # absent means "nothing said yet", which `progress.read` answers as None;
        # an empty file would be a parse failure saying the same thing less clearly
        self.job.path.unlink(missing_ok=True)

    def __enter__(self) -> _Job:
        with _jobs_lock:
            _jobs.append(self.job)
        return self.job

    def __exit__(self, *_: object) -> None:
        with _jobs_lock:
            if self.job in _jobs:
                _jobs.remove(self.job)
        self.job.path.unlink(missing_ok=True)


class _Counted:
    """One job for a *request* that spends several CLI calls, counting them itself.

    `/api/fetch` makes one call per card whose description the client already had, so a
    tray of four Pokémon cards was **four jobs of one item each**. One item is not a
    position (:attr:`proxdex.progress.Report.positional`), so every one of them fell
    back to the sweep, and the browser showed an indeterminate bar with a note
    flickering between four card ids — for work that knew exactly how many cards it was
    filing. The count was there; it was just cut into pieces too small to have one.

    So the count belongs to the **request**, which is the thing that knows the total,
    and the inner calls run unwatched (``run_cli(..., watch=False)``): two jobs for one
    wait is what put the uncounted one on screen, since `/api/progress` shows the
    newest. This writes through the same :class:`proxdex.progress.Sink` a command uses,
    so the browser cannot tell — and does not need to — whether the count came from a
    subprocess or from here.
    """

    def __init__(self, command: str, verb: str, total: int) -> None:
        self.job = _Job(command, scratch.file(".json"), time.time())
        self.job.path.unlink(missing_ok=True)
        self._sink = progress.Sink(self.job.path)
        self._verb, self._total = verb, total

    def __enter__(self) -> Self:
        with _jobs_lock:
            _jobs.append(self.job)
        self._sink.start(self._verb, self._total)
        return self

    def at(self, note: str) -> None:
        """Which item is being worked on, not yet finished."""
        self._sink.at(note)

    def advance(self, by: int = 1, note: str = "") -> None:
        """One (or several) items finished. ``by`` is more than one where a single CLI
        call really does cover several cards — the batch leg of a mixed fetch — since
        stepping through them one at a time would be a position nobody measured."""
        for _ in range(max(1, by)):
            self._sink.advance(note)

    def __exit__(self, *_: object) -> None:
        with _jobs_lock:
            if self.job in _jobs:
                _jobs.remove(self.job)
        self._sink.finish()


def _running() -> list[_Job]:
    """The jobs in flight, newest first."""
    with _jobs_lock:
        return sorted(_jobs, key=lambda j: j.at, reverse=True)


def create_app(lib: Library) -> FastAPI:
    app = FastAPI(title="proxdex", docs_url=None, redoc_url=None)
    # the shell and the JSON payloads compress well; images are already coded
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    cfg_path = lib.root / "proxdex.toml"

    def run_cli(args: list[str], *, watch: bool = True) -> dict[str, Any]:
        """Run the real CLI, and let the browser watch it.

        Every mutation goes through here, so this is the one place a job has to be
        registered: the command reports its own count into a file
        (``$PROXDEX_PROGRESS``, see :mod:`proxdex.progress`) and
        :func:`api_progress` reads it while this call is still blocking. Nothing is
        parsed out of the log, and a command that reports nothing is simply a job
        with no count — which is the truth about it.

        ``watch=False`` is for a call already inside a :class:`_Counted` request, and it
        does two things that go together: no job is registered, and the child is spawned
        **without** ``$PROXDEX_PROGRESS``, so its sink is the no-op it is for a person
        at a terminal. Otherwise one wait would have two jobs and the browser would show
        the newest — the one-item call that has no position in it.
        """
        env = dict(os.environ)
        if not watch:
            env.pop(progress.ENV, None)
            proc = subprocess.run(
                [sys.executable, "-m", "proxdex", "--root", str(lib.root), *args],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            return {"ok": proc.returncode == 0, "log": proc.stdout + proc.stderr}
        with _Watched(args) as job:
            proc = subprocess.run(
                [sys.executable, "-m", "proxdex", "--root", str(lib.root), *args],
                capture_output=True,
                text=True,
                check=False,
                env={**env, progress.ENV: str(job.path)},
            )
        return {"ok": proc.returncode == 0, "log": proc.stdout + proc.stderr}

    @app.get("/api/progress")
    def api_progress() -> dict[str, Any]:
        """How far along whatever is running has got — newest job first.

        No job id, deliberately. This is a single-user console on localhost and
        every mutation happens behind one overlay, so "what is running" is a
        question about the server rather than about a request. It also means a
        second tab watching an upscale somebody started in the first one sees it,
        which is better than a spinner that knows nothing.
        """
        running = _running()
        newest = running[0] if running else None
        if newest is None:
            return {"running": False, "jobs": 0}
        report = progress.read(newest.path)
        out: dict[str, Any] = {
            "running": True,
            "jobs": len(running),
            "command": newest.command,
            "elapsed": round(time.time() - newest.at, 2),
        }
        if report is not None:
            out |= report.json()
            # Both decided in one place (`progress.Report`) rather than by each
            # reader's own arithmetic — and `remaining` is measured on the command's
            # own clock, so the second or two this subprocess spent importing and
            # reading the library is not counted as part of the per-item rate.
            out["positional"] = report.positional
            left = report.remaining
            if left is not None:
                out["left"] = round(left, 1)
        return out

    # ---- pages / static ----------------------------------------------------
    # the vendored component library (Bootstrap, MIT) ships with the package, so
    # the UI is fully offline — nothing is fetched from a CDN at runtime
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _HTML_PATH.read_text(encoding="utf-8")  # re-read → edit & refresh

    # ---- config ------------------------------------------------------------
    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
        doc = tomlkit.parse(text)
        allowed = _field_options()
        docs = Config.describe()
        sections: dict[str, Any] = {}
        options: dict[str, list[str]] = {}
        described: dict[str, dict[str, str]] = {}
        for name, table in doc.items():
            if not hasattr(table, "items"):
                continue
            sections[name] = {k: _unwrap(v) for k, v in table.items()}
            for key in sections[name]:
                field = Config.field_name(name, key)
                if field in allowed:
                    options[f"{name}.{key}"] = allowed[field]
                # the label, explanation, unit and real default, straight off the
                # Config field — so the settings screen never invents its own
                if field in docs:
                    described[f"{name}.{key}"] = docs[field]
        # **The one setting whose options are not knowable from the dataclass.**
        # `library_game` used to be a `GameId`, so `_field_options` read them off the
        # enum and the screen drew a dropdown for free. It is a plain name now — a
        # library defines its own games — so the values come from *this library* and
        # are injected here, which is the same thing `/api/meta` does for `--frame`.
        # Without it the field silently became a free-text box you could typo.
        options.setdefault("library.game", list(games.load(lib.root).ids))
        return {
            "root": str(lib.root),
            "sections": sections,
            "options": options,
            "docs": described,
            # keys nothing reads any more. Served as a list rather than left for the
            # UI to derive from `docs`, so the screen and `config prune` agree on
            # exactly which keys are ignored.
            "stale": [
                f"{name}.{key}"
                for name, table in sections.items()
                for key in table
                if Config.field_name(name, key) is None
            ],
        }

    @app.post("/api/config/prune")
    def api_config_prune() -> dict[str, Any]:
        """Delete every key nothing reads — `proxdex config prune --yes`.

        Through the CLI like every other mutation, so there is one implementation of
        which keys go and what happens to a table left empty.
        """
        return run_cli(["config", "prune", "--yes"])

    @app.put("/api/config")
    def api_config_put(body: ConfigBody) -> Any:
        text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
        doc = tomlkit.parse(text)
        # Coerce every value through Config's own annotations *before* writing:
        # the file then always holds the declared type (an enum's own value, not
        # a select's "2"), and a bad value is a 400 instead of a broken library.
        updates: list[tuple[str, str, Any]] = []
        for section, kv in body.sections.items():
            for key, value in kv.items():
                field = Config.field_name(section, key)
                if field is None:
                    updates.append((section, key, value))
                    continue
                try:
                    clean = Config.coerce(field, value)
                except ConfigError as exc:
                    return _bad(str(exc))
                updates.append(
                    (
                        str(section),
                        str(key),
                        clean.value if isinstance(clean, Enum) else clean,
                    )
                )
        for section, key, value in updates:
            # An **optional** setting cleared is unset, and TOML spells unset by the key
            # not being there — there is no `None` to write, so this removes it. Same
            # rule `config set key=` follows, and the same distinction the sheet
            # builder's controls draw between "clear this" and "store a blank".
            if value is None:
                if section in doc and key in doc[section]:
                    del doc[section][key]
                continue
            if section not in doc:
                doc[section] = tomlkit.table()
            doc[section][key] = value
        cfg_path.write_text(tomlkit.dumps(doc), encoding="utf-8", newline="\n")
        return {"ok": True}

    @app.get("/api/meta")
    def api_meta() -> dict[str, Any]:
        cfg = Config.load(lib.root)
        return {
            # the whole pipeline — order, labels, skippability and every step's
            # settings schema with this library's defaults. The UI renders its
            # stepper and its control panels from this and spells nothing itself,
            # so a new step appears in the UI as soon as it exists in Python.
            # the border step's frame list depends on the library, so the root
            # travels with it — an OPEN option cannot be filled in from `cfg`
            "pipeline": steps.json_pipeline(cfg, lib.root),
            "profiles": profiles.names(lib.root),
            "active_profile": cfg.print_profile,
            "active_back_profile": cfg.print_back_profile,
            "faces": [f.value for f in Faces],
            # **Every page setting a print run can override, described.** The sheet
            # builder renders its controls from this and spells none of them itself —
            # the same relationship the step panels have with `steps.py`, and the
            # reason a setting becomes overridable on the page the moment
            # `run=Run.SHEET` is added to it. `current` is *this library's* value,
            # which is what a row shows as its default; an absent override means
            # exactly that value, so the builder never has to send one.
            "sheet_options": [
                {**opt.json(), "current": _current_text(cfg, opt)}
                for opt in Config.run_options(config.Run.SHEET)
            ],
            "stages": [s.label for s in _STAGES],
            "steps": [s.value for s in Step],
            # the import vocabulary: what to do about a stage that already exists,
            # what the planner can conclude about a file, and which suffixes count
            # as a card image — so the wizard filters and labels off this, not off
            # its own copy of the list
            "import": {
                "on_existing": [o.value for o in imports.OnExisting],
                "dispositions": [
                    {
                        "id": d.value,
                        "writes": d.writes,
                        "blocked": d.blocked,
                    }
                    for d in imports.Disposition
                ],
                "suffixes": sorted(imports.IMAGE_SUFFIXES),
            },
            # every game this library has, its own included. `provider` is what a
            # screen branches on: a game with none cannot be searched or browsed, so
            # those screens offer only the games that can answer.
            "games": [
                {
                    "id": g.id,
                    "name": g.name,
                    "example": g.example,
                    "provider": g.provider,
                    "custom": g.custom,
                    "sets": [one.json() for one in g.sets],
                }
                for g in games.load(lib.root).games
            ],
            "default_game": lib.default_game,
            "frames": [_guide_json(g) for g in specs.load(lib.root).specs.values()],
            # the print-kind vocabulary, so the UI names a layout the same way
            # the CLI does instead of keeping its own copy
            "layouts": [
                {"id": lay.value, "label": lay.label, "note": lay.note}
                for lay in games.Layout
            ],
            # every card prints at its own size: the configured trim keeps the
            # configured grid, an oversized card gets its own pages. Both grids
            # are served so the sheet dialog can say what the pages will be.
            "trims": [
                {
                    "name": "standard",
                    "mm": [cfg.card_w_mm, cfg.card_h_mm],
                    "grid": list(
                        sheet_mod.grid_for(cfg, (cfg.card_w_mm, cfg.card_h_mm))
                    ),
                },
                {
                    "name": "oversized",
                    "mm": [games.OVERSIZED_W_MM, games.OVERSIZED_H_MM],
                    "grid": list(
                        sheet_mod.grid_for(
                            cfg, (games.OVERSIZED_W_MM, games.OVERSIZED_H_MM)
                        )
                    ),
                },
            ],
            # the topbar's library path — here so boot needs no second request
            "root": str(lib.root),
        }

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        """Which card APIs are misbehaving right now — recorded by every request
        proxdex makes, including the ones a CLI subprocess made."""
        return {
            "hosts": [
                {
                    "host": h.host,
                    "health": h.health.value,
                    "detail": h.detail,
                    "age": round(h.age),
                }
                for h in net.health()
            ]
        }

    # ---- cards / images ----------------------------------------------------
    @app.get("/api/cards")
    def api_cards() -> list[dict[str, Any]]:
        by_card = report.card_batch_index(lib)
        # one registry read for the whole listing rather than one per card: the
        # answer cannot change halfway down a response
        reg = specs.load(lib.root)
        result: list[dict[str, Any]] = []
        for card in lib.cards():
            batch = by_card.get(card.id)
            names = card.face_names()
            result.append(
                {
                    "id": card.id,
                    "name": card.name.title(),
                    "set": card.set_id,
                    "game": card.game,
                    # one entry per printable side, front first. A single-faced
                    # card still has exactly one, so nothing has to special-case.
                    "faces": [
                        {
                            "index": f,
                            "name": names[f] or ("Front" if f == FRONT else "Back"),
                            "status": {
                                s.label: card.status(s, f).value for s in _STAGES
                            },
                        }
                        for f in card.faces
                    ],
                    "front_face": card.front_face,
                    # what this printing is, so the contact sheet can badge a
                    # meld half or an oversized card without asking the API
                    "layout": card.layout.value,
                    "oversized": card.oversized,
                    "frame": card.printing_frame,
                    "pin": card.pin,
                    # the card's own state for the contact sheet: a card is only
                    # done at a stage when every one of its sides is
                    "status": {s.label: card.rollup(s).value for s in _STAGES},
                    # the spec this card's border step will actually fit to, and
                    # *why* — a pin, its printing, a rule, its era or nothing
                    "frame_spec": (spec := (found := _resolution(reg, card)).spec)
                    and spec.id,
                    "frame_sure": found.sure,
                    "frame_via": found.via.value,
                    "batch": batch.name if batch else None,
                    "printed": bool(batch and batch.printed),
                    "rev": _rev(card),
                }
            )
        return result

    @app.get("/api/thumb/{cid}")
    def api_thumb(
        request: Request, cid: str, face: int = FRONT, rev: str | None = None
    ) -> Response:
        card = lib.find(cid)
        src = card.best(*_BEST, face=face) if card else None
        if src is None:
            return Response(status_code=404)
        return _derived(request, src, _THUMB_BOX, 82, rev)

    @app.get("/api/view/{cid}/{stage}")
    def api_view(
        request: Request,
        cid: str,
        stage: str,
        face: int = FRONT,
        rev: str | None = None,
    ) -> Response:
        """A downscaled JPEG for the viewer — big enough to fill the proof on a
        large display, small enough that every stage of the card preloads."""
        card = lib.find(cid)
        st = STAGE_BY_LABEL.get(stage)
        if card is None or st is None or not card.has(st, face):
            return Response(status_code=404)
        return _derived(request, card.stage_path(st, face), _VIEW_BOX, 88, rev)

    @app.get("/api/image/{cid}/{stage}")
    def api_image(
        cid: str, stage: str, face: int = FRONT, rev: str | None = None
    ) -> Response:
        card = lib.find(cid)
        st = STAGE_BY_LABEL.get(stage)
        if card is None or st is None or not card.has(st, face):
            return Response(status_code=404)
        return FileResponse(
            card.stage_path(st, face), headers={"Cache-Control": _cache_control(rev)}
        )

    @app.get("/api/art")
    def api_art(request: Request, u: str, size: art.Size) -> Response:
        """A provider's picture, downscaled to the size it is drawn at and kept.

        Browse's cost was never its JSON: a set index pulled 24.7 MB of logo PNGs
        into a slot 2.25rem tall and a 60-card page pulled 45 MB of full-size
        scans into 190px tiles, every visit. See :mod:`proxdex.art` — including
        why the host is checked against a list rather than taken on trust.

        The URL names one picture at one size, so the answer is immutable and the
        browser is told so; a mismatched ``If-None-Match`` still costs only a
        read from the cache directory.
        """
        try:
            picture = art.load(u, size, Config.load(lib.root))
        except FileError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (net.NetworkError, requests.RequestException, OSError, ValueError):
            # a picture that will not arrive is a picture: the tile's `onerror`
            # drops it and the card is still identified by its text
            return Response(status_code=502)
        headers = {
            "ETag": picture.etag,
            "Cache-Control": "private, max-age=31536000, immutable",
        }
        if request.headers.get("if-none-match") == picture.etag:
            return Response(status_code=304, headers=headers)
        return Response(picture.body, media_type=picture.media_type, headers=headers)

    @app.get("/api/details/{cid}")
    def api_details(cid: str) -> dict[str, Any]:
        """Everything the card's API says about it — facts, links, raw JSON.

        A live provider call, not library state: it is display-only, so a
        degraded API is reported as an ``error`` the panel shows rather than
        breaking the card page.
        """
        if not _ID_OK.match(cid):
            return {"error": f"{cid}: not a card id"}
        card = lib.find(cid)
        mine = games.load(lib.root).get(card.game) if card else None
        if card is not None and mine is not None and mine.custom:
            # **A state, not an error.** A custom game has no provider, so asking one
            # is the only part of this panel that cannot work — and reporting it as a
            # failure would put a red box on every card of your own game forever. The
            # card is described from what proxdex already knows instead: no fact groups
            # and no outbound links, because those are the provider's and there is
            # none. Same call `proxdex show` makes (`cli._detail`).
            detail = sources.CardDetail(
                meta=sources.local_meta(
                    cid, mine, name=card.name.title(), faces=len(card.faces)
                ),
                source=mine.source,
            )
        else:
            try:
                detail = sources.details(
                    cid, Config.load(lib.root), card.game if card else None
                )
            except (requests.RequestException, ProxdexError) as exc:
                return {"error": str(exc)}
        return {
            "id": detail.meta.id,
            "name": detail.meta.name,
            "game": detail.meta.game,
            "set": detail.meta.set_name,
            "source": detail.source,
            "layout": detail.meta.layout.value,
            "layout_label": detail.meta.layout.label,
            "layout_note": detail.meta.layout.note,
            "oversized": detail.meta.oversized,
            # meld halves, the melded card, the tokens it makes — each a card in
            # its own right, so each can be added from here
            "related": [
                {
                    "relation": r.relation.value,
                    "label": r.relation.label,
                    "name": r.name,
                    "id": r.id,
                    "have": bool(r.id and lib.find(r.id)),
                }
                for r in detail.related
            ],
            "groups": [
                {
                    "title": g.title,
                    "facts": [
                        {"label": f.label, "value": f.value, "block": f.block}
                        for f in g.facts
                    ],
                }
                for g in detail.groups
            ],
            "links": [{"label": ln.label, "url": ln.url} for ln in detail.links],
        }

    @app.get("/api/frame/{cid}")
    def api_frame(
        cid: str, stage: str | None = None, face: int = FRONT
    ) -> dict[str, Any]:
        """Image pixel size + this card's era frame guide, for the align tool."""
        card = lib.find(cid)
        if card is None:
            return {"error": "no image"}
        st = STAGE_BY_LABEL.get(stage) if stage else None
        src = (
            card.stage_path(st, face)
            if st and card.has(st, face)
            else card.best(*_BEST, face=face)
        )
        if src is None or not src.exists():
            return {"error": "no image"}
        cfg = Config.load(lib.root)
        with Image.open(src) as im:
            w, h = im.width, im.height
        reg = specs.load(lib.root)
        found = _resolution(reg, card)
        guide = found.spec
        # the size *this* card prints at, so the align ghost draws the trim
        # `border` will really produce — an oversized printing is 88.9×127mm, and
        # the JS `solveFit` mirrors cardbleed against whatever it is handed here
        trim_w, trim_h = sheet_mod.trim_mm(card, cfg)
        return {
            "w": w,
            "h": h,
            # the two millimetres, and no aspect beside them: `solveFit` divides
            # them itself and is held to cardbleed within 1e-12, so a rounded
            # third copy of the same fact could only ever be the wrong one
            "card_w_mm": trim_w,
            "card_h_mm": trim_h,
            "game": card.game,
            "game_name": games.load(lib.root).name_of(card.game),
            # frame-size guide: inner border inset [top,right,bottom,left], plus
            # how much to trust it — the UI warns on an unmeasured set.
            "guide": _guide_json(guide) if guide else None,
            "guides": [_guide_json(g) for g in reg.choices(card.game)],
            # which of the seven ways this spec was arrived at, and anything the
            # align panel has to say out loud about it
            "resolution": found.json(),
            "pin": card.pin,
            # the fill settings this card already carries, so the Advanced section
            # opens on what it was last bordered with rather than on the defaults
            "tune": bleed.Tuning.from_pairs(card.tune(Stage.BORDERED, face)).json(),
            "knobs": [k.json() for k in bleed.KNOBS],
            # the reading this card's master was fitted from, so a *done* step can
            # answer "is any border being invented here" with the marks down — and so
            # the panel knows a re-fill is even possible
            "marks": _edges_json(card.marks(Stage.BORDERED, face)),
        }

    # ---- frame specs -------------------------------------------------------
    # Read directly; every mutation shells out to `proxdex frames …`, so the CLI
    # stays the only implementation of what a spec, a rule and a pin mean.
    @app.get("/api/frames")
    def api_frames() -> dict[str, Any]:
        """Every spec, every rule, what this library's cards resolve to, and the
        warnings — one read, because the frames screen shows all four at once."""
        reg = specs.load(lib.root)
        held: dict[tuple[str, str], dict[str, Any]] = {}
        resolved: list[tuple[str, specs.Resolution]] = []
        for card in lib.cards():
            found = _resolution(reg, card)
            resolved.append((card.id, found))
            key = (card.game, card.set_id)
            row = held.setdefault(
                key,
                {
                    "game": card.game,
                    "set": card.set_id,
                    "cards": 0,
                    "spec": found.spec.id if found.spec else "",
                    "via": found.via.value,
                    "via_label": found.via.label,
                    "rule": found.rule,
                    "pinned": 0,
                    "undecided": 0,
                },
            )
            row["cards"] += 1
            if card.pin:
                row["pinned"] += 1
            if found.undecided:
                row["undecided"] += 1
        return {
            **specs.json_registry(reg),
            "mine": sorted(held.values(), key=lambda r: (r["game"], r["set"])),
            "issues": [i.json() for i in specs.audit(reg, resolved)],
            "faults": [{"id": f.value, "label": f.label} for f in specs.Fault],
        }

    @app.get("/api/frames/preview/{set_id}")
    def api_frames_preview(set_id: str, game: str | None = None) -> dict[str, Any]:
        """Which spec every card of one set gets, and which rule decided it."""
        try:
            found = inventory.preview(
                set_id,
                Config.load(lib.root),
                specs.load(lib.root),
                games.coerce(game, lib.default_game),
            )
        except ProxdexError as exc:
            return {"error": str(exc), "set": set_id, "rows": []}
        return found.json()

    @app.get("/api/frames/coverage")
    def api_frames_coverage() -> dict[str, Any]:
        """What has a measured frame spec and what nobody has read yet, per game.

        Its own route rather than part of `/api/frames`, because it costs a provider
        request per game (the set list, cached a day) and the other three tabs must
        not wait on one. Every game in one answer: "have we covered everything?" is a
        question about the whole of what proxdex can border, and asking it a game at a
        time is how a gap in the one you were not looking at stays invisible. A custom
        game is in it too, and costs no request at all — its sets are declared.
        """
        cfg = Config.load(lib.root)
        reg = specs.load(lib.root)
        held = browse.owned([card.set_id for card in lib.cards()])
        out: list[dict[str, Any]] = []
        for game in games.load(lib.root).games:
            try:
                out.append(inventory.coverage(game, cfg, reg, held).json())
            except (requests.RequestException, ProxdexError) as exc:
                # one game's provider being down must not blank the other's answer —
                # the same reason a facet whose catalog request failed is dropped
                out.append(
                    {
                        "game": game.id,
                        "error": f"could not list this game's sets (try again): {exc}",
                    }
                )
        return {"games": out}

    @app.post("/api/frames/spec")
    def api_frames_spec(body: SpecBody) -> Any:
        """Add or correct a spec. One verb, `frames set`, and the CLI is what
        decides what a spec means — this only spells the flags."""
        return run_cli(body.argv())

    @app.delete("/api/frames/spec/{spec_id}")
    def api_frames_spec_delete(spec_id: SpecName) -> Any:
        return run_cli(["frames", "rm", spec_id])

    @app.post("/api/frames/rule")
    def api_frames_rule(body: RuleBody) -> Any:
        return run_cli(body.argv())

    @app.delete("/api/frames/rule/{rule_id}")
    def api_frames_rule_delete(rule_id: RuleName) -> Any:
        return run_cli(["frames", "unassign", rule_id])

    @app.post("/api/frames/pin")
    def api_frames_pin(body: PinBody) -> Any:
        """Pin cards to a spec, or drop their pins when ``spec`` is absent."""
        if body.spec is None:
            return run_cli(["frames", "unpin", *body.ids])
        return run_cli(["frames", "pin", body.spec, *body.ids])

    @app.delete("/api/card/{cid}")
    def api_delete(cid: str) -> dict[str, Any]:
        card = lib.find(cid)
        if card is None:
            return {"ok": False, "log": f"{cid}: not found"}
        shutil.rmtree(card.dir, ignore_errors=True)
        report.write_index(lib)
        return {"ok": True, "log": f"deleted {cid}"}

    # ---- search / acquire --------------------------------------------------
    @app.get("/api/search")
    def api_search(
        q: str = "",
        game: str | None = None,
        set_filter: Annotated[str | None, Query(alias="set")] = None,
        rarity: str | None = None,
        year: str | None = None,
        type_: Annotated[str | None, Query(alias="type")] = None,
        supertype: str | None = None,
        subtype: str | None = None,
        color: str | None = None,
        sort: str | None = None,
        desc: bool | None = None,
        page: int = 1,
        per_page: int = browse.PER_PAGE,
    ) -> Any:
        """One page of the cards matching a query — searching *and* browsing.

        The same endpoint answers both, because they are the same question:
        browsing a set is a query carrying a set and no text (see
        :class:`proxdex.browse.Query`). ``type`` and ``color`` take a
        comma-separated list, which is how a multi-pick filter travels in a URL.
        """
        cfg = Config.load(lib.root)
        want = games.coerce(game, cfg.library_game)
        wanted = browse.Query(
            game=want,
            text=q,
            set_id=set_filter or "",
            rarity=rarity or "",
            year=year or "",
            types=_csv(type_),
            supertype=supertype or "",
            subtype=subtype or "",
            colors=tuple(c.upper() for c in _csv(color)),
            # an unknown sort is the default rather than a 422: this arrives from
            # an address bar somebody may have edited, and a browse screen that
            # will not draw is a worse answer than one sorted by date
            sort=browse.parse_sort(sort) or browse.Sort.RELEASED,
            desc=desc,
            page=page,
            per_page=per_page,
        )
        try:
            found = sources.search_page(wanted, cfg)
        except (requests.RequestException, ProxdexError) as exc:
            return {"error": f"search failed (try again): {exc}"}
        # the whole page's pictures, six at a time, while the browser lazily asks
        # for the ones on screen — the two share one fetch each (see art.load)
        art.warm((r.thumb for r in found.items), art.Size.CARD, cfg)
        return {
            **found.json(),
            "query": wanted.params(),
            "narrowed": wanted.narrowed,
            "items": [_hit_json(r) for r in found.items],
        }

    def _hit_json(r: sources.SearchResult) -> dict[str, Any]:
        """One search hit. ``have`` is the library's own answer, per row — the
        notice that stops you re-fetching a card you already filed."""
        return {
            "id": r.id,
            "name": r.name,
            "game": r.game,
            "set": r.set_name,
            "set_id": r.set_id,
            "year": r.year,
            "number": f"{r.number}/{r.printed_total}" if r.printed_total else r.number,
            "rarity": r.rarity,
            "artist": r.artist,
            # two pictures, because they answer two things: `image` is the
            # full-resolution scan the `full ↗` link offers (and what `fetch` would
            # file), `thumb` is the small one the tile draws. Equal where the provider
            # publishes only one size.
            "image": r.image_url,
            "thumb": r.thumb,
            # the traits a frame rule matches on, so a client adding this card can
            # hand them straight back instead of costing a second metadata request
            "subtypes": r.traits.get("subtypes", ""),
            # what this printing is, so a hit can be badged before it is fetched
            "layout": r.layout.value,
            "oversized": r.oversized,
            "have": lib.find(r.id) is not None,
        }

    @app.get("/api/expansions")
    def api_expansions(game: str | None = None) -> Any:
        """Every set of one game, grouped the way that game groups them, with how
        many cards of each the library already holds.

        The counts are local and free (they come off the card folders), which is
        what makes the index worth opening: the interesting fact about a set you
        are browsing is how much of it you already have.
        """
        cfg = Config.load(lib.root)
        want = games.coerce(game, cfg.library_game)
        held = browse.owned([card.set_id for card in lib.cards()])
        try:
            found = browse.groups(want, cfg)
        except (requests.RequestException, ProxdexError) as exc:
            return {"error": f"could not list sets (try again): {exc}"}
        # start on the art while the browser is still drawing the tiles: it will
        # ask for six at a time, and there are ~174 of them
        art.warm((e.logo_url for g in found for e in g.expansions), art.Size.LOGO, cfg)
        art.warm(
            (e.symbol_url for g in found for e in g.expansions), art.Size.SYMBOL, cfg
        )
        return {
            **browse.meta(want),
            "groups": [g.json(held) for g in found],
            "sets": sum(len(g.expansions) for g in found),
            "owned": sum(held.values()),
        }

    @app.get("/api/facets")
    def api_facets(game: str | None = None) -> Any:
        """What this game can be filtered by, and the values each filter offers.

        Served rather than spelled in JS for the reason ``/api/meta`` serves the
        step schema: the vocabulary is the provider's, it differs per game, and a
        copy in the UI would be a second list to keep in step. A facet whose
        catalog request failed is simply absent — see
        :func:`proxdex.browse.facets`.
        """
        cfg = Config.load(lib.root)
        want = games.coerce(game, cfg.library_game)
        return {
            **browse.meta(want),
            "facets": [f.json() for f in browse.facets(want, cfg)],
        }

    @app.post("/api/fetch")
    def api_fetch(body: FetchBody) -> dict[str, Any]:
        """Download cards by id — one batch call, plus one call per card whose
        description the client already had.

        A card sent with its `known` description needs **no metadata request at all**,
        which matters because that request is the one that fails when pokemontcg.io is
        having a bad afternoon: the browser had just drawn the card's name, set and
        rarity from a search response, and `fetch` was asking for all of it again.
        Those go one at a time, because a description belongs to one card — and the
        batch keeps its single call, so nothing is slower for the ordinary case.

        **The count is the request's, not each call's** (:class:`_Counted`). Split
        across one-card calls it was a total of 1 every time, which has no position in
        it, so a four-card add drew a sweep. With nothing described there is nothing to
        count here
        and the batch reports its own progress exactly as before.
        """
        described = {k.id: k for k in body.known}
        plain = [cid for cid in body.ids if cid not in described]
        if not described:
            return _fetch_batch(body, plain)
        logs: list[str] = []
        ok = True
        with _Counted("fetch", "fetching", len(body.ids)) as job:
            for cid in body.ids:
                if cid not in described:
                    continue
                k = described[cid]
                args = [
                    "fetch",
                    cid,
                    *_side(body.face),
                    "--game",
                    games.GameId.POKEMON.value,
                    "--name",
                    k.name,
                ]
                for flag, value in (
                    ("--set-name", k.set_name),
                    ("--rarity", k.rarity),
                    ("--subtypes", k.subtypes),
                ):
                    if value:
                        args += [flag, value]
                if body.related:
                    args.append("--related")
                job.at(cid)
                out = run_cli(args, watch=False)
                job.advance(note=cid)
                logs.append(str(out.get("log", "")))
                ok = ok and out.get("ok") is not False
            if plain:
                # one call for the lot, so it lands as one advance of len(plain): the
                # cards inside it finish at times this process does not see, and
                # stepping the bar through them would be a position nobody measured
                job.at(f"{len(plain)} more")
                out = _fetch_batch(body, plain, watch=False)
                job.advance(by=len(plain))
                logs.append(str(out.get("log", "")))
                ok = ok and out.get("ok") is not False
        return {"ok": ok, "log": "\n".join(x for x in logs if x)}

    def _fetch_batch(
        body: FetchBody, ids: list[str], *, watch: bool = True
    ) -> dict[str, Any]:
        """The ids nobody described — one call, and the CLI counts them itself."""
        if not ids:
            return {"ok": True, "log": ""}
        args = ["fetch", *ids, *_side(body.face)]
        if body.game is not None:
            args += ["--game", body.game]
        if body.related:
            args.append("--related")
        return run_cli(args, watch=watch)

    @app.post("/api/import/plan")
    def api_import_plan(body: ImportPlanBody) -> dict[str, Any]:
        """What importing these files would do — from their names, reading only
        the library. The wizard's review table and ``import --dry-run`` are the
        same :func:`proxdex.imports.plan`, so they cannot disagree."""
        run = imports.plan(
            lib, [i.item() for i in body.items], on_existing=body.on_existing
        )
        return run.json()

    @app.post("/api/import")
    def api_import(
        file: Annotated[UploadFile, File()],
        cid: Annotated[str, Form(alias="id", pattern=_ID_OK.pattern)],
        stage: Annotated[StageLabel, Form()] = "original",
        game: Annotated[GameName | None, Form()] = None,
        face: Annotated[int | None, Form(ge=1, le=_MAX_FACE)] = None,
        card_name: Annotated[str, Form(max_length=120)] = "",
        faces: Annotated[int, Form(ge=1, le=_MAX_FACE)] = 1,
        on_existing: Annotated[imports.OnExisting, Form()] = (
            imports.OnExisting.OVERWRITE
        ),
    ) -> dict[str, Any]:
        """File one uploaded image. The wizard calls this once per row, so a
        failure names its own file and the rest of the folder still lands."""
        tmp = _spool(file)
        try:
            args = [
                "import",
                str(tmp),
                "--id",
                cid,
                "--stage",
                stage,
                "--on-existing",
                on_existing.value,
                "--move",
            ]
            if game is not None:
                args += ["--game", game]
            # a custom game has no lookup to name the card or count its sides, so
            # both come from the row. Sent only when they say something, so the argv
            # for every ordinary import is byte-for-byte what it was.
            if card_name:
                args += ["--card-name", card_name]
            if faces > 1:
                args += ["--faces", str(faces)]
            return run_cli([*args, *_side(face)])
        finally:
            tmp.unlink(missing_ok=True)

    # ---- prepare steps -----------------------------------------------------
    @app.post("/api/step")
    def api_step(body: StepBody) -> Any:
        side = _side(body.face)
        # pipeline-state verbs: `<cmd> <step> <ids...>`
        if body.cmd in {"skip", "unskip", "reset"}:
            if body.step is None:
                return _bad(f"{body.cmd} needs a step")
            return run_cli([body.cmd, body.step.value, *body.ids, *side])
        spec = steps.get(body.cmd)
        if spec is None or spec.step is None:
            return _bad(f"bad step {body.cmd!r}")
        if (wrong := _bad_setting(spec, body.settings, lib.root)) is not None:
            return _bad(wrong)
        args = [spec.step.value, *body.ids, *side, *spec.argv(body.settings)]
        if spec.step is Step.BORDER:
            if body.inner is not None:  # marked border edges → spec-based fit
                for edge, val in body.inner:
                    args += [f"--inner-{edge}", f"{val:g}"]
            elif body.grow is not None:  # plain per-edge growth, no fit
                for edge, val in body.grow:
                    args += [f"--{edge}", f"{val:g}"]
            if body.tune is not None:
                try:
                    tuning = bleed.Tuning.parse(body.tune)
                except FileError as exc:
                    return _bad(str(exc))
                # an empty tuning is a real request — "use the defaults" — and the CLI
                # spells that `--no-tune`, not an absent flag (which means "keep what
                # the card has")
                args += (
                    [f for pair in tuning.spelled() for f in ("--tune", pair)]
                    if not tuning.empty
                    else ["--no-tune"]
                )
        if body.force:
            args.append("--force")
        return run_cli(args)

    @app.post("/api/flip")
    def api_flip(body: FlipBody) -> Any:
        """Choose which side of a two-sided card prints on the front."""
        return run_cli(["flip", *body.ids, *_side(body.face)])

    # ---- produce -----------------------------------------------------------
    @app.post("/api/sheet")
    def api_sheet(body: SheetBody) -> Any:
        """Impose the run, and say which PDF came out of it.

        ``--no-open`` is not a preference here, it is a correction: `sheet`'s
        `[sheet] open` would launch a PDF viewer on the machine running the
        server, which is not the machine you are looking at. The browser's own
        equivalent is the link this returns — see `_written`.
        """
        if bad := _bad_override(body):
            return _bad(bad)
        res = run_cli(["sheet", body.name, *body.argv(), "--no-open"])
        if res["ok"]:
            res["batch"] = _written()
        return res

    def _written() -> dict[str, Any] | None:
        """The batch whose PDF was written most recently — what `--open` opens.

        Found by mtime rather than by rebuilding ``<date>_<slug>/<faces>.pdf``
        here: the CLI owns that naming, and a second copy of it in the web layer
        is a copy that can be wrong.
        """
        newest: tuple[float, report.Batch, Path] | None = None
        for batch in report.batches(lib):
            for pdf in batch.pdfs:
                stamp = pdf.stat().st_mtime
                if newest is None or stamp > newest[0]:
                    newest = (stamp, batch, pdf)
        if newest is None:
            return None
        _, found, latest = newest
        return {
            "name": found.name,
            "dir": found.dir.name,
            "pdf": latest.name,
            "pdfs": sorted(p.name for p in found.pdfs),
        }

    @app.post("/api/sheet/plan")
    def api_sheet_plan(body: SheetBody) -> Any:
        """What this run would print — pages per size, and what is not ready.

        A query, so it is answered here rather than by shelling out; the answer
        comes from `sheet.plan`, the same function the real run uses, so the page
        count the builder promises is the page count you get.
        """
        if bad := _bad_override(body):
            return _bad(bad)
        cfg = Config.load(lib.root)
        _apply_overrides(cfg, body)
        if body.cards:
            wanted = {c.id: c.copies for c in body.cards}
            chosen = [
                (card, wanted.get(card.id, 1)) for card in lib.select(tuple(wanted))
            ]
        else:
            chosen = [(card, 1) for card in lib.cards()]
        try:
            prof = profiles.active(lib.root, cfg, body.profile)
            back = profiles.active_back(lib.root, cfg, body.back_profile, prof)
        except ProxdexError as exc:
            return _bad(str(exc))
        out = sheet_mod.plan(chosen, cfg).json(cfg)
        out["profile"] = prof.summary()
        # Only when backs are actually printed AND on a different medium. Naming a
        # second profile on a fronts-only run would describe a correction that
        # never happens.
        prints_backs = cfg.sheet_faces is not Faces.FRONTS
        out["back_profile"] = (
            back.summary() if prints_backs and back.name != prof.name else None
        )
        return out

    @app.post("/api/printed/{name}")
    def api_printed(name: Annotated[str, PathParam(max_length=64)]) -> dict[str, Any]:
        return run_cli(["printed", name])

    @app.post("/api/index")
    def api_index() -> dict[str, Any]:
        report.write_index(lib)
        return {"ok": True, "log": "INDEX.md regenerated"}

    # ---- doctor ------------------------------------------------------------
    # `proxdex doctor`, both halves of it. The report is read directly (it opens
    # headers and writes nothing); the repair shells out like every other
    # mutation, so the CLI stays the only thing that rewrites a stored master.
    @app.get("/api/doctor")
    def api_doctor(ids: str = "") -> dict[str, Any]:
        cards = lib.select(tuple(i for i in ids.split(",") if i))
        return doctor.json_report(
            doctor.examine(cards, Config.load(lib.root), specs.load(lib.root))
        )

    @app.post("/api/doctor/fix")
    def api_doctor_fix(body: DoctorBody) -> dict[str, Any]:
        return run_cli(["doctor", "--fix", "--yes", *body.ids])

    @app.post("/api/back")
    def api_back(body: BackBody) -> dict[str, Any]:
        game = body.game or lib.default_game
        known = games.load(lib.root)
        found = known.get(game)
        args = ["back", "--game", game]
        if body.url:
            return run_cli([*args, "--url", body.url])
        if found is None or found.back_url is None:
            return {
                "ok": False,
                "log": f"no downloadable back for {known.name_of(game)} — "
                "upload your own scan",
            }
        return run_cli(args)

    @app.post("/api/back/upload")
    def api_back_upload(
        file: Annotated[UploadFile, File()],
        game: Annotated[str | None, Form()] = None,
    ) -> dict[str, Any]:
        tmp = _spool(file)
        want = games.coerce(game, lib.default_game)
        try:
            return run_cli(["back", "--game", want, "--file", str(tmp)])
        finally:
            tmp.unlink(missing_ok=True)

    @app.get("/api/batches")
    def api_batches() -> list[dict[str, Any]]:
        return [
            {
                "name": b.name,
                "dir": b.dir.name,
                "printed": b.printed,
                "cards": len(b.cards),
                "pdfs": sorted(p.name for p in b.dir.glob("*.pdf")),
            }
            for b in report.batches(lib)
        ]

    @app.get("/api/pdf/{batch}/{filename}")
    def api_pdf(batch: str, filename: str) -> Response:
        path = lib.batches_dir / batch / filename
        if path.suffix != ".pdf" or not path.is_file():
            return Response(status_code=404)
        return FileResponse(path, media_type="application/pdf")

    # ---- games -------------------------------------------------------------
    # A game a library defined is a file in it, like a profile — so reads are
    # computed here and every write shells out, which is what keeps `game add` the
    # only implementation of what defining a game means.
    @app.get("/api/games")
    def api_games() -> dict[str, Any]:
        cfg = Config.load(lib.root)
        known = games.load(lib.root)
        held: dict[str, int] = {}
        for card in lib.cards():
            held[card.game] = held.get(card.game, 0) + 1
        return {
            "default": cfg.library_game,
            # the same broken reference `profiles.dangling` and `frames check`
            # report: a name in a text file outliving the thing it names
            "dangling": games.dangling(lib.root, cfg),
            "reserved": sorted(games.RESERVED),
            # named rather than swallowed — a game silently absent takes its cards'
            # frame specs with it, and they then refuse to border for no stated reason
            "unreadable": list(known.unreadable),
            "games": [
                {**one.json(), "cards": held.get(one.id, 0)} for one in known.games
            ],
        }

    @app.post("/api/games/{game_id}")
    def api_game_new(game_id: GameName, body: GameBody) -> Any:
        if not body.name:
            return _bad("a game needs a name")
        return run_cli(["game", "add", game_id, *body.argv()])

    @app.patch("/api/games/{game_id}")
    def api_game_edit(game_id: GameName, body: GameBody) -> dict[str, Any]:
        args = body.argv()
        if not args:
            return {"ok": True, "log": "nothing to change"}
        return run_cli(["game", "edit", game_id, *args])

    @app.delete("/api/games/{game_id}")
    def api_game_rm(game_id: GameName) -> dict[str, Any]:
        return run_cli(["game", "rm", game_id, "--yes"])

    @app.post("/api/games/{game_id}/sets/{set_id}")
    def api_game_set(
        game_id: GameName, set_id: SetName, body: GameSetBody
    ) -> dict[str, Any]:
        return run_cli(["game", "set", "add", game_id, set_id, *body.argv()])

    @app.delete("/api/games/{game_id}/sets/{set_id}")
    def api_game_set_rm(game_id: GameName, set_id: SetName) -> dict[str, Any]:
        return run_cli(["game", "set", "rm", game_id, set_id, "--yes"])

    # ---- print profiles ----------------------------------------------------
    # Reads are computed here (a profile is a file in the library, like a card);
    # every write goes through the CLI, so there is one implementation of what a
    # calibration round means.
    @app.get("/api/profiles")
    def api_profiles() -> dict[str, Any]:
        cfg = Config.load(lib.root)
        return {
            "active": cfg.print_profile,
            "active_back": cfg.print_back_profile,
            # what those two names actually resolve to in this library, so the
            # screen marks the right row (or no row, and says why below) rather
            # than string-comparing a name that may not be here at all
            "active_name": profiles.named(lib.root, cfg.print_profile),
            "active_back_name": profiles.named(lib.root, cfg.print_back_profile)
            if cfg.print_back_profile
            else None,
            # the same broken reference `frames check` reports, on the same terms:
            # `profile list` says it in the terminal, this says it here
            "dangling": [d.json() for d in profiles.dangling(lib.root, cfg)],
            "identity": profiles.NONE,
            "recipe_keys": list(media.RECIPE_KEYS),
            "recipe_range": [media.RECIPE_LOW, media.RECIPE_HIGH],
            "profiles": [p.summary() for p in profiles.listing(lib.root)],
        }

    @app.get("/api/profile/{name}")
    def api_profile(name: ProfileName) -> Any:
        """One profile in full — notes, recipe, and every round's patch pairs."""
        try:
            return profiles.resolve(lib.root, name).detail()
        except ProxdexError as exc:
            return _bad(str(exc))

    @app.post("/api/profile/{name}")
    def api_profile_new(name: ProfileName, body: ProfileBody) -> dict[str, Any]:
        return run_cli(["profile", "new", name, *body.argv()])

    @app.patch("/api/profile/{name}")
    def api_profile_set(name: ProfileName, body: ProfileBody) -> dict[str, Any]:
        args = body.argv()
        if not args:
            return {"ok": True, "log": "nothing to change"}
        return run_cli(["profile", "set", name, *args])

    @app.delete("/api/profile/{name}")
    def api_profile_rm(name: ProfileName) -> dict[str, Any]:
        return run_cli(["profile", "rm", name, "--yes"])

    @app.post("/api/profile/{name}/rename")
    def api_profile_rename(name: ProfileName, body: RenameBody) -> dict[str, Any]:
        return run_cli(["profile", "rename", name, body.name])

    @app.post("/api/profile/{name}/use")
    def api_profile_use(name: ProfileName) -> dict[str, Any]:
        return run_cli(["profile", "use", name])

    @app.get("/api/profile/{name}/preview")
    def api_profile_preview(name: ProfileName, card: str | None = None) -> Response:
        """Before/after on a real card, so numbers set by hand are not set blind."""
        args = ["profile", "preview", name]
        if card:
            if not _ID_OK.match(card):
                return _bad(f"{card}: not a card id")
            args += ["--card", card]
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "preview.png"
            res = run_cli([*args, "-o", str(png)])
            if not res["ok"] or not png.is_file():
                return JSONResponse({"ok": False, "log": res["log"]}, status_code=400)
            body = png.read_bytes()
        return Response(
            body, media_type="image/png", headers={"Cache-Control": "no-store"}
        )

    @app.get("/api/profile/{name}/strip")
    def api_profile_strip(
        name: ProfileName,
        vary: str = "saturation",
        steps: Annotated[int, Query(ge=2, le=12)] = 5,
        card: str | None = None,
    ) -> Response:
        """A printable page of one card at a row of values for one number."""
        if vary not in media.RECIPE_KEYS:
            return _bad(f"cannot vary {vary!r}")
        args = ["profile", "strip", name, "--vary", vary, "--steps", str(steps)]
        if card:
            if not _ID_OK.match(card):
                return _bad(f"{card}: not a card id")
            args += ["--card", card]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / f"{name}-{vary}.pdf"
            res = run_cli([*args, "-o", str(pdf)])
            if not res["ok"] or not pdf.is_file():
                return JSONResponse({"ok": False, "log": res["log"]}, status_code=400)
            body = pdf.read_bytes()
        return Response(
            body,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{name}-{vary}-strip.pdf"',
                "Cache-Control": "no-store",
            },
        )

    # ---- calibration rounds -------------------------------------------------
    @app.get("/api/calibrate/chart/{name}")
    def api_cal_chart(name: ProfileName, slot: str | None = None) -> Response:
        """The next round's chart, as a print-ready PDF."""
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / f"{name}-chart.pdf"
            args = ["calibrate", "chart", name, "-o", str(pdf)]
            if slot:
                args += ["--slot", slot]
            res = run_cli(args)
            if not res["ok"] or not pdf.is_file():
                return JSONResponse({"ok": False, "log": res["log"]}, status_code=400)
            # read it before the temp dir goes: FileResponse streams later
            body = pdf.read_bytes()
        return Response(
            body,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{name}-chart.pdf"',
                "Cache-Control": "no-store",
            },
        )

    @app.get("/api/calibrate/proof/{name}/{round_n}")
    def api_cal_proof(name: ProfileName, round_n: int) -> Response:
        """Target above, scanned below — what the paper did, patch by patch."""
        try:
            prof = profiles.resolve(lib.root, name)
        except ProxdexError as exc:
            return _bad(str(exc))
        rnd = prof.round(round_n)
        if rnd is None:
            return Response(status_code=404)
        buf = io.BytesIO()
        calibrate.proof_sheet(rnd.scanned).save(buf, "PNG")
        return Response(
            buf.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "no-cache"},
        )

    @app.post("/api/calibrate/add/{name}")
    def api_cal_add(
        name: ProfileName,
        file: Annotated[UploadFile, File()],
        slot: Annotated[str | None, Form(pattern=r"^[1-6],[1-6]$")] = None,
        whole: Annotated[bool, Form()] = False,
        note: Annotated[str, Form(max_length=2000)] = "",
    ) -> dict[str, Any]:
        tmp = _spool(file)
        try:
            args = ["calibrate", "add", name, "--scan", str(tmp)]
            if whole:
                args.append("--whole")
            elif slot:
                args += ["--slot", slot]
            if note:
                args += ["--note", note]
            return run_cli(args)
        finally:
            tmp.unlink(missing_ok=True)

    @app.post("/api/calibrate/round/{name}/{round_n}")
    def api_cal_switch(
        name: ProfileName, round_n: int, body: RoundBody
    ) -> dict[str, Any]:
        """Include or exclude one round. Nothing is deleted — that is the point."""
        verb = "enable" if body.enabled else "disable"
        return run_cli(["calibrate", verb, name, "--round", str(round_n)])

    # ---- SPA fallback (registered last, so it shadows nothing) --------------
    @app.get("/{route}", response_class=HTMLResponse)
    @app.get("/{route}/{rest:path}", response_class=HTMLResponse)
    def spa(route: str, rest: str = "") -> Response:  # noqa: ARG001 (path only)
        """Serve the shell for the client's own routes (`/card/ex3-90/upscale`).

        The UI navigates with the History API, so those paths have to survive a
        reload, a bookmark and a pasted link. Only the known route roots are
        served — anything else still 404s instead of rendering the app.
        """
        if route not in _SPA_ROUTES:
            return Response(status_code=404)
        return HTMLResponse(_HTML_PATH.read_text(encoding="utf-8"))

    return app


def app_from_env() -> FastAPI:
    """Factory for ``uvicorn --reload``: discovers the library from PROXDEX_ROOT."""
    root = os.environ.get("PROXDEX_ROOT")
    return create_app(Library.discover(explicit=Path(root) if root else None))


def _spool(file: UploadFile) -> Path:
    """Write an uploaded file to a temp path (sync — used in sync handlers)."""
    suffix = Path(file.filename or "upload.png").suffix or ".png"
    tmp = scratch.file(suffix)
    tmp.write_bytes(file.file.read())
    return tmp


def _field_options() -> dict[str, list[str]]:
    """Config fields whose values are a closed set → the values, for the UI.

    Reads the dataclass' own annotations, so a new enum-typed setting turns
    into a dropdown with no further wiring.
    """
    out: dict[str, list[str]] = {}
    for name, hint in get_type_hints(Config).items():
        # an optional setting is still a closed set — `GuideStyle | None` must draw the
        # same dropdown `GuideStyle` does, or the backs' guide style silently becomes a
        # free-text box you can typo
        bare = config.optional_of(hint) or hint
        if isinstance(bare, type) and issubclass(bare, Enum):
            out[name] = [str(m.value) for m in bare]
        elif get_origin(bare) is Literal:
            out[name] = [str(a) for a in get_args(bare)]
    return out


def _current_text(cfg: Config, opt: config.RunOption) -> str:
    """This library's value for one overridable setting, as a control reads it.

    The *library's*, not the dataclass' — a row's "default" has to be what leaving it
    alone will actually do, and a library that set `[sheet] cols = 4` would otherwise
    be told its default was 3 while printing 4.
    """
    value = getattr(cfg, opt.key)
    # an optional setting left unset has no text of its own — the control shows what
    # unset *does* (`RunOption.auto`) instead, so this must not offer the word "None"
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _flag_value(value: str | float | bool) -> str:
    """One override as the CLI reads it. ``%g`` for a float, so 1.5 stays 1.5 and
    8.0 becomes 8 rather than reaching argv as ``8.0`` for an int-typed flag."""
    if isinstance(value, bool):  # before the numeric case: a bool *is* an int
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _bad_override(body: SheetBody) -> str:
    """This run's overrides, checked at the boundary — `config.bad_run_value`."""
    return config.bad_run_value(config.Run.SHEET, body.overrides)


def _apply_overrides(cfg: Config, body: SheetBody) -> None:
    """This run's overrides on a loaded config — planning only, never written.

    The real run gets them by argv (`SheetBody.argv`), so the plan and the print are
    configured the same way and cannot drift. Not merely "the same way": literally the
    same function the CLI's `_overrides` calls.
    """
    config.apply_run(cfg, config.Run.SHEET, body.overrides)


def _side(face: int | None) -> list[str]:
    """``--face N``, or nothing at all when the request means "every side"."""
    return ["--face", str(face)] if face is not None else []


def _edges_json(
    marks: tuple[float, float, float, float] | None,
) -> dict[str, float] | None:
    """Four per-edge fractions as the ``{top, right, bottom, left}`` shape every other
    edge quadruple in this API uses; ``None`` when nothing was recorded."""
    if marks is None:
        return None
    top, right, bottom, left = marks
    return {"top": top, "right": right, "bottom": bottom, "left": left}


def _side_index(face: int | None) -> int:
    """A request's 1-based side as the 0-based index the library uses; the front
    when the request named none."""
    return FRONT if face is None else face - 1


def _csv(value: str | None) -> tuple[str, ...]:
    """A comma-separated query parameter as a tuple, blanks dropped.

    How a multi-pick filter travels in a URL — ``?type=Fire,Water`` — so the
    address bar stays readable and shareable, which is the whole reason the search
    screen keeps its query there.
    """
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


def _bad(log: str) -> JSONResponse:
    return JSONResponse({"ok": False, "log": log}, status_code=400)


def _guide_json(guide: frames.FrameGuide) -> dict[str, Any]:
    return {
        "id": guide.id,
        "name": guide.name,
        "game": guide.game,
        "inset": list(guide.inset),
        "mm": list(guide.mm()),
        "card_mm": list(guide.card_mm),
        "oversized": guide.oversized,
        "shipped": frames.is_shipped(guide.id),
        "frameless": guide.frameless,
    }


def _bad_setting(
    spec: steps.StepSpec, settings: dict[str, SettingValue], root: Path
) -> str | None:
    """What is wrong with these step settings, or ``None`` if nothing is.

    Every value is checked against the step's own schema here, at the boundary — an
    undeclared or malformed one is a 400, never an argv string that fails later
    inside an external tool, once per card.
    """
    for key, value in settings.items():
        option = spec.option(key)
        if option is None:
            return f"{spec.key} has no setting {key!r}"
        clean = option.coerce(value)
        if clean is None:
            return f"bad {key} {value!r}"
        # an OPEN option's values live in the library, not in an enum, so the
        # *existence* check happens here rather than in `coerce` — and it names
        # every option, which is exactly what a closed choice would have done
        if option.kind is steps.OptKind.OPEN:
            offered = [c.value for c in option.values(root)]
            if str(clean) not in offered:
                return (
                    f"no {option.label.lower()} {value!r} in this library. "
                    f"Known: {', '.join(offered) or 'none'}"
                )
    return None


def _resolution(reg: specs.Registry, card: Card) -> specs.Resolution:
    """The spec this card's border step will fit to, and why.

    The same call `cli._resolve_spec` makes, with the same arguments — so the
    align ghost, the contact-sheet chip and the fit that actually runs cannot
    disagree about which spec is in force.
    """
    return specs.resolve(
        reg,
        card.id,
        card.set_id,
        card.game,
        pin=card.pin,
        printing=card.printing_frame,
        traits=card.traits,
    )


def _unwrap(value: Any) -> Any:
    return value.unwrap() if hasattr(value, "unwrap") else value
