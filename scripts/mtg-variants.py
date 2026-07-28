#!/usr/bin/env -S uv run python
"""Does this (frame × border_color × frame_effects) combination have its own border?

    uv run python scripts/mtg-variants.py default-cards.json [out.json]

The question `scripts/mtg-census.py` cannot answer. That one measures a *generation*;
this one takes every combination of the three frame-ish fields Scryfall records and
asks whether each is geometrically its own thing or the same border as its
generation with a different picture inside it.

**This is the one thing a scan survey is genuinely good for.** A scan carries its own
crop, so it cannot tell you a border is 2.45mm (see :mod:`proxdex.frames`) — but a
crop error common to every scan **cancels when two populations read the same way are
compared to each other**. Grouping is a comparison. So the survey decides *how many
specs there should be and what selects each*, and calipers decide what the numbers
are. Neither job can do the other's.

What comes out is deliberately small: the combinations collapse into a handful of
groups, and proving that 40-odd effects do *not* move the border is most of the
value. `legendary` is a crown, `inverted` recolours the text box, `enchantment` is
the Nyx treatment, `etched` is a foil process — none of them is a border, and now
that is measured rather than assumed.

Sampling notes, each one a correction of a wrong answer this got first:

* **Sampled proportionally to the population**, not round-robin over sets. Round-robin
  gives a 6-card promo set the same weight as 4th Edition, which made
  ``frame:1993 border:black`` read 2.83mm — a mean over 30th Anniversary and Secret
  Lair reprints at modern trim — while its white-bordered sibling, the same
  generation, read 2.65mm. The set mix of each sample is reported so a result
  dominated by one odd product is visible.
* **Only trusted edges count** (support >= ``_TRUSTED``). This is load-bearing here:
  an extended-art card's *sides* genuinely cannot be measured, and reporting how many
  samples the detector refused is the finding, not an inconvenience.
* **Standard-size, non-token, non-digital only.** An oversized card is 89x127mm, so
  its border as a fraction of 63x88 is meaningless — that alone once invented a
  "gold border" variant that does not exist.
* **Double-faced cards come in through their faces.** Scryfall puts ``image_uris`` on
  the *faces* of a DFC rather than on the card, so requiring the top-level one dropped
  all 4244 DFC printings silently — the single worst kind of sampling bug, since the
  report still looked complete. Measured separately afterwards, a DFC front reads
  2.50/2.50/2.92 and its back 2.51/2.53/2.92 against a plain M15 card's
  2.48/2.45/2.88, so nothing about the specs changed; the hole was in the method, not
  the answer.

The tail (combinations under ``_MIN_PRINTS`` printings) is **not** sampled, and the
report says so rather than leaving it implied. Those combinations are all *unions* of
effects that are sampled individually, so measuring `enchantment` and `extendedart`
separately covers `enchantment,extendedart` in every way that matters.
"""

from __future__ import annotations

import collections
import io
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image

from proxdex import borders, scratch

_HEADERS = {"User-Agent": "proxdex-mtg-variants/1.0", "Accept": "*/*"}
_PAUSE = 0.15

#: how many cards to read per combination
_SAMPLES = 15
#: combinations rarer than this are named but not measured — see the docstring
_MIN_PRINTS = 20
#: an edge whose scan lines agreed less than this measured nothing
_TRUSTED = 0.8

#: layouts that are not a standard 63x88 card with one picture on it
_SKIP_LAYOUTS = frozenset(
    {
        "token",
        "double_faced_token",
        "emblem",
        "art_series",
        "planar",
        "scheme",
        "vanguard",
        "augment",
        "host",
    }
)

_EDGES = ("top", "right", "bottom", "left")
#: two groups whose sides differ by less than this are the same border. Well above
#: the detector's own spread (~0.05mm) and well below any real difference (M15 took
#: 0.55mm off its predecessor).
_SAME_MM = 0.15


