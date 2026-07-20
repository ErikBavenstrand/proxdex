"""proxdex — organize and print proxy cards.

proxdex is the librarian for a proxy-making pipeline. It keeps every card's
assets in a predictable place (keyed by TCG id), tracks which stage each card
has reached, and records what has actually been printed. It uses ``cardbleed``
(border/bleed) and Upscayl (upscaling), and imposes the print sheet itself.

Each card's stored file is the actual trim-size card — no bleed. Cut bleed and
medium colour-correction are applied at print (sheet) time, outside the trim.

Pipeline stages (per card):

===  ==========  ==================================================
 #   stage       contents
===  ==========  ==================================================
 1   original    source scan, downloaded from scrydex
 2   bordered    thin frame expanded to trim proportions (optional)
 3   upscaled    Upscayl output, after any border fix
 4   edited      graded (uniform look) — the trim-size master
===  ==========  ==================================================

Layout on disk::

    <root>/cards/<setid>-<slug>/<id>_<name>/<id>_<n>_<stage>.png
    <root>/back.png          (optional shared card back)
    <root>/print-batches/<date>_<name>/{<faces>.pdf, batch.toml}
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
