"""The calibration chart: render it, read it back, fit a correction.

Colour calibration here is a **loop you can walk several times on one sheet of
paper**, because the medium is the thing being measured and paper is the thing
you have least of:

1. :func:`chart_page` renders a page with the chart in **one slot** of a grid
   (2×3 on A4 by default) and the rest left blank. Print it on the medium.
2. Scan it (scanner auto-correction OFF). :func:`read_scan` crops that slot and
   reads what every patch actually came back as.
3. That round is recorded on the profile, and the correction is refitted over
   **every round's** samples at once (:func:`fit`) — so each round adds real
   measurements instead of replacing the last ones.
4. Re-feed the *same* sheet, print the next round into the next free slot, and go
   again. The residual per round (:func:`error`) is what tells you it converged.

Round 1 prints the raw target, so it measures how far off the medium is. Round 2
prints the target *through* the correction learned so far, which samples the space
right where the cards live, and the fit gets truer exactly there. Six rounds fit
on one A4 sheet.

The scanner is the measuring device, so accuracy is "true as your scanner sees
it", not colorimetric — good enough for proxies, and bounded by scanner
neutrality and printer gamut.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont

from proxdex.config import Config
from proxdex.errors import ProxdexError

RGB = NDArray[np.float32]
#: an (n, 3) block of patch colours — one row per chart patch
Patches = NDArray[np.float32]
#: the (10, 3) polynomial coefficients of a correction
Coef = NDArray[np.float32]

# chart geometry, in normalized [0, 1] chart coordinates -----------------------
CANVAS_W, CANVAS_H = 1200, 1350
COLS, ROWS = 6, 6
FIDUCIALS = ((0.06, 0.05), (0.94, 0.05), (0.06, 0.95), (0.94, 0.95))
_FID_SIZE = 0.045
_GRID = (0.12, 0.16, 0.88, 0.90)  # x0, y0, x1, y1 patch region
_LABEL_Y = 0.115


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


def target() -> Patches:
    """The chart's patches as a float array — what a true print would scan as."""
    return np.array(chart_patches(), np.float32)


def _patch_centers() -> list[tuple[float, float]]:
    x0, y0, x1, y1 = _GRID
    centers: list[tuple[float, float]] = []
    for i in range(len(chart_patches())):
        col, row = i % COLS, i // COLS
        cx = x0 + (col + 0.5) / COLS * (x1 - x0)
        cy = y0 + (row + 0.5) / ROWS * (y1 - y0)
        centers.append((cx, cy))
    return centers


# ------------------------------------------------------------------- slots ----
#: how many charts one sheet holds. Six rounds of A4 is enough to converge and
#: still leaves the numbers readable at this chart size.
GRID: tuple[int, int] = (2, 3)
#: the chart sits inside its cell, so a small crop error lands on white paper
#: rather than in the neighbouring chart
_CELL_INSET = 0.06
#: and the crop is padded by less than that inset, for scan/page misalignment
_CROP_PAD = 0.04
#: a crop smaller than this is not a chart, it is a mistake
_MIN_CROP_PX = 2
#: a slot, or a grid, is exactly two numbers
_PAIR = 2


@dataclass(frozen=True, slots=True)
class Slot:
    """Which cell of the sheet a round was printed in."""

    col: int
    row: int

    @property
    def text(self) -> str:
        return f"{self.col + 1},{self.row + 1}"

    def json(self) -> list[int]:
        return [self.col, self.row]

    def within(self, grid: tuple[int, int]) -> bool:
        return 0 <= self.col < grid[0] and 0 <= self.row < grid[1]

    @classmethod
    def parse(cls, text: str, grid: tuple[int, int] = GRID) -> Slot:
        """``"2,3"`` → the slot, 1-based as printed on the chart."""
        parts = [p for p in text.replace("x", ",").split(",") if p.strip()]
        try:
            col, row = (int(p) - 1 for p in parts)
        except ValueError:
            raise ProxdexError(f"slot {text!r}: expected COL,ROW like '1,2'") from None
        slot = cls(col=col, row=row)
        if not slot.within(grid):
            raise ProxdexError(
                f"slot {text!r} is outside the {grid[0]}×{grid[1]} sheet grid"
            )
        return slot

    @classmethod
    def read(cls, data: object) -> Slot:
        if isinstance(data, list) and len(data) == _PAIR:
            pair: list[object] = data
            if all(isinstance(v, int) for v in pair):
                return cls(col=int(pair[0]), row=int(pair[1]))  # type: ignore[arg-type]
        return cls(col=0, row=0)