def _get(url: str) -> bytes:
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers=_HEADERS)  # noqa: S310
            with urllib.request.urlopen(request, timeout=30) as reply:  # noqa: S310
                return reply.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _cards(path: Path) -> list[dict[str, Any]]:
    """Every printable standard-size card in the bulk file, reduced to what we need."""
    dec = json.JSONDecoder()
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        buf = fh.read(1 << 20)
        i = buf.index("[") + 1
        while True:
            while i < len(buf) and buf[i] in " \n\r\t,":
                i += 1
            if i < len(buf) and buf[i] == "]":
                break
            while True:
                try:
                    obj, end = dec.raw_decode(buf, i)
                    break
                except ValueError:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        raise
                    buf = buf[i:] + chunk
                    i = 0
            i = end
            # A double-faced card carries `image_uris` on its **faces**, not on
            # itself. Requiring the top-level one skipped all 4244 DFC printings
            # without a word — and proxdex has a whole faces feature for exactly
            # those. Front face, since that is the side a combination is named for.
            png = (obj.get("image_uris") or {}).get("png")
            if not png:
                for face in obj.get("card_faces") or []:
                    png = (face.get("image_uris") or {}).get("png")
                    if png:
                        break
            if (
                png
                and obj.get("layout") not in _SKIP_LAYOUTS
                and not obj.get("oversized")
                and not obj.get("digital")
            ):
                out.append(
                    {
                        "id": f"{obj.get('set')}-{obj.get('collector_number')}",
                        "set": str(obj.get("set") or ""),
                        "png": png,
                        "key": (
                            str(obj.get("frame") or ""),
                            str(obj.get("border_color") or ""),
                            ",".join(sorted(obj.get("frame_effects") or [])),
                        ),
                    }
                )
            if i > (1 << 19):
                buf = buf[i:]
                i = 0
                buf += fh.read(1 << 20)
    return out


def _spread(rows: list[dict[str, Any]], wanted: int) -> list[dict[str, Any]]:
    """A sample **proportional to the population**, spread evenly across it.

    This started as a round-robin over the distinct sets, and that was wrong in a
    way that produced a wrong answer immediately: it gives a 6-card promo set the
    same weight as 4th Edition, so ``frame:1993 border:black`` came back at 2.83mm
    — a mean over 30th Anniversary and Secret Lair reprints at modern trim rather
    than over the generation. Its own white-bordered sibling read 2.65mm, which is
    the same generation and cannot differ.

    Sorting by (set, number) and striding gives every printing equal probability, so
    the sample mean estimates the *population* mean — which is the thing a rule
    needs, since a rule is applied to whatever card the user actually owns. The set
    mix is reported alongside, so a result dominated by one odd product is visible
    rather than silently averaged in.
    """
    ordered = sorted(rows, key=lambda r: (r["set"], r["id"]))
    if len(ordered) <= wanted:
        return ordered
    step = len(ordered) / wanted
    return [ordered[min(int(i * step), len(ordered) - 1)] for i in range(wanted)]


def _measure(url: str) -> tuple[list[float], list[float], bool] | None:
    try:
        raw = _get(url)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    path = scratch.file(".png")
    image.save(path)
    try:
        found = borders.detect_inset(path)
    finally:
        path.unlink(missing_ok=True)
    mm = [v * (88.0 if i % 2 == 0 else 63.0) for i, v in enumerate(found.inset)]
    return mm, list(found.support), found.frameless


