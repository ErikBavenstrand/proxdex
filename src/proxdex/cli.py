"""Command-line interface (click + rich-click)."""

from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, TypeVar, cast

import rich_click as click
from PIL import Image
from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
)
from rich.table import Table

from proxdex import (
    bleed,
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
from proxdex import calibrate as calibrate_mod
from proxdex import doctor as doctor_mod
from proxdex import grade as grade_mod
from proxdex import sheet as sheet_mod
from proxdex import upscale as upscale_mod
from proxdex._version import __version__
from proxdex.config import (
    MARKER,
    Config,
    Faces,
    Orientation,
    PageSize,
    UpscaylModel,
    UpscaylScale,
)
from proxdex.errors import FileError, ProxdexError
from proxdex.frames import FrameGuide
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
            "commands": [
                "init",
                "where",
                "ls",
                "show",
                "rm",
                "config",
                "doctor",
                "index",
                "ui",
            ],
        },
        {"name": "Acquire", "commands": ["search", "fetch", "import"]},
        {"name": "Prepare", "commands": ["border", "upscale", "grade", "frames"]},
        {"name": "Pipeline", "commands": ["skip", "unskip", "reset"]},
        {
            "name": "Produce",
            "commands": ["back", "flip", "sheet", "batches", "printed"],
        },
        {"name": "Colour", "commands": ["profile", "calibrate"]},
    ]
}

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

T = TypeVar("T")
#: a click command callback, for the decorators that bundle shared options
F = TypeVar("F", bound=Callable[..., Any])

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
    help="Which TCG to use (default: [cyan]\\[library] game[/] in proxdex.toml).",
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

[border]
# Un-distort the art so the finished borders land exactly on the frame spec,
# instead of as close as the source allows. Hitting the spec is the point of the
# step, so this is on.
stretch = true

[grade]
# One identical look over every card, so a batch prints as a set. The defaults are
# **identity** — grade changes nothing until you ask it to. A lift that suits your
# printer and paper is a real thing to want, but it is a fact about your press, and
# numbers proxdex invented for a press it has never seen are a guess wearing a label.
# Print a card, look at it, then set these. (If the correction is the same for every
# card on the sheet, it belongs in a [print] profile, not here.)
brightness = 1.0
contrast   = 1.0
saturation = 1.0
gamma      = 1.0
# Stretch a single card's own black and white points to full range, blended by
# this much (0 = off). Helps a flat, hazy scan; it reads that card only.
levels     = 0.0
# Grade does NOT try to match cards to each other by colour — a card frame is
# yellow on a Pokémon card and black on a Magic one, so there is no shared
# baseline to pull them to. Matching the *paper* is [print]'s job, at sheet time.

[card]
# 2.5x3.5in, the poker-size standard both games print at. One number: it is the
# trim AND the card a frame spec's millimetres are measured against, so a caliper
# reading of a 3.45mm border prints as a 3.45mm border.
w_mm = 63.5
h_mm = 88.9

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
open        = false        # open the PDF after writing (CLI; --no-open overrides)

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
# Which medium a sheet is corrected for, at sheet time — the stored masters stay
# neutral. "none" is the identity: no correction at all. Anything else is a profile
# you made: `proxdex profile new matte-200 --notes "..."`, then either measure it
# (`calibrate chart` → print → scan → `calibrate add`) or set four numbers by hand
# off a printed strip (`profile strip`). Nothing ships pre-filled, because numbers
# for a printer proxdex has never seen would be a guess. Everything about a medium —
# its notes, its numbers, its calibration — lives in <root>/profiles/<name>.json.
profile = "none"
# Card backs, when they are not the same medium as the fronts — the reverse of a
# one-sided glossy stock, or a backs-only run on other paper. Empty = same as above.
back_profile = ""

[tools]
# Upscayl (the upscale stage). On macOS the bundled binary and models are
# auto-detected; set explicit paths on other platforms.
# One of Upscayl's built-in models: upscayl-standard-4x | upscayl-lite-4x |
# high-fidelity-4x | remacri-4x | ultramix-balanced-4x | ultrasharp-4x |
# digital-art-4x. Anything else fails at load, naming these.
upscayl_model = "digital-art-4x"
# The resolution every master must *clear*, in dots per inch of the finished card —
# not a fixed factor. Sources arrive anywhere from 400 to 745px wide, so one factor
# scatters the masters it makes (592 dpi on one card and 1011 on another, same
# settings). The step picks the smallest factor that clears this, per card. 1000 dpi
# is 2480px across a 63mm card. Set 0 to use upscayl_scale verbatim instead.
upscayl_min_dpi = 1000
upscayl_double = true             # run the model twice (2x twice = 4x, up to 16x)
upscayl_scale = 2                 # 1 | 2 | 3 | 4 — the fallback when there is no target
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


def _open_locally(path: Path) -> None:
    """Hand a written file to whatever this desktop opens it with.

    Only the CLI does this, and only on the machine you typed the command on:
    ``open`` is macOS', ``xdg-open`` is the freedesktop one, and Windows has
    ``os.startfile``. It is best-effort — a headless box has none of them, and
    failing to launch a viewer must never fail the command that produced the
    file. The web UI has no equivalent *by design*: see the note in `sheet`.
    """
    with contextlib.suppress(OSError):
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 - a file this command just wrote
            return
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        if shutil.which(opener) is None:
            err.print(f"[dim]no {opener} here — the file is at {path}[/]")
            return
        subprocess.run([opener, str(path)], check=False)


def _cascade(card: Card, stage: Stage, face: int = FRONT) -> None:
    """Drop downstream outputs made stale by a change to ``stage``, and say so."""
    removed = card.invalidate_downstream(stage, face)
    if removed:
        names = ", ".join(s.label for s in removed)
        console.print(f"  [dim]↳ removed stale downstream: {names}[/]")


#: what to print, and whether a side is printable at all — declared in `sheet`,
#: because they are facts about paper rather than about the CLI
_master = sheet_mod.master
_sheet_ready = sheet_mod.print_ready


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
    marker.write_text(DEFAULT_TOML, encoding="utf-8", newline="\n")
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


def _kind_note(lib: Library, card: Card, meta: sources.CardMeta) -> None:
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
            f"  [dim]frame:[/] {_spec_name(lib, meta.frame)} "
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
    card.write_kind(
        meta.layout,
        oversized=meta.oversized,
        frame=meta.frame,
        traits=meta.traits,
    )
    _kind_note(lib, card, meta)
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
    "--open",
    "open_images",
    is_flag=True,
    help="Open the first 12 result images in your browser.",
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
@click.option(
    "--on-existing",
    "on_existing",
    type=click.Choice([o.value for o in imports.OnExisting]),
    default=imports.OnExisting.OVERWRITE.value,
    help="When that stage image is already there: replace it, or keep it.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Report what each file would become and write nothing.",
)
@click.pass_context
def import_(
    ctx: click.Context,
    paths: tuple[str, ...],
    cid: str | None,
    game: str | None,
    stage: str | None,
    face: int | None,
    move: bool,
    on_existing: str,
    dry_run: bool,
) -> None:
    """File loose images (e.g. an Upscayl output folder) into card stages.

    Where each file lands is read off its name — the card id it starts with, the
    stage ([cyan]upscayl[/] in the name → upscaled, else original) and the side
    (proxdex's own [cyan]_f2[/] suffix). [cyan]--id/--stage/--face[/] override
    that for every file in the run.

    With no [cyan]--id[/], the card folder must already exist: a guessed id is
    not enough to invent one. With [cyan]--id[/] the metadata is looked up and
    the folder created on the fly, so an arbitrarily-named scan files fine:

    [dim]  proxdex import my-scan.png --id ex6-105 --stage original[/]

    [cyan]--dry-run[/] prints the plan — one row per file, with what it replaces
    and what it invalidates — and writes nothing. It is the same plan the web
    UI's import wizard shows, so the two cannot promise different outcomes.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    want = games.parse(game)
    existing = imports.OnExisting(on_existing)
    files: list[Path] = [
        Path(match)
        for pattern in paths
        # glob.glob handles user-supplied shell patterns (e.g. ~/dump/*.png)
        for match in glob.glob(str(Path(pattern).expanduser()))  # noqa: PTH207
    ]
    if not files:
        raise click.UsageError("no files matched")
    run = imports.plan(
        lib,
        [
            imports.Item(
                name=str(f),
                id=cid,
                game=want,
                stage=STAGE_BY_LABEL[stage] if stage else None,
                # --face is 1-based everywhere it is typed; faces are 0-based inside
                face=face - 1 if face is not None else None,
            )
            for f in files
        ],
        on_existing=existing,
    )
    _import_plan(run, lib)
    if dry_run:
        console.print("[dim]dry run — nothing written.[/]")
        return
    if not run.ready:
        raise click.UsageError("nothing to import — see the plan above")

    def one(planned: imports.Assignment) -> None:
        source = Path(planned.item.name)
        assert planned.id is not None  # noqa: S101 (a writing plan always has one)
        card = _ensure_card(lib, cfg, planned.id, want) if cid else lib.find(planned.id)
        if card is None:  # the library changed under a plan made a moment ago
            raise FileError(f"{planned.id}: no card folder any more")
        # a card the plan created is only now known to have one side or two, so
        # the side is checked here as well as in the plan
        _faces(card, planned.face + 1)
        dst = card.stage_path(planned.stage, planned.face)
        (shutil.move if move else shutil.copy2)(str(source), str(dst))
        _flatten_filed(dst)
        card.clear_skip(planned.stage, planned.face)
        _cascade(card, planned.stage, planned.face)
        console.print(
            f"[green]✓[/] {source.name} → {dst.relative_to(lib.root)} "
            f"[dim](stage {planned.stage.value} {planned.stage.label})[/]"
        )

    failed = _each(list(run.ready), one, "importing")
    console.print(
        f"[dim]{len(run.ready) - failed} filed"
        + (f", {failed} failed" if failed else "")
        + (f", {len(run.skipped)} kept" if run.skipped else "")
        + (f", {len(run.blocked)} not imported" if run.blocked else "")
        + "[/]"
    )
    _reindex(lib)


def _flatten_filed(path: Path) -> None:
    """Put a file just written into a stage in the form proxdex stores: **RGB**.

    **No stage image in the library carries an alpha channel**, and that has to be
    enforced everywhere one is written, not once at the front door. ``fetch``
    flattens on download, but three other things file images: ``import`` copies
    bytes, cardbleed passes alpha straight through, and **Upscayl emits RGBA** — so
    a card's transparent die-cut corners survived all the way to the printed sheet,
    where the corner pixels are whatever happened to be under the alpha. Measured
    on a real library that is near-white on one card (212,225,229 against a
    143,171,174 border) and near-black on an upscaled one (mean 51, min 0).

    A mode that is merely not RGB — a grayscale or CMYK scan ``import`` copied in —
    is the same problem one step further out: every tool downstream converts it its
    own way, so the pixels that reach the imposition are not the ones proxdex
    measured. Both are the one call, and both are what `proxdex doctor` reports on
    a library filed before this ran here.

    Cheap and non-destructive: a file already stored as RGB is not rewritten, so an
    imported file's bytes stay verbatim in the ordinary case.
    """
    with Image.open(path) as im:
        if im.mode == "RGB" and not sources.transparent(im):
            return
        flat = sources.flatten(im)
    flat.save(path)


def _import_plan(run: imports.Run, lib: Library) -> None:
    """Print an import plan: one row per file, and what the run costs.

    The same `imports.Run` the UI's wizard renders — a preview that could differ
    from the import is worse than no preview.
    """
    table = Table(box=None, pad_edge=False, header_style="dim")
    table.add_column("#", style="dim", justify="right")
    table.add_column("file")
    table.add_column("card")
    table.add_column("stage")
    table.add_column("side", justify="right")
    table.add_column("becomes", no_wrap=True)
    table.add_column("")
    for n, a in enumerate(run.items, 1):
        tone = "green" if a.disposition.writes else "yellow"
        if a.disposition is imports.Disposition.SKIP:
            tone = "dim"
        note = a.reason
        if a.discards:
            note += f" ↳ discards {', '.join(s.label for s in a.discards)}"
        card = a.id or "—"
        if a.guessed_id and a.id:
            card += " ?"  # the id was read off the filename, not confirmed
        table.add_row(
            str(n),
            a.name,
            card,
            a.stage.label,
            str(a.face + 1),
            f"[{tone}]{a.disposition.value}[/]",
            f"[dim]{note.strip()}[/]",
        )
    console.print(table)
    if any(a.guessed_id and a.id for a in run.items):
        console.print("[dim]? = card id read off the filename, not given[/]")
    counts = [f"{len(run.ready)} to file"]
    if run.skipped:
        counts.append(f"{len(run.skipped)} kept as-is")
    if run.blocked:
        counts.append(f"{len(run.blocked)} blocked")
    console.print(f"[dim]{lib.root.name}: {', '.join(counts)}[/]")
    if run.creates:
        console.print(
            f"[cyan]+[/] {len(run.creates)} new card folder(s) — "
            f"{', '.join(run.creates)} [dim](looked up as they are filed)[/]"
        )
    if run.discards:
        console.print(
            f"[yellow]⚠[/] {run.discards} later-stage image(s) go stale and are "
            "removed [dim](they were derived from what you are replacing)[/]"
        )


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
    # the one external tool proxdex drives, and the one step that cannot run
    # without it — so "why is upscale refusing?" is answerable here rather than
    # only at the moment it refuses
    cfg = Config.load(lib.root)
    found = upscale_mod.availability(cfg)
    # escaped: the message names `[tools] upscayl_bin`, and rich would read that
    # square bracket as a style tag and silently drop it
    if found.ready:
        console.print(
            f"upscaler  {found.backend} [green]✓[/] [dim]{escape(found.detail)}[/]"
        )
    else:
        console.print(f"upscaler  [yellow]not found[/] [dim]{escape(found.message)}[/]")
        # "where did you look?" is the next question when the app is installed
        # somewhere else — Upscayl's Windows installer lets you choose, and its
        # portable zip and the Linux AppImage have no fixed home at all. Only
        # when nothing was configured, because with `[tools] upscayl_bin` set it
        # did not look anywhere: it used what it was told.
        if not cfg.upscayl_bin:
            for exe, _ in upscale_mod.installs():
                console.print(f"          [dim]looked in {escape(str(exe))}[/]")
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
    if card.printing_frame is not None:
        tags.append(f"[dim]{card.printing_frame}[/]")
    if card.pin is not None:
        tags.append(f"[magenta]pin:{card.pin}[/]")
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
        "frame": card.printing_frame,
        "pin": card.pin,
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
            + _spec_name(lib, meta.frame)
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
    stale: list[str] = []
    for section, key, value in _config_rows(lib):
        path = f"{section}.{key}"
        field_name = Config.field_name(section, key)
        doc = docs.get(field_name or "", {})
        label = doc.get("label", "").lower()
        if field_name is None:
            stale.append(path)
        if text and text not in path.lower() and text not in label:
            continue
        shown += 1
        unit = f" {doc['unit']}" if doc.get("unit") else ""
        table.add_row(
            path,
            f"{_toml_text(value)}{unit}",
            f"[dim]{doc.get('default', '')}[/]",
            doc.get("label") or _unknown_note(field_name),
        )
    if not shown:
        console.print("[dim]no settings match[/]")
        return
    console.print(table)
    if stale:
        # a key proxdex no longer reads does nothing at all, and a file that still
        # holds it looks like it is configuring something. Say so.
        err.print(
            f"[yellow]⚠[/] {len(stale)} key(s) in this file are not proxdex "
            f"settings and are ignored: [dim]{', '.join(stale)}[/]\n"
            "[dim]  remove them with `proxdex config prune`[/]"
        )
    console.print(
        f"[dim]{lib.root / MARKER} — change one with "
        "`proxdex config set sheet.dpi=1200`[/]"
    )


def _stale_keys(lib: Library) -> list[tuple[str, str, Any]]:
    """Every ``[section] key`` in this library's file that nothing reads."""
    return [
        (section, key, value)
        for section, key, value in _config_rows(lib)
        if Config.field_name(section, key) is None
    ]


