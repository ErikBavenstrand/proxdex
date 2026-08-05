"""Per-run overrides: every page setting, on the page that prints it.

`Config.run_options(Run.SHEET)` is the **single declaration** of what a print run may
override, and this file earns its place because that used to be four declarations and
they had drifted: a hand-written `click.option`, a field on the request body, a branch
in the CLI's override helper and another in the web layer's. Twenty of the twenty-seven
settings a print run reads had been added to `Config` and to none of the four, so no
surface could touch them — including the front/back ink offsets, which are the ones you
reach for with a misregistered duplex sheet in your hand and which no screen offered.

Three things are pinned, and each of them is invisible until a sheet comes off a
printer wrong:

1. **A flag keeps its spelling.** The flags are *derived* from field names now, so a
   wrong rule would silently rename `--bleed` and break every command line and script
   that has ever used it. The eight that existed before are asserted by name.
2. **The two surfaces cannot disagree.** `SheetBody.argv()` must spell flags the CLI
   actually has, and `_apply_overrides` must change the same settings the CLI's
   `_overrides` does — otherwise the page count the builder promises is not the page
   count the PDF has, which is the one thing `sheet.plan` exists to guarantee.
3. **An override never writes the config.** A run is this paper on this printer today;
   a builder that edited `proxdex.toml` would silently redefine the library from a
   one-off job.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from PIL import Image

from proxdex import config, webui
from proxdex.cli import cli
from proxdex.config import MARKER, Config, OptKind, Run
from proxdex.errors import ConfigError
from proxdex.library import Library

#: what `proxdex sheet` accepted before any of this was derived. A flag is a promise:
#: these appear in shell history, scripts and this project's own README.
HISTORICAL_FLAGS = {
    "sheet_faces": "faces",
    "sheet_page": "page",
    "sheet_orientation": "orientation",
    "sheet_dpi": "dpi",
    "sheet_cols": "cols",
    "sheet_rows": "rows",
    "bleed_mm": "bleed",
    "sheet_guides": "guides",
}


def options() -> tuple[Any, ...]:
    return Config.run_options(Run.SHEET)


def by_key() -> dict[str, Any]:
    return {o.key: o for o in options()}


class TestTheDeclarationIsTheOnlyOne:
    def test_a_print_run_can_override_every_setting_it_reads(self) -> None:
        """The point of the whole thing. Anything a sheet run *reads* out of the
        config is a page setting, so the page that prints it must be able to change
        it for one job — the offsets, the guide colour, the registration inset, all
        of which existed in `Config` and were reachable from nowhere."""
        keys = set(by_key())
        for wanted in (
            "sheet_front_offset_x_mm",
            "sheet_front_offset_y_mm",
            "sheet_back_offset_x_mm",
            "sheet_back_offset_y_mm",
            "sheet_margin_mm",
            "sheet_spacing_mm",
            "sheet_spacing_y_mm",
            "sheet_duplex_flip",
            "sheet_fit",
            "sheet_guide_style",
            "sheet_guide_placement",
            "sheet_guide_mm",
            "sheet_guide_color",
            "sheet_guide_width_mm",
            "sheet_guides_front",
            "sheet_guides_back",
            "sheet_reg_marks",
            "sheet_reg_inset_mm",
            "sheet_back_image",
        ):
            assert wanted in keys, wanted

    def test_the_trim_is_deliberately_not_overridable(self) -> None:
        """Not an oversight, and the comment in `config.py` says so: the card size is
        what every stored master was *fitted* to, so a one-run override would impose
        cards at a size nothing was fitted at and `cover` would crop the difference
        off two edges — invisible until it is cut."""
        assert "card_w_mm" not in by_key()
        assert "card_h_mm" not in by_key()

    def test_opening_the_pdf_is_not_a_page_setting(self) -> None:
        """`sheet_open` launches an application on the machine the command was typed
        on, which is why `/api/sheet` always passes `--no-open`. It keeps `sheet`'s
        own flag and stays out of this list."""
        assert "sheet_open" not in by_key()

    def test_every_option_can_describe_itself(self) -> None:
        """These are rendered as a form row by a screen that spells none of them."""
        for opt in options():
            assert opt.label, opt.key
            # short is fine ("Cards down the page.") — empty is not
            assert len(opt.help) > 10, opt.key
            assert opt.group, opt.key

    def test_a_closed_set_offers_its_own_values(self) -> None:
        for opt in options():
            if opt.kind is OptKind.CHOICE:
                assert opt.choices, opt.key
                # an optional setting's default is *unset*, which is
                # deliberately not one of the values — see the next test
                assert opt.optional or opt.default_text in opt.choices, opt.key
            else:
                assert not opt.choices, opt.key

    def test_an_optional_setting_says_what_unset_does(self) -> None:
        """A field declared ``T | None`` has a fourth state beyond its type, and a
        control cannot render it as an empty box: "unset" means something specific and
        different per setting — for the backs' guides, "the same as the fronts". So the
        wording is declared beside the setting, the way `steps.StepOption.auto_label`
        already is, rather than invented by whichever screen draws it."""
        optionals = [o for o in options() if o.optional]
        assert optionals, "the mechanism exists for the backs' guides"
        for opt in optionals:
            assert opt.auto, opt.key
            assert opt.default_text == "", opt.key

    def test_a_numeric_option_is_bounded(self) -> None:
        """Unbounded, a typo becomes a 4800mm margin or a 900-column grid. Both
        surfaces read these same two numbers."""
        for opt in options():
            if opt.kind in {OptKind.INT, OptKind.FLOAT}:
                assert opt.low is not None, opt.key
                assert opt.high is not None, opt.key
                assert opt.low < opt.high, opt.key


class TestFlagsKeepTheirSpelling:
    """The flags are derived, so the derivation has to reproduce history exactly."""

    def test_the_flags_that_existed_are_unchanged(self) -> None:
        known = by_key()
        for key, flag in HISTORICAL_FLAGS.items():
            assert known[key].flag == flag, key

    def test_no_two_settings_claim_one_flag(self) -> None:
        flags = [o.flag for o in options()]
        assert len(flags) == len(set(flags)), flags

    def test_a_flag_is_dashed_and_carries_no_unit(self) -> None:
        """`sheet_front_offset_x_mm` → `front-offset-x`: the section prefix and the
        unit suffix are noise on a command line."""
        assert by_key()["sheet_front_offset_x_mm"].flag == "front-offset-x"
        assert by_key()["sheet_guide_width_mm"].flag == "guide-width"
        for opt in options():
            assert "_" not in opt.flag, opt.key
            assert not opt.flag.startswith("sheet"), opt.key

    def test_the_cli_really_has_every_one(self) -> None:
        """Derived flags that click never registered would fail only when somebody
        typed one, so the command's own parameters are read back. Not its `-h` text:
        that is wrapped to the terminal and a long flag is clipped, which would make
        this pass or fail on the width of the output rather than on the flag.
        """
        registered = {
            spelling
            for param in cli.commands["sheet"].params
            for spelling in param.opts + param.secondary_opts
        }
        for opt in options():
            assert f"--{opt.flag}" in registered, opt.key
            if opt.kind is OptKind.BOOL:
                assert f"--no-{opt.flag}" in registered, opt.key


class TestTheTwoSurfacesAgree:
    """The CLI and the web layer, on the same declaration."""

    def test_the_body_spells_flags_the_cli_has(self) -> None:
        body = webui.SheetBody(
            name="x",
            overrides={o.key: _sample(o) for o in options()},
        )
        argv = body.argv()
        for opt in options():
            if opt.kind is OptKind.BOOL:
                assert f"--{opt.flag}" in argv or f"--no-{opt.flag}" in argv, opt.key
            else:
                assert f"--{opt.flag}" in argv, opt.key

    def test_that_argv_actually_parses(self, library: Library) -> None:
        """The end of the parity argument: every override the browser can send, as one
        command line, accepted by the real `sheet` — which is the command the web layer
        shells out to."""
        body = webui.SheetBody(
            name="x", overrides={o.key: _sample(o) for o in options()}
        )
        out = CliRunner().invoke(
            cli,
            ["--root", str(library.root), "sheet", "x", *body.argv(), "--dry-run"],
            catch_exceptions=False,
        )
        # no cards are ready in a bare library, so it refuses on *that* and not on a
        # flag it does not know — which is what this is checking
        assert "no such option" not in out.output.lower(), out.output
        assert "no card masters to impose" in out.output

    def test_every_override_really_reaches_the_config(self) -> None:
        """`config.apply_run` is the *one* implementation both the plan and the print
        go through, so this is the whole of "the override took effect"."""
        for opt in options():
            value = _sample(opt)
            cfg = Config()
            config.apply_run(cfg, Run.SHEET, {opt.key: value})
            assert getattr(cfg, opt.key) != getattr(Config(), opt.key), opt.key
            assert getattr(cfg, opt.key) == Config.coerce(opt.key, value), opt.key

    def test_an_override_arrives_as_its_declared_type(self) -> None:
        """**A browser can only send text**, and so can argv — an `<input type=number>`
        hands over `"8"`, not `8`. Every numeric and boolean override was being stored
        on the config *as that string*, so the field's declared type was a lie and the
        arithmetic downstream was silently string arithmetic: `sheet_cols = "4"` makes
        `cols * rows` the string `"444"`, and the page count died with a `TypeError` at
        imposition rather than being refused at the boundary. It hid because nothing
        looked at the value until the PDF was being written."""
        for opt in options():
            if opt.kind not in {OptKind.INT, OptKind.FLOAT, OptKind.BOOL}:
                continue
            cfg = Config()
            # as a string, which is the only thing an HTML control or argv can send
            config.apply_run(cfg, Run.SHEET, {opt.key: str(_sample(opt)).lower()})
            got = getattr(cfg, opt.key)
            want = {OptKind.INT: int, OptKind.FLOAT: float, OptKind.BOOL: bool}[
                opt.kind
            ]
            assert isinstance(got, want), (opt.key, type(got), got)

    def test_a_string_that_is_not_a_number_is_refused(self) -> None:
        """The other half: coercing text must not mean accepting any text. And `false`
        may never become `True`, which is what a bare `bool(value)` would have done."""
        with pytest.raises(ConfigError):
            Config.coerce("sheet_margin_mm", "eight")
        with pytest.raises(ConfigError):
            Config.coerce("sheet_guides", "maybe")
        assert Config.coerce("sheet_guides", "false") is False
        assert Config.coerce("sheet_guides", "off") is False
        assert Config.coerce("sheet_guides", "true") is True

    def test_an_absent_override_changes_nothing(self) -> None:
        """The state every control starts in: the library's own setting, untouched."""
        cfg = Config()
        config.apply_run(cfg, Run.SHEET, {})
        assert cfg == Config()

    def test_an_empty_value_means_the_default(self) -> None:
        """Clearing a box is how the UI says "use the library's", so an empty string
        must not reach argv as `--margin ''`."""
        body = webui.SheetBody(name="x", overrides={"sheet_margin_mm": ""})
        assert "--margin" not in body.argv()
        cfg = Config()
        config.apply_run(cfg, Run.SHEET, body.overrides)
        assert cfg.sheet_margin_mm == Config().sheet_margin_mm


