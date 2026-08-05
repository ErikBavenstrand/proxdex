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
produce a device-independent, trim-size master (what the card _should_ look
like); the `sheet` step reproduces it faithfully on a specific printer + medium
(colour-correct + add bleed outside the trim).

## Pipeline

Each card's stored file is the **actual trim-size card** — no bleed. Four
stages, one file per stage:

| #   | stage      | produced by                             | command              |
| --- | ---------- | --------------------------------------- | -------------------- |
| 1   | `original` | downloaded from the game's image source | `search` / `fetch`   |
| 2   | `bordered` | thin frame expanded to trim (optional)  | `border`             |
| 3   | `upscaled` | Upscayl — _after_ the border fix        | `upscale` / `import` |
| 4   | `edited`   | one uniform look — the **master**       | `grade`              |

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
two dozen _layouts_ — saga, adventure, prototype, leveler, battle — but nearly all
of them differ in rules text, not in ink, so proxdex records only what changes
what goes on paper: **one side, two sides, or half of a meld pair**, plus whether
the card is **oversized**. `proxdex ls` shows it in a Kind column, `proxdex show`
spells it out, and the UI badges it on the tile and the card page.

#### Two sides

A Magic transform, modal, reversible or art-series card is **one card with two
sides**, and `fetch` downloads both. Each side runs the pipeline on its own — a back face is a
different picture and needs its own border fit — so every step, skip and reset
takes `--face 1` (front) or `--face 2` (back). Without it they act on both.

Which side goes on the _paper_ is your call, because a transform card has two
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
applied at `sheet` time, extended _outside_ the trim, so the master stays a
clean, resizable, device-neutral card. **Where do effects go?** Author the look
in `grade` (after upscaling, WYSIWYG); frame expansion goes _before_ upscale in
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
├── games/
│   └── lorcana.json                 # a game you defined: its name and its sets
│                                    # (the two proxdex ships are code, not files)
├── frames/
│   ├── lorcana-base.json            # a border you measured yourself
│   └── rules.json                   # which cards get which spec
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

**macOS, Linux and Windows**, on Python 3.11–3.13. CI runs the suite _and_ a real
library end to end — init, import, border fit, imposed PDF — on all three, because
the defects that only appear on one platform live in that path and not in a unit
test. One caveat, inherited rather than ours: on **Linux arm64** (a Pi, Graviton,
Docker on Apple Silicon) `cardbleed`'s `jpeglib` dependency has no prebuilt wheel,
so `apt install build-essential` first. x86_64 Linux, macOS and Windows all
install from wheels with no compiler.

`cardbleed` ships as a dependency, so it's bundled in proxdex's own venv — no
separate install, and proxdex finds it there even though it isn't on your PATH.
It is pinned `>=0.4.1`: cardbleed runs _inside_ proxdex's process and prints to
proxdex's stdout, and 0.4.1 is the first release whose own output survives a
stream that cannot encode it.

### Upscayl is installed separately, and cannot be otherwise

Everything proxdex needs comes with it — **except the upscaler**. That one is a
desktop application, and no `pip`/`uv` install can supply it:

