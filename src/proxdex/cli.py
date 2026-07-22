"""Command-line interface (click + rich-click)."""

from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TypeVar, cast

import numpy as np
import rich_click as click
from numpy.typing import NDArray
from PIL import Image
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
)
from rich.table import Table

from . import bleed, borders, frames, media, report, sources
from . import calibrate as calibrate_mod
from . import grade as grade_mod
from . import sheet as sheet_mod
from . import upscale as upscale_mod
from ._version import __version__
from .config import Config
from .errors import FileError, ProxdexError
from .library import Card, Library, Stage, slugify

click.rich_click.USE_RICH_MARKUP = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.STYLE_OPTIONS_TABLE_LEADING = 0
click.rich_click.COMMAND_GROUPS = {
    "proxdex": [
        {"name": "Library", "commands": ["init", "where", "ls", "index", "ui"]},
        {"name": "Acquire", "commands": ["search", "fetch", "import"]},
        {"name": "Prepare", "commands": ["border", "upscale", "grade"]},
        {"name": "Produce", "commands": ["build", "back", "sheet", "printed"]},
        {"name": "Calibrate", "commands": ["calibrate"]},
    ]
}

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

T = TypeVar("T")

_STAGES = (Stage.ORIGINAL, Stage.BORDERED, Stage.UPSCALED, Stage.EDITED)
_STAGE_BY_LABEL = {s.label: s for s in Stage}

DEFAULT_TOML = """\
# proxdex library config — tune here, no code edits needed.

# `proxdex border` only EXPANDS edges (via cardbleed) — no auto edge detection,
# no auto aspect fix. Give the growth per edge: `proxdex border <id>
# --top/--bottom/--left/--right <mm>`, or use the UI frame-align tool.

[grade]
# 1) normalize: pull each card to a common baseline first (so scans and
#    digital art match) — white-balance the frame + even out black/white points.
normalize = true
black_pct = 0.5             # luminance percentile mapped to black
white_pct = 99.5            # luminance percentile mapped to white
level_strength = 0.6        # how hard to pull toward those points (0=off, 1=full)
# Frame white-balance target. [] = use the library's own median frame colour;
# or pin it, e.g. [252, 214, 46], so all cards converge on that yellow.
match_border_target = []
# 2) look: one identical recipe on top → uniform prints. Printers and matte
#    paper dull the image, so the defaults lift it slightly.
brightness = 1.03
contrast   = 1.06
saturation = 1.10
gamma      = 1.0

[card]
w_mm = 63.0
h_mm = 88.0

[sources]
bleed_mm = 2.5              # cut bleed added to every edge by cardbleed

[sheet]
# proxdex imposes the trim-size masters into the print PDF: each card is sized
# to the actual card size, colour-corrected for the medium, then cut bleed is
# extended OUTSIDE the trim (cut guides sit at the card edge). It owns the whole
# path to paper, so calibration transfers. Print with colour management OFF.
page        = "a4"         # a4 | letter
orientation = "portrait"   # portrait | landscape
dpi         = 1400         # high so the printer never upsamples; PDF stays lossless
cols        = 3
rows        = 3
margin_mm   = 5.0
spacing_mm  = 0.0          # gap between cards, x
spacing_y_mm = 0.0
# How the trim master maps to the exact card cell (see [card]) at this dpi.
# stretch = force to the exact cell (default): the border step sized the card
# per-axis to 63x88mm, so its pixels may not be exactly 63:88 and must fill the
# cell 1:1. cover = fill preserving aspect (center-crops overflow); contain =
# whole image + white pad. Use cover/contain only for raw imports, not masters.
fit = "stretch"

# what to output
faces       = "fronts"     # fronts | backs | duplex
duplex_flip = "long"       # long | short print-flip edge (mirrors the backs)
back_image  = ""           # shared card back; or per-card <id>_back.png
open        = false         # open the PDF after writing

# offsets (mm) — nudge the whole image; back offset aligns duplex front/back
front_offset_x_mm = 0.0
front_offset_y_mm = 0.0
back_offset_x_mm  = 0.0
back_offset_y_mm  = 0.0

# cut guides
guides          = true
guide_style     = "corners"  # full (grid lines) | corners (crop marks) | none
guide_placement = "outside"  # outside | inside the trim
guide_mm        = 4.0        # crop-mark length
guide_color     = "#00ff00"
guide_width_mm  = 0.3
guides_front    = true
guides_back     = false      # cut from the front, so back guides usually off

# registration marks (printer front/back alignment)
reg_marks    = "none"        # none | corners
reg_inset_mm = 10.0

[print]
# Colour reproduction applied at sheet time (the stored master stays neutral),
# per medium. A preset here is just training wheels until you `proxdex
# calibrate` the medium — a measured calibration then supersedes it.
# "none" | "paper" | "foil".
profile = "foil"
# saturation = 1.38
# contrast   = 1.16
# brightness = 0.95
# gamma      = 0.88        # < 1 darkens midtones → more ink density

[tools]
# Upscayl (the upscale stage). On macOS the bundled binary and models are
# auto-detected; set explicit paths on other platforms.
upscayl_model = "digital-art-4x"  # or ultrasharp-4x, remacri-4x, high-fidelity-4x, ...
upscayl_scale = 2                 # 1, 2, 3, or 4
upscayl_double = true             # run the model twice (2x doubled = 4x, up to 16x)
# upscayl_bin    = "/Applications/Upscayl.app/Contents/Resources/bin/upscayl-bin"
# upscayl_models = "/Applications/Upscayl.app/Contents/Resources/models"
"""


