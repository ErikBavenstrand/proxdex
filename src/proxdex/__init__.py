"""proxdex — organize and drive a Pokémon proxy-card making pipeline.

proxdex is the librarian around the printing tools (``cardbleed`` for border
extension, ``paperloom`` for sheet layout). It keeps every card's assets in a
predictable place, tracks which pipeline stage each card has reached, and
records what has actually been printed.

Pipeline stages (per card):

===  =========  ===================================================
 #   stage      contents
===  =========  ===================================================
 1   original   source scan, downloaded from scrydex
 2   upscaled   Upscayl output
 3   edited     graded: saturation / contrast / levels (uniform look)
 4   print      border-corrected + cardbleed, ready for a print sheet
===  =========  ===================================================

Layout on disk::

    <root>/cards/<setid>-<slug>/<id>_<name>/<id>_<n>_<stage>.png
    <root>/print-batches/<date>_<name>/{fronts.pdf, batch.toml}
    <root>/INDEX.md          (generated)
    <root>/proxdex.toml      (config + library marker)
"""

from ._version import __version__
from .cli import cli
from .config import Config
from .errors import FileError, LibraryError, ProxdexError
from .library import Card, Library, Stage

__all__ = [
    "Card",
    "Config",
    "FileError",
    "Library",
    "LibraryError",
    "ProxdexError",
    "Stage",
    "__version__",
    "cli",
]