- **Upscayl is not a Python package.** It is an Electron app whose engine,
  `upscayl-bin`, is a native Vulkan binary. It is not published on PyPI, so there
  is nothing for an extra to depend on. (There _is_ an `upscayl` name on PyPI —
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

|         | looked for at                                                                                        |
| ------- | ---------------------------------------------------------------------------------------------------- |
| macOS   | `/Applications/Upscayl.app/Contents/Resources/…` (and `~/Applications`)                              |
| Windows | `%ProgramFiles%\Upscayl\resources\…`, and `%LOCALAPPDATA%\Programs\Upscayl\…` for a per-user install |
| Linux   | `/opt/Upscayl/resources/…` (the `.deb` and `.rpm` both land there)                                   |

Some installs have no fixed home and cannot be guessed: Upscayl's Windows
installer lets you choose the directory, its portable zip unpacks wherever you
put it, and a Linux **AppImage** runs from a temporary mount. Point at those:

```toml
[tools]
upscayl_bin    = "D:/Apps/Upscayl/resources/bin/upscayl-bin.exe"
upscayl_models = "D:/Apps/Upscayl/resources/models"
```

Keep the binary _in its own folder_ — on Windows it needs the `vcomp140.dll`
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
changes — the _stage_ still exists, an upscaled image a card already holds is
still shown and still printed, and `proxdex skip upscale` is a first-class choice
that leaves the earlier stage as the master.

Internally the step talks to an `Upscaler` backend rather than to Upscayl by name
(`upscale.BACKENDS`), so a second engine is one class and one registry entry —
including, if one ever ships wheels for every Python proxdex supports, a
pip-installable one. Today there is exactly one backend, and it is Upscayl.

### Library vs. tool

The **tool** is installed once; your **library** (cards, config, batches) is
just a folder. proxdex locates it, git-style:

1. `--root DIR` (accepted before _or_ after the command), else
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

proxdex sets                   # every set of the game, grouped as the game groups them
proxdex browse base1           # page through one set, ✓ on the cards you already have
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
proxdex ls --per-page 40 --page 2       # a long library, by the page
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

proxdex learns things about how a stage image has to be _stored_, and a library
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
_not_ repaired: re-fitting a border needs to know where the border is, which is a
decision, so `doctor` names the step to re-run instead. The same check is the
settings screen's **stored images** panel in the web UI.

### Several games in one library

Each card records its game in a `.game` file next to its images, so a mixed
library just works — `ls` shows it, backs and frame specs follow it. `--game`
picks which game a command means; without it, `[library] game` from
`proxdex.toml` is the default (and `fetch` falls back to trying the others).

```bash
proxdex search --game mtg delver of secrets --set isd
proxdex fetch neo-136              # tries the default game, then the rest
proxdex fetch --game mtg 4ed-100   # or say it outright
```

|               | Pokémon                 | Magic: The Gathering                             |
| ------------- | ----------------------- | ------------------------------------------------ |
| id            | `ex3-90`                | `neo-136`                                        |
| metadata      | pokemontcg.io           | Scryfall                                         |
| image         | scrydex                 | Scryfall PNG (745×1040)                          |
| card back     | no API — supply a scan  | Scryfall's standard back                         |
| frame spec    | per era (WOTC measured) | one spec, uniform on all edges                   |
| borderless    | not exposed by the API  | detected from the printing                       |
| sides         | always one              | one or two (transform, modal, reversible)        |
| related cards | —                       | meld halves + result, tokens (`fetch --related`) |

### A game of your own

proxdex ships Pokémon and Magic because they have APIs behind them. Any other game
you can define yourself — a name, its sets, and the pictures you supply:

```bash
proxdex game add lorcana --name "Disney Lorcana"
proxdex game set add lorcana tfc --name "The First Chapter" --total 204

# measure one card's border by hand, then point the whole game at it — one
# printer, one stock, one border, and every set you declare later is covered
proxdex frames set lorcana-base --name "Lorcana base" --game lorcana \
    --top 3.1 --right 3.0 --bottom 3.1 --left 3.0
proxdex frames assign lorcana-base --game lorcana --match set

# your pictures go in through `import` — there is no API to fetch from
proxdex import ~/scans/*.png --game lorcana --id tfc-1 --card-name "Elsa"
proxdex border tfc-1 --inner-top .035 --inner-right .049 \
    --inner-bottom .035 --inner-left .049
proxdex sheet friday tfc-1:4
```

It lives in `<root>/games/<id>.json`, beside `frames/` and `profiles/`, and the
settings screen's **games** panel does all of the above.

Two things follow from having no provider, and both are deliberate:

- **`fetch`, `search` and `browse` refuse it**, naming `import` instead. They have
  nothing to ask, and a game silently sent to Scryfall would come back with either a
  404 about the wrong problem or — worse — somebody else's card.
- **Its sets are declared rather than discovered**, which is what lets `import` refuse
  a typo. For Pokémon and Magic the metadata lookup is what proves a card id exists;
  here nothing would ever object, so `tfcc-9` would file happily into a brand-new set
  called `tfcc` and read as a clean import. With `tfc` declared, it is blocked and told
  which ids are real.

A set that needs its own border still gets one — `frames assign … --set tfc` beats the
game-wide rule — and `proxdex frames rules` lists both, plus the baseline proxdex ships
for Pokémon and Magic, so what decides a border is on one screen.

Everything after the source image is the ordinary pipeline — border, upscale, grade,
impose, `doctor`, `frames coverage` — because nothing downstream of the original ever
knew which API answered. Two-sided cards work too (`import --faces 2`), and the shared
card back comes from `proxdex back --game lorcana --file back.png`.

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
- **Border tool** — focusing Border draws the card **untouched**, with a dashed
  outline over it showing where this printing's border _should_ be, from its frame
  spec. Nothing has measured where it actually sits: that is yours to place, because
  a border read off the image is wrong in ways no screen shows until the card is cut
  (see [Border correction](#border-correction-frame-expansion)). From there you
  **Skip**, or press **Align the border** to bring up four draggable marks starting at
  the spec's own numbers. Drag one and a **loupe** shows the source pixels at 6× with
  the mark on a crosshair; arrow keys nudge it, or type the inset as a percentage.
  Run stays disabled until the marks are up and a fit solves — a reshape with no idea
  where the border is has nothing to reshape against. A borderless printing needs no
  marks at all, so it says so and Run is live immediately. The dashed lines are what
  the fit is aiming at and the solid ones are what you drag; once the step has run,
  the outline stays over its **output**, which is the cheapest check there is that the
  fit landed.
- **Browse** — every set of a game as a grid of its own logos, under the game's own
  grouping (a Pokémon series, a Magic `set_type`), with a bar on each tile showing how
  much of that set you already hold. Open one and it is a paged card grid with the
  set's symbol beside every print, a green **✓** on the ones already filed, and the
  same filter bar Search has. This is the screen for when you know the set and not
  the name, which is most of the time.
- **Adding cards, two ways** — because they are two intentions. **`+ Add`** under a
  card downloads that one now, and the button becomes `✓ In your library` the moment
  it lands. Or **click the artwork** to pick cards — a picked card takes the accent on
  its own frame and a badge you can read across the grid, and a tray at the foot of
  the window shows the thumbnails of everything picked with one **`Add N to library`**.
  **The tray outlives the page you picked on**: it survives turning the page, changing
  a filter, switching between Browse and Search, and a reload. It can hold both games
  at once, and each card is fetched under its own.
- **Search** — pick a game, query by name with filters served from the provider's own
  catalogs (rarities, types, subtypes; colours for Magic), sorted and paged, add
  selected, with or without the cards they're printed alongside. Hover a hit for
  `full ↗` — the provider's own scan at full size, for telling two prints apart.
  Every filter is a removable chip, and the whole query lives in the URL.
- **Import wizard** — drop a folder of scans or an Upscayl output and review it
  one row per file before anything is sent: the card it goes to, the stage, the
  side, and what it would do — a new stage, a replacement, a slot two files want,
  a name with no id in it. The review is `import --dry-run` computed on the
  server from the **filenames alone**, so a two-hundred-file folder previews
  without uploading a byte; the thumbnails are the browser's own copies. A row
  with no id gets a **Find…** search to name the card, or an id for **every**
  unnamed row at once from a set code plus the number already in each filename
  (`4.png` under `base1` → `base1-4`; a name with no number is left for you).
  `already there` chooses replace-or-keep for the run, a checkbox narrows the table
  to just the rows that need a decision, and the files then go one request at a time
  so a failure belongs to one named file instead of the folder. Before you have
  dropped anything the screen states the three steps and **what a filename tells
  it** — which is knowable up front, and much cheaper to act on before you have
  chosen two hundred files.
- **Card data sheet** — every field the provider returns, its outbound links, and
  what the card is **printed alongside** — a meld partner, the melded card, the
  tokens it makes — each addable in one click.
- **Settings** — a real form: every setting carries its label, explanation, unit
  and default, all read from the code itself, with the raw TOML key kept visible.
  Nothing is written until you save, and the save bar only exists while something
  differs. Card backs, the frame specs (with the warnings `frames check` reports) and
  the calibration loop live here too.
- **Make sheet** — the whole library or just what you selected, with a per-card
  side choice for two-sided cards, made where the consequence is. If the batch
  mixes card sizes it says so up front, with how many of each fit a page. The
  PDF it writes is linked the moment it exists — the browser's version of
  `sheet --open`, which opens the file on whatever machine you _typed_ it on.
  **Mark printed** and **rebuild index** from the toolbar.
- **The border is two steps, and the second one is the fill.** Step one is the reading:
  place the marks and Run, with cardbleed's defaults. That reading is recorded, so it
  never has to be taken again. Step two appears once the step is done — cardbleed's
  thirteen fill settings, right under the step's own controls, and **changing one
  re-borders the card there and then**. What you are looking at is the output, not a
  preview; the geometry never moves, only the invented border, and the later stages are
  rebuilt as with any re-run. What you settle on is kept on the card. Where nothing is
  being added — the marks already on the spec — the panel **says so** rather than
  offering controls that cannot change the picture.
- **Waiting tells you what it is waiting for.** A batch of cards or the pages of a
  PDF shows a **real bar** — the running command's own count, `3 of 12`, with the card
  or page it is on. The long single jobs that nothing can count (one card through
  Upscayl) show elapsed time and, once you have run that command twice, `~41s
typical` — the median of _your_ last runs, labelled as the estimate it is, never
  dressed up as a bar position. A plain read is quicker than a dialog deserves and
  gets a hairline at the top of the window instead.

`?` shows every keyboard shortcut; `G` then `L`/`S`/`,` jumps between screens.

Every screen has its own URL — `/library`, `/card/ex3-90/upscale`,
`/card/isd-51/border?side=2`, `/browse?game=mtg`, `/browse/base1?sort=number&page=2`,
`/search?q=charizard&rarity=Rare+Holo`, `/settings` — so **back and forward work**, a
card (and a side) is bookmarkable, and a reload lands where you were (scroll
included). A set you are browsing is a _path_, because it is a page you navigated to;
the filters beside it are parameters, because they narrow that page rather than
replacing it. Switching views does no round-trip at all: the library, search results,
the set index and settings are held client-side, and images are served under a version
stamp so the browser caches them permanently and never refetches one it already has.

**Set logos and card scans come through the server, shrunk and kept.** The providers
serve a set's wordmark at 1000px and a card at full scan size, and the set index draws
174 of the former in a slot 2.25rem tall: 24.7 MB, and 45 MB for one 60-card page,
every visit. proxdex fetches each once, resizes it to the size it is actually drawn at
and keeps it in the cache directory — so the first screen of the index is 215 KB
instead of 4.2 MB, and the second visit does no network at all. `proxdex where` counts
what is held and `--clear-cache` drops it.

**A result tile also asks for a smaller picture in the first place**, since shrinking on
arrival only helps the browser — the server was still pulling the full scan to fill its
cache. A tile takes Pokémon's `/small` (245px, ~30 KB against 825 KB) and Magic's
`image_uris.normal` (488×680, ~120 KB against a median 1657 KB), which took a cold
60-card page from 3.5s to **0.7s**. Only the full scan is ever _filed_ — the thumbnail
never becomes a master — and a card's `full ↗` link still opens the provider's original,
which is what it is for.

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
  to the whole run, and **every page setting has a flag** — `--faces`, `--page`,
  `--orientation`, `--cols`, `--rows`, `--bleed`, `--dpi`, `--margin`, the ink
  offsets, the cut guides, the registration marks, `--profile` — so a run changes
  without touching the library's settings. `proxdex sheet -h` lists them with their
  bounds and this library's defaults; the web UI's **Page setup** panel is the same
  list, grouped. `--dry-run` reports the page plan and writes nothing.
- **The grid is checked against the paper, and you are told when it doesn't
  fit.** A cell is the card plus bleed on every edge, so three columns of a
  63.5mm card cost `190.5 + 6 × bleed` mm — 199.5mm at the default 1.5mm bleed,
  which clears about 5mm a side on A4. `margin_mm` is how close to the paper's
  edge your printer can actually print; the grid is centred in what's left, and
  `sheet` names the numbers when it won't go:

  ```
  $ proxdex sheet deck --page letter
    ▤ 9 card(s) at standard → 1 page(s) (3×3 per page)
    ⚠ standard does not fit: the grid is 199.5×275.7mm and the printable box is
      205.9×269.4mm — too tall by 6.30mm. 3×2 fits, or keep the grid with bleed ≤ 0.44mm
  ```

  **Letter cannot hold three rows of cards** — 3 × 88.9mm is 266.7mm of bare
  card on a 279.4mm sheet — so set `rows = 2` there. A4 portrait holds 3×3, A4
  landscape 4×2.
- **Margins are per edge**, because printers are: `margin_top_mm`,
  `margin_right_mm`, `margin_bottom_mm`, `margin_left_mm` each override
  `margin_mm` for one edge and default to it. 4mm at the sides with 5mm at the
  top is an ordinary inkjet, and many grip ~12mm at the bottom — set that and
  the grid is held higher up the sheet to clear it.
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
- **Cut guides — where, and how far.** Two settings, because they are two
  questions. `guide_style` = `corners` (marks at each card's cut corners, which
  never touch a card) / `full` (the trim lines straight across, over them) /
  `none`. `guide_reach` then says how far each corner mark runs *away* from the
  card:

  | reach | in the gap between two cards | at the outer edge |
  |---|---|---|
  | `fixed` | a `guide_mm` tick from each side | a `guide_mm` tick |
  | `join` | **one line**, bridging the gap | a `guide_mm` tick — margin stays clean |
  | `paper` | **one line**, bridging the gap | **out to the sheet edge** |

  `join` guarantees the bridge whatever the gap is, where `fixed` leaves a hole
  as soon as the gap exceeds twice the mark length. `paper` is what a rotary
  trimmer wants, since you line its blade up on the sheet edge. **A mark never
  runs past a neighbour onto its face** — only `guide_cross_mm` may touch a card,
  and a little of it makes the four lines meet in a `+` at every corner, which is
  the one thing on the page that says the grid is square. Plus `placement`,
  `color` and width. Guides follow the ink offsets, because a guide marks where
  the card really lands, and only cells that **hold a card** are marked.
- **The backs get their own guides.** `guides_front` / `guides_back` decide which
  sides carry any (you cut from one side, so backs default off), and
  `back_guide_style` / `_placement` / `_mm` / `_color` / `_width_mm` / `_cross_mm`
  override the fronts' for the backs alone — **leave one out and it follows the
  fronts**. Turning the backs on with a second colour is how you check duplex
  registration: hold the sheet to a light and see whether the two sides' lines land
  on each other. Note `back_guide_style = "none"` draws none on the backs, which is
  a different answer from leaving the key out.
- **Registration marks** (`reg_marks`, `reg_inset_mm`) are corner targets for
  *measuring* that drift, and are deliberately **not** moved by the offsets — nudged
  along with the cards they would line up on every sheet and tell you nothing. The
  gap between the two sides' targets is what you set `back_offset_*` from.

All of the above live under `[sheet]`.

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

There are two ways in, and they answer different questions. **Search** needs a
name. **Browse** does not — which matters, because most of the time what you know
is the set.

#### Browsing by set

`proxdex sets` lists every set of a game, grouped the way that game groups them,
and says how many of each set your library already holds:

```
$ proxdex sets --game pokemon --group "Mega"

Mega Evolution 6 sets · 979 cards · 2025-2026
Set     Name               Cards    Released  Yours
me5     Pitch Black          120  2026-07-17      ·
me4     Chaos Rising         122  2026-05-22      ·
...
```

The grouping is the _game's own_, not proxdex's, and the two are different kinds
of fact: a Pokémon **series** is an era, so the newest leads; a Magic
**`set_type`** (Expansion, Core, Commander, Masters, …) is a kind of product,
which has no date order, so those follow a fixed list. `--group`, `--match` and
`--owned` narrow the list; `--json` gives you the whole thing as data.

Then `proxdex browse <set>` pages through one set, with a `✓` on every card
already in your library:

```
$ proxdex browse base1 --sort number --per-page 8
Base base1 · 102 cards · 1999-01-09 · Base  1/102 in your library
#     ID       Name       Set   Year    No.  Rarity     Artist
1     base1-1  Alakazam   Base  1999  1/102  Rare Holo  Ken Sugimori
...
4  ✓  base1-4  Charizard  Base  1999  4/102  Rare Holo  Mitsuhiro Arita
...
1-5 of 102 · page 1 of 21 · ✓ 1 already in your library · --page 2 for more
```

#### Searching by name

`proxdex search` queries the game's API by name — every word must appear in the
card name — and shows each match's set, release year, collector number, rarity
and artist so you can tell prints apart:

```
$ proxdex search entei ex
#  ID       Name            Set                       Year     No.  Rarity        Artist
1  ex4-91   Entei ex        Team Magma vs Team Aqua   2004   91/95  Rare Holo EX  Ryo Ueda
2  ex7-97   Rocket's Entei  Team Rocket Returns       2004  97/109  Rare Holo EX  Ryo Ueda
3  bw5-13   Entei-EX        Dark Explorers            2012  13/108  Rare Holo EX  Shizurow
Fetch which? [numbers/ranges/ids · 'all' · blank to cancel]: 1
```

Type `1`, `1,3`, `1-3`, an id, or `all`. Skip the prompt with `--select 1,3` or
`--fetch`; add `--open` to preview the first 12 result images in your browser.
(The web UI's equivalent is a `full ↗` link on each hit — a browser cannot ask
the machine running the server to open anything, and should not.)

#### The filters are the same on both

`search` and `browse` take one set of filters, because **browsing a set is a
search with a set and no name** — one code path, one result row, one pager:

| flag                                                       | means                                             |
| ---------------------------------------------------------- | ------------------------------------------------- |
| `--set base1`                                              | one set (`browse`'s argument does this)           |
| `--rarity 'Rare Holo'`                                     | one rarity, as the game spells it                 |
| `--year 2004`                                              | released that year                                |
| `--type Fire`                                              | Pokémon energy type / Magic card type; repeatable |
| `--supertype Trainer` · `--subtype VMAX`                   | Pokémon only                                      |
| `--color R --color G`                                      | Magic only; **any** of them matches               |
| `--sort released\|name\|number\|rarity` + `--asc`/`--desc` | ordering                                          |
| `--page N` · `--per-page N`                                | paging                                            |

**Every filter is pushed to the provider**, so the count in the footer is the
whole answer's and not this page's — `1-5 of 102`, with a real page count and a
real last page. An earlier version fetched a hundred rows and sieved them
locally, which could only ever report "100 results" for a set of 553 and re-fetched
the same hundred for page 2. Ask for a page past the end and it says so, and names
the last real one, rather than reporting no matches over an answer that has plenty.

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
is _frame_ correction, distinct from _cut bleed_ (which `sheet` adds outside
the trim).

You say where the border currently sits with
`--inner-top/-right/-bottom/-left` (fractions of the image; the UI's align marks do
this by dragging), and the card's **frame spec** supplies the target widths. The art is
un-distorted so the borders land exactly on spec — that is the point of the step, so it
is on by default; `--no-stretch` gets as close as the source allows instead.

**There is no `--auto`, and removing it was the point.** There was: it scanned each
edge inward and pre-placed the marks for you. It shipped, and it was wrong in three
ways that no screen showed — each found only by comparing it against a reading taken by
hand:

- On a Beta Sol Ring (stone frame at luminance 37 against a black border at 27) it
  walked straight past the real edge to the light text box: 37-41px where the border
  ends at 23px, **65% too far**.
- On `dft-501` it crossed a black keyline, a thin yellow line and a second black
  keyline, answering 56px where the flat yellow band ends at 50px.
- On `sld-912`, which is **full-bleed**, it found a border anyway — the dark art read
  as one at T4.04 R6.45%, and the fit then cropped into the picture on all four edges.

Each of those produces four plausible numbers, and a plausible number presented as a
measurement is worse than no number at all: it looks finished. So proxdex asks where
the border is instead of inventing it, and the measurements that replaced the detector
are one row per card in [`docs/measuring-frames.md`](docs/measuring-frames.md), taken
by hand. Do not expect it back.

### Only measured specs ship

Twelve do, and every millimetre in them was read off a real card by hand:

```
$ proxdex frames
Spec                Name                                                        Game                  Border T/R/B/L (mm)        Card
pokemon-wotc        Pokémon · WOTC vintage (Base-Neo Destiny)                   Pokémon               3.45 / 3.15 / 3.45 / 3.15
pokemon-ecard       Pokémon · e-Card (Expedition-Skyridge)                      Pokémon               3.12 / 3.24 / 6.76 / 7.16
mtg-1993            MTG · 1993 frame (Arabian Nights-4th Edition)               Magic: The Gathering  2.74 / 2.47 / 2.74 / 2.47
mtg-1993-alpha      MTG · 1993 frame (Alpha, Beta, Collectors' Edition)         Magic: The Gathering  2.74 / 1.96 / 2.74 / 1.96
mtg-1993-unlimited  MTG · 1993 frame (Unlimited, Revised)                       Magic: The Gathering  3.63 / 2.98 / 3.63 / 2.98
mtg-1997            MTG · 1997 frame (Mirage-7th Edition)                       Magic: The Gathering  3.42 / 3.07 / 3.42 / 3.07
mtg-2003            MTG · 2003 frame (8th Edition-M14, and the `future` frame)  Magic: The Gathering  2.99 / 2.98 / 2.99 / 2.98
mtg-m15             MTG · M15 frame (Magic 2015-present)                        Magic: The Gathering  2.56 / 2.56 / 2.56 / 2.56
mtg-yellow-band     MTG · yellow box-topper band                                Magic: The Gathering  3.76 / 4.27 / 3.76 / 4.27
mtg-oversized       MTG · oversized plane or scheme (89×127mm)                  Magic: The Gathering  2.98 / 2.99 / 2.98 / 2.99  oversized
mtg-vanguard        MTG · oversized Vanguard                                    Magic: The Gathering  5.30 / 4.03 / 5.30 / 4.03  oversized
borderless          Borderless (no printed frame)                               any                   0.00 / 0.00 / 0.00 / 0.00
```

There is **no confidence column**, and that was a correction rather than a
simplification. There were three levels, and one of them — "read off the publisher's
scans" — rested on a false premise: **a scan carries its own crop.** Trimmed 0.3mm
inside the real cut edge, every border read from it is 0.3mm narrow, every card in the
sample agrees with every other, and nothing in the image says so. Grading that
"trusted" dressed up a guess. So the account of where a number came from lives where it
cannot be rendered as a verdict: a comment above each spec in `frames.py`, and a row
per card in `docs/measuring-frames.md`.

**A printing with no measured spec resolves to nothing, and `border` refuses it** —
rather than fitting it to another era's numbers, which looks perfect and is wrong:

```
$ proxdex border sv1-1
SKIPPED sv1-1: no frame spec has been measured for this printing (sv1, Pokémon).
Measure a card and record it with `proxdex frames set`, assign it, or pass
--frame to fit against a spec for this run.
```

`proxdex frames check` lists every card in that position, along with the other three
faults it reports: a `frames/*.json` that will not parse, a pin or rule naming a spec
that is gone, and a trait rule on a card whose traits were never recorded.

`proxdex frames coverage` is the same question about cards you do *not* own yet — what
has nobody measured at all — and it asks each game the question **its own border
followed**. Pokémon's ran for known runs of sets, so a row is a set:

```
$ proxdex frames coverage --game pokemon
○ Pokémon — 21 of 174 sets covered

Scarlet & Violet — 0 of 18 covered
Set       Name              Year  Held  Spec           From
sv10      Destined Rivals   2025  —     none measured  —
sv9       Journey Together  2025  —     none measured  —
…
```

Magic's changed with the printing's *frame generation* — a modern set holds retro-frame
cards beside modern ones — so a row is a generation and there is deliberately no per-set
verdict, which comes out `✓ 5 of 5 frame generations covered`. Pokémon from **Diamond &
Pearl onward** (2007-05) is the real gap today; the e-Card sets closed with hand readings,
and the whole ex series answers — though only its first five sets were actually read, with
`ex5`–`ex16` **inheriting** that era's plain border rather than being measured, which
`docs/measuring-frames.md` records as inherited.

**The e-Reader specs are the ones whose four numbers differ, and that is the card's
doing.** Expedition, Aquapolis and Skyridge carry the Nintendo e-Reader dot-code strip
down the left edge and along the bottom, so those two are roughly twice the other two —
and the other two, at 3.12 and 3.24mm, are WOTC's border almost exactly. Collapsing
opposite edges the way every other spec here does would have split the difference on all
four, asking ~2.5mm too much border on two edges and too little on the others, and it
would have looked right on screen because the overlay is drawn in fractions too.

The strip then **moved**: across Ruby & Sapphire, Sandstorm, Dragon, Team Magma vs Team
Aqua and the promos beside them (`ex1`–`ex4`, `np`) it runs along the bottom alone, and
some cards in those sets carry no dot code at all and have a plain square border. So
those five sets resolve to **two** specs, nothing in the metadata says which a given card
is, and `border` names both and lets you pick — the same offer the e-Card sets get.

### Which spec a card gets, and why

Seven things can decide it, and `border` says which one did: `--frame` for this run, the
card's own pin, the _printing_ (the provider said borderless), one of your rules, a
set default, the shipped baseline for its era or frame generation, or nothing at all.
Add your own with `frames set`, and rules in `frames/rules.json` match on a number
range, an id list, or a trait the provider recorded — so one set can need more than one
spec without anybody choosing per card.

A borderless or full-art print has no frame to match, and a modern set mixes both under
one set code — so the _set_ cannot answer this but the printing can. proxdex reads it
from the provider at fetch (Scryfall's `border_color` and the art-series layout) and
records it in the card's own `.frame` marker, so the border step reshapes it to the card
aspect and nothing else. `--frame borderless` is the whole fix for a print whose
metadata is wrong about its own border:

```bash
proxdex border neo-136 --frame borderless
proxdex border neo-136 --frame mtg-m15 --save    # and pin it to the card for good
```

Changing a spec's numbers invalidates nothing on its own — a master fitted to the old
ones still looks perfect. So `border` records what it fitted to beside the file, and
`proxdex doctor` reports a master whose spec has moved since.

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

Matching the _medium_ is a real problem, but a print-time one — the paper and ink
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
numbers to offer for _your_ paper: "foil needs saturation 1.38" was true of exactly
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

## Calibrating a medium (measure once properly, then check)

With a scanner, proxdex _measures_ the correction instead of guessing it. The shape is
the one every profiling tool uses: **characterize once, verify, and refine only while
verifying says it is worth it.**

```bash
proxdex calibrate survey                     # → profiles/<name>_survey_full.pdf
#   print it on the medium (colour management OFF)
#   scan the middle of the platen (auto-correction OFF), then:
proxdex calibrate add --scan scan.png        # reads the paper, fits the model, reports
proxdex profile show --stages                # what each stage of the model bought

proxdex calibrate verify                     # → what the model PREDICTS, in one slot
proxdex calibrate add --scan check.png       # scores how far those predictions landed

proxdex calibrate chart                      # a refinement round, if the check asks for one
proxdex calibrate proof                      # target vs scan, patch by patch
proxdex calibrate disable --round 3          # a misfeed or a crooked scan
proxdex calibrate enable  --round 3          # …and put it back
```

**Why not "print six charts and watch a number fall".** That was the old loop, and it is
what let a real holographic-sticker profile ask its printer for **more yellow ink on every
one of four rounds** while the single figure on screen improved every time. A refinement
round re-fits the model with more data, so it can only ever agree with itself. There was no
number in the system that could fail.

`calibrate verify` is that number. It prints what the model predicts and scores where those
predictions landed, and it is deliberately kept **out** of the fit — a model that trains on
its own exam cannot fail it. `profile show` reports it separately, and "converged" is judged
on the checks once there are any. The whole account, with the measurements, is in
[`docs/calibration.md`](docs/calibration.md).

### Three numbers, never one

- **ΔE00** — the perceptual distance the trade judges a print by, over the colours this
  medium can actually reach.
- **the cast** — the mean a\*/b\* of the neutral patches alone. A cast is the first thing
  an eye sees and the thing a mean over hundreds of patches hides. It is what the old
  single RGB figure could not see at all.
- **the verification error** — how far the model's own predictions landed.

### It measures the paper, and aims at what the paper can give

The survey is covered in bare, unprinted patches, so proxdex reads **your stock's own
colour** off the chart. That matters because a white on a blue holographic sticker *is*
blue-white — no ink makes it whiter — and demanding an absolute neutral demands the
impossible. The bill for demanding it is paid in yellow across the whole tone range.

```bash
proxdex profile intent 1        # aim relative to the paper (the default)
proxdex profile intent 0        # aim at an absolute neutral — reachable, never automatic
proxdex profile intent 0.5      # half way
```

`profile show` prints the paper it measured, the aim in force, and the state of the scanner
reference above the rounds, because a residual is not interpretable without all three.

### The model is four stages, and a cast belongs to one of them

`ink limit → linearization → grey balance → colour transform`, in that order, each fitted
on what the ones before it could not remove. This is the industry sequence and the reason
for it is that **a stage downstream cannot repair a stage upstream**: one polynomial doing
all four spends its parameters undoing them, and when the answer is wrong there is no way
to say which of the four things it got wrong. `profile show --stages` prints one row each.

Measured against a simulated press and scanner, the split beats the single polynomial it
replaced on every combination tested — 2.22 → **1.34** ΔE00 on warm matte with an honest
scanner, 10.05 → **6.42** on a blue holographic sticker with a biased one — and each stage
really does reduce the residual the next one sees.

### The chart, and what a sheet of it costs

Two charts, because characterizing a medium and confirming a correction are different
errands:

- the **survey** gets a whole sheet: bare paper, a ramp per ink (without which no
  per-channel linearization is possible at all), an L\*-spaced grey axis, the heaviest ink
  each way, repeats to measure read noise, and the colour lattice. `--size full|half|quarter`
  is the cost, and **only the lattice shrinks** — everything else is what the later stages
  are built from;
- the **verification chart** goes in one slot of the sheet grid, six to a page, and places
  its interior patches where the model is least certain *and inside the gamut your medium
  was measured to have*. A fixed lattice spends patches on colours the paper cannot make.

Denser is not automatically better, and that is measured rather than assumed: patch **area**
is the budget. 228 patches scored worse than 80, and a 512-patch near-continuous chart worse
than the 36-patch one it would have replaced, because read noise and neighbour bleed grow
faster than coverage helps. (A 3-D LUT, which a dense lattice would justify, also lost to
the polynomial at every density tested.)

### Rounds are never deleted

A bad one — a misfeed, a scan with the scanner's auto-correction left on — is _switched
off_: the model refits without it, and switching it back on restores exactly what it was
doing. That is the only way to see with and without. `profile show` also gives each round a
**pull**: how far the answer moves if that round is left out. A round pulling much harder
than its neighbours is either your most informative measurement or an outlier, and it is
worth knowing which. In a run where round 3 was scanned with auto-correction on, its pull
came out at 17.7 against 5.6 / 5.4 / 4.4 for the others.

The chart travels the same renderer as a card sheet, so the correction is measured on the
exact path it is applied to. `proxdex sheet` then applies it, and the stored masters stay
neutral — switching media is a different `--profile`, not a re-grade.

### Honest limits, stated on every profile

**Your scanner is not a colorimeter, and until you tell proxdex otherwise it assumes one
thing that is not true**: that a scanner's reading and a card's sRGB pixel are the same
kind of number. They are not — a flatbed's red, green and blue filters are not a linear
transform of the eye's — so the loop converges on "the print **scans as** the target"
when what you want is "the print **looks like** the card". On ordinary matte a decent
scanner is close enough that the two nearly coincide, which is why matte profiles work. On
a coloured or specular stock they diverge, and the divergence is the cast.

proxdex says so wherever the profile is named, and one purchase removes it:

```bash
proxdex calibrate reference --scan it8.png   # a ColorChecker, scanned and cropped
proxdex calibrate reference --clear          # back to assuming sRGB, deliberately
```

The literature puts an uncharacterized flatbed at around ΔE00 10 of error no calibration
can remove, and a matrix off a published target at around 4.9.

Three more, and none of them is fixable by measuring harder:

- **some colours are outside a medium's gamut.** Paper is not 255, ink is not 0, and a
  saturated blue at mid-lightness can need more cyan than exists. Those patches are named
  and excluded rather than averaged in, which would leave a floor that can never fall. What
  cannot be reached is **compressed toward the neutral axis at constant hue**, never clipped
  one channel at a time — clipping is what turns a grey into a yellow.
- **a hologram cannot be measured at one geometry.** Its colour depends on how it is lit and
  viewed, which is why the trade uses sphere or multi-angle instruments. proxdex detects the
  case (the bare-paper readings disagree across the sheet) and says so, rather than fitting
  a confident polynomial to one slice of it.
- **ink varies between cartridges**, so a profile describes the cartridge that printed it.
  Record it in the notes, and measure again when the ink, the paper or the driver changes.

And you **must** turn off the scanner's auto colour/contrast, or it fights the loop.

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

## Development

One command for the environment, and it is the same one CI uses:

```bash
uv sync --group dev      # ruff, pyright, pytest — plus the [ui] extra
```

The `dev` group deliberately pulls the **`[ui]` extra** rather than listing its
packages again, so lint and typecheck see `webui.py`. `node` is needed too, for one
test (below); every CI runner ships one, and the test skips loudly without it.

### The gate

Five commands, and they are exactly what CI runs — if these pass locally, CI passes:

```bash
uv lock --check                             # the lock still agrees with pyproject
uv run --group dev ruff check src tests
uv run --group dev ruff format --check src tests
uv run --group dev pyright                  # strict
uv run --group dev pytest
```

`webui.html` is a vanilla-JS SPA and is **not** linted or typechecked by any of them.
After editing its `<script>`, extract the block and run `node --check` on it — that is
the only thing standing between a typo and a blank page. `scripts/release.sh` does it
for you, and so does the parity test below, indirectly.

The suite is deliberately small: it covers only what a person cannot re-check by eye,
which is why `tests/test_fit_parity.py` exists — it cuts `solveFit` out of `webui.html`
and runs it **in node** against cardbleed's Python `solve_fit`, because a drift between
the two makes the align overlay lie about where a card will land.

### Running your checkout vs. the installed tool

These are not the same program, and the difference has shipped bugs twice:

```bash
# your checkout, on a library in the current directory
uv run proxdex ls

# your checkout, on a library somewhere else
uv run proxdex --root ~/Documents/Proxies ls

# your checkout, from *any* directory (handy when the library is the cwd)
uv run --project ~/Code/proxdex proxdex --root ~/Documents/Proxies ls

# what a user actually gets: no dev group, no extras
uv tool install .
```

**`uv run` gives you the dev group, and a user has no dev group.** Because that group
carries the `[ui]` extra, an import that a core module needs but that is declared under
`[ui]` works perfectly in a checkout and dies for everyone else. That is not
hypothetical: `tomlkit` shipped that way for two releases — green on six CI jobs across
three platforms — and a plain `pip install proxdex` wrote the print PDF and _then_ died
with `ModuleNotFoundError`. Two things guard it now, and both are worth knowing about
before you add a dependency:

- `tests/test_deps.py` reads the **declaration** in `pyproject.toml` rather than trying
  the import, because in the environment the suite runs in every import works. Every
  non-stdlib import must be a declared dependency, or sit inside a
  `try/except ModuleNotFoundError` that says how to install it.
- CI's `installed` job builds the wheel, installs it into a bare venv with **no extras
  and no dev group**, and drives the whole pipeline through it.

To reproduce that job locally before you push:

```bash
uv build && uv venv bare && VIRTUAL_ENV=bare uv pip install dist/*.whl
bare/bin/proxdex init lib && bare/bin/proxdex --root lib where
```

### Test against a throwaway library

Never your real one. A temp directory with a `proxdex.toml` marker is a library, so:

```bash
uv run proxdex init /tmp/lib
uv run proxdex --root /tmp/lib <command>
```

`cardbleed` ships its own suite — `cardbleed --selfcheck`.

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