# --------------------------------------------------------------- helpers -----
def _lib(ctx: click.Context) -> Library:
    root = ctx.obj.get("root")
    return Library.discover(explicit=Path(root) if root else None)


def _dots(card: Card) -> str:
    return " ".join("[green]✓[/]" if card.has(s) else "[dim]·[/]" for s in _STAGES)


def _reindex(lib: Library) -> None:
    """Refresh INDEX.md after a state change; never break the command over it."""
    with contextlib.suppress(Exception):
        report.write_index(lib)


def _each(items: Sequence[T], fn: Callable[[T], None], verb: str) -> int:
    """Run ``fn`` over items with a progress bar; skip per-item FileErrors."""
    failed = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
        transient=True,
        disable=len(items) < 3,
    ) as progress:
        task = progress.add_task(verb, total=len(items))
        for item in items:
            progress.update(task, description=str(item))
            try:
                fn(item)
            except FileError as e:
                err.print(f"[yellow]SKIPPED[/] {e}")
                failed += 1
            progress.advance(task)
    return failed


# ------------------------------------------------------------------ cli ------
@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--root",
    default=None,
    metavar="DIR",
    help="Library folder (default: search up from the current directory).",
)
@click.version_option(__version__, "-V", "--version")
@click.pass_context
def cli(ctx: click.Context, root: str | None) -> None:
    """[bold]proxdex[/] — organize and drive your Pokémon proxy pipeline.

    A card flows through four stages: [cyan]original[/] → [cyan]bordered[/] →
    [cyan]upscaled[/] → [cyan]edited[/] (the trim master); bleed and colour are
    added at [cyan]sheet[/] time. proxdex fetches sources, files each
    stage in a predictable place, corrects thin frames, and tracks what you've
    actually printed.

    [dim]Examples:[/]

    [dim]  proxdex fetch ex3-90 ex6-105[/]

    [dim]  proxdex build && proxdex sheet my-deck[/]
    """
    ctx.obj = {"root": root}


@cli.command()
@click.argument(
    "path", required=False, type=click.Path(file_okay=False, path_type=Path)
)
@click.pass_context
def init(ctx: click.Context, path: Path | None) -> None:
    """Create a new library here (or at PATH): cards/, print-batches/, config."""
    root_opt = ctx.obj.get("root")
    root = (path or (Path(root_opt) if root_opt else Path.cwd())).resolve()
    (root / "cards").mkdir(parents=True, exist_ok=True)
    (root / "print-batches").mkdir(parents=True, exist_ok=True)
    marker = root / "proxdex.toml"
    if marker.exists():
        console.print(f"[yellow]already a library:[/] {root}")
        return
    marker.write_text(DEFAULT_TOML)
    console.print(f"[green]initialized[/] proxdex library at [bold]{root}[/]")


def _card_from_meta(lib: Library, meta: sources.CardMeta) -> Card:
    """Find the card, or create its correctly-named folder from metadata."""
    card = lib.find(meta.id)
    if card is not None:
        return card
    set_dir = lib.set_dir(meta.set_id, meta.set_name)
    card_dir = set_dir / f"{meta.id}_{slugify(meta.name)}"
    card_dir.mkdir(parents=True, exist_ok=True)
    return Card(id=meta.id, dir=card_dir, set_id=meta.set_id)


def _ensure_card(lib: Library, cfg: Config, cid: str) -> Card:
    """Find the card, or look up its metadata and create the folder."""
    card = lib.find(cid)
    if card is not None:
        return card
    return _card_from_meta(lib, sources.lookup(cid, cfg))


def _acquire(lib: Library, cfg: Config, meta: sources.CardMeta, force: bool) -> None:
    """Create the card folder if needed and download its stage-1 original."""
    card = _card_from_meta(lib, meta)
    dst = card.stage_path(Stage.ORIGINAL)
    if dst.exists() and not force:
        console.print(f"[dim]· {meta.id} {meta.name}: original exists[/]")
        return
    sources.download_large(meta.id, cfg).save(dst)
    console.print(
        f"[green]✓[/] {meta.id:<9} {meta.name:<18} → {dst.relative_to(lib.root)}"
    )


@cli.command()
@click.argument("ids", nargs=-1, required=True, metavar="ID...")
@click.option("--force", is_flag=True, help="Re-download even if the original exists.")
@click.pass_context
def fetch(ctx: click.Context, ids: tuple[str, ...], force: bool) -> None:
    """Download originals by id from scrydex + names/sets from the TCG API.

    IDs are canonical TCG ids, e.g. [cyan]ex3-90[/] or [cyan]ex15-94[/]. Don't
    know the id? Use [cyan]proxdex search[/] instead.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    _each(
        ids, lambda cid: _acquire(lib, cfg, sources.lookup(cid, cfg), force), "fetching"
    )
    _reindex(lib)


@cli.command()
@click.argument("query", nargs=-1, required=True, metavar="QUERY...")
@click.option(
    "--set", "set_filter", metavar="SET", help="Set id (ex4) or name substring."
)
@click.option("--rarity", metavar="TEXT", help="Keep only rarities containing TEXT.")
@click.option("--year", metavar="YYYY", help="Keep only cards released that year.")
@click.option("--limit", default=100, show_default=True, help="Max results to request.")
@click.option(
    "--select",
    "selection",
    metavar="SPEC",
    help="Skip the prompt and fetch this selection (e.g. [cyan]1,3-5[/] or an id).",
)
@click.option("-f", "--fetch", "fetch_all", is_flag=True, help="Fetch every result.")
@click.option(
    "--open", "open_images", is_flag=True, help="Open result images in the browser."
)
@click.option("--force", is_flag=True, help="Re-download even if the original exists.")
@click.pass_context
def search(
    ctx: click.Context,
    query: tuple[str, ...],
    set_filter: str | None,
    rarity: str | None,
    year: str | None,
    limit: int,
    selection: str | None,
    fetch_all: bool,
    open_images: bool,
    force: bool,
) -> None:
    """Search cards by name, then pick which to fetch.

    Shows matches with set, year, collector number, rarity and artist so you
    can tell prints apart, then downloads the ones you choose.

    [dim]Examples:[/]

    [dim]  proxdex search entei ex[/]

    [dim]  proxdex search charizard --set base1 --rarity holo[/]
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    text = " ".join(query)
    results = sources.search(
        text, cfg, set_filter=set_filter, rarity=rarity, year=year, limit=limit
    )
    if not results:
        console.print(f"[yellow]no matches for[/] {text!r}")
        return
    _print_results(results)
    if open_images:
        import webbrowser

        for result in results[:12]:
            webbrowser.open(cfg.scrydex_url.format(id=result.id))

    if fetch_all:
        chosen = results
    elif selection is not None:
        chosen = _parse_selection(selection, results)
    elif sys.stdin.isatty():
        raw = click.prompt(
            "Fetch which? [numbers/ranges/ids · 'all' · blank to cancel]",
            default="",
            show_default=False,
        )
        chosen = _parse_selection(raw, results)
    else:
        console.print("[dim]non-interactive — re-run with --select or --fetch.[/]")
        return
    if not chosen:
        console.print("[dim]nothing selected.[/]")
        return
    _each(chosen, lambda r: _acquire(lib, cfg, r.to_meta(), force), "fetching")
    _reindex(lib)


