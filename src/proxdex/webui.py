"""Local web UI: a FastAPI app with full parity to the CLI.

Display + light queries (cards, search, measure, config) are computed in-process
from the library; mutating actions (fetch, border/upscale/grade/build, sheet,
back, import, printed, calibrate) shell out to the real ``proxdex`` CLI so the
UI and terminal share exactly one implementation. Served on localhost only.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any

import requests
import tomlkit
from fastapi import Body, FastAPI, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from PIL import Image

from . import bleed, borders, frames, media, report, sources
from . import upscale as upscale_mod
from .config import Config
from .errors import ProxdexError
from .library import Library, Stage

_STAGES = (Stage.ORIGINAL, Stage.BORDERED, Stage.UPSCALED, Stage.EDITED)
_BEST = (Stage.EDITED, Stage.UPSCALED, Stage.BORDERED, Stage.ORIGINAL)
_BY_LABEL = {s.label: s for s in Stage}
_HTML_PATH = Path(__file__).parent / "webui.html"
_ID_OK = re.compile(r"^[A-Za-z0-9]+-[A-Za-z0-9]+$")


def _safe_ids(ids: list[Any]) -> list[str]:
    return [s for s in (str(i) for i in ids) if _ID_OK.match(s)]


def create_app(lib: Library) -> FastAPI:
    app = FastAPI(title="proxdex", docs_url=None, redoc_url=None)
    cfg_path = lib.root / "proxdex.toml"
    cal_dir = lib.root / "calibration"

    def run_cli(args: list[str]) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, "-m", "proxdex", "--root", str(lib.root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        return {"ok": proc.returncode == 0, "log": proc.stdout + proc.stderr}

    # ---- pages / static ----------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _HTML_PATH.read_text(encoding="utf-8")  # re-read → edit & refresh

    # ---- config ------------------------------------------------------------
    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        text = cfg_path.read_text() if cfg_path.exists() else ""
        doc = tomlkit.parse(text)
        sections: dict[str, Any] = {}
        for name, table in doc.items():
            if hasattr(table, "items"):
                sections[name] = {k: _unwrap(v) for k, v in table.items()}
        return {"root": str(lib.root), "sections": sections}

    @app.put("/api/config")
    def api_config_put(body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        text = cfg_path.read_text() if cfg_path.exists() else ""
        doc = tomlkit.parse(text)
        for section, kv in body.get("sections", {}).items():
            if section not in doc:
                doc[section] = tomlkit.table()
            for key, value in kv.items():
                doc[section][key] = value
        cfg_path.write_text(tomlkit.dumps(doc))
        return {"ok": True}

    @app.get("/api/meta")
    def api_meta() -> dict[str, Any]:
        calibrated = sorted(p.stem for p in cal_dir.glob("*.json"))
        return {
            "models": list(upscale_mod.MODELS),
            "profiles": list(media.PROFILES) + calibrated,
            "faces": ["fronts", "backs", "duplex"],
            "pages": ["a4", "letter"],
            "stages": [s.label for s in _STAGES],
        }

    # ---- cards / images ----------------------------------------------------
    @app.get("/api/cards")
    def api_cards() -> list[dict[str, Any]]:
        by_card = report.card_batch_index(lib)
        result: list[dict[str, Any]] = []
        for card in lib.cards():
            batch = by_card.get(card.id)
            result.append(
                {
                    "id": card.id,
                    "name": card.name.title(),
                    "set": card.set_id,
                    "stages": {s.label: card.has(s) for s in _STAGES},
                    "batch": batch.name if batch else None,
                    "printed": bool(batch and batch.printed),
                }
            )
        return result

    @app.get("/api/thumb/{cid}")
    def api_thumb(cid: str) -> Response:
        card = lib.find(cid)
        src = card.best(*_BEST) if card else None
        if src is None:
            return Response(status_code=404)
        im = Image.open(src).convert("RGB")
        im.thumbnail((360, 504))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82)
        return Response(buf.getvalue(), media_type="image/jpeg")

    @app.get("/api/view/{cid}/{stage}")
    def api_view(cid: str, stage: str) -> Response:
        """A downscaled JPEG for the viewer — small so all stages preload and
        flipping is instant/flicker-free (the lightbox uses full-res)."""
        card = lib.find(cid)
        st = _BY_LABEL.get(stage)
        if card is None or st is None or not card.has(st):
            return Response(status_code=404)
        im = Image.open(card.stage_path(st)).convert("RGB")
        im.thumbnail((1000, 1400))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return Response(buf.getvalue(), media_type="image/jpeg")

    @app.get("/api/image/{cid}/{stage}")
    def api_image(cid: str, stage: str) -> Response:
        card = lib.find(cid)
        st = _BY_LABEL.get(stage)
        if card is None or st is None or not card.has(st):
            return Response(status_code=404)
        return FileResponse(card.stage_path(st))

    @app.get("/api/measure/{cid}")
    def api_measure(cid: str, stage: str | None = None) -> dict[str, Any]:
        card = lib.find(cid)
        if card is None:
            return {"error": "no image"}
        st = _BY_LABEL.get(stage) if stage else None
        src = card.stage_path(st) if st and card.has(st) else card.best(*_BEST)
        if src is None or not src.exists():
            return {"error": "no image"}
        cfg = Config.load(lib.root)
        w, h = borders.size(src)
        plan = bleed.plan(w, h, cfg)  # aspect-only auto plan
        guide = frames.for_set(card.set_id)
        return {
            "w": w,
            "h": h,
            "aspect": round(w / h, 3),
            "card_aspect": round(cfg.card_w_mm / cfg.card_h_mm, 3),
            "delta": round(bleed.aspect_delta(w, h, cfg), 3),
            "format_ok": bleed.format_ok(w, h, cfg),
            # aspect-fix config so the UI can replicate bleed.plan for a live preview
            "card_w_mm": cfg.card_w_mm,
            "card_h_mm": cfg.card_h_mm,
            "fix_aspect": cfg.border_fix_aspect,
            "bias_x": cfg.border_aspect_bias_x,
            "bias_y": cfg.border_aspect_bias_y,
            # frame-size guide for this era: inner border inset [top,right,bottom,left]
            "guide": {"id": guide.id, "name": guide.name, "inset": list(guide.inset)},
            "plan": {
                "top": plan.top,
                "bottom": plan.bottom,
                "left": plan.left,
                "right": plan.right,
            },
        }

    @app.delete("/api/card/{cid}")
    def api_delete(cid: str) -> dict[str, Any]:
        card = lib.find(cid)
        if card is None:
            return {"ok": False, "log": f"{cid}: not found"}
        shutil.rmtree(card.dir, ignore_errors=True)
        report.write_index(lib)
        return {"ok": True, "log": f"deleted {cid}"}

    # ---- search / acquire --------------------------------------------------
    @app.get("/api/search")
    def api_search(
        q: str,
        set_filter: Annotated[str | None, Query(alias="set")] = None,
        rarity: str | None = None,
        year: str | None = None,
        limit: int = 60,
    ) -> Any:
        cfg = Config.load(lib.root)
        try:
            found = sources.search(
                q, cfg, set_filter=set_filter, rarity=rarity, year=year, limit=limit
            )
        except (requests.RequestException, ProxdexError) as exc:
            return {"error": f"search failed (try again): {exc}"}
        return [
            {
                "id": r.id,
                "name": r.name,
                "set": r.set_name,
                "year": r.year,
                "number": f"{r.number}/{r.printed_total}",
                "rarity": r.rarity,
                "artist": r.artist,
                "image": cfg.scrydex_url.format(id=r.id),
                "have": lib.find(r.id) is not None,
            }
            for r in found
        ]

    @app.post("/api/fetch")
    def api_fetch(body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        ids = _safe_ids(body.get("ids") or [])
        if not ids:
            return {"ok": False, "log": "no valid ids"}
        return run_cli(["fetch", *ids])

    @app.post("/api/import")
    def api_import(
        file: Annotated[UploadFile, File()],
        cid: Annotated[str, Form(alias="id")],
        stage: Annotated[str, Form()] = "original",
    ) -> dict[str, Any]:
        tmp = _spool(file)
        try:
            args = ["import", str(tmp), "--id", cid, "--stage", stage, "--move"]
            return run_cli(args)
        finally:
            tmp.unlink(missing_ok=True)

    # ---- prepare steps -----------------------------------------------------
    @app.post("/api/step")
    def api_step(body: Annotated[dict[str, Any], Body()]) -> Any:
        cmd = str(body.get("cmd", ""))
        if cmd not in {"border", "upscale", "grade", "build"}:
            return JSONResponse(
                {"ok": False, "log": f"bad step {cmd}"}, status_code=400
            )
        args = [cmd, *_safe_ids(body.get("ids") or [])]
        opts = body.get("opts") or {}
        if cmd == "border":
            for edge in ("top", "bottom", "left", "right"):
                val = opts.get(edge)
                if val:
                    args += [f"--{edge}", str(float(val))]
            if "fix_aspect" in opts:
                args.append("--fix-aspect" if opts["fix_aspect"] else "--no-fix-aspect")
        if cmd == "upscale":
            if opts.get("model"):
                args += ["--model", str(opts["model"])]
            if opts.get("scale"):
                args += ["--scale", str(int(opts["scale"]))]
            if "double" in opts:
                args.append("--double" if opts["double"] else "--no-double")
        if cmd == "grade" and "normalize" in opts:
            args.append("--normalize" if opts["normalize"] else "--no-normalize")
        if body.get("force"):
            args.append("--force")
        return run_cli(args)

    # ---- produce -----------------------------------------------------------
    @app.post("/api/sheet")
    def api_sheet(body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        args = ["sheet", str(body.get("name") or "deck")]
        args += ["--faces", str(body.get("faces") or "fronts")]
        if body.get("profile"):
            args += ["--profile", str(body["profile"])]
        if body.get("page"):
            args += ["--page", str(body["page"])]
        if body.get("dpi"):
            args += ["--dpi", str(int(body["dpi"]))]
        return run_cli(args)

    @app.post("/api/printed/{name}")
    def api_printed(name: str) -> dict[str, Any]:
        return run_cli(["printed", name])

    @app.post("/api/index")
    def api_index() -> dict[str, Any]:
        report.write_index(lib)
        return {"ok": True, "log": "INDEX.md regenerated"}

    @app.post("/api/back")
    def api_back(body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        if body.get("tcg"):
            return run_cli(["back", "--tcg", str(body["tcg"])])
        if body.get("url"):
            return run_cli(["back", "--url", str(body["url"])])
        return {"ok": False, "log": "give a url or tcg"}

    @app.post("/api/back/upload")
    def api_back_upload(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        tmp = _spool(file)
        try:
            return run_cli(["back", "--file", str(tmp)])
        finally:
            tmp.unlink(missing_ok=True)

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

    # ---- calibrate ---------------------------------------------------------
    @app.get("/api/calibrate")
    def api_calibrate_list() -> list[dict[str, Any]]:
        import json

        out: list[dict[str, Any]] = []
        for f in sorted(cal_dir.glob("*.json")):
            data = json.loads(f.read_text())
            out.append(
                {
                    "profile": data.get("profile", f.stem),
                    "error": data.get("uncorrected_error", {}),
                }
            )
        return out

    @app.get("/api/calibrate/chart")
    def api_cal_chart(profile: str, corrected: bool = False) -> Response:
        args = ["calibrate", "target", "--profile", profile, "--pdf"]
        if corrected:
            args.append("--corrected")
        res = run_cli(args)
        suffix = "_chart_corrected" if corrected else "_chart"
        pdf = cal_dir / f"{profile}{suffix}.pdf"
        if not res["ok"] or not pdf.is_file():
            return JSONResponse({"ok": False, "log": res["log"]}, status_code=400)
        return FileResponse(pdf, media_type="application/pdf")

    @app.post("/api/calibrate/fit")
    def api_cal_fit(
        file: Annotated[UploadFile, File()],
        profile: Annotated[str, Form()],
        check: Annotated[bool, Form()] = False,
    ) -> dict[str, Any]:
        tmp = _spool(file)
        sub = "check" if check else "fit"
        try:
            return run_cli(["calibrate", sub, "--profile", profile, "--scan", str(tmp)])
        finally:
            tmp.unlink(missing_ok=True)

    return app


def app_from_env() -> FastAPI:
    """Factory for ``uvicorn --reload``: discovers the library from PROXDEX_ROOT."""
    root = os.environ.get("PROXDEX_ROOT")
    return create_app(Library.discover(explicit=Path(root) if root else None))


def _spool(file: UploadFile) -> Path:
    """Write an uploaded file to a temp path (sync — used in sync handlers)."""
    suffix = Path(file.filename or "upload.png").suffix or ".png"
    tmp = Path(tempfile.mkstemp(suffix=suffix)[1])
    tmp.write_bytes(file.file.read())
    return tmp


def _unwrap(value: Any) -> Any:
    return value.unwrap() if hasattr(value, "unwrap") else value