class TestWhatTheApiRefuses:
    def bad(self, **values: Any) -> str:
        return config.bad_run_value(Run.SHEET, values)

    def test_a_key_that_is_not_a_page_setting(self) -> None:
        """A setting that exists but is not a page setting — the honest 422, rather
        than an option dropped on the way to argv."""
        assert "not a page setting" in self.bad(upscayl_model="remacri-4x")

    def test_a_value_outside_its_bounds(self) -> None:
        assert "outside" in self.bad(sheet_margin_mm=999)

    def test_a_value_outside_a_closed_set(self) -> None:
        assert "not one of" in self.bad(sheet_page="a3")

    def test_a_number_that_is_not_one(self) -> None:
        assert "not a number" in self.bad(sheet_cols="lots")

    def test_every_sample_is_accepted(self) -> None:
        assert self.bad(**{o.key: _sample(o) for o in options()}) == ""


class TestNothingIsWritten:
    def test_a_run_never_edits_the_config(self, library: Library) -> None:
        """A print run is a one-off. The builder editing `proxdex.toml` would let one
        job silently redefine the library."""
        path = library.root / MARKER
        before = path.read_text(encoding="utf-8")
        CliRunner().invoke(
            cli,
            [
                "--root",
                str(library.root),
                "sheet",
                "x",
                "--margin",
                "12",
                "--front-offset-x",
                "1.5",
                "--reg-marks",
                "corners",
                "--dry-run",
            ],
            catch_exceptions=False,
        )
        assert path.read_text(encoding="utf-8") == before


