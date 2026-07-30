"""A `[print]` profile setting that names nothing — reported, not raised at print.

`profiles.named`/`profiles.dangling` are one pure function pair over the config and
the profiles directory, with four consumers: `proxdex where`, `profile list`'s
marker *and* its legend, and `/api/profiles`. They earn a test for the reason
`imports.plan` does — one answer, several screens — and because the failure they
exist to name is invisible until the end of a print run: the real library carried
`[print] profile = "foil"` from the deleted built-in presets, so every `sheet` run
died with `no print profile named 'foil'` and nothing before that said so.
"""

from __future__ import annotations

from pathlib import Path

from proxdex import profiles
from proxdex.config import Config
from proxdex.profiles import PrintSetting


def _cfg(profile: str = "", back: str = "") -> Config:
    return Config(print_profile=profile, print_back_profile=back)


class TestWhatANameRefersTo:
    def test_unset_is_the_identity(self, tmp_path: Path) -> None:
        """Because that is what `active` resolves it to — so the marker is on a row."""
        assert profiles.named(tmp_path, "") == profiles.NONE

    def test_the_identity_needs_no_file(self, tmp_path: Path) -> None:
        assert profiles.named(tmp_path, profiles.NONE) == profiles.NONE

    def test_a_stored_profile_answers_to_its_name(self, tmp_path: Path) -> None:
        profiles.create(tmp_path, "matte-200")
        assert profiles.named(tmp_path, "matte-200") == "matte-200"

    def test_a_name_is_canonicalised(self, tmp_path: Path) -> None:
        """`[print] profile` is typed by hand, and `listing` spells slugs."""
        profiles.create(tmp_path, "matte-200")
        assert profiles.named(tmp_path, "Matte 200") == "matte-200"

    def test_a_profile_that_is_gone_answers_nothing(self, tmp_path: Path) -> None:
        assert profiles.named(tmp_path, "foil") is None

    def test_an_illegal_name_answers_nothing_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        """This is asked in order to *report*, so it must survive any string."""
        assert profiles.named(tmp_path, "!!!") is None


class TestWhatDangles:
    def test_nothing_configured_dangles_nothing(self, tmp_path: Path) -> None:
        assert profiles.dangling(tmp_path, _cfg()) == ()

    def test_the_identity_does_not_dangle(self, tmp_path: Path) -> None:
        assert profiles.dangling(tmp_path, _cfg(profiles.NONE)) == ()

    def test_a_stored_profile_does_not_dangle(self, tmp_path: Path) -> None:
        profiles.create(tmp_path, "matte-200")
        assert profiles.dangling(tmp_path, _cfg("matte-200", "matte-200")) == ()

    def test_a_missing_front_profile_is_named(self, tmp_path: Path) -> None:
        (gone,) = profiles.dangling(tmp_path, _cfg("foil"))
        assert gone.setting is PrintSetting.PROFILE
        assert gone.name == "foil"
        assert "[print] profile" in gone.message
        assert "foil" in gone.message

    def test_a_missing_back_profile_is_named_too(self, tmp_path: Path) -> None:
        """Its own key and its own sentence: it corrects the backs, not the fronts."""
        (gone,) = profiles.dangling(tmp_path, _cfg("none", "glossy"))
        assert gone.setting is PrintSetting.BACK_PROFILE
        assert gone.setting.prints == "backs"
        assert "[print] back_profile" in gone.message

    def test_both_are_reported(self, tmp_path: Path) -> None:
        assert len(profiles.dangling(tmp_path, _cfg("foil", "glossy"))) == 2

    def test_an_unset_back_is_not_a_fault(self, tmp_path: Path) -> None:
        """Unset means "the same medium as the fronts", which is an answer."""
        profiles.create(tmp_path, "matte-200")
        assert profiles.dangling(tmp_path, _cfg("matte-200")) == ()