@config_cmd.command("prune")
@click.option("--yes", is_flag=True, help="Delete without asking.")
@click.pass_context
def config_prune(ctx: click.Context, yes: bool) -> None:
    """Delete the settings in proxdex.toml that nothing reads any more.

    A key left behind by a removed feature is worse than clutter: it *looks* like it
    is configuring something. Real libraries carry them — a border
    [cyan]thresh[/] and two target ratios from the auto-detector that was deleted,
    a [cyan]normalize[/] and its percentiles from the grade white-balance that turned
    a neutral grey into deep blue. Nothing has read any of them for releases, and the
    settings screen has been calling them ignored without offering to remove them.

    Only ever keys with **no** :class:`Config` field. A real setting is changed with
    [cyan]config set[/], never deleted here, so this cannot quietly reset one.
    """
    import tomlkit

    lib = _lib(ctx)
    stale = _stale_keys(lib)
    if not stale:
        console.print("[green]✓[/] nothing to prune — every key here is a real setting")
        return
    console.print(f"[bold]{len(stale)} ignored key(s)[/] in {lib.root / MARKER}")
    for section, key, value in stale:
        console.print(f"  [dim]\\[{section}][/] {key} = {_toml_text(value)}")
    if not yes and not click.confirm("Delete them?", default=False):
        console.print("[dim]left alone[/]")
        return
    path = lib.root / MARKER
    original = path.read_text(encoding="utf-8")
    doomed = {(s, k) for s, k, _ in stale}
    pruned = _prune_text(original, doomed)
    # Proved rather than trusted: the result must parse, and hold exactly the keys
    # that were there minus the ones asked for. If the line pass got confused by
    # something this file shape does not have, fall back to deleting the keys through
    # tomlkit and leaving their comments — the wrong prose beats a broken config.
    if _keys_of(pruned) != _keys_of(original) - doomed:
        doc = tomlkit.parse(original)
        for section, key, _ in stale:
            table = doc.get(section)
            if isinstance(table, dict) and key in table:
                del table[key]
        pruned = tomlkit.dumps(doc)
        err.print("[dim]kept the comments — this file is not shaped as expected[/]")
    path.write_text(pruned, encoding="utf-8", newline="\n")
    console.print(f"[green]✓[/] removed {len(stale)} key(s) from {MARKER}")


def _keys_of(text: str) -> set[tuple[str, str]]:
    """Every ``(section, key)`` in some TOML, for checking an edit did what it said."""
    doc = tomllib.loads(text)
    return {
        (section, key)
        for section, table in doc.items()
        if isinstance(table, dict)
        for key in table
    }


_TOML_SECTION = re.compile(r"^\s*\[([^\]]+)\]")


def _prune_text(text: str, doomed: set[tuple[str, str]]) -> str:
    """``text`` without ``doomed``'s keys, **or the comments explaining them**.

    A line pass rather than a tomlkit round-trip, for the comments: deleting the key
    alone leaves its explanation behind, and an orphaned comment is the same trap one
    level up. The real library's file would have ended up with "normalize: pull each
    card to a common baseline first" sitting above ``brightness``, describing a
    feature that was deleted for turning a neutral grey into deep blue. So a pruned
    key takes its own prose with it, and every other byte of the file is untouched.

    A section left holding nothing but comments goes too — ``[border]`` existed only
    for the auto-detector's three settings. The caller re-parses the result and falls
    back if any of this misread the file.
    """
    kept: list[str] = []
    section = ""
    # where each section's header sits in `kept`, and whether it gained a real key
    headers: dict[int, bool] = {}
    at_header = -1
    for line in text.split("\n"):
        found = _TOML_SECTION.match(line)
        if found:
            section = found.group(1).strip()
            at_header = len(kept)
            headers[at_header] = False
            kept.append(line)
            continue
        key, sep, _ = line.partition("=")
        name = key.strip()
        if sep and (section, name) in doomed:
            # take the contiguous comment lines written directly above it; a blank
            # line or another setting ends the block, so no heading is ever eaten
            while kept and kept[-1].lstrip().startswith("#"):
                kept.pop()
            continue
        if sep and name and not name.startswith("#") and at_header >= 0:
            headers[at_header] = True
        kept.append(line)
    return "\n".join(_drop_empty_sections(kept, headers))


def _drop_empty_sections(lines: list[str], headers: dict[int, bool]) -> list[str]:
    """Remove ``[section]`` headers that no longer have a setting under them."""
    empty = {i for i, used in headers.items() if not used}
    if not empty:
        return lines
    out: list[str] = []
    skipping = False
    for i, line in enumerate(lines):
        if i in headers:
            skipping = i in empty
            if skipping:
                # its own preceding comment block goes with it
                while out and out[-1].lstrip().startswith("#"):
                    out.pop()
                continue
        if skipping and (line.strip().startswith("#") or not line.strip()):
            continue
        skipping = False
        out.append(line)
    return out


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
    doc = tomlkit.parse(path.read_text(encoding="utf-8") if path.exists() else "")
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
    path.write_text(tomlkit.dumps(doc), encoding="utf-8", newline="\n")
    console.print(f"[dim]wrote {path}[/]")


def _write_setting(lib: Library, section: str, key: str, value: Any) -> None:
    """Write one setting into ``proxdex.toml``, keeping the file's comments."""
    import tomlkit

    path = lib.root / MARKER
    doc = tomlkit.parse(path.read_text(encoding="utf-8") if path.exists() else "")
    if section not in doc:
        doc[section] = tomlkit.table()
    doc[section][key] = value.value if isinstance(value, Enum) else value
    path.write_text(tomlkit.dumps(doc), encoding="utf-8", newline="\n")


def _unknown_note(field_name: str | None) -> str:
    return "[dim]—[/]" if field_name else "[yellow]not a proxdex setting[/]"


def _config_rows(lib: Library) -> list[tuple[str, str, Any]]:
    """Every ``[section] key`` in this library's TOML, in file order."""
    path = lib.root / MARKER
    if not path.exists():
        return []
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
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
    min_dpi: float | None,
    scale: str | None,
    double: bool | None,
    face: int | None,
    force: bool,
) -> None:
    """Upscale → stage 3 (upscaled), after any border fix.

    Runs on the bordered image if present, else the original — so frame
    expansion happens first. Defaults live under [cyan]\\[tools][/].

    This is the one step that needs a tool proxdex does not ship, and **cannot**
    ship: Upscayl is a desktop application with a native Vulkan engine, not a
    Python package. Install it (its bundled [cyan]upscayl-bin[/] is found
    automatically on macOS) or set [cyan]\\[tools] upscayl_bin[/]; see
    [cyan]proxdex where[/] for what this machine has. Without it, skip the step —
    [cyan]proxdex skip upscale[/] — and the earlier stage stands as the master.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    # asked once, up front: a missing upscaler is a fact about the machine, and
    # finding out per card halfway through a batch is the version of this that
    # wastes your afternoon
    found = upscale_mod.availability(cfg)
    if not found.ready:
        raise click.UsageError(
            f"{found.message}\nOr skip the step: proxdex skip upscale"
            + (f" {' '.join(ids)}" if ids else "")
        )
    # the registry coerces each flag into its enum, or falls back to this
    # library's config — so only well-typed values reach upscayl-bin
    opts = steps.resolve(
        "upscale",
        cfg,
        model=model,
        min_dpi=min_dpi,
        scale=scale,
        double=double,
    )
    use_model = cast("UpscaylModel", opts["model"])
    use_double = bool(opts["double"])
    # `scale` is optional here: unset means "whatever reaches the target resolution",
    # which is a different factor per card and so cannot be settled up front. An
    # explicit --scale is honoured as that factor.
    asked = cast("UpscaylScale | None", opts["scale"])
    target = int(opts["min_dpi"] or 0)
    run_cfg = replace(cfg, upscayl_min_dpi=target)

    def one(card: Card) -> None:
        for f in _faces(card, face):
            src = card.best(Stage.BORDERED, Stage.ORIGINAL, face=f)
            if src is None:
                raise FileError(f"{_label(card, f)}: no original yet (fetch it first)")
            dst = card.stage_path(Stage.UPSCALED, f)
            if dst.exists() and not force:
                console.print(f"[dim]· {_label(card, f)}: already upscaled[/]")
                continue
            with Image.open(src) as im:
                src_w = im.width
            plan = upscale_mod.plan(
                src_w,
                sheet_mod.trim_mm(card, cfg)[0],
                run_cfg,
                scale=asked,
                double=use_double,
            )
            # A master that will print visibly soft is a fact about the *source* that
            # no setting fixes, so it is said rather than discovered on paper later —
            # but **once per run**, not per card, because a line each would be 500
            # warnings on a 500-card run.
            if plan.short:
                fell_short.append((_label(card, f), src_w, plan.dpi))
            upscale_mod.run(
                src, dst, cfg, model=use_model, scale=plan.scale, double=plan.double
            )
            _flatten_filed(dst)
            card.clear_skip(Stage.UPSCALED, f)
            _cascade(card, Stage.UPSCALED, f)
            console.print(
                f"[green]✓[/] {_label(card, f)}: upscaled "
                f"[dim]({use_model.value} {plan.label})[/] → "
                f"{dst.relative_to(lib.root)}"
            )

    fell_short: list[tuple[str, int, int]] = []
    _each(lib.select(ids), one, "upscaling")
    if fell_short:
        worst = min(dpi for _, _, dpi in fell_short)
        err.print(
            f"[yellow]⚠[/] {len(fell_short)} side(s) will print soft — as low as "
            f"{worst}dpi across the card. [dim]That is the source's own resolution, "
            "which no factor adds to: find a better scan if it matters.[/]"
        )
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
    brightness: float | None,
    contrast: float | None,
    saturation: float | None,
    gamma: float | None,
    levels: float | None,
    face: int | None,
    force: bool,
) -> None:
    """Apply the look → stage 4 (edited), the trim-size master.

    One identical recipe — brightness, contrast, saturation, gamma — over every
    card, so a batch prints as a set. [cyan]--levels[/] additionally stretches a
    single card's own black and white points, which helps a flat scan; it reads
    that card only.

    Grade does *not* try to match cards to each other by colour: a card frame is
    yellow on a Pokémon card, black on a Magic one and absent on a full-art
    print, so there is no shared baseline to pull them to. Matching the **paper**
    is a print-time job — see [cyan]proxdex profile[/] — and it happens at
    [cyan]sheet[/] time, outside this master. Defaults live in [cyan]\\[grade][/].
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    look = steps.resolve(
        "grade",
        cfg,
        brightness=brightness,
        contrast=contrast,
        saturation=saturation,
        gamma=gamma,
        levels=levels,
    )

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
                Image.open(src),
                cfg,
                brightness=look["brightness"],
                contrast=look["contrast"],
                saturation=look["saturation"],
                gamma_value=look["gamma"],
                levels=look["levels"],
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


