"""How far along a running command is, for a caller that is not a terminal.

Every batch verb already shows a real bar — :func:`proxdex.cli._each` counts the
items and rich draws it. None of that reaches the web UI, and for two good
reasons: rich turns its live display *off* when stdout is not a terminal, and the
UI reads that stdout as the command's log. So the browser got one full-screen
spinner for everything, whether it was filing one card (a second) or upscaling
forty (an afternoon).

This is the side channel. A command writes its count to the file named by
``$PROXDEX_PROGRESS`` if there is one, and does nothing at all if there is not —
so the terminal keeps exactly the output it had, and nothing is parsed out of
human text that a later wording change would break.

**Only a real count is ever written.** Where nothing knows the total — one card
through Upscayl is one item, and the model does not report its own progress — the
reader is told the total is unknown (:data:`UNKNOWN`) rather than handed a
fraction somebody invented. A guessed bar that sits at 90% is the same failure as
a guessed border: it looks finished.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: Names the file to write progress into. Set by whatever spawned the command;
#: absent for a person at a terminal, which is why the sink is a no-op by default.
ENV: Final = "PROXDEX_PROGRESS"

#: How many items there are, when nothing knows. A reader must show elapsed time
#: rather than a fraction — see the module docstring.
UNKNOWN: Final = -1


@dataclass(frozen=True, slots=True)
class Report:
    """The last thing a running command said about itself."""

    #: what it is doing, in the words the command already uses ("Fetching")
    verb: str = ""
    done: int = 0
    total: int = UNKNOWN
    #: which item it is on — a card id, a page number
    note: str = ""
    #: unix time it was written, so a reader can tell a stalled job from a slow one
    at: float = 0.0
    #: unix time the *counting* began. Not the same as when the process started: a
    #: CLI subprocess spends a second or two importing and reading the library
    #: before it knows what it is counting, and a rate measured over that is slower
    #: than the work really is. ``at - started`` is the time the items took.
    started: float = 0.0

    @property
    def known(self) -> bool:
        return self.total > UNKNOWN

    @property
    def fraction(self) -> float | None:
        """0..1, or ``None`` when the total is unknown — never a guess."""
        if not self.known or self.total <= 0:
            return None
        return min(1.0, self.done / self.total)

    @property
    def positional(self) -> bool:
        """Whether there is a *position* here worth drawing as a bar.

        A count of one is a real count and its fraction is a real fraction, and
        neither says anything a reader can use: the bar can only ever read 0% and
        then be gone, which is what a single card through Upscayl looked like — an
        empty bar beside a running clock. Two steps is the least that can show
        movement, so at one item a reader should fall back to whatever it does when
        the total is unknown (a sweep, an elapsed clock, an estimate labelled as
        one). The rule lives here because both readers need the same one: the CLI's
        rich bar already draws nothing below three items.
        """
        return self.known and self.total > 1

    @property
    def remaining(self) -> float | None:
        """Seconds left at the rate *this run* has actually managed, or ``None``.

        Not a typical and not a guess: the numerator is the command's own clock over
        the items it has already finished, which is why nothing is offered until at
        least one has (before that the rate is a division by zero wearing a
        forecast's clothes).

        ``None`` once the count is complete, too. The last thing an imposition does
        is embed the pages — reported as ``14 of 14 · writing the PDF`` — and a "0s
        left" beside a bar at 100% for the length of that embed is the "spinner that
        knows nothing" this module replaced, only more confident. When there is no
        item left to time, there is nothing to say.
        """
        if not self.positional or not 0 < self.done < self.total:
            return None
        elapsed = self.at - self.started
        if self.started <= 0 or elapsed <= 0:
            return None
        return elapsed / self.done * (self.total - self.done)

    def json(self) -> dict[str, object]:
        return {
            "verb": self.verb,
            "done": self.done,
            "total": self.total,
            "note": self.note,
            "at": self.at,
            "started": self.started,
        }


class Sink:
    """Where a command reports to. Silent unless ``$PROXDEX_PROGRESS`` is set.

    Writes are best-effort and atomic: a progress file that cannot be written must
    never fail the work, and a half-written one would be read as a wrong count.
    """

    def __init__(self, path: Path | None = None) -> None:
        env = os.environ.get(ENV)
        self._path = path if path is not None else (Path(env) if env else None)
        self._verb = ""
        self._done = 0
        self._total = UNKNOWN
        self._started = 0.0

    @property
    def on(self) -> bool:
        return self._path is not None

    def start(self, verb: str, total: int = UNKNOWN) -> None:
        self._verb, self._done, self._total = verb, 0, total
        self._started = time.time()
        self._write("")

    def advance(self, note: str = "") -> None:
        self._done += 1
        self._write(note)

    def at(self, note: str) -> None:
        """Say which item is being worked on, without counting it as finished."""
        self._write(note)

    def finish(self) -> None:
        """Nothing more is coming. The file is removed rather than left reading
        100%, so a reader can tell 'done' from 'the last thing I heard'."""
        path, self._path = self._path, None
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                return

    def _write(self, note: str) -> None:
        if self._path is None:
            return
        report = Report(
            self._verb, self._done, self._total, note, time.time(), self._started
        )
        try:
            tmp = self._path.with_suffix(self._path.suffix + ".part")
            tmp.write_text(json.dumps(report.json()), encoding="utf-8", newline="\n")
            tmp.replace(self._path)
        except OSError:
            return


def read(path: Path) -> Report | None:
    """The last report written to ``path``, or ``None`` — nothing yet, or done."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Report(
            verb=str(raw.get("verb", "")),
            done=int(raw.get("done", 0)),
            total=int(raw.get("total", UNKNOWN)),
            note=str(raw.get("note", "")),
            at=float(raw.get("at", 0.0)),
            started=float(raw.get("started", 0.0)),
        )
    except (OSError, ValueError, TypeError):
        return None
