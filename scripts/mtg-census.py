#!/usr/bin/env -S uv run python
"""Every distinct MTG border variant that exists, and roughly what each measures.

    uv run python scripts/mtg-census.py

**A survey, not the source of the specs.** It answers "how many border variants are
there, and do they differ from each other" — which it can, because a systematic
error in the scans cancels when you compare two populations read the same way. It
cannot answer "how wide is this border", because **a scan carries its own crop**: a
scan trimmed 0.3mm inside the real cut edge reports every border 0.3mm narrow, all
the cards agree, and nothing in the image says so. No sample size touches that.

So the numbers below establish the *shape* of the answer and go into `frames.py` as
working defaults that say they are provisional. The *size* comes from calipers on a
real card — `docs/measuring-frames.md` lists which cards and how, and
`proxdex frames set` records the result.

A script rather than a test because it needs Scryfall and a few dozen image
downloads. Re-run it when a new frame generation ships.

Two halves:

**The census.** Scryfall names every printing's ``frame`` and ``border_color``, so
the complete list of border variants is a count over those two fields rather than a
guess — which is how the yellow full-art box toppers turned up (4.70mm against an
ordinary 2.45) and how "gold border" turned out *not* to be a variant at all. This
half is trustworthy: it counts metadata and measures nothing.

The two fields are separate and mean different things, which is easy to misread: a
card is ``frame: 2015`` **and** ``border_color: borderless`` at once, so "borderless"
is a border colour and never a frame generation. The residual query at the end of
:func:`census` is what proves the ``frame`` list is *closed* rather than merely long
— it counts the printings whose frame is none of the five, and that count is 0.

**The measurement.** Each populated combination is sampled and read with
:func:`proxdex.borders.detect_inset`. Three things the sampling has to exclude, each
of which produced a wrong answer first time round:

* ``is:oversized`` — a Vanguard or Planechase card is 89×127mm, so its border as a
  fraction of 63×88 is nonsense. This alone invented a "gold border" variant.
* tokens — their own frame, often no border at all.
* promos and Secret Lair — one-off treatments that describe nothing.

The sample is also taken **oldest first**, so each generation is measured on cards
actually printed in it rather than on modern retro-frame reprints. Both of those
shift a mean by ~0.1-0.2mm — which is a reason to sample carefully even for a
survey, since the whole point is comparing populations to each other.

Only edges the detector *trusted* are averaged (support ≥ 0.8). That matters: an
extended-art card's sides and an M15 card's black collector strip both read wrong,
and both say so through their support rather than quietly skewing a mean.
"""

from __future__ import annotations

import io
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from PIL import Image

from proxdex import borders, scratch

#: Scryfall asks for a descriptive agent and at most ~10 requests a second
_HEADERS = {"User-Agent": "proxdex-mtg-census/1.0", "Accept": "*/*"}
_PAUSE = 0.12

_FRAMES = ("1993", "1997", "2003", "2015", "future")
_BORDERS = ("black", "white", "silver", "gold", "borderless", "yellow")
#: ordinary cards of ordinary sets — see the module docstring for why each clause
_ORDINARY = (
    "-is:oversized -t:token -is:promo -is:digital (st:core or st:expansion)"
)
#: how confident an edge's scan lines have to be before its number is worth a mean
_TRUSTED = 0.8
_SAMPLES = 20


def _get(url: str) -> bytes:
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers=_HEADERS)  # noqa: S310
            with urllib.request.urlopen(request, timeout=30) as reply:  # noqa: S310
                return reply.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404 or attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _search(query: str, page: int = 1) -> dict[str, Any]:
    url = "https://api.scryfall.com/cards/search?" + urllib.parse.urlencode(
        {
            "q": query,
            "unique": "prints",
            # oldest first, and this matters: a `frame:1997` card in a 2025 set is a
            # retro-frame *reprint* at modern trim, not a 1997 printing. Sampling
            # newest-first measured those and widened every old generation's spread.
            "order": "released",
            "dir": "asc",
            "page": page,
        }
    )
    try:
        found: dict[str, Any] = json.loads(_get(url))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # Scryfall's "no cards matched"
            return {"total_cards": 0, "data": []}
        raise
    return found