# ------------------------------------------------------------- frame specs ---
def _registry(lib: Library) -> specs.Registry:
    return specs.load(lib.root)


def _spec_name(lib: Library, spec_id: str) -> str:
    """A spec's own name, or its id if this library has never heard of it.

    Total on purpose: the id may come from a card marker written by a proxdex that
    knew a spec this one does not, and a missing name is no reason to fail.
    """
    spec = _registry(lib).get(spec_id)
    return spec.name if spec is not None else spec_id


def _spec(reg: specs.Registry, value: str | None) -> str | None:
    """A spec id from a flag, checked against *this library's* specs.

    Not a ``click.Choice``: the list lives in a library that is not open when the
    decorator runs. The error names every option, which is what a Choice does.
    """
    if not value:
        return None
    found = reg.get(value)
    if found is None:
        known = ", ".join(sorted(reg.specs)) or "none"
        raise click.UsageError(
            f"'{value}' is not a frame spec in this library. Known: {known} "
            "(`proxdex frames list`)"
        )
    return found.id


def _resolve_spec(
    reg: specs.Registry, card: Card, override: str | None = None
) -> specs.Resolution:
    """The spec this card's border step will fit to, and why — one call, so the
    CLI, the API and the align tool cannot disagree about which spec is in force."""
    return specs.resolve(
        reg,
        card.id,
        card.set_id,
        card.game,
        override=override,
        pin=card.pin,
        printing=card.printing_frame,
        traits=card.traits,
    )


def _warn_spec(card: Card, found: specs.Resolution) -> None:
    """Say out loud when the fit is running against a broken or unanswerable
    choice of spec. Not against a spec whose *numbers* are provisional — that is
    every shipped MTG spec, so warning about it would be a line on every card."""
    if found.missing:
        landed = found.spec.id if found.spec else "no spec at all"
        err.print(
            f"[yellow]⚠[/] {card.id}: frame spec [bold]{found.missing}[/] no longer "
            f"exists — fitting to '{landed}' instead "
            "[dim](`proxdex frames list`, then pin or assign one)[/]"
        )
    if found.undecided:
        err.print(
            f"[yellow]⚠[/] {card.id}: rule(s) "
            f"[bold]{', '.join(found.undecided)}[/] match on what the provider "
            "said about this printing, which this card has no record of "
            "[dim](re-fetch it, or pin a spec with `proxdex frames pin`)[/]"
        )
    elif found.spec is None:
        err.print(
            f"[yellow]⚠[/] {card.id}: no frame spec has been measured for this "
            "printing [dim](`proxdex frames set`, or `--frame` for this run)[/]"
        )


def _spec_row(spec: FrameGuide) -> tuple[str, ...]:
    top, right, bottom, left = spec.mm()
    return (
        spec.id,
        spec.name,
        games.get(spec.game).name if spec.game else "any",
        f"{top:.2f} / {right:.2f} / {bottom:.2f} / {left:.2f}",
        # two of the shipped specs describe an 89×127 card, so a millimetre column
        # with no card beside it would be read against the wrong one
        "oversized" if spec.oversized else "",
    )


def _spec_table(reg: specs.Registry) -> Table:
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("Spec", "Name", "Game", "Border T/R/B/L (mm)", "Card"):
        table.add_column(col)
    for spec in reg.specs.values():
        table.add_row(*_spec_row(spec))
    return table


def _issue_table(issues: Sequence[specs.Issue]) -> Table:
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("What", "Where", "Detail"):
        table.add_column(col)
    for issue in issues:
        table.add_row(
            f"[yellow]{issue.fault.label}[/]",
            issue.subject,
            f"[dim]{escape(issue.detail)}[/]",
        )
    return table


def _audit(lib: Library, reg: specs.Registry) -> list[specs.Issue]:
    """This library's frame warnings — the same list the UI's panel shows."""
    return specs.audit(
        reg, [(card.id, _resolve_spec(reg, card)) for card in lib.cards()]
    )


def _rules_table(reg: specs.Registry) -> Table:
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("Rule", "Game", "Set", "Catches", "Spec", "Needs traits"):
        table.add_column(col)
    for rule in reg.rules:
        table.add_row(
            rule.id,
            games.get(rule.game).name,
            rule.set_id or "[dim]every set[/]",
            rule.describes,
            rule.spec,
            "[yellow]yes[/]" if rule.match.needs_traits else "[dim]no[/]",
        )
    return table


@cli.group(name="frames", invoke_without_command=True)
@click.pass_context
def frames_cmd(ctx: click.Context) -> None:
    """Frame specs: the border widths a card is reshaped to, and which set gets which.

    Run bare, this lists the specs and what your own cards resolve to.
    [cyan]set[/] records a spec's four numbers, [cyan]assign[/] points part of a
    set (or all of it) at one, [cyan]pin[/] overrules the rules for one card, and
    [cyan]check[/] lists everything about this library's frames that needs a
    decision.

    A spec is four numbers — there is no confidence grade, because reading a border
    off the publisher's scan is not a measurement of the card and grading it as one
    was worse than saying nothing.
    The shipped MTG numbers are working defaults; measure a real card and
    [cyan]set[/] them.
    """
    if ctx.invoked_subcommand is not None:
        return
    lib = _lib(ctx)
    reg = _registry(lib)
    console.print(_spec_table(reg))
    if reg.rules:
        console.print("\n[bold]Rules[/] [dim](first match wins)[/]")
        console.print(_rules_table(reg))

    cards = lib.cards()
    if not cards:
        console.print(
            "\n[dim]no cards yet — nothing resolves until there is something to "
            "resolve[/]"
        )
        return
    # keyed by the answer, not by the set: a set with a rule for its secret-rare
    # tail resolves two ways, and one row claiming the default for all of them
    # would name a fit that half those cards never get
    seen: dict[tuple[GameId, str, str, str], tuple[specs.Resolution, int]] = {}
    for card in cards:
        found = _resolve_spec(reg, card)
        answer = found.spec.id if found.spec else "—"
        key = (card.game, card.set_id, answer, found.rule or found.via.value)
        _, count = seen.get(key, (found, 0))
        seen[key] = (found, count + 1)
    mine = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("Set", "Game", "Cards", "Resolves to", "From"):
        mine.add_column(col)
    for (game, set_id, _, _), (found, count) in sorted(seen.items()):
        mine.add_row(
            set_id,
            games.get(game).name,
            str(count),
            found.spec.id if found.spec else "[yellow]none measured[/]",
            found.via.label + (f" ({found.rule})" if found.rule else ""),
        )
    console.print("\n[bold]Sets in this library[/]")
    console.print(mine)
    if issues := _audit(lib, reg):
        console.print(
            f"\n[yellow]{len(issues)}[/] thing(s) need a decision — "
            "[cyan]proxdex frames check[/]"
        )


@frames_cmd.command("list")
@click.pass_context
def frames_list(ctx: click.Context) -> None:
    """Every frame spec this library can fit to."""
    console.print(_spec_table(_registry(_lib(ctx))))


@frames_cmd.command("show")
@click.argument("spec_id", metavar="SPEC")
@click.pass_context
def frames_show(ctx: click.Context, spec_id: str) -> None:
    """One spec's numbers, provenance and where it is used."""
    lib = _lib(ctx)
    reg = _registry(lib)
    spec = reg.get(spec_id)
    if spec is None:
        raise click.UsageError(
            f"no frame spec '{spec_id}' — `proxdex frames list` shows them"
        )
    top, right, bottom, left = spec.mm()
    stored = specs.path_for(lib.root, spec.id)
    where = (
        f"stored here ([dim]{stored.relative_to(lib.root)}[/])"
        if stored.is_file()
        else "[dim]shipped with proxdex[/]"
        if frames.is_shipped(spec.id)
        else "[dim]not stored[/]"
    )
    console.print(
        f"[bold]{spec.name}[/]  [dim]{spec.id}[/]\n"
        f"{games.get(spec.game).name if spec.game else 'any game'} · {where}\n"
        f"[dim]border:[/] top {top:.2f} · right {right:.2f} · bottom {bottom:.2f} "
        f"· left {left:.2f} mm  [dim](of a {spec.card_mm[0]:g}×"
        f"{spec.card_mm[1]:g}mm card"
        f"{' — oversized' if spec.oversized else ''})[/]\n"
        f"[dim]inset:[/] " + " ".join(f"{v * 100:.3f}%" for v in spec.inset)
    )
    used = reg.uses(spec.id)
    if used:
        console.print("\n[bold]Used by[/]")
        console.print(_rules_table(specs.Registry(specs=reg.specs, rules=tuple(used))))
    pinned = [c.id for c in lib.cards() if c.pin == spec.id]
    if pinned:
        console.print(f"[dim]pinned to:[/] {', '.join(pinned)}")


_EDGES = (
    click.option("--top", type=float, required=True, help="Top border (mm)."),
    click.option("--right", type=float, required=True, help="Right border (mm)."),
    click.option("--bottom", type=float, required=True, help="Bottom border (mm)."),
    click.option("--left", type=float, required=True, help="Left border (mm)."),
    click.option("--name", default="", help="What to call it in listings."),
    click.option(
        "--game",
        type=click.Choice([g.value for g in GameId]),
        default=None,
        help="The game whose frame this is. Omit for a spec that suits any game.",
    ),
    click.option(
        "--oversized",
        is_flag=True,
        help=f"These millimetres are of an oversized card ({games.OVERSIZED_W_MM:g}×"
        f"{games.OVERSIZED_H_MM:g}mm) — a plane, scheme or Vanguard — rather than the "
        "standard one. It changes the fractions stored, since the same border is a "
        "smaller fraction of a bigger card.",
    ),
)


def _edges(fn: Any) -> Any:
    for option in reversed(_EDGES):
        fn = option(fn)
    return fn


def _store(lib: Library, spec: FrameGuide) -> None:
    existed = specs.path_for(lib.root, spec.id).is_file()
    path = specs.save(lib.root, spec)
    top, right, bottom, left = spec.mm()
    verb = (
        "updated"
        if existed
        else "corrected the shipped"
        if frames.is_shipped(spec.id)
        else "added"
    )
    console.print(
        f"[green]✓[/] {verb} [bold]{spec.id}[/]: "
        f"{top:.2f} / {right:.2f} / {bottom:.2f} / {left:.2f} mm "
        f"→ {path.relative_to(lib.root)}"
    )
    if not frames.is_shipped(spec.id) and not _registry(lib).uses(spec.id):
        console.print(
            "[dim]nothing uses it yet — `proxdex frames assign "
            f"{spec.id} --set <set> --match set`[/]"
        )


@frames_cmd.command("set")
@click.argument("spec_id", metavar="SPEC")
@_edges
@click.pass_context
def frames_set(
    ctx: click.Context,
    spec_id: str,
    top: float,
    right: float,
    bottom: float,
    left: float,
    name: str,
    game: str | None,
    oversized: bool,
) -> None:
    """Record a spec's four border widths — a new one, or a correction.

    Millimetres of a real card, which is 63.5×88.9mm for both games. One verb,
    because a spec is four numbers however you arrived at them. There used to be
    three (measure / scan / estimate) and the middle one was a mistake: it graded a
    border read off the publisher's scan as trustworthy, when a scan's crop shifts
    every reading taken from it by the same unknown amount.

    Correcting a shipped spec is the expected case — the MTG numbers that ship are
    working defaults. [cyan]docs/measuring-frames.md[/] names the card to measure
    for each, how to measure it, and is where to write down what you did.

    [dim]  proxdex frames set mtg-m15 --game mtg \\
          --top 2.4 --right 2.4 --bottom 2.4 --left 2.4[/]
    """
    _store(
        _lib(ctx),
        specs.spec(
            spec_id,
            name,
            games.parse(game),
            (top, right, bottom, left),
            oversized=oversized,
        ),
    )


