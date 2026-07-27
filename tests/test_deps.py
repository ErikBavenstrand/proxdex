"""Everything the CLI imports is something a plain ``pip install proxdex`` has.

``tomlkit`` was declared under the ``[ui]`` extra and imported by ``cli.py`` — by
``sheet`` (which writes the batch manifest through it, so a note or a printer name
containing a quote cannot corrupt the record) and by ``config set``. So a plain
install shipped a CLI whose ``sheet`` command wrote the PDF and *then* died with
``ModuleNotFoundError``. Two releases, green on six CI jobs across three
platforms, because everything there runs under ``uv run`` — and the project
environment has the dev group, which pulls the ``[ui]`` extra in.

That is the gap this file closes, and the reason it reads the *declared*
dependencies rather than asking whether an import works: in the environment these
tests run in, every import works.

``proxdex ui`` is the one thing allowed to need the extra, and it is allowed
because it asks first: its imports sit inside ``try/except ModuleNotFoundError``
with an install hint. That, not a list of blessed names, is the rule here — a
module either comes with proxdex or is imported somewhere that copes with its
absence.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "proxdex"

#: import name -> the distribution that provides it, where they differ
PROVIDES = {
    "PIL": "pillow",
    "rich_click": "rich-click",
    "rich": "rich-click",  # rich-click's own dependency, and it re-exports it
}

#: `webui.py` is only ever imported *by* the guarded block in `cli.ui`, so it may
#: use the extra's packages freely — importing it at all already required them.
EXTRA_ONLY = {"webui.py"}


def declared() -> set[str]:
    """The module names a bare ``pip install proxdex`` guarantees."""
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dists = set()
    for spec in meta["project"]["dependencies"]:
        name = spec.split(">")[0].split("=")[0].split("[")[0].strip()
        dists.add(name.lower())
    mods = {d.replace("-", "_") for d in dists}
    mods |= {mod for mod, dist in PROVIDES.items() if dist.lower() in dists}
    return mods


def guarded(tree: ast.Module) -> set[int]:
    """Line numbers inside a ``try`` that handles a missing module."""
    safe: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches = any(
            isinstance(h.type, ast.Name)
            and h.type.id in {"ModuleNotFoundError", "ImportError"}
            for h in node.handlers
        )
        if catches:
            for stmt in node.body:
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.stmt | ast.expr):
                        safe.add(sub.lineno)
    return safe


def imports() -> list[tuple[str, str, int, bool]]:
    """``(file, module, line, is_guarded)`` for every non-stdlib import."""
    local = {p.stem for p in SRC.glob("*.py")} | {"proxdex"}
    out: list[tuple[str, str, int, bool]] = []
    for path in sorted(SRC.glob("*.py")):
        if path.name in EXTRA_ONLY:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        safe = guarded(tree)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            line = node.lineno
            for name in names:
                if name in sys.stdlib_module_names or name in local:
                    continue
                out.append((path.name, name, line, line in safe))
    return out


class TestNothingIsImportedThatIsNotShipped:
    def test_the_scan_finds_something(self) -> None:
        """A guard that matches nothing passes for the wrong reason."""
        found = {name for _f, name, _l, _g in imports()}
        assert {"PIL", "numpy", "cardbleed"} <= found, found

    def test_every_unguarded_import_is_a_declared_dependency(self) -> None:
        have = declared()
        stray = [
            f"  {f}:{line} imports {name!r}"
            for f, name, line, is_guarded in imports()
            if not is_guarded and name not in have
        ]
        assert not stray, (
            "imported without being a dependency, and without coping if it is "
            "missing:\n" + "\n".join(stray) + f"\n\ndeclared: {sorted(have)}"
        )

    def test_tomlkit_in_particular(self) -> None:
        """The one that shipped broken, named so a re-move is loud."""
        assert "tomlkit" in declared()

    @pytest.mark.parametrize("module", ["fastapi", "uvicorn", "pydantic"])
    def test_the_ui_only_packages_stay_out_of_the_core(self, module: str) -> None:
        """They are genuinely optional; the extra is not decoration."""
        assert module not in declared()


class TestTheUiCommandCopes:
    def test_it_asks_before_it_imports(self) -> None:
        """`proxdex ui` is the only command allowed to need the extra, and only
        because a missing package there is an install hint rather than a
        traceback."""
        tree = ast.parse((SRC / "cli.py").read_text(encoding="utf-8"))
        safe = guarded(tree)
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            and any(a.name.split(".")[0] == "uvicorn" for a in node.names)
            and node.lineno not in safe
        ]
        assert not offenders, f"unguarded uvicorn import at {offenders}"

    def test_the_hint_names_the_extra(self) -> None:
        """And it has to survive rich's markup parser — see test_output.py."""
        source = (SRC / "cli.py").read_text(encoding="utf-8")
        assert "proxdex[ui]" in source
