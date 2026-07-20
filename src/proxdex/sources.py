"""Card metadata (Pokémon TCG API) and image download (scrydex)."""

from __future__ import annotations

import io
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


def download_large(cid: str, cfg: Config) -> Image.Image:
    """Download the high-res card image from scrydex, normalized to RGB."""
    url = cfg.scrydex_url.format(id=cid)
    resp = requests.get(url, timeout=60)
    if not resp.ok:
        raise FileError(f"{cid}: scrydex returned {resp.status_code} for {url}")
    return Image.open(io.BytesIO(resp.content)).convert("RGB")
