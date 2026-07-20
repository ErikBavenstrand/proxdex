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

Think of it as **author a master, then reproduce it**: stages 1–3 produce a
device-independent master (what the card *should* look like); stage 4 + the
sheet reproduce that master faithfully on a specific printer + medium.

## Pipeline

Each card moves through four stages, one file per stage in its own folder:

| # | stage      | produced by                         | command |
|---|------------|-------------------------------------|---------|
| 1 | `original` | downloaded from scrydex             | `search` / `fetch` |
| 2 | `upscaled` | Upscayl (its CLI, or the GUI)       | `upscale` / `import` |
| 3 | `edited`   | normalize + uniform look (**master**) | `grade` |
| 4 | `print`    | colour reproduction + cardbleed bleed | `finish` |

`build` runs 2→4 in one go; `sheet` imposes the finished fronts into a print
PDF. **Where do effects go?** Author them in `grade` (stage 3, after upscaling
so it's what-you-see-is-what-prints); device/medium correction happens later in
`finish`. See [uniform prints](#uniform-prints).

## Layout on disk

```
<library>/
├── proxdex.toml                     # config + library marker
├── INDEX.md                         # generated: search hub + print status
├── cards/
│   └── ex3-dragon/
│       └── ex3-90_dragonite-ex/
│           ├── ex3-90_1_original.png
│           ├── ex3-90_2_upscaled.png
│           ├── ex3-90_3_edited.png
│           └── ex3-90_4_print.png
└── print-batches/
    └── 2026-07-18_dark-deck/
        ├── fronts.pdf
        └── batch.toml               # cards, printed?, paper/printer, notes
```

## Install

```bash
uv tool install proxdex        # or: pip install proxdex
```

## Usage

```bash
cd ~/Documents/Pokémon\ Proxies
proxdex init                   # one-time: create the library here

proxdex search entei ex        # find a card by name, pick which print to fetch
proxdex fetch ex3-90 ex6-105   # or download directly by id

proxdex build                  # upscale → grade → finish, for cards that need it
proxdex sheet dark-deck        # impose finished fronts → print PDF + batch manifest
#   ...print the PDF (colour management OFF), then:
proxdex printed dark-deck      # mark the batch printed

proxdex ls                     # every card, stage progress, printed?
```

That's the whole loop: **search → build → sheet → printed.** The individual
stage commands (`upscale`, `grade`, `finish`) exist for granular control, and
`import` files loose images (Upscayl-GUI output, or `--id` an arbitrary scan):

```bash
proxdex import ~/upscaled/*.png         # ex*_upscayl_*.png → stage 2
proxdex import scan.png --id ex6-105    # arbitrary file → looks up + files it
```

Commands accept card ids to scope them (`proxdex build ex6-105`); with none,
they act on the whole library. `proxdex` searches up from the current
directory for `proxdex.toml`, or pass `--root DIR`.

### Print sheet

`proxdex sheet <name> [ids...]` imposes finished cards onto pages and writes
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

## Border correction

Real cards have a uniform frame on the top and both sides and a **thicker
bottom** (set symbol, ©). Some scrydex scans are cut into the frame, so a
printed-and-cut proxy ends up with a border that's too thin. `proxdex measure`
reports the top and side thickness (the bottom is deliberately never measured);
`proxdex finish` computes the per-edge extension — *frame correction + cut
bleed* — and hands it to cardbleed, which continues the existing border pattern
rather than smearing pixels.

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
screen. `border` bakes a **media profile** into the print stage to cancel that,
so stage 4 is print-ready for a medium while your `edited` master stays neutral
(switch media → just re-run `border`, no regrade):

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

Override per run with `proxdex finish --write-print --profile foil`.

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
#   → measures a degree-2 polynomial correction; `finish` now bakes it in.

# verify / iterate:
proxdex calibrate target --profile foil-holo --corrected --pdf   # chart with fix baked in
proxdex calibrate check --scan corrected_scan.png
#   → prints the residual error; reprint & re-fit until it plateaus.
```

`--pdf` sends the chart through the *same* renderer as your card sheets, so the
correction is measured on the exact path it's applied to. Then `proxdex finish`
(and `build`) bakes the measured correction in — it supersedes the manual `foil`
preset for that profile.

**Honest limits:** the scanner is the measuring device, so this makes prints
true *as your scanner sees them* — excellent for proxies, but not colorimetric
(that needs a reference target or a spectrophotometer). Some saturated colours
are simply outside a medium's gamut and can't be fully reached. And you **must
turn off the scanner's auto colour/contrast**, or it fights the loop.

## License

MIT
