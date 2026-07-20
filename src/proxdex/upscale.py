"""Drive Upscayl's bundled CLI (``upscayl-bin``) to produce stage-2 images.

The command construction mirrors the Upscayl app exactly (see
``upscayl/electron/utils/get-arguments.ts``):

* the seven default models are the app's ``MODELS`` ids;
* ``-s`` is passed only when the requested scale differs from the model's
  native scale (all defaults are 4x), matching the app's ``includeScale``;
* "double upscayl" runs the binary twice with the same model/scale, the
  second pass reading the first's output in place.

On macOS the bundled binary and models are auto-detected inside
``Upscayl.app``; elsewhere set ``[tools] upscayl_bin`` / ``upscayl_models``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import Config
from .errors import FileError

#: the app's seven built-in models, in its own order (the `-n` literals)
MODELS: tuple[str, ...] = (
    "upscayl-standard-4x",
    "upscayl-lite-4x",
    "high-fidelity-4x",
    "remacri-4x",
    "ultramix-balanced-4x",
    "ultrasharp-4x",
    "digital-art-4x",
)

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


def model_scale(model: str) -> int:
    """The model's native scale, read from its name (app's getModelScale)."""
    name = model.lower()
    if "x2" in name or "2x" in name:
        return 2
    if "x3" in name or "3x" in name:
        return 3
    return 4


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


def _pass(exe: str, inp: Path, out: Path, models: str, model: str, scale: int) -> None:
    include_scale = model_scale(model) != scale
    cmd = [exe, "-i", str(inp), "-o", str(out)]
    if include_scale:  # matches the app: omit -s when scale == model native scale
        cmd += ["-s", str(scale)]
    cmd += ["-m", models, "-n", model, "-f", "png"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise FileError(f"upscayl failed on {inp.name}: {detail}") from e


def run(
    src: Path,
    dst: Path,
    cfg: Config,
    *,
    model: str | None = None,
    scale: int | None = None,
    double: bool | None = None,
) -> None:
    exe = resolve_bin(cfg)
    models = resolve_models(cfg)
    model = model or cfg.upscayl_model
    scale = cfg.upscayl_scale if scale is None else scale
    double = cfg.upscayl_double if double is None else double

    _pass(exe, src, dst, models, model, scale)
    if double:  # second pass reads the first pass' output in place
        _pass(exe, dst, dst, models, model, scale)
    if not dst.exists():
        raise FileError(f"upscayl produced no output for {src.name}")