#: the slot a sheet starts at, and the default when none is given
FIRST_SLOT = Slot(col=0, row=0)


def slots(grid: tuple[int, int] = GRID) -> tuple[Slot, ...]:
    """Every slot of a sheet, in the order they are used (row-major)."""
    return tuple(Slot(col=c, row=r) for r in range(grid[1]) for c in range(grid[0]))


def _cell(cfg: Config, slot: Slot, grid: tuple[int, int]) -> tuple[float, ...]:
    """The slot's rectangle as a fraction of the page: x0, y0, x1, y1."""
    from proxdex.sheet import page_mm

    page_w, page_h = page_mm(cfg)
    mx = cfg.sheet_margin_mm / page_w
    my = cfg.sheet_margin_mm / page_h
    cw = (1.0 - 2 * mx) / grid[0]
    ch = (1.0 - 2 * my) / grid[1]
    x0 = mx + slot.col * cw
    y0 = my + slot.row * ch
    return x0, y0, x0 + cw, y0 + ch


# --------------------------------------------------------- correction ---------
# Degree-2 polynomial colour correction: the square terms act as per-channel
# tone curves, the cross terms as a colour matrix — so it subsumes a
# curves+matrix model without assuming an application order.
def _features(arr: RGB) -> RGB:
    n = (arr.clip(0, 255) / 255.0).astype(np.float32)
    r, g, b = n[..., 0], n[..., 1], n[..., 2]
    o = np.ones_like(r)
    return np.stack([o, r, g, b, r * r, g * g, b * b, r * g, g * b, b * r], axis=-1)


#: the coefficients that change nothing — features(v) @ IDENTITY == v
IDENTITY: Coef = np.zeros((10, 3), np.float32)
IDENTITY[1, 0] = IDENTITY[2, 1] = IDENTITY[3, 2] = 255.0

#: ridge weight, in pseudo-samples of "change nothing". Deliberately small and
#: *absolute*: it conditions the solve and keeps a single round from inventing a
#: wild curve, but it does not grow with the data, so every round you add makes
#: the fit truer rather than being damped by the prior. It is not a defence
#: against a bad round — measured, a shuffled scan is ruinous at any weight — and
#: the honest defence for that is naming it (`calibrate add` warns) and being able
#: to remove it (`calibrate drop`).
_RIDGE = 0.1


@dataclass(frozen=True, slots=True)
class Correction:
    """What to send the printer so the paper comes back the colour you asked for."""

    coef: Coef  # (10, 3): send = features(wanted) . coef

    def apply(self, arr: RGB) -> RGB:
        return (_features(arr) @ self.coef).clip(0, 255).astype(np.float32)

    def apply_to_image(self, im: Image.Image) -> Image.Image:
        arr = np.asarray(im.convert("RGB"), np.float32)
        return Image.fromarray(self.apply(arr).round().astype(np.uint8))

    def json(self) -> list[list[float]]:
        return [[float(v) for v in row] for row in self.coef]

    @classmethod
    def read(cls, data: object) -> Correction | None:
        """A correction from untrusted JSON, or None if it isn't one."""
        if not isinstance(data, list):
            return None
        arr = np.asarray(data, np.float32)
        if arr.shape != IDENTITY.shape or not np.isfinite(arr).all():
            return None
        return cls(coef=arr)


