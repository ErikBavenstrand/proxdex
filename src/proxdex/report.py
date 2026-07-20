"""Print-batch manifests and INDEX.md generation."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .library import Library, Stage


@dataclass(slots=True)
class Batch:
    name: str
    dir: Path
    printed: bool = False
    printed_date: str = ""
    cards: list[str] = field(default_factory=list)
    notes: str = ""


def batches(lib: Library) -> list[Batch]:
    out: list[Batch] = []
    for tf in sorted(lib.batches_dir.glob("*/batch.toml")):
        data = tomllib.loads(tf.read_text())
        out.append(
            Batch(
                name=str(data.get("name", tf.parent.name)),
                dir=tf.parent,
                printed=bool(data.get("printed", False)),
                printed_date=str(data.get("printed_date", "")),
                cards=list(data.get("cards", [])),
                notes=str(data.get("notes", "")),
            )
        )
    return out


def card_batch_index(lib: Library) -> dict[str, Batch]:
    """Map card id -> the batch that contains it (last one wins)."""
    index: dict[str, Batch] = {}
    for batch in batches(lib):
        for cid in batch.cards:
            index[cid] = batch
    return index


def write_index(lib: Library) -> Path:
    cards = lib.cards()
    by_card = card_batch_index(lib)
    lines = [
        "# Proxy Card Index",
        "",
        f"_{len(cards)} cards · stages: 1 original · 2 bordered · 3 upscaled · "
        "4 edited (trim master); bleed added at print_",
        "",
        "| Card | Name | Set | Orig | Bord | Upscl | Edit | Batch | Printed |",
        "|------|------|-----|:----:|:----:|:-----:|:----:|-------|:-------:|",
    ]
    for card in cards:
        marks = [
            " ✓ " if card.has(s) else " · "
            for s in (Stage.ORIGINAL, Stage.BORDERED, Stage.UPSCALED, Stage.EDITED)
        ]
        batch = by_card.get(card.id)
        printed = "✓" if batch and batch.printed else ""
        lines.append(
            f"| `{card.id}` | {card.name.title()} | {card.set_id} |"
            + "|".join(marks)
            + f"| {batch.name if batch else ''} | {printed} |"
        )
    lines += ["", "## Print batches", ""]
    for batch in batches(lib):
        state = f"printed {batch.printed_date}".strip() if batch.printed else "queued"
        note = f" — {batch.notes}" if batch.notes else ""
        lines.append(f"- **{batch.name}** ({state}) · {len(batch.cards)} cards{note}")
    dst = lib.root / "INDEX.md"
    dst.write_text("\n".join(lines) + "\n")
    return dst