class TestTheManifestRecordsTheWholeRun:
    """A reprint should be reproducible, not remembered — and it was not.

    The manifest's page settings were a hand-written **four** (page, orientation, dpi,
    bleed) out of the thirty-six a run reads, under a comment claiming they were "the
    page settings this run used". So a run that set a margin, an ink offset or a cut
    guide could not be reproduced from its own record. Same defect as the four override
    lists, same fix: derive from `Config.run_options`.
    """

    def manifest(self, root: Path) -> dict[str, Any]:
        path = next((root / "print-batches").glob("*/batch.toml"))
        return tomllib.loads(path.read_text(encoding="utf-8"))

    def impose(self, root: Path, *extra: str) -> None:
        out = CliRunner().invoke(
            cli,
            ["--root", str(root), "sheet", "run", *extra, "--no-open"],
            catch_exceptions=False,
        )
        assert "→" in out.output, out.output

    def test_every_page_setting_with_a_value_is_recorded(self, filled: Library) -> None:
        """Every setting that *has* a value — an unset optional has none, and its
        absence is the record (see the last test in this class)."""
        self.impose(filled.root)
        got = self.manifest(filled.root)["settings"]
        for opt in options():
            if opt.optional:
                continue
            assert opt.key in got, opt.key

    def test_an_optional_that_was_set_is_recorded_too(self, filled: Library) -> None:
        self.impose(filled.root, "--margin-top", "5", "--back-guide-reach", "fixed")
        got = self.manifest(filled.root)["settings"]
        assert got["sheet_margin_top_mm"] == 5.0
        assert got["sheet_back_guide_reach"] == "fixed"

    def test_the_values_are_the_run_s_and_not_the_library_s(
        self, filled: Library
    ) -> None:
        self.impose(filled.root, "--margin-left", "4", "--guide-reach", "paper")
        got = self.manifest(filled.root)["settings"]
        assert got["sheet_margin_left_mm"] == 4.0
        assert got["sheet_guide_reach"] == "paper"

    def test_marking_it_printed_does_not_lose_them(self, filled: Library) -> None:
        """`printed` **rewrites** the manifest from the parsed dict, so a key the writer
        does not know is a key it deletes — which is why the settings are one table it
        copies whole rather than named fields it would have to enumerate again."""
        self.impose(filled.root, "--margin-bottom", "12")
        before = self.manifest(filled.root)["settings"]
        out = CliRunner().invoke(
            cli, ["--root", str(filled.root), "printed", "run"], catch_exceptions=False
        )
        assert out.exit_code == 0, out.output
        assert self.manifest(filled.root)["settings"] == before

    def test_an_unset_optional_says_nothing_rather_than_null(
        self, filled: Library
    ) -> None:
        """TOML has no null, and "unset" is not a value to record — the absence is the
        record, exactly as it is in `proxdex.toml`."""
        self.impose(filled.root)
        got = self.manifest(filled.root)["settings"]
        assert "sheet_margin_top_mm" not in got
        assert "sheet_back_guide_style" not in got