#: below this many usable samples, take the clipped ones back rather than fit a
#: 10-term polynomial to almost nothing
_MIN_SAMPLES = 12
#: what counts as pinned against the bottom or top of what can be sent
_FLOOR, _CEILING = 0.5, 254.5


def usable(sent: Patches) -> NDArray[np.bool_]:
    """Which samples carry information about the printer's response.

    A send value pinned at 0 or 255 is a colour the printer was *asked* for and
    could not make: several different wanted colours all clip to the same send, so
    the pair says nothing about the invertible part of the response and only drags
    the polynomial away from the range the cards actually live in.
    """
    inside = (sent > _FLOOR) & (sent < _CEILING)
    return np.asarray(inside.all(axis=1), dtype=np.bool_)


def fit(scanned: Patches, sent: Patches) -> Correction:
    """Fit ``wanted -> send`` from every (scanned, sent) pair measured so far.

    ``scanned`` is what the paper gave back, ``sent`` is what was asked for, so
    the fit maps a colour you *want* to the value that produces it. Stacking every
    round's pairs is what makes the loop converge: later rounds sample the space
    nearer the target, where accuracy actually matters.
    """
    keep = usable(sent)
    if keep.sum() >= _MIN_SAMPLES:
        scanned, sent = scanned[keep], sent[keep]
    feat = _features(scanned)
    # an absolute prior, not one that grows with the data: a ridge proportional to
    # the sample count damps every round you add, which is exactly backwards for a
    # loop whose whole purpose is to get truer the more you measure
    gram = feat.T @ feat + _RIDGE * np.eye(feat.shape[1], dtype=np.float32)
    rhs = feat.T @ sent + _RIDGE * IDENTITY
    coef = np.linalg.solve(gram, rhs)
    return Correction(coef=coef.astype(np.float32))


@dataclass(frozen=True, slots=True)
class Error:
    """How far a print landed from the target, over the colours it could reach.

    Both halves matter. A medium has a *gamut*: white paper is not 255 and no ink
    is 0, so some target patches are unreachable no matter how good the
    calibration — averaging those in gives a number that can never fall and tells
    you nothing about whether the loop is working. So ``mean``/``max`` cover the
    patches this medium can actually hit, and ``clipped`` says how many it cannot.
    """

    mean: float
    max: float
    #: patches the error was measured over
    measured: int = 0
    #: target patches outside what this medium can print — a fact about the paper
    #: and ink, not a fault in the calibration
    clipped: int = 0

    @property
    def total(self) -> int:
        return self.measured + self.clipped

    def json(self) -> dict[str, float]:
        return {
            "mean": self.mean,
            "max": self.max,
            "measured": self.measured,
            "clipped": self.clipped,
        }

    @classmethod
    def read(cls, data: object) -> Error:
        raw: dict[str, object] = data if isinstance(data, dict) else {}
        return cls(
            mean=_float(raw.get("mean")),
            max=_float(raw.get("max")),
            measured=int(_float(raw.get("measured"))),
            clipped=int(_float(raw.get("clipped"))),
        )


def in_gamut(scanned: Patches, wanted: Patches) -> NDArray[np.bool_]:
    """Which target patches this print could have reached.

    The reachable range is read from the print itself — the darkest and brightest
    each channel actually came back as — so it is a measurement of *this* paper
    and ink, not an assumption about printers in general.
    """
    lo = scanned.min(axis=0)
    hi = scanned.max(axis=0)
    inside = (wanted >= lo) & (wanted <= hi)
    return np.asarray(inside.all(axis=1), dtype=np.bool_)


def error(scanned: Patches, wanted: Patches | None = None) -> Error:
    """RGB distance from the target, over the patches the medium can reach."""
    goal = target() if wanted is None else wanted
    d = np.sqrt(((scanned - goal) ** 2).sum(axis=1))
    reach = in_gamut(scanned, goal)
    inside = d[reach]
    if not inside.size:  # nothing reachable — report the whole thing rather than
        inside = d  # claim a clean sheet on no evidence
        reach = np.ones_like(reach)
    return Error(
        mean=float(inside.mean()),
        max=float(inside.max()),
        measured=int(reach.sum()),
        clipped=int((~reach).sum()),
    )


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    out = float(value)
    return 0.0 if not np.isfinite(out) else out


