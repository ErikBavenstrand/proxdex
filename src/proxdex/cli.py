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
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, TypeVar, cast

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

from proxdex import bleed, borders, frames, games, media, net, report, sources, steps
from proxdex import calibrate as calibrate_mod
from proxdex import grade as grade_mod
from proxdex import sheet as sheet_mod
from proxdex import upscale as upscale_mod
from proxdex._version import __version__
from proxdex.config import (
    MARKER,
    Config,
    Faces,
    PageSize,
    UpscaylModel,
    UpscaylScale,
)
from proxdex.errors import FileError, ProxdexError
from proxdex.frames import FrameGuide, GuideId
from proxdex.games import GameId
from proxdex.library import (
    FRONT,
    STAGE_BY_LABEL,
    Card,
    Library,
    Stage,
    Status,
    Step,
    slugify,
)

click.rich_click.USE_RICH_MARKUP = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.STYLE_OPTIONS_TABLE_LEADING = 0
click.rich_click.COMMAND_GROUPS = {
    "proxdex": [
        {
            "name": "Library",
            "commands": ["init", "where", "ls", "show", "rm", "config", "index", "ui"],
        },
        {"name": "Acquire", "commands": ["search", "fetch", "import"]},
        {"name": "Prepare", "commands": ["border", "upscale", "grade", "frames"]},
        {"name": "Pipeline", "commands": ["skip", "unskip", "reset"]},
        {
            "name": "Produce",
            "commands": ["back", "flip", "sheet", "batches", "printed"],
        },
        {"name": "Calibrate", "commands": ["calibrate"]},
    ]
}

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

T = TypeVar("T")

#: the stage order, read from the one place it is declared
_STAGES = steps.STAGES

_GAME_CHOICE = click.Choice([g.value for g in GameId])
_STEP_CHOICE = click.Choice([s.value for s in Step])

#: how far `fetch --related` follows the provider's links. Two rounds reach both
#: meld halves and the melded card from any of the three; a bound is what stops a
#: cycle of "related to each other" from fetching a whole set.
_RELATED_ROUNDS = 2
#: which relations `--related` actually fetches. A meld half needs its partner and
#: the melded card, and a card that makes tokens needs the tokens; a "combo piece"
#: is as loose as a set's checklist card, so those are named and left alone.
_FOLLOWED = frozenset(
    {
        sources.Relation.MELD_PART,
        sources.Relation.MELD_RESULT,
        sources.Relation.TOKEN,
    }
)

#: shared `--game`; unset means "the library default, then the other games"
_GAME = click.option(
    "--game",
    type=_GAME_CHOICE,
    default=None,
    help="Which TCG to use (default: [cyan][library] game[/] in proxdex.toml).",
)
#: shared `--face`; unset means every face the card has. A two-sided card's back
#: is its own picture with its own pipeline state, so a step can target one side.
_FACE = click.option(
    "--face",
    type=int,
    default=None,
    metavar="N",
    help="Only this side of a two-sided card ([cyan]1[/] = front, [cyan]2[/] = "
    "back). Default: every side.",
)

DEFAULT_TOML = """\
# proxdex library config — tune here, no code edits needed.

# Each card runs an ordered, optional pipeline: border → upscale → grade. Every
# step is one you run or skip — nothing is automatic. `proxdex border` reshapes
# to the exact card size (mark the frame with --inner-*, or the UI align tool),
# `upscale` and `grade` follow; `proxdex skip <step> <id>` bypasses one.

[library]
# Which game a bare `search`/`fetch` means. Every card also records its own
# game in a `.game` file, so one library can hold both — this is just the
# default for new lookups. "pokemon" | "mtg"
game = "pokemon"

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
# Where each game's metadata and images come from. Pokémon splits the two
# (pokemontcg.io for data, scrydex for the scan); Scryfall serves both for MTG.
# api_url     = "https://api.pokemontcg.io/v2/cards/{id}"
# scrydex_url = "https://images.scrydex.com/pokemon/{id}/large"
# mtg_api_url = "https://api.scryfall.com"

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
# cover = fill preserving aspect (default): the bordered master is already
# exactly 63:88, so cover neither crops nor distorts. contain = whole image +
# white pad. Never stretch — it re-introduces the print-time rescale the border
# step exists to avoid.
fit = "cover"

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
# One of Upscayl's built-in models: upscayl-standard-4x | upscayl-lite-4x |
# high-fidelity-4x | remacri-4x | ultramix-balanced-4x | ultrasharp-4x |
# digital-art-4x. Anything else fails at load, naming these.
upscayl_model = "digital-art-4x"
upscayl_scale = 2                 # 1 | 2 | 3 | 4
upscayl_double = true             # run the model twice (2x doubled = 4x, up to 16x)
# upscayl_bin    = "/Applications/Upscayl.app/Contents/Resources/bin/upscayl-bin"
# upscayl_models = "/Applications/Upscayl.app/Contents/Resources/models"
"""


class UserPath(click.Path):  # pyright: ignore[reportMissingTypeArgument]
    """A :class:`click.Path` that expands ``~`` before its own checks.

    The shell expands a bare ``~/x`` but not a quoted ``"~/x"`` — and a library
    path with a space in it (``"~/Documents/Pokémon Proxies"``) has to be
    quoted, so a literal ``~`` reaches us and would resolve against the cwd.
    """

    def convert(
        self,
        value: str | os.PathLike[str],
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> Any:
        return super().convert(Path(value).expanduser(), param, ctx)


# --------------------------------------------------------------- helpers -----
def _lib(ctx: click.Context) -> Library:
    return Library.discover(explicit=_root_opt(ctx))


def _root_opt(ctx: click.Context) -> Path | None:
    """The group's ``--root``, already ``~``-expanded by :class:`UserPath`."""
    root = ctx.obj.get("root")
    return Path(root) if root else None


_STATUS_GLYPH: dict[Status, str] = {
    Status.DONE: "[green]✓[/]",
    Status.SKIPPED: "[yellow]⤳[/]",
    Status.PENDING: "[dim]·[/]",
}


def _dots(card: Card, face: int = FRONT) -> str:
    return " ".join(_STATUS_GLYPH[card.status(s, face)] for s in _STAGES)


def _faces(card: Card, face: int | None) -> tuple[int, ...]:
    """Which sides of a card a command should act on.

    ``--face`` is 1-based because that is how the sides are labelled everywhere
    else; out of range is an error rather than a silent fall-back to the front.
    """
    if face is None:
        return card.faces
    if face - 1 not in card.faces:
        raise FileError(f"{card.id}: no side {face} — this card has {len(card.faces)}")
    return (face - 1,)


def _some_faces(card: Card, face: int | None) -> tuple[int, ...]:
    """:func:`_faces`, but a card that simply hasn't got that side is passed over.

    ``proxdex skip border --face 2`` over a whole library means "the back of
    every two-sided card"; a one-sided card is not an error there.
    """
    try:
        return _faces(card, face)
    except FileError:
        console.print(f"[dim]· {card.id}: one side, no side {face}[/]")
        return ()


def _label(card: Card, face: int) -> str:
    """``neo-136`` for a one-sided card, ``neo-136 · back`` for a side of two."""
    if len(card.faces) < 2:
        return card.id
    name = card.face_names()[face] or ("front" if face == FRONT else "back")
    return f"{card.id} · {name}"


def _reindex(lib: Library) -> None:
    """Refresh INDEX.md after a state change; never break the command over it."""
    with contextlib.suppress(Exception):
        report.write_index(lib)


def _api_note() -> None:
    """Say when a card API misbehaved during this command, so a slow or partly
    empty result reads as "the API is flaky", not "proxdex is broken"."""
    for host in net.incidents():
        err.print(
            f"[yellow]⚠[/] {host.message} [dim](retried; cached where possible)[/]"
        )


def _cascade(card: Card, stage: Stage, face: int = FRONT) -> None:
    """Drop downstream outputs made stale by a change to ``stage``, and say so."""
    removed = card.invalidate_downstream(stage, face)
    if removed:
        names = ", ".join(s.label for s in removed)
        console.print(f"  [dim]↳ removed stale downstream: {names}[/]")


def _master(card: Card, face: int = FRONT) -> Path | None:
    """The furthest-along image to print — the graded master, or the best
    earlier stage when a later step was skipped."""
    return card.best(*steps.BEST, face=face)


def _sheet_ready(card: Card, face: int = FRONT) -> bool:
    """A side is ready to impose once grade is settled — done, or skipped so an
    earlier stage stands as the master."""
    return (
        card.has(Stage.EDITED, face) or card.skipped(Stage.EDITED, face)
    ) and _master(card, face) is not None


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
    type=UserPath(file_okay=False, path_type=Path),
    help="Library folder (default: search up from the current directory).",
)
@click.version_option(__version__, "-V", "--version")
@click.pass_context
def cli(ctx: click.Context, root: Path | None) -> None:
    """[bold]proxdex[/] — organize and drive your Pokémon proxy pipeline.

    A card flows through four stages: [cyan]original[/] → [cyan]bordered[/] →
    [cyan]upscaled[/] → [cyan]edited[/] (the trim master); bleed and colour are
    added at [cyan]sheet[/] time. Each step is one you run or skip — nothing is
    automatic. proxdex fetches sources, files each stage in a predictable place,
    corrects thin frames, and tracks what you've actually printed.

    [dim]Examples:[/]

    [dim]  proxdex fetch ex3-90 ex6-105[/]

    [dim]  proxdex upscale ex3-90 && proxdex grade ex3-90 && proxdex sheet my-deck[/]
    """
    ctx.obj = {"root": root}


