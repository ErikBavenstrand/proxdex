"""``config prune`` edits the file a person hand-wrote, so it has to be provable.

This earns a place in a deliberately small suite because every way it can go wrong is
**invisible until much later**. It rewrites ``proxdex.toml`` — the one file in a
library that someone typed themselves, with their own comments in it — and the
failures are: a live setting quietly deleted (which reverts to a default and prints
differently), a file left unparseable (which breaks every command at once), or a
comment left behind explaining a feature that no longer exists.

That last one is the reason this is a line pass rather than a tomlkit round-trip, and
it is not cosmetic. A real library's file held ``normalize``, ``black_pct``,
``white_pct`` and ``match_border_target`` from the grade white-balance that was
deleted for turning a neutral grey into deep blue, plus ``thresh`` and two target
ratios from the border auto-detector that was deleted for reading a black border 65%
too far. Deleting the keys and keeping the prose would leave "pull each card to a
common baseline first" sitting above ``brightness``, describing nothing — the same
"looks like a setting" trap the pruning exists to remove, one level up.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from click.testing import CliRunner

from proxdex.cli import cli
from proxdex.config import Config
from proxdex.library import Library

#: the shape of the problem, taken from a real library: dead keys with their own
#: comment blocks, a section that holds nothing else, and live settings interleaved
REAL = """\
# proxdex library config — tune here, no code edits needed.

[border]
# Frame-thickness targets as a fraction of card width / height.
# 0.0 = auto: pad the thin edges up to match the sides.
target_side_ratio = 0.0
target_top_ratio  = 0.0
thresh            = 62      # RGB distance still counted as "the frame colour"

[grade]
# 1) normalize: pull each card to a common baseline first (so scans and
#    digital art match) — white-balance the frame.
normalize = true
black_pct = 0.5             # luminance percentile mapped to black
white_pct = 99.5
# Frame white-balance target. [] = use the library's own median frame colour.
match_border_target = []
# 2) look: one identical recipe on top → uniform prints.
brightness = 1.03
contrast   = 1.06

[card]
w_mm = 63.0
h_mm = 88.0
"""


def prune(root: Path, *args: str) -> str:
    result = CliRunner().invoke(
        cli, ["--root", str(root), "config", "prune", *args], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    return result.output


def read(root: Path) -> str:
    return (root / "proxdex.toml").read_text(encoding="utf-8")


class TestWhatItRemoves:
    def test_it_removes_the_dead_keys_and_keeps_every_live_one(
        self, library: Library
    ) -> None:
        (library.root / "proxdex.toml").write_text(REAL, encoding="utf-8", newline="\n")
        prune(library.root, "--yes")
        doc = tomllib.loads(read(library.root))
        # the whole point: nothing a person set is touched
        assert doc["grade"] == {"brightness": 1.03, "contrast": 1.06}
        assert doc["card"] == {"w_mm": 63.0, "h_mm": 88.0}
        # and every key that nothing reads is gone
        assert "border" not in doc
        for gone in ("normalize", "black_pct", "white_pct", "match_border_target"):
            assert gone not in doc["grade"], gone

    def test_a_pruned_key_takes_its_own_comment_with_it(self, library: Library) -> None:
        """An orphaned comment is the same trap one level up — prose describing a
        setting that is not there. The comment for a *live* key must survive, which is
        what stops this from simply stripping every comment in the file."""
        (library.root / "proxdex.toml").write_text(REAL, encoding="utf-8", newline="\n")
        prune(library.root, "--yes")
        text = read(library.root)
        for gone in ("normalize:", "white-balance", "Frame-thickness"):
            assert gone not in text, gone
        assert "# 2) look:" in text  # explains `brightness`, which is still here
        assert "# proxdex library config" in text  # the file's own heading

    def test_a_section_that_held_only_dead_keys_goes_too(
        self, library: Library
    ) -> None:
        """`[border]` existed for the auto-detector's three settings and nothing
        else. An empty table is not a setting anyone can act on."""
        (library.root / "proxdex.toml").write_text(REAL, encoding="utf-8", newline="\n")
        prune(library.root, "--yes")
        assert "[border]" not in read(library.root)
        assert "[grade]" in read(library.root)

    def test_the_result_is_still_the_config_the_library_loads(
        self, library: Library
    ) -> None:
        """The strongest check available: it parses, and `Config.load` agrees with the
        values that were left. A file this breaks breaks every command at once."""
        (library.root / "proxdex.toml").write_text(REAL, encoding="utf-8", newline="\n")
        prune(library.root, "--yes")
        cfg = Config.load(library.root)
        assert cfg.grade_brightness == 1.03
        assert cfg.grade_contrast == 1.06
        assert cfg.card_w_mm == 63.0


class TestWhatItRefusesToDo:
    def test_it_asks_first(self, library: Library) -> None:
        """It edits a file someone wrote by hand, so `--yes` is opt-in."""
        (library.root / "proxdex.toml").write_text(REAL, encoding="utf-8", newline="\n")
        result = CliRunner().invoke(
            cli,
            ["--root", str(library.root), "config", "prune"],
            input="n\n",
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "normalize" in result.output  # it names them before asking
        assert read(library.root) == REAL  # and declining changes nothing

    def test_a_file_of_only_real_settings_is_left_exactly_alone(
        self, library: Library
    ) -> None:
        before = read(library.root)
        out = prune(library.root, "--yes")
        assert "nothing to prune" in out
        assert read(library.root) == before

    def test_it_never_touches_a_key_config_actually_has(self, library: Library) -> None:
        """The safety property, stated directly: what goes is exactly the set with no
        `Config` field. A bug here silently reverts a setting to its default, and a
        default that prints differently is the kind of thing found on paper."""
        (library.root / "proxdex.toml").write_text(REAL, encoding="utf-8", newline="\n")
        before = tomllib.loads(REAL)
        prune(library.root, "--yes")
        after = tomllib.loads(read(library.root))
        for section, table in before.items():
            for key in table:
                lives = Config.field_name(section, key) is not None
                assert lives == (key in after.get(section, {})), f"[{section}] {key}"
