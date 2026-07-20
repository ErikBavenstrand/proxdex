# proxdex

[![CI](https://github.com/ErikBavenstrand/proxdex/actions/workflows/ci.yml/badge.svg)](https://github.com/ErikBavenstrand/proxdex/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/proxdex)](https://pypi.org/project/proxdex/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

The librarian for making Pokémon proxies. proxdex keeps every card's assets in
a predictable place keyed by its **set + collector number** (`ex3-90`), tracks
which pipeline stage each card has reached, corrects thin card frames, and
records what you've actually printed — so a growing collection stays easy to
search, and you never reprint a card you already have.

It's the glue around the tools that do the heavy lifting:
[**cardbleed**](https://github.com/ErikBavenstrand/cardbleed) (border
extension) and **paperloom** (print-sheet layout), plus
[Upscayl](https://upscayl.org) for upscaling.

## Pipeline

Each card moves through four stages, one file per stage in its own folder:

| # | stage      | produced by                    | proxdex command  |
|---|------------|--------------------------------|------------------|
| 1 | `original` | downloaded from scrydex        | `proxdex fetch`  |
| 2 | `upscaled` | Upscayl                        | `proxdex import` |
| 3 | `edited`   | uniform saturation/contrast    | `proxdex grade`  |
| 4 | `print`    | frame-corrected + cardbleed    | `proxdex border` |

> **Where do effects go?** Grade at **stage 3 — after upscaling, before
> cardbleed.** Upscayl's models shift contrast and saturation themselves, so
> grading the upscaled pixels is what-you-see-is-what-prints; and grading
> before the border step means the extended border inherits the corrected
> colour. See [uniform prints](#uniform-prints).

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

proxdex fetch ex3-90 ex6-105   # download originals + look up names/sets
# ...upscale with Upscayl into some folder, then:
proxdex import ~/upscaled/*.png   # files ex*_upscayl_*.png as stage 2

proxdex grade                  # stage 3 for every card (uniform recipe)
proxdex measure                # how thin is each frame? what needs extending?
proxdex border --write-print   # stage 4 via cardbleed, per-edge

proxdex ls                     # every card, stage progress, printed?
proxdex index                  # regenerate INDEX.md
```

Commands accept card ids to scope them (`proxdex grade ex6-105`); with none,
they act on the whole library. `proxdex` searches up from the current
directory for `proxdex.toml`, or pass `--root DIR`.

## Border correction

Real cards have a uniform frame on the top and both sides and a **thicker
bottom** (set symbol, ©). Some scrydex scans are cut into the frame, so a
printed-and-cut proxy ends up with a border that's too thin. `proxdex measure`
reports the top and side thickness (the bottom is deliberately never measured);
`proxdex border` computes the per-edge extension — *frame correction + cut
bleed* — and hands it to cardbleed, which continues the existing border pattern
rather than smearing pixels.

By default the target is symmetric (pad thin edges up to the sides). Once
you've eyeballed a known-good card, set a real-card ratio in `proxdex.toml`:

```toml
[border]
target_side_ratio = 0.045   # side frame ≈ 4.5% of card width
```

## Uniform prints

Uniformity comes from applying the **same** numeric grade to every card, not
from eyeballing each one. Tune the recipe once in `proxdex.toml`:

```toml
[grade]
brightness = 1.03    # printers + matte paper dull the image
contrast   = 1.06
saturation = 1.10
```

Calibrate it with a test strip: print one sheet, compare to screen, nudge the
numbers, reprint. For cross-card consistency you can also normalize every
card's frame to a single colour (all yellows print identically):

```toml
[grade]
match_border_target = [252, 214, 46]
```

## License

MIT
