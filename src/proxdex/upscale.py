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

import os
import shutil
import subprocess
import sys
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

    @property
    def install(self) -> str:
        """How to obtain it, for the "not installed" message. A property, not a
        constant: the right advice depends on the platform asking."""
        ...

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
# Where the app installs itself, per platform. Every one of these is Upscayl's
# own layout, taken from its `electron-builder` config rather than guessed: the
# engine is `extraFiles`-copied to ``resources/bin`` and the models to
# ``resources/models``, under whatever the platform's app root is. The binary is
# ``upscayl-bin``, ``.exe`` on Windows (which also ships the vcomp DLLs beside
# it, so the *directory* matters — never copy the exe out on its own).
#
# None of this is exhaustive on purpose. Upscayl's Windows installer lets you
# choose the directory and also ships a portable zip, and a Linux AppImage runs
# from a temporary mount with no persistent path at all — which is exactly why
# ``[tools] upscayl_bin`` exists and why not finding it is a *reported* state
# rather than a crash.
def _env_dirs(*names: str) -> tuple[Path, ...]:
    """The environment paths among ``names`` that are actually set, deduplicated.

    Windows' install root is ``%ProgramFiles%``, which is not a fixed string: the
    drive varies, it is localised on some installs, and a 32-bit Python reports
    the x86 one — so it is read from the environment, never spelled out.
    """
    seen: dict[Path, None] = {}
    for name in names:
        value = os.environ.get(name)
        if value:
            seen.setdefault(Path(value), None)
    return tuple(seen)


def platform() -> str:
    """``sys.platform``, through a function on purpose.

    Two reasons, both practical: a type checker *narrows* ``sys.platform`` to the
    host it is running on, so branching on it directly leaves the other platforms'
    code unchecked — and a test cannot pretend to be Windows. Behind a ``-> str``
    call, every branch is analysed and every branch is reachable from a test, which
    is the only coverage the Windows paths can ever get: CI has no Windows runner.
    """
    return sys.platform


def _app_roots() -> tuple[Path, ...]:
    if platform() == "win32":
        # the NSIS installer is `perMachine`, so Program Files by default; the
        # LOCALAPPDATA entry covers a per-user install or a portable unpack there
        programs = _env_dirs("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")
        local = _env_dirs("LOCALAPPDATA")
        return tuple(p / "Upscayl" for p in programs) + tuple(
            p / "Programs" / "Upscayl" for p in local
        )
    if platform() == "darwin":
        return (
            Path("/Applications/Upscayl.app/Contents"),
            Path.home() / "Applications/Upscayl.app/Contents",
        )
    return (Path("/opt/Upscayl"),)  # the .deb and .rpm both land here


def _resources() -> str:
    """The resources directory's name inside the app root.

    macOS capitalises it (``Contents/Resources``); Windows and Linux do not. Case
    matters on a case-sensitive volume, so it is not left to luck.
    """
    return "Resources" if platform() == "darwin" else "resources"


def _exe_name() -> str:
    return "upscayl-bin.exe" if platform() == "win32" else "upscayl-bin"


def installs() -> tuple[tuple[Path, Path], ...]:
    """``(engine, models)`` pairs this platform is searched for, in order.

    Public because "where did you look?" is the question a person asks when their
    install is somewhere else — ``proxdex where`` prints these when nothing is
    found, which beats being told only that it is missing.

    Paired rather than searched separately, so a configured binary can never be
    matched against the models folder of a *different* install.
    """
    res = _resources()
    return tuple(
        (root / res / "bin" / _exe_name(), root / res / "models")
        for root in _app_roots()
    )


def _discover() -> tuple[Path, Path] | None:
    """The first install where both halves are present."""
    return next(
        (
            (exe, models)
            for exe, models in installs()
            if exe.exists() and models.is_dir()
        ),
        None,
    )


def _find_bin(cfg: Config) -> str | None:
    if cfg.upscayl_bin:
        return cfg.upscayl_bin if Path(cfg.upscayl_bin).exists() else None
    found = _discover()
    if found is not None:
        return str(found[0])
    # a PATH hit is last: `which` finds `upscayl-bin.exe` on Windows too, but
    # Upscayl's installer does not put its resources/bin on PATH, so this is for
    # a hand-placed build rather than the ordinary install
    return next(
        filter(None, (shutil.which(n) for n in ("upscayl-bin", "upscayl"))), None
    )


def _find_models(cfg: Config) -> str | None:
    if cfg.upscayl_models:
        return cfg.upscayl_models if Path(cfg.upscayl_models).exists() else None
    found = _discover()
    return str(found[1]) if found is not None else None


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

    @property
    def install(self) -> str:
        """How to get it *on this machine*.

        Telling a Linux user to run `brew` is the kind of small lie that makes the
        rest of the message untrustworthy, so the suggestion follows the platform.
        """
        how = {
            "darwin": "`brew install --cask upscayl`, or from https://upscayl.org",
            "win32": "the installer at https://upscayl.org",
        }.get(platform(), "the AppImage, .deb or .rpm at https://upscayl.org")
        return f"install Upscayl — {how} — or set [tools] upscayl_bin in proxdex.toml"

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