def _spread(query: str, wanted: int) -> list[dict[str, Any]]:
    """A sample spread across the results rather than one set's first cards."""
    rows: list[dict[str, Any]] = _search(query).get("data", [])
    if not rows:
        return []
    step = max(1, len(rows) // max(wanted, 1))
    return rows[::step][:wanted]


def _measure(card: dict[str, Any]) -> tuple[list[float], list[float], bool] | None:
    """This card's per-edge border in mm, its per-edge support, and frameless."""
    url = (card.get("image_uris") or {}).get("png")
    if not url:
        return None
    image = Image.open(io.BytesIO(_get(url))).convert("RGB")
    path = scratch.file(".png")
    image.save(path)
    try:
        found = borders.detect_inset(path)
    finally:
        path.unlink(missing_ok=True)
    mm = [v * (88.0 if i % 2 == 0 else 63.0) for i, v in enumerate(found.inset)]
    return mm, list(found.support), found.frameless


def _stat(values: list[float]) -> str:
    if not values:
        return "—"
    spread = statistics.stdev(values) if len(values) > 1 else 0.0
    return (
        f"{statistics.mean(values):5.2f} ±{spread:.2f} "
        f"[{min(values):.2f}-{max(values):.2f}] n={len(values)}"
    )


def census() -> None:
    """Which (frame × border) combinations exist at all, and how many prints each."""
    print("\n=== the census: every combination that exists ===")
    print(f"{'frame':8} {'border':11} {'prints':>8}")
    for frame in _FRAMES:
        for border in _BORDERS:
            found = _search(f"frame:{frame} border:{border}")
            total = int(found.get("total_cards") or 0)
            time.sleep(_PAUSE)
            if total:
                print(f"{frame:8} {border:11} {total:8}")
    # and the check that makes the list above *closed* rather than merely long: if
    # any printing carried a sixth frame value it would land here. Last run: 0 of
    # 116233. A new generation shipping is the one thing that changes that, and this
    # is how it announces itself instead of quietly taking the fallback.
    left = int(
        _search(" ".join(f"-frame:{f}" for f in _FRAMES)).get("total_cards") or 0
    )
    print(f"\nprintings whose frame is none of the {len(_FRAMES)}: {left}")


#: The reference sets each generation's shipped numbers are read off, and *why*
#: each one: a **light border** is what makes the bottom edge readable at all. On a
#: black-bordered card the bottom border runs straight into the black
#: text/collector strip and no scan line can see where one ends and the other
#: begins — every black sample reports the bottom at support 0.36-0.69 — but a
#: white or silver border makes that same boundary a step change. Every generation
#: that ever printed a light border is therefore measured on one, with a
#: black-bordered set of the same generation as the cross-check.
_REFERENCE: tuple[tuple[str, str, str, str], ...] = (
    ("1993", "2ed", "white", "Unlimited — the white-bordered Alpha/Beta reprint"),
    ("1993", "3ed", "white", "Revised, same generation, second white sample"),
    ("1993", "lea", "black", "Alpha: cut differently, deliberately NOT the spec"),
    ("1997", "5ed", "white", "5th Edition — white-bordered core set"),
    ("1997", "6ed", "white", "6th Edition, second white sample"),
    ("1997", "mir", "black", "Mirage — the black-bordered cross-check"),
    ("2003", "8ed", "white", "8th Edition — the redesign, white-bordered"),
    ("2003", "9ed", "white", "9th Edition, second white sample"),
    ("2003", "10e", "black", "10th Edition — black-bordered cross-check"),
    ("future", "fut", "black", "Future Sight's timeshifted frame (black only)"),
    ("2015", "und", "silver", "Unstable — silver border, so the bottom is readable"),
    ("2015", "ulst", "silver", "Unsanctioned, second silver sample"),
    ("2015", "m15", "black", "Magic 2015 — the generation's own core set"),
    ("2015", "ori", "black", "Magic Origins — black-bordered cross-check"),
)

_EDGES = ("top", "right", "bottom", "left")


def _set_sample(set_id: str, wanted: int) -> list[dict[str, Any]]:
    """Ordinary cards of one set, spread across its numbering."""
    return _spread(f"set:{set_id} -t:token -is:oversized -is:promo", wanted)


def generations() -> None:
    """Each frame generation's border, off the sets that can actually show it.

    Prints per-edge mean, spread and **how many of the sampled cards the detector
    trusted on that edge** — an edge measured on two of twenty cards is not a
    measurement however tidy its mean, and that is exactly the M15 bottom.
    """
    print("\n=== each generation, off its reference sets ===")
    print("(n = cards whose scan lines agreed on that edge, of the sample)")
    for generation, set_id, border, why in _REFERENCE:
        rows: dict[str, list[float]] = {e: [] for e in _EDGES}
        seen, flat = 0, 0
        for card in _set_sample(set_id, _SAMPLES):
            got = _measure(card)
            if got is None:
                continue
            mm, support, frameless = got
            seen += 1
            if frameless:
                flat += 1
                continue
            for i, edge in enumerate(_EDGES):
                if support[i] >= _TRUSTED:
                    rows[edge].append(mm[i])
            time.sleep(_PAUSE)
        head = f"frame:{generation} {set_id} ({border} border, n={seen})"
        print(f"\n{head}\n  {why}")
        for edge in _EDGES:
            print(f"  {edge:7} {_stat(rows[edge])}")
        sides = rows["right"] + rows["left"]
        print(f"  {'sides':7} {_stat(sides)}")
        if flat:
            print(f"  {flat}/{seen} read as frameless")


def treatments() -> None:
    """Whether a *treatment* changes the geometry, or only the picture.

    Full-art, extended-art, showcase and textless printings keep their
    generation's border; a genuinely borderless one has none. This is the half
    that caught a real bug — ``full_art`` was being read as "no border".
    """
    groups = (
        ("2015 full-art", f"frame:2015 border:black is:fullart {_ORDINARY}", 8),
        ("2015 extended", f"frame:2015 is:extendedart {_ORDINARY}", 8),
        ("2015 showcase", f"frame:2015 is:showcase {_ORDINARY}", 8),
        ("2015 borderless", f"frame:2015 is:borderless {_ORDINARY}", 6),
        ("2015 yellow band", "frame:2015 border:yellow -is:oversized -t:token", 6),
    )
    print("\n=== treatments: does it change the border, or only the art? ===")
    for label, query, wanted in groups:
        rows: dict[str, list[float]] = {e: [] for e in _EDGES}
        seen, flat, sets = 0, 0, set[str]()
        for card in _spread(query, wanted):
            got = _measure(card)
            if got is None:
                continue
            mm, support, frameless = got
            seen += 1
            sets.add(str(card.get("set", "")))
            if frameless:
                flat += 1
                continue
            for i, edge in enumerate(_EDGES):
                if support[i] >= _TRUSTED:
                    rows[edge].append(mm[i])
            time.sleep(_PAUSE)
        print(f"\n{label:17} sides  {_stat(rows['right'] + rows['left'])}")
        print(f"{'':17} top    {_stat(rows['top'])}")
        print(f"{'':17} bottom {_stat(rows['bottom'])}")
        note = f"{flat}/{seen} frameless · " if flat else ""
        print(f"{'':17} {note}sets: {' '.join(sorted(sets)) or 'none'}")


if __name__ == "__main__":
    census()
    generations()
    treatments()
