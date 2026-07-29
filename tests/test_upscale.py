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


class TestWhatFactorACardGets:
    """The factor is derived from the source, and that is the whole point.

    A fixed factor is the wrong thing to hold still: source images arrive anywhere
    from 400 to 745px wide, so one factor scatters the masters it makes. Measured on
    a real library, identical settings produced **592 dpi** on one card and **1011**
    on another — and 592 dpi is a visibly softer print with nothing on screen to say
    so. So what is configured is a resolution and the factor is arithmetic.

    This is pinned rather than eyeballed because it is arithmetic nobody re-checks:
    the number that comes out is a file size, and a file size looks fine at any
    value. The reference row is a real card from a real library.
    """

    def test_the_reference_card_lands_exactly_where_it_used_to(self) -> None:
        """`base3-4` Dragonite: a 627px bordered master that the library holds at
        2508x3504. That was 2x-doubled by hand, at what worked out to 1011 dpi — just
        over the shipped 1000 dpi minimum, so the derivation arrives at the same place
        on its own. If it did not, the default would have quietly changed every master
        in an existing library."""
        plan = upscale.plan(627, 63.0, Config(upscayl_min_dpi=1000))
        assert plan.out_px == 2508
        assert (plan.scale.value, plan.double) == (2, True)
        assert plan.dpi == 1011
        assert not plan.short

    def test_a_small_source_is_enlarged_harder_than_a_large_one(self) -> None:
        """The behaviour a fixed factor cannot have. Both of these are real widths
        from the same library, and under one factor the small one printed at 653 dpi
        while the large one printed at 1184."""
        cfg = Config()
        small, large = upscale.plan(405, 63.0, cfg), upscale.plan(744, 63.0, cfg)
        assert upscale.effective(small.scale, double=small.double) > upscale.effective(
            large.scale, double=large.double
        )
        for plan in (small, large):
            assert plan.dpi >= 1000, plan

    def test_the_smallest_factor_that_clears_the_minimum_wins(self) -> None:
        """The rule, stated directly: the result clears the minimum, and no smaller
        factor would have. Both halves matter — the first is the promise, the second is
        what stops a card being enlarged further than it needs to be."""
        cfg = Config()
        for width in (405, 500, 600, 625, 627, 733, 744, 1200, 2508):
            plan = upscale.plan(width, 63.0, cfg)
            assert plan.out_px >= plan.want_px, width
            smaller = [
                s
                for s in type(plan.scale)
                if upscale.effective(s, double=plan.double)
                < upscale.effective(plan.scale, double=plan.double)
            ]
            for s in smaller:
                rival = width * upscale.effective(s, double=plan.double)
                assert rival < plan.want_px, (width, s)

    def test_every_real_source_width_clears_it(self) -> None:
        """The point of the whole thing. These are the widths a real library holds, and
        under one fixed factor they scattered from 592 to 1011 dpi."""
        cfg = Config()
        for width in (405, 416, 600, 625, 627, 733, 734, 744):
            plan = upscale.plan(width, 63.0, cfg)
            assert plan.dpi >= 1000, (width, plan.dpi)
            assert not plan.short, width

    def test_clearing_the_minimum_beats_landing_near_it(self) -> None:
        """A deliberate step, and `sheet_dpi` is the reason.

        The doubled ladder is 1, 4, 9, 16, so a source a few percent under jumps a long
        way: a 600px master goes to 5400px rather than the 2400px that would have landed
        it at 968 dpi. That looks wasteful until you notice the page is rendered at
        `sheet_dpi` — 1400 by default, 3472px across a 63mm card — so the 2400px version
        would have been resampled *up* 1.45x by a plain filter at print time, which is
        exactly the work the neural upscaler was run to avoid. Overshooting costs disk;
        undershooting costs resolution nothing downstream can restore.
        """
        cfg = Config()
        plan = upscale.plan(600, 63.0, cfg)
        assert plan.out_px == 5400
        assert plan.dpi > 1000
        # the rung below would have landed under the minimum *and* under sheet_dpi
        assert 600 * upscale.effective(plan.scale, double=True) > 600 * 4
        assert upscale.target_px(63.0, cfg.sheet_dpi) > 2400

    def test_a_source_too_small_to_clear_it_says_so(self) -> None:
        """The one case the minimum cannot be met: there is no factor that fixes a 120px
        scan, so the answer is the largest one *and* a flag — the alternative is finding
        out on paper."""
        plan = upscale.plan(120, 63.0, Config())
        assert plan.scale.value == 4
        assert plan.short
        assert "under the minimum" in plan.label

    def test_an_explicit_factor_is_honoured_and_says_nothing_about_dpi(self) -> None:
        """Asking for a factor is asking for that factor. It is also the one case
        with no target to report against, so the label must not invent one."""
        from proxdex.config import UpscaylScale

        plan = upscale.plan(627, 63.0, Config(), scale=UpscaylScale.X1)
        assert (plan.scale, plan.want_px) == (UpscaylScale.X1, 0)
        assert plan.out_px == 627 * 1**2  # X1 doubled is still X1
        assert "dpi" not in plan.label

    def test_no_target_falls_back_to_the_configured_factor(self) -> None:
        """0 turns it off, and then the library's fixed factor stands — which is what
        a library that had one before this existed keeps doing."""
        from proxdex.config import UpscaylScale

        plan = upscale.plan(627, 63.0, Config(upscayl_min_dpi=0))
        assert plan.scale is UpscaylScale.X2
        assert plan.want_px == 0

    def test_an_oversized_card_wants_more_pixels_for_the_same_resolution(self) -> None:
        """dpi is per inch of the *card*, so an 89mm planar card needs 1.4x the pixels
        of a 63mm one to print at the same resolution — which is why this is a
        resolution and not a pixel count. The same 627px source therefore has to be
        enlarged a rung harder to clear the minimum on the bigger card."""
        cfg = Config()
        assert upscale.target_px(89.0, 1000) > upscale.target_px(63.0, 1000)
        ordinary = upscale.plan(627, 63.0, cfg)
        oversized = upscale.plan(627, 89.0, cfg)
        assert oversized.want_px > ordinary.want_px
        assert oversized.out_px > ordinary.out_px
        # and both still clear it, measured against the card each is printed on
        assert not ordinary.short
        assert not oversized.short

    def test_the_step_offers_the_minimum_and_an_automatic_factor(self) -> None:
        """Both surfaces read the step registry, so what the UI renders and what the
        CLI accepts come from here. The factor is *optional* with no config default:
        unset has to mean "whatever clears the minimum" rather than some factor."""
        spec = steps.get("upscale")
        assert spec is not None
        target = spec.option("min_dpi")
        factor = spec.option("scale")
        assert target is not None
        assert factor is not None
        assert target.default(Config()) == 1000
        assert factor.optional
        assert not factor.config_field
        assert factor.default(Config()) is None
        # and the flag is dashed even though the wire key is not
        assert target.flag == "min-dpi"
        assert target.argv(900) == ["--min-dpi", "900"]
        # the unit is declared, so both surfaces can say *dpi* rather than a bare number
        assert target.unit == "dpi"

    def test_the_shipped_default_is_the_one_the_docs_name(self) -> None:
        """1000 dpi, which is 2480px across a 63mm card. Pinned because it is quoted in
        `DEFAULT_TOML`, the field help and the docs, and a drift between the number and
        the prose is the kind of thing nobody re-checks."""
        assert Config().upscayl_min_dpi == 1000
        assert upscale.target_px(63.0, 1000) == 2480
