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
from enum import Enum
from pathlib import Path
from typing import (
    Annotated,
    Any,
    Literal,
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
    borders,
    calibrate,
    doctor,
    frames,
    games,
    imports,
    inventory,
    media,
    net,
    profiles,
    report,
    scratch,
    sources,
    specs,
    steps,
)
from proxdex import sheet as sheet_mod
from proxdex.config import Config, Faces, Orientation, PageSize
from proxdex.errors import ConfigError, ProxdexError
from proxdex.games import GameId
from proxdex.library import FRONT, STAGE_BY_LABEL, Card, Library, Step

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
    #: measure the inner border off the image instead of being told where it is
    auto: bool = False


class FetchBody(Body):
    ids: list[CardId] = Field(min_length=1, max_length=512)
    game: GameId | None = None
    face: Side | None = None
    #: also fetch the cards these are printed alongside — both meld halves and
    #: the melded card, the tokens they make
    related: bool = False


class FlipBody(Body):
    ids: list[CardId] = Field(min_length=1, max_length=512)
    face: Side | None = None


class SpecBody(Body):
    """One frame spec, as the frames screen states it.

    The four numbers are millimetres, like the CLI's — a border is a physical
    width, and nobody measures a fraction with calipers. There is no confidence
    field: a spec is its numbers plus a note about where they came from, and the
    grade that used to sit here called a border read off a publisher's scan
    trustworthy (see :mod:`proxdex.frames`).
    """

    id: SpecName
    name: str = Field(default="", max_length=80)
    game: GameId | None = None
    top: Annotated[float, Field(ge=0, le=20)]
    right: Annotated[float, Field(ge=0, le=20)]
    bottom: Annotated[float, Field(ge=0, le=20)]
    left: Annotated[float, Field(ge=0, le=20)]
    note: str = Field(default="", max_length=500)
    #: the numbers were taken off an oversized card, not a 63×88 one
    oversized: bool = False
    #: the card actually measured, when it was neither — a real Magic/Pokémon card
    #: is 63.5×88.9mm, and the insets are fractions of whatever was measured
    card_w: Annotated[float, Field(gt=0, le=200)] | None = None
    card_h: Annotated[float, Field(gt=0, le=300)] | None = None

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
            args += ["--game", self.game.value]
        if self.oversized:
            args.append("--oversized")
        if self.card_w:
            args += ["--card-w", f"{self.card_w:g}"]
        if self.card_h:
            args += ["--card-h", f"{self.card_h:g}"]
        if self.note:
            args += ["--note", self.note]
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
    game: GameId | None = None

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
            args += ["--game", self.game.value]
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
    faces: Faces | None = None
    page: PageSize | None = None
    orientation: Orientation | None = None
    dpi: Annotated[int, Field(ge=72, le=4800)] | None = None
    cols: Annotated[int, Field(ge=1, le=12)] | None = None
    rows: Annotated[int, Field(ge=1, le=12)] | None = None
    bleed: Annotated[float, Field(ge=0, le=20)] | None = None
    guides: bool | None = None
    profile: str | None = Field(default=None, max_length=64)
    #: card backs, when they land on a different medium than the fronts. Empty or
    #: absent means "the same profile", which is the ordinary case.
    back_profile: str | None = Field(default=None, max_length=64)
    notes: str = Field(default="", max_length=2000)

    def argv(self) -> list[str]:
        """This run as CLI arguments — the one place the flags are spelled."""
        args = [c.argv for c in self.cards]
        args += ["--faces", (self.faces or Faces.FRONTS).value]
        if self.profile:
            args += ["--profile", self.profile]
        if self.back_profile:
            args += ["--back-profile", self.back_profile]
        if self.page is not None:
            args += ["--page", self.page.value]
        if self.orientation is not None:
            args += ["--orientation", self.orientation.value]
        if self.dpi is not None:
            args += ["--dpi", str(self.dpi)]
        if self.cols is not None:
            args += ["--cols", str(self.cols)]
        if self.rows is not None:
            args += ["--rows", str(self.rows)]
        if self.bleed is not None:
            args += ["--bleed", f"{self.bleed:g}"]
        if self.guides is not None:
            args.append("--guides" if self.guides else "--no-guides")
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
    game: GameId | None = None
    stage: StageLabel | None = None
    face: Side | None = None

    def item(self) -> imports.Item:
        return imports.Item(
            name=self.name,
            id=self.id,
            game=self.game,
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


class RoundBody(Body):
    """Whether a calibration round feeds the fit."""

    enabled: bool


class BackBody(Body):
    game: GameId | None = None
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


def create_app(lib: Library) -> FastAPI:
    app = FastAPI(title="proxdex", docs_url=None, redoc_url=None)
    # the shell and the JSON payloads compress well; images are already coded
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    cfg_path = lib.root / "proxdex.toml"

    def run_cli(args: list[str]) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, "-m", "proxdex", "--root", str(lib.root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        return {"ok": proc.returncode == 0, "log": proc.stdout + proc.stderr}

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
        return {
            "root": str(lib.root),
            "sections": sections,
            "options": options,
            "docs": described,
        }

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
            "pages": [p.value for p in PageSize],
            "orientations": [o.value for o in Orientation],
            # what a sheet run defaults to, so the builder opens on this
            # library's own settings instead of inventing its own
            "sheet": {
                "faces": cfg.sheet_faces.value,
                "page": cfg.sheet_page.value,
                "orientation": cfg.sheet_orientation.value,
                "dpi": cfg.sheet_dpi,
                "cols": cfg.sheet_cols,
                "rows": cfg.sheet_rows,
                "bleed": cfg.bleed_mm,
                "guides": cfg.sheet_guides,
            },
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
            "games": [
                {"id": g.id.value, "name": g.name, "example": g.id_example}
                for g in games.GAMES.values()
            ],
            "default_game": lib.default_game.value,
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
                    "game": card.game.value,
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
        try:
            detail = sources.details(
                cid, Config.load(lib.root), card.game if card else None
            )
        except (requests.RequestException, ProxdexError) as exc:
            return {"error": str(exc)}
        return {
            "id": detail.meta.id,
            "name": detail.meta.name,
            "game": detail.meta.game.value,
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
        w, h = borders.size(src)
        reg = specs.load(lib.root)
        found = _resolution(reg, card)
        guide = found.spec
        return {
            "w": w,
            "h": h,
            "card_aspect": round(cfg.card_w_mm / cfg.card_h_mm, 3),
            "card_w_mm": cfg.card_w_mm,
            "card_h_mm": cfg.card_h_mm,
            "game": card.game.value,
            "game_name": games.get(card.game).name,
            # frame-size guide: inner border inset [top,right,bottom,left], plus
            # how much to trust it — the UI warns on an unmeasured set.
            "guide": _guide_json(guide) if guide else None,
            "guides": [_guide_json(g) for g in reg.choices(card.game)],
            # which of the seven ways this spec was arrived at, and anything the
            # align panel has to say out loud about it
            "resolution": found.json(),
            "pin": card.pin,
        }

    @app.get("/api/detect/{cid}")
    def api_detect(
        cid: str, stage: str | None = None, face: int = FRONT
    ) -> dict[str, Any]:
        """Measure where this side's printed border ends, for the align marks.

        Read-only and cheap, so the border panel can offer "measure it" and the
        marks land somewhere real before anyone drags them. The per-edge support
        travels with the numbers: the UI flags the edges the scan lines disagreed
        about rather than presenting all four as equally certain.
        """
        card = lib.find(cid)
        if card is None:
            return {"error": f"{cid}: not in this library"}
        st = STAGE_BY_LABEL.get(stage) if stage else None
        src = (
            card.stage_path(st, face)
            if st and card.has(st, face)
            else card.best(*_BEST, face=face)
        )
        if src is None or not src.exists():
            return {"error": "no image to measure"}
        guide = _resolution(specs.load(lib.root), card).spec
        if guide is None:
            return {
                "error": "no frame spec has been measured for this printing — "
                "record one with `proxdex frames set`, or pick one for this run"
            }
        if guide.frameless:
            return {
                "inset": [0.0, 0.0, 0.0, 0.0],
                "support": [1.0, 1.0, 1.0, 1.0],
                "weak": [],
                "reliable": True,
                "frameless": True,
                "note": f"{guide.name} — nothing to measure, so the fit is pure "
                "aspect correction.",
            }
        found = borders.detect_inset(src)
        return {
            "inset": list(found.inset),
            "support": list(found.support),
            "weak": list(found.weak),
            "reliable": found.reliable,
            "frameless": found.frameless,
            "note": found.note,
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
            key = (card.game.value, card.set_id)
            row = held.setdefault(
                key,
                {
                    "game": card.game.value,
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
        q: str,
        game: str | None = None,
        set_filter: Annotated[str | None, Query(alias="set")] = None,
        rarity: str | None = None,
        year: str | None = None,
        limit: int = 60,
    ) -> Any:
        cfg = Config.load(lib.root)
        want = games.coerce(game, cfg.library_game)
        try:
            found = sources.search(
                q,
                cfg,
                want,
                set_filter=set_filter,
                rarity=rarity,
                year=year,
                limit=limit,
            )
        except (requests.RequestException, ProxdexError) as exc:
            return {"error": f"search failed (try again): {exc}"}
        return [
            {
                "id": r.id,
                "name": r.name,
                "game": r.game.value,
                "set": r.set_name,
                "year": r.year,
                "number": f"{r.number}/{r.printed_total}"
                if r.printed_total
                else r.number,
                "rarity": r.rarity,
                "artist": r.artist,
                "image": r.image_url,
                "have": lib.find(r.id) is not None,
            }
            for r in found
        ]

    @app.post("/api/fetch")
    def api_fetch(body: FetchBody) -> dict[str, Any]:
        args = ["fetch", *body.ids, *_side(body.face)]
        if body.game is not None:
            args += ["--game", body.game.value]
        if body.related:
            args.append("--related")
        return run_cli(args)

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
        game: Annotated[GameId | None, Form()] = None,
        face: Annotated[int | None, Form(ge=1, le=_MAX_FACE)] = None,
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
                args += ["--game", game.value]
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
            if body.auto:  # measure the inner border off the image
                args.append("--auto")
            elif body.inner is not None:  # marked border edges → spec-based fit
                for edge, val in body.inner:
                    args += [f"--inner-{edge}", f"{val:g}"]
            elif body.grow is not None:  # plain per-edge growth, no fit
                for edge, val in body.grow:
                    args += [f"--{edge}", f"{val:g}"]
        if body.force:
            args.append("--force")
        return run_cli(args)

    @app.post("/api/flip")
    def api_flip(body: FlipBody) -> Any:
        """Choose which side of a two-sided card prints on the front."""
        return run_cli(["flip", *body.ids, *_side(body.face)])

    # ---- produce -----------------------------------------------------------
    @app.post("/api/sheet")
    def api_sheet(body: SheetBody) -> dict[str, Any]:
        """Impose the run, and say which PDF came out of it.

        ``--no-open`` is not a preference here, it is a correction: `sheet`'s
        `[sheet] open` would launch a PDF viewer on the machine running the
        server, which is not the machine you are looking at. The browser's own
        equivalent is the link this returns — see `_written`.
        """
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
        args = ["back", "--game", game.value]
        if body.url:
            return run_cli([*args, "--url", body.url])
        if games.get(game).back_url is None:
            return {
                "ok": False,
                "log": f"no downloadable back for {games.get(game).name} — "
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
            return run_cli(["back", "--game", want.value, "--file", str(tmp)])
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
        if isinstance(hint, type) and issubclass(hint, Enum):
            out[name] = [str(m.value) for m in hint]
        elif get_origin(hint) is Literal:
            out[name] = [str(a) for a in get_args(hint)]
    return out


def _apply_overrides(cfg: Config, body: SheetBody) -> None:
    """This run's overrides on a loaded config — planning only, never written.

    The real run gets them by argv (`SheetBody.argv`), so the plan and the print
    are configured the same way and cannot drift.
    """
    if body.faces is not None:
        cfg.sheet_faces = body.faces
    if body.page is not None:
        cfg.sheet_page = body.page
    if body.orientation is not None:
        cfg.sheet_orientation = body.orientation
    if body.dpi is not None:
        cfg.sheet_dpi = body.dpi
    if body.cols is not None:
        cfg.sheet_cols = body.cols
    if body.rows is not None:
        cfg.sheet_rows = body.rows
    if body.bleed is not None:
        cfg.bleed_mm = body.bleed
    if body.guides is not None:
        cfg.sheet_guides = body.guides


def _side(face: int | None) -> list[str]:
    """``--face N``, or nothing at all when the request means "every side"."""
    return ["--face", str(face)] if face is not None else []


def _bad(log: str) -> JSONResponse:
    return JSONResponse({"ok": False, "log": log}, status_code=400)


def _guide_json(guide: frames.FrameGuide) -> dict[str, Any]:
    return {
        "id": guide.id,
        "name": guide.name,
        "game": guide.game.value if guide.game else None,
        "inset": list(guide.inset),
        "mm": list(guide.mm()),
        "ref_mm": list(guide.ref_mm),
        "shipped": frames.is_shipped(guide.id),
        "frameless": guide.frameless,
        "note": guide.note,
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
