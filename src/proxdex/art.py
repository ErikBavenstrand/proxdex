"""Provider pictures, downscaled once and kept on disk.

Browsing was slow, and it was **never the JSON**. Measured on a real index: the
set list answers in 9ms warm, and then the page pulls **174 logo PNGs at ~139 KB
each — 24.7 MB** into a slot 2.25rem tall, and one 60-card page pulls **45 MB** of
full-size scans into tiles 190px wide. Every byte of that crossed the network
again on the next visit, because a provider CDN's caching headers are its
business and a browser that has turned a page has thrown the row away.

So art asked for by a *screen* comes through here instead: fetched once,
resampled to the size it will actually be drawn at, written into
:func:`proxdex.net.cache_dir` and served with an immutable URL. The saving is
two orders of magnitude and it compounds — the second visit does no network at
all.

Three things this is deliberately not:

* **Not a general proxy.** The host must be one a provider actually serves art
  from (:func:`hosts`), so this cannot be pointed at anything else — an open
  fetcher is a hole even on a machine only you can reach.
* **Not an arbitrary resizer.** :class:`Size` is a closed set of the places
  proxdex draws provider art, so the cache holds one file per picture per *use*
  rather than one per pixel width somebody typed.
* **Not in the library.** This is a cache, in the cache directory, emptied by
  ``proxdex where --clear-cache`` like every other cached response.

The vector case passes through untouched: Scryfall's set symbols are ~2 KB of
SVG, already the smallest they will ever be, and rasterizing them would cost
quality to save nothing.
"""

from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import TYPE_CHECKING, Final

from PIL import Image

from proxdex import net
from proxdex.errors import FileError

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable
    from pathlib import Path

    from proxdex.config import Config

#: Hosts the two providers serve pictures from. Kept explicit rather than
#: inferred from a response, because the point is to refuse everything else.
PROVIDER_HOSTS: Final = frozenset(
    {
        "images.pokemontcg.io",  # Pokémon set logos and symbols
        "images.scrydex.com",  # Pokémon card scans
        "cards.scryfall.io",  # MTG card scans (and the card back)
        "svgs.scryfall.io",  # MTG set symbols
    }
)


class Size(StrEnum):
    """Where proxdex draws a provider picture, which is what fixes its size.

    A closed set for the reason every closed set here is one: the alternative is
    a width in the URL, which is untrusted input that becomes a resample and a
    cache file. The boxes are the drawn size at 2x, so a retina screen gets real
    pixels and nothing gets more than it can show.
    """

    #: a set's wordmark on an index tile or a set header
    LOGO = "logo"
    #: a set's symbol, which is square and much smaller
    SYMBOL = "symbol"
    #: a card's picture in a search or browse result
    CARD = "card"

    @property
    def box(self) -> tuple[int, int]:
        return _BOXES[self]


_BOXES: Final[dict[Size, tuple[int, int]]] = {
    # the widest logo slot is 7rem x 2.5rem
    Size.LOGO: (224, 96),
    # a symbol is 2.25rem square
    Size.SYMBOL: (96, 96),
    # a result tile is ~190px wide, and a hit's own `full` link is what offers
    # the provider's original — this only has to be good enough to pick by. A cap,
    # never a target: where the provider publishes a small scan of its own
    # (`Config.scrydex_thumb_url`, 245px) that arrives under the box and is passed
    # through rather than enlarged, which is the point of asking for it.
    Size.CARD: (400, 560),
}

#: WebP because these are photographs and logos with transparent corners: it
#: keeps the alpha a PNG would and costs a fraction of the bytes. Measured on the
#: real set index, a 139 KB logo lands at ~6 KB.
_QUALITY: Final = 82
_RASTER: Final = ("image/webp", ".webp")
_VECTOR: Final = ("image/svg+xml", ".svg")

#: How many art reads may be in flight at once when warming. Six is what a
#: browser allows itself per origin, and there is no reason to be greedier than
#: the thing whose job this is doing.
_POOL_SIZE: Final = 6
_pool: ThreadPoolExecutor | None = None
#: one lock per picture, so a warm and a browser request share the single fetch
_locks: Final[dict[str, Lock]] = {}
_locks_lock: Final = Lock()


@dataclass(frozen=True, slots=True)
class Art:
    """One picture, ready to serve."""

    body: bytes
    media_type: str

    @property
    def etag(self) -> str:
        return f'"{hashlib.blake2b(self.body, digest_size=8).hexdigest()}"'


