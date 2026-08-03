"""The provider-art cache: what it refuses, what it keeps, and what survives it.

Earns a file for three reasons, none of them about speed:

1. **It is a fetcher that takes a URL.** The host check is the only thing between
   "downscale a set logo" and "GET whatever you like from a process running on
   your machine", so it is asserted rather than trusted — including that a
   reconfigured ``scrydex_url`` extends the list and a lookalike host does not.
2. **Alpha must survive.** proxdex flattens a card's transparent corner to the
   card's own border colour when it *files* one (``tests/test_flatten.py``), but a
   logo drawn on a screen has to keep its transparency or it gets a grey box
   behind every set name. A format change that quietly dropped it would look fine
   in every test that only measured bytes.
3. **The disk cache is keyed by URL *and* size**, so the wrong size must not be
   served for the right picture — invisible on screen, since both are the same
   image and CSS scales either.

Nothing here reaches the network: the fetch is stubbed, which is also how "a
second ask does not fetch again" can be asserted at all.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from proxdex import art, net
from proxdex.art import Size
from proxdex.config import Config
from proxdex.errors import FileError

#: a Pokémon set logo's shape: wide, and transparent around the wordmark
LOGO_URL = "https://images.pokemontcg.io/base1/logo.png"
CARD_URL = "https://images.scrydex.com/pokemon/base1-4/large"
SVG_URL = "https://svgs.scryfall.io/sets/dft.svg"
SVG_BODY = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"></svg>'


def png(w: int, h: int, *, alpha: bool = True) -> bytes:
    im = Image.new(
        "RGBA" if alpha else "RGB", (w, h), (200, 30, 90, 0 if alpha else 255)
    )
    # something opaque in the middle, so a transparency check has both to find
    im.paste(
        (250, 220, 40, 255) if alpha else (250, 220, 40),
        (w // 4, h // 4, w // 2, h // 2),
    )
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the cache at a temp dir — a real one is never touched."""
    monkeypatch.setenv("PROXDEX_CACHE", str(tmp_path / "cache"))
    return tmp_path / "cache" / "art"