@frames_cmd.command("rm")
@click.argument("spec_id", metavar="SPEC")
@click.pass_context
def frames_rm(ctx: click.Context, spec_id: str) -> None:
    """Remove one of this library's own specs.

    Refused while a rule or a pinned card still names it: that card would quietly
    start bordering off the fallback, which is a different picture and no warning.
    """
    lib = _lib(ctx)
    specs.delete(
        lib.root, spec_id, pinned=[c.id for c in lib.cards() if c.pin == spec_id]
    )
    console.print(f"[green]✓[/] removed [bold]{spec_id}[/]")


@frames_cmd.command("rules")
@click.pass_context
def frames_rules(ctx: click.Context) -> None:
    """Which cards get which spec, in the order they are tried."""
    reg = _registry(_lib(ctx))
    if not reg.rules:
        console.print(
            "[dim]no rules — every set falls back to its era or its game's "
            "generic spec. `proxdex frames assign` adds one.[/]"
        )
        return
    console.print(_rules_table(reg))


@frames_cmd.command("assign")
@click.argument("spec_id", metavar="SPEC")
@click.option(
    "--set",
    "set_id",
    default="",
    help="The set code this rule is for. Omit it for a rule covering every set of "
    "the game — the only way to express a frame treatment, which is not a property "
    "of any one set. Game-wide rules lose to set-specific ones.",
)
@click.option(
    "--match",
    "match",
    type=click.Choice([m.value for m in specs.Match]),
    default=specs.Match.SET.value,
    help="Which cards of the set it catches. `set` is the set's default spec.",
)
@click.option(
    "--value",
    default="",
    help="What the match needs: 188-216 for numbers, a rarity, a subtype, a "
    "frame generation. Not used by `set` or `full-art`.",
)
@_GAME
@click.pass_context
def frames_assign(
    ctx: click.Context,
    spec_id: str,
    set_id: str,
    match: str,
    value: str,
    game: str | None,
) -> None:
    """Point part of a set (or all of it) at a frame spec.

    A set can need more than one: the ordinary cards take the set's default and
    the exceptions are caught by their own rule, which is tried first. Nobody
    picks a spec per card — the rules do it, and [cyan]frames preview[/] shows
    exactly which card each one catches before you trust it.

    A rule with no [cyan]--set[/] covers every set of its game. That is what a
    *treatment* needs: `extendedart` runs the art to the card edges in every set
    that ever printed one, and listing those sets would go stale every release.
    Specificity decides, not file order — a set's own rule beats a game-wide one.

    [dim]  proxdex frames assign pokemon-swsh --set swsh4 --match set
      proxdex frames assign pokemon-secret --set swsh4 --match numbers
        --value 188-216
      proxdex frames assign mtg-extended --game mtg --match effect
        --value extendedart[/]
    """
    lib = _lib(ctx)
    chosen = specs.parse_match(match)
    if chosen is None:  # click.Choice already refused anything else
        raise click.UsageError(f"unknown match kind '{match}'")
    rule = specs.assign(
        lib.root,
        spec_id,
        games.coerce(game, lib.default_game),
        set_id,
        chosen,
        value,
    )
    console.print(
        f"[green]✓[/] [bold]{rule.id}[/]: {rule.scope} · {rule.describes} → {rule.spec}"
    )
    if chosen is specs.Match.EFFECT:
        # not the warning below: a printing with no treatments is an *answer*, and
        # the commonest one, so this rule decides for every card that has traits
        console.print(
            "[dim]matches a frame treatment. A printing with no treatments is a "
            "clean no, so this decides for every card whose traits were recorded — "
            "and only two of the ~26 treatments change the border at all "
            "(`extendedart` and the yellow band, both already handled).[/]"
        )
    elif chosen.needs_traits:
        console.print(
            "[dim]matches on what the provider said about the printing — cards "
            "fetched before proxdex recorded that will report that they cannot be "
            "decided. `proxdex frames preview` shows the whole set.[/]"
        )


@frames_cmd.command("unassign")
@click.argument("rule_id", metavar="RULE")
@click.pass_context
def frames_unassign(ctx: click.Context, rule_id: str) -> None:
    """Remove one rule. Numbering never reuses, so ids keep meaning what they did."""
    rule = specs.unassign(_lib(ctx).root, rule_id)
    console.print(
        f"[green]✓[/] removed [bold]{rule.id}[/] ({rule.scope} · {rule.describes})"
    )


@frames_cmd.command("pin")
@click.argument("spec_id", metavar="SPEC")
@click.argument("ids", nargs=-1, required=True, metavar="ID...")
@click.pass_context
def frames_pin(ctx: click.Context, spec_id: str, ids: tuple[str, ...]) -> None:
    """Pin these cards to a spec, whatever the rules say.

    The last word on one card, and stored — a re-fetch will not touch it. Use it
    for the printing the rules got wrong, not as a substitute for a rule.
    """
    lib = _lib(ctx)
    chosen = _spec(_registry(lib), spec_id)
    for card in lib.select(ids):
        card.set_pin(chosen)
        console.print(f"[green]✓[/] {card.id}: pinned to [bold]{chosen}[/]")


@frames_cmd.command("unpin")
@click.argument("ids", nargs=-1, required=True, metavar="ID...")
@click.pass_context
def frames_unpin(ctx: click.Context, ids: tuple[str, ...]) -> None:
    """Drop these cards' pins — back to whatever the rules resolve."""
    lib = _lib(ctx)
    reg = _registry(lib)
    for card in lib.select(ids):
        card.set_pin(None)
        found = _resolve_spec(reg, card)
        console.print(
            f"[green]✓[/] {card.id}: unpinned → "
            f"{found.spec.id if found.spec else '[yellow]no spec[/]'} "
            f"[dim]({found.via.label})[/]"
        )


@frames_cmd.command("check")
@click.option("--json", "as_json", is_flag=True, help="Emit the warnings as JSON.")
@click.pass_context
def frames_check(ctx: click.Context, as_json: bool) -> None:
    """Everything about this library's frames that needs a decision.

    Four things, and all four are a broken reference or a question nobody can
    answer from what is recorded: a spec file that will not parse, something
    pointing at a spec that does not exist, a trait rule on a card whose traits
    were never recorded, and a card whose printing nothing knows the frame of.

    Deliberately **not** a coverage report. There used to be one, grading every
    set that has ever printed, and it could not work: MTG's border follows the
    printing's frame generation, so a set-level row has no printing to read and it
    called 1046 sets unmeasured while every card in them resolves exactly.
    """
    lib = _lib(ctx)
    reg = _registry(lib)
    issues = _audit(lib, reg)
    if as_json:
        console.print_json(data={"issues": [i.json() for i in issues]})
        return
    if not issues:
        console.print(
            "[green]✓[/] nothing to decide — every spec reads, every rule points "
            "somewhere, and every card resolves to a spec that knows its printing."
        )
        return
    console.print(_issue_table(issues))
    console.print()
    for fault in specs.Fault:
        if n := sum(1 for i in issues if i.fault is fault):
            console.print(f"[yellow]{n}[/] {fault.label}")
            console.print(f"  [dim]{_FAULT_HINT[fault]}[/]")


#: one hint per fault, printed under the count rather than on every row
_FAULT_HINT: dict[specs.Fault, str] = {
    fault: specs.Issue(fault=fault, subject="").hint for fault in specs.Fault
}


@frames_cmd.command("preview")
@click.argument("set_id", metavar="SET")
@_GAME
@click.option(
    "--spec", "only", default="", help="Only the cards that resolve to this spec."
)
@click.option("--limit", type=int, default=30, help="How many cards to print.")
@click.pass_context
def frames_preview(
    ctx: click.Context, set_id: str, game: str | None, only: str, limit: int
) -> None:
    """Which spec every card of one set gets, and which rule decided it.

    This is what makes a rule trustworthy rather than hopeful — especially one
    matching on rarity or frame generation, where the answer depends on data you
    cannot see. Reads that set's cards from the provider (cached), so what it
    shows is what a fetched card will get.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    found = inventory.preview(
        set_id, cfg, _registry(lib), games.coerce(game, lib.default_game)
    )
    rows = [r for r in found.rows if not only or r.spec == only]
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("Card", "No.", "Name", "Rarity", "Spec", "From"):
        table.add_column(col)
    for row in rows[: max(limit, 0)]:
        table.add_row(
            row.card.id,
            row.card.number,
            row.card.name,
            row.card.rarity or "—",
            row.spec,
            row.via.label + (f" ({row.rule})" if row.rule else ""),
        )
    console.print(table)
    tally = " · ".join(f"{spec} [bold]{n}[/]" for spec, n in found.tally().items())
    console.print(f"[dim]{len(found.rows)} card(s):[/] {tally}")
    undecided = [r.card.id for r in found.rows if r.undecided]
    if undecided:
        err.print(
            f"[yellow]⚠[/] {len(undecided)} card(s) could not be decided by a "
            "trait rule — the provider did not say. [dim]They take the set's "
            "default; pin the ones that need something else.[/]"
        )
    if len(rows) > limit > 0:
        console.print(f"[dim]… {len(rows) - limit} more — raise --limit[/]")


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
@steps.click_options("border")
@click.option(
    "--save",
    "save_frame",
    is_flag=True,
    help="Keep [cyan]--frame[/] as this card's pin, so every later run uses it too.",
)
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
    stretch: bool | None,
    frame: str | None,
    save_frame: bool,
    face: int | None,
    force: bool,
    dry_run: bool,
) -> None:
    """Reshape a card → stage 2 (bordered), before upscaling.

    Two ways to say where the border is, and **nothing measures it for you** —
    where a printed border sits is a reading, and a wrong one is invisible until
    the card is cut:

    • [cyan]--inner-top/-right/-bottom/-left[/] <fraction 0-1>: where the card's
    inner border edge currently sits, which is what the web UI's align marks
    place. From the frame spec this library's rules resolve for the card (see
    [cyan]proxdex frames[/]) [cyan]cardbleed[/] reshapes to the exact card aspect
    with the correct border widths (add [cyan]--stretch[/] to hit the borders
    exactly by un-distorting the art).

    • [cyan]--top/--bottom/--left/--right[/] <mm>: just add that much border to
    each edge — no fit, no distortion.

    A printing whose spec is [b]borderless[/] needs neither: there is no frame to
    align to, so the fit is pure aspect correction and it runs on its own.

    Which spec that is, and *why*, is printed with every fit — a rule, the set's
    default, its era, or a pin. [cyan]--frame[/] overrides it for this run;
    [cyan]--frame … --save[/] pins it to the card for good.

    [cyan]--dry-run[/] reports the plan without writing.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    inner = (inner_top, inner_right, inner_bottom, inner_left)
    use_inner = any(v is not None for v in inner)
    if use_inner and not all(v is not None for v in inner):
        raise click.UsageError("give all four --inner-top/-right/-bottom/-left or none")
    grow_mm = {"top": top_mm, "right": right_mm, "bottom": bottom_mm, "left": left_mm}
    reg = _registry(lib)
    override = _spec(reg, frame)
    if save_frame and override is None:
        raise click.UsageError("--save needs a --frame to save")
    do_stretch = bool(steps.resolve("border", cfg, stretch=stretch)["stretch"])

    def one_face(card: Card, f: int) -> None:
        dst = card.stage_path(Stage.BORDERED, f)
        name = _label(card, f)
        if dst.exists() and not force and not dry_run:
            console.print(f"[dim]· {name}: already bordered[/]")
            return
        src = card.stage_path(Stage.ORIGINAL, f)
        if not src.exists():
            raise FileError(f"{name}: no original yet (fetch it first)")
        with Image.open(src) as im:
            w, h = im.width, im.height
        marks = cast("tuple[float, float, float, float]", inner) if use_inner else None
        # one call decides the spec, and it reports which of the seven ways it got
        # there: an override, this card's pin, its printing, a rule, the set's
        # default, its era, or nothing at all
        chosen = _resolve_spec(reg, card, override)
        if chosen.spec is None:
            # nothing measured describes this printing. Refusing is deliberate: the
            # alternative is reshaping the card to somebody else's numbers, which
            # looks perfect and is wrong once it is cut.
            raise FileError(
                f"{name}: no frame spec has been measured for this printing "
                f"({card.set_id}, {games.get(card.game).name}). Measure a card and "
                "record it with `proxdex frames set`, assign it, or pass --frame to "
                "fit against a spec for this run."
            )
        if marks is None and chosen.spec.frameless:
            # A borderless print has no frame to align to, so there is nothing for
            # anyone to place: the marks are the image edges and the fit is pure
            # aspect correction. This is the one case that needs no reading, which
            # is why it is the one case that runs unasked.
            marks = (0.0, 0.0, 0.0, 0.0)
            console.print(
                f"  [dim]⌖ {name}: {chosen.spec.name} — no border to align, "
                "reshaping to the card aspect only[/]"
            )
        if marks is not None:
            guide = chosen.spec  # refused above if nothing describes this printing
            _warn_spec(card, chosen)
            inner_t = marks
            plan = bleed.fit_plan(w, h, guide, inner_t, cfg, stretch=do_stretch)
            tw, th = round(plan.trim_w), round(plan.trim_h)
            bd = plan.borders
            tag = f"{guide.name}{', stretch' if do_stretch else ''}"
            note = (
                f"fit → {tw}×{th}px  "
                f"T{bd['top'] * 100:.2f} R{bd['right'] * 100:.2f} "
                f"B{bd['bottom'] * 100:.2f} L{bd['left'] * 100:.2f}%  "
                f"[dim]({tag} · {chosen.via.label})[/]"
            )
            if plan.cropped:
                note += f" [yellow](cropped {', '.join(plan.cropped)})[/]"
            if dry_run:
                console.print(f"[cyan]{name}[/]: {note}")
                return
            bleed.fit(src, dst, guide, inner_t, cfg, stretch=do_stretch)
            # what it was fitted to, beside the file: `doctor` compares it against
            # what the rules say today, because a spec that has since been
            # corrected leaves a master that is wrong and looks fine
            card.write_fit(Stage.BORDERED, f, guide.id, guide.inset)
        else:
            if max(grow_mm.values()) <= 0:
                console.print(f"[dim]· {name}: nothing to expand[/]")
                return
            note = " ".join(f"+{e[0].upper()}{v:g}" for e, v in grow_mm.items()) + "mm"
            if dry_run:
                console.print(f"[cyan]{name}[/]: {note}")
                return
            bleed.grow(src, dst, cfg, **grow_mm)
        _flatten_filed(dst)
        card.clear_skip(Stage.BORDERED, f)
        _cascade(card, Stage.BORDERED, f)
        console.print(f"[green]✓[/] {name}: {note} → {dst.relative_to(lib.root)}")

    def one(card: Card) -> None:
        if save_frame and override is not None and not dry_run:
            card.set_pin(override)
            console.print(f"  [dim]⌗ {card.id}: pinned to {override}[/]")
        for f in _faces(card, face):
            one_face(card, f)

    _each(lib.select(ids), one, "bordering")
    if not dry_run:
        _reindex(lib)


