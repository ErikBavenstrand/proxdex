"""Upscaling is the one step that needs a tool proxdex cannot ship.

Upscayl is an Electron application with a native Vulkan engine; it is not on PyPI
and no extra can install it. So "is it here?" is a question the CLI, the API and
the UI all have to be able to ask *without* running anything — and the answer has
to leave a library usable either way, because a card may already hold an upscaled
image and skipping the step is a first-class choice.

These are pinned because both halves fail quietly. A probe that raised would turn
drawing a screen into an error path, and a step that vanished when its tool was
missing would make an existing upscaled image unexplainable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proxdex import steps, upscale
from proxdex.config import Config
from proxdex.library import Stage, Step


def with_upscayl(tmp_path: Path) -> Config:
    """A config pointing at an Upscayl that exists — the installed case, without
    depending on whether this machine really has the app."""
    exe = tmp_path / "upscayl-bin"
    exe.write_bytes(b"")
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)  # called twice in one test, deliberately
    return Config(upscayl_bin=str(exe), upscayl_models=str(models))


def without_upscayl(tmp_path: Path) -> Config:
    """A config whose configured binary does not exist. Deterministic on a machine
    that *does* have Upscayl installed, which is the awkward case for this test."""
    return Config(upscayl_bin=str(tmp_path / "nope" / "upscayl-bin"))


class TestAvailability:
    def test_it_answers_rather_than_raising(self, tmp_path: Path) -> None:
        found = upscale.availability(without_upscayl(tmp_path))
        assert found.ready is False
        assert found.backend is upscale.BackendId.UPSCAYL

    def test_it_says_what_to_do(self, tmp_path: Path) -> None:
        found = upscale.availability(without_upscayl(tmp_path))
        assert "upscayl" in found.hint.lower()
        # one text, used verbatim by the CLI's refusal and the UI's panel
        assert found.hint in found.message
        assert found.detail in found.message

    def test_a_configured_path_that_is_wrong_says_so(self, tmp_path: Path) -> None:
        """Different fix from "not installed": that one is a typo in the config."""
        found = upscale.availability(without_upscayl(tmp_path))
        assert "configured" in found.detail

    def test_an_installed_backend_reports_where_it_is(self, tmp_path: Path) -> None:
        cfg = with_upscayl(tmp_path)
        found = upscale.availability(cfg)
        assert found.ready
        assert found.detail == cfg.upscayl_bin
        assert found.hint == ""

    def test_models_missing_is_its_own_answer(self, tmp_path: Path) -> None:
        """The binary and the models are found separately and want different
        fixes, so they must not collapse into one 'not installed'."""
        exe = tmp_path / "upscayl-bin"
        exe.write_bytes(b"")
        found = upscale.availability(
            Config(upscayl_bin=str(exe), upscayl_models=str(tmp_path / "gone"))
        )
        assert not found.ready
        assert "models" in found.detail
        assert "upscayl_models" in found.hint


class TestBackends:
    def test_there_is_at_least_one_and_it_satisfies_the_interface(self) -> None:
        assert upscale.BACKENDS
        for backend in upscale.BACKENDS:
            assert isinstance(backend.id, upscale.BackendId)
            assert backend.name
            assert backend.install

    def test_resolve_falls_back_to_a_known_backend(self, tmp_path: Path) -> None:
        """Never None: the caller wants something to report a reason from, not a
        special case to write."""
        assert upscale.resolve(without_upscayl(tmp_path)) is upscale.BACKENDS[0]

    def test_resolve_prefers_one_that_is_installed(self, tmp_path: Path) -> None:
        assert (
            upscale.resolve(with_upscayl(tmp_path)).probe(with_upscayl(tmp_path)).ready
        )


class TestWhereItLooks:
    """The per-platform install paths — Upscayl's own layout, from its
    ``electron-builder`` config.

    This is the only coverage these can get: CI has no Windows runner, and the
    Windows branch is not even *typechecked* on another host (a checker narrows
    ``sys.platform`` to the machine it runs on), which is why the platform is read
    through :func:`upscale.platform` instead of directly.

    Assertions are on path *components*, never on separators: faking Windows on a
    POSIX host still builds ``PosixPath``s, so only the structure is meaningful
    here — the separators are the real ``Path``'s business.
    """

    def fake(self, monkeypatch: pytest.MonkeyPatch, plat: str, **env: str) -> None:
        monkeypatch.setattr(upscale, "platform", lambda: plat)
        for name in (
            "ProgramW6432",
            "ProgramFiles",
            "ProgramFiles(x86)",
            "LOCALAPPDATA",
        ):
            monkeypatch.delenv(name, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

    def test_windows_looks_under_program_files(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.fake(monkeypatch, "win32", ProgramFiles=r"C:\Program Files")
        (exe, models), *rest = upscale.installs()
        assert not rest
        assert exe.parts[-4:] == ("Upscayl", "resources", "bin", "upscayl-bin.exe")
        assert models.parts[-3:] == ("Upscayl", "resources", "models")

    def test_windows_covers_a_per_user_install(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.fake(monkeypatch, "win32", LOCALAPPDATA=r"C:\Users\x\AppData\Local")
        ((exe, _),) = upscale.installs()
        assert exe.parts[-5:-1] == ("Programs", "Upscayl", "resources", "bin")

    def test_the_program_files_variables_are_deduplicated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 64-bit install reports the same directory twice; probing it twice is
        just two stat calls and a duplicate in any message built from this."""
        self.fake(
            monkeypatch,
            "win32",
            ProgramW6432=r"C:\Program Files",
            ProgramFiles=r"C:\Program Files",
        )
        assert len(upscale.installs()) == 1

    def test_macos_capitalises_resources(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``Contents/Resources`` on macOS, ``resources`` elsewhere — and case
        matters on a case-sensitive volume, so it is not left to luck."""
        self.fake(monkeypatch, "darwin")
        exe, models = upscale.installs()[0]
        assert "Resources" in exe.parts
        assert exe.name == "upscayl-bin"
        assert models.parts[-1] == "models"

    def test_linux_looks_in_opt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fake(monkeypatch, "linux")
        exe, _ = upscale.installs()[0]
        assert exe.parts[-4:] == ("Upscayl", "resources", "bin", "upscayl-bin")

    def test_the_install_advice_suits_the_platform(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Telling a Linux user to run `brew` is the kind of small lie that makes
        the rest of the message untrustworthy."""
        cfg = without_upscayl(tmp_path)
        self.fake(monkeypatch, "darwin")
        assert "brew" in upscale.availability(cfg).hint
        self.fake(monkeypatch, "linux")
        linux = upscale.availability(cfg).hint
        assert "brew" not in linux
        assert "AppImage" in linux
        self.fake(monkeypatch, "win32")
        assert "installer" in upscale.availability(cfg).hint

    def test_a_windows_shaped_install_is_actually_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The whole point, end to end: a tree shaped like Upscayl's Windows
        install is discovered with no configuration at all."""
        root = tmp_path / "Upscayl" / "resources"
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "upscayl-bin.exe").write_bytes(b"")
        (root / "models").mkdir()
        self.fake(monkeypatch, "win32", ProgramFiles=str(tmp_path))

        found = upscale.availability(Config())

        assert found.ready
        assert found.detail.endswith("upscayl-bin.exe")

    def test_the_two_halves_come_from_one_install(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """An install with the binary but no models must not be reported ready by
        borrowing another install's models folder."""
        root = tmp_path / "Upscayl" / "resources"
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "upscayl-bin.exe").write_bytes(b"")
        self.fake(monkeypatch, "win32", ProgramFiles=str(tmp_path))
        assert not upscale.availability(Config()).ready


