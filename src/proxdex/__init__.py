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

A card has one or two printable *faces* (MTG's transform cards). Face 0 is the
front and keeps the plain filenames; face 1 carries an ``_f2`` suffix and has its
own pipeline state, because a back face is a different picture.

What a card *is* — one side, two, half of a meld pair, oversized, borderless — is
read from the provider at fetch and kept in marker files beside the images, so it
never has to be asked for twice. A meld pair is three ordinary cards plus a
recorded relation, not one card with three sides.

Layout on disk::

    <root>/cards/<setid>-<slug>/<id>_<name>/<id>_<n>_<stage>[_f2].png
    <root>/back-<game>.png   (optional shared card back, per game)
    <root>/print-batches/<date>_<name>/{<faces>.pdf, batch.toml}
    <root>/INDEX.md          (generated)
    <root>/proxdex.toml      (config + library marker)
"""

from proxdex._version import __version__
from proxdex.cli import cli
from proxdex.config import Config
from proxdex.errors import FileError, LibraryError, ProxdexError
from proxdex.library import Card, Library, Stage

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
