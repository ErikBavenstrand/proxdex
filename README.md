# proxdex

[![CI](https://github.com/ErikBavenstrand/proxdex/actions/workflows/ci.yml/badge.svg)](https://github.com/ErikBavenstrand/proxdex/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/proxdex)](https://pypi.org/project/proxdex/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

The librarian for making Pokémon proxies. proxdex keeps every card's assets in
a predictable place keyed by its **set + collector number** (`ex3-90`), tracks
which pipeline stage each card has reached, corrects thin card frames, and
records what you've actually printed — so a growing collection stays easy to
search, and you never reprint a card you already have.

It uses [**cardbleed**](https://github.com/ErikBavenstrand/cardbleed) (border
extension) and [Upscayl](https://upscayl.org) (upscaling), and imposes the
print sheet itself — so it owns the whole path to paper.

Think of it as **author a master, then reproduce it**: the four card stages
produce a device-independent, trim-size master (what the card *should* look
like); the `sheet` step reproduces it faithfully on a specific printer + medium
(colour-correct + add bleed outside the trim).

## Pipeline

Each card's stored file is the **actual trim-size card** — no bleed. Four
stages, one file per stage:

| # | stage      | produced by                            | command |
|---|------------|----------------------------------------|---------|
| 1 | `original` | downloaded from scrydex                | `search` / `fetch` |
| 2 | `bordered` | thin frame expanded to trim (optional) | `border` |
| 3 | `upscaled` | Upscayl — *after* the border fix       | `upscale` / `import` |
| 4 | `edited`   | normalize + uniform look — the **master** | `grade` |

`build` runs 2→4 in one go. **Cut bleed and medium colour-correction are not
baked into the card** — they're applied at `sheet` time, extended *outside* the
trim, so the master stays a clean, resizable, device-neutral card. **Where do
effects go?** Author the look in `grade` (after upscaling, WYSIWYG); frame
expansion goes *before* upscale in `border`; bleed + medium reproduction happen
in `sheet`. See [uniform prints](#uniform-prints).

## Layout on disk

```
<library>/
├── proxdex.toml                     # config + library marker
├── INDEX.md                         # generated: search hub + print status
├── back.png                         # shared card back (optional; trim-size)
├── cards/
│   └── ex3-dragon/
│       └── ex3-90_dragonite-ex/
│           ├── ex3-90_1_original.png
│           ├── ex3-90_2_bordered.png   # only if the frame needed expanding
│           ├── ex3-90_3_upscaled.png
│           └── ex3-90_4_edited.png     # trim-size master (no bleed)
└── print-batches/
    └── 2026-07-18_dark-deck/
        ├── fronts.pdf
        └── batch.toml               # cards, printed?, paper/printer, notes
```

## Install

```bash
uv tool install proxdex        # global CLI in ~/.local/bin  (or: pip install proxdex)
uv tool install "proxdex[ui]"  # + the local web UI (`proxdex ui`)
uv tool install .              # from a local checkout
uv tool upgrade proxdex        # later
```

`cardbleed` ships as a dependency, so it's bundled in proxdex's own venv — no
separate install, and proxdex finds it there even though it isn't on your PATH.

### Library vs. tool

The **tool** is installed once; your **library** (cards, config, batches) is
just a folder. proxdex locates it, git-style:

1. `--root DIR`, else
2. the nearest `proxdex.toml` searching up from the current directory, else
3. `$PROXDEX_ROOT` (set this in your shell profile to run from anywhere):

```bash
export PROXDEX_ROOT=~/Documents/Pokémon\ Proxies
proxdex where     # confirm which library + config is active
```

Config lives in `<library>/proxdex.toml` (created by `init`), so it travels
with the data and each library can differ. New config keys added by a tool
upgrade fall back to defaults, so old libraries keep working. `INDEX.md` is
**regenerated automatically** after any command that changes state (no need to
run `index` by hand).

## Usage

```bash
cd ~/Documents/Pokémon\ Proxies
proxdex init                   # one-time: create the library here

proxdex search entei ex        # find a card by name, pick which print to fetch
proxdex fetch ex3-90 ex6-105   # or download directly by id

proxdex build                  # border → upscale → grade, for cards that need it
proxdex sheet dark-deck        # colour-correct + bleed + impose → print PDF + batch
#   ...print the PDF (colour management OFF), then:
proxdex printed dark-deck      # mark the batch printed

proxdex ls                     # every card, stage progress, printed?
```

That's the whole loop: **search → build → sheet → printed.** The individual
stage commands (`border`, `upscale`, `grade`) exist for granular control, and
`import` files loose images (Upscayl-GUI output, or `--id` an arbitrary scan):

```bash
proxdex import ~/upscaled/*.png         # ex*_upscayl_*.png → stage 2
proxdex import scan.png --id ex6-105    # arbitrary file → looks up + files it
```

Commands accept card ids to scope them (`proxdex build ex6-105`); with none,
they act on the whole library. `proxdex` searches up from the current
directory for `proxdex.toml`, or pass `--root DIR`.

### Web UI

Prefer clicking to typing? `proxdex ui` (needs the `[ui]` extra) starts a local
server and opens a browser with **full parity to the CLI** — nothing leaves your
machine (localhost only):

- **Library gallery** — thumbnails + stage badges (`O B U E`) + print status.
- **Card detail** — flip through the library (`←`/`→`), see every stage's
  transformation side by side, and **rerun any step on that card** with its
  own options (upscale model/scale/double, grade normalize, border), measure
  the frame, or delete it.
- **Search** — query the TCG API with filters, preview art, and fetch selected.
- **Settings** — edit every `proxdex.toml` knob (comment-preserving), set the
  card back (tcg/url/upload), and run the calibration loop (download chart,
  upload scan, fit/check).
- **Build / Make sheet / mark printed / regenerate index** from the toolbar.

```bash
proxdex ui                 # → http://127.0.0.1:8756
```

### Print sheet

`proxdex sheet <name> [ids...]` colour-corrects each master, extends cut bleed
outside the trim (cardbleed), imposes onto pages, and writes
`print-batches/<date>_<name>/<faces>.pdf` plus a manifest. proxdex renders the
PDF itself, so the print path is fully determined — **print with your printer's
colour management OFF** so a calibration holds.

- **Any input size → exact card size.** Whatever resolution a card is, it's
  scaled to the configured card dimensions (`[card]`, default 63×88mm) at sheet
  DPI. `fit = cover` fills the card preserving aspect (matching-aspect cards
  lose nothing); `contain` pads; `stretch` forces it.
- **Fronts, backs, or duplex** (`--faces` or `[sheet] faces`). Duplex emits a
  front page then a **mirrored** back page (`duplex_flip = long|short`), so
  double-siding lines up. Backs come from a shared `[sheet] back_image` or a
  per-card `<id>_back.png`.
- **Offsets** nudge the whole image (mm): `front_offset_*` and, crucially for
  duplex registration, `back_offset_*` (e.g. `0.4, 0.35`).
- **Cut guides**: `guide_style` = `full` (grid lines) / `corners` (crop marks)
  / `none`, with `placement`, length, `color`, width, and independent
  `guides_front` / `guides_back` (cut from the front, so backs default off).
  Optional printer `reg_marks`. All under `[sheet]`.

The PDF is **lossless** (Flate-embedded, never JPEG) and rendered at
`[sheet] dpi` (default 1400, `--dpi` to override) so the printer never
upsamples; only one page raster is held in memory at a time.

### Card backs

`proxdex back` sets the shared back used by `sheet --faces backs|duplex`:

```bash
proxdex back --tcg mtg              # Scryfall's standard MTG back
proxdex back --file my-back.png     # your own scan (any TCG)
proxdex back --url https://…/back.png
```

It runs the back through the **same medium colour-correction as the fronts**
and adds bleed with cardbleed, then saves `back.png` (auto-used by `sheet`).
Per-card backs: drop `<id>_back.png` in a card's folder. Note: there's **no
reliable Pokémon-back API** (the back is one image, owned by TPC) — supply your
own high-res scan via `--file`/`--url`; MTG has a clean source via Scryfall.

### Finding cards

Don't know the id? `proxdex search` queries the TCG API by name — every word
must appear in the card name — and shows each match's set, release year,
collector number, rarity and artist so you can tell prints apart:

```
$ proxdex search entei ex
#  ID       Name            Set                       Year     No.  Rarity        Artist
1  ex4-91   Entei ex        Team Magma vs Team Aqua   2004   91/95  Rare Holo EX  Ryo Ueda
2  ex7-97   Rocket's Entei  Team Rocket Returns       2004  97/109  Rare Holo EX  Ryo Ueda
3  bw5-13   Entei-EX        Dark Explorers            2012  13/108  Rare Holo EX  Shizurow
Fetch which? [numbers/ranges/ids · 'all' · blank to cancel]: 1
```

Type `1`, `1,3`, `1-3`, an id, or `all`. Narrow with `--set base1`,
`--rarity holo`, `--year 2004`; skip the prompt with `--select 1,3` or
`--fetch`; add `--open` to preview result images in your browser.

### Upscaling

`proxdex upscale` drives Upscayl's engine (`upscayl-bin`) directly — no GUI
round-trip — and mirrors the app's own options: any of the seven built-in
models, an output scale, and optional **Double Upscayl** (runs the model twice,
so 2× doubled = 4×, up to 16×). The command construction matches the app
exactly, including only passing `-s` when the scale differs from the model's
native 4×. On macOS the bundled binary and models are auto-detected inside
`Upscayl.app`; elsewhere set the paths under `[tools]`.

Set defaults once in `proxdex.toml`:

```toml
[tools]
upscayl_model  = "digital-art-4x"  # + upscayl-standard-4x, upscayl-lite-4x, high-fidelity-4x,
                                   #   remacri-4x, ultramix-balanced-4x, ultrasharp-4x
upscayl_scale  = 2                 # 1, 2, 3, or 4
upscayl_double = true              # run the model twice (default on → 2× becomes 4×)
```

Override per run: `proxdex upscale --model ultrasharp-4x --scale 4 --double`.
Prefer the GUI? Skip this step and `proxdex import` its output instead.

## Border correction (frame expansion)

Real cards have a uniform frame on the top and both sides and a **thicker
bottom** (set symbol, ©). Some scrydex scans are cut into the frame, so the
card's own border is too thin. `proxdex measure` reports the top/side thickness
(the bottom is never measured); `proxdex border` expands a too-thin frame up to
the target — **before upscaling** — using cardbleed to continue the existing
pattern rather than smear pixels. Cards already at size are left untouched
(within `tolerance_mm`). This is *frame* correction, distinct from *cut bleed*
(which `sheet` adds outside the trim).

By default the target is symmetric (pad thin edges up to the sides). Once
you've eyeballed a known-good card, set a real-card ratio in `proxdex.toml`:

```toml
[border]
target_side_ratio = 0.045   # side frame ≈ 4.5% of card width
```

## Uniform prints

A mixed collection — crisp digital art next to warm, flat scans — won't print
uniformly if you just apply the same multipliers to everything, because each
card starts from a different place. So `grade` works in two steps:

1. **normalize (per card, dynamic)** — white-balances the shared card frame to
   one target colour and evens out black/white points, so every card lands on
   the same baseline regardless of how it was made. The target defaults to the
   library's *own median frame colour*, so the collection converges on its own
   consensus; pin it if you prefer.
2. **look (uniform)** — one identical recipe on top. Because the baseline is now
   shared, your intended saturation lands the same way on every card.

```toml
[grade]
normalize = true          # step 1
match_border_target = []  # [] = library median; or pin e.g. [252, 214, 46]
saturation = 1.10         # step 2 — the intended look
contrast   = 1.06
brightness = 1.03         # printers + matte paper dull the image
```

Calibrate the look with a test strip: print one sheet, compare to screen, nudge
the numbers, reprint. Run `proxdex grade --no-normalize` to apply only the
recipe (skip step 1).

## Printing media (washed-out foil)

Some media shift colour — transparent plastic foil especially, where the ink is
semi-transparent so prints come out **lighter and less saturated** than the
screen. `sheet` applies a **media profile** at print time to cancel that, while
your `edited` master stays neutral (switch media → just re-run `sheet` with a
different `--profile`, no regrade):

```toml
[print]
profile = "foil"    # "none" | "paper" | "foil"
```

`foil` boosts saturation and ink density (`saturation 1.38, contrast 1.16,
brightness 0.95, gamma 0.88`). These are a solid automatic starting point;
**calibrate once** with a test print and override any value:

```toml
[print]
profile    = "foil"
saturation = 1.45   # push harder if prints still look washed out
gamma      = 0.85
```

Override per run with `proxdex sheet <name> --profile foil`.

## Calibrating to your printer (closed loop)

If you have a scanner, proxdex can *measure* a per-medium correction instead of
guessing at a preset — print a chart, scan it, and it fits the colour transform
that makes prints true to the original. Each medium is its own profile (e.g.
`paper` on white, `foil-holo` for foil on a holographic backing), so they can
carry different corrections.

```bash
proxdex calibrate target --profile foil-holo --pdf  # emit a patch chart (as a PDF)
#   → print it on that medium, scan it (auto-correction OFF), then:
proxdex calibrate fit --profile foil-holo --scan chart_scan.png
#   → measures a degree-2 polynomial correction; `sheet` now applies it.

# verify / iterate:
proxdex calibrate target --profile foil-holo --corrected --pdf   # chart with fix baked in
proxdex calibrate check --scan corrected_scan.png
#   → prints the residual error; reprint & re-fit until it plateaus.
```

`--pdf` sends the chart through the *same* renderer as your card sheets, so the
correction is measured on the exact path it's applied to. Then `proxdex sheet`
applies the measured correction — it supersedes the manual `foil` preset for
that profile.

**Honest limits:** the scanner is the measuring device, so this makes prints
true *as your scanner sees them* — excellent for proxies, but not colorimetric
(that needs a reference target or a spectrophotometer). Some saturated colours
are simply outside a medium's gamut and can't be fully reached. And you **must
turn off the scanner's auto colour/contrast**, or it fights the loop.

## License

MIT