@cli.command()
@click.argument("path", required=False, type=UserPath(file_okay=False, path_type=Path))
@click.pass_context
def init(ctx: click.Context, path: Path | None) -> None:
    """Create a new library here (or at PATH): cards/, print-batches/, config."""
    root = (path or _root_opt(ctx) or Path.cwd()).resolve()
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
    set_dir = lib.set_dir(meta.set_id, meta.set_name, meta.game)
    card_dir = set_dir / f"{meta.id}_{slugify(meta.name)}"
    card_dir.mkdir(parents=True, exist_ok=True)
    card = Card(id=meta.id, dir=card_dir, set_id=meta.set_id)
    card.write_game(meta.game)
    return card


def _ensure_card(lib: Library, cfg: Config, cid: str, game: GameId | None) -> Card:
    """Find the card, or look up its metadata and create the folder."""
    card = lib.find(cid)
    if card is not None:
        return card
    return _card_from_meta(lib, sources.lookup_any(cid, cfg, game))


def _resolve_meta(
    lib: Library, cfg: Config, cid: str, game: GameId | None
) -> sources.CardMeta:
    """Metadata for an id, preferring the game an already-filed card records."""
    known = lib.find(cid)
    return sources.lookup_any(cid, cfg, game or (known.game if known else None))


def _kind_note(card: Card, meta: sources.CardMeta) -> None:
    """Say out loud when a printing is not an ordinary one-sided 63×88 card.

    Three things change what goes on paper — two sides, a meld pair, an oversized
    card — and one changes how the border is fitted (a borderless print has no
    frame to match). Silence here would mean finding out at ``sheet`` time.
    """
    if meta.layout is not games.Layout.SINGLE:
        console.print(
            f"  [cyan]{meta.layout.label.lower()}[/] [dim]{meta.layout.note}[/]"
        )
    if meta.oversized:
        err.print(
            f"[yellow]⚠[/] {card.id} is an oversized card "
            f"({games.OVERSIZED_W_MM:g}×{games.OVERSIZED_H_MM:g}mm). [dim]`sheet` "
            "imposes at the \\[card] trim size, so it would print at standard "
            "size — set \\[card] w_mm/h_mm for a sheet of these.[/]"
        )
    if meta.frame is not None:
        console.print(
            f"  [dim]frame:[/] {frames.GUIDES[meta.frame].name} "
            "[dim](from the printing, not its set)[/]"
        )


def _related_ids(lib: Library, cfg: Config, cid: str, game: GameId | None) -> list[str]:
    """The ids of cards printed alongside ``cid``, reported as they are found.

    A meld pair is three physical cards; a card that makes tokens is printed with
    them. Fetching one and not the others leaves a deck you cannot play, so
    ``fetch --related`` follows the provider's own links.
    """
    known = lib.find(cid)
    try:
        detail = sources.details(cid, cfg, game or (known.game if known else None))
    except ProxdexError as exc:
        err.print(f"[yellow]⚠[/] {cid}: could not read related cards ({exc})")
        return []
    found: list[str] = []
    for rel in detail.related:
        label = rel.relation.label.lower()
        if rel.relation not in _FOLLOWED:
            # a checklist card is a "combo piece" of every meld card in its set;
            # naming it is useful, fetching it is not what anyone meant
            console.print(f"  [dim]· {label} {rel.name} ({rel.id or 'no id'})[/]")
        elif rel.id:
            found.append(rel.id)
            console.print(f"  [dim]↳ {label}[/] {rel.name} [dim]({rel.id})[/]")
        else:
            err.print(
                f"[yellow]↳[/] {label} {rel.name} [dim](the API gave no id for "
                "that printing — find it with `proxdex search`)[/]"
            )
    return found


def _acquire(
    lib: Library, meta: sources.CardMeta, force: bool, face: int | None = None
) -> None:
    """Create the card folder if needed and download each side's stage-1 original.

    A two-sided card downloads both sides — they are one card with one id, and
    each side then runs its own pipeline.
    """
    card = _card_from_meta(lib, meta)
    card.write_faces(meta.face_names)
    # what kind of printing this is — recorded now so the border step, `sheet`
    # and the card page can act on it without asking the API again
    card.write_kind(meta.layout, oversized=meta.oversized, frame=meta.frame)
    _kind_note(card, meta)
    wanted = card.faces if face is None else _faces(card, face)
    for f in wanted:
        dst = card.stage_path(Stage.ORIGINAL, f)
        if dst.exists() and not force:
            console.print(f"[dim]· {_label(card, f)} {meta.name}: original exists[/]")
            continue
        sources.download(meta, f).save(dst)
        _cascade(card, Stage.ORIGINAL, f)
        console.print(
            f"[green]✓[/] {_label(card, f):<9} {meta.name:<18} → "
            f"{dst.relative_to(lib.root)}"
        )