@cli.command()
@click.argument("ids", nargs=-1, metavar="[ID...]")
@click.option(
    "--fix", is_flag=True, help="Repair, in place, every finding that can be repaired."
)
@click.option("-y", "--yes", is_flag=True, help="Repair without confirming.")
@click.pass_context
def doctor(ctx: click.Context, ids: tuple[str, ...], fix: bool, yes: bool) -> None:
    """Check stored images against what proxdex would write today.

    A library outlives the code that filled it, and every difference this looks
    for is one you cannot see on screen — a transparent die-cut corner, a
    grayscale file, a bordered master that is not the trim aspect, a master fitted
    to border widths that have since been corrected. They show up on paper.

    Reads headers only and writes nothing until you pass [cyan]--fix[/], which
    repairs the two findings that *are* a repair (both are the same call every
    filing point already makes) and names the step to re-run for the rest.
    Downstream stages are left alone: the picture does not change, so nothing
    derived from it went stale.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    cards = lib.select(ids)
    found = doctor_mod.examine(cards, cfg, _registry(lib))
    scanned = (
        f"[dim]checked {found.images} image(s) across {found.cards} card(s)[/]"
        if found.images
        else "[dim]no stage images to check[/]"
    )
    if found.clean:
        console.print("[green]✓[/] every stored image is what proxdex writes today")
        console.print(scanned)
        return

    by_id = {c.id: c for c in cards}
    table = Table(box=None, pad_edge=False, header_style="dim")
    for col in ("card", "stage", "finding", "measured"):
        table.add_column(col)
    for f in found.findings:
        card = by_id.get(f.id)
        tone = "yellow" if f.repairable else "red"
        table.add_row(
            _label(card, f.face) if card and f.face is not None else f.id,
            f.stage.label if f.stage else "[dim]the card[/]",
            f"[{tone}]{f.check.label}[/]",
            f"[dim]{escape(f.detail)}[/]",
        )
    console.print(table)
    console.print(scanned)
    # one explanation per *kind* found, not per file: the sentence is the same for
    # every file with the same defect, and it is the reason to act on it. Under
    # `--fix` only the kinds that cannot be repaired are explained — the rest are
    # about to be dealt with, and the reasons would bury what actually happened.
    for ailment, n in found.counts().items():
        check = doctor_mod.CHECK[ailment]
        if fix and check.repairable:
            continue
        console.print(f"\n[bold]{check.label}[/] [dim]× {n}[/] — {check.why}")
        if check.hint:
            console.print(f"  [dim]↳ {escape(check.hint)}[/]")

    if not fix:
        if found.repairable:
            console.print(
                f"\n[cyan]→[/] {len(found.repairable)} of {len(found.findings)} can be "
                "repaired in place — run `proxdex doctor --fix`"
            )
        else:
            console.print("\n[dim]nothing here is a repair — see the hints above.[/]")
        return
    if not found.repairable:
        console.print("\n[dim]nothing to repair.[/]")
        return
    if not yes:
        if not sys.stdin.isatty():
            raise click.UsageError("not a terminal — pass --yes to repair these")
        if not click.confirm(
            f"\nRepair {len(found.repairable)} file(s)?", default=True
        ):
            console.print("[dim]nothing repaired.[/]")
            return

    def one(finding: doctor_mod.Finding) -> None:
        doctor_mod.repair(finding)
        console.print(
            f"[green]✓[/] {finding.path.name} [dim]({finding.check.label})[/]"
        )

    failed = _each(found.repairable, one, "repairing")
    console.print(
        f"[dim]{len(found.repairable) - failed} repaired"
        + (f", {failed} failed" if failed else "")
        + (f", {len(found.stuck)} left for a step re-run" if found.stuck else "")
        + "[/]"
    )


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

    doc = tomlkit.document()
    doc["name"] = str(data.get("name", ""))
    doc["date"] = str(data.get("date", ""))
    doc["faces"] = str(data.get("faces", Faces.FRONTS.value))
    doc["printed"] = bool(data.get("printed"))
    for key in ("printed_date", "paper", "printer", "notes"):
        doc[key] = str(data.get(key, ""))
    doc["pdf"] = str(data.get("pdf", "fronts.pdf"))
    # how it was printed, so a reprint is reproducible rather than remembered
    doc["profile"] = str(data.get("profile", ""))
    doc["back_profile"] = str(data.get("back_profile", ""))
    for key in ("page", "orientation"):
        doc[key] = str(data.get(key, ""))
    doc["dpi"] = _as_int(data.get("dpi"))
    doc["bleed_mm"] = _as_float(data.get("bleed_mm"))
    doc["cards"] = _as_strings(data.get("cards"))
    doc["copies"] = _as_ints(data.get("copies"))
    path.write_text(tomlkit.dumps(doc), encoding="utf-8", newline="\n")


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _as_float(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _as_strings(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else []


def _as_ints(value: object) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_as_int(v) for v in cast("Sequence[object]", value)]


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


#: the size a card prints at — declared in `sheet`, with the imposition
_trim_mm = sheet_mod.trim_mm


@dataclass(slots=True)
class _Repro:
    """The print-time reproduction: fit any master to trim, correct it for the
    medium, then extend cut bleed outside the trim with cardbleed.

    This is where the medium is matched — never in the stored master. A measured
    correction supersedes the profile's hand-set recipe, because one was printed
    and scanned and the other was a guess.
    """

    cfg: Config
    profile: profiles.Profile
    #: what corrects a card *back*. The same profile unless the backs land on a
    #: different medium, which is a thing that happens.
    back_profile: profiles.Profile
    tmpdir: Path

    def cell(
        self, master: Image.Image, trim: tuple[float, float], *, back: bool = False
    ) -> Image.Image:
        cfg = self.cfg
        ppm = cfg.sheet_dpi / 25.4
        trim_w, trim_h = round(trim[0] * ppm), round(trim[1] * ppm)
        im = sheet_mod.fit(master, trim_w, trim_h, cfg.sheet_fit)
        im = _apply_profile(im, self.back_profile if back else self.profile)
        if cfg.bleed_mm <= 0:
            return im
        bp = round(cfg.bleed_mm * ppm)
        src = scratch.file(".png", self.tmpdir)
        dst = scratch.file(".png", self.tmpdir)
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


def _parse_copies(ids: Sequence[str]) -> list[tuple[str, int]]:
    """``ex3-90:4`` → four copies of that card, in the order they were asked for.

    Copies are how proxies are actually printed — a playset is four of the same
    card — and a repeated id would otherwise be silently deduplicated by
    ``Library.select``.
    """
    out: list[tuple[str, int]] = []
    for raw in ids:
        cid, sep, count = raw.partition(":")
        if not sep:
            out.append((cid, 1))
            continue
        if not count.isdigit() or not 1 <= int(count) <= _MAX_COPIES:
            raise click.UsageError(
                f"{raw!r}: copies must be a number from 1 to {_MAX_COPIES}"
            )
        out.append((cid, int(count)))
    return out


#: enough for a playset of everything; a typo like `:400` is a mistake, not a plan
_MAX_COPIES = 99


def _plan_note(run: sheet_mod.Run, cfg: Config) -> None:
    """Say what the pages will be, per size — an unexpected page count is the one
    thing about a print run that costs real money."""
    for group in run.groups:
        console.print(
            f"  [cyan]▤[/] {group.cards} card(s) at {group.name(cfg)} → "
            f"{group.pages} page(s) [dim]({group.grid[0]}×{group.grid[1]} "
            "per page)[/]"
        )


def _overrides(
    cfg: Config,
    *,
    faces: str | None = None,
    page: str | None = None,
    orientation: str | None = None,
    dpi: int | None = None,
    cols: int | None = None,
    rows: int | None = None,
    bleed: float | None = None,
    guides: bool | None = None,
) -> None:
    """Apply this run's overrides to a loaded config, in place.

    A sheet run is a one-off — this paper, this printer, today — so the flags
    change the run and never the library's settings.
    """
    if faces:
        cfg.sheet_faces = Faces(faces)
    if page:
        cfg.sheet_page = PageSize(page)
    if orientation:
        cfg.sheet_orientation = Orientation(orientation)
    if dpi:
        cfg.sheet_dpi = dpi
    if cols:
        cfg.sheet_cols = cols
    if rows:
        cfg.sheet_rows = rows
    if bleed is not None:
        cfg.bleed_mm = bleed
    if guides is not None:
        cfg.sheet_guides = guides


_SHEET_OPTIONS = (
    click.option(
        "--faces",
        type=click.Choice([f.value for f in Faces]),
        default=None,
        help="What to impose (default from [sheet]).",
    ),
    click.option(
        "--page",
        type=click.Choice([p.value for p in PageSize]),
        default=None,
        help="Page size override.",
    ),
    click.option(
        "--orientation",
        type=click.Choice([o.value for o in Orientation]),
        default=None,
        help="Paper orientation override.",
    ),
    click.option(
        "--dpi",
        type=click.IntRange(72, 4800),
        default=None,
        help="Render resolution override.",
    ),
    click.option(
        "--cols", type=click.IntRange(1, 12), default=None, help="Cards across."
    ),
    click.option(
        "--rows", type=click.IntRange(1, 12), default=None, help="Cards down."
    ),
    click.option(
        "--bleed",
        type=click.FloatRange(0, 20),
        default=None,
        help="Cut bleed in mm, outside the trim.",
    ),
    click.option("--guides/--no-guides", default=None, help="Print the cut guides."),
    click.option(
        "--profile",
        default=None,
        help="Print profile for the card fronts (default from "
        "[cyan]\\[print] profile[/]).",
    ),
    click.option(
        "--back-profile",
        "back_profile",
        default=None,
        help="Print profile for the card backs, when they land on a different "
        "medium (default from [cyan]\\[print] back_profile[/], else the fronts').",
    ),
    click.option(
        "--copies",
        type=click.IntRange(1, _MAX_COPIES),
        default=1,
        show_default=True,
        help="Copies of every card. Per card, write [cyan]ID:N[/].",
    ),
)


def _sheet_options(fn: F) -> F:
    wrapped: Any = fn
    for option in reversed(_SHEET_OPTIONS):
        wrapped = option(wrapped)
    return cast("F", wrapped)


@cli.command()
@click.argument("name")
@click.argument("ids", nargs=-1, metavar="[ID[:COPIES]...]")
@_sheet_options
@click.option("--notes", default="", help="Recorded in the batch manifest.")
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Report the page plan and write nothing.",
)
@click.option(
    "--open/--no-open",
    "open_pdf",
    default=None,
    help="Open the PDF when done (default: the library's sheet.open).",
)
@click.pass_context
def sheet(
    ctx: click.Context,
    name: str,
    ids: tuple[str, ...],
    faces: str | None,
    page: str | None,
    orientation: str | None,
    dpi: int | None,
    cols: int | None,
    rows: int | None,
    bleed: float | None,
    guides: bool | None,
    profile: str | None,
    back_profile: str | None,
    copies: int,
    notes: str,
    dry_run: bool,
    open_pdf: bool | None,
) -> None:
    """Impose the trim masters into a print PDF and record the batch.

    Each card is scaled to the exact size it prints at, corrected for the medium
    by its [cyan]--profile[/], then given cut bleed *outside* the trim via
    cardbleed — the stored masters stay bleed-free. Cut guides sit at the card
    edge. Fronts, backs, or duplex (back pages mirrored + offset).

    Copies: [cyan]ID:4[/] prints a playset of that card, [cyan]--copies N[/]
    applies to every card in the run.

    Fronts and backs can be corrected for different media —
    [cyan]--profile[/] and [cyan]--back-profile[/] — for the runs where the two
    sides do not land on the same paper.

    Every page setting can be overridden for this run only —
    [cyan]--page/--orientation/--cols/--rows/--bleed/--dpi/--guides[/] — because
    a print run is this paper on this printer today, not a library preference.
    [cyan]--dry-run[/] reports the page plan and writes nothing.

    A two-sided card contributes the side [cyan]proxdex flip[/] points at, and in
    a duplex sheet its *other* side prints on the reverse instead of the shared
    card back. proxdex owns the PDF — print with colour management OFF for
    calibration to hold.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    _overrides(
        cfg,
        faces=faces,
        page=page,
        orientation=orientation,
        dpi=dpi,
        cols=cols,
        rows=rows,
        bleed=bleed,
        guides=guides,
    )
    wanted = _parse_copies(ids)
    if wanted:
        by_id = dict(wanted)
        selected = lib.select(tuple(by_id))
        chosen = [(c, by_id.get(c.id, 1) * copies) for c in selected]
    else:
        chosen = [(c, copies) for c in lib.cards()]
    run = sheet_mod.plan(chosen, cfg)
    if run.missing:
        err.print(
            f"[yellow]not ready, skipping:[/] {', '.join(run.missing)} "
            "[dim](finish grade, or `proxdex skip grade`)[/]"
        )
    if not run.ready:
        raise click.UsageError(
            "no card masters to impose — run upscale/grade, or skip them"
        )
    ready, counts = run.ready, run.copies
    _plan_note(run, cfg)
    # an oversized card prints at its own size, on its own pages — say which,
    # because it is a page count the user did not ask for
    if run.oversized:
        console.print(
            f"[cyan]⬗[/] {len(run.oversized)} oversized card(s) — "
            f"{', '.join(c.id for c in run.oversized)} — at "
            f"{games.OVERSIZED_W_MM:g}×{games.OVERSIZED_H_MM:g}mm, on their own pages"
        )
    two_sided = run.two_sided
    if two_sided and cfg.sheet_faces is not Faces.DUPLEX:
        err.print(
            f"[yellow]⚠[/] {len(two_sided)} two-sided card(s) — only the flipped-to "
            "side is in this sheet. [dim]`--faces duplex` prints the reverse too; "
            "`proxdex flip` swaps which side is the front.[/]"
        )

    prof = profiles.active(lib.root, cfg, profile)
    back_prof = profiles.active_back(lib.root, cfg, back_profile, prof)
    _profile_note(prof)
    if back_prof.name != prof.name:
        console.print(f"[cyan]◐[/] card backs print through [bold]{back_prof.name}[/]")
        _profile_note(back_prof)
    if dry_run:
        console.print(
            f"[dim]dry run — {run.cards} card(s) ({cfg.sheet_faces}) would make "
            f"{run.pages} page(s) @ {cfg.sheet_dpi}dpi on "
            f"'{prof.name}'. Nothing written.[/]"
        )
        return

    tmpdir = Path(tempfile.mkdtemp(prefix="proxdex-sheet-"))
    try:
        repro = _Repro(cfg, prof, back_prof, tmpdir)
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
                    cache[key] = repro.cell(
                        Image.open(path).convert("RGB"), trim, back=True
                    )
                backs[i] = cache[key]
        # one cell per copy: a playset is the same reproduction four times, and
        # reproducing it once is both faster and bit-identical on paper
        cells: list[sheet_mod.Cell] = []
        for card, trim, back, count in zip(ready, trims, backs, counts, strict=True):
            front = repro.cell(
                Image.open(cast("Path", _master(card, card.front_face))).convert("RGB"),
                trim,
            )
            cells += [
                sheet_mod.Cell(front=front, back=back, trim=trim) for _ in range(count)
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
            # what was actually imposed, so a reprint is reproducible: the copy
            # counts, the medium, and the page settings this run used
            "copies": counts,
            "profile": prof.name,
            "back_profile": back_prof.name,
            "page": cfg.sheet_page,
            "orientation": cfg.sheet_orientation,
            "dpi": cfg.sheet_dpi,
            "bleed_mm": cfg.bleed_mm,
            "notes": notes,
            "pdf": pdf.name,
        },
    )
    _reindex(lib)
    copy_note = f" from {len(ready)} card(s)" if run.cards != len(ready) else ""
    console.print(
        f"[green]✓[/] {run.cards} card(s){copy_note} ({cfg.sheet_faces}) → "
        f"{n_pages} page(s) @ {cfg.sheet_dpi}dpi → {pdf.relative_to(lib.root)}"
    )
    console.print(
        f"[dim]print with colour management OFF, then `proxdex printed {slug}`[/]"
    )
    # `--open/--no-open` overrides `[sheet] open` for this run, in both
    # directions. It has to work downwards too: the web UI shells out to this
    # very command, and a library configured to open its sheets would otherwise
    # launch a PDF viewer on whatever machine is running the server — which is
    # not necessarily, or even usually, the machine you are looking at.
    if cfg.sheet_open if open_pdf is None else open_pdf:
        _open_locally(pdf)


