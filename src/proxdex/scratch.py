"""Temporary files, created the one way that works on every platform.

``tempfile.mkstemp`` returns ``(fd, path)`` — an *open* file descriptor and the
name. Reaching for ``[1]`` and throwing the descriptor away looks like it works
and does not:

* the descriptor is never closed, so a long run leaks one per file. A duplex sheet
  of 500 cards took two per card, which is 2000 descriptors against a default soft
  limit of 1024 on Linux and 256 on macOS.
* on **Windows** it is worse than a leak. A file with an open handle cannot be
  deleted, so the ``unlink`` in the ``finally`` that was meant to clean up raises
  ``PermissionError`` instead — and ``shutil.rmtree`` over a directory of them
  fails the same way. Every temp file proxdex made would have stayed on disk, and
  the command that made it would have failed after doing its work.

So: one function, which closes the descriptor and hands back only the path. proxdex
wants the *name* — the file is then written by Pillow, or by ``write_bytes``, or by
cardbleed — and never the handle.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def file(suffix: str = ".png", folder: Path | None = None) -> Path:
    """An empty temp file's path, with no handle left open on it.

    ``folder`` keeps a group of them together so one ``rmtree`` clears the lot.
    The caller owns the file and is responsible for removing it.
    """
    fd, name = tempfile.mkstemp(suffix=suffix, dir=folder)
    os.close(fd)  # the whole point: proxdex wants the name, never the handle
    return Path(name)