def hosts(cfg: Config) -> frozenset[str]:
    """The hosts art may be read from — the providers', plus whatever this
    library's own configured card URL points at.

    ``[library] scrydex_url`` is a setting, so a user who repoints it at another
    mirror must not lose the cache with it.
    """
    return PROVIDER_HOSTS | {_host(cfg.scrydex_url)}


def load(url: str, size: Size, cfg: Config) -> Art:
    """The picture at ``url`` at ``size``, from disk if it has been asked for.

    Raises :class:`~proxdex.errors.FileError` for a host that is not a
    provider's, and :class:`~proxdex.net.NetworkError` when the fetch itself
    could not be made at all.
    """
    if _host(url) not in hosts(cfg):
        raise FileError(f"not a card-provider picture host: {_host(url) or url}")
    if (held := _read(url, size)) is not None:
        return held
    # One fetch per picture, not one per asker. Warming and the browser's own
    # request arrive at the same art within milliseconds of each other, so
    # without this the first screen fetches everything visible twice.
    with _lock_for(url, size):
        if (held := _read(url, size)) is not None:
            return held
        return _store(url, size)


def warm(urls: Iterable[str], size: Size, cfg: Config) -> int:
    """Fetch any of ``urls`` not already held, in the background; returns how
    many were queued.

    A set index is 174 pictures and a browser will ask for six at a time, so the
    first scroll would spend seconds on art the server could already have been
    fetching while the page was still being drawn. Warming is best-effort by
    definition: nothing waits on it and a failure just means the browser's own
    request does the work.
    """
    want = [u for u in urls if u and _host(u) in hosts(cfg) and not _cached(u, size)]
    if want:
        pool = _executor()
        for url in want:
            pool.submit(_quietly, url, size)
    return len(want)


def cached(size: Size | None = None) -> int:
    """How many art files are held, for ``proxdex where``'s cache line."""
    pattern = f"art/{size}-*" if size else "art/*"
    return sum(1 for _ in net.cache_dir().glob(pattern))


# ------------------------------------------------------------------ internals ---
def _read(url: str, size: Size) -> Art | None:
    for media, suffix in (_RASTER, _VECTOR):
        try:
            return Art(_path(url, size, suffix).read_bytes(), media)
        except OSError:
            continue
    return None


def _lock_for(url: str, size: Size) -> Lock:
    key = f"{size}-{url}"
    with _locks_lock:
        return _locks.setdefault(key, Lock())


def _store(url: str, size: Size) -> Art:
    """Fetch, shrink and keep. A body that will not open as an image is served
    through verbatim rather than replaced with nothing: it is a picture the
    provider chose to send, and the browser is better at guessing than this is."""
    reply = net.get(url, accept="image/*", interval=net.CDN_INTERVAL)
    if not reply.ok:
        raise FileError(f"{reply.status} for {url}")
    art = _shrink(reply.body, size)
    suffix = _VECTOR[1] if art.media_type == _VECTOR[0] else _RASTER[1]
    _write(_path(url, size, suffix), art.body)
    return art


def _shrink(body: bytes, size: Size) -> Art:
    if _is_svg(body):
        return Art(body, _VECTOR[0])
    with Image.open(io.BytesIO(body)) as im:
        im.thumbnail(size.box)
        buf = io.BytesIO()
        # WebP holds alpha, so a die-cut corner or a logo's transparency survives
        im.save(buf, "WEBP", quality=_QUALITY)
    return Art(buf.getvalue(), _RASTER[0])


def _is_svg(body: bytes) -> bool:
    head = body[:512].lstrip()
    return head.startswith((b"<svg", b"<?xml"))


def _path(url: str, size: Size, suffix: str) -> Path:
    key = hashlib.sha256(url.encode()).hexdigest()[:32]
    return net.cache_dir() / "art" / f"{size}-{key}{suffix}"


def _cached(url: str, size: Size) -> bool:
    return any(_path(url, size, suffix).exists() for _, suffix in (_RASTER, _VECTOR))


def _write(path: Path, body: bytes) -> None:
    """Best-effort, and atomic: a cache that cannot be written must never fail a
    screen, and a torn file would be served as a broken picture forever."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(body)
        tmp.replace(path)
    except OSError:
        return


def _quietly(url: str, size: Size) -> None:
    try:
        with _lock_for(url, size):
            if _read(url, size) is None:
                _store(url, size)
    except (FileError, OSError, net.NetworkError, ValueError):
        return


def _executor() -> ThreadPoolExecutor:
    global _pool  # noqa: PLW0603 - one pool per process, made on first use
    if _pool is None:
        _pool = ThreadPoolExecutor(_POOL_SIZE, thread_name_prefix="art")
    return _pool


def _host(url: str) -> str:
    return url.split("/")[2] if "//" in url else ""