@cli.command()
@click.argument("name")
@click.pass_context
def printed(ctx: click.Context, name: str) -> None:
    """Mark a print batch as printed (updates its manifest)."""
    lib = _lib(ctx)
    slug = slugify(name)
    for tf in lib.batches_dir.glob("*/batch.toml"):
        data = tomllib.loads(tf.read_text(encoding="utf-8"))
        if data.get("name") == slug or tf.parent.name.endswith(f"_{slug}"):
            data["printed"] = True
            data["printed_date"] = date.today().isoformat()
            _write_batch(tf, data)
            _reindex(lib)
            console.print(f"[green]✓[/] '{slug}' printed {data['printed_date']}")
            return
    raise click.UsageError(f"no batch named '{name}'")


# --------------------------------------------------------- print profiles ----
_PROFILE_ARG = click.argument("name", required=False, metavar="[PROFILE]")


def _round_count(prof: profiles.Profile) -> str:
    """``4`` — or ``4 (+1 off)`` when a round is being held out of the fit."""
    if not prof.rounds:
        return "[dim]—[/]"
    off = len(prof.rounds) - len(prof.live)
    return f"{len(prof.live)}" + (f" [dim](+{off} off)[/]" if off else "")


def _profile(lib: Library, name: str | None) -> profiles.Profile:
    """The named profile, or the active one from [cyan]\\[print] profile[/]."""
    return profiles.active(lib.root, Config.load(lib.root), name)


def _stored(lib: Library, name: str | None) -> profiles.Profile:
    """A profile that is a real file — the identity is not one, and cannot be."""
    prof = _profile(lib, name)
    if prof.stored:
        return prof
    raise click.UsageError(
        f"'{profiles.NONE}' means no correction at all, so there is nothing to "
        "set or measure. Name the medium you are actually printing on: "
        "`proxdex profile new <name>`"
    )


def _profile_note(prof: profiles.Profile) -> None:
    """Say what the sheet is being corrected by, and how well it is known."""
    _unreadable_note(prof)
    residual = prof.residual
    if residual is not None:
        off = len(prof.rounds) - len(prof.live)
        muted = f", {off} switched off" if off else ""
        console.print(
            f"[cyan]◐[/] profile [bold]{prof.name}[/] — measured over "
            f"{len(prof.live)} round(s){muted}, last print off by mean "
            f"{residual.mean:.1f} RGB over {residual.measured} reachable patch(es)"
        )
    elif not prof.recipe.neutral:
        console.print(
            f"[cyan]◐[/] profile [bold]{prof.name}[/] — set by hand: "
            f"{prof.recipe.text()}. [dim]`proxdex calibrate chart` measures it "
            "instead, if you have a scanner.[/]"
        )
    elif prof.name != profiles.NONE:
        err.print(
            f"[yellow]⚠[/] profile '{prof.name}' corrects nothing yet — no "
            "measurement, and its numbers are all 1. [dim]`proxdex profile set "
            f"{prof.name} --saturation …`, or measure it.[/]"
        )


@cli.group("profile")
def profile_cmd() -> None:
    """Manage print profiles — one per medium you actually print on.

    A profile is a name, [bold]your notes[/], the recipe it started from, and the
    calibration rounds measured on it. `sheet --profile <name>` prints through
    one; [cyan]\\[print] profile[/] names the default.

    [dim]  proxdex profile new matte-200 --medium paper --notes "Canon matte, no CM"
      proxdex profile use matte-200
      proxdex calibrate chart          → print → scan → calibrate add --scan s.png[/]
    """


@profile_cmd.command("list")
@click.pass_context
def profile_list(ctx: click.Context) -> None:
    """Every profile in this library, plus the identity."""
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("", "Profile", "Corrects", "Rounds", "Last print off by", "Notes"):
        table.add_column(col)
    for prof in profiles.listing(lib.root):
        residual = prof.residual
        table.add_row(
            "→" if prof.name == cfg.print_profile else "",
            f"[bold]{prof.name}[/]" if prof.stored else f"[dim]{prof.name}[/]",
            prof.how if prof.stored else "[dim]nothing (identity)[/]",
            _round_count(prof),
            f"mean {residual.mean:.1f} / max {residual.max:.1f}"
            if residual
            else "[dim]not measured[/]",
            _one_line(prof.notes),
        )
    console.print(table)
    console.print(
        f"[dim]→ is the active profile (\\[print] profile). '{profiles.NONE}' is "
        "the identity — it corrects nothing, and is not a file.[/]"
    )


@profile_cmd.command("show")
@_PROFILE_ARG
@click.pass_context
def profile_show(ctx: click.Context, name: str | None) -> None:
    """A profile in full: notes, its numbers, and every calibration round."""
    lib = _lib(ctx)
    prof = _profile(lib, name)
    console.print(f"[bold]{prof.name}[/]  [dim]corrects: {prof.how}[/]")
    if not prof.stored:
        console.print(
            "[dim]the identity — it corrects nothing and has nothing to edit. "
            "`proxdex profile new <name>` makes a real one.[/]"
        )
    if prof.notes:
        console.print(f"\n{prof.notes}\n")
    console.print(
        f"[bold]By hand[/] {prof.recipe.text()}"
        + ("  [dim](superseded by the measurement below)[/]" if prof.live else "")
    )
    _unreadable_note(prof)
    if not prof.rounds:
        console.print(
            "\n[dim]no calibration rounds yet — `proxdex calibrate chart"
            f"{'' if name is None else ' ' + prof.name}` prints the first one[/]"
        )
        return
    table = Table(box=None, pad_edge=False, header_style="bold")
    cols = ("", "Round", "Slot", "Date", "Off by (mean/max)", "Pull", "Note")
    for col in cols:
        table.add_column(col)
    for rnd in prof.rounds:
        e = prof.score(rnd)
        pull = prof.influence(rnd.n)
        table.add_row(
            "✓" if rnd.enabled else "[dim]·[/]",
            str(rnd.n) if rnd.enabled else f"[dim]{rnd.n}[/]",
            rnd.slot.text,
            rnd.date,
            f"{e.mean:.1f} / {e.max:.1f}",
            f"{pull:.1f}" if pull is not None else "[dim]—[/]",
            _one_line(rnd.note),
        )
    console.print("\n[bold]Calibration[/]")
    console.print(table)
    live = prof.live
    if not live:
        console.print(
            "[yellow]every round is switched off[/] — nothing is correcting this "
            "medium. [dim]`proxdex calibrate enable --round N` puts one back.[/]"
        )
        return
    trend = " → ".join(f"{prof.score(r).mean:.1f}" for r in live)
    console.print(f"[dim]mean error by live round: {trend} (lower is truer)[/]")
    console.print(
        "[dim]✓ = in the fit, · = switched off. Pull is how far the correction "
        "moves if that round is left out — a round pulling much harder than its "
        "neighbours is either your most informative measurement or an outlier.[/]"
    )
    last = prof.score(live[-1])
    console.print(
        f"[dim]every round is scored over the same {last.measured} of {last.total} "
        "patches — one medium, one gamut.[/]"
    )
    if last.clipped:
        console.print(
            f"[dim]the other {last.clipped} are outside what this medium can print "
            "at all — paper is not 255, ink is not 0, and a saturated colour can "
            "need more of one ink than exists. That floor is the paper's, not the "
            "calibration's.[/]"
        )
    _converged(prof)
    free = len(prof.free_slots)
    console.print(
        f"[dim]next chart goes in slot {prof.next_slot.text} of "
        f"{prof.grid[0]}×{prof.grid[1]}; {free} slot(s) left on the sheet[/]"
        if free
        else "[dim]the sheet is full — the next chart starts a new one at slot 1,1[/]"
    )


