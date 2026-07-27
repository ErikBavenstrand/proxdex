"""proxdex's output must degrade on a stream that cannot carry it, never crash.

The CLI prints ``✓ → · × ⤳ ⌖`` on purpose — the pipeline's state marks and the size
and progress glyphs (there is a RUF001 ignore for exactly this). A stream that
cannot encode them used to raise ``UnicodeEncodeError`` *instead of running the
command*::

    $ proxdex --help | cat        # on Windows
    UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192'

Not a Windows quirk. A redirected stream there falls back to the ANSI codepage
(cp1252) rather than the console's own UTF-8 path — and ``LC_ALL=C`` on Linux,
which is what a container or a cron job often has, does the same thing. It went
unnoticed because every developer machine and every CI runner happened to be UTF-8,
which is also why this is a test and not a note.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

import pytest

from proxdex.cli import GLYPHS, writable_output

#: encodings that cannot carry proxdex's glyphs — Windows' redirected default, and
#: the one a POSIX box gets from `LC_ALL=C`
HOSTILE = ("cp1252", "ascii")


def wrapper(encoding: str) -> io.TextIOWrapper:
    """A text stream with a real encoding, like a redirected stdout."""
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding)


def _base_env() -> dict[str, str]:
    """The parent environment minus anything that would force UTF-8 back on.

    ``PYTHONUTF8`` would mask the very thing under test; the rest has to survive
    because the interpreter needs it (``SYSTEMROOT`` on Windows, ``PATH`` for the
    venv), which is why this is a filter and not a fresh dict.
    """
    return {k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}


class TestTheGlyphList:
    def test_it_names_what_the_cli_prints(self) -> None:
        """If the list drifts from the real output, the check passes while the
        command still dies — so the marks are asserted individually."""
        for glyph in "✓→·⤳⌖":
            assert glyph in GLYPHS


class TestReconfiguring:
    @pytest.mark.parametrize("encoding", HOSTILE)
    def test_a_hostile_stream_is_made_writable(
        self, monkeypatch: pytest.MonkeyPatch, encoding: str
    ) -> None:
        out = wrapper(encoding)
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", wrapper(encoding))

        writable_output()

        out.write(GLYPHS)  # this is what used to raise
        assert out.errors == "replace"

    def test_a_utf8_stream_is_left_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nothing to fix, so nothing is touched: reconfiguring a stream that was
        already fine is a side effect for its own sake."""
        out = wrapper("utf-8")
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", wrapper("utf-8"))

        writable_output()

        assert out.encoding == "utf-8"
        assert out.errors == "strict"

    def test_a_stream_with_no_reconfigure_is_survived(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A captured or wrapped stdout may not be reconfigurable at all, and the
        CLI still has to start."""
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        writable_output()  # must not raise


class TestTheRealCommand:
    """The end of the argument: run the CLI in a subprocess with the encoding
    Windows hands a redirected stream, and require a clean exit."""

    def run(self, encoding: str, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-m", "proxdex", *args],
            capture_output=True,
            check=False,
            env={**_base_env(), "PYTHONIOENCODING": encoding},
        )

    @pytest.mark.parametrize("encoding", HOSTILE)
    def test_help_survives_it(self, encoding: str) -> None:
        done = self.run(encoding, "--help")
        assert done.returncode == 0, done.stderr[-400:].decode(errors="replace")
        assert b"Traceback" not in done.stderr

    def test_an_unrepresentable_mark_becomes_a_question_mark(self) -> None:
        """`errors=replace` is the point: a tick the terminal cannot show is a
        ``?``, and the command still does its job."""
        done = self.run("ascii", "--help")
        assert done.returncode == 0
        assert b"?" in done.stdout


class TestAnErrorSaysWhatItMeans:
    """An error message is arbitrary text, and rich reads ``[...]`` as markup and
    *removes* it. So the hint for the one genuinely optional dependency read

        install with `uv tool install "proxdex"`

    on a machine without it — with the ``[ui]`` that is the entire point of the
    sentence silently deleted. Any message naming a path with brackets, or a
    stage list, would be edited the same way.
    """

    def test_the_bracket_survives_the_console(self) -> None:
        from rich.console import Console

        from proxdex.cli import escape

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)
        text = 'install with `pip install "proxdex[ui]"`'
        console.print(f"[bold red]error:[/] {escape(text)}")
        assert "proxdex[ui]" in buf.getvalue()

    def test_unescaped_is_the_bug_being_prevented(self) -> None:
        """Naming what goes wrong, so the guard above cannot be 'simplified'."""
        from rich.console import Console

        buf = io.StringIO()
        Console(file=buf, force_terminal=False, width=200).print(
            'install with `pip install "proxdex[ui]"`'
        )
        assert "proxdex[ui]" not in buf.getvalue()
