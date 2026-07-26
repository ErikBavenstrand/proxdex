"""Drive Upscayl's bundled CLI (``upscayl-bin``) to produce stage-2 images.

The command construction mirrors the Upscayl app exactly (see
``upscayl/electron/utils/get-arguments.ts``):

* the models and scales are closed sets — :class:`proxdex.config.UpscaylModel`
  and :class:`proxdex.config.UpscaylScale`, the app's own ``-n``/``-s`` literals;
* ``-s`` is passed only when the requested scale differs from the model's
  native scale (all built-ins are 4x), matching the app's ``includeScale``;
* "double upscayl" runs the binary twice with the same model/scale, the
  second pass reading the first's output in place.

On macOS the bundled binary and models are auto-detected inside
``Upscayl.app``; elsewhere set ``[tools] upscayl_bin`` / ``upscayl_models``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from proxdex.config import Config, UpscaylModel, UpscaylScale
from proxdex.errors import FileError

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


def _pass(
    exe: str,
    inp: Path,
    out: Path,
    models: str,
    model: UpscaylModel,
    scale: UpscaylScale,
) -> None:
    cmd = [exe, "-i", str(inp), "-o", str(out)]
    if model.native_scale is not scale:  # the app omits -s when they match
        cmd += ["-s", str(scale.value)]
    cmd += ["-m", models, "-n", model.value, "-f", "png"]
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
    model: UpscaylModel | None = None,
    scale: UpscaylScale | None = None,
    double: bool | None = None,
) -> None:
    exe = resolve_bin(cfg)
    models = resolve_models(cfg)
    use_model = cfg.upscayl_model if model is None else model
    use_scale = cfg.upscayl_scale if scale is None else scale
    use_double = cfg.upscayl_double if double is None else double

    _pass(exe, src, dst, models, use_model, use_scale)
    if use_double:  # second pass reads the first pass' output in place
        _pass(exe, dst, dst, models, use_model, use_scale)
    if not dst.exists():
        raise FileError(f"upscayl produced no output for {src.name}")
