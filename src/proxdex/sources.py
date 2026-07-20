"""Card metadata (Pokémon TCG API) and image download (scrydex)."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import requests
from PIL import Image

from .config import Config
from .errors import FileError


@dataclass(slots=True)
class CardMeta:
    id: str
    name: str
    set_id: str
    set_name: str


@dataclass(slots=True)
class SearchResult:
    """A card returned by :func:`search`, with metadata for picking."""

    id: str
    name: str
    set_id: str
    set_name: str
    series: str
    year: str
    number: str
    printed_total: str
    rarity: str
    artist: str

    def to_meta(self) -> CardMeta:
        return CardMeta(self.id, self.name, self.set_id, self.set_name)


def lookup(cid: str, cfg: Config) -> CardMeta:
    """Resolve a card id to its name and set via the Pokémon TCG API."""
    resp = requests.get(cfg.api_url.format(id=cid), timeout=30)
    if resp.status_code == 404:
        raise FileError(f"{cid}: not found in the Pokémon TCG API")
    resp.raise_for_status()
    data = resp.json()["data"]
    return CardMeta(
        id=data["id"],
        name=data["name"],
        set_id=data["set"]["id"],
        set_name=data["set"]["name"],
    )


def search(
    query: str,
    cfg: Config,
    *,
    set_filter: str | None = None,
    rarity: str | None = None,
    year: str | None = None,
    limit: int = 100,
) -> list[SearchResult]:
    """Search the TCG API by name; each query word must appear in the name.

    ``set_filter`` is pushed into the query (by set id like ``ex4`` or a set
    name substring); ``rarity`` and ``year`` are matched on the results.
    """
    parts = [f"name:*{token}*" for token in query.split() if token]
    if set_filter:
        if re.fullmatch(r"[a-z]+\d*", set_filter, re.IGNORECASE):
            parts.append(f"set.id:{set_filter}")
        else:
            parts.append(f"set.name:*{set_filter}*")
    base = cfg.api_url.replace("/{id}", "")
    params = {
        "q": " ".join(parts) or "*",
        "orderBy": "set.releaseDate",
        "pageSize": min(limit, 250),
        "select": "id,name,number,rarity,artist,set",
    }
    resp = requests.get(base, params=params, timeout=30)
    if not resp.ok:
        raise FileError(f"TCG API search failed ({resp.status_code}) for {query!r}")
    results: list[SearchResult] = []
    for data in resp.json().get("data", []):
        card_set = data.get("set", {})
        result = SearchResult(
            id=data["id"],
            name=data.get("name", ""),
            set_id=card_set.get("id", ""),
            set_name=card_set.get("name", ""),
            series=card_set.get("series", ""),
            year=str(card_set.get("releaseDate", "")).split("/")[0],
            number=str(data.get("number", "")),
            printed_total=str(card_set.get("printedTotal", "")),
            rarity=data.get("rarity") or "—",
            artist=data.get("artist") or "—",
        )
        if rarity and rarity.lower() not in result.rarity.lower():
            continue
        if year and str(year) != result.year:
            continue
        results.append(result)
    return results


def download_large(cid: str, cfg: Config) -> Image.Image:
    """Download the high-res card image from scrydex, normalized to RGB."""
    url = cfg.scrydex_url.format(id=cid)
    resp = requests.get(url, timeout=60)
    if not resp.ok:
        raise FileError(f"{cid}: scrydex returned {resp.status_code} for {url}")
    return Image.open(io.BytesIO(resp.content)).convert("RGB")