@pytest.fixture
def filled(library: Library) -> Library:
    """A library with one printable card, so `sheet` really writes a manifest."""
    folder = library.root / "cards" / "base1-base" / "base1-1_card"
    folder.mkdir(parents=True)
    (folder / ".game").write_text("pokemon\n", encoding="utf-8", newline="\n")
    Image.new("RGB", (200, 280), (90, 90, 90)).save(folder / "base1-1_4_edited.png")
    return library


def _sample(opt: Any) -> Any:
    """A legal value for one option that is **never** its default.

    Guaranteed-different on purpose: with a value that happens to equal the default,
    "the override was applied" and "nothing happened at all" are the same assertion,
    and this test's whole job is to tell them apart. (The first version computed a
    number from the bounds and handed `sheet_cols` its own default of 3.)
    """
    if opt.kind is OptKind.CHOICE:
        others = [c for c in opt.choices if c != opt.default_text]
        assert others, opt.key  # a one-value closed set could not be overridden
        return others[0]
    if opt.kind is OptKind.BOOL:
        return opt.default_text != "on"
    if opt.kind is OptKind.TEXT:
        return "#123456" if "color" in opt.key else "somewhere/back.png"
    default = float(opt.default_text or 0)
    if opt.kind is OptKind.INT:
        low = int(opt.low)
        return low if low != int(default) else low + 1
    step = (opt.high - opt.low) / 8
    return round(opt.low + step, 4) if opt.low + step != default else default + step


@pytest.fixture
def library(tmp_path: Path) -> Library:
    (tmp_path / MARKER).write_text('[library]\ngame = "pokemon"\n', encoding="utf-8")
    (tmp_path / "cards").mkdir()
    return Library(tmp_path)
