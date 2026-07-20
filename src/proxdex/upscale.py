"""Drive Upscayl's bundled CLI (``upscayl-bin``) to produce stage-2 images.

Upscayl ships the ``upscayl-ncnn`` engine as a standalone binary next to a
folder of ``.param``/``.bin`` models. On macOS both live inside the app
bundle and are auto-detected; on other platforms, or a non-standard install,
set ``[tools] upscayl_bin`` / ``upscayl_models`` in ``proxdex.toml``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import Config
from .errors import FileError

# common bundle locations for the binary + models, checked in order
_BIN_CANDIDATES = (
    "/Applications/Upscayl.app/Contents/Resources/bin/upscayl-bin",
    str(Path.home() / "Applications/Upscayl.app/Contents/Resources/bin/upscayl-bin"),
    "/opt/Upscayl/resources/bin/upscayl-bin",
)
_MODEL_CANDIDATES = (
    "/Applications/Upscayl.app/Contents/Resources/models",
    str(Path.home() / "Applications/Upscayl.app/Contents/Resources/models"),
    "/opt/Upscayl/resources/models",
)


def resolve_bin(cfg: Config) -> str:
    if cfg.upscayl_bin:
        return cfg.upscayl_bin
    for candidate in _BIN_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for name in ("upscayl-bin", "upscayl"):
        found = shutil.which(name)
        if found:
            return found
    raise FileError(
        "upscayl-bin not found — install Upscayl, or set [tools] upscayl_bin "
        "in proxdex.toml"
    )


def resolve_models(cfg: Config) -> str:
    if cfg.upscayl_models:
        return cfg.upscayl_models
    for candidate in _MODEL_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise FileError(
        "Upscayl models folder not found — set [tools] upscayl_models in proxdex.toml"
    )


def available_models(cfg: Config) -> list[str]:
    models = Path(resolve_models(cfg))
    return sorted(p.stem for p in models.glob("*.param"))


def run(
    src: Path,
    dst: Path,
    cfg: Config,
    *,
    model: str | None = None,
    scale: int | None = None,
) -> None:
    exe = resolve_bin(cfg)
    models = resolve_models(cfg)
    cmd = [
        exe,
        "-i",
        str(src),
        "-o",
        str(dst),
        "-m",
        models,
        "-n",
        model or cfg.upscayl_model,
        "-s",
        str(scale or cfg.upscayl_scale),
        "-f",
        "png",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise FileError(f"upscayl failed on {src.name}: {detail}") from e
    if not dst.exists():
        raise FileError(f"upscayl produced no output for {src.name}")
