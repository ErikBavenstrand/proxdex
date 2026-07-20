"""Closed-loop colour calibration: print a chart, scan it, fit a correction.

The workflow is per *medium* (a print profile like ``paper`` or ``foil-holo``):

1. ``render_chart()`` → a grid of known patches with four corner fiducials.
   Print it on the medium, scan it (scanner auto-correction OFF).
2. ``detect_fiducials`` + ``sample_patches`` read the printed-then-scanned RGB
   of every patch; ``fit`` inverts the round trip into a degree-2 polynomial
   colour correction ``C`` s.t. printing ``C(v)`` scans back as ``v`` (the
   square terms behave as per-channel tone curves, the cross terms as a colour
   matrix).
3. The correction is applied at the print stage. Re-print the *corrected* chart
   and ``check`` the residual to see how true it now is (repeat to converge).

The scanner is the measuring device, so accuracy is "true as your scanner sees
it", not colorimetric — good enough for proxies, bounded by scanner neutrality
and printer gamut.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw

RGB = NDArray[np.float32]

# chart geometry, in normalized [0, 1] chart coordinates -----------------------
CANVAS_W, CANVAS_H = 1200, 1350
COLS, ROWS = 6, 6
FIDUCIALS = ((0.06, 0.05), (0.94, 0.05), (0.06, 0.95), (0.94, 0.95))
_FID_SIZE = 0.045
_GRID = (0.12, 0.14, 0.88, 0.90)  # x0, y0, x1, y1 patch region


def chart_patches() -> list[tuple[int, int, int]]:
    """36 known patches: a neutral ramp (first 8) + primaries + card tones."""
    grays = [(v, v, v) for v in (0, 36, 73, 109, 146, 182, 219, 255)]
    prim: list[tuple[int, int, int]] = []
    for lvl in (255, 170, 85):
        prim += [
            (lvl, 0, 0),
            (0, lvl, 0),
            (0, 0, lvl),
            (0, lvl, lvl),
            (lvl, 0, lvl),
            (lvl, lvl, 0),
        ]
    misc = [
        (250, 214, 46),
        (230, 150, 40),
        (200, 40, 40),
        (40, 80, 200),
        (30, 150, 90),
        (240, 200, 170),
        (120, 70, 40),
        (20, 20, 20),
        (200, 200, 255),
        (255, 240, 200),
    ]
    return grays + prim + misc


def _patch_centers() -> list[tuple[float, float]]:
    x0, y0, x1, y1 = _GRID
    centers = []
    for i in range(len(chart_patches())):
        col, row = i % COLS, i // COLS
        cx = x0 + (col + 0.5) / COLS * (x1 - x0)
        cy = y0 + (row + 0.5) / ROWS * (y1 - y0)
        centers.append((cx, cy))
    return centers


# --------------------------------------------------------- correction ---------
# Degree-2 polynomial colour correction: the square terms act as per-channel
# tone curves, the cross terms as a colour matrix — so it subsumes a
# curves+matrix model without assuming an application order.
def _features(arr: RGB) -> RGB:
    n = (arr.clip(0, 255) / 255.0).astype(np.float32)
    r, g, b = n[..., 0], n[..., 1], n[..., 2]
    o = np.ones_like(r)
    return np.stack([o, r, g, b, r * r, g * g, b * b, r * g, g * b, b * r], axis=-1)


@dataclass(slots=True)
class Stage:
    coef: NDArray[np.float32]  # (10, 3): send = features(look) . coef


def apply_stage(arr: RGB, stage: Stage) -> RGB:
    out = _features(arr) @ stage.coef
    return out.clip(0, 255).astype(np.float32)


def fit(measured: RGB, target: RGB) -> Stage:
    """Fit the scanned->send correction by least squares over all patches."""
    coef, *_ = np.linalg.lstsq(_features(measured), target, rcond=None)
    return Stage(coef=coef.astype(np.float32))


def error(measured: RGB, target: RGB) -> dict[str, float]:
    """Euclidean RGB distance between a print's patches and the target."""
    d = np.sqrt(((measured - target) ** 2).sum(axis=1))
    return {"mean": float(d.mean()), "max": float(d.max())}