# ------------------------------------------------------------ chart render ----
def sent_patches(correction: Correction | None) -> Patches:
    """What this round actually puts on paper: the target, through what we know."""
    goal = target()
    return goal if correction is None else correction.apply(goal)


def render_chart(
    correction: Correction | None = None,
    label: str = "",
    size: tuple[int, int] = (CANVAS_W, CANVAS_H),
) -> Image.Image:
    """The chart itself. ``label`` is printed above the patches.

    ``size`` is in pixels: the chart is *drawn* at whatever size it will print
    at, never drawn small and scaled up, so every patch stays exactly the colour
    it is meant to be and no resampler invents one in between.
    """
    width, height = size
    im = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(im)
    fid = int(_FID_SIZE * width)
    for fx, fy in FIDUCIALS:
        cx, cy = fx * width, fy * height
        draw.rectangle(
            [cx - fid / 2, cy - fid / 2, cx + fid / 2, cy + fid / 2], fill=(0, 0, 0)
        )
    if label:
        draw.text(
            (_GRID[0] * width, _LABEL_Y * height),
            label,
            fill=(0, 0, 0),
            font=_font(width),
            anchor="ls",
        )
    x0, y0, x1, y1 = _GRID
    cw = (x1 - x0) / COLS * width
    ch = (y1 - y0) / ROWS * height
    pad = min(cw, ch) * 0.12
    for i, color in enumerate(sent_patches(correction).round().astype(int)):
        col, row = i % COLS, i // COLS
        px = (x0 + col / COLS * (x1 - x0)) * width
        py = (y0 + row / ROWS * (y1 - y0)) * height
        fill = (int(color[0]), int(color[1]), int(color[2]))
        draw.rectangle([px + pad, py + pad, px + cw - pad, py + ch - pad], fill=fill)
    return im


#: label height as a share of the chart width, so it scales with the print
_LABEL_SCALE = 0.028


