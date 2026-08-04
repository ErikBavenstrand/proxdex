"""What a running command says about itself, and to whom.

This earns a file because the whole point of :mod:`proxdex.progress` is a report
that reaches a *different process* — so every way it can go wrong is invisible
where it happens. A sink that is silently on would put a file in a terminal
user's way; a sink that is silently off would leave the web UI with the spinner
this replaced; and a total that is *guessed* rather than counted is the failure
this project names everywhere else (a plausible number presented as a
measurement), so "unknown stays unknown" is asserted rather than assumed.

The reader's totality is the other half: it is called on a file another process
is writing, so a half-written, missing or malformed one has to answer ``None``
rather than raise into whichever screen asked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proxdex import progress
from proxdex.progress import UNKNOWN, Report, Sink


class TestSilentUnlessAsked:
    """A person at a terminal must not get a progress file they never asked for."""

    def test_no_env_and_no_path_writes_nothing(self, tmp_path: Path) -> None:
        sink = Sink()
        assert not sink.on
        sink.start("Fetching", 5)
        sink.advance("base1-4")
        sink.finish()
        assert list(tmp_path.iterdir()) == []

    def test_the_env_var_turns_it_on(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "p.json"
        monkeypatch.setenv(progress.ENV, str(target))
        sink = Sink()
        assert sink.on
        sink.start("Fetching", 2)
        assert progress.read(target) is not None

    def test_an_explicit_path_beats_the_environment(self, tmp_path: Path) -> None:
        target = tmp_path / "explicit.json"
        Sink(target).start("Imposing", 3)
        assert (progress.read(target) or Report()).verb == "Imposing"


class TestWhatItReports:
    def test_a_count_travels(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        sink = Sink(target)
        sink.start("Fetching", 3)
        sink.advance("base1-1")
        sink.advance("base1-2")
        got = progress.read(target)
        assert got is not None
        assert (got.verb, got.done, got.total, got.note) == (
            "Fetching",
            2,
            3,
            "base1-2",
        )

    def test_which_item_can_be_said_before_it_is_finished(self, tmp_path: Path) -> None:
        """`at` names the card being worked on; only `advance` counts it done —
        the difference is the whole of "downloading base1-4" versus "4 done"."""
        target = tmp_path / "p.json"
        sink = Sink(target)
        sink.start("Fetching", 3)
        sink.at("base1-1")
        got = progress.read(target)
        assert got is not None
        assert (got.done, got.note) == (0, "base1-1")

    def test_finishing_removes_the_file(self, tmp_path: Path) -> None:
        """Not left reading 100%: a reader must be able to tell "done" from "the
        last thing I heard", and an absent report is the only unambiguous way."""
        target = tmp_path / "p.json"
        sink = Sink(target)
        sink.start("Fetching", 1)
        sink.advance("base1-1")
        sink.finish()
        assert not target.exists()
        assert progress.read(target) is None

    def test_writing_after_finish_is_silent(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        sink = Sink(target)
        sink.start("Fetching", 1)
        sink.finish()
        sink.advance("late")
        assert not target.exists()


class TestAnUnknownTotalStaysUnknown:
    """The one thing this must never do is invent a denominator.

    A single card through Upscayl is one item and the model reports nothing, so
    there is no fraction to be had — and a bar sitting at 90% because somebody
    guessed is the same defect as a border measured off a dark frame.
    """

    def test_the_default_total_is_unknown(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        Sink(target).start("Upscaling")
        got = progress.read(target)
        assert got is not None
        assert got.total == UNKNOWN
        assert not got.known
        assert got.fraction is None

    def test_advancing_an_unknown_total_still_yields_no_fraction(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "p.json"
        sink = Sink(target)
        sink.start("Upscaling")
        sink.advance("base1-4")
        got = progress.read(target)
        assert got is not None
        assert got.done == 1
        assert got.fraction is None

    def test_a_zero_total_is_not_a_division(self) -> None:
        """An empty batch: `known` is true (nothing said "I don't know"), and the
        fraction is still None rather than a ZeroDivisionError."""
        assert Report(total=0).fraction is None

    def test_one_item_has_a_count_but_no_position(self) -> None:
        """The bar over a single card read 0% and then vanished — a real fraction
        carrying no information, which looked exactly like a broken bar. One item is
        `known` (nothing said "I don't know") and still not `positional`, so a reader
        falls back to the sweep and the elapsed clock it uses for an unknown total.
        """
        one = Report(total=1)
        assert one.known
        assert one.fraction == 0.0
        assert not one.positional

    def test_two_items_are_worth_a_bar(self) -> None:
        assert Report(total=2).positional

    def test_an_unknown_total_is_not_positional_either(self) -> None:
        assert not Report(total=UNKNOWN).positional
        assert not Report(total=0).positional


class TestHowLongIsLeft:
    """The one estimate here that is measured rather than remembered.

    `remaining` divides the command's *own* elapsed time by the items it has really
    finished, so it is a rate this run achieved and not a median of previous ones.
    Every way it could become a guess instead is refused: before the first item, at
    a total of one, once the count is complete, and with no clock to divide by.
    """

    def test_it_is_this_run_s_own_rate(self) -> None:
        # four of ten done in 8s → 2s each → 12s for the six that are left
        got = Report(done=4, total=10, started=100.0, at=108.0)
        assert got.remaining == pytest.approx(12.0)

    def test_nothing_is_offered_before_the_first_item(self) -> None:
        """A rate over zero items is a division by zero wearing a forecast's
        clothes."""
        assert Report(done=0, total=10, started=100.0, at=108.0).remaining is None

    def test_nothing_is_offered_once_the_count_is_complete(self) -> None:
        """`sheet` reports its last page and *then* embeds the PDF, which is the
        slowest part — measured at 14 of 14 for several seconds. A "0s left" beside a
        full bar for the length of that is the spinner-that-knows-nothing this module
        replaced, only more confident. The note says what is happening instead.
        """
        assert Report(done=14, total=14, started=100.0, at=108.0).remaining is None

    def test_an_overrun_count_says_nothing_rather_than_a_negative(self) -> None:
        assert Report(done=15, total=14, started=100.0, at=108.0).remaining is None

    def test_a_single_item_gets_no_estimate(self) -> None:
        """It is not positional, so there is no rate to report either — and the one
        item it would be timing is the one that has not finished."""
        assert Report(done=0, total=1, started=100.0, at=108.0).remaining is None

    def test_a_report_from_before_the_clock_existed_says_nothing(self) -> None:
        """`started` defaults to 0.0, so an unstamped report must not report the
        whole unix epoch as the time one page took."""
        assert Report(done=4, total=10, at=108.0).remaining is None

    def test_a_clock_that_has_not_moved_says_nothing(self) -> None:
        assert Report(done=4, total=10, started=108.0, at=108.0).remaining is None

    def test_the_start_of_the_count_travels(self, tmp_path: Path) -> None:
        """It has to reach the reader, and it is the start of the *counting* — a CLI
        subprocess spends a second or two importing before it knows what it is
        counting, and a rate measured over that is slower than the work is.

        **What is asserted here is the round trip, not a forecast.** This used to end
        with ``got.remaining is not None``, which raced the clock: `start` and
        `advance` are two consecutive calls, and on Windows ``time.time()`` advances
        in ~15.6ms steps, so both landed in the same tick and ``at == started``
        exactly. `remaining` then correctly refused to divide by a zero elapsed and
        the test failed the code for being right — which is what
        `test_a_clock_that_has_not_moved_says_nothing` above *pins as correct*, one
        method up. Whether a rate is offered is a question about arithmetic and is
        answered by the `Report(...)` cases either side of this, at timestamps nobody
        has to hope for; what only a real sink can show is that `started` survives
        being written and read back.
        """
        target = tmp_path / "p.json"
        sink = Sink(target)
        sink.start("Imposing", 4)
        sink.advance("page 1")
        got = progress.read(target)
        assert got is not None
        assert got.started > 0
        assert got.at >= got.started
        assert (got.verb, got.done, got.total, got.note) == ("Imposing", 1, 4, "page 1")

    def test_a_fraction_never_exceeds_one(self) -> None:
        """A count can overrun its total — a verb that retried an item, say — and a
        bar past 100% is a rendering bug in whoever reads this."""
        assert Report(done=7, total=5).fraction == 1.0


class TestTheReaderIsTotal:
    """It reads a file another process is writing, so nothing may raise."""

    def test_a_missing_file(self, tmp_path: Path) -> None:
        assert progress.read(tmp_path / "never-written.json") is None

    def test_an_empty_file(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        target.write_text("", encoding="utf-8")
        assert progress.read(target) is None

    def test_a_half_written_file(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        target.write_text('{"verb": "Fetch", "done": 2', encoding="utf-8")
        assert progress.read(target) is None

    def test_a_directory(self, tmp_path: Path) -> None:
        (tmp_path / "dir.json").mkdir()
        assert progress.read(tmp_path / "dir.json") is None

    def test_fields_of_the_wrong_shape(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        target.write_text('{"verb": 7, "done": "lots", "total": 3}', encoding="utf-8")
        assert progress.read(target) is None

    def test_missing_fields_take_their_defaults(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        target.write_text('{"verb": "Fetching"}', encoding="utf-8")
        got = progress.read(target)
        assert got is not None
        assert (got.verb, got.done, got.total) == ("Fetching", 0, UNKNOWN)


class TestNothingBreaksTheWork:
    """A progress file that cannot be written must never fail a command — the
    report is a courtesy and the work is the point."""

    def test_an_unwritable_path_is_swallowed(self, tmp_path: Path) -> None:
        sink = Sink(tmp_path / "no-such-dir" / "p.json")
        sink.start("Fetching", 2)
        sink.advance("base1-1")
        sink.finish()  # no exception, and nothing was written

    def test_a_write_is_atomic(self, tmp_path: Path) -> None:
        """Via a temp file and `replace`, so a reader never sees half a report and
        no `.part` is left behind for it to find."""
        target = tmp_path / "p.json"
        sink = Sink(target)
        sink.start("Fetching", 2)
        sink.advance("base1-1")
        assert [p.name for p in sorted(tmp_path.iterdir())] == ["p.json"]