# ------------------------------------------------------------ chart render ----
def render_chart(correction: Stage | None = None) -> Image.Image:
    im = Image.new("RGB", (CANVAS_W, CANVAS_H), (255, 255, 255))
    draw = ImageDraw.Draw(im)
    fid = int(_FID_SIZE * CANVAS_W)
    for fx, fy in FIDUCIALS:
        cx, cy = fx * CANVAS_W, fy * CANVAS_H
        draw.rectangle(
            [cx - fid / 2, cy - fid / 2, cx + fid / 2, cy + fid / 2], fill=(0, 0, 0)
        )
    x0, y0, x1, y1 = _GRID
    cw = (x1 - x0) / COLS * CANVAS_W
    ch = (y1 - y0) / ROWS * CANVAS_H
    pad = min(cw, ch) * 0.12
    for i, color in enumerate(chart_patches()):
        col, row = i % COLS, i // COLS
        px = (x0 + col / COLS * (x1 - x0)) * CANVAS_W
        py = (y0 + row / ROWS * (y1 - y0)) * CANVAS_H
        fill = color
        if correction is not None:
            arr = np.array([[color]], np.float32)
            out = apply_stage(arr, correction)[0, 0]
            fill = (int(out[0]), int(out[1]), int(out[2]))
        draw.rectangle([px + pad, py + pad, px + cw - pad, py + ch - pad], fill=fill)
    return im


# ------------------------------------------------------------ extraction ------
def detect_fiducials(arr: RGB) -> list[tuple[float, float]]:
    """Locate the four corner fiducials (dark blob centroid per corner)."""
    h, w, _ = arr.shape
    lum = arr @ np.array([0.299, 0.587, 0.114], np.float32)
    win = 0.18
    points: list[tuple[float, float]] = []
    for fx, fy in FIDUCIALS:
        xs = slice(0, int(w * win)) if fx < 0.5 else slice(int(w * (1 - win)), w)
        ys = slice(0, int(h * win)) if fy < 0.5 else slice(int(h * (1 - win)), h)
        region = lum[ys, xs]
        dark = region < 70
        if dark.sum() < 20:
            raise ValueError(
                "couldn't find a corner fiducial — scan cropped to the chart, "
                "with scanner auto-correction off?"
            )
        yy, xx = np.nonzero(dark)
        points.append((float(xx.mean() + xs.start), float(yy.mean() + ys.start)))
    return points


def _affine(dst: list[tuple[float, float]]) -> NDArray[np.float32]:
    """Map chart-normalized (fx, fy) -> scan (x, y) from the four fiducials."""
    src = np.array([[fx, fy, 1.0] for fx, fy in FIDUCIALS], np.float32)
    out = np.array(dst, np.float32)
    params, *_ = np.linalg.lstsq(src, out, rcond=None)  # (3, 2)
    return params.astype(np.float32)


def sample_patches(arr: RGB, params: NDArray[np.float32]) -> RGB:
    h, w, _ = arr.shape
    measured = np.zeros((len(chart_patches()), 3), np.float32)
    r = max(3, int(0.01 * min(h, w)))
    for i, (cx, cy) in enumerate(_patch_centers()):
        x, y = np.array([cx, cy, 1.0], np.float32) @ params
        xi, yi = round(float(x)), round(float(y))
        patch = arr[max(0, yi - r) : yi + r, max(0, xi - r) : xi + r]
        measured[i] = np.median(patch.reshape(-1, 3), axis=0)
    return measured


def read_scan(path: Path) -> RGB:
    arr = np.asarray(Image.open(path).convert("RGB"), np.float32)
    return sample_patches(arr, _affine(detect_fiducials(arr)))


# --------------------------------------------------------------- storage ------
def path_for(cal_dir: Path, profile: str) -> Path:
    return cal_dir / f"{profile}.json"


def save(cal_dir: Path, profile: str, stage: Stage, err: dict[str, float]) -> Path:
    cal_dir.mkdir(parents=True, exist_ok=True)
    dst = path_for(cal_dir, profile)
    dst.write_text(
        json.dumps(
            {
                "profile": profile,
                "model": "poly2",
                "uncorrected_error": err,
                "coef": stage.coef.tolist(),
            },
            indent=2,
        )
    )
    return dst


def load(cal_dir: Path, profile: str) -> Stage | None:
    f = path_for(cal_dir, profile)
    if not f.exists():
        return None
    data = json.loads(f.read_text())
    return Stage(coef=np.asarray(data["coef"], np.float32))


def apply_to_image(im: Image.Image, stage: Stage) -> Image.Image:
    arr = np.asarray(im.convert("RGB"), np.float32)
    return Image.fromarray(apply_stage(arr, stage).round().astype(np.uint8))
