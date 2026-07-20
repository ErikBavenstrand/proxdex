"""Local web UI: a FastAPI app over the existing library and CLI.

Display data is read straight from the library (fast, no subprocess); actions
(build / sheet / printed) shell out to the real ``proxdex`` CLI so the UI and
terminal share exactly one implementation. Launched by ``proxdex ui``; served
only on localhost.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image

from . import report
from .library import Library, Stage

_STAGES = (Stage.ORIGINAL, Stage.BORDERED, Stage.UPSCALED, Stage.EDITED)
_BY_LABEL = {s.label: s for s in Stage}
_HTML = (Path(__file__).parent / "webui.html").read_text(encoding="utf-8")


def create_app(lib: Library) -> FastAPI:
    app = FastAPI(title="proxdex", docs_url=None, redoc_url=None)

    def run_cli(args: list[str]) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, "-m", "proxdex", "--root", str(lib.root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        return {"ok": proc.returncode == 0, "log": proc.stdout + proc.stderr}

    def known_ids(ids: list[str]) -> list[str]:
        """Keep only ids that resolve to a card (guards the subprocess argv)."""
        return [i for i in ids if lib.find(i) is not None]

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _HTML

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        return {"root": str(lib.root)}

    @app.get("/api/cards")
    def api_cards() -> list[dict[str, Any]]:
        by_card = report.card_batch_index(lib)
        cards: list[dict[str, Any]] = []
        for card in lib.cards():
            batch = by_card.get(card.id)
            cards.append(
                {
                    "id": card.id,
                    "name": card.name.title(),
                    "set": card.set_id,
                    "stages": {s.label: card.has(s) for s in _STAGES},
                    "batch": batch.name if batch else None,
                    "printed": bool(batch and batch.printed),
                }
            )
        return cards

    @app.get("/api/thumb/{cid}")
    def api_thumb(cid: str) -> Response:
        card = lib.find(cid)
        src = card.best(*reversed(_STAGES)) if card else None
        if src is None:
            return Response(status_code=404)
        im = Image.open(src).convert("RGB")
        im.thumbnail((360, 504))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82)
        return Response(buf.getvalue(), media_type="image/jpeg")

    @app.get("/api/image/{cid}/{stage}")
    def api_image(cid: str, stage: str) -> Response:
        card = lib.find(cid)
        st = _BY_LABEL.get(stage)
        if card is None or st is None or not card.has(st):
            return Response(status_code=404)
        return FileResponse(card.stage_path(st))

    @app.post("/api/build")
    def api_build(body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        ids = known_ids(list(body.get("ids") or []))
        return run_cli(["build", *ids])

    @app.post("/api/sheet")
    def api_sheet(body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        name = str(body.get("name") or "deck")
        faces = str(body.get("faces") or "fronts")
        args = ["sheet", name, "--faces", faces]
        if body.get("profile"):
            args += ["--profile", str(body["profile"])]
        return run_cli(args)

    @app.post("/api/printed/{name}")
    def api_printed(name: str) -> dict[str, Any]:
        return run_cli(["printed", name])

    @app.get("/api/batches")
    def api_batches() -> list[dict[str, Any]]:
        return [
            {
                "name": b.name,
                "dir": b.dir.name,
                "printed": b.printed,
                "cards": len(b.cards),
                "pdfs": sorted(p.name for p in b.dir.glob("*.pdf")),
            }
            for b in report.batches(lib)
        ]

    @app.get("/api/pdf/{batch}/{filename}")
    def api_pdf(batch: str, filename: str) -> Response:
        path = lib.batches_dir / batch / filename
        if path.suffix != ".pdf" or not path.is_file():
            return Response(status_code=404)
        return FileResponse(path, media_type="application/pdf")

    return app