@pytest.fixture(autouse=True)
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Stub the transport, recording every URL it was actually asked for."""
    asked: dict[str, list[str]] = {"urls": []}
    bodies = {LOGO_URL: png(1000, 300), CARD_URL: png(734, 1024), SVG_URL: SVG_BODY}

    def fake_get(url: str, **_: object) -> net.Reply:
        asked["urls"].append(url)
        if url not in bodies:
            return net.Reply(404, b"")
        return net.Reply(200, bodies[url])

    monkeypatch.setattr(net, "get", fake_get)
    return asked


class TestWhatItWillFetchFrom:
    """An open fetcher is a hole even on a machine only you can reach."""

    def test_the_providers_own_hosts_are_allowed(self) -> None:
        allowed = art.hosts(Config())
        assert "images.pokemontcg.io" in allowed
        assert "cards.scryfall.io" in allowed
        assert "svgs.scryfall.io" in allowed

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/logo.png",
            "http://localhost:8000/secret",
            "http://127.0.0.1/admin",
            "file:///etc/passwd",
            # a lookalike: the allowed host as a *prefix* of another domain, which a
            # `startswith` or an `in` check would have let through
            "https://images.pokemontcg.io.evil.test/logo.png",
            # ...and as a path segment on someone else's host
            "https://evil.test/images.pokemontcg.io/logo.png",
            "",
        ],
    )
    def test_everything_else_is_refused(
        self, url: str, served: dict[str, list[str]]
    ) -> None:
        with pytest.raises(FileError):
            art.load(url, Size.LOGO, Config())
        assert served["urls"] == []  # refused before any request was made

    def test_a_reconfigured_card_url_extends_the_list(self) -> None:
        """`scrydex_url` is a setting, so a library pointed at a mirror must not
        lose its art cache with it."""
        cfg = Config(scrydex_url="https://mirror.test/pokemon/{id}/large")
        assert "mirror.test" in art.hosts(cfg)
        assert "mirror.test" not in art.hosts(Config())

    def test_warming_silently_skips_a_host_it_may_not_read(self) -> None:
        """Warming is best-effort and takes a whole page's URLs at once, so one bad
        row must not cost the other 59 — it is dropped, not raised."""
        queued = art.warm([LOGO_URL, "https://example.com/x.png"], Size.LOGO, Config())
        assert queued == 1


class TestWhatItKeeps:
    def test_a_raster_is_shrunk_to_the_box_it_is_drawn_in(self) -> None:
        got = art.load(LOGO_URL, Size.LOGO, Config())
        with Image.open(io.BytesIO(got.body)) as im:
            assert im.size[0] <= Size.LOGO.box[0]
            assert im.size[1] <= Size.LOGO.box[1]
        assert got.media_type == "image/webp"

    def test_transparency_survives(self) -> None:
        """A logo is transparent around its wordmark. Re-encoded to a format
        without alpha it would sit in a coloured box on every set tile — and the
        bytes would look perfectly healthy."""
        got = art.load(LOGO_URL, Size.LOGO, Config())
        with Image.open(io.BytesIO(got.body)) as im:
            assert im.mode == "RGBA"
            assert im.getchannel("A").getextrema()[0] == 0  # still fully clear

    def test_it_is_smaller_than_what_it_replaced(self) -> None:
        """The whole reason this exists. A 1000px logo drawn 2.25rem tall was the
        measured cost of the set index (24.7 MB over 174 tiles)."""
        original = len(png(1000, 300))
        assert len(art.load(LOGO_URL, Size.LOGO, Config()).body) < original

    def test_a_vector_passes_through_untouched(self) -> None:
        """Scryfall's set symbols are ~2 KB of SVG: already the smallest they will
        be, and rasterizing them would cost sharpness to save nothing."""
        got = art.load(SVG_URL, Size.SYMBOL, Config())
        assert got.body == SVG_BODY
        assert got.media_type == "image/svg+xml"

    def test_a_non_ok_answer_is_not_cached_as_a_picture(self, cache: Path) -> None:
        with pytest.raises(FileError):
            art.load("https://images.pokemontcg.io/nope/logo.png", Size.LOGO, Config())
        assert not cache.exists() or list(cache.iterdir()) == []


class TestItOnlyFetchesOnce:
    def test_a_second_ask_comes_off_the_disk(
        self, served: dict[str, list[str]]
    ) -> None:
        first = art.load(CARD_URL, Size.CARD, Config())
        second = art.load(CARD_URL, Size.CARD, Config())
        assert served["urls"] == [CARD_URL]
        assert first.body == second.body

    def test_it_survives_a_restart(self, cache: Path) -> None:
        """It is held **on disk**, not in the process, which is what makes
        ``uvicorn --reload`` and a real restart free rather than another 60
        pictures. Nothing is memoized in memory, so the file is the whole cache."""
        got = art.load(CARD_URL, Size.CARD, Config())
        held = list(cache.iterdir())
        assert len(held) == 1
        assert held[0].read_bytes() == got.body

    def test_warming_and_asking_share_the_one_fetch(
        self, served: dict[str, list[str]]
    ) -> None:
        """A page's JSON warms its art while the browser is asking for the visible
        rows, so the two arrive at the same picture milliseconds apart."""
        art.load(CARD_URL, Size.CARD, Config())
        assert art.warm([CARD_URL], Size.CARD, Config()) == 0
        assert served["urls"] == [CARD_URL]

    def test_each_size_is_its_own_entry(self, served: dict[str, list[str]]) -> None:
        """Keyed by URL *and* size. Sharing one entry would serve a 96px symbol
        where a 400px card belongs — the same picture, so CSS would scale it and
        nothing would look wrong."""
        card = art.load(CARD_URL, Size.CARD, Config())
        logo = art.load(CARD_URL, Size.LOGO, Config())
        assert served["urls"] == [CARD_URL, CARD_URL]
        assert card.body != logo.body
        assert art.cached() == 2
        assert art.cached(Size.CARD) == 1

    def test_clearing_the_cache_takes_the_art_with_it(self) -> None:
        """`where --clear-cache` says it drops the cached responses, and art is by
        far the largest of them — a cache that cannot be emptied is a leak."""
        art.load(CARD_URL, Size.CARD, Config())
        art.load(SVG_URL, Size.SYMBOL, Config())
        assert net.clear_cache() >= 2
        assert art.cached() == 0


class TestTheSizesAreAClosedSet:
    """A width in the URL would be untrusted input that becomes a resample and a
    cache file; these are the places proxdex actually draws provider art."""

    def test_every_size_has_a_box(self) -> None:
        assert all(min(s.box) > 0 for s in Size)

    def test_a_card_is_the_largest(self) -> None:
        """It is the only one you look *at* rather than glance at; a logo and a
        symbol are chrome beside a name."""
        assert Size.CARD.box[0] == max(s.box[0] for s in Size)
