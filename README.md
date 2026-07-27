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
| 4 | `edited`   | one uniform look — the **master**         | `grade` |

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
├── profiles/
│   └── matte-200.json               # one print medium: your notes, its recipe,
│                                    # and every calibration round measured on it
└── print-batches/
    └── 2026-07-18_dark-deck/
        ├── fronts.pdf
        └── batch.toml               # cards + copies, the profile and page
                                     # settings used, printed?, notes
```

## Install

```bash
uv tool install proxdex        # global CLI in ~/.local/bin  (or: pip install proxdex)
uv tool install "proxdex[ui]"  # + the local web UI (`proxdex ui`)
uv tool install .              # from a local checkout
uv tool upgrade proxdex        # later
```

**macOS, Linux and Windows**, on Python 3.11–3.13. CI runs the suite *and* a real
library end to end — init, import, border fit, imposed PDF — on all three, because
the defects that only appear on one platform live in that path and not in a unit
test. One caveat, inherited rather than ours: on **Linux arm64** (a Pi, Graviton,
Docker on Apple Silicon) `cardbleed`'s `jpeglib` dependency has no prebuilt wheel,
so `apt install build-essential` first. x86_64 Linux, macOS and Windows all
install from wheels with no compiler.

`cardbleed` ships as a dependency, so it's bundled in proxdex's own venv — no
separate install, and proxdex finds it there even though it isn't on your PATH.
It is pinned `>=0.4.1`: cardbleed runs *inside* proxdex's process and prints to
proxdex's stdout, and 0.4.1 is the first release whose own output survives a
stream that cannot encode it.

### Upscayl is installed separately, and cannot be otherwise

Everything proxdex needs comes with it — **except the upscaler**. That one is a
desktop application, and no `pip`/`uv` install can supply it:

- **Upscayl is not a Python package.** It is an Electron app whose engine,
  `upscayl-bin`, is a native Vulkan binary. It is not published on PyPI, so there
  is nothing for an extra to depend on. (There *is* an `upscayl` name on PyPI —
  version `0.0.0a1`, described as "A small example package". It is unrelated.
  proxdex does not depend on it and neither should you.)
- **So there is no `proxdex[upscale]` extra**, deliberately. An extra can only
  pull Python wheels, and the upscale step has no Python dependencies at all — it
  runs a binary. An extra here would install nothing and mean nothing.

Install it the way you install applications:

```bash
brew install --cask upscayl      # macOS
# or download from https://upscayl.org — macOS, Windows (installer or portable
# zip), Linux (AppImage, deb, rpm)
```

proxdex then finds it by itself on all three platforms, in Upscayl's own install
layout — the engine at `resources/bin`, the models at `resources/models`:

| | looked for at |
|---|---|
| macOS | `/Applications/Upscayl.app/Contents/Resources/…` (and `~/Applications`) |
| Windows | `%ProgramFiles%\Upscayl\resources\…`, and `%LOCALAPPDATA%\Programs\Upscayl\…` for a per-user install |
| Linux | `/opt/Upscayl/resources/…` (the `.deb` and `.rpm` both land there) |

Some installs have no fixed home and cannot be guessed: Upscayl's Windows
installer lets you choose the directory, its portable zip unpacks wherever you
put it, and a Linux **AppImage** runs from a temporary mount. Point at those:

```toml
[tools]
upscayl_bin    = "D:/Apps/Upscayl/resources/bin/upscayl-bin.exe"
upscayl_models = "D:/Apps/Upscayl/resources/models"
```

Keep the binary *in its own folder* — on Windows it needs the `vcomp140.dll`
shipped beside it, so copying just the `.exe` out will not work.

`proxdex where` tells you what this machine has, and when it finds nothing it
says where it looked, which is usually the answer:

```
upscaler  upscayl ✓ /Applications/Upscayl.app/Contents/Resources/bin/upscayl-bin
```

**Without it, proxdex still works.** The upscale step is the only thing affected,
and it is affected honestly: `proxdex upscale` refuses up front with what to
install (not per card, halfway through a batch), and in the web UI its Run button
is disabled with the same sentence and the Skip button beside it. Nothing else
changes — the *stage* still exists, an upscaled image a card already holds is
still shown and still printed, and `proxdex skip upscale` is a first-class choice
that leaves the earlier stage as the master.

Internally the step talks to an `Upscaler` backend rather than to Upscayl by name
(`upscale.BACKENDS`), so a second engine is one class and one registry entry —
including, if one ever ships wheels for every Python proxdex supports, a
pip-installable one. Today there is exactly one backend, and it is Upscayl.

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
proxdex grade ex3-90           # the uniform look → the trim master

proxdex sheet dark-deck        # correct for the medium + bleed + impose → PDF + batch
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

`import` files loose images (Upscayl-GUI output, or `--id` an arbitrary scan).
Where each file lands is read off its name — the card id it starts with, the
stage (`upscayl` → upscaled), the side (`_f2`) — and `--dry-run` shows the whole
plan before a byte moves:

```bash
proxdex import ~/upscaled/*.png --dry-run   # what each file would become
proxdex import ~/upscaled/*.png             # ex*_upscayl_*.png → the upscaled stage
proxdex import scan.png --id ex6-105        # arbitrary file → looks up + files it
proxdex import ~/dump/*.png --on-existing skip   # keep the stages already there
```

The plan names what it replaces, what two files would collide over, and which
later stages go stale — and it is the same plan the web UI's **import wizard**
shows, so the preview and the import cannot disagree.

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
proxdex doctor                 # stored images that aren't what proxdex writes now
```

### `doctor` — the files you already have

proxdex learns things about how a stage image has to be *stored*, and a library
filled last year does not benefit from a fix applied at the front door. `doctor`
reads the header of every stored image and names what a current proxdex would have
written differently: a die-cut corner left transparent (it prints as whatever was
under the alpha), a grayscale or CMYK file, an unreadable file, or a bordered
master that is not the trim aspect — which `sheet` crops to fit, losing border off
two edges without saying so. Nothing about any of them is visible on screen.

```bash
proxdex doctor                 # report only; reads headers, writes nothing
proxdex doctor --fix           # repair what is a repair, in place (asks first)
proxdex doctor ex3-90          # or scope it to some cards
```

A repair rewrites only the file it names and leaves every later stage alone — the
picture does not change, so nothing derived from it went stale. A wrong aspect is
*not* repaired: re-fitting a border needs to know where the border is, which is a
decision, so `doctor` names the step to re-run instead. The same check is the
settings screen's **stored images** panel in the web UI.

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
  or without the cards they're printed alongside. Hover a hit for `full ↗` — the
  provider's own scan at full size, for telling two prints apart.
- **Import wizard** — drop a folder of scans or an Upscayl output and review it
  one row per file before anything is sent: the card it goes to, the stage, the
  side, and what it would do — a new stage, a replacement, a slot two files want,
  a name with no id in it. The review is `import --dry-run` computed on the
  server from the **filenames alone**, so a two-hundred-file folder previews
  without uploading a byte; the thumbnails are the browser's own copies. A row
  with no id gets a **Find…** search to name the card, `already there` chooses
  replace-or-keep for the run, and the files then go one request at a time so a
  failure belongs to one named file instead of the folder.
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
  mixes card sizes it says so up front, with how many of each fit a page. The
  PDF it writes is linked the moment it exists — the browser's version of
  `sheet --open`, which opens the file on whatever machine you *typed* it on.
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

`proxdex sheet <name> [ID[:COPIES]...]` corrects each master for the medium,
extends cut bleed
outside the trim (cardbleed), imposes onto pages, and writes
`print-batches/<date>_<name>/<faces>.pdf` plus a manifest. proxdex renders the
PDF itself, so the print path is fully determined — **print with your printer's
colour management OFF** so a calibration holds.

- **Copies and per-run overrides.** `ID:4` prints a playset, `--copies N` applies
  to the whole run, and `--faces/--page/--orientation/--cols/--rows/--bleed/--dpi/
  --guides/--profile` change this run without touching the library's settings.
  `--dry-run` reports the page plan and writes nothing.
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
`--fetch`; add `--open` to preview the first 12 result images in your browser.
(The web UI's equivalent is a `full ↗` link on each hit — a browser cannot ask
the machine running the server to open anything, and should not.)

### Upscaling

**This is the one step that needs software proxdex does not ship** — see
[Upscayl is installed separately](#upscayl-is-installed-separately-and-cannot-be-otherwise)
for why that cannot be fixed with an extra, and what happens when it is missing
(short version: only this step is affected, and it says so).

`proxdex upscale` drives Upscayl's engine (`upscayl-bin`) directly — no GUI
round-trip — and mirrors the app's own options: any of the seven built-in
models, an output scale, and optional **Double Upscayl** (runs the model twice,
so 2× doubled = 4×, up to 16×). The command construction matches the app
exactly, including only passing `-s` when the scale differs from the model's
native 4×. The bundled binary and models are auto-detected on macOS, Windows and
Linux; for an install in a non-standard place, set the paths under `[tools]`.

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
Prefer the GUI? Skip this step and `proxdex import` its output instead — the
import wizard reads `_upscayl_` in a filename as "this is the upscaled stage", so
a whole output folder files itself.

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

`--auto` measures those four numbers off the image instead. Each edge is scanned
inward over 64 lines until the picture stops looking like the border — and every
line decides that from its *own* pixels just inside the cut edge, because a card's
frame is often not one colour at all: a silver full-art border is a gradient, an
ex-era border is a sheen, and a single colour read for the whole ring needs a
tolerance wide enough to swallow the art along with the variation. The answer is
the depth the most lines agree on. It is a *pre-placement*, not a decision: every
edge reports the share of its lines that agreed, and an edge they disagreed about
is named rather than passed off as measured.

```
$ proxdex border --auto base1-4
  ⌖ base1-4: border ends at T2.06 R2.67 B2.06 L3.17% — every edge measured cleanly.
✓ base1-4: fit → 628×877px  T3.92 R5.00 B3.92 L5.00%  (Pokémon · WOTC vintage, stretch)

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

`grade` applies **one look** — brightness, contrast, saturation, gamma — to every
card, so a mixed batch prints as a set. Defaults live under `[grade]` and every
one of them is also a per-run flag:

```toml
[grade]
brightness = 1.03   # printers + matte paper dull the image
contrast   = 1.06
saturation = 1.10
gamma      = 1.0
levels     = 0.0    # optional: stretch ONE card's own black/white points
```

```bash
proxdex grade ex3-90                       # the library's look
proxdex grade ex3-90 --saturation 1.2      # this run only
proxdex grade base1-4 --levels 0.4         # a flat, hazy scan
```

`--levels` reads that card and changes only that card: it pulls its own darkest
and brightest pixels toward full range, blended by the amount you give.

**Grade does not try to match cards to each other by colour.** An earlier version
did — it read the colour of each card's frame and white-balanced every frame to
one shared target. That is wrong at the premise: a card frame is yellow on a
Pokémon card, black on a Magic one, and absent on a full-art print, so there is
no common baseline to pull them to. With a mixed library the shared target came
out olive, and a neutral grey inside a yellow-bordered card graded to **deep
blue** while the same grey in a black-bordered card blew out to white. Both were
measured; both are gone.

Matching the *medium* is a real problem, but a print-time one — the paper and ink
are the same for every card on the sheet. That is what a print profile is for.

## Print profiles (one per medium you own)

A profile is everything proxdex needs to know about "matte 200 g on the XP-15000
with colour management off": a name, **your notes**, and how it corrects. They live
in `<root>/profiles/<name>.json`; `[print] profile` names the default.

```bash
proxdex profile list
proxdex profile new matte-200 \
        --notes "Canon TS8350 · matte 200 g · plain-paper setting · CM OFF" --use
proxdex profile show matte-200        # notes, numbers, every round, the trend
proxdex profile set matte-200 --note "switched to the rear tray"
proxdex profile rename matte-200 matte-200-rear
proxdex profile rm old-glossy
```

Write the notes down. Six months later they are the only way to reproduce a print.

**Nothing ships pre-filled.** There is one built-in name, `none`, and it is the
identity — no correction at all. A new profile starts there too. proxdex has no
numbers to offer for *your* paper: "foil needs saturation 1.38" was true of exactly
one setup that nobody reading this owns, so a recipe like that is a guess wearing a
label. Every real profile is one you made, one of two ways.

Fronts and backs can be corrected for **different media** — `[print] back_profile`
or `sheet --back-profile`. Leave it unset and both sides use one profile, which is
right for duplex, since a duplex sheet is one piece of paper. It exists because
that is not always true: the reverse of a one-sided glossy stock is a different
surface, and a backs-only run often goes on other paper entirely.

## Defining a profile without a scanner

Four multipliers, set by hand, applied at print time. The trick is not to guess
them on screen — a screen is not the paper — but to **print one page of the same
card at a row of values and pick the one that looks right**:

```bash
proxdex profile strip matte-200 --vary saturation --from 1.0 --to 1.6 --steps 4
#   → one page, four cards at true size, each labelled with its saturation
#   → print it on the medium (colour management OFF), look at it, then:
proxdex profile set matte-200 --saturation 1.4
proxdex profile preview matte-200      # before | after on a card, on screen
```

One knob at a time: a page where two things changed tells you which page you like,
not which value to keep. `--vary` takes `saturation`, `contrast`, `brightness` or
`gamma`, and `profile show` tells you the numbers currently in force.

The web UI has the same thing under **Print → By hand**: the four numbers, an
inline before/after, and a strip to print.

## Calibrating a medium (a loop, on one sheet of paper)

With a scanner, proxdex *measures* the correction instead of guessing it. The
loop is designed to be walked several times on **one sheet**: each round prints
the chart into a different slot of a 2×3 grid, so six rounds fit an A4 page.

```bash
proxdex calibrate chart                      # → profiles/<name>_round1.pdf, slot 1,1
#   print it on the medium (colour management OFF)
#   scan the whole page (scanner auto-correction OFF), then:
proxdex calibrate add --scan scan.png        # records the round, refits, reports
#   feed the SAME sheet back in and repeat — the next chart goes in slot 2,1
proxdex profile show                         # watch the error fall
proxdex calibrate proof                      # target vs scan, patch by patch
proxdex calibrate disable --round 3          # a misfeed or a crooked scan
proxdex calibrate enable  --round 3          # …and put it back
```

Every round is **kept**, and the correction is refitted over all of them at once,
so each round makes it truer rather than replacing what you measured last time.
Round 1 prints the raw target, which measures how far off the medium is; every
round after prints the target *through* what is known so far, which samples the
space where your cards actually live. Against a simulated press it converges
`16.1 → 2.2 → 1.6 → 1.5 → 1.4` mean RGB.

**It also tells you when to stop.** A loop you are invited to repeat forever wastes
paper: once three rounds in a row have improved the fit by under half a level each,
`calibrate add` and `profile show` say so and stop suggesting the next chart —
what is left is the medium's own gamut, and no amount of measuring puts ink in the
printer that is not there. Measure again when the ink, the paper or the driver
changes.

**The error you are shown covers only colours this medium can reach.** White paper
is not 255, ink is not 0, and a saturated blue at mid-lightness can need more cyan
than exists — so those patches are named and excluded rather than averaged in,
which would leave a floor that can never fall. Reachability is measured by
inverting your print's own response, and it is a property of the *medium*, so every
round is scored over the same patches: the trend moves when the print improves, not
when the patch set does.

**The chart is 80 patches: a 16-step neutral ramp, then a 4×4×4 lattice of the
cube's interior.** Two decisions there are worth knowing, because both were
measured rather than assumed:

- *The lattice is pulled inside the printable box on purpose.* Paper is not 255
  and ink is not 0, so a patch at pure red or pure white measures nothing — it
  clips, and gets dropped from the fit. The chart proxdex shipped through 0.5.0
  spent 24 of its 36 patches that way, leaving ~12 usable samples to fit a
  10-parameter model. Same press, same code: the old chart settled at 2.31 mean
  RGB, this one at **1.36**.
- *Denser is not better.* Patch area is the budget. At six charts per A4 these are
  5.1 mm of ink with 1.1 mm gutters — 121 px across on a 600 dpi scan. Push to 228
  patches and accuracy gets *worse*; a 512-patch near-continuous chart was worse
  than the 36-patch one it would replace, because read noise and neighbour bleed
  grow faster than coverage helps. A continuous gradient is worse again: there is
  no flat area to average, and 1% of geometric error becomes a correlated 2.3
  levels of error in every reading. (A 3-D LUT, which is what a dense lattice
  would justify, also lost to the polynomial at every density tested.)

**Rounds are never deleted.** A bad one — a misfeed, a scan with the scanner's
auto-correction left on — is *switched off*: the correction refits without it, and
switching it back on restores exactly what it was doing. That is the only way to
see with and without. `proxdex profile show` also gives each round a **pull**: how
far the correction moves if that round is left out. A round pulling much harder
than its neighbours is either your most informative measurement or an outlier, and
it is worth knowing which. In a run where round 3 was scanned with auto-correction
on, its pull came out at 17.7 against 5.6 / 5.4 / 4.4 for the others.

The chart travels the same renderer as a card sheet, so the correction is
measured on the exact path it is applied to. `proxdex sheet` then applies it, and
the stored masters stay neutral — switching media is a different `--profile`, not
a re-grade.

**Honest limits.** The scanner is the measuring device, so this makes prints true
*as your scanner sees them* — excellent for proxies, not colorimetric. Some
target colours are simply outside a medium's gamut: paper is not 255 and ink is
not 0, so those patches can never be hit, and proxdex says how many rather than
folding them into an average that could never reach zero. And you **must** turn
off the scanner's auto colour/contrast, or it fights the loop.

## Building a print sheet

`proxdex sheet <name> [ID[:COPIES]...]` imposes the masters. Copies are how
proxies are actually printed, and every page setting can be overridden for this
run only — a print run is this paper on this printer today, not a library
preference.

```bash
proxdex sheet playset ex3-90:4 base1-4:2      # a playset, and a pair
proxdex sheet deck --copies 4                 # four of everything ready
proxdex sheet deck --dry-run                  # the page plan, writing nothing
proxdex sheet deck --orientation landscape --cols 4 --rows 2 --bleed 3
proxdex sheet deck --profile foil-clear --notes "third attempt, rear tray"
proxdex sheet deck --faces duplex --profile matte-200 --back-profile glossy-reverse
```

`--dry-run` reports the pages per card size before anything is rendered, from the
same code that imposes the PDF. The batch manifest records the copies, the
profile and the page settings, so a reprint is reproducible rather than
remembered.

The web UI has the same thing as a screen (`Sheet`): pick cards, set copies, and
the page plan updates as you go. `Print` manages profiles and walks the
calibration loop — the slot map shows which part of the sheet is used, and the
round table shows the error falling.

## Releasing

One command, from a clean `main`:

```bash
scripts/release.sh 0.6.0 notes.md    # or omit the file and write the tag message
```

It runs the full gate (lint, format, typecheck, `node --check` on the web UI's
script, and a wheel build that asserts the data files are in it), bumps
`_version.py`, commits, writes an annotated tag, and pushes. Nothing is mutated
until every check passes. Pushing the tag runs the `Release` workflow, which
re-checks the gate, refuses a tag whose version does not match `_version.py`,
publishes to PyPI by trusted publishing, and creates the GitHub release from the
tag's own message with the artifacts attached.

## License

MIT