def survey(path: Path, out: Path) -> None:
    print(f"reading {path} …", flush=True)
    cards = _cards(path)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = collections.defaultdict(
        list
    )
    for card in cards:
        groups[card["key"]].append(card)
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    todo = [(k, v) for k, v in ranked if len(v) >= _MIN_PRINTS]
    skipped = [(k, len(v)) for k, v in ranked if len(v) < _MIN_PRINTS]
    total = sum(min(len(v), _SAMPLES) for _, v in todo)
    print(
        f"{len(cards)} cards, {len(groups)} combinations; measuring {len(todo)} of "
        f"them ({total} images), naming {len(skipped)} rarer than {_MIN_PRINTS}\n",
        flush=True,
    )

    results: list[dict[str, Any]] = []
    done = 0
    for (frame, border, effects) in [k for k, _ in todo]:
        rows = _spread(groups[(frame, border, effects)], _SAMPLES)
        edges: dict[str, list[float]] = {e: [] for e in _EDGES}
        flat, read, sets = 0, 0, set()
        set_mix: collections.Counter[str] = collections.Counter()
        for row in rows:
            got = _measure(row["png"])
            done += 1
            if got is None:
                continue
            mm, support, frameless = got
            read += 1
            sets.add(row["set"])
            set_mix[row["set"]] += 1
            if frameless:
                flat += 1
            else:
                for idx, edge in enumerate(_EDGES):
                    if support[idx] >= _TRUSTED:
                        edges[edge].append(mm[idx])
            time.sleep(_PAUSE)
        entry = {
            "frame": frame,
            "border": border,
            "effects": effects,
            "prints": len(groups[(frame, border, effects)]),
            "read": read,
            "frameless": flat,
            "sets": sorted(sets),
            "set_mix": dict(
                sorted(set_mix.items(), key=lambda kv: -kv[1])[:8]
            ),
            "edges": {
                e: {
                    "n": len(v),
                    "mean": round(statistics.mean(v), 3) if v else None,
                    "sd": round(statistics.stdev(v), 3) if len(v) > 1 else 0.0,
                }
                for e, v in edges.items()
            },
        }
        results.append(entry)
        label = f"{frame}/{border}/{effects or '-'}"
        sides = edges["right"] + edges["left"]
        summary = (
            f"sides {statistics.mean(sides):5.2f} (n={len(sides)})"
            if sides
            else "sides unmeasurable"
        )
        flag = f" · {flat}/{read} frameless" if flat else ""
        print(f"[{done:4}/{total}] {label:52} {summary}{flag}", flush=True)

    out.write_text(
        json.dumps(
            {
                "samples": _SAMPLES,
                "min_prints": _MIN_PRINTS,
                "trusted": _TRUSTED,
                "results": results,
                "unsampled": [
                    {"frame": k[0], "border": k[1], "effects": k[2], "prints": n}
                    for k, n in skipped
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"\nwrote {out}")


def _sides(entry: dict[str, Any]) -> float | None:
    parts = [
        entry["edges"][e]["mean"]
        for e in ("right", "left")
        if entry["edges"][e]["mean"] is not None
    ]
    return statistics.mean(parts) if parts else None


def _shape(entry: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """(top, sides, bottom) — the whole geometry, because sides alone hide the
    difference that matters most. The 1997 and 2003 frames measure the *same* at the
    sides (~3.0) and differ by 0.4mm at the top and bottom, so a grouping that
    compared sides would have merged two generations that are visibly different on
    cut paper."""
    edges = entry["edges"]
    return (edges["top"]["mean"], _sides(entry), edges["bottom"]["mean"])


def _apart(
    a: tuple[float | None, ...], b: tuple[float | None, ...]
) -> float | None:
    """The largest per-edge gap between two shapes, over the edges both measured."""
    both = [
        (x, y) for x, y in zip(a, b, strict=True) if x is not None and y is not None
    ]
    return max((abs(x - y) for x, y in both), default=None)


def group(path: Path) -> None:
    """Collapse the survey into the specs proxdex should actually have.

    Prints three things, and the second is the point of the exercise: which
    combinations are their own border, which are **the same border as their plain
    generation** (so they need no spec and no rule), and which cannot be measured at
    all — the last being a finding rather than a failure, since a borderless print
    has no border and an extended-art card's sides genuinely do not exist.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = data["results"]
    #: the reference each combination is compared against: its own generation, plain
    #: black-bordered, no treatments
    plain = {
        r["frame"]: _shape(r)
        for r in rows
        if not r["effects"] and r["border"] == "black"
    }

    def fmt(shape: tuple[float | None, ...]) -> str:
        return " ".join("  —  " if v is None else f"{v:5.2f}" for v in shape)

    own: list[tuple[str, tuple[float | None, ...], float, int]] = []
    same: list[tuple[str, tuple[float | None, ...], int]] = []
    flat: list[tuple[str, str, int]] = []
    for r in rows:
        label = f"{r['frame']}/{r['border']}/{r['effects'] or '-'}"
        shape = _shape(r)
        base = plain.get(r["frame"])
        gap = _apart(shape, base) if base is not None else None
        if all(v is None for v in shape):
            why = (
                f"{r['frameless']}/{r['read']} read as frameless"
                if r["frameless"]
                else "no edge the detector trusted"
            )
            flat.append((label, why, r["prints"]))
        elif gap is None or gap >= _SAME_MM:
            own.append((label, shape, gap if gap is not None else 99.0, r["prints"]))
        else:
            same.append((label, shape, r["prints"]))

    head = f"{'':54}{'top':>6}{'sides':>6}{'bot':>6}{'prints':>8}"
    print(f"\n=== its own border (some edge >= {_SAME_MM}mm from its plain "
          f"generation, or an edge the plain one has and it does not) ===")
    print(head)
    for label, shape, gap, prints in sorted(own, key=lambda x: -x[3]):
        mark = "" if gap > 90 else f"  worst edge {gap:+.2f}"
        print(f"  {label:52}{fmt(shape)}{prints:8}{mark}")
    print("\n=== no border to measure ===")
    print(head)
    for label, why, prints in sorted(flat, key=lambda x: -x[2]):
        print(f"  {label:52}{'':18}{prints:8}  {why}")
    print(f"\n=== same border as its plain generation ({len(same)} combinations) ===")
    print("  no spec and no rule needed: the treatment changes the picture, not the")
    print("  border — now measured rather than assumed.")
    print(head)
    for label, shape, prints in sorted(same, key=lambda x: -x[2]):
        print(f"  {label:52}{fmt(shape)}{prints:8}")
    print(f"\nunsampled tail: {len(data['unsampled'])} combinations, "
          f"{sum(u['prints'] for u in data['unsampled'])} printings")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--group"]:
        group(Path(sys.argv[2] if len(sys.argv) > 2 else "mtg-variants.json"))
    else:
        source = Path(sys.argv[1] if len(sys.argv) > 1 else "default-cards.json")
        target = Path(sys.argv[2] if len(sys.argv) > 2 else "mtg-variants.json")
        survey(source, target)