#: millimetres per inch, so a dpi target becomes a pixel count
_MM_PER_INCH = 25.4

#: how far under the minimum still counts as clearing it. One pixel of rounding, no
#: more — this exists so a card computing to 999.6 dpi is not sent up a whole rung.
_SLACK = 0.995


def effective(scale: UpscaylScale, *, double: bool) -> int:
    """How much bigger the output really is. "Double Upscayl" runs the model a
    second time over its own output, so the factor is *squared* rather than
    doubled: 2× twice is 4×, and 4× twice is 16×."""
    return scale.value**2 if double else scale.value


def target_px(card_w_mm: float, dpi: int) -> int:
    """The master's wanted width in pixels: ``dpi`` across the card."""
    return max(1, round(card_w_mm / _MM_PER_INCH * dpi))


@dataclass(frozen=True, slots=True)
class Plan:
    """Which factor this card gets, and what it lands on.

    Carried as a value rather than returned as a bare enum because the *result* is
    the interesting part and every surface reports it: undershooting the target is
    a fact about the source that nobody can fix by trying harder, and it has to be
    said out loud rather than discovered on paper.
    """

    scale: UpscaylScale
    double: bool
    #: the source width the factor was chosen for
    src_px: int
    #: the width it will come out at
    out_px: int
    #: pixels wanted; 0 when no minimum was set and the configured factor stands
    want_px: int
    #: the card the pixels are spread across, which is what makes them a resolution
    card_w_mm: float

    @property
    def dpi(self) -> int:
        """The resolution the master lands at, across the card it was planned for."""
        if not self.card_w_mm:
            return 0
        return round(self.out_px / self.card_w_mm * _MM_PER_INCH)

    @property
    def short(self) -> bool:
        """Did even the largest factor fail to clear the minimum?

        The only case that can, since the minimum is otherwise always met. It means
        the source has too few pixels for the size you print at, which no setting
        fixes — so it is reported rather than discovered on paper.
        """
        return bool(self.want_px) and self.out_px < self.want_px * _SLACK

    @property
    def label(self) -> str:
        """One phrase for the CLI's per-card line and the UI's readout."""
        tag = f"×{self.scale.value}{' ×2' if self.double else ''}"
        if not self.want_px:
            return tag
        return f"{tag} → {self.out_px}px, {self.dpi}dpi" + (
            " — under the minimum, the source has no more detail" if self.short else ""
        )


def plan(
    src_px: int,
    card_w_mm: float,
    cfg: Config,
    *,
    scale: UpscaylScale | None = None,
    double: bool | None = None,
) -> Plan:
    """Which factor to enlarge a ``src_px``-wide source by.

    **The factor is the wrong thing to hold still.** Sources arrive anywhere from 400 to
    745px wide, so one fixed factor scatters the masters it makes — measured on a real
    library, identical settings produced 592 dpi on one card and 1011 on another, with
    nothing on screen to say so. So what is configured is a **minimum resolution**
    (:attr:`~proxdex.config.Config.upscayl_min_dpi`) and the factor is arithmetic: the
    *smallest* one that clears it, so a small scan is enlarged harder than a large one
    and nothing is enlarged further than it needs to be.

    **Clearing the minimum wins over landing near it, and `sheet_dpi` is why.** The page
    is rendered at :attr:`~proxdex.config.Config.sheet_dpi` — 1400 by default, which is
    3472px across a 63mm card — so a master below that is resampled *up* at print time
    by a plain filter, which is exactly the work the neural upscaler was run to avoid.
    Overshooting costs disk and a little time; undershooting costs resolution on paper
    that nothing downstream can put back.

    That does mean a step: the doubled ladder is 1, 4, 9, 16, so a source a few percent
    under can jump a long way (a 600px master goes to 5400px rather than 2400px to clear
    1000 dpi). Taken deliberately — the 2400px version would have been upsampled 1.45x
    by the sheet renderer anyway. An explicit ``scale`` is honoured as-is, and
    ``min_dpi = 0`` turns the whole thing off in favour of the configured factor.
    """
    use_double = cfg.upscayl_double if double is None else double
    want = target_px(card_w_mm, cfg.upscayl_min_dpi)
    fixed = cfg.upscayl_scale if scale is None else scale
    if scale is not None or cfg.upscayl_min_dpi <= 0:
        return Plan(
            scale=fixed,
            double=use_double,
            src_px=src_px,
            out_px=src_px * effective(fixed, double=use_double),
            want_px=0,
            card_w_mm=card_w_mm,
        )

    # the smallest factor that clears the minimum; the largest if none does
    ladder = sorted(UpscaylScale, key=lambda s: effective(s, double=use_double))
    chosen = next(
        (
            s
            for s in ladder
            if src_px * effective(s, double=use_double) >= want * _SLACK
        ),
        ladder[-1],
    )
    return Plan(
        scale=chosen,
        double=use_double,
        src_px=src_px,
        out_px=src_px * effective(chosen, double=use_double),
        want_px=want,
        card_w_mm=card_w_mm,
    )


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
