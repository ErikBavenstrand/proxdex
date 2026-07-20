"""Command-line interface (click + rich-click)."""

from __future__ import annotations

import glob
import json
import re
import shutil
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

import numpy as np
import rich_click as click
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
)
from rich.table import Table

from . import bleed, borders, media, report, sources
from . import calibrate as calibrate_mod
from . import grade as grade_mod
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
        {"name": "Library", "commands": ["init", "ls", "index"]},
        {"name": "Acquire", "commands": ["search", "fetch", "import"]},
        {"name": "Prepare", "commands": ["upscale", "grade", "measure", "border"]},
        {"name": "Calibrate", "commands": ["calibrate"]},
    ]
}

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

T = TypeVar("T")

_STAGES = (Stage.ORIGINAL, Stage.UPSCALED, Stage.EDITED, Stage.PRINT)
_STAGE_BY_LABEL = {s.label: s for s in Stage}

DEFAULT_TOML = """\
# proxdex library config — tune here, no code edits needed.

[border]
# Frame-thickness targets as a fraction of card width / height.
# 0.0 = auto: pad the thin edges up to match the sides (bottom is never
# measured). After eyeballing a known-good card with `proxdex measure`, set
# e.g. target_side_ratio = 0.045 to also fix cards whose frame is uniformly
# too thin for a real card.
target_side_ratio = 0.0
target_top_ratio  = 0.0
thresh            = 62      # RGB distance still counted as "the frame colour"

[grade]
# 1) normalize: pull each card to a common baseline first (so scans and
#    digital art match) — white-balance the frame + even out black/white points.
normalize = true
black_pct = 0.5             # luminance percentile mapped to black
white_pct = 99.5            # luminance percentile mapped to white
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

[print]
# Media compensation baked into stage 4 (the master stays neutral).
# "none" | "paper" | "foil". Foil (transparent plastic) washes colours out,
# so its profile boosts saturation and density. Calibrate with a test print
# and override any value below (uncomment to pin).
profile = "foil"
# saturation = 1.38
# contrast   = 1.16
# brightness = 0.95
# gamma      = 0.88        # < 1 darkens midtones → more ink density

[tools]
# Upscayl (optional stage-2 step). On macOS the bundled binary and models are
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

    A card flows through four stages: [cyan]original[/] → [cyan]upscaled[/] →
    [cyan]edited[/] → [cyan]print[/]. proxdex fetches sources, files each
    stage in a predictable place, corrects thin frames, and tracks what you've
    actually printed.

    [dim]Examples:[/]

    [dim]  proxdex fetch ex3-90 ex6-105[/]

    [dim]  proxdex measure && proxdex border --write-print[/]
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
    files: list[Path] = []
    for pattern in paths:
        expanded = glob.glob(str(Path(pattern).expanduser()))
        files.extend(Path(p) for p in expanded)

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
    table.add_column("O U E P", justify="center")
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
    console.print("[dim]stages: O original · U upscaled · E edited · P print[/]")


@cli.command()
@click.argument("ids", nargs=-1, metavar="[ID...]")
@click.pass_context
def measure(ctx: click.Context, ids: tuple[str, ...]) -> None:
    """Measure top/side frame thickness and flag cards needing extension."""
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col, just in (
        ("Card", "left"),
        ("Size", "left"),
        ("top", "right"),
        ("left", "right"),
        ("right", "right"),
        ("side%", "right"),
        ("", "left"),
    ):
        table.add_column(col, justify=just)  # type: ignore[arg-type]
    for card in lib.select(ids):
        src = card.best(Stage.EDITED, Stage.UPSCALED, Stage.ORIGINAL)
        if src is None:
            table.add_row(card.id, "[dim]no image[/]", "", "", "", "", "")
            continue
        b = borders.measure(borders.load_rgb(src), cfg)
        tgt = borders.target(b, cfg)
        need = b.top < tgt.top - 2 or b.left < tgt.side - 2 or b.right < tgt.side - 2
        verdict = "[yellow]extend[/]" if need else "[green]ok[/]"
        table.add_row(
            card.id,
            f"{b.w}×{b.h}",
            f"{b.top:.0f}",
            f"{b.left:.0f}",
            f"{b.right:.0f}",
            f"{b.side_ratio * 100:.1f}%",
            verdict,
        )
    console.print(table)


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
@click.option("--force", is_flag=True, help="Re-upscale even if stage 2 exists.")
@click.pass_context
def upscale(
    ctx: click.Context,
    ids: tuple[str, ...],
    model: str | None,
    scale: int | None,
    double: bool | None,
    force: bool,
) -> None:
    """Upscale originals with Upscayl's CLI → stage 2 (upscaled).

    Optional step; needs Upscayl installed (its bundled [cyan]upscayl-bin[/] is
    auto-detected on macOS). Mirrors the app's own options — pick any of the
    seven models, a scale, and optional [cyan]--double[/]. Defaults live under
    [cyan][tools][/] in proxdex.toml.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    use_model = model or cfg.upscayl_model
    use_scale = cfg.upscayl_scale if scale is None else scale
    use_double = cfg.upscayl_double if double is None else double
    tag = f"{use_model} ×{use_scale}{' ×2' if use_double else ''}"

    def one(card: Card) -> None:
        src = card.stage_path(Stage.ORIGINAL)
        if not src.exists():
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