def _font(width: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A label font sized for this chart, falling back on old Pillow."""
    try:
        return ImageFont.load_default(size=round(_LABEL_SCALE * width))
    except (TypeError, AttributeError):  # pragma: no cover — Pillow < 10.1
        return ImageFont.load_default()


def fit_size(box: tuple[int, int]) -> tuple[int, int]:
    """The largest chart of the right shape that fits a box, in pixels."""
    box_w, box_h = box
    scale = min(box_w / CANVAS_W, box_h / CANVAS_H)
    return max(1, round(CANVAS_W * scale)), max(1, round(CANVAS_H * scale))


def chart_page(
    cfg: Config,
    correction: Correction | None = None,
    *,
    slot: Slot | None = None,
    grid: tuple[int, int] = GRID,
    label: str = "",
) -> Image.Image:
    """A full print page with the chart in ``slot`` and every other slot blank.

    That is the whole trick behind iterating on one sheet: print this, then feed
    the same paper back in and print the next round into the next slot.
    """
    from proxdex.sheet import blank_page

    page = blank_page(cfg)
    pw, ph = page.size
    x0, y0, x1, y1 = _cell(cfg, slot or FIRST_SLOT, grid)
    inset_x = (x1 - x0) * _CELL_INSET * pw
    inset_y = (y1 - y0) * _CELL_INSET * ph
    box_w = round((x1 - x0) * pw - 2 * inset_x)
    box_h = round((y1 - y0) * ph - 2 * inset_y)
    chart = render_chart(correction, label, fit_size((box_w, box_h)))
    page.paste(
        chart,
        (
            round(x0 * pw + inset_x + (box_w - chart.width) / 2),
            round(y0 * ph + inset_y + (box_h - chart.height) / 2),
        ),
    )
    return page


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
            raise ProxdexError(
                "couldn't find a corner fiducial. Scan the whole page with "
                "auto-correction off and pass the slot you printed in, or crop to "
                "just the one chart and pass --whole."
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


def sample_patches(arr: RGB, params: NDArray[np.float32]) -> Patches:
    h, w, _ = arr.shape
    measured = np.zeros((len(chart_patches()), 3), np.float32)
    r = max(3, int(0.01 * min(h, w)))
    for i, (cx, cy) in enumerate(_patch_centers()):
        x, y = np.array([cx, cy, 1.0], np.float32) @ params
        xi, yi = round(float(x)), round(float(y))
        patch = arr[max(0, yi - r) : yi + r, max(0, xi - r) : xi + r]
        measured[i] = np.median(patch.reshape(-1, 3), axis=0)
    return measured


def crop_slot(arr: RGB, cfg: Config, slot: Slot, grid: tuple[int, int] = GRID) -> RGB:
    """The part of a whole-page scan holding one slot's chart.

    The scan is assumed to be the page — which a flatbed gives you — and the
    crop is padded by less than the chart's own inset, so any slack lands on
    blank paper instead of in the next chart along.
    """
    h, w, _ = arr.shape
    x0, y0, x1, y1 = _cell(cfg, slot, grid)
    pad_x = (x1 - x0) * _CROP_PAD
    pad_y = (y1 - y0) * _CROP_PAD
    left = max(0, round((x0 + pad_x) * w))
    top = max(0, round((y0 + pad_y) * h))
    right = min(w, round((x1 - pad_x) * w))
    bottom = min(h, round((y1 - pad_y) * h))
    if right - left < _MIN_CROP_PX or bottom - top < _MIN_CROP_PX:
        raise ProxdexError(f"slot {slot.text} is not inside this scan")
    return arr[top:bottom, left:right]


#: one proof patch: wide and shallow, so a pair reads as one swatch split in two
#: and 36 pairs fit a shape you can look at without scrolling
_PROOF_W, _PROOF_H = 120, 27
#: the gap between pairs — the seam inside a pair must be the tighter one, or the
#: eye compares the wrong two colours
_PROOF_GAP = 8


def proof_sheet(scanned: Patches) -> Image.Image:
    """Target above, scanned below, patch by patch.

    A mean error says a print is off; this says *how* — whether the paper is
    running warm, crushing the darks or losing the cyans — which is what decides
    whether another round is worth the paper. The two halves of a pair touch, so
    any difference between them shows as a visible seam.
    """
    goal = target()
    pair_h = _PROOF_H * 2 + _PROOF_GAP
    im = Image.new(
        "RGB",
        (COLS * (_PROOF_W + _PROOF_GAP), ROWS * pair_h),
        (255, 255, 255),
    )
    draw = ImageDraw.Draw(im)
    for i in range(len(goal)):
        col, row = i % COLS, i // COLS
        x = col * (_PROOF_W + _PROOF_GAP)
        y = row * pair_h
        for offset, patch in ((0, goal[i]), (_PROOF_H, scanned[i])):
            fill = tuple(int(v) for v in patch.clip(0, 255).round())
            draw.rectangle(
                [x, y + offset, x + _PROOF_W - 1, y + offset + _PROOF_H - 1], fill=fill
            )
    return im


def read_scan(
    path: Path,
    cfg: Config | None = None,
    *,
    slot: Slot | None = None,
    grid: tuple[int, int] = GRID,
) -> Patches:
    """Read every patch of one chart out of a scan.

    With a ``slot`` (and the config the page was rendered with) the whole page is
    cropped to that slot first; without one the image is taken to be a single
    chart.
    """
    arr = np.asarray(Image.open(path).convert("RGB"), np.float32)
    if slot is not None and cfg is not None:
        arr = crop_slot(arr, cfg, slot, grid)
    return sample_patches(arr, _affine(detect_fiducials(arr)))