@profile_cmd.command("new")
@click.argument("name")
@click.option("--notes", default="", help="What is special about this medium.")
@click.option("--use", is_flag=True, help="Also make it the active profile.")
@click.pass_context
def profile_new(ctx: click.Context, name: str, notes: str, use: bool) -> None:
    """Create a profile. It corrects nothing until you say what it does.

    Write down what you did — the paper, the printer setting, whether colour
    management was off. In six months the notes are the only way to reproduce it.

    Then define the correction, either way round:

    • [cyan]with a scanner[/] — `calibrate chart` → print → scan →
    `calibrate add`, repeating on one sheet until the error stops falling. This
    measures your printer instead of guessing at it.

    • [cyan]by hand[/] — `profile set --saturation … --gamma …`, judged off a
    test print. `profile strip` prints one page of the same card at a row of
    values so you can pick the one that looks right, and `profile preview` shows
    the numbers on screen first.
    """
    lib = _lib(ctx)
    prof = profiles.create(lib.root, name, notes=notes)
    console.print(
        f"[green]✓[/] profile [bold]{prof.name}[/] created at identity → "
        f"{profiles.path_for(lib.root, prof.name).name}"
    )
    if use:
        _write_setting(lib, "print", "profile", prof.name)
        console.print(f"[green]✓[/] \\[print] profile = {prof.name}")
    console.print(
        f"[dim]measure it: `proxdex calibrate chart {prof.name}` → print → scan → "
        f"`proxdex calibrate add {prof.name} --scan <file>`\n"
        f"or set it by hand: `proxdex profile strip {prof.name} --vary saturation` "
        f"→ print → `proxdex profile set {prof.name} --saturation <what looked "
        "right>`[/]"
    )


@profile_cmd.command("set")
@_PROFILE_ARG
@click.option("--notes", default=None, help="Replace the notes.")
@click.option("--note", "append", default=None, help="Add a line to the notes.")
@click.option("--saturation", type=float, default=None, help="Recipe saturation.")
@click.option("--contrast", type=float, default=None, help="Recipe contrast.")
@click.option("--brightness", type=float, default=None, help="Recipe brightness.")
@click.option("--gamma", type=float, default=None, help="Recipe gamma.")
@click.option(
    "--grid",
    default=None,
    metavar="COLSxROWS",
    help="How many charts one sheet holds (default 2x3).",
)
@click.pass_context
def profile_set(
    ctx: click.Context,
    name: str | None,
    notes: str | None,
    append: str | None,
    saturation: float | None,
    contrast: float | None,
    brightness: float | None,
    gamma: float | None,
    grid: str | None,
) -> None:
    """Set a profile's numbers by hand, its notes, or its sheet grid.

    This is the no-scanner path, and it is a real one: four multipliers applied at
    print time. Judge them off paper — [cyan]profile strip[/] prints one page of a
    card at a row of values for a single knob, which is how you pick a number
    without an instrument.

    A measured calibration supersedes these entirely, so on a profile with rounds
    they are only the record of where it started.
    """
    lib = _lib(ctx)
    prof = _stored(lib, name)
    if notes is not None:
        prof.notes = notes.strip()
    if append:
        prof.notes = f"{prof.notes}\n{append.strip()}".strip()
    prof.recipe = media.Recipe(
        saturation=_pick(saturation, prof.recipe.saturation),
        contrast=_pick(contrast, prof.recipe.contrast),
        brightness=_pick(brightness, prof.recipe.brightness),
        gamma=_pick(gamma, prof.recipe.gamma),
    )
    if grid:
        prof.grid = _parse_grid(grid)
    profiles.save(lib.root, prof)
    console.print(f"[green]✓[/] {prof.name} updated — {prof.recipe.text()}")
    if prof.live and any(
        v is not None for v in (saturation, contrast, brightness, gamma)
    ):
        console.print(
            f"[dim]note: {prof.name} has {len(prof.live)} measured round(s), and a "
            "measurement supersedes numbers set by hand — these are kept as the "
            "record, but the sheet uses the measurement.[/]"
        )


@profile_cmd.command("rename")
@click.argument("old")
@click.argument("new")
@click.pass_context
def profile_rename(ctx: click.Context, old: str, new: str) -> None:
    """Rename a profile, keeping its notes and its calibration."""
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    prof = profiles.rename(lib.root, old, new)
    console.print(f"[green]✓[/] {old} → [bold]{prof.name}[/]")
    if cfg.print_profile == profiles.slug(old):
        _write_setting(lib, "print", "profile", prof.name)
        console.print(f"[green]✓[/] \\[print] profile = {prof.name}")


@profile_cmd.command("rm")
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="Delete without confirming.")
@click.pass_context
def profile_rm(ctx: click.Context, name: str, yes: bool) -> None:
    """Delete a profile — its notes and every measured round with it."""
    lib = _lib(ctx)
    prof = _stored(lib, name)
    rounds = len(prof.rounds)
    if not yes:
        if not sys.stdin.isatty():
            raise click.UsageError("refusing to delete without --yes")
        detail = f" and {rounds} calibration round(s)" if rounds else ""
        click.confirm(f"Delete profile '{prof.name}'{detail}?", abort=True)
    profiles.delete(lib.root, prof.name)
    console.print(f"[green]✓[/] deleted profile {prof.name}")
    # never leave [print] pointing at a profile that is gone — the next sheet run
    # would fail on a name nobody typed
    if Config.load(lib.root).print_profile == prof.name:
        _write_setting(lib, "print", "profile", profiles.NONE)
        console.print(
            f"[dim]\\[print] profile was {prof.name}; reset to {profiles.NONE}[/]"
        )


@profile_cmd.command("use")
@click.argument("name")
@click.pass_context
def profile_use(ctx: click.Context, name: str) -> None:
    """Make a profile the default for `sheet` ([cyan]\\[print] profile[/])."""
    lib = _lib(ctx)
    prof = profiles.resolve(lib.root, name)
    _write_setting(lib, "print", "profile", prof.name)
    console.print(f"[green]✓[/] \\[print] profile = [bold]{prof.name}[/]")
    _profile_note(prof)


def _unreadable_note(prof: profiles.Profile) -> None:
    """Say when a profile's file holds rounds that cannot be read back.

    The usual cause is a chart of a different size — the patch arrays no longer
    match — and the honest thing is to name it, because the error trend is only
    as good as the rounds behind it.
    """
    if prof.unreadable:
        err.print(
            f"[yellow]⚠[/] {prof.unreadable} round(s) in {prof.name}.json could "
            "not be read and are not in the fit. [dim]A damaged entry, or one "
            "measured on a chart proxdex no longer knows. Re-measure, or keep the "
            "file for the record.[/]"
        )


# --------------------------------------------- setting a profile by hand ------
#: what a variation strip sweeps by default, when the user names no values
_STRIP_SPREAD = 0.3
_STRIP_STEPS = 5


def _sample_card(lib: Library, cid: str | None) -> tuple[Image.Image, str]:
    """A card to judge a correction on — the one named, else the first ready one.

    A real card, not a synthetic swatch: you are deciding whether *your cards*
    look right on this paper, and skin tones and a yellow border tell you things a
    grey ramp does not.
    """
    cards = lib.select((cid,)) if cid else lib.cards()
    for card in cards:
        master = _master(card, card.front_face)
        if master is not None:
            return Image.open(master).convert("RGB"), card.id
    raise click.UsageError(
        "no card image to preview on — fetch one, or pass --card <id>"
        if not cid
        else f"{cid} has no image yet"
    )


@profile_cmd.command("preview")
@_PROFILE_ARG
@click.option("--card", "cid", default=None, metavar="ID", help="Judge on this card.")
@click.option(
    "-o",
    "--out",
    "out",
    type=UserPath(path_type=Path),
    default=None,
    help="Where to write the PNG.",
)
@click.pass_context
def profile_preview(
    ctx: click.Context, name: str | None, cid: str | None, out: Path | None
) -> None:
    """Write a before/after PNG of what this profile does to a card.

    On screen, so it costs no paper — but a screen is not the medium, so use this
    to see the *direction* of a correction and a test print to judge its amount.
    """
    lib = _lib(ctx)
    prof = _profile(lib, name)
    im, used = _sample_card(lib, cid)
    after = _apply_profile(im, prof)
    gap = 16
    canvas = Image.new("RGB", (im.width * 2 + gap, im.height), (255, 255, 255))
    canvas.paste(im, (0, 0))
    canvas.paste(after, (im.width + gap, 0))
    dst = out or profiles.profiles_dir(lib.root) / f"{prof.name}_preview.png"
    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst)
    console.print(
        f"[green]wrote[/] {dst} [dim]— {used} before | after, through "
        f"'{prof.name}' ({prof.how})[/]"
    )
    if prof.how == "identity":
        console.print("[dim]both halves are the same: this profile corrects nothing[/]")