@cli.command()
@click.argument("ids", nargs=-1, metavar="[ID...]")
@click.option(
    "--normalize/--no-normalize",
    "normalize",
    default=None,
    help="Pull each card to a common baseline before the recipe (default on).",
)
@click.pass_context
def grade(ctx: click.Context, ids: tuple[str, ...], normalize: bool | None) -> None:
    """Normalize each card to a common baseline, then apply the uniform look.

    Normalization white-balances the card frame and evens out black/white
    points so scanned and digitally-drawn cards start from the same place;
    then one identical recipe (saturation/contrast) makes the batch print
    uniformly. Writes stage 3 (edited). Tune both under [cyan][grade][/].
    """
    from PIL import Image

    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    do_norm = cfg.grade_normalize if normalize is None else normalize
    # dynamic target: the collection's own median frame colour (unless pinned)
    frame_target = None
    if do_norm and not cfg.match_border_target:
        frame_target = _library_frame_target(lib, cfg)

    def one(card: Card) -> None:
        src = card.best(Stage.UPSCALED, Stage.ORIGINAL)
        if src is None:
            raise FileError(f"{card.id}: nothing to grade yet")
        out = grade_mod.grade(
            Image.open(src), cfg, frame_target=frame_target, normalize=do_norm
        )
        dst = card.stage_path(Stage.EDITED)
        out.save(dst)
        console.print(f"[green]✓[/] {card.id}: graded → {dst.relative_to(lib.root)}")

    _each(lib.select(ids), one, "grading")


def _library_frame_target(
    lib: Library, cfg: Config
) -> tuple[float, float, float] | None:
    """Median frame colour across the whole library — the consensus to aim at."""
    colors = []
    for card in lib.cards():
        src = card.best(Stage.UPSCALED, Stage.ORIGINAL)
        if src is not None:
            colors.append(borders.frame_color(borders.load_rgb(src)))
    if not colors:
        return None
    median = np.median(np.stack(colors), axis=0)
    return (float(median[0]), float(median[1]), float(median[2]))


@cli.command()
@click.argument("ids", nargs=-1, metavar="[ID...]")
@click.option(
    "--write-print",
    is_flag=True,
    help="Write stage 4 (print). Otherwise report the plan only (dry run).",
)
@click.option(
    "--profile",
    default=None,
    help="Print medium: a calibrated profile name, or a none/paper/foil preset "
    "(default from [print]).",
)
@click.pass_context
def border(
    ctx: click.Context,
    ids: tuple[str, ...],
    write_print: bool,
    profile: str | None,
) -> None:
    """Build the print-ready image: media compensation + cardbleed bleed.

    Measures the top and sides, computes the per-edge extension (frame
    correction + cut bleed), optionally pre-compensates colour for the print
    medium (e.g. [cyan]foil[/] washes out, so saturation/density are boosted),
    then runs [cyan]cardbleed[/]. Without [cyan]--write-print[/] it only prints
    the plan.
    """
    from PIL import Image

    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    prof_name, recipe = media.resolve(cfg, profile)
    # a measured calibration for this medium supersedes the manual preset
    cal = calibrate_mod.load(_cal_dir(lib), prof_name) if prof_name != "none" else None
    if cal is not None:
        tag = f" [dim]({prof_name} · calibrated)[/]"
    elif prof_name != "none":
        tag = f" [dim]({prof_name})[/]"
        if prof_name not in media.PROFILES:
            err.print(
                f"[yellow]note[/] '{prof_name}' has no calibration or preset — "
                f"printing uncompensated (run `proxdex calibrate fit`)"
            )
    else:
        tag = ""

    def one(card: Card) -> None:
        src = card.best(Stage.EDITED, Stage.UPSCALED, Stage.ORIGINAL)
        if src is None:
            raise FileError(f"{card.id}: nothing to process yet")
        b = borders.measure(borders.load_rgb(src), cfg)
        ext = bleed.plan(b, borders.target(b, cfg), cfg)
        plan = f"top+{ext.top} left+{ext.left} right+{ext.right} bottom+{ext.bottom}px"
        if not write_print:
            console.print(f"[cyan]{card.id}[/] from [dim]{src.name}[/]: {plan}{tag}")
            return
        dst = card.stage_path(Stage.PRINT)
        feed = src
        tmp: Path | None = None
        if cal is not None or prof_name != "none":  # bake colour in before cardbleed
            tmp = card.dir / f".{card.id}_print_src.png"
            im = Image.open(src)
            corrected = (
                calibrate_mod.apply_to_image(im, cal)
                if cal is not None
                else media.compensate(im, recipe)
            )
            corrected.save(tmp)
            feed = tmp
        try:
            bleed.run(feed, dst, ext, cfg)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        console.print(
            f"[green]✓[/] {card.id}: {plan}{tag} → {dst.relative_to(lib.root)}"
        )

    _each(lib.select(ids), one, "bordering")


@cli.command()
@click.pass_context
def index(ctx: click.Context) -> None:
    """Regenerate INDEX.md from the cards and print batches on disk."""
    lib = _lib(ctx)
    dst = report.write_index(lib)
    console.print(f"[green]wrote[/] {dst}")


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
    correction that [cyan]border[/] then bakes in. [dim]target --corrected[/] +
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
    "-o",
    "--out",
    "out",
    type=click.Path(path_type=Path),
    default=None,
    help="Output path (default: <lib>/calibration/<profile>_chart.png).",
)
@click.pass_context
def cal_target(
    ctx: click.Context, profile: str | None, corrected: bool, out: Path | None
) -> None:
    """Write a printable calibration chart."""
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    prof = _active_profile(cfg, profile)
    stage = calibrate_mod.load(_cal_dir(lib), prof) if corrected else None
    if corrected and stage is None:
        raise click.UsageError(f"no calibration for '{prof}' yet — run `fit` first")
    suffix = "_chart_corrected" if corrected else "_chart"
    dst = out or _cal_dir(lib) / f"{prof}{suffix}.png"
    dst.parent.mkdir(parents=True, exist_ok=True)
    calibrate_mod.render_chart(stage).save(dst)
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
        f"[dim]saved {dst.relative_to(lib.root)} · `border` now bakes it in. "
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