@cli.command()
@click.argument("ids", nargs=-1, required=True, metavar="ID...")
@_GAME
@_FACE
@click.option("--force", is_flag=True, help="Re-download even if the original exists.")
@click.option(
    "--related",
    "with_related",
    is_flag=True,
    help="Also fetch the cards this one is printed alongside — both meld halves "
    "and the melded card, the tokens it makes.",
)
@click.pass_context
def fetch(
    ctx: click.Context,
    ids: tuple[str, ...],
    game: str | None,
    face: int | None,
    force: bool,
    with_related: bool,
) -> None:
    """Download originals by id, with names/sets from that game's API.

    IDs are canonical TCG ids — [cyan]ex3-90[/] for Pokémon, [cyan]neo-136[/]
    for MTG. Without [cyan]--game[/] each id is looked up in the library's
    default game first, then the others. A two-sided MTG card downloads both
    sides, each with its own pipeline. Don't know the id? Use
    [cyan]proxdex search[/] instead.

    A meld pair is three separate cards, so [cyan]--related[/] follows the API's
    own links and fetches the partner and the melded card too (and any tokens the
    card makes):

    [dim]  proxdex fetch --related inr-14[/]
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    want = games.parse(game)
    queue = list(ids)
    seen: set[str] = set()
    for round_no in range(_RELATED_ROUNDS):
        batch = [cid for cid in queue if cid.lower() not in seen]
        if not batch:
            break
        seen.update(cid.lower() for cid in batch)
        _each(
            batch,
            lambda cid: _acquire(lib, _resolve_meta(lib, cfg, cid, want), force, face),
            "fetching",
        )
        # nothing found in the last round could be fetched, so don't spend the
        # requests (or the noise) looking it up
        last = round_no == _RELATED_ROUNDS - 1
        queue = (
            [rel for cid in batch for rel in _related_ids(lib, cfg, cid, want)]
            if with_related and not last
            else []
        )
    _reindex(lib)


@cli.command()
@click.argument("query", nargs=-1, required=True, metavar="QUERY...")
@_GAME
@click.option(
    "--set", "set_filter", metavar="SET", help="Set id (ex4, neo) or name substring."
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
    game: str | None,
    set_filter: str | None,
    rarity: str | None,
    year: str | None,
    limit: int,
    selection: str | None,
    fetch_all: bool,
    open_images: bool,
    force: bool,
) -> None:
    """Search one game's cards by name, then pick which to fetch.

    Shows matches with set, year, collector number, rarity and artist so you
    can tell prints apart, then downloads the ones you choose. Searching is
    per-game (the APIs are different); [cyan]--game[/] picks which.

    [dim]Examples:[/]

    [dim]  proxdex search entei ex[/]

    [dim]  proxdex search --game mtg delver of secrets --set isd[/]
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    want = games.coerce(game, cfg.library_game)
    text = " ".join(query)
    results = sources.search(
        text, cfg, want, set_filter=set_filter, rarity=rarity, year=year, limit=limit
    )
    if not results:
        console.print(
            f"[yellow]no {games.get(want).name} matches for[/] {text!r} "
            "[dim](--game switches TCG)[/]"
        )
        return
    _print_results(results)
    if open_images:
        import webbrowser

        for result in results[:12]:
            webbrowser.open(result.image_url)

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
    _each(chosen, lambda r: _acquire(lib, r.to_meta(), force), "fetching")
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
@_GAME
@click.option(
    "--stage",
    type=click.Choice([s.label for s in Stage]),
    default=None,
    help="Target stage (default: guessed — 'upscayl' in the name → upscaled, "
    "else original).",
)
@_FACE
@click.option("--move", is_flag=True, help="Move files instead of copying them.")
@click.pass_context
def import_(
    ctx: click.Context,
    paths: tuple[str, ...],
    cid: str | None,
    game: str | None,
    stage: str | None,
    face: int | None,
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
    want = games.parse(game)
    forced_stage = STAGE_BY_LABEL[stage] if stage else None
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
        card = _ensure_card(lib, cfg, file_cid, want) if cid else lib.find(file_cid)
        if card is None:
            raise FileError(
                f"{file_cid}: no card folder — pass --id to create it, or "
                f"`proxdex fetch {file_cid}` first"
            )
        target = forced_stage or (
            Stage.UPSCALED if "upscayl" in f.name.lower() else Stage.ORIGINAL
        )
        # one file is one side; without --face it replaces the front
        side = _faces(card, face)[0] if face is not None else FRONT
        dst = card.stage_path(target, side)
        (shutil.move if move else shutil.copy2)(str(f), str(dst))
        card.clear_skip(target, side)
        _cascade(card, target, side)
        console.print(
            f"[green]✓[/] {f.name} → {dst.relative_to(lib.root)} "
            f"[dim](stage {target.value} {target.label})[/]"
        )

    if not files:
        raise click.UsageError("no files matched")
    _each(files, one, "importing")
    _reindex(lib)


@cli.command()
@click.option(
    "--clear-cache", is_flag=True, help="Drop the cached API responses and re-fetch."
)
@click.pass_context
def where(ctx: click.Context, clear_cache: bool) -> None:
    """Show the active library root and config (which one am I operating on?)."""
    lib = _lib(ctx)
    cfg_file = lib.root / MARKER
    mark = "[green]✓[/]" if cfg_file.exists() else "[red]missing[/]"
    console.print(f"[bold]library[/]  {lib.root}")
    console.print(f"config    {cfg_file} {mark}")
    console.print(f"game      {games.get(lib.default_game).name} [dim](default)[/]")
    console.print(f"cache     {net.cache_dir()}")
    if clear_cache:
        console.print(f"[green]✓[/] cleared {net.clear_cache()} cached response(s)")
    for host in net.health():
        console.print(f"[yellow]⚠[/] {host.message} [dim]({host.age:.0f}s ago)[/]")
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
    """Launch the local web UI (card gallery, per-step pipeline, sheet)."""
    lib = _lib(ctx)
    try:
        import uvicorn

        from proxdex.webui import create_app
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


class LsOnly(StrEnum):
    """The library filters, the same set the web UI's contact sheet offers."""

    TODO = "todo"
    READY = "ready"
    PRINTED = "printed"
    TWOSIDED = "twosided"
    OVERSIZED = "oversized"


class LsSort(StrEnum):
    NAME = "name"
    ID = "id"
    SET = "set"
    RECENT = "recent"


def _newest(card: Card) -> float:
    """When this card's pixels last changed — the ``recent`` sort key."""
    stamps = [
        p.stat().st_mtime
        for f in card.faces
        for s in _STAGES
        if (p := card.stage_path(s, f)).exists()
    ]
    return max(stamps) if stamps else 0.0


def _matches(
    card: Card,
    printed: bool,
    *,
    match: str | None,
    game: GameId | None,
    set_id: str | None,
    only: LsOnly | None,
) -> bool:
    text = match.lower() if match else ""
    if text and text not in card.id.lower() and text not in card.name.lower():
        return False
    if game is not None and card.game is not game:
        return False
    if set_id is not None and card.set_id.lower() != set_id.lower():
        return False
    ready = _sheet_ready(card, card.front_face)
    return {
        None: True,
        LsOnly.TODO: not ready,
        LsOnly.READY: ready and not printed,
        LsOnly.PRINTED: printed,
        LsOnly.TWOSIDED: len(card.faces) > 1,
        LsOnly.OVERSIZED: card.oversized,
    }[only]


@cli.command()
@click.argument("match", required=False, metavar="[TEXT]")
@_GAME
@click.option("--set", "set_id", metavar="SET", help="Only this set id.")
@click.option(
    "--only",
    type=click.Choice([o.value for o in LsOnly]),
    default=None,
    help="[cyan]todo[/] not ready to print · [cyan]ready[/] ready, not printed · "
    "[cyan]printed[/] · [cyan]twosided[/] · [cyan]oversized[/].",
)
@click.option(
    "--sort",
    type=click.Choice([s.value for s in LsSort]),
    default=LsSort.SET.value,
    show_default=True,
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Machine-readable output: one object per card, sides included.",
)
@click.pass_context
def ls(
    ctx: click.Context,
    match: str | None,
    game: str | None,
    set_id: str | None,
    only: str | None,
    sort: str,
    as_json: bool,
) -> None:
    """List cards with their stage progress and print status.

    TEXT filters by card id or name. The filters and sorts are the same ones the
    web UI's contact sheet offers, so a view you found there can be spelled here:

    [dim]  proxdex ls --only ready --sort recent[/]

    [dim]  proxdex ls charizard --game pokemon[/]
    """
    lib = _lib(ctx)
    by_card = report.card_batch_index(lib)
    want_only = LsOnly(only) if only else None
    cards = [
        card
        for card in lib.cards()
        if _matches(
            card,
            bool((b := by_card.get(card.id)) and b.printed),
            match=match,
            game=games.parse(game),
            set_id=set_id,
            only=want_only,
        )
    ]
    key: dict[LsSort, Callable[[Card], Any]] = {
        LsSort.NAME: lambda c: c.name.lower(),
        LsSort.ID: lambda c: c.id,
        LsSort.SET: lambda c: (c.set_id, c.id),
        LsSort.RECENT: lambda c: -_newest(c),
    }
    cards.sort(key=key[LsSort(sort)])

    if as_json:
        console.print_json(
            json.dumps(
                [_card_json(card, by_card.get(card.id)) for card in cards], indent=2
            )
        )
        return
    if not cards:
        console.print("[dim]no cards match[/]")
        return

    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("Card", "Name", "Game", "Set", "Kind", "Side"):
        table.add_column(col)
    table.add_column("O B U E", justify="center")
    table.add_column("Batch")
    table.add_column("Printed", justify="center")
    for card in cards:
        batch = by_card.get(card.id)
        names = card.face_names()
        for f in card.faces:
            first = f == 0
            side = "" if len(names) < 2 else (names[f] or f"side {f + 1}")
            if f == card.front_face and len(names) > 1:
                side = f"[cyan]{side}[/] ↑"  # the side that prints on the front
            table.add_row(
                card.id if first else "",
                card.name.title() if first else "",
                card.game.value if first else "",
                card.set_id if first else "",
                _kind_tag(card) if first else "",
                side,
                _dots(card, f),
                (batch.name if batch else "") if first else "",
                ("[green]✓[/]" if batch and batch.printed else "") if first else "",
            )
    console.print(table)
    console.print(
        "[dim]stages: O original · B bordered · U upscaled · E edited   "
        "([green]✓[/] done · [yellow]⤳[/] skipped · · pending)   "
        "↑ = the side printed on the front[/]"
    )
    _tally(cards)


def _kind_tag(card: Card) -> str:
    """The short "this is not an ordinary card" column for ``ls``."""
    tags: list[str] = []
    if card.layout is not games.Layout.SINGLE:
        tags.append(f"[cyan]{card.layout.value}[/]")
    if card.oversized:
        tags.append("[yellow]oversized[/]")
    if card.frame is not None:
        tags.append(f"[dim]{card.frame.value}[/]")
    return " ".join(tags)


def _card_json(card: Card, batch: report.Batch | None) -> dict[str, Any]:
    """One card as data — the same shape ``/api/cards`` serves the web UI."""
    names = card.face_names()
    return {
        "id": card.id,
        "name": card.name.title(),
        "game": card.game.value,
        "set": card.set_id,
        "layout": card.layout.value,
        "oversized": card.oversized,
        "frame": card.frame.value if card.frame else None,
        "front_face": card.front_face,
        "faces": [
            {
                "index": f,
                "name": names[f] or ("front" if f == FRONT else "back"),
                "status": {s.label: card.status(s, f).value for s in _STAGES},
            }
            for f in card.faces
        ],
        "status": {s.label: card.rollup(s).value for s in _STAGES},
        "batch": batch.name if batch else None,
        "printed": bool(batch and batch.printed),
    }


def _tally(cards: Sequence[Card]) -> None:
    """Where this listing stands, per stage — counted over sides, because a
    two-sided card is two jobs, not one."""
    sides = [(card, f) for card in cards for f in card.faces]
    if not sides:
        return
    parts: list[str] = []
    for stage in _STAGES:
        done = sum(1 for c, f in sides if c.status(stage, f) is Status.DONE)
        parts.append(f"{stage.label} [green]{done}[/]/{len(sides)}")
    ready = sum(1 for c in cards if _sheet_ready(c, c.front_face))
    console.print(
        f"[dim]{len(cards)} cards · {len(sides)} sides · "
        + " · ".join(parts)
        + f" · [/][bold]{ready}[/] [dim]ready to print[/]"
    )


@cli.command()
@click.argument("cid", metavar="ID")
@_GAME
@click.pass_context
def show(ctx: click.Context, cid: str, game: str | None) -> None:
    """Everything the card's API says about one card, plus its local state.

    The terminal twin of the card page's data sheet: every field of the response
    worth reading, the links the provider hands out, and the other cards this one
    is printed alongside — a meld partner, the melded card, the tokens it makes.

    [dim]  proxdex show inr-14[/]
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    known = lib.find(cid)
    detail = sources.details(
        cid, cfg, games.parse(game) or (known.game if known else None)
    )
    meta = detail.meta
    console.print(
        f"[bold]{meta.name}[/]  [dim]{meta.id}[/]\n"
        f"{meta.set_name} [dim]({meta.set_id})[/] · {games.get(meta.game).name} "
        f"[dim]· {detail.source}[/]"
    )
    kind = [meta.layout.label] + (["oversized"] if meta.oversized else [])
    console.print(
        f"[cyan]{' · '.join(kind)}[/] [dim]{meta.layout.note}[/]\n"
        + (
            "[dim]frame: [/]"
            + frames.GUIDES[meta.frame].name
            + " [dim](from the printing)[/]\n"
            if meta.frame is not None
            else ""
        )
        + (
            "[dim]sides: [/]" + " · ".join(f.name or "front" for f in meta.faces) + "\n"
            if len(meta.faces) > 1
            else ""
        ),
        end="",
    )
    if known is not None:
        console.print(
            f"[dim]in this library:[/] {known.dir.relative_to(lib.root)}  "
            + "  ".join(f"{_label(known, f)} {_dots(known, f)}" for f in known.faces)
        )
    else:
        console.print(f"[dim]not in this library — `proxdex fetch {meta.id}`[/]")

    for group in detail.groups:
        console.print(f"\n[bold]{group.title}[/]")
        table = Table(box=None, pad_edge=False, show_header=False)
        table.add_column("", style="dim", no_wrap=True)
        table.add_column("")
        for fact in group.facts:
            table.add_row(fact.label, fact.value)
        console.print(table)
    if detail.related:
        console.print("\n[bold]Printed alongside[/]")
        table = Table(box=None, pad_edge=False, show_header=False)
        table.add_column("", style="dim", no_wrap=True)
        table.add_column("")
        table.add_column("", style="dim")
        for rel in detail.related:
            have = "✓ in library" if rel.id and lib.find(rel.id) else ""
            table.add_row(rel.relation.label, rel.name, f"{rel.id} {have}".strip())
        console.print(table)
        console.print(f"[dim]`proxdex fetch --related {meta.id}` adds them all[/]")
    if detail.links:
        console.print("\n[bold]Links[/]")
        for link in detail.links:
            console.print(f"  [dim]{link.label}[/]  {link.url}")


@cli.command("rm")
@click.argument("ids", nargs=-1, required=True, metavar="ID...")
@click.option("-y", "--yes", is_flag=True, help="Delete without confirming.")
@click.pass_context
def rm(ctx: click.Context, ids: tuple[str, ...], yes: bool) -> None:
    """Delete cards from the library — every stage image and marker they own.

    This removes work, not just state, so it lists what would go and asks first.
    Nothing else in proxdex deletes a folder.
    """
    lib = _lib(ctx)
    cards = [c for c in (lib.find(cid) for cid in ids) if c is not None]
    unknown = [cid for cid in ids if lib.find(cid) is None]
    for cid in unknown:
        err.print(f"[yellow]SKIPPED[/] {cid}: not in this library")
    if not cards:
        return
    for card in cards:
        stages = sum(1 for f in card.faces for s in _STAGES if card.has(s, f))
        console.print(
            f"  [red]-[/] {card.id} {card.name.title()} "
            f"[dim]({stages} stage image(s), {card.dir.relative_to(lib.root)})[/]"
        )
    if not yes:
        if not sys.stdin.isatty():
            raise click.UsageError("not a terminal — pass --yes to delete these")
        if not click.confirm(f"Delete {len(cards)} card(s)?", default=False):
            console.print("[dim]nothing deleted.[/]")
            return
    for card in cards:
        shutil.rmtree(card.dir, ignore_errors=True)
        console.print(f"[green]✓[/] deleted {card.id}")
    _reindex(lib)


@cli.command()
@click.pass_context
def batches(ctx: click.Context) -> None:
    """List the print batches in this library and whether they're printed."""
    lib = _lib(ctx)
    found = report.batches(lib)
    if not found:
        console.print("[dim]no batches yet — `proxdex sheet <name>` makes one[/]")
        return
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("Batch", "Date", "Faces", "Cards", "PDF", "Printed"):
        table.add_column(col)
    for batch in found:
        table.add_row(
            batch.name,
            batch.date,
            batch.faces.value,
            str(len(batch.cards)),
            ", ".join(p.name for p in batch.pdfs),
            f"[green]✓ {batch.printed_date}[/]" if batch.printed else "[dim]queued[/]",
        )
    console.print(table)
    console.print("[dim]mark one printed with `proxdex printed <name>`[/]")


@cli.group("config")
def config_cmd() -> None:
    """Read and write this library's [cyan]proxdex.toml[/].

    The same settings the web UI's settings screen edits, spelled as
    ``[section] key``. Every value is coerced through the option's own declared
    type before it is written, so a typo fails here rather than at print time.
    """


@config_cmd.command("show")
@click.argument("match", required=False, metavar="[TEXT]")
@click.pass_context
def config_show(ctx: click.Context, match: str | None) -> None:
    """Print every setting with its value, what it means and its default."""
    lib = _lib(ctx)
    docs = Config.describe()
    text = (match or "").lower()
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("Setting", "Value", "Default", "Means"):
        table.add_column(col)
    shown = 0
    for section, key, value in _config_rows(lib):
        path = f"{section}.{key}"
        field_name = Config.field_name(section, key)
        doc = docs.get(field_name or "", {})
        label = doc.get("label", "").lower()
        if text and text not in path.lower() and text not in label:
            continue
        shown += 1
        unit = f" {doc['unit']}" if doc.get("unit") else ""
        table.add_row(
            path,
            f"{_toml_text(value)}{unit}",
            f"[dim]{doc.get('default', '')}[/]",
            doc.get("label", "[dim]—[/]"),
        )
    if not shown:
        console.print("[dim]no settings match[/]")
        return
    console.print(table)
    console.print(
        f"[dim]{lib.root / MARKER} — change one with "
        "`proxdex config set sheet.dpi=1200`[/]"
    )


@config_cmd.command("set")
@click.argument("assignments", nargs=-1, required=True, metavar="SECTION.KEY=VALUE...")
@click.pass_context
def config_set(ctx: click.Context, assignments: tuple[str, ...]) -> None:
    """Write settings into proxdex.toml, keeping its comments.

    [dim]  proxdex config set sheet.dpi=1200 sheet.faces=duplex[/]
    """
    import tomlkit

    lib = _lib(ctx)
    path = lib.root / MARKER
    doc = tomlkit.parse(path.read_text() if path.exists() else "")
    for raw in assignments:
        path_part, sep, value = raw.partition("=")
        if not sep:
            raise click.UsageError(f"{raw!r}: expected SECTION.KEY=VALUE")
        section, dot, key = path_part.strip().partition(".")
        if not dot:
            raise click.UsageError(f"{path_part!r}: expected SECTION.KEY")
        field_name = Config.field_name(section, key)
        if field_name is None:
            raise click.UsageError(
                f"[{section}] {key} is not a proxdex setting — "
                "`proxdex config show` lists every one"
            )
        # coerced through the field's own annotation, so the file only ever holds
        # the declared type and a bad value names the valid options right here
        clean = Config.coerce(field_name, _toml_value(value.strip()))
        if section not in doc:
            doc[section] = tomlkit.table()
        doc[section][key] = clean.value if isinstance(clean, Enum) else clean
        console.print(f"[green]✓[/] \\[{section}] {key} = {_toml_text(clean)}")
    path.write_text(tomlkit.dumps(doc))
    console.print(f"[dim]wrote {path}[/]")


def _config_rows(lib: Library) -> list[tuple[str, str, Any]]:
    """Every ``[section] key`` in this library's TOML, in file order."""
    path = lib.root / MARKER
    if not path.exists():
        return []
    doc = tomllib.loads(path.read_text())
    return [
        (section, key, value)
        for section, table in doc.items()
        if isinstance(table, dict)
        for key, value in table.items()
    ]


def _toml_value(text: str) -> Any:
    """A CLI word as the TOML scalar it spells — Config.coerce types it after."""
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    for cast_to in (int, float):
        try:
            return cast_to(text)
        except ValueError:
            continue
    return text


def _toml_text(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_text(v) for v in cast("list[Any]", value)) + "]"
    return str(value)


@cli.command()
@click.argument("ids", nargs=-1, metavar="[ID...]")
@steps.click_options("upscale")
@_FACE
@click.option("--force", is_flag=True, help="Re-upscale even if it exists.")
@click.pass_context
def upscale(
    ctx: click.Context,
    ids: tuple[str, ...],
    model: str | None,
    scale: str | None,
    double: bool | None,
    face: int | None,
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
    # the registry coerces each flag into its enum, or falls back to this
    # library's config — so only well-typed values reach upscayl-bin
    opts = steps.resolve("upscale", cfg, model=model, scale=scale, double=double)
    use_model = cast("UpscaylModel", opts["model"])
    use_scale = cast("UpscaylScale", opts["scale"])
    use_double = bool(opts["double"])
    tag = f"{use_model.value} ×{use_scale.value}{' ×2' if use_double else ''}"

    def one(card: Card) -> None:
        for f in _faces(card, face):
            src = card.best(Stage.BORDERED, Stage.ORIGINAL, face=f)
            if src is None:
                raise FileError(f"{_label(card, f)}: no original yet (fetch it first)")
            dst = card.stage_path(Stage.UPSCALED, f)
            if dst.exists() and not force:
                console.print(f"[dim]· {_label(card, f)}: already upscaled[/]")
                continue
            upscale_mod.run(
                src, dst, cfg, model=use_model, scale=use_scale, double=use_double
            )
            card.clear_skip(Stage.UPSCALED, f)
            _cascade(card, Stage.UPSCALED, f)
            console.print(
                f"[green]✓[/] {_label(card, f)}: upscaled [dim]({tag})[/] → "
                f"{dst.relative_to(lib.root)}"
            )

    _each(lib.select(ids), one, "upscaling")
    _reindex(lib)


@cli.command()
@click.argument("ids", nargs=-1, metavar="[ID...]")
@steps.click_options("grade")
@_FACE
@click.option("--force", is_flag=True, help="Re-grade even if stage 3 exists.")
@click.pass_context
def grade(
    ctx: click.Context,
    ids: tuple[str, ...],
    normalize: bool | None,
    face: int | None,
    force: bool,
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
    do_norm = bool(steps.resolve("grade", cfg, normalize=normalize)["normalize"])
    # dynamic target: the collection's own median frame colour (unless pinned)
    frame_target = None
    if do_norm and not cfg.match_border_target:
        frame_target = _library_frame_target(lib)

    def one(card: Card) -> None:
        for f in _faces(card, face):
            dst = card.stage_path(Stage.EDITED, f)
            if dst.exists() and not force:
                console.print(f"[dim]· {_label(card, f)}: already graded[/]")
                continue
            src = card.best(Stage.UPSCALED, Stage.BORDERED, Stage.ORIGINAL, face=f)
            if src is None:
                raise FileError(f"{_label(card, f)}: nothing to grade yet")
            out = grade_mod.grade(
                Image.open(src), cfg, frame_target=frame_target, normalize=do_norm
            )
            out.save(dst)
            card.clear_skip(Stage.EDITED, f)
            console.print(
                f"[green]✓[/] {_label(card, f)}: graded → {dst.relative_to(lib.root)}"
            )

    _each(lib.select(ids), one, "grading")
    _reindex(lib)


@cli.command()
@click.argument("step", type=_STEP_CHOICE)
@click.argument("ids", nargs=-1, metavar="[ID...]")
@_FACE
@click.pass_context
def skip(ctx: click.Context, step: str, ids: tuple[str, ...], face: int | None) -> None:
    """Bypass a processing step: drop its output and mark it skipped.

    A skipped step contributes nothing — the next step reads the earlier stage
    instead. Undo with [cyan]unskip[/], or just run the step again to redo it.
    """
    lib = _lib(ctx)
    stage = Step(step).stage
    cards = lib.select(ids)
    for card in cards:
        for f in _some_faces(card, face):
            card.mark_skip(stage, f)
            console.print(f"[yellow]⤳[/] {_label(card, f)}: {step} skipped")
            _cascade(card, stage, f)
    if not cards:
        console.print("[dim]no cards[/]")
    _reindex(lib)


@cli.command()
@click.argument("step", type=_STEP_CHOICE)
@click.argument("ids", nargs=-1, metavar="[ID...]")
@_FACE
@click.pass_context
def unskip(
    ctx: click.Context, step: str, ids: tuple[str, ...], face: int | None
) -> None:
    """Clear a step's skip mark → pending (the output is not restored)."""
    lib = _lib(ctx)
    stage = Step(step).stage
    for card in lib.select(ids):
        for f in _some_faces(card, face):
            if card.skipped(stage, f):
                card.clear_skip(stage, f)
                console.print(
                    f"[green]○[/] {_label(card, f)}: {step} no longer skipped"
                )
    _reindex(lib)


@cli.command()
@click.argument("step", type=_STEP_CHOICE)
@click.argument("ids", nargs=-1, metavar="[ID...]")
@_FACE
@click.pass_context
def reset(
    ctx: click.Context, step: str, ids: tuple[str, ...], face: int | None
) -> None:
    """Return a step to pending: delete its output and clear any skip mark."""
    lib = _lib(ctx)
    stage = Step(step).stage
    for card in lib.select(ids):
        for f in _some_faces(card, face):
            if card.has(stage, f) or card.skipped(stage, f):
                card.reset(stage, f)
                console.print(f"[green]○[/] {_label(card, f)}: {step} reset to pending")
                _cascade(card, stage, f)
    _reindex(lib)


@cli.command()
@click.argument("ids", nargs=-1, metavar="[ID...]")
@click.option(
    "--face",
    type=int,
    default=None,
    metavar="N",
    help="Print side N on the front ([cyan]1[/] or [cyan]2[/]). Default: swap.",
)
@click.pass_context
def flip(ctx: click.Context, ids: tuple[str, ...], face: int | None) -> None:
    """Choose which side of a two-sided card prints on the front of a sheet.

    A transform card has two real fronts and no back of its own, so which one
    goes on the paper is your call. [cyan]sheet --faces duplex[/] then prints the
    other side on the reverse; a one-sided card keeps the configured card back.
    """
    lib = _lib(ctx)
    for card in lib.select(ids):
        if len(card.faces) < 2:
            console.print(f"[dim]· {card.id}: one side, nothing to flip[/]")
            continue
        want = _faces(card, face)[0] if face is not None else card.back_face or FRONT
        card.set_front_face(want)
        names = card.face_names()
        console.print(
            f"[green]✓[/] {card.id}: printing [bold]{names[want] or want + 1}[/] "
            "on the front"
        )
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


def _warn_unmeasured(card: Card, guide: FrameGuide) -> None:
    """Say out loud when a reshape is running against a guessed frame spec."""
    if guide.measured:
        return
    err.print(
        f"[yellow]⚠[/] {card.id}: no measured frame spec for "
        f"[bold]{card.set_id}[/] ({games.get(card.game).name}) — using "
        f"'{guide.id.value}'. [dim]{guide.note} See `proxdex frames`.[/]"
    )


@cli.command(name="frames")
@click.pass_context
def frames_cmd(ctx: click.Context) -> None:
    """List the frame specs and which of your sets have a measured one.

    [cyan]border --inner-*[/] reshapes each card to its set's real border
    widths, so a set with no measured spec is fitted against an *estimate*.
    This shows which is which; override per run with
    [cyan]border --frame <id>[/].
    """
    lib = _lib(ctx)
    known = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("Spec", "Game", "Border T/R/B/L (mm)", "Confidence"):
        known.add_column(col)
    for guide in frames.GUIDES.values():
        top, right, bottom, left = guide.mm()
        known.add_row(
            guide.id.value,
            games.get(guide.game).name if guide.game else "any",
            f"{top:.2f} / {right:.2f} / {bottom:.2f} / {left:.2f}",
            "[green]measured[/]" if guide.measured else "[yellow]estimated[/]",
        )
    console.print(known)

    cards = lib.cards()
    if not cards:
        return
    # keyed by the card's own override too: a borderless print inside a bordered
    # set resolves differently from its neighbours, and hiding that would make the
    # table claim a fit that isn't what runs
    seen: dict[tuple[GameId, str, str], tuple[FrameGuide, int]] = {}
    for card in cards:
        guide = frames.resolve(card.set_id, card.game, card.frame)
        key = (card.game, card.set_id, card.frame.value if card.frame else "")
        _, count = seen.get(key, (guide, 0))
        seen[key] = (guide, count + 1)
    mine = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("Set", "Game", "Cards", "Resolves to", "From", "Confidence"):
        mine.add_column(col)
    for (game, set_id, override), (guide, count) in sorted(seen.items()):
        mine.add_row(
            set_id,
            games.get(game).name,
            str(count),
            guide.id.value,
            "the printing" if override else "its era",
            "[green]measured[/]" if guide.measured else "[yellow]estimated[/]",
        )
    console.print("\n[bold]Sets in this library[/]")
    console.print(mine)
    unmeasured = sum(1 for guide, _ in seen.values() if not guide.measured)
    if unmeasured:
        console.print(
            f"[yellow]{unmeasured}[/] set(s) fall back to an estimated spec — "
            "measure a real card and add a guide in [cyan]frames.py[/] to fix."
        )


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
@click.option(
    "--auto",
    "auto",
    is_flag=True,
    help="Measure where the border currently sits from the image itself instead "
    "of marking it by hand. Reports how much the measurement can be trusted.",
)
@steps.click_options("border")
@_FACE
@click.option("--force", is_flag=True, help="Re-run even if a bordered image exists.")
@click.option("--dry-run", is_flag=True, help="Report the plan; don't write.")
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
    auto: bool,
    stretch: bool | None,
    frame: str | None,
    face: int | None,
    force: bool,
    dry_run: bool,
) -> None:
    """Reshape a card → stage 2 (bordered), before upscaling.

    Three ways to say where the border is:

    • [cyan]--inner-top/-right/-bottom/-left[/] <fraction 0-1>: where the card's
    inner border edge currently sits. From the card's frame spec (its game +
    era, see [cyan]proxdex frames[/]) [cyan]cardbleed[/] reshapes to the exact
    card aspect with the correct border widths (add [cyan]--stretch[/] to hit
    the borders exactly by un-distorting the art). Sets whose spec has not been
    measured are called out — the fit still runs, but on an estimate.

    • [cyan]--auto[/]: measure those four numbers off the image instead of typing
    them. Each edge reports how much its scan lines agreed, so a card the
    measurement does not suit says which edge to check. Pair it with
    [cyan]--dry-run[/] to measure and write nothing.

    • [cyan]--top/--bottom/--left/--right[/] <mm>: just add that much border to
    each edge — no fit, no distortion.

    [cyan]--dry-run[/] reports the plan without writing.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    inner = (inner_top, inner_right, inner_bottom, inner_left)
    use_inner = any(v is not None for v in inner)
    if use_inner and not all(v is not None for v in inner):
        raise click.UsageError("give all four --inner-top/-right/-bottom/-left or none")
    grow_mm = {"top": top_mm, "right": right_mm, "bottom": bottom_mm, "left": left_mm}
    if auto and use_inner:
        raise click.UsageError(
            "--auto measures the inner border itself — drop --inner-*, or drop "
            "--auto to keep your own numbers"
        )
    if auto and max(grow_mm.values()) > 0:
        raise click.UsageError(
            "--auto fits to the frame spec; --top/--bottom/--left/--right only "
            "add millimetres. Pick one."
        )
    override = frames.parse(frame)
    do_stretch = bool(stretch)

    def one_face(card: Card, f: int) -> None:
        dst = card.stage_path(Stage.BORDERED, f)
        name = _label(card, f)
        if dst.exists() and not force and not dry_run:
            console.print(f"[dim]· {name}: already bordered[/]")
            return
        src = card.stage_path(Stage.ORIGINAL, f)
        if not src.exists():
            raise FileError(f"{name}: no original yet (fetch it first)")
        w, h = borders.size(src)
        marks = cast("tuple[float, float, float, float]", inner) if use_inner else None
        # the card's own frame beats its set's era: a borderless print has no
        # frame to fit whatever era the rest of its set belongs to
        recorded = override or card.frame
        if auto:
            spec = frames.resolve(card.set_id, card.game, recorded)
            if not any(spec.inset):
                # a borderless print has no frame to match, so there is nothing to
                # measure: the marks are the image edges and the fit is pure
                # aspect correction. Measuring anyway would find the art's own
                # edge and crop the card to it.
                marks = (0.0, 0.0, 0.0, 0.0)
                console.print(
                    f"  [dim]⌖ {name}: {spec.name} — nothing to measure, "
                    "reshaping to the card aspect only[/]"
                )
            else:
                found = borders.detect_inset(src)
                tone = "green" if found.reliable else "yellow"
                console.print(f"  [{tone}]⌖[/] {name}: {found.note}")
                marks = found.inset
                if found.frameless:
                    recorded = GuideId.BORDERLESS
        if marks is not None:
            guide = frames.resolve(card.set_id, card.game, recorded)
            _warn_unmeasured(card, guide)
            inner_t = marks
            plan = bleed.fit_plan(w, h, guide, inner_t, cfg, stretch=do_stretch)
            tw, th = round(plan.trim_w), round(plan.trim_h)
            bd = plan.borders
            tag = f"{guide.name}{', stretch' if do_stretch else ''}"
            note = (
                f"fit → {tw}×{th}px  "
                f"T{bd['top'] * 100:.2f} R{bd['right'] * 100:.2f} "
                f"B{bd['bottom'] * 100:.2f} L{bd['left'] * 100:.2f}%  [dim]({tag})[/]"
            )
            if plan.cropped:
                note += f" [yellow](cropped {', '.join(plan.cropped)})[/]"
            if dry_run:
                console.print(f"[cyan]{name}[/]: {note}")
                return
            bleed.fit(src, dst, guide, inner_t, cfg, stretch=do_stretch)
        else:
            if max(grow_mm.values()) <= 0:
                console.print(f"[dim]· {name}: nothing to expand[/]")
                return
            note = " ".join(f"+{e[0].upper()}{v:g}" for e, v in grow_mm.items()) + "mm"
            if dry_run:
                console.print(f"[cyan]{name}[/]: {note}")
                return
            bleed.grow(src, dst, cfg, **grow_mm)
        card.clear_skip(Stage.BORDERED, f)
        _cascade(card, Stage.BORDERED, f)
        console.print(f"[green]✓[/] {name}: {note} → {dst.relative_to(lib.root)}")

    def one(card: Card) -> None:
        for f in _faces(card, face):
            one_face(card, f)

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


def _write_batch(path: Path, data: dict[str, object]) -> None:
    """Write a batch manifest.

    Through tomlkit rather than by hand: a note or printer name can contain a
    quote, a backslash or a newline, and a manifest that no longer parses would
    lose the record of what was printed.
    """
    import tomlkit

    cards = data.get("cards", [])
    doc = tomlkit.document()
    doc["name"] = str(data.get("name", ""))
    doc["date"] = str(data.get("date", ""))
    doc["faces"] = str(data.get("faces", Faces.FRONTS.value))
    doc["printed"] = bool(data.get("printed"))
    for key in ("printed_date", "paper", "printer", "notes"):
        doc[key] = str(data.get(key, ""))
    doc["pdf"] = str(data.get("pdf", "fronts.pdf"))
    doc["cards"] = [str(cid) for cid in (cards if isinstance(cards, list) else [])]
    path.write_text(tomlkit.dumps(doc))


def _back_path(root: Path, game: GameId) -> Path:
    """Where `proxdex back --game <g>` stores that game's shared back.

    A mixed library needs one per game — Pokémon and MTG backs are different
    pictures — so they can't share a single ``back.png``.
    """
    return root / f"back-{game.value}.png"


def _resolve_back_path(card: Card, cfg: Config, lib: Library) -> Path | None:
    """Per-card ``<id>_back.png``, then ``[sheet] back_image``, then the card
    game's shared back, then the legacy single ``back.png``."""
    candidates = [card.dir / f"{card.id}_back.png"]
    if cfg.sheet_back_image:
        shared = Path(cfg.sheet_back_image)
        candidates.append(shared if shared.is_absolute() else lib.root / shared)
    candidates.append(_back_path(lib.root, card.game))
    candidates.append(lib.root / "back.png")
    return next((p for p in candidates if p.exists()), None)


def _reverse_path(card: Card, cfg: Config, lib: Library) -> Path | None:
    """What goes on the reverse of this card in a duplex sheet.

    A two-sided card's other side is a real card face, so it prints there — that
    is the whole point of a transform card. Anything else takes the shared card
    back, which is what a Pokémon card or a normal MTG card wants.
    """
    reverse = card.back_face
    if reverse is not None and _sheet_ready(card, reverse):
        return _master(card, reverse)
    return _resolve_back_path(card, cfg, lib)


def _trim_mm(card: Card, cfg: Config) -> tuple[float, float]:
    """The physical size this card prints at.

    Ordinary cards are the configured trim; an oversized card is its own real
    size, because a planar card imposed into a 63×88 cell is not that card — it
    is a small, wrong one. Nothing has to be configured for this: the size came
    from the provider at fetch time and lives in the card's own marker.
    """
    if card.oversized:
        return (games.OVERSIZED_W_MM, games.OVERSIZED_H_MM)
    return (cfg.card_w_mm, cfg.card_h_mm)


@dataclass(slots=True)
class _Repro:
    """The print-time reproduction: fit any master to trim, colour-correct for
    the medium, then extend cut bleed outside the trim with cardbleed."""

    cfg: Config
    profile: str
    recipe: media.Recipe
    cal: calibrate_mod.Stage | None
    tmpdir: Path

    def cell(self, master: Image.Image, trim: tuple[float, float]) -> Image.Image:
        cfg = self.cfg
        ppm = cfg.sheet_dpi / 25.4
        trim_w, trim_h = round(trim[0] * ppm), round(trim[1] * ppm)
        im = sheet_mod.fit(master, trim_w, trim_h, cfg.sheet_fit)
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
        bleed.cut_bleed(src, dst, cfg, bp)
        return Image.open(dst).convert("RGB")


@cli.command()
@click.option("--url", default=None, help="Download the back image from this URL.")
@click.option(
    "--file",
    "file_path",
    type=UserPath(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Use a local image as the back.",
)
@_GAME
@click.option(
    "-o",
    "--out",
    "out",
    type=UserPath(path_type=Path),
    default=None,
    help="Where to save (default: [cyan]<lib>/back-<game>.png[/]).",
)
@click.pass_context
def back(
    ctx: click.Context,
    url: str | None,
    file_path: Path | None,
    game: str | None,
    out: Path | None,
) -> None:
    """Set a game's shared card back — a trim-size master.

    Backs are per game (a mixed library needs both), stored as
    [cyan]back-<game>.png[/] and picked automatically by each card's game.
    This just fetches/imports and stores the image; colour correction and cut
    bleed are applied at [cyan]sheet[/] time (exactly like the fronts), so
    front and back match on the medium.

    With no [cyan]--file[/]/[cyan]--url[/] the game's own source is used —
    Scryfall's standard back for MTG. There is no reliable Pokémon-back API
    (the back is one image owned by TPC), so supply your own scan there. A
    per-card [cyan]<id>_back.png[/] overrides the shared one.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    want = games.coerce(game, cfg.library_game)
    if not url and not file_path:
        url = games.get(want).back_url
    if file_path:
        im = Image.open(file_path).convert("RGB")
    elif not url:
        raise click.UsageError(
            f"no downloadable back for {games.get(want).name} — give --file or "
            "--url with your own scan"
        )
    else:
        import io

        try:
            resp = net.get(url, accept="image/*")
        except net.NetworkError as exc:
            raise ProxdexError(f"download failed: {exc}") from exc
        if not resp.ok:
            raise ProxdexError(f"download failed ({resp.status}) for {url}")
        im = Image.open(io.BytesIO(resp.body)).convert("RGB")

    dst = out or _back_path(lib.root, want)
    im.save(dst)
    console.print(
        f"[green]✓[/] {games.get(want).name} card back → "
        f"{dst.relative_to(lib.root)} [dim](colour + bleed applied at sheet "
        "time)[/]"
    )


@cli.command()
@click.argument("name")
@click.argument("ids", nargs=-1, metavar="[ID...]")
@click.option(
    "--faces",
    type=click.Choice([f.value for f in Faces]),
    default=None,
    help="What to impose (default from [sheet]).",
)
@click.option(
    "--page",
    type=click.Choice([p.value for p in PageSize]),
    default=None,
    help="Page size override.",
)
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
    card edge. Fronts, backs, or duplex (back pages mirrored + offset).

    A two-sided card contributes the side [cyan]proxdex flip[/] points at, and in
    a duplex sheet its *other* side prints on the reverse instead of the shared
    card back. proxdex owns the PDF — print with colour management OFF for
    calibration to hold.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    if page:
        cfg.sheet_page = PageSize(page)
    if faces:
        cfg.sheet_faces = Faces(faces)
    if dpi:
        cfg.sheet_dpi = dpi
    cards = lib.select(ids) if ids else lib.cards()
    # each card contributes the side it is flipped to; `proxdex flip` chooses
    ready = [c for c in cards if _sheet_ready(c, c.front_face)]
    missing = [c.id for c in cards if not _sheet_ready(c, c.front_face)]
    if missing:
        err.print(
            f"[yellow]not ready, skipping:[/] {', '.join(missing)} "
            "[dim](finish grade, or `proxdex skip grade`)[/]"
        )
    if not ready:
        raise click.UsageError(
            "no card masters to impose — run upscale/grade, or skip them"
        )
    # an oversized card prints at its own size, on its own pages — say which and
    # how many fit, because that is a page count the user did not ask for
    big = [c for c in ready if c.oversized]
    if big:
        cols, rows = sheet_mod.grid_for(
            cfg, (games.OVERSIZED_W_MM, games.OVERSIZED_H_MM)
        )
        console.print(
            f"[cyan]⬗[/] {len(big)} oversized card(s) — "
            f"{', '.join(c.id for c in big)} — print at "
            f"{games.OVERSIZED_W_MM:g}×{games.OVERSIZED_H_MM:g}mm on their own "
            f"pages [dim]({cols}×{rows} per page)[/]"
        )
    two_sided = [c for c in ready if c.back_face is not None]
    if two_sided and cfg.sheet_faces is not Faces.DUPLEX:
        err.print(
            f"[yellow]⚠[/] {len(two_sided)} two-sided card(s) — only the flipped-to "
            "side is in this sheet. [dim]`--faces duplex` prints the reverse too; "
            "`proxdex flip` swaps which side is the front.[/]"
        )

    prof_name, recipe = media.resolve(cfg, profile)
    cal = calibrate_mod.load(_cal_dir(lib), prof_name) if prof_name != "none" else None
    if prof_name != "none" and cal is None and prof_name not in media.PROFILES:
        err.print(f"[yellow]note[/] '{prof_name}' has no calibration or preset")

    tmpdir = Path(tempfile.mkdtemp(prefix="proxdex-sheet-"))
    try:
        repro = _Repro(cfg, prof_name, recipe, cal, tmpdir)
        trims = [_trim_mm(c, cfg) for c in ready]
        # the back of a card is reproduced at that card's own size, so the cache is
        # keyed by both — one shared back image serves two trims in a mixed batch
        cache: dict[tuple[Path, tuple[float, float]], Image.Image] = {}
        backs: list[Image.Image | None] = [None] * len(ready)
        if cfg.sheet_faces in ("backs", "duplex"):
            # a two-sided card's reverse IS its back; everything else takes the
            # shared card back for its game
            paths = [_reverse_path(c, cfg, lib) for c in ready]
            no_back = [c.id for c, p in zip(ready, paths, strict=True) if p is None]
            if no_back:
                raise click.UsageError(
                    f"{cfg.sheet_faces} needs backs, none for: {', '.join(no_back)}"
                    " — `proxdex back ...`, [sheet] back_image, or <id>_back.png"
                )
            for i, (path, trim) in enumerate(zip(paths, trims, strict=True)):
                if path is None:
                    continue
                key = (path, trim)
                if key not in cache:
                    cache[key] = repro.cell(Image.open(path).convert("RGB"), trim)
                backs[i] = cache[key]
        cells = [
            sheet_mod.Cell(
                front=repro.cell(
                    Image.open(cast("Path", _master(c, c.front_face))).convert("RGB"),
                    trim,
                ),
                back=back,
                trim=trim,
            )
            for c, trim, back in zip(ready, trims, backs, strict=True)
        ]

        slug = slugify(name)
        today = date.today().isoformat()
        bdir = lib.batches_dir / f"{today}_{slug}"
        bdir.mkdir(parents=True, exist_ok=True)
        pdf = bdir / f"{cfg.sheet_faces}.pdf"
        n_pages = sheet_mod.impose_to_pdf(cells, cfg, pdf)
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
    type=UserPath(exists=True, dir_okay=False, path_type=Path),
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
    type=UserPath(path_type=Path),
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
    """The card id a filename starts with — ``ex3-90``, ``neo-136``, ``bw11-1a``.

    MTG collector numbers can carry a letter suffix, so one is allowed; ids
    with anything stranger in them need an explicit ``--id``.
    """
    m = re.match(r"[a-z]+\d*-\d+[a-z]?", stem, re.IGNORECASE)
    return m.group(0) if m else None


def _hoist_root(args: Sequence[str]) -> list[str]:
    """Move ``--root DIR`` to the front so it works after the subcommand too.

    ``--root`` belongs to the group, so click would only accept it before the
    command name — but ``proxdex fetch ex3-90 --root DIR`` is the spelling
    people reach for (and the one the "no library here" error suggests).
    """
    root: list[str] = []
    rest: list[str] = []
    skip = False
    for i, arg in enumerate(args):
        if skip:
            skip = False
        elif arg == "--":  # everything past it is a literal argument
            rest.extend(args[i:])
            break
        elif arg == "--root" and i + 1 < len(args):
            root = ["--root", args[i + 1]]
            skip = True
        elif arg.startswith("--root="):
            root = [arg]
        else:
            rest.append(arg)
    return root + rest


def main() -> None:
    try:
        cli(args=_hoist_root(sys.argv[1:]))
    except ProxdexError as e:
        err.print(f"[bold red]error:[/] {e}")
        raise SystemExit(1) from e
    finally:
        _api_note()


if __name__ == "__main__":
    main()