@profile_cmd.command("strip")
@_PROFILE_ARG
@click.option(
    "--vary",
    type=click.Choice(list(media.RECIPE_KEYS)),
    default="saturation",
    show_default=True,
    help="Which single number to sweep.",
)
@click.option(
    "--from",
    "low",
    type=click.FloatRange(media.RECIPE_LOW, media.RECIPE_HIGH),
    default=None,
    help="Lowest value (default: the current one, less a little).",
)
@click.option(
    "--to",
    "high",
    type=click.FloatRange(media.RECIPE_LOW, media.RECIPE_HIGH),
    default=None,
    help="Highest value.",
)
@click.option(
    "--steps",
    type=click.IntRange(2, 12),
    default=_STRIP_STEPS,
    show_default=True,
    help="How many variations.",
)
@click.option("--card", "cid", default=None, metavar="ID", help="Which card to print.")
@click.option(
    "-o",
    "--out",
    "out",
    type=UserPath(path_type=Path),
    default=None,
    help="Where to write the PDF.",
)
@click.pass_context
def profile_strip(
    ctx: click.Context,
    name: str | None,
    vary: str,
    low: float | None,
    high: float | None,
    steps: int,
    cid: str | None,
    out: Path | None,
) -> None:
    """Print one card at a row of values for one number, each labelled.

    This is how you set a profile without a scanner. Print it on the medium, look
    at it, read the label under the one that looks right, and
    [cyan]profile set --<number> <value>[/]. One number at a time: a page where
    two things changed tells you which page you like, not which value to keep.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    prof = _stored(lib, name)
    current = float(getattr(prof.recipe, vary))
    lo = current - _STRIP_SPREAD if low is None else low
    hi = current + _STRIP_SPREAD if high is None else high
    if hi <= lo:
        raise click.UsageError(f"--from {lo:g} is not below --to {hi:g}")
    values = [round(lo + (hi - lo) * i / (steps - 1), 3) for i in range(steps)]
    im, used = _sample_card(lib, cid)
    trim = sheet_mod.trim_mm(lib.find(used) or next(iter(lib.cards())), cfg)
    fits = sheet_mod.tiles_per_page(cfg, trim)
    tiles = [
        (label, media.compensate(im, recipe))
        for label, recipe in media.vary(prof.recipe, vary, values)
    ]
    page = sheet_mod.labelled_page(cfg, tiles, trim)
    dst = out or profiles.profiles_dir(lib.root) / f"{prof.name}_{vary}_strip.pdf"
    dst.parent.mkdir(parents=True, exist_ok=True)
    sheet_mod.write_page_pdf(page, dst, cfg)
    console.print(
        f"[green]wrote[/] {dst} [dim]— {used} at {vary} "
        f"{', '.join(f'{v:g}' for v in values)}[/]"
    )
    if len(tiles) > fits:
        err.print(
            f"[yellow]⚠[/] only {fits} of {len(tiles)} variations fit one page at "
            f"{trim[0]:g}×{trim[1]:g}mm — the rest were left off. "
            "[dim]Use fewer --steps.[/]"
        )
    console.print(
        f"[dim]print it on the medium with colour management OFF, then "
        f"`proxdex profile set {prof.name} --{vary} <the one that looked right>`[/]"
    )


def _apply_profile(im: Image.Image, prof: profiles.Profile) -> Image.Image:
    """What a sheet would do to this image through that profile."""
    correction = prof.correction
    if correction is not None:
        return correction.apply_to_image(im)
    return media.compensate(im, prof.recipe)


def _pick(value: float | None, current: float) -> float:
    return current if value is None else value


def _parse_grid(text: str) -> tuple[int, int]:
    parts = [p for p in re.split(r"[x,×]", text.lower()) if p.strip()]
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        raise click.UsageError(f"{text!r}: expected COLSxROWS, e.g. 2x3")
    cols, rows = (int(p) for p in parts)
    if not (1 <= cols <= 6 and 1 <= rows <= 6):
        raise click.UsageError("grid must be between 1x1 and 6x6")
    return cols, rows


def _one_line(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first if len(first) <= 48 else first[:47] + "…"


# ----------------------------------------------------------- calibration -----
@cli.group()
def calibrate() -> None:
    """Measure a print profile against paper, one round at a time.

    [dim]chart[/] renders a page with the chart in one slot of the sheet — print
    it on the medium, scan it with auto-correction OFF, and [dim]add[/] records
    what came back. The correction refits over [bold]every[/] round, so feeding
    the same sheet through again and printing the next slot makes it truer rather
    than replacing what you measured last time.

    [dim]  proxdex calibrate chart                → print it on the medium
      proxdex calibrate add --scan scan.png  → record what came back
      proxdex profile show                   → watch the error fall
      (feed the same sheet back in and repeat — six rounds fit an A4)[/]
    """


@calibrate.command("chart")
@_PROFILE_ARG
@click.option(
    "--slot",
    default=None,
    metavar="COL,ROW",
    help="Which slot of the sheet to print in (default: the next free one).",
)
@click.option(
    "--raw",
    is_flag=True,
    help="Print the plain target, uncorrected — what round 1 does anyway.",
)
@click.option(
    "--png",
    "as_png",
    is_flag=True,
    help="Write a PNG of the page instead of a print-ready PDF.",
)
@click.option(
    "-o",
    "--out",
    "out",
    type=UserPath(path_type=Path),
    default=None,
    help="Output path (default: [cyan]<lib>/profiles/<name>_round<n>.pdf[/]).",
)
@click.pass_context
def cal_chart(
    ctx: click.Context,
    name: str | None,
    slot: str | None,
    raw: bool,
    as_png: bool,
    out: Path | None,
) -> None:
    """Render the next round's chart, ready to print.

    The chart is corrected by everything measured so far, which is the point: the
    next print lands near the target, so the next measurement is taken where your
    cards actually live.
    """
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    prof = _stored(lib, name)
    where = profiles.Slot.parse(slot, prof.grid) if slot else prof.next_slot
    if where in prof.used_slots:
        err.print(
            f"[yellow]⚠[/] slot {where.text} already holds round "
            f"{next(r.n for r in prof.rounds if r.slot == where)} — printing there "
            "again lands ink on ink. Feed a blank sheet, or pick another slot."
        )
    if prof.sheet_full and slot is None:
        console.print(
            "[cyan]▤[/] the sheet is full — this chart starts a new one at "
            f"slot {where.text}"
        )
    correction = None if raw else prof.correction
    label = prof.chart_label(where)
    page = calibrate_mod.chart_page(
        cfg, correction, slot=where, grid=prof.grid, label=label
    )
    dst = out or profiles.profiles_dir(lib.root) / (
        f"{prof.name}_round{len(prof.rounds) + 1}.{'png' if as_png else 'pdf'}"
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    if as_png:
        page.save(dst)
    else:
        sheet_mod.write_page_pdf(page, dst, cfg)
    prof.pending = where
    profiles.save(lib.root, prof)
    console.print(
        f"[green]wrote[/] {dst} [dim]— round {len(prof.rounds) + 1}, "
        f"slot {where.text} of {prof.grid[0]}×{prof.grid[1]}"
        f"{', uncorrected' if correction is None else ', corrected so far'}[/]"
    )
    console.print(
        "[dim]print it on this medium with colour management OFF, scan the whole "
        f"page with auto-correction OFF, then `proxdex calibrate add {prof.name} "
        "--scan <file>`[/]"
    )


@calibrate.command("add")
@_PROFILE_ARG
@click.option(
    "--scan",
    "scan_path",
    required=True,
    type=UserPath(exists=True, dir_okay=False, path_type=Path),
    help="The scanned page.",
)
@click.option(
    "--slot",
    default=None,
    metavar="COL,ROW",
    help="Which slot this chart is in (default: the one the last chart used).",
)
@click.option(
    "--whole",
    is_flag=True,
    help="The image is one chart already, cropped — don't look for a slot.",
)
@click.option("--note", default="", help="What was different about this round.")
@click.pass_context
def cal_add(
    ctx: click.Context,
    name: str | None,
    scan_path: Path,
    slot: str | None,
    whole: bool,
    note: str,
) -> None:
    """Read a scanned chart and record it as the next calibration round."""
    lib = _lib(ctx)
    cfg = Config.load(lib.root)
    prof = _stored(lib, name)
    where = (
        profiles.Slot.parse(slot, prof.grid)
        if slot
        else (prof.pending or prof.next_slot)
    )
    # what this print was asked to be: the chart was rendered through whatever was
    # known then, which is exactly the correction fitted over the rounds recorded
    # so far — the round being added is not in that fit yet
    sent = calibrate_mod.sent_patches(prof.correction)  # the current chart
    scanned = calibrate_mod.read_scan(
        scan_path, cfg, slot=None if whole else where, grid=prof.grid
    )
    before = prof.residual
    rnd = prof.add_round(scanned, sent, where, scan=scan_path.name, note=note.strip())
    profiles.save(lib.root, prof)
    e = prof.score(rnd)
    trend = ""
    if before is not None:
        delta = before.mean - e.mean
        arrow = "[green]↓[/]" if delta > 0 else "[yellow]↑[/]"
        trend = f"  {arrow} {abs(delta):.1f} from round {rnd.n - 1}"
    console.print(
        f"[green]✓[/] round {rnd.n} recorded (slot {where.text}): this print was "
        f"off by mean {e.mean:.1f} / max {e.max:.1f} RGB over {e.measured} "
        f"reachable patch(es){trend}"
    )
    if e.clipped:
        console.print(
            f"[dim]{e.clipped} of {e.total} patch(es) are outside this medium's "
            "gamut — too dark, too light, or more saturated than its inks reach — "
            "so they are not counted. No calibration can hit them.[/]"
        )
    console.print(
        f"[dim]correction refitted over {len(prof.rounds)} round(s) — `sheet "
        f"--profile {prof.name}` uses it.[/]"
    )
    if not _converged(prof):
        console.print(
            f"[dim]another round: `proxdex calibrate chart {prof.name}` "
            f"(slot {prof.next_slot.text})[/]"
        )
    _suspect_round(prof, rnd)


def _converged(prof: profiles.Profile) -> bool:
    """Say when the loop is done, instead of inviting a round that buys nothing."""
    flat = prof.plateau
    if flat is None:
        return False
    console.print(
        f"[green]✓ this looks converged[/] — {flat.text}, so what is left is the "
        "medium's gamut and the scanner's noise, not something another chart fixes. "
        f"[dim]Print a card: `proxdex sheet check --profile {prof.name} <id>`. "
        "Measure again when the ink, the paper or the driver changes.[/]"
    )
    return True


#: how much worse than the best round so far still counts as ordinary variation.
#: Past it, the likely cause is the scan — wrong slot, upside down, or
#: auto-corrected — and a bad round genuinely damages the fit, so it is named
#: loudly rather than folded in quietly.
_CAL_SUSPECT_RATIO = 2.0
_CAL_SUSPECT_FLOOR = 5.0


def _suspect_round(prof: profiles.Profile, rnd: profiles.Round) -> None:
    """Say so when a round looks like a bad scan rather than a bad printer."""
    others = [r for r in prof.rounds if r.n != rnd.n]
    if not others:
        return
    best = min(others, key=lambda r: prof.score(r).mean)
    mine, theirs = prof.score(rnd).mean, prof.score(best).mean
    limit = max(theirs * _CAL_SUSPECT_RATIO, theirs + _CAL_SUSPECT_FLOOR)
    if mine <= limit:
        return
    err.print(
        f"[yellow]⚠[/] round {rnd.n} is much worse than round {best.n} "
        f"({mine:.1f} vs {theirs:.1f}) — check the scan is the "
        "right slot, the right way up and unretouched. [dim]`proxdex calibrate "
        f"disable {prof.name} --round {rnd.n}` refits without it, and keeps it.[/]"
    )


_ROUND = click.option("--round", "which", type=int, required=True, help="Round number.")


@calibrate.command("disable")
@_PROFILE_ARG
@_ROUND
@click.pass_context
def cal_disable(ctx: click.Context, name: str | None, which: int) -> None:
    """Leave a round out of the fit, without losing it.

    A misfeed, a crooked scan, or just a suspicion: switch it off and the
    correction refits without it. The round stays in the file with its number and
    its measurements, so `enable` puts it back exactly as it was — which is the
    only way to see what it was actually doing.
    """
    _switch(ctx, name, which, on=False)


@calibrate.command("enable")
@_PROFILE_ARG
@_ROUND
@click.pass_context
def cal_enable(ctx: click.Context, name: str | None, which: int) -> None:
    """Put a switched-off round back into the fit."""
    _switch(ctx, name, which, on=True)


def _switch(ctx: click.Context, name: str | None, which: int, *, on: bool) -> None:
    lib = _lib(ctx)
    prof = _stored(lib, name)
    before = prof.residual
    rnd = prof.switch_round(which, on=on)
    profiles.save(lib.root, prof)
    verb = "back in the fit" if on else "switched off"
    console.print(
        f"[green]✓[/] round {which} (slot {rnd.slot.text}) {verb} — "
        f"{len(prof.live)} of {len(prof.rounds)} round(s) now feed the correction"
    )
    after = prof.residual
    if before is not None and after is not None and before.mean != after.mean:
        console.print(
            f"[dim]the newest live round's error reads {after.mean:.1f} now, "
            f"was {before.mean:.1f}[/]"
        )
    if not prof.live:
        err.print(
            "[yellow]⚠[/] nothing is left in the fit, so this profile corrects "
            f"by its recipe alone. [dim]`proxdex calibrate enable {prof.name} "
            "--round N` to undo.[/]"
        )


@calibrate.command("proof")
@_PROFILE_ARG
@click.option(
    "-o",
    "--out",
    "out",
    type=UserPath(path_type=Path),
    default=None,
    help="Where to write the swatch sheet.",
)
@click.pass_context
def cal_proof(ctx: click.Context, name: str | None, out: Path | None) -> None:
    """Write a PNG comparing what you asked for with what the paper gave back.

    Two rows per patch — target above, most recent scan below. Numbers say a
    print is off; this says *how*, which is what tells you whether to keep going.
    """
    lib = _lib(ctx)
    prof = _stored(lib, name)
    if not prof.rounds:
        raise click.UsageError(f"'{prof.name}' has no rounds to proof yet")
    last = prof.rounds[-1]
    default = profiles.profiles_dir(lib.root) / f"{prof.name}_round{last.n}_proof.png"
    dst = out or default
    dst.parent.mkdir(parents=True, exist_ok=True)
    calibrate_mod.proof_sheet(last.scanned).save(dst)
    e = prof.score(last)
    console.print(
        f"[green]wrote[/] {dst} [dim]— round {last.n}, off by mean {e.mean:.1f} / "
        f"max {e.max:.1f} RGB[/]"
    )


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


#: the characters proxdex prints on purpose — the pipeline's state marks, the
#: size and progress glyphs. If a stream cannot encode *these*, it cannot encode
#: proxdex's output at all.
GLYPHS = "→·×⤳⌖✓⚠▤⬗◐↳—"


def writable_output() -> None:
    """Make sure stdout and stderr can carry proxdex's own characters.

    They are used deliberately (see the RUF001 ignore in pyproject) and a stream
    that cannot encode them must **degrade, not crash**. It crashed: piping
    ``proxdex --help`` on Windows died with ``UnicodeEncodeError`` on ``→``,
    because a redirected stream there falls back to the ANSI codepage (cp1252)
    rather than the console's own UTF-8 path. It is not a Windows quirk either —
    ``LC_ALL=C`` on Linux, which is what a container or a cron job often has, does
    exactly the same thing.

    So: UTF-8 where the stream cannot manage as it stands, and ``errors=replace``
    so the worst case is a ``?`` in place of a tick rather than a traceback in
    place of the command.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = getattr(stream, "encoding", None)
        if reconfigure is None:
            continue
        try:
            GLYPHS.encode(encoding or "ascii")
        except (UnicodeEncodeError, LookupError):
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    writable_output()
    try:
        cli(args=_hoist_root(sys.argv[1:]))
    except ProxdexError as e:
        # escaped: an error text is arbitrary, and rich reads `[...]` as markup and
        # *removes* it. The `proxdex ui` hint said `install "proxdex"` — dropping
        # the `[ui]` that is the entire point of the sentence — and any message
        # naming a path or a stage list would be silently edited the same way.
        err.print(f"[bold red]error:[/] {escape(str(e))}")
        raise SystemExit(1) from e
    finally:
        _api_note()


if __name__ == "__main__":
    main()
