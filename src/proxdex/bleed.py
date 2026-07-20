"""Drive ``cardbleed`` with per-edge extensions computed from the measured
frame, so the printed-and-cut card ends up with correct proportions.

The extension for the top and sides is ``(frame correction) + (cut bleed)``;
the bottom (never measured, already thick) just gets the cut bleed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .borders import Borders, Target
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


def frame_plan(b: Borders, tgt: Target) -> Extension:
    """Per-edge expansion to bring a too-thin frame up to trim proportions.

    Top and sides only (the bottom frame is legitimately thicker); no cut bleed
    — that is added later, at the sheet step, outside the trim.
    """
    return Extension(
        top=round(max(0.0, tgt.top - b.top)),
        left=round(max(0.0, tgt.side - b.left)),
        right=round(max(0.0, tgt.side - b.right)),
        bottom=0,
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
