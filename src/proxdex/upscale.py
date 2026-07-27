"""Upscaling, behind a backend — one today, and room for another.

proxdex does not upscale anything itself; it drives a neural upscaler and stores
what comes back. That tool is **not a Python dependency and cannot become one**:
Upscayl is an Electron application whose engine, ``upscayl-bin``, is a native
Vulkan binary, and it is not on PyPI (the ``upscayl`` name there is an unrelated
package). So it is installed the way desktop applications are installed, and
proxdex's job is to find it, use it, and — when it is absent — say so clearly
instead of failing halfway through a batch.

That is what this module is: an :class:`Upscaler` interface, a registry of the
backends proxdex knows, and :func:`availability` — a *probe* that answers "can
this machine upscale, and if not what should I do about it" without running or
raising anything. Adding a backend is one class plus one entry in
:data:`BACKENDS`; nothing else needs editing, because the step's own declaration
in :mod:`proxdex.steps` asks this module rather than naming Upscayl.

**The stage is not the backend.** ``Stage.UPSCALED`` exists whether or not
anything can produce it: a card may already hold an upscaled image, and a library
must stay readable and printable on a machine with no upscaler installed. So an
absent backend disables *running* the step — it never removes the step, the
stage, or the option to skip it.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from proxdex.config import Config, UpscaylModel, UpscaylScale
from proxdex.errors import FileError


class BackendId(StrEnum):
    """Which upscaler is doing the work — a closed set, like every other
    vocabulary in proxdex."""

    UPSCAYL = "upscayl"


@dataclass(frozen=True, slots=True)
class Availability:
    """Whether the step can run here, and what to do if it cannot.

    Deliberately a value rather than an exception: this is asked to *draw a
    screen* (`/api/meta`, the UI's Run button) and to write one line in
    ``proxdex where``, long before anyone asks for work to be done. A probe that
    raised would make "is it installed?" and "install it" the same operation.
    """

    backend: BackendId
    ready: bool
    #: where it was found, or why it was not — one line, already in the voice
    #: both the CLI and the UI use
    detail: str
    #: what to do about it, when there is something to do
    hint: str = ""

    @property
    def message(self) -> str:
        """The refusal, as one sentence — used verbatim by the CLI and the UI so
        there is one text and not two that drift."""
        return f"{self.detail}{f' {self.hint}' if self.hint else ''}"


class Upscaler(Protocol):
    """What proxdex needs of an upscaler: a name, a probe, and a run."""

    id: BackendId
    name: str
    #: how it is obtained, for the "not installed" message
    install: str

    def probe(self, cfg: Config) -> Availability:
        """Can this backend run here? Never raises, never runs the tool."""
        ...

    def run(
        self,
        src: Path,
        dst: Path,
        cfg: Config,
        *,
        model: UpscaylModel,
        scale: UpscaylScale,
        double: bool,
    ) -> None:
        """Upscale ``src`` to ``dst``. Raises :class:`FileError` on failure."""
        ...


# ------------------------------------------------------------------ upscayl --
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


def _find_bin(cfg: Config) -> str | None:
    if cfg.upscayl_bin:
        return cfg.upscayl_bin if Path(cfg.upscayl_bin).exists() else None
    for candidate in _BIN_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for name in ("upscayl-bin", "upscayl"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _find_models(cfg: Config) -> str | None:
    if cfg.upscayl_models:
        return cfg.upscayl_models if Path(cfg.upscayl_models).exists() else None
    for candidate in _MODEL_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def resolve_bin(cfg: Config) -> str:
    found = _find_bin(cfg)
    if found is None:
        raise FileError(UPSCAYL.probe(cfg).message)
    return found


def resolve_models(cfg: Config) -> str:
    found = _find_models(cfg)
    if found is None:
        raise FileError(UPSCAYL.probe(cfg).message)
    return found


class _Upscayl:
    """Upscayl's bundled CLI, driven exactly as the app drives it.

    The command construction mirrors the app (see
    ``upscayl/electron/utils/get-arguments.ts``):

    * the models and scales are closed sets — :class:`proxdex.config.UpscaylModel`
      and :class:`proxdex.config.UpscaylScale`, the app's own ``-n``/``-s``
      literals;
    * ``-s`` is passed only when the requested scale differs from the model's
      native scale (all built-ins are 4x), matching the app's ``includeScale``;
    * "double upscayl" runs the binary twice with the same model/scale, the
      second pass reading the first's output in place.

    On macOS the bundled binary and models are found inside ``Upscayl.app``;
    elsewhere set ``[tools] upscayl_bin`` / ``upscayl_models``.
    """

    id = BackendId.UPSCAYL
    name = "Upscayl"
    install = (
        "install Upscayl from https://upscayl.org (or `brew install --cask "
        "upscayl`), or set [tools] upscayl_bin in proxdex.toml"
    )

    def probe(self, cfg: Config) -> Availability:
        exe, models = _find_bin(cfg), _find_models(cfg)
        # the two halves fail for different reasons and want different fixes: a
        # configured path that does not exist is a typo, a missing app is an install
        if exe is None:
            configured = " (the configured [tools] upscayl_bin does not exist)"
            return Availability(
                backend=self.id,
                ready=False,
                detail=f"Upscayl not found{configured if cfg.upscayl_bin else ''}.",
                hint=self.install,
            )
        if models is None:
            return Availability(
                backend=self.id,
                ready=False,
                detail=f"Upscayl's binary is at {exe} but its models folder is not.",
                hint="set [tools] upscayl_models in proxdex.toml",
            )
        return Availability(backend=self.id, ready=True, detail=exe)

    def run(
        self,
        src: Path,
        dst: Path,
        cfg: Config,
        *,
        model: UpscaylModel,
        scale: UpscaylScale,
        double: bool,
    ) -> None:
        exe = resolve_bin(cfg)
        models = resolve_models(cfg)
        self._pass(exe, src, dst, models, model, scale)
        if double:  # second pass reads the first pass' output in place
            self._pass(exe, dst, dst, models, model, scale)
        if not dst.exists():
            raise FileError(f"upscayl produced no output for {src.name}")

    def _pass(
        self,
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


UPSCAYL = _Upscayl()

#: every upscaler proxdex knows, in preference order. Adding one means adding a
#: class above and an entry here — the step, the CLI, the API and the UI all read
#: this through :func:`availability` and name whatever answered.
BACKENDS: tuple[Upscaler, ...] = (UPSCAYL,)


# -------------------------------------------------------------------- public --
def resolve(cfg: Config) -> Upscaler:
    """The backend to use: the first one that is actually installed.

    Falls back to the first *known* backend when none is ready, so the caller
    still gets something to report a reason from rather than ``None`` to
    special-case.
    """
    return next((b for b in BACKENDS if b.probe(cfg).ready), BACKENDS[0])


def availability(cfg: Config) -> Availability:
    """Whether upscaling can run on this machine, and what to do if not."""
    return resolve(cfg).probe(cfg)


def run(
    src: Path,
    dst: Path,
    cfg: Config,
    *,
    model: UpscaylModel | None = None,
    scale: UpscaylScale | None = None,
    double: bool | None = None,
) -> None:
    """Upscale ``src`` to ``dst`` with whichever backend is installed."""
    backend = resolve(cfg)
    found = backend.probe(cfg)
    if not found.ready:
        raise FileError(found.message)
    backend.run(
        src,
        dst,
        cfg,
        model=cfg.upscayl_model if model is None else model,
        scale=cfg.upscayl_scale if scale is None else scale,
        double=cfg.upscayl_double if double is None else double,
    )
