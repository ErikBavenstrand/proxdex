# proxdex

[![CI](https://github.com/ErikBavenstrand/proxdex/actions/workflows/ci.yml/badge.svg)](https://github.com/ErikBavenstrand/proxdex/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/proxdex)](https://pypi.org/project/proxdex/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

The librarian for making proxy cards. proxdex keeps every card's assets in a
predictable place keyed by its **set + collector number** (`ex3-90`,
`neo-136`), tracks which pipeline stage each card has reached, corrects thin
card frames, and records what you've actually printed — so a growing
collection stays easy to search, and you never reprint a card you already have.

It handles **Pokémon** (pokemontcg.io + scrydex) and **Magic: The Gathering**
(Scryfall), and one library can hold both — each card records its own game, so
searching, backs and border specs follow the card.

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

| # | stage      | produced by                               | command |
|---|------------|-------------------------------------------|---------|
| 1 | `original` | downloaded from the game's image source   | `search` / `fetch` |
| 2 | `bordered` | thin frame expanded to trim (optional)    | `border` |
| 3 | `upscaled` | Upscayl — *after* the border fix          | `upscale` / `import` |
| 4 | `edited`   | normalize + uniform look — the **master** | `grade` |

**Every step is one you run or skip — nothing is automatic.** Steps 2–4 each
carry their own state: `pending` → `done` (you ran it) or `skipped` (you
decided it isn't needed). Skipping is a real answer: the next step just reads
the previous stage instead, and `sheet` prints the furthest stage that exists.

```bash
proxdex skip upscale ex3-90     # bypass a step: drops its output, marks it skipped
proxdex unskip upscale ex3-90   # back to pending (the output is not restored)
proxdex reset upscale ex3-90    # delete the output and any skip mark
```

Changing an upstream stage removes every output after it — those went stale —
so you can always re-border a card and rebuild from there without stale pixels
surviving.

### Special printings

Almost every card is one picture on one side of one 63×88mm card. Providers name
two dozen *layouts* — saga, adventure, prototype, leveler, battle — but nearly all
of them differ in rules text, not in ink, so proxdex records only what changes
what goes on paper: **one side, two sides, or half of a meld pair**, plus whether
the card is **oversized**. `proxdex ls` shows it in a Kind column, `proxdex show`
spells it out, and the UI badges it on the tile and the card page.

#### Two sides

A Magic transform, modal, reversible or art-series card is **one card with two
sides**, and `fetch` downloads both. Each side runs the pipeline on its own — a back face is a
different picture and needs its own border fit — so every step, skip and reset
takes `--face 1` (front) or `--face 2` (back). Without it they act on both.

Which side goes on the *paper* is your call, because a transform card has two
real fronts and no back of its own:

```bash
proxdex flip isd-51             # swap which side prints on the front of a sheet
proxdex flip isd-51 --face 2    # or name it outright
```

`sheet --faces duplex` then prints that card's **other side on the reverse**
instead of the shared card back. A one-sided card still takes the configured
back. On a fronts-only sheet only the flipped-to side is imposed, and `sheet`
says so.

#### Meld

A meld pair is **three physical cards**: both halves and the melded card, each
with its own id and its own picture. So proxdex files them as three cards and
records the relationship rather than pretending one card has three sides —
`fetch --related` follows the provider's own links and gets the lot:

```bash
proxdex fetch --related inr-14
#   ↳ melds into Brisela, Voice of Nightmares (inr-14b)
#   ↳ melds with Gisela, the Broken Blade (inr-24)
```

The same flag picks up the tokens a card makes. `proxdex show <id>` lists what a
card is printed alongside and which of those you already have; the card page in
the UI shows the same list with an Add button per row.

#### Oversized

Planar, scheme and Vanguard cards are printed at 89×127mm, not 63×88. proxdex
records that at fetch time and **imposes each card at its own size** — no flag, no
config. Cards of one size share pages; a size that isn't the configured trim gets
pages of its own, with as many cells as the page actually holds:

```
$ proxdex sheet deck
⬗ 1 oversized card(s) — oafr-21 — print at 88.9×127mm on their own pages (2×2 per page)
✓ 4 cards (fronts) → 2 page(s) @ 1400dpi → print-batches/…/fronts.pdf
```

The configured trim keeps the configured `cols`/`rows` exactly as before, so a
library of ordinary cards sees the layout it always did. A card's size is never
silently changed to fit the sheet.

**Cut bleed and medium colour-correction are not baked into the card** — they're
applied at `sheet` time, extended *outside* the trim, so the master stays a
clean, resizable, device-neutral card. **Where do effects go?** Author the look
in `grade` (after upscaling, WYSIWYG); frame expansion goes *before* upscale in
`border`; bleed + medium reproduction happen in `sheet`. See
[uniform prints](#uniform-prints).

## Layout on disk

```
<library>/
├── proxdex.toml                     # config + library marker
├── INDEX.md                         # generated: search hub + print status
├── back-mtg.png                     # shared card back, per game (optional)
├── cards/
│   ├── ex3-dragon/
│   │   ├── .game                    # "pokemon" — set codes alone can't say
│   │   └── ex3-90_dragonite-ex/
│   │       ├── .game
│   │       ├── .skip-upscaled       # this step was deliberately bypassed
│   │       ├── ex3-90_1_original.png
│   │       ├── ex3-90_2_bordered.png   # only if the frame needed expanding
│   │       ├── ex3-90_3_upscaled.png
│   │       └── ex3-90_4_edited.png     # trim-size master (no bleed)
│   └── isd-innistrad/
│       ├── .game                    # "mtg"
│       └── isd-51_delver-of-secrets-insectile-aberration/   # two-sided
│           ├── .faces               # the side names, front first
│           ├── .front               # which side prints on the front (default 1)
│           ├── .skip-bordered_f2    # per-side state, like everything else
│           ├── isd-51_1_original.png       # side 1 keeps the plain names
│           └── isd-51_1_original_f2.png    # side 2 carries the _f2 suffix
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

1. `--root DIR` (accepted before *or* after the command), else
2. the nearest `proxdex.toml` searching up from the current directory, else
3. `$PROXDEX_ROOT` (set this in your shell profile to run from anywhere):

```bash
export PROXDEX_ROOT=~/Documents/Proxies
proxdex where     # confirm which library, config and default game are active
```

Config lives in `<library>/proxdex.toml` (created by `init`), so it travels
with the data and each library can differ. New config keys added by a tool
upgrade fall back to defaults, so old libraries keep working. `INDEX.md` is
**regenerated automatically** after any command that changes state (no need to
run `index` by hand).

## Usage

```bash
cd ~/Documents/Proxies
proxdex init                   # one-time: create the library here

proxdex search entei ex        # find a card by name, pick which print to fetch
proxdex fetch ex3-90 ex6-105   # or download directly by id

proxdex border ex3-90 --inner-top .04 --inner-right .05 \
                      --inner-bottom .04 --inner-left .05   # reshape to spec
proxdex upscale ex3-90         # Upscayl
proxdex grade ex3-90           # normalize + uniform look → the master

proxdex sheet dark-deck        # colour-correct + bleed + impose → print PDF + batch
#   ...print the PDF (colour management OFF), then:
proxdex printed dark-deck      # mark the batch printed

proxdex ls                     # every card, side, stage progress, printed?
proxdex ls --only ready --sort recent   # same filters the contact sheet offers
proxdex show ex3-90            # everything the card's API says, plus local state
```

Two-sided cards read the same way, one row per side, with `↑` on the side that
prints on the front:

```bash
proxdex fetch isd-51           # downloads both sides
proxdex border isd-51 --face 2 --inner-top .04 --inner-right .05 \
                               --inner-bottom .04 --inner-left .05
proxdex flip isd-51            # print the other side on the front
proxdex sheet dfc --faces duplex   # its reverse prints on the back
```

That's the loop: **search → prepare (per step) → sheet → printed.** Any of
`border`, `upscale` and `grade` can be skipped instead of run — a card is ready
to impose once `grade` is settled either way.

`import` files loose images (Upscayl-GUI output, or `--id` an arbitrary scan):

```bash
proxdex import ~/upscaled/*.png         # ex*_upscayl_*.png → the upscaled stage
proxdex import scan.png --id ex6-105    # arbitrary file → looks up + files it
```

Commands accept card ids to scope them (`proxdex upscale ex6-105`); with none,
they act on the whole library. `proxdex` searches up from the current
directory for `proxdex.toml`, or pass `--root DIR`.

Everything the web UI can do has a verb here, and the reverse — settings
included:

```bash
proxdex config show dpi        # every setting with its meaning and default
proxdex config set sheet.dpi=1200 sheet.faces=duplex   # comment-preserving
proxdex batches                # what has been imposed, and what is printed
proxdex rm ex3-90              # delete a card (asks first)
proxdex ls --json              # the same shape /api/cards serves the UI
```

### Two games in one library

Each card records its game in a `.game` file next to its images, so a mixed
library just works — `ls` shows it, backs and frame specs follow it. `--game`
picks which TCG a command means; without it, `[library] game` from
`proxdex.toml` is the default (and `fetch` falls back to trying the others).

```bash
proxdex search --game mtg delver of secrets --set isd
proxdex fetch neo-136              # tries the default game, then the rest
proxdex fetch --game mtg 4ed-100   # or say it outright
```

| | Pokémon | Magic: The Gathering |
|---|---|---|
| id | `ex3-90` | `neo-136` |
| metadata | pokemontcg.io | Scryfall |
| image | scrydex | Scryfall PNG (745×1040) |
| card back | no API — supply a scan | Scryfall's standard back |
| frame spec | per era (WOTC measured) | one spec, uniform on all edges |
| borderless | not exposed by the API | detected from the printing |
| sides | always one | one or two (transform, modal, reversible) |
| related cards | — | meld halves + result, tokens (`fetch --related`) |

### Web UI

Prefer clicking to typing? `proxdex ui` (needs the `[ui]` extra) starts a local
server and opens a browser with **full parity to the CLI** — nothing leaves your
machine (localhost only):

- **Contact sheet** — every card as a thumbnail with its game, stage strip and
  print status. Filter by name, game, set or state, sort, change tile size, and
  select cards to run or skip one step across all of them. A header strip counts
  where the whole library stands per stage, and how much is ready to print.
- **Card console** — the pipeline as a **filmstrip**: one frame per stage,
  carrying that stage's actual image, so the rail is the card's own history.
  Pick a frame and the panel beside it holds that step's settings — always, from
  the moment you focus it, with no mode to enter — plus Run / Skip / Reset.
  A step that has run shows its **output**, with a compare tool above the card:
  `Result` on its own (the default), `Wipe` to drag a split between the step's
  input and output, or `Fade` to cross-dissolve them on a slider — one at a time.
  A step that hasn't run shows its input at **full colour**, with the fact stated
  in the chrome around the card rather than dimmed over it. Two-sided cards get a
  side tab strip and a control for which side prints on the front.
  `←`/`→` cards, `↑`/`↓` steps, `F` flips side, `C` cycles compare.
- **Border tool** — the four align marks are live whenever Border is focused, and
  they land on a **measurement of the card's own border** the first time you open
  the tool, with a chip saying whether every edge read cleanly (Measure it does it
  again on demand). Drag one and a **loupe** shows the source pixels at 6× with
  the mark on a crosshair; arrow keys nudge it, or type the inset as a percentage.
  A cyan ghost
  outlines the trim the fit will produce and shades the border it is aiming at,
  so you can see the result before running it. The frame spec — and how much it
  is trusted — is a setting like any other.
- **Search** — pick a game, query with filters, preview art, add selected, with
  or without the cards they're printed alongside.
- **Card data sheet** — every field the provider returns, its outbound links, and
  what the card is **printed alongside** — a meld partner, the melded card, the
  tokens it makes — each addable in one click.
- **Settings** — a real form: every setting carries its label, explanation, unit
  and default, all read from the code itself, with the raw TOML key kept visible.
  Nothing is written until you save, and the save bar only exists while something
  differs. Card backs, the frame specs (and which of your sets ride on an
  estimate) and the calibration loop live here too.
- **Make sheet** — the whole library or just what you selected, with a per-card
  side choice for two-sided cards, made where the consequence is. If the batch
  mixes card sizes it says so up front, with how many of each fit a page.
  **Mark printed** and **rebuild index** from the toolbar.

`?` shows every keyboard shortcut; `G` then `L`/`S`/`,` jumps between screens.

Every screen has its own URL — `/library`, `/card/ex3-90/upscale`,
`/card/isd-51/border?side=2`, `/search?q=charizard`, `/settings` — so **back and
forward work**, a card (and a side) is bookmarkable, and a reload lands where you
were (scroll included). Switching views does no round-trip at all: the library,
search results and settings are held client-side, and images are served under a
version stamp so the browser caches them permanently and never refetches one it
already has.

The whole pipeline — its order, each step's label and skippability, and each
step's settings schema with this library's defaults — is served from Python. The
UI renders its stepper and its control panels from that and spells no step name
or option of its own, so a step added in the code appears in the browser with its
controls already built.

The UI bundles its own component library (Bootstrap, MIT), so it never reaches
out to a CDN — everything is served from localhost.

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
  scaled to **its own** physical size at sheet DPI — the configured dimensions
  (`[card]`, default 63×88mm) for an ordinary card, 89×127mm for an oversized one.
  `fit = cover` fills the card preserving aspect (matching-aspect cards lose
  nothing); `contain` pads; `stretch` forces it.
- **Mixed sizes get their own pages.** Cells are grouped by trim size, and each
  group is imposed with its own grid, so an oversized card never has to share a
  63×88 cell. Duplex mirroring happens within each group, so backs still land
  behind their fronts.
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

`proxdex back` sets the shared back used by `sheet --faces backs|duplex`.
Backs are **per game** — a mixed library needs both — and each card picks its
own, so a duplex sheet of Pokémon and MTG cards comes out right:

```bash
proxdex back --game mtg                        # Scryfall's standard MTG back
proxdex back --game pokemon --file my-back.png # your own scan
proxdex back --game mtg --url https://…/back.png
```

The file lands at `back-<game>.png` and is applied at `sheet` time through the
**same medium colour-correction and bleed as the fronts**. Per-card backs: drop
`<id>_back.png` in a card's folder. Note: there's **no reliable Pokémon-back
API** (the back is one image, owned by TPC) — supply your own high-res scan.

### Finding cards

Don't know the id? `proxdex search` queries the game's API by name — every word
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
upscayl_scale  = 2                 # 1 | 2 | 3 | 4
upscayl_double = true              # run the model twice (default on → 2× becomes 4×)
```

Every one of these is a closed set — model, scale and double are validated in
the config, on the CLI and in the web UI alike, so a typo names the valid
options at load instead of failing later inside `upscayl-bin`. (The flip side:
custom `.param` models are not selectable.)

Override per run: `proxdex upscale --model ultrasharp-4x --scale 4 --double`.
Prefer the GUI? Skip this step and `proxdex import` its output instead.

## Border correction (frame expansion)

Some scans are cut into the card's printed frame, so its border is too thin.
`proxdex border` expands the frame up to the real thing — **before upscaling** —
using cardbleed to continue the existing pattern rather than smear pixels. This
is *frame* correction, distinct from *cut bleed* (which `sheet` adds outside
the trim).

You say where the border currently sits with
`--inner-top/-right/-bottom/-left` (fractions of the image; the UI's align tool
does this by dragging), and the card's **frame spec** supplies the target
widths. Add `--stretch` to un-distort the art so the borders land exactly.

`--auto` measures those four numbers off the image instead. The frame is a ring
of nearly one colour, so each edge is scanned inward until the picture stops
matching it, over 64 scan lines per edge. It is a *pre-placement*, not a
decision: every edge reports the share of its lines that agreed, and an edge they
disagreed about is named rather than passed off as measured.

```
$ proxdex border --auto base1-4
  ⌖ base1-4: border ends at T2.18 R2.67 B2.06 L3.17% — every edge measured cleanly.
✓ base1-4: fit → 621×867px  T4.44 R4.48 B4.44 L4.48%  (Pokémon · WOTC vintage)

$ proxdex border --auto --dry-run inr-14
  ⌖ inr-14: border ends at about T3.51 R4.03 B3.56 L3.90%. The top scan lines
    disagreed — a decorated frame or art touching the border — so check that mark.
```

In the UI the marks land on that measurement the first time you open the border
tool on a card, with a chip saying whether it was clean, and you nudge from
there. `--dry-run` measures and writes nothing.

Specs differ by game and era, and proxdex is honest about which it has actually
measured:

```
$ proxdex frames
Spec             Game                  Border T/R/B/L (mm)        Confidence
pokemon-wotc     Pokémon               3.45 / 3.15 / 3.45 / 3.15  measured
pokemon-generic  Pokémon               3.45 / 3.15 / 3.45 / 3.15  estimated
mtg-bordered     Magic: The Gathering  3.00 / 3.00 / 3.00 / 3.00  estimated
borderless       any                   0.00 / 0.00 / 0.00 / 0.00  measured
```

It also lists which specs the sets *in your library* resolve to. A set with no
measured spec still works — the fit just runs against an estimate — and both
the CLI and the UI say so rather than pretending. Pokémon frames have a
**thicker bottom** (set symbol, ©) and change by era; MTG's frame is uniform on
all four edges across every bordered set, so one spec covers it.

A borderless or full-art print has no frame to match, and a modern set mixes
both under one set code — so the *set* can't answer this but the printing can.
proxdex reads it from the provider at fetch time (Scryfall's `border_color` and
`full_art`) and records it in the card's own `.frame` marker, so the border step
reshapes it to the card aspect and nothing else. `proxdex frames` shows which
cards resolve that way, and `--frame` still overrides by hand:

```bash
proxdex border neo-136 --frame borderless --auto
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