def _print_results(results: Sequence[sources.SearchResult]) -> None:
    table = Table(box=None, pad_edge=False, header_style="bold")
    table.add_column("#", justify="right", style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Name")
    table.add_column("Set")
    table.add_column("Year", justify="right")
    table.add_column("No.", justify="right")
    table.add_column("Rarity")
    table.add_column("Artist")
    for i, r in enumerate(results, 1):
        num = f"{r.number}/{r.printed_total}" if r.printed_total else r.number
        table.add_row(str(i), r.id, r.name, r.set_name, r.year, num, r.rarity, r.artist)
    console.print(table)


def _parse_selection(
    text: str, results: Sequence[sources.SearchResult]
) -> list[sources.SearchResult]:
    """Turn a selection spec into result objects.

    Understands 1-based indices, ``a-b`` ranges, literal ids, and ``all``.
    """
    text = text.strip().lower()
    if not text or text in {"q", "quit", "cancel"}:
        return []
    if text == "all":
        return list(results)
    by_id = {r.id.lower(): r for r in results}
    picked: dict[str, sources.SearchResult] = {}
    for token in re.split(r"[,\s]+", text):
        if not token:
            continue
        range_match = re.fullmatch(r"(\d+)-(\d+)", token)
        if range_match:
            lo, hi = int(range_match[1]), int(range_match[2])
            for i in range(lo, hi + 1):
                if 1 <= i <= len(results):
                    picked[results[i - 1].id] = results[i - 1]
        elif token.isdigit():
            i = int(token)
            if 1 <= i <= len(results):
                picked[results[i - 1].id] = results[i - 1]
            else:
                err.print(f"[yellow]skip[/] {i}: out of range")
        elif token in by_id:
            picked[by_id[token].id] = by_id[token]
        else:
            err.print(f"[yellow]skip[/] {token!r}: not a listed number or id")
    return list(picked.values())


@cli.command(name="import")
@click.argument("paths", nargs=-1, required=True, metavar="PATH...")
@click.option(
    "--id",
    "cid",
    metavar="CARD_ID",
    help="Assign this TCG id to the file(s); looks up name/set and creates the "
    "card folder if missing. Use when the filename has no id.",
)
@click.option(
    "--stage",
    type=click.Choice([s.label for s in Stage]),
    default=None,
    help="Target stage (default: guessed — 'upscayl' in the name → upscaled, "
    "else original).",
)
@click.option("--move", is_flag=True, help="Move files instead of copying them.")
@click.pass_context
def import_(
    ctx: click.Context,
    paths: tuple[str, ...],
    cid: str | None,
    stage: str | None,
    move: bool,
) -> None:
    """File loose images (e.g. an Upscayl output folder) into card stages.

    With no [cyan]--id[/], the card id is read from each filename and the card
    folder must already exist. With [cyan]--id[/] the metadata is looked up and
    the folder created on the fly, so you can import an arbitrarily-named scan:

    [dim]  proxdex import my-scan.png --id ex6-105 --stage original[/]
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    forced_stage = _STAGE_BY_LABEL[stage] if stage else None
    files: list[Path] = [
        Path(match)
        for pattern in paths
        # glob.glob handles user-supplied shell patterns (e.g. ~/dump/*.png)
        for match in glob.glob(str(Path(pattern).expanduser()))  # noqa: PTH207
    ]

    def one(f: Path) -> None:
        file_cid = cid or _card_id_from(f.stem)
        if file_cid is None:
            raise FileError(f"{f.name}: no card id in filename (pass --id)")
        card = _ensure_card(lib, cfg, file_cid) if cid else lib.find(file_cid)
        if card is None:
            raise FileError(
                f"{file_cid}: no card folder — pass --id to create it, or "
                f"`proxdex fetch {file_cid}` first"
            )
        target = forced_stage or (
            Stage.UPSCALED if "upscayl" in f.name.lower() else Stage.ORIGINAL
        )
        dst = card.stage_path(target)
        (shutil.move if move else shutil.copy2)(str(f), str(dst))
        console.print(
            f"[green]✓[/] {f.name} → {dst.relative_to(lib.root)} "
            f"[dim](stage {target.value} {target.label})[/]"
        )

    if not files:
        raise click.UsageError("no files matched")
    _each(files, one, "importing")
    _reindex(lib)


@cli.command()
@click.pass_context
def where(ctx: click.Context) -> None:
    """Show the active library root and config (which one am I operating on?)."""
    lib = _lib(ctx)
    cfg_file = lib.root / "proxdex.toml"
    mark = "[green]✓[/]" if cfg_file.exists() else "[red]missing[/]"
    console.print(f"[bold]library[/]  {lib.root}")
    console.print(f"config    {cfg_file} {mark}")
    if env := os.environ.get("PROXDEX_ROOT"):
        console.print(f"[dim]PROXDEX_ROOT={env}[/]")


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8756, show_default=True)
@click.option("--no-open", is_flag=True, help="Don't open a browser tab.")
@click.option(
    "--reload",
    is_flag=True,
    help="Auto-restart on code changes (dev; run from the repo).",
)
@click.pass_context
def ui(ctx: click.Context, host: str, port: int, no_open: bool, reload: bool) -> None:
    """Launch the local web UI (card gallery, build/sheet, previews)."""
    lib = _lib(ctx)
    try:
        import uvicorn

        from .webui import create_app
    except ModuleNotFoundError as exc:
        raise ProxdexError(
            "the web UI needs extra deps — install with "
            '`uv tool install "proxdex[ui]"` (or `pip install "proxdex[ui]"`)'
        ) from exc
    url = f"http://{host}:{port}"
    if not no_open:
        import threading
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    console.print(f"[green]proxdex UI[/] → [bold]{url}[/]  [dim](Ctrl-C to stop)[/]")
    if reload:
        os.environ["PROXDEX_ROOT"] = str(lib.root)
        uvicorn.run(
            "proxdex.webui:app_from_env",
            factory=True,
            reload=True,
            host=host,
            port=port,
            log_level="warning",
        )
    else:
        uvicorn.run(create_app(lib), host=host, port=port, log_level="warning")


@cli.command()
@click.pass_context
def ls(ctx: click.Context) -> None:
    """List every card with its stage progress and print status."""
    lib = _lib(ctx)
    by_card = report.card_batch_index(lib)
    table = Table(box=None, pad_edge=False, header_style="bold")
    table.add_column("Card")
    table.add_column("Name")
    table.add_column("Set")
    table.add_column("O B U E", justify="center")
    table.add_column("Batch")
    table.add_column("Printed", justify="center")
    for card in lib.cards():
        batch = by_card.get(card.id)
        table.add_row(
            card.id,
            card.name.title(),
            card.set_id,
            _dots(card),
            batch.name if batch else "",
            "[green]✓[/]" if batch and batch.printed else "",
        )
    console.print(table)
    console.print("[dim]stages: O original · B bordered · U upscaled · E edited[/]")


@cli.command()
@click.argument("ids", nargs=-1, metavar="[ID...]")
@click.option(
    "--model",
    type=click.Choice(upscale_mod.MODELS),
    default=None,
    help="Upscayl model (default from config: [cyan]digital-art-4x[/]).",
)
@click.option(
    "--scale",
    type=click.IntRange(1, 4),
    default=None,
    help="Output scale 1-4 (default from config: [cyan]2[/]).",
)
@click.option(
    "--double/--no-double",
    "double",
    default=None,
    help="Double Upscayl: run the model twice (2× → 4×, up to 16×).",
)
@click.option("--force", is_flag=True, help="Re-upscale even if it exists.")
@click.pass_context
def upscale(
    ctx: click.Context,
    ids: tuple[str, ...],
    model: str | None,
    scale: int | None,
    double: bool | None,
    force: bool,
) -> None:
    """Upscale with Upscayl → stage 3 (upscaled), after any border fix.

    Runs on the bordered image if present, else the original — so frame
    expansion happens first. Needs Upscayl installed (its bundled
    [cyan]upscayl-bin[/] is auto-detected on macOS). Mirrors the app's own
    options; defaults live under [cyan][tools][/].
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    use_model = model or cfg.upscayl_model
    use_scale = cfg.upscayl_scale if scale is None else scale
    use_double = cfg.upscayl_double if double is None else double
    tag = f"{use_model} ×{use_scale}{' ×2' if use_double else ''}"

    def one(card: Card) -> None:
        src = card.best(Stage.BORDERED, Stage.ORIGINAL)
        if src is None:
            raise FileError(f"{card.id}: no original yet (fetch it first)")
        dst = card.stage_path(Stage.UPSCALED)
        if dst.exists() and not force:
            console.print(f"[dim]· {card.id}: already upscaled[/]")
            return
        upscale_mod.run(
            src, dst, cfg, model=use_model, scale=use_scale, double=use_double
        )
        console.print(
            f"[green]✓[/] {card.id}: upscaled [dim]({tag})[/] → "
            f"{dst.relative_to(lib.root)}"
        )

    _each(lib.select(ids), one, "upscaling")
    _reindex(lib)


@cli.command()
@click.argument("ids", nargs=-1, metavar="[ID...]")
@click.option(
    "--normalize/--no-normalize",
    "normalize",
    default=None,
    help="Pull each card to a common baseline before the recipe (default on).",
)
@click.option("--force", is_flag=True, help="Re-grade even if stage 3 exists.")
@click.pass_context
def grade(
    ctx: click.Context, ids: tuple[str, ...], normalize: bool | None, force: bool
) -> None:
    """Normalize each card to a common baseline, then apply the uniform look.

    Normalization white-balances the card frame and evens out black/white
    points so scanned and digitally-drawn cards start from the same place;
    then one identical recipe (saturation/contrast) makes the batch print
    uniformly. Writes stage 4 (edited) — the trim-size master. Tune both under
    [cyan][grade][/].
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    do_norm = cfg.grade_normalize if normalize is None else normalize
    # dynamic target: the collection's own median frame colour (unless pinned)
    frame_target = None
    if do_norm and not cfg.match_border_target:
        frame_target = _library_frame_target(lib)

    def one(card: Card) -> None:
        dst = card.stage_path(Stage.EDITED)
        if dst.exists() and not force:
            console.print(f"[dim]· {card.id}: already graded[/]")
            return
        src = card.best(Stage.UPSCALED, Stage.BORDERED, Stage.ORIGINAL)
        if src is None:
            raise FileError(f"{card.id}: nothing to grade yet")
        out = grade_mod.grade(
            Image.open(src), cfg, frame_target=frame_target, normalize=do_norm
        )
        out.save(dst)
        console.print(f"[green]✓[/] {card.id}: graded → {dst.relative_to(lib.root)}")

    _each(lib.select(ids), one, "grading")
    _reindex(lib)


def _library_frame_target(lib: Library) -> tuple[float, float, float] | None:
    """Median frame colour across the whole library — the consensus to aim at."""
    colors: list[NDArray[np.float32]] = []
    for card in lib.cards():
        src = card.best(Stage.UPSCALED, Stage.BORDERED, Stage.ORIGINAL)
        if src is not None:
            colors.append(borders.frame_color(borders.load_rgb(src)))
    if not colors:
        return None
    median = np.median(np.stack(colors), axis=0)
    return (float(median[0]), float(median[1]), float(median[2]))


@cli.command()
@click.argument("ids", nargs=-1, metavar="[ID...]")
@click.option(
    "--top", "top_mm", type=float, default=0.0, help="Expand the top edge (mm)."
)
@click.option(
    "--bottom", "bottom_mm", type=float, default=0.0, help="Expand bottom (mm)."
)
@click.option("--left", "left_mm", type=float, default=0.0, help="Expand left (mm).")
@click.option("--right", "right_mm", type=float, default=0.0, help="Expand right (mm).")
@click.option("--inner-top", type=float, default=None, help="Inner frac (top).")
@click.option("--inner-right", type=float, default=None, help="Inner frac (right).")
@click.option("--inner-bottom", type=float, default=None, help="Inner frac (bottom).")
@click.option("--inner-left", type=float, default=None, help="Inner frac (left).")
@click.option("--force", is_flag=True, help="Re-run even if a bordered image exists.")
@click.option("--dry-run", is_flag=True, help="Report the per-edge plan; don't write.")
@click.pass_context
def border(
    ctx: click.Context,
    ids: tuple[str, ...],
    top_mm: float,
    bottom_mm: float,
    left_mm: float,
    right_mm: float,
    inner_top: float | None,
    inner_right: float | None,
    inner_bottom: float | None,
    inner_left: float | None,
    force: bool,
    dry_run: bool,
) -> None:
    """Expand a card's edges → stage 2 (bordered), before upscaling.

    Only ever *adds* border (via [cyan]cardbleed[/]) — no auto edge detection.
    Two ways to say how much:

    • [cyan]--top/--bottom/--left/--right[/] <mm>: grow each edge by that much.

    • [cyan]--inner-top/-right/-bottom/-left[/] <fraction 0-1>: where the card's
    inner border edge currently sits; from the set's era spec proxdex computes
    the growth that hits the exact card aspect AND correct border widths.

    [cyan]--dry-run[/] reports the plan without writing.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    inner = (inner_top, inner_right, inner_bottom, inner_left)
    use_inner = any(v is not None for v in inner)
    if use_inner and not all(v is not None for v in inner):
        raise click.UsageError("give all four --inner-top/-right/-bottom/-left or none")
    edges = {
        "top_mm": top_mm,
        "bottom_mm": bottom_mm,
        "left_mm": left_mm,
        "right_mm": right_mm,
    }

    def one(card: Card) -> None:
        dst = card.stage_path(Stage.BORDERED)
        if dst.exists() and not force and not dry_run:
            console.print(f"[dim]· {card.id}: already bordered[/]")
            return
        src = card.stage_path(Stage.ORIGINAL)
        if not src.exists():
            raise FileError(f"{card.id}: no original yet (fetch it first)")
        w, h = borders.size(src)
        note = ""
        if use_inner:
            guide = frames.for_set(card.set_id)
            inner_t = cast("tuple[float, float, float, float]", inner)
            sol = frames.solve_extension(
                w, h, inner_t, guide, cfg.card_w_mm, cfg.card_h_mm
            )
            ext = bleed.plan(
                w,
                cfg,
                top_mm=sol.top,
                bottom_mm=sol.bottom,
                left_mm=sol.left,
                right_mm=sol.right,
            )
            note = f" [dim]({guide.name})[/]"
            if sol.over_target:
                over = ", ".join(sol.over_target)
                note += f" [yellow](over spec on {over})[/]"
        else:
            ext = bleed.plan(w, cfg, **edges)
        plan = f"+top{ext.top} +bottom{ext.bottom} +left{ext.left} +right{ext.right}px"
        if max(ext.top, ext.bottom, ext.left, ext.right) == 0:
            console.print(f"[dim]· {card.id}: nothing to expand[/]")
            return
        if dry_run:
            console.print(f"[cyan]{card.id}[/]: {plan}{note}")
            return
        bleed.run(src, dst, ext, cfg)
        rel = dst.relative_to(lib.root)
        console.print(f"[green]✓[/] {card.id}: {plan}{note} → {rel}")

    _each(lib.select(ids), one, "bordering")
    if not dry_run:
        _reindex(lib)


@cli.command()
@click.pass_context
def index(ctx: click.Context) -> None:
    """Regenerate INDEX.md from the cards and print batches on disk."""
    lib = _lib(ctx)
    dst = report.write_index(lib)
    console.print(f"[green]wrote[/] {dst}")


@cli.command()
@click.argument("ids", nargs=-1, metavar="[ID...]")
@click.option("--force", is_flag=True, help="Redo stages even if they exist.")
@click.pass_context
def build(ctx: click.Context, ids: tuple[str, ...], force: bool) -> None:
    """Prepare cards into trim-size masters: border → upscale → grade.

    Skips stages already present (unless [cyan]--force[/]). Bleed and colour
    reproduction are not baked in here — they're applied at [cyan]sheet[/] time.
    """
    lib = _lib(ctx)
    cards = lib.select(ids)
    if not cards:
        console.print("[dim]no cards[/]")
        return
    # border + upscale run on cards not yet upscaled (border self-skips if fine)
    to_upscale = [c.id for c in cards if force or not c.has(Stage.UPSCALED)]
    if to_upscale:
        console.print(f"[bold]border[/] ({len(to_upscale)})")
        ctx.invoke(border, ids=tuple(to_upscale), force=force)
        console.print(f"[bold]upscale[/] ({len(to_upscale)})")
        ctx.invoke(
            upscale,
            ids=tuple(to_upscale),
            model=None,
            scale=None,
            double=None,
            force=force,
        )
    to_grade = [c.id for c in cards if force or not c.has(Stage.EDITED)]
    if to_grade:
        console.print(f"[bold]grade[/] ({len(to_grade)})")
        ctx.invoke(grade, ids=tuple(to_grade), normalize=None, force=force)
    console.print("[green]build complete[/]")


def _write_batch(path: Path, data: dict[str, object]) -> None:
    def s(v: object) -> str:
        return '"' + str(v).replace('"', '\\"') + '"'

    cards = data.get("cards", [])
    card_ids = cards if isinstance(cards, list) else []
    lines = [
        f"name = {s(data.get('name', ''))}",
        f"date = {s(data.get('date', ''))}",
        f"faces = {s(data.get('faces', 'fronts'))}",
        f"printed = {'true' if data.get('printed') else 'false'}",
        f"printed_date = {s(data.get('printed_date', ''))}",
        f"paper = {s(data.get('paper', ''))}",
        f"printer = {s(data.get('printer', ''))}",
        f"notes = {s(data.get('notes', ''))}",
        f"pdf = {s(data.get('pdf', 'fronts.pdf'))}",
        "cards = [",
    ]
    lines += [f"  {s(cid)}," for cid in card_ids]
    lines.append("]")
    path.write_text("\n".join(lines) + "\n")


def _resolve_back_path(card: Card, cfg: Config, lib: Library) -> Path | None:
    """Per-card <id>_back.png, then [sheet] back_image, then <lib>/back.png."""
    candidates = [card.dir / f"{card.id}_back.png"]
    if cfg.sheet_back_image:
        shared = Path(cfg.sheet_back_image)
        candidates.append(shared if shared.is_absolute() else lib.root / shared)
    candidates.append(lib.root / "back.png")
    return next((p for p in candidates if p.exists()), None)


@dataclass(slots=True)
class _Repro:
    """The print-time reproduction: fit any master to trim, colour-correct for
    the medium, then extend cut bleed outside the trim with cardbleed."""

    cfg: Config
    profile: str
    recipe: media.Recipe
    cal: calibrate_mod.Stage | None
    tmpdir: Path

    def cell(self, master: Image.Image) -> Image.Image:
        cfg = self.cfg
        ppm = cfg.sheet_dpi / 25.4
        trim_w, trim_h = round(cfg.card_w_mm * ppm), round(cfg.card_h_mm * ppm)
        im = sheet_mod.fit(master, trim_w, trim_h, cfg.sheet_fit.lower())
        if self.cal is not None:
            im = calibrate_mod.apply_to_image(im, self.cal)
        elif self.profile != "none":
            im = media.compensate(im, self.recipe)
        if cfg.bleed_mm <= 0:
            return im
        bp = round(cfg.bleed_mm * ppm)
        src = Path(tempfile.mkstemp(suffix=".png", dir=self.tmpdir)[1])
        dst = Path(tempfile.mkstemp(suffix=".png", dir=self.tmpdir)[1])
        im.save(src)
        bleed.run(src, dst, bleed.uniform(bp), cfg)
        return Image.open(dst).convert("RGB")


_SCRYFALL_BACK = "https://cards.scryfall.io/back.png"


@cli.command()
@click.option("--url", default=None, help="Download the back image from this URL.")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Use a local image as the back.",
)
@click.option(
    "--tcg",
    type=click.Choice(["mtg", "pokemon"]),
    default=None,
    help="Preset source: [cyan]mtg[/] = Scryfall's standard back.",
)
@click.option(
    "-o",
    "--out",
    "out",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to save (default: <lib>/back.png).",
)
@click.pass_context
def back(
    ctx: click.Context,
    url: str | None,
    file_path: Path | None,
    tcg: str | None,
    out: Path | None,
) -> None:
    """Set the shared card back — a trim-size master.

    Just fetches/imports and stores the image; colour correction and cut bleed
    are applied at [cyan]sheet[/] time (exactly like the fronts), so front and
    back match on the medium. Source: [cyan]--file[/], [cyan]--url[/], or
    [cyan]--tcg mtg[/] (Scryfall's standard back). There's no reliable
    Pokémon-back API — supply your own scan. A per-card [cyan]<id>_back.png[/]
    overrides this shared one.
    """
    lib = _lib(ctx)
    if not url and not file_path and tcg == "mtg":
        url = _SCRYFALL_BACK
    if not url and not file_path:
        raise click.UsageError(
            "give --file, --url, or --tcg mtg — there's no Pokémon back API, "
            "so supply your own scan"
        )
    if file_path:
        im = Image.open(file_path).convert("RGB")
    else:
        import io

        import requests

        headers = {"User-Agent": f"proxdex/{__version__}", "Accept": "image/*"}
        resp = requests.get(url, headers=headers, timeout=60)  # type: ignore[arg-type]
        if not resp.ok:
            raise ProxdexError(f"download failed ({resp.status_code}) for {url}")
        im = Image.open(io.BytesIO(resp.content)).convert("RGB")

    dst = out or lib.root / "back.png"
    im.save(dst)
    console.print(
        f"[green]✓[/] card back → {dst.relative_to(lib.root)} "
        "[dim](colour + bleed applied at sheet time)[/]"
    )


