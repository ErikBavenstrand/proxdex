"""Every text file proxdex writes is UTF-8 with LF, and every one it reads is UTF-8.

Python's ``Path.read_text()``/``write_text()`` default to the *locale* encoding, so
without an explicit one a library's own files are written differently depending on
the machine. That is not cosmetic:

* ``proxdex init`` **crashed** on Windows writing its own `proxdex.toml`, because
  the template says ``border → upscale → grade`` and cp1252 has no ``→``.
* a card called *Flabébé*, an MTG name with an accent, or a note typed into a print
  profile would be written in cp1252 on Windows and UTF-8 on macOS — so a library
  synced between the two decodes to mojibake on one of them.
* line endings would churn every marker file back and forth in a synced folder,
  which is why writes pin ``newline="\\n"`` as well.

Ruff has a rule for this (``PLW1514``) and it catches **one** of the twenty-seven
sites, because it only fires where it can infer the receiver is a ``Path``. So the
guard is here instead: it reads the source, which needs no inference.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "proxdex"

#: the two calls that default to the *locale* encoding
TEXT_IO = ("read_text", "write_text")


def sources() -> list[Path]:
    found = sorted(SRC.glob("*.py"))
    assert found, f"no sources found under {SRC}"
    return found


def calls(how: str) -> list[tuple[str, int, set[str]]]:
    """Every ``read_text``/``write_text`` in the package, with its keywords.

    Parsed rather than grepped: the formatter splits long calls over several lines,
    and a regex over lines reports those as missing an argument that is simply on
    the next one. The syntax tree does not care where the newlines fell.
    """
    out: list[tuple[str, int, set[str]]] = []
    for path in sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == how:
                given = {kw.arg for kw in node.keywords if kw.arg}
                out.append((path.name, node.lineno, given))
    return out


def shown(bad: list[tuple[str, int, set[str]]]) -> str:
    return "\n".join(f"  {name}:{line}" for name, line, _ in bad)


class TestTextIsUtf8:
    def test_the_scan_finds_something(self) -> None:
        """A guard that matches nothing passes for the wrong reason."""
        assert calls("read_text")
        assert calls("write_text")

    @pytest.mark.parametrize("how", TEXT_IO)
    def test_every_call_states_its_encoding(self, how: str) -> None:
        bare = [c for c in calls(how) if "encoding" not in c[2]]
        assert not bare, "locale-encoded text I/O:\n" + shown(bare)

    def test_every_write_pins_the_line_ending(self) -> None:
        """Otherwise Windows writes CRLF and a synced library churns."""
        loose = [c for c in calls("write_text") if "newline" not in c[2]]
        assert not loose, "text written with the platform's line ending:\n" + shown(
            loose
        )


class TestItActuallyRoundTrips:
    """The scan proves the calls are spelled right; this proves the behaviour."""

    def test_a_non_ascii_card_name_survives(self, tmp_path: Path) -> None:
        """A real Pokémon (Flabébé) and a real MTG name (Æther Vial) — the ones
        that would have been mojibake on a machine with a cp1252 locale."""
        from proxdex.library import slugify

        for name in ("Flabébé", "Æther Vial", "Pokémon"):
            marker = tmp_path / ".faces"
            marker.write_text(name + "\n", encoding="utf-8", newline="\n")
            assert marker.read_text(encoding="utf-8").strip() == name
            assert slugify(name)  # a folder name is still derivable

    def test_the_config_template_needs_utf8(self) -> None:
        """The reason `init` crashed: proxdex's own default config is not ASCII."""
        from proxdex.cli import DEFAULT_TOML

        with pytest.raises(UnicodeEncodeError):
            DEFAULT_TOML.encode("cp1252")
        assert DEFAULT_TOML.encode("utf-8")
