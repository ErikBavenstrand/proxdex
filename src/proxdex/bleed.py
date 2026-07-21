"""Drive ``cardbleed`` to expand a card's edges — never crop, never auto-detect.

Two expansions: bring the card to the correct aspect ratio (pad the short axis),
and optional explicit per-edge growth (from the CLI/UI) to nudge the framing.
The cut bleed added at sheet time is separate (``uniform``).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .errors import FileError


def _resolve_exe(cmd: str) -> str | None:
    """Find ``cmd`` on PATH, or next to the running interpreter.

    cardbleed is a dependency, so after ``uv tool install proxdex`` it lives in
    proxdex's isolated venv (beside this interpreter) but not on the user's
    PATH — check there too so stage 4 works out of the box.
    """
    if (found := shutil.which(cmd)) is not None:
        return found
    sibling = Path(sys.executable).parent / cmd
    return str(sibling) if sibling.exists() else None


@dataclass(slots=True)
class Extension:
    top: int
    bottom: int
    left: int
    right: int

    def as_flags(self) -> list[str]:
        return [
            "--top",
            str(self.top),
            "--bottom",
            str(self.bottom),
            "--left",
            str(self.left),
            "--right",
            str(self.right),
        ]


# aspect deviation above which a card visibly doesn't fill the cutout
ASPECT_TOL = 0.004


def aspect_delta(w: int, h: int, cfg: Config) -> float:
    """Image aspect minus card aspect; ~0 means correctly formatted.

    Positive = too wide (needs height); negative = too tall (needs width).
    """
    return w / h - cfg.card_w_mm / cfg.card_h_mm


def format_ok(w: int, h: int, cfg: Config) -> bool:
    return abs(aspect_delta(w, h, cfg)) <= ASPECT_TOL


def plan(
    w: int,
    h: int,
    cfg: Config,
    *,
    top_mm: float = 0.0,
    bottom_mm: float = 0.0,
    left_mm: float = 0.0,
    right_mm: float = 0.0,
    fix_aspect: bool | None = None,
) -> Extension:
    """Edges to add: explicit per-edge growth first, then (if aspect-fix is on)
    pad the short axis to the card aspect, split per ``aspect_bias_x/y``. All
    expansion, never crop. ``fix_aspect`` overrides the config when not None —
    the align UI passes exact margins with it off.
    """
    fix = cfg.border_fix_aspect if fix_aspect is None else fix_aspect
    ppm = cfg.px_per_mm(w)
    top, bottom = top_mm * ppm, bottom_mm * ppm
    left, right = left_mm * ppm, right_mm * ppm
    if fix:
        nw, nh = w + left + right, h + top + bottom
        card = cfg.card_w_mm / cfg.card_h_mm
        if nw / nh > card:  # too wide → add height
            pad = nw / card - nh
            top += pad * cfg.border_aspect_bias_y
            bottom += pad * (1.0 - cfg.border_aspect_bias_y)
        elif nw / nh < card:  # too tall → add width
            pad = nh * card - nw
            left += pad * cfg.border_aspect_bias_x
            right += pad * (1.0 - cfg.border_aspect_bias_x)
    return Extension(
        top=round(top), bottom=round(bottom), left=round(left), right=round(right)
    )


def uniform(px: int) -> Extension:
    """Equal extension on all four edges — the cut bleed added at print time."""
    return Extension(top=px, bottom=px, left=px, right=px)


def run(src: Path, dst: Path, ext: Extension, cfg: Config) -> None:
    exe = _resolve_exe(cfg.cardbleed_cmd)
    if exe is None:
        raise FileError(
            f"{cfg.cardbleed_cmd!r} not found — it ships as a proxdex dependency, "
            "so reinstall with `uv tool install --force proxdex`, or `pip install "
            "cardbleed`"
        )
    suffix = "__cb"
    cmd = [
        exe,
        str(src),
        "-o",
        str(dst.parent),
        "--suffix",
        suffix,
        "--force",
        *ext.as_flags(),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise FileError(f"cardbleed failed on {src.name}: {e.stderr.strip()}") from e
    produced = dst.parent / f"{src.stem}{suffix}{src.suffix}"
    if not produced.exists():
        raise FileError(f"cardbleed produced no output for {src.name}")
    produced.replace(dst)