@cli.command()
@click.argument("name")
@click.argument("ids", nargs=-1, metavar="[ID...]")
@click.option(
    "--faces",
    type=click.Choice(["fronts", "backs", "duplex"]),
    default=None,
    help="What to impose (default from [sheet]).",
)
@click.option("--page", default=None, help="Page size override (a4 | letter).")
@click.option("--dpi", type=int, default=None, help="Render resolution override.")
@click.option(
    "--profile", default=None, help="Medium colour profile (default from [print])."
)
@click.option("--open", "open_pdf", is_flag=True, help="Open the PDF when done.")
@click.pass_context
def sheet(
    ctx: click.Context,
    name: str,
    ids: tuple[str, ...],
    faces: str | None,
    page: str | None,
    dpi: int | None,
    profile: str | None,
    open_pdf: bool,
) -> None:
    """Impose the trim masters into a print PDF and record the batch.

    Each card is scaled to the exact configured card size at sheet DPI, colour-
    corrected for the medium, then given cut bleed *outside* the trim via
    cardbleed — the individual masters stay bleed-free. Cut guides sit at the
    card edge. Fronts, backs, or duplex (back pages mirrored + offset). proxdex
    owns the PDF — print with colour management OFF for calibration to hold.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    if page:
        cfg.sheet_page = page
    if faces:
        cfg.sheet_faces = faces
    if dpi:
        cfg.sheet_dpi = dpi
    cards = lib.select(ids) if ids else lib.cards()
    ready = [c for c in cards if c.has(Stage.EDITED)]
    missing = [c.id for c in cards if not c.has(Stage.EDITED)]
    if missing:
        err.print(
            f"[yellow]no master, skipping:[/] {', '.join(missing)} "
            "[dim](run `proxdex build`)[/]"
        )
    if not ready:
        raise click.UsageError("no card masters to impose — run `proxdex build`")

    prof_name, recipe = media.resolve(cfg, profile)
    cal = calibrate_mod.load(_cal_dir(lib), prof_name) if prof_name != "none" else None
    if prof_name != "none" and cal is None and prof_name not in media.PROFILES:
        err.print(f"[yellow]note[/] '{prof_name}' has no calibration or preset")

    tmpdir = Path(tempfile.mkdtemp(prefix="proxdex-sheet-"))
    try:
        repro = _Repro(cfg, prof_name, recipe, cal, tmpdir)
        fronts = [
            repro.cell(Image.open(c.stage_path(Stage.EDITED)).convert("RGB"))
            for c in ready
        ]
        backs: list[Image.Image | None] = [None] * len(ready)
        if cfg.sheet_faces in ("backs", "duplex"):
            cache: dict[Path, Image.Image] = {}
            paths = [_resolve_back_path(c, cfg, lib) for c in ready]
            no_back = [c.id for c, p in zip(ready, paths, strict=True) if p is None]
            if no_back:
                raise click.UsageError(
                    f"{cfg.sheet_faces} needs backs, none for: {', '.join(no_back)}"
                    " — `proxdex back ...`, [sheet] back_image, or <id>_back.png"
                )
            for i, p in enumerate(paths):
                if p is not None and p not in cache:
                    cache[p] = repro.cell(Image.open(p).convert("RGB"))
                backs[i] = cache[p] if p is not None else None

        slug = slugify(name)
        today = date.today().isoformat()
        bdir = lib.batches_dir / f"{today}_{slug}"
        bdir.mkdir(parents=True, exist_ok=True)
        pdf = bdir / f"{cfg.sheet_faces}.pdf"
        n_pages = sheet_mod.impose_to_pdf(fronts, backs, cfg, pdf)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    _write_batch(
        bdir / "batch.toml",
        {
            "name": slug,
            "date": today,
            "faces": cfg.sheet_faces,
            "cards": [c.id for c in ready],
            "pdf": pdf.name,
        },
    )
    _reindex(lib)
    console.print(
        f"[green]✓[/] {len(ready)} cards ({cfg.sheet_faces}) → {n_pages} "
        f"page(s) @ {cfg.sheet_dpi}dpi → {pdf.relative_to(lib.root)}"
    )
    console.print(
        f"[dim]print with colour management OFF, then `proxdex printed {slug}`[/]"
    )
    if open_pdf or cfg.sheet_open:
        import subprocess

        subprocess.run(["open", str(pdf)], check=False)


@cli.command()
@click.argument("name")
@click.pass_context
def printed(ctx: click.Context, name: str) -> None:
    """Mark a print batch as printed (updates its manifest)."""
    lib = _lib(ctx)
    slug = slugify(name)
    for tf in lib.batches_dir.glob("*/batch.toml"):
        data = tomllib.loads(tf.read_text())
        if data.get("name") == slug or tf.parent.name.endswith(f"_{slug}"):
            data["printed"] = True
            data["printed_date"] = date.today().isoformat()
            _write_batch(tf, data)
            _reindex(lib)
            console.print(f"[green]✓[/] '{slug}' printed {data['printed_date']}")
            return
    raise click.UsageError(f"no batch named '{name}'")


def _cal_dir(lib: Library) -> Path:
    return lib.root / "calibration"


def _active_profile(cfg: Config, profile: str | None) -> str:
    return profile or cfg.print_profile or "none"


_SCAN = click.option(
    "--scan",
    "scan_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The scanned chart image.",
)
_PROFILE = click.option(
    "--profile", default=None, help="Medium profile (default from [print])."
)


@cli.group()
def calibrate() -> None:
    """Colour-calibrate a print medium with a print+scan loop.

    [dim]target[/] emits a chart → print it on the medium (scanner
    auto-correction OFF) → [dim]fit[/] reads the scan and measures a per-medium
    correction that [cyan]sheet[/] then applies. [dim]target --corrected[/] +
    [dim]check[/] verify how true the corrected print is; repeat to converge.
    """


@calibrate.command("target")
@_PROFILE
@click.option(
    "--corrected",
    is_flag=True,
    help="Bake the saved correction into the chart (print this to verify).",
)
@click.option(
    "--pdf",
    "as_pdf",
    is_flag=True,
    help="Output a PDF via the same renderer as print sheets (path parity).",
)
@click.option(
    "-o",
    "--out",
    "out",
    type=click.Path(path_type=Path),
    default=None,
    help="Output path (default: <lib>/calibration/<profile>_chart.png).",
)
@click.pass_context
def cal_target(
    ctx: click.Context,
    profile: str | None,
    corrected: bool,
    as_pdf: bool,
    out: Path | None,
) -> None:
    """Write a printable calibration chart.

    Use [cyan]--pdf[/] so the chart travels the exact same path to paper as
    your card sheets — otherwise the correction is measured on a different
    print path than it's applied to.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    prof = _active_profile(cfg, profile)
    stage = calibrate_mod.load(_cal_dir(lib), prof) if corrected else None
    if corrected and stage is None:
        raise click.UsageError(f"no calibration for '{prof}' yet — run `fit` first")
    suffix = "_chart_corrected" if corrected else "_chart"
    ext = "pdf" if as_pdf else "png"
    dst = out or _cal_dir(lib) / f"{prof}{suffix}.{ext}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    chart = calibrate_mod.render_chart(stage)
    if as_pdf:
        sheet_mod.single_page_pdf(chart, dst, cfg)
    else:
        chart.save(dst)
    console.print(
        f"[green]wrote[/] {dst}\n[dim]print it on '{prof}' with scanner "
        "auto-correction OFF, then `proxdex calibrate fit --scan <scan>`[/]"
    )