class TestTheStepSurvivesIt:
    """An absent tool disables *running*, and nothing else."""

    def test_the_step_is_still_declared(self, tmp_path: Path) -> None:
        cfg = without_upscayl(tmp_path)
        spec = steps.get("upscale")
        assert spec is not None
        assert spec.stage is Stage.UPSCALED
        assert spec.step is Step.UPSCALE
        assert spec.json(cfg)["tool_ready"] is False

    def test_the_stage_is_still_in_the_pipeline(self, tmp_path: Path) -> None:
        """A card may already hold an upscaled image; a library with no upscaler
        installed still has to read and print it."""
        assert Stage.UPSCALED in steps.STAGES
        keys = [s["key"] for s in steps.json_pipeline(without_upscayl(tmp_path))]
        assert "upscale" in keys

    def test_it_is_still_skippable(self, tmp_path: Path) -> None:
        """Which is the honest way past it, so it must not be gated too."""
        spec = steps.get("upscale")
        assert spec is not None
        assert spec.json(without_upscayl(tmp_path))["skippable"]

    def test_a_step_with_nothing_to_install_says_nothing(self, tmp_path: Path) -> None:
        """Only a step that declares `needs` reports a tool — border and grade run
        in-process and must not grow a spurious "installed" claim."""
        for key in ("border", "grade"):
            spec = steps.get(key)
            assert spec is not None
            body = spec.json(without_upscayl(tmp_path))
            assert body["tool_ready"] is True
            assert body["tool"] is None