@calibrate.command("fit")
@_PROFILE
@_SCAN
@click.pass_context
def cal_fit(ctx: click.Context, profile: str | None, scan_path: Path) -> None:
    """Measure a correction for the medium from a scanned chart."""
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    prof = _active_profile(cfg, profile)
    target = np.array(calibrate_mod.chart_patches(), np.float32)
    measured = calibrate_mod.read_scan(scan_path)
    err = calibrate_mod.error(measured, target)
    stage = calibrate_mod.fit(measured, target)
    dst = calibrate_mod.save(_cal_dir(lib), prof, stage, err)
    console.print(
        f"[green]calibrated[/] '{prof}': raw print was off by "
        f"mean {err['mean']:.1f} / max {err['max']:.1f} RGB"
    )
    console.print(
        f"[dim]saved {dst.relative_to(lib.root)} · `sheet` now applies it. "
        "verify: `calibrate target --corrected` → print → `calibrate check`[/]"
    )


@calibrate.command("check")
@_PROFILE
@_SCAN
@click.pass_context
def cal_check(ctx: click.Context, profile: str | None, scan_path: Path) -> None:
    """Report residual error from a scan of the *corrected* chart."""
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    prof = _active_profile(cfg, profile)
    target = np.array(calibrate_mod.chart_patches(), np.float32)
    err = calibrate_mod.error(calibrate_mod.read_scan(scan_path), target)
    console.print(
        f"'{prof}' residual after correction: "
        f"mean {err['mean']:.1f} / max {err['max']:.1f} RGB [dim](lower is truer)[/]"
    )


@calibrate.command("show")
@click.pass_context
def cal_show(ctx: click.Context) -> None:
    """List the measured calibrations in this library."""
    lib = _lib(ctx)
    files = sorted(_cal_dir(lib).glob("*.json"))
    if not files:
        console.print("[dim]no calibrations yet — run `calibrate fit`[/]")
        return
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("Profile", "Model", "Raw err (mean/max)"):
        table.add_column(col)
    for f in files:
        data = json.loads(f.read_text())
        e = data.get("uncorrected_error", {})
        table.add_row(
            data.get("profile", f.stem),
            data.get("model", "?"),
            f"{e.get('mean', 0):.1f} / {e.get('max', 0):.1f}",
        )
    console.print(table)


def _card_id_from(stem: str) -> str | None:
    m = re.match(r"[a-z]+\d*-\d+", stem, re.IGNORECASE)
    return m.group(0) if m else None


def main() -> None:
    try:
        cli()
    except ProxdexError as e:
        err.print(f"[bold red]error:[/] {e}")
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
