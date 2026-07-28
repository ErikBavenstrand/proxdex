# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

proxdex is a CLI + optional local web UI that manages a Pokémon/MTG proxy-card
pipeline (one library can hold both games): fetch a card, reshape/upscale/colour-correct it into a trim-size
master, then impose print-ready PDFs. It drives **cardbleed** (border
extension, a separate repo at `~/Code/cardbleed`, pinned `>=0.4.1` — 0.4.1 is the
first release whose output survives a redirected stream, which matters here
because cardbleed runs *in* proxdex's process and prints to proxdex's stdout) and **Upscayl**
(upscaling), and owns the imposition step itself.

## TODO.md — the shared backlog (READ AND MAINTAIN THIS)

`TODO.md` at the repo root is the running work list, and it is **committed** —
so it travels between machines and survives a fresh clone.
It is where the user drops things to do and where you park anything that
shouldn't be done right now. Treat it as a live queue:

- **Format: one terse line per item, as a Markdown checkbox.** `- [ ]` = to do,
  `- [x]` = done. A few words, not sentences. No sub-bullets, no prose.
- **Anything actionable goes here.** When the user asks for something, or you
  **detect something that must be done**, add it as a `- [ ]` item.
- **Park, don't drop or derail.** If you notice work that is **unrelated to the
  current task, or too large to do right now**, add it to TODO.md and keep going
  — never silently expand scope, never lose the thread.
- **To make progress**, read TODO.md, pick up a batch of `- [ ]` items, do them,
  and flip each to `- [x]`. Prune completed items when the list grows long.
- Check TODO.md at the start of open-ended work so nothing queued is forgotten.

## Commands

Everything runs through `uv` (src-layout, hatchling, dynamic version from
`src/proxdex/_version.py`). The gate is lint + typecheck + tests, matching CI:

```bash
uv sync --group dev                       # dev deps (ruff, pyright, pytest, + the ui extra)
uv run --group dev ruff check src tests   # lint (ruff select = ALL, curated ignores)
uv run --group dev ruff format src tests  # format (or --check to verify)
uv run --group dev pyright                # typecheck (strict; include = ["src", "tests"])
uv run --group dev pytest                 # the suite (needs node for the fit-parity test)
uv run proxdex --help                     # run the CLI
uv run proxdex ui                         # local FastAPI web UI (needs the [ui] extra, in the dev group)
```

**The suite is deliberately small: only things a person cannot re-check by eye
every time.** Each file earns its place by naming a failure that is *invisible*
until it reaches paper or a pasted link:

- `tests/test_faces.py` — filenames (face 0 has *no* suffix, so no library
  migrates), per-face state, rollup, which side prints.
- `tests/test_borders.py` — `detect_inset` against synthetic cards whose border
  width the test chose, the weak-edge/frameless reporting it promises, and the
  three card classes it once could not measure at all (a gradient frame, a pale
  cut edge outside a dark border, a decorated frame with two inner edges).
- `tests/test_fit_parity.py` — the UI's `solveFit`, **sliced out of `webui.html`
  by brace matching and run in node**, against cardbleed's `solve_fit` over a
  table of fits. It exists because a drift between the two is invisible: the
  align ghost simply lies about where the card will land. Extracting the JS
  rather than copying it is the point — a copy would be a third implementation,
  and the one that stayed right.
- `tests/test_import_plan.py` — `imports.plan`, for the same reason as the fit
  parity test (one pure function, two consumers: `import --dry-run` and the
  wizard): filename reading, guessed-vs-confirmed ids, and the two ways a
  destination is already taken.
- `tests/test_flatten.py` — that a filed image keeps no alpha and is filled from
  the card's own border, because nothing about a transparent corner is visible
  until the sheet is printed.
- `tests/test_doctor.py` — that `doctor` finds each of the four things a stored
  image can be wrong about and, just as much, that it leaves a correct file and
  every downstream stage **untouched** — a repair that rewrites everything is one
  nobody can run on a real library.
- `tests/test_frames.py` — `specs.resolve`, for the same reason as the import plan:
  one pure function, many consumers (`border`, `frames check|preview`, `/api/frame`,
  the align ghost), and a wrong answer that is invisible until two cards are cut and
  laid side by side. It pins the order all seven `Via`s are tried in, that a number
  range never crosses a `TG` prefix, and the two questions it must refuse to guess
  at — a trait rule with no traits, and a pin whose spec was removed.
- `tests/test_upscale.py` — that a missing upscaler disables *running* the step
  and nothing else (the stage, the skip and an already-upscaled image all
  survive), and that the probe answers instead of raising.
- `tests/test_deps.py` — that every non-stdlib import is a *declared* dependency,
  or is guarded by a `try/except ModuleNotFoundError` that says how to install it.
  It reads `pyproject.toml` rather than trying the import, because in the
  environment the suite runs in every import works — which is exactly how `sheet`
  shipped twice with an undeclared `tomlkit`.

Everything else is still verified by running commands against a **throwaway
library** (never the user's real one at `~/Documents/Pokémon Proxies`): a temp dir
with a `proxdex.toml` marker and a fake card folder + stage PNGs, then `uv run
python -m proxdex --root <tmp> <cmd>`. The `cardbleed` dependency ships its own
suite: `cardbleed --selfcheck`.

Fixtures live in `tests/conftest.py`: a `library`/`card` on `tmp_path`, and
`bordered_card()`, which is two numpy fills — a flat ring and a flat art panel —
so a border test can assert the exact number it put in. **Do not grow the suite
into a mirror of the CLI**; a test that needs a network provider, Upscayl or a
printer is not a test proxdex can keep honest.

`webui.html` is a vanilla-JS SPA and is **not linted or typechecked**. After
editing its `<script>`, sanity-check it by extracting the script body and
running `node --check` on it.

**Releasing is one command, and it is `scripts/release.sh <version> [notes.md]`.**
It refuses a dirty tree or a branch that is not `main`, runs the whole gate
(including `pytest`, `node --check` on the UI script and a wheel build that
asserts `webui.html` and `static/` are in it), bumps `_version.py`, commits, writes an
**annotated tag**, and pushes. Nothing mutates until every check has passed,
because a PyPI version cannot be reused and a pushed tag is one people have.
The tag's message is the release notes: `Release` then re-checks the gate, verifies
**the tag's version matches `_version.py`**, publishes to PyPI by trusted
publishing, and creates the GitHub release with `--notes-from-tag` and the built
artifacts attached. So there is one text, written once, and no second copy to keep
in step. A release built from a tag is reproducible — v0.5.0 rebuilt from its tag
matched PyPI's sha256 exactly.

**Three platforms, and CI proves it (`ci.yml`).** macOS, Linux and Windows × Python
3.11 and 3.13, running the suite *and* a real library end to end — `init`, a card
filed by `import`, a border fit, an imposed PDF, `where`, `ls --json`. That second
job exists because **every** platform-specific defect found so far lived in the
filesystem path and not in a unit test. Windows is the one that differs in kind: a
file with an open handle cannot be deleted, the separator is not `/`, install roots
come from the environment, and none of it is exercised — or even *typechecked*, since
a checker narrows `sys.platform` to the host — on a POSIX runner.

- **CI also installs the built wheel bare and drives the pipeline through it**
  (the `installed` job), because `uv run` is *not* what a user gets: the project
  environment carries the dev group, so `tomlkit` was a core import declared under
  the `[ui]` extra and nothing could tell. `sheet` writes its batch manifest through
  it and `config set` edits `proxdex.toml` through it, so a plain
  `pip install proxdex` wrote the PDF and *then* died with `ModuleNotFoundError` —
  green on six jobs across three platforms, for two releases. `tests/test_deps.py`
  reads the *declaration* rather than asking whether an import works (in the test
  environment it always does): every non-stdlib import outside `webui.py` must be a
  declared dependency **or** sit inside a `try/except ModuleNotFoundError` that
  explains itself. `proxdex ui` is the only thing allowed to need the extra, and
  only because it asks first.
- **Error text is escaped before rich sees it** (`main`, pinned by
  `tests/test_output.py`). rich reads `[...]` as markup and *removes* it, so the
  install hint for the extra printed ``install "proxdex"`` — without the `[ui]` that
  was the whole sentence. Any message naming a path with brackets or a stage list
  would be edited the same way.
- **Output survives a stream that cannot carry it** (`cli.writable_output`, pinned by
  `tests/test_output.py`). The state marks and size glyphs (`✓ → · × ⤳`) are
  deliberate, and `proxdex --help | cat` on Windows used to die with
  `UnicodeEncodeError` on `→` — a *redirected* stream there falls back to the ANSI
  codepage rather than the console's UTF-8 path. Not a Windows quirk: `LC_ALL=C` on
  Linux does the same. Streams that cannot encode `GLYPHS` are reconfigured to UTF-8
  with `errors="replace"`, so the worst case is a `?` instead of a tick rather than a
  traceback instead of the command.
- **Every text file is UTF-8 with LF, stated at every call** (pinned by
  `tests/test_encoding.py`, which parses the source with `ast`). `Path.read_text()`
  and `write_text()` default to the *locale* encoding, so `proxdex init` crashed on
  Windows writing its own config (`DEFAULT_TOML` says `border → upscale → grade`),
  and a card called *Flabébé* would have been written cp1252 on Windows and UTF-8 on
  macOS — mojibake in a synced library. `newline="\n"` too, or a synced folder
  churns CRLF back and forth. Ruff's `PLW1514` catches **one** of the 27 sites (it
  only fires where it can infer the receiver is a `Path`), which is why the guard
  reads the syntax tree instead.
- **Temp files go through `scratch.file()`, never `tempfile.mkstemp(...)[1]`.** That
  idiom drops the open file descriptor: a leak everywhere (a 500-card duplex sheet
  took two per card, against a 1024 soft limit on Linux and 256 on macOS) and a
  *failure* on Windows, where the `unlink`/`rmtree` meant to clean up raises
  `PermissionError` instead. One helper, which closes the descriptor and returns
  only the path — proxdex always wants the name, never the handle.
- `net.cache_dir()` uses `%LOCALAPPDATA%` on Windows, `$XDG_CACHE_HOME`/`~/.cache`
  elsewhere. Safe to relocate by definition: it is a cache with a `--clear-cache`.
- Writes that must not tear use `Path.replace` (`os.replace`), which overwrites
  atomically on Windows too — `Path.rename` does not.
- **Known limit, not ours:** on Linux **arm64**, `cardbleed`'s `jpeglib` ships no
  wheel (its wheels are `cp38-abi3` for macOS, Linux x86_64/i686 and Windows), so
  that one platform needs a compiler. GitHub's runners are x86_64, so CI cannot see
  this — it is in the README instead.

## Architecture

**Library + Card model (`library.py`).** A `Library` is any directory
containing a `proxdex.toml` marker (discovered by `--root`, then nearest marker
upward, then `$PROXDEX_ROOT`). A `Card` is one folder holding per-stage image
files; **all card state is derived from the filesystem** — which
`<id>_<n>_<stage>[_f<n>].png` files exist, plus `.skip-<stage>[_f<n>]`, `.game`,
`.pin` (the frame spec chosen for this card), `.traits` (what the provider said
about the printing, for frame rules), `.fit-<stage>` (what a master was fitted to),
`.faces` and `.front` marker files. There is no database. The vocabulary enums
live here too: `Stage`, `Status` (pending/done/skipped) and `Step` (the three
skippable steps, each with a `.stage`).

**Faces (`library.py`).** A card has **one or two** printable sides — MTG's
transform/modal cards. Face 0 is the front and keeps the filenames a
single-faced card always had (`face_suffix(0) == ""`), so **no existing library
needs migrating**; face 1 carries `_f2`. Every state method takes `face` and
defaults to `FRONT`, because a back face is a different picture that needs its
own border fit: `stage_path/has/status/skip/reset/invalidate_downstream` are all
per-face. `.faces` records the provider's side names (written at fetch, so a
second side is *known* before its image exists); `.front` records which side
prints on the front of a sheet (`front_face`/`back_face`, set by `proxdex flip`)
— a transform card has two real fronts and no back of its own, so that is a
choice only the user can make. `Card.rollup(stage)` is the card-level status the
contact sheet shows: done only when *every* side is.

**Print kind (`games.Layout`).** Providers name two dozen layouts; almost all of
them are one picture on one side of one card — a difference in *rules text*, not
in ink. `Layout` records only what changes what goes on paper: `SINGLE`, `DOUBLE`,
`MELD_PART`, `MELD_RESULT`. Two more facts travel beside it: `oversized` (89×127mm
— planar, scheme, Vanguard; `games.OVERSIZED_*_MM`) and `frame` (a `GuideId` the
*printing* dictates, i.e. borderless). All three are read from the provider at
fetch and written into the card's own `.layout` / `.oversized` / `.frame` markers,
so nothing downstream needs another API call. `_kind_note` says out loud when a
printing is not an ordinary one-sided 63×88 card.

**Every card is imposed at its own size** (`cli._trim_mm` → `sheet.Cell.trim`).
`sheet.py` groups cells by trim and gives each group its own pages and its own
`Geo` (`sheet.grid_for`: the configured `cols`/`rows` at the configured trim, as
many as the page holds at any other), so an oversized card is never shrunk into a
63×88 cell and an ordinary library's layout is bit-for-bit what it was. `Cell`
carries front, back and trim *together* — a back belongs behind exactly one front
at exactly its size, and one object makes the pairing impossible to get wrong.
Duplex mirroring (`_grid_reorder`) happens per group, off `Geo.cols/rows`, never
off `cfg.sheet_cols/rows`.

**Meld is three cards, not three sides.** Both halves and the melded card each
have their own id and their own picture, so they are three ordinary cards plus a
recorded *relationship* — `sources.Related` / `Relation`, read from Scryfall's
`all_parts` (each part's uuid resolved to a `<set>-<number>` id, one cached
request each). `fetch --related` follows `_FOLLOWED` relations only (meld parts,
the result, tokens) — a "combo piece" is as loose as a set's checklist card, so
those are named and left alone. `details` carries them; `proxdex show` and the
card page list them with what you already have.

**Games (`games.py`).** `GameId` is `pokemon | mtg`; a `Game` carries the id
shape, nominal trim size, metadata source and whether a card back can be
downloaded at all. **Set codes alone cannot tell you the game** (MTG's `neo`
vs Pokémon's `neo1`), so it is filesystem state: a `.game` file written into
the card *and* set folder at creation, read back by `library.read_game()`,
falling back to `[library] game`. `Library.set_dir` appends the game to the
folder name if two games ever collide on a set code. Adding a game = a `Game`
+ a provider in `sources.py` + a `FrameGuide`; nothing else should need to
change.

**The step registry (`steps.py`) is the single declaration of the pipeline.**
One ordered list of `StepSpec`s: key, stage, label, blurb, skippability, and each
step's **settings schema** (`StepOption`: kind, the enum of allowed values, and
the `Config` field holding this library's default). The CLI's flags
(`steps.click_options`), its default resolution (`steps.resolve`), `/api/meta`,
`/api/step`'s validation and the web UI's control panels are all derived from it.
**Adding a step = one `StepSpec` + the code that does the work**; nothing else
should need editing, and there is no second copy of the step list in JS.
`steps.STAGES` / `steps.BEST` are the one place stage order is written down.

**The pipeline is the core abstraction.** Each card flows through four stages —
`original → bordered → upscaled → edited` (the trim master) — produced by the
commands `fetch/import`, `border`, `upscale`, `grade`. Every processing step is
**explicitly run or skipped, one at a time — nothing is automatic** ("no auto
mode"; the old `build`/`finish` batch commands were removed). Each step has a
persisted 3-state on `Card`: `pending ○ / done ✓ / skipped ⤳`
(`status/mark_skip/clear_skip/reset`), where **run** clears skip, **skip**
removes the output, **reset** returns to pending. CLI verbs: `skip/unskip/reset
<step>` (step = the `Step` enum in `library.py`, whose `.stage` gives the stage
it produces).

**Downstream invalidation.** Changing an upstream stage
(fetch/import/border/upscale run, skip, or reset) removes every existing output
after it *on that side* — they went stale (`Card.invalidate_downstream`, reported
by `cli._cascade`). Skip markers are kept (they're intent, not derived pixels).

**Sources (`sources.py`).** One provider per game behind `lookup` / `search` /
`download`, all returning `CardMeta`/`SearchResult` that carry their own
`faces: tuple[FaceImage, ...]` (front first, `.image_url` = face 0) — so nothing
downstream knows which API answered *or* how many sides came back. Scryfall marks
a genuinely two-sided card by keeping `image_uris` **on the faces** rather than on
the card, which is how `_mtg_faces` tells a transform card (two images) from a
split/adventure card (two named faces, one image). `_flatten` composites
transparent die-cut corners onto the **median colour of the card's own opaque
outer ring**, so a yellow-bordered Pokémon fills yellow and a black-bordered MTG
fills black with no per-game configuration. Pokémon =
pokemontcg.io + scrydex; MTG = Scryfall (PNG 745×1040, its largest and only
lossless size). `lookup_any` tries the
library's default game first, then the others. `details` is the read-only
sibling: it returns a `CardDetail` = every field of the response worth reading,
as ordered `FactGroup`s of label → value, plus the outbound `Link`s the provider
hands out (Scryfall's Gatherer link, the marketplaces). Every reader there is
total — a missing/null/wrong-shaped field becomes an empty fact, and empty facts
and groups are dropped — because the JSON is untyped and the two APIs agree on
almost no key.

**HTTP (`net.py`).** Every provider request goes through `net.get`: rate-limited
per host (Scryfall asks for it), retried with exponential backoff on 429/5xx and
transport errors (`Retry-After` honoured), and JSON responses cached on disk
(`$PROXDEX_CACHE`, else `~/.cache/proxdex`) — a *stale* cache entry is served
when every attempt failed, so a degraded API still lists cards. Each host's last
behaviour is recorded in `health.json`, so `net.incidents()` (this process → the
CLI's `⚠` line) and `net.health()` (the shared file → `/api/health` and the UI's
topbar pill) can say *which* API is misbehaving; the UI needs the file because
mutations run in a CLI subprocess. `proxdex where --clear-cache` empties it.

**Upscaling is behind a backend, and Upscayl cannot ever be a dependency
(`upscale.py`).** Upscayl is an Electron app whose engine is a native Vulkan
binary; it is not on PyPI (the `upscayl` name there is an unrelated `0.0.0a1`
"small example package" — never depend on it). **So there is deliberately no
`[upscale]` extra**: an extra can only pull Python wheels, and this step has no
Python dependencies at all, so one would install nothing and mean nothing. That
was considered and rejected, not overlooked.

Discovery covers **macOS, Windows and Linux**, from Upscayl's own
`electron-builder` layout (engine at `resources/bin`, models at
`resources/models`; `upscayl-bin.exe` on Windows, and its vcomp DLLs sit beside it
so the *directory* is what matters). Windows roots come from `%ProgramFiles%` /
`%LOCALAPPDATA%` **read from the environment**, never spelled out — the drive
varies and a 32-bit interpreter reports the x86 one. The two halves are searched
as a *pair*, so a binary can never be matched to another install's models.
`upscale.platform()` wraps `sys.platform` deliberately: a type checker narrows
`sys.platform` to the host, which leaves the other platforms' branches
**unchecked**, and a test cannot fake it — behind a `-> str` call both problems
go away, and `tests/test_upscale.py::TestWhereItLooks` is the only coverage the
Windows paths can get, because CI has no Windows runner (`ubuntu`, `macos`).

Instead the module is an `Upscaler` protocol, a `BACKENDS` registry (one entry
today: Upscayl) and `availability(cfg) → Availability` — a **probe that returns a
value and never raises**, because it is asked to draw a screen (`/api/meta`, the
Run button) and to write one line in `proxdex where`, long before anyone asks for
work. Adding a backend is one class plus one registry entry. A step declares its
dependency *in the registry* — `StepSpec.needs = upscale.availability` — so the
CLI, the API and the UI all learn "this cannot run, and here is why" from
`steps.py` rather than each testing for Upscayl themselves. One text
(`Availability.message`) is used verbatim by the CLI's refusal and the UI's panel.

**The stage is not the backend.** `Stage.UPSCALED` exists whether or not anything
can produce it: a card may already hold an upscaled image, and a library must stay
readable and printable on a machine with no upscaler. So an absent backend
disables *running* — it never removes the step, the stage, or skipping. In JSON
the field is `tool_ready`, **not** `ready`, because "ready" already means "this
step is next" everywhere in the UI, and a step can be next and unrunnable at once
— the pill then reads `not installed` and the rail `no tool`, never `ready`.

**No stage image carries an alpha channel** (`cli._flatten_filed` →
`sources.flatten`, pinned by `tests/test_flatten.py`). A card's die-cut corners
arrive transparent, and every tool downstream then decides for itself what is under
them — `convert("RGB")` keeps whatever bytes the encoder wrote there — so the
corners print as that. It has to be enforced at **every** point that files an
image, not just at the front door: `fetch` flattens on download, but `import`
copies bytes, cardbleed passes alpha straight through, and **Upscayl emits RGBA**.
Found the hard way on a real library — 14 files across three stages, corners
near-white on one card (212,225,229 against a 141,169,177 border) and near-black on
an upscaled one (mean 51, min 0), none of it visible on screen. `grade` and
`sheet.fit` already convert to RGB, so those are covered. The fill is the card's
*own* outer-ring median, never a fixed colour: black would swallow a Pokémon card's
yellow corner and yellow would ring an MTG card. A file already stored as RGB is
not rewritten, so imported bytes stay verbatim in the ordinary case — and a mode
that is merely not RGB (a grayscale or CMYK scan `import` copied in) is converted
too, since every tool downstream converts one its own way.

**A library outlives the code that filled it (`doctor.py`).** Enforcing an invariant
at every filing point does nothing for the files already on disk, so `proxdex doctor`
walks every stored stage image and reports each one that is not what proxdex would
write today. Four findings, declared once as `CHECKS` — `alpha`, `mode` (both
repaired in place by the same `sources.flatten` every filing point calls),
`unreadable`, and `aspect`: a **bordered** master that is not the configured trim's
aspect, which `sheet` would `cover`-crop, losing border off two edges silently.
Only the bordered stage is measured for aspect — every later stage inherits it, and
reporting one cause three times is not a better report. Each check owns its own
label, *why* and hint, so the CLI's terminal output and the settings screen's
**stored images** panel (`/api/doctor`, `/api/doctor/fix`) explain a defect with the
same sentence. Two things it must keep doing: examining reads **headers only** and
writes nothing without `--fix`, and a repair **never invalidates downstream** — the
picture does not change, so throwing away an upscale over a corner fill would
destroy work to fix a corner. What it cannot repair it does not touch: re-fitting a
border needs to know where the border is, which is a decision (the align marks), not
a repair.

**Border auto-detection (`borders.detect_inset`).** Each edge is scanned inward
along 64 block-averaged lines until the picture stops looking like the border, and
**every line decides that for itself**, from the pixels it holds in a small window
just inside the cut edge: the range of colours there, widened by an absolute floor
(`_MIN_DELTA`). It returns a `Detection` carrying **per-edge `support`** — the
share of lines that agreed — because a decorated frame reaching into the top
border says nothing about the left one. It is a *pre-placement*, never a decision:
`border --auto` prints the note and names the weak edges, `/api/detect` serves the
same, and the UI's align marks land there on first open with a clean/check chip.

Three things there were wrong once, each of which broke a whole class of card, and
all three are pinned by `tests/test_borders.py`:

- **One colour for the whole ring cannot describe a ring that is not one colour.**
  A silver full-art frame is a gradient and an ex-era frame is a sheen, so the
  ring's own spread set the tolerance at ~156 levels while the art sat 107 from
  the ring's median — *inside* it. Nothing read as "not the border" and the card
  came back with none. Judging per line fixed it because locally there is nothing
  to average away: measured over a real 15-card library, ex-era cards went from
  9-10% (nonsense; a border is ~3%) to 2-4%, and the silver card from nothing at
  all to 2.73/3.82% at full support.
- **The scan has to start where the reference is read from**, not at pixel 0. The
  outer `_SKIN` is the cut edge, its antialiasing and whatever a transparent
  die-cut corner was composited onto; scanning from before it made every card
  whose extreme edge differs from its border (a black MTG border under a pale cut
  edge) end on its first pixel and report no border at all.
- **The median is the wrong summary when the lines find two edges**, and on a
  decorated frame they do — a printed line just inside the colour. The median
  lands in the *gap* between the two clusters, a number not one line measured.
  `_consensus` takes the densest cluster instead (ties to the shallower, since the
  border is the outermost ring), and that cluster's size *is* the support.

A card with no border reads as `frameless`, which is a finding (borderless print)
rather than a failure — and `--auto` then skips measuring entirely, because
measuring a full-art card would find the art's own edge and crop the card to it.
`frameless` is deliberately **not** "the numbers came out small": art that changes
a little way in yields four plausible numbers no line agreed on, so the test is
that *not one edge* cleared `_TRUST`. Cropping a card to its own art is the one
outcome worse than declining to measure.

**Frame specs (`frames.py`) are geometry; `specs.py` decides which one a card
gets.** A `FrameGuide` is **four numbers, a note and `ref_mm`** — nothing else. The
note is prose saying where the numbers came from, and it is the *whole* account of
how much to trust them; `ref_mm` is the card they were taken off, because an
oversized card's 3mm border is a different *fraction* from a 63×88 one's, and so is
a real card's (63.5×88.9mm, not the 63×88 proxdex trims to — getting that wrong is
0.8% of error in every border). Eight
specs ship (`frames.SHIPPED`), so a fresh library borders a Base Set card with
nothing configured; a library adds its own as `<root>/frames/<id>.json`, mirroring
`profiles/`. **A spec id is therefore an open set, not a `StrEnum`** — the same
call `profiles` makes, for the same reason: a user can measure a new era tonight.
`GuideId` survives as the closed set of ids *code* names (the fallbacks, the
shipped baseline, and `borderless`, which `sources` returns for a frameless printing and
which is the one `RESERVED` id — it can be neither redefined nor removed). A
stored file **may** correct a shipped spec, and for the MTG ones it is *meant* to:
"we shipped that number wrong" is half of why this exists, and the shipped MTG
numbers are provisional by construction. `frames show` names the file when there is
one, which replaced a `shipped / shipped-edited / local` enum that graded provenance
the same way the confidence levels graded numbers.

**One set can need more than one spec, and rules pick per card — nobody chooses
one by hand.** `<root>/frames/rules.json` is an ordered list of `specs.Rule`s:
`(game, set)` — never set alone, since MTG's `neo` is not Pokémon's `neo1` — plus a
`Match` selector. Two kinds, deliberately: `numbers` (`188-216`, and `TG1-TG30`
never swallows card 12) and `ids` are read off the **card id**, so they answer
offline and for a library filed years ago; `rarity` / `subtype` / `finish` /
`full-art` / `frame` (Scryfall's frame generation — the thing that actually changed
MTG's border) / `effect` (a treatment layered on the frame) are read from the card's
own `.traits` marker, written at fetch beside `.layout` so choosing a spec never
costs a second API call.

**A rule with no set covers every set of its game, and that is the only way to say
something true of a frame *treatment*.** `extendedart` runs the art to the left and
right card edges in every set that ever printed one; enumerating those sets would be
a list that goes stale each release. So `set_id == ""` means "this game"
(`Rule.is_global`), and `Registry.for_set` returns **four bands, most specific
first** — this set's exceptions, the game's exceptions, this set's default, the
game's default — as a *stable* sort, so file order still decides within a band. Two
things that had to be got right: `assign` refuses a whole-set rule with no set (it
would claim every card of the game, which is what the game's fallback spec already
is), and the one-default-per-set replacement compares the **set id** rather than
calling `covers`, which a global rule answers `True` to for every set and which
would therefore have deleted every default in the file.

**An empty `effects` value is an answer, not a gap.** 93,190 of Magic's 116,233
printings carry no frame treatment at all, so `Match.EFFECT` reads a missing or empty
value as **false** rather than taking the generic trait path's `None`
("undecidable"). Left as `None` it put a warning on four cards in five the moment a
game-wide treatment rule existed. Same reading `full_art` has always had; only
`traits is None` — nothing recorded for the card at all — is undecidable.

**`specs.resolve` returns a `Resolution`, not a spec: which one, and *why*.**
`Via` names all seven ways — `override` (`border --frame`, this run) → `pin` (the
card's `.pin`, a decision someone stored) → `printing` (the provider said
borderless) → `rule` → `set-default` → `era` (the shipped baseline) →
`fallback`. Every surface reports it: the `border` readout, the `frames` tables,
`/api/cards`'s `frame_via`, the align panel's chip. **The `.pin` and `.frame`
markers are deliberately separate** — `.frame` is *derived* (a re-fetch rewrites it
freely, along with `.layout`/`.oversized`/`.traits`), `.pin` is a decision and a
re-fetch must never touch it.

Two things `resolve` must keep refusing to guess about, both pinned by
`tests/test_frames.py`: a trait rule on a card with no recorded traits is
**undecidable, not false** (`Resolution.undecided` — rounding it down would border a
secret rare as an ordinary card and say nothing), and an id that no longer names a
spec is **reported** (`Resolution.missing`) while the fit carries on at the next
answer down. `frames rm` refuses while a rule or a pinned card still names a spec,
for the same reason.

**Never silently fit against a guess**, still: `proxdex frames` lists the specs,
the rules and what your own cards resolve to (keyed by the *answer*, so a set with
a secret-rare rule shows both rows); `border` warns per card; the UI's align panel
shows the note and a Pin control.

**There is no confidence level, and removing it was a correction rather than a
simplification.** There were three — `MEASURED` (calipers), `SCANNED` (read off the
publisher's scans), `ESTIMATED` (typed) — and `SCANNED` was built on a false
premise: **a scan carries its own crop.** A scan trimmed 0.3mm inside the real cut
edge reports every border 0.3mm narrow, every card in the sample agrees with every
other, and nothing in the image says so. It is systematic, so ten cards agreeing to
±0.03mm measures the *scans'* consistency and not the card — and grading it
"trusted, nothing warns" dressed the guess up. (Pokémon's scans are visibly worse
about this than Scryfall's, which is the observation that killed it.) Collapsing the
three back into a boolean would have kept the same error, so what replaced them is
**prose**: `FrameGuide.note` says which card, which calipers, or that somebody typed
it, and every surface prints that sentence verbatim. One verb records a spec,
`frames set`, and correcting a shipped one is the *expected* path — the shipped MTG
numbers are working defaults that say so in their own notes.
`docs/measuring-frames.md` names the five cards that settle them.

**MTG has one bordered spec per frame generation, and the split is by *frame*, not
by set.** Scryfall documents exactly five `frame` values — `1993`, `1997`, `2003`,
`2015`, `future` — so that list is closed, and `frames.FRAME_GENERATIONS` covers all
of it, pinned by a test (which is how a sixth generation announces itself instead of
silently taking the fallback). **Proven exhaustive against the data, not taken from
the docs**: over Scryfall's `default_cards` bulk file — one object per printing,
116,233 of them — the distinct `frame` values are exactly `2015` (79,951), `2003`
(18,084), `1997` (12,303), `1993` (5,653), `future` (242), and those five sum to the
total, so they partition every Magic printing with nothing left over. A search census
agrees: `-frame:1993 -frame:1997 -frame:2003 -frame:2015 -frame:future` returns 0.
**Five values, five specs, no aliasing** — `future`
shared `mtg-2003` once, on the grounds that Future Sight's timeshifts print at the
same era's width, and they do not quite: a spec answering for a generation it was
not read off is the approximation this module exists to refuse.

The numbers each one ships with come from `scripts/mtg-census.py` reading Scryfall's
scans, and they are **provisional and say so in their own notes**. What that survey
*can* establish is the shape, because a crop error common to all the scans cancels
when two populations are compared the same way:

| frame | read off | top | sides | bottom |
|---|---|---|---|---|
| 1993 | 2ed (white) | 3.55 | 2.96 | 3.55 |
| 1997 | 5ed (white) | 3.38 | 3.05 | 3.38 |
| 2003 | 8ed (white) | 3.00 | 3.00 | 3.00 |
| future | fut | 2.93 | 2.93 | 2.93 |
| 2015 | m15 | 2.45 | 2.45 | (assumed) |

**Read off white-bordered core sets, which is what makes the bottom edge readable
at all.** On a black-bordered card the bottom border runs into the black
text/collector strip and no scan line can see where it ends — every black sample
reports the bottom at support 0.36-0.69 — but a white border makes that boundary
visible against the same strip. Support came out 1.00 on every edge, and the
black-bordered sets of the same generation agree to 0.05mm. That is a real result
about *relative* widths: **1993 and 1997 thicken the top and bottom** by 0.35-0.6mm
over the sides, **the 2003 redesign made all four edges uniform**, and **M15 took
~0.55mm off everything** — the reduction Wizards announced ("we've reduced the width
of the black border by almost a millimetre all the way around"). So "MTG's border
does not thicken at the bottom" is true from 2003 onward and false before it, and
`tests/test_frames.py` pins that *shape* rather than the numbers.

What the survey cannot do is fix the absolute width, and no rerun changes that —
see the confidence section above. Two further gaps the specs do **not** describe.
**Alpha** (`lea`, 302 of 5318 `frame:1993` prints) reads ~0.65mm off the rest of its
own generation; its print run was cut differently, and letting 302 cards pull the
spec for 5000 would be backwards, so pin those. And the **M15 bottom** cannot be
read even on a white border (one trusted sample at 2.54) because the collector line
sits in it — the ~6mm of black under an M15 card is border *plus* strip. It is
assumed symmetric with the top.

A **set** cannot answer which generation applies: a 2023 set holds retro-frame cards
at the old width beside modern ones (`dmr-354` is `frame: 1997` inside a
`frame: 2015` set). So the shipped baseline reads the printing's own frame —
`frames.baseline(set_id, game, traits)`, keyed on the card's recorded `frame` trait.
Pokémon still answers from its set-id era table; a game uses whichever its border
actually follows. Library rules are consulted **first**, so this is a default, not a
decision, and a card filed before proxdex recorded traits gets the fallback until
re-fetched. **MTG's fallback is `mtg-m15`**, because two thirds of all MTG prints
(71110 of ~106000) carry that frame, which makes it the least-wrong answer to "no
idea"; `Via.FALLBACK` still reports it, so `frames check` names the card and a
re-fetch settles it exactly.

That per-printing answer is also **why there is no coverage report** — see
`inventory.py`. A set-level row has no printing to read, so for MTG it cannot name
one spec, and the one that existed named the fallback: every modern set claimed
`mtg-2003` while every card in it actually takes `mtg-m15`, so the report called 1046
sets unmeasured while every one of their cards resolved exactly. Warnings replaced
it.

**Three sampling traps, each of which produced a wrong answer first.** An oversized
Vanguard or Planechase card is 89×127mm, so its border as a fraction of 63×88 is
nonsense — measuring those invented a "gold border" variant that does not exist and
made `frame:1993` read 2.85mm. Token sets have their own frame. And sampling
*newest-first* measures modern retro-frame reprints rather than the generation
itself, which widened every old frame's spread by ~0.2mm. The census excludes
`is:oversized`, tokens, promos and Secret Lair, restricts to `st:core or st:expansion`,
and orders oldest-first.

**Every combination of Scryfall's three frame fields has been surveyed, and almost
none of them matter (`scripts/mtg-variants.py`).** `frame` × `border_color` ×
`frame_effects` gives **114** populated combinations over 98,585 printable
standard-size cards; the 54 with 20+ printings cover 99.63%, and each was read over
15 cards sampled **proportionally to the population**. The result is the reason there
are not 114 specs:

- **31 combinations measure at their own generation's border.** A `legendary` crown,
  an `inverted` text box, the Nyx `enchantment` treatment, an `etched` foil, `snow`,
  `devoid`, `miracle`, `companion`, `draft`, `spree`, `colorshifted`, `tombstone` and
  `fullart` change the *picture*, not the border. So do white, gold and silver
  borders. None needs a spec or a rule, and that is now **measured** rather than
  assumed — `tests/test_frames.py::TestMeasuredVariants` guards it, so none of them
  can quietly start returning a spec.
- **`extendedart` is the one treatment that changes the geometry**, and it has a shape
  no four-edge inset described before: the art runs off the **left and right card
  edges** while the top and bottom keep the generation's border. That is `sides = 0`,
  which is exactly what `detect_inset` reports by refusing to measure them across
  every sample of 2,824 printings, with the top at 2.40 against an ordinary 2.48. It
  ships as `mtg-extended-art` and closes what used to be documented here as the one
  genuine gap.
- **`border_color: yellow` is a decorative band, not a colour**: 4.70mm sides against
  2.45, the largest error in the survey (2.25mm) over 79 printings. Ships as
  `mtg-yellow-band`.

Both are returned by **`sources.mtg_frame` from the printing**, like `borderless` —
a fact the provider stated about this card, so it lands at `Via.PRINTING`, above any
rule and below a pin. `borderless` is checked first, since a borderless extended-art
card has no border at all.

Three readings in that survey are **artifact, not finding**, and the module
deliberately does not act on them. Every `border_color: borderless` row reports a
"border" (1.52-3.81mm) because the detector is finding the *art's* own edge — those
are settled by the printing before any measurement is consulted. `showcase` looks
0.2-0.3mm off, but a showcase frame is a different bespoke design **per set**, so that
is variance across designs rather than an offset to encode. And the M15 *bottom* reads
~0.4mm thick on every black-bordered row (2.88 against a 2.45 side) because the
collector strip sits in it — which is why several combinations' "worst edge" is a
bottom edge that means nothing.

**`full_art` does not mean "no border", and treating it as if it did was a real
bug.** It reads that way and it is about the *art*: a full-art card's picture fills
the frame area and the black border is still there at its generation's width —
measured across six sets, full-art M15 printings sit at 2.45 ±0.10, the same as
their ordinary neighbours. `sources.mtg_frame` consulted it and returned
`borderless`, so those cards reshaped to pure aspect and printed the art into the
cut line, invisibly. **Only `border_color == "borderless"` decides** (plus the
`art_series` layout), and the two flags really are independent: `hoc-76` Sauron is
`borderless` with `full_art: False`, ZNR's pathways are `borderless` *and* full-art,
ZNR's basics are full-art with a black border. `borders.detect_inset` agrees
independently on the genuine ones — on `hoc-76` no edge clears `_TRUST` (support
0.14-0.59) and it reports `frameless`. Pinned by
`tests/test_frames.py::TestWhatTheProviderSays`, which is why `mtg_frame` and
`mtg_traits` are public: they are *readings* of one card object, testable without a
network round trip.

**Extended-art used to be the one genuine gap and is now a spec.** The art runs to
the left and right card edges while the top and bottom keep their border, which no
*symmetric* inset describes — but an inset is per-edge, so `mtg-extended-art` says
`sides = 0` and the fit is right. What made it look unsolvable was reading the
detector's refusal to measure the sides (support 0.36-0.59) as a failure rather than
as the answer.

**A spec is a number, and changing it invalidates masters that nothing else
would.** Every other invalidation in proxdex is about *pixels*; a corrected spec
leaves a bordered master fitted to numbers nobody uses any more, and it looks
perfect. So `border` records what it fitted to beside the file
(`.fit-bordered[_f2]` → `Card.write_fit`/`Card.fit`) and `doctor` compares that
against what the rules resolve today — the `stale-spec` finding, report-only like
every `doctor` finding that is a decision rather than a repair. A master filed
before proxdex recorded the fit is **not** a finding: nothing is known about it,
and inventing a comparison would be worse than staying quiet.

**Warnings, not coverage (`specs.audit`; `inventory.py` is only the preview now).**
`proxdex frames check` and the UI's Warnings tab call one function and report four
`Fault`s: `unreadable` (a `frames/*.json` that will not parse), `missing` (a pin or
rule naming a spec that is gone), `undecided` (a trait rule on a card whose traits
were never recorded) and `unknown` (nothing knows this printing's frame, so the
game's fallback applied). Every one is a **broken reference or an unanswerable
question** — none is a judgement about a spec's numbers, which is what the deleted
coverage report got wrong twice over: it graded specs it could not grade, and it
graded them per *set* when MTG answers per printing. `audit` takes `(card_id,
Resolution)` pairs rather than cards, so `specs` never imports `library`, and the CLI
and UI cannot disagree about what counts as a problem. The provider set-list fetch
(`sources.sets`, `SetInfo`, pokemontcg.io `/v2/sets`, Scryfall `/sets`) is **gone**
with it.

What survives in `inventory.py` is `preview`: **cards read one set at a time**
(`frames preview <set>`, `/api/frames/preview`), only to show which cards a rule
catches. A rule that cannot be previewed is a rule nobody should trust, and "every
card of every set" is minutes of API traffic to answer a question nobody asked. It
caches in `net.cache_dir()`, never in the library.

**The border step's frame setting is the one `OptKind.OPEN` option.** Its values
live in a library that is not open when `steps.click_options` runs, so `--frame` is
**not** a `click.Choice`: the command validates against that library's registry and
names the options in the error (`cli._spec`), `/api/meta` serves the list per
library, and `/api/step` checks it at the boundary (`webui._bad_setting`). Adding a
spec is visible immediately, with nothing restarted.

**cardbleed integration (`bleed.py`, `frames.py`).** proxdex reshaping runs
**in-process** over `cardbleed.bleed_card` (no subprocess). proxdex owns the
*inputs* — the era's target border widths (`frames.FrameGuide.inset`) and where
the border currently sits (align marks / `--inner-*`) — and cardbleed does the
fit (`cardbleed.geometry.solve_fit`): reshape to exactly 63:88 with correct
borders, optional `stretch` to hit them precisely. Never store card sizes/border
% in cardbleed — they're proxdex config. The border master is exactly 63:88 by
construction, so **`sheet` must never stretch** (default `fit = cover`).

**Sheet / production (`sheet.py`, `sheet` command).** Cut bleed and medium
colour-correction are **not baked into the card** — they're added at `sheet`
time, extended *outside* the trim, so the stored master stays a clean, neutral,
resizable card. A side is printable once grade is *settled* (done **or** skipped)
— `sheet.print_ready` — and it imposes `sheet.master()` = `best(edited, upscaled,
bordered, original)`, so skipping grade prints the upscaled rather than blocking.
Those two, plus `trim_mm`, live in `sheet.py` rather than the CLI: they are facts
about paper, and the web UI needs them too. PDFs are lossless via img2pdf
(Pillow's PDF export is avoided — it re-encodes to JPEG).

**An import is planned before it is filed (`imports.py`).** `import` is the one
command whose input proxdex did not make, so what each file *means* has to be read
off its name — the id it starts with, the stage (`upscayl` → upscaled), the side
(`_f2`, via `library.parse_stage_file`, so proxdex's own files round-trip). That
guess can be wrong, which is why `imports.plan(lib, items, on_existing)` is a
separate, **pixel-free** step over filenames alone, returning a `Run` of
`Assignment`s each carrying a `Disposition`: `new`/`create`/`replace` write,
`skip` keeps, and `collide`/`missing`/`no-side`/`unmatched`/`not-image` block.
`import --dry-run` and `/api/import/plan` are the same call, so the preview and
the import cannot disagree. Planning from *names* is also what makes the wizard
cheap: a folder of two hundred files is two hundred strings on the wire, the
thumbnails are the browser's own copies, and only the rows you keep are uploaded.
Three things it exists to stop happening silently: a **guessed** id creating a
card folder (only a confirmed `--id`/wizard row may, `Assignment.guessed_id`); two
files in one run claiming the same slot (`art.png` beside `art (1).png` — the
first keeps it, the second is named); and a `.DS_Store` being copied over a card's
scan (`IMAGE_SUFFIXES`). `OnExisting` is a **per-run** choice like every page
setting, not a config key, and it defaults to `overwrite` because that is what
`import` always did — the difference is that the plan now says so first.

**A print run is planned before it is rendered.** `sheet.plan(cards, cfg)` takes
(card, copies) pairs and returns a `Run` — what is in, what is not ready, and the
`Group`s of pages per trim size — using the same grouping `impose_to_pdf` does. So
`sheet --dry-run` and `/api/sheet/plan` (and the UI's live page count) cannot
promise a different number of pages than the PDF contains; there is one
implementation, not three. **Copies** are a first-class part of a run (`ID:4`,
`--copies N`, `SheetCard.copies`) because a playset is four of the same card, and
they are recorded in the batch manifest along with the profile and page settings —
a reprint should be reproducible, not remembered. Every page setting is an
override *for the run* (`cli._overrides` / `webui._apply_overrides`), never a
config edit.

**CLI/UI parity is two-way and load-bearing.** Anything one can do, the other
can: the UI's contact-sheet filters are `ls --only/--sort/--game/--set` (plus
`ls --json`, the same shape `/api/cards` serves); its data sheet is `proxdex show`;
its settings screen is `proxdex config show|set` (tomlkit, comment-preserving,
every value through `Config.coerce`); its batch list is `proxdex batches`; its
delete is `proxdex rm`; its frame-specs screen is the `frames` group — specs, rules and warnings, with
`frames preview` behind every Preview button, `frames check` behind the Warnings tab,
and a Pin control on the align panel; its
stored-images screen is `proxdex doctor` (the report read directly, the repair
shelled out as `doctor --fix --yes`); its **sheet
builder** is `sheet` with copies and per-run overrides (its live page count is
`--dry-run`); its **import wizard** is `import` with `--dry-run`/`--on-existing`
(its review table *is* the dry run, and every row is one `import <file> --id …
--stage … --face …` call, so the CLI stays the only implementation); its **print
screen** is `proxdex profile` + `proxdex calibrate`, one control per verb. Going the other way, `SheetBody.cards` lets the UI impose a
*selection with copies* (`sheet <name> <id[:n]...>`), `FetchBody.related` is
`fetch --related`, and `StepBody.auto` is `border --auto` — which is what makes the
bulk Border action useful, since nobody is dragging marks onto fifty cards.
**When you add a verb to one side, add it to the other in the same change.**

**A flag that opens a local app gets the browser's equivalent, never the
server's.** `search --open` (the first 12 result images) and `sheet --open` (the
finished PDF) hand a file or a URL to the desktop of the machine you typed the
command on. The web UI must not do that: the server may be another machine
entirely, and a tab opened after an `await` is a blocked popup. So the equivalent
is a **link, placed where you were already looking** — a `full ↗` on each search
hit (the provider's own scan, one card at a time instead of twelve tabs at once),
and a "Just imposed · Open fronts.pdf" panel under the Impose button, served by
`/api/pdf/<batch>/<file>`. For that, `/api/sheet` returns the batch it wrote,
found by **newest PDF mtime** (`webui._written`) rather than by rebuilding
`<date>_<slug>/<faces>.pdf` in the web layer — the CLI owns that naming.
`sheet` therefore takes `--open/--no-open` rather than a bare `--open`, and
**`/api/sheet` always passes `--no-open`**: a library with `[sheet] open = true`
would otherwise launch a PDF viewer on the server when someone imposes from a
browser. `cli._open_locally` is the one place a desktop app is launched, and it
knows `open` / `xdg-open` / `os.startfile` and gives up quietly on a box with
none of them.

**Web UI (`webui.py` + `webui.html`).** `webui.py` (FastAPI) reads the library
directly for *display/search/config*, but every *mutation* shells out to the
real CLI (`python -m proxdex --root … <cmd>`) — so the CLI is the single source
of truth and the UI never reimplements pipeline logic. **Every request body is a
pydantic model with `extra="forbid"`** (`StepBody`, `FetchBody`, `FlipBody`,
`SheetBody`/`SheetCard`, `BackBody`, `ConfigBody`, `ProfileBody`, `RenameBody`): card ids are pattern-validated (they
become argv), a side is `Annotated[int, Field(ge=1, le=2)]`, enums are enums, and
a step's `settings` are checked against that step's declared schema before
anything is spelled as a flag. An unknown key is a 422, not a silently dropped
option.

The card view is a "pipeline console": left rail = a **filmstrip** stepper whose
frames carry each stage's real image for the focused side, right panel = that
step's settings (rendered from the schema `/api/meta` serves — the UI spells no
step name or option itself) plus Run/Skip/Reset, centre = the proof. A done step
shows its **output** — and offers a compare tool above the card with three
mutually-exclusive modes: `Result` (the output alone, the default), `Wipe` (the
output clipped over its input along a draggable split) and `Fade` (a cross-
dissolve, with a blend slider). `C` cycles them, and the tool only exists where
there really are two stages to compare — it stands down while the align marks own
the viewer. A step that hasn't run shows its input **undimmed and unobscured**
(`.viewer.is-input` = dashed frame + a badge above the card), because you cannot
judge a grade or place a border on a greyed-out card. **There is no lightbox** —
the proof is height- and width-bounded to be as large as the viewport allows, and
a link offers the full-resolution file. Two-sided cards get a side tab strip; the
side is in the URL as `?side=N`.

The border tool has no mode: the marks are live whenever Border is focused (a
`Show the align marks` checkbox in the panel is the only switch, defaulting off
once there *is* a result to look at, since the marks live over the source). A
**loupe** shows source pixels at 6× on a crosshair while dragging, parked in the
corner of the proof's own column furthest from the pointer; arrow keys nudge the
selected mark and four numeric fields take an exact inset. A **cyan** ghost
outlines the trim the fit will produce and shades the target border band — cyan
is measurement, magenta is control, and the two never read as the same thing.
The JS `solveFit` **mirrors cardbleed's `solve_fit`**, and
`tests/test_fit_parity.py` now holds the two to it: it cuts `solveFit` out of
this file, runs it in node over fifteen fits (stretched and not, art too wide and
too tall, targets already over-shot, lopsided guides) and compares trim, borders,
extensions and shaved edges against the Python. Change one and the test names the
case the other stopped agreeing on.

Full width beneath it sits the **card-data sheet** — `/api/details/<id>` (a live
`sources.details` call, the one place the UI hits a provider for display)
rendered as fact columns plus its outbound links. It is fetched once per card
into `state.details` and repainted from there, since nothing about it changes
when a step runs.

**Re-render only what changed.** Creating a fresh `<img>` makes the browser paint
an empty box before it decodes even a file it already holds — so a full teardown
*is* the flicker. `_built` records the `id/face/rev` the card shell was built for:
focusing another step restates the rail in place (`paintFilm` — classes and the
status word as text, an image touched only when its file changed), repaints the
proof and rebuilds the step panel, leaving the rail, face tabs and card-data sheet
alone. Typing in the library filter hides tiles rather than rebuilding the grid
(`filterInPlace`, which bails to a real render when the empty state is involved).
Images carry a `skel` plate with a *delayed* sheen, and `unskelAll` clears it
synchronously for anything already `complete`, so a cached image shows no loading
state at all. Focusing a step does not scroll to the top — it is not a new page.
**Adding a render path? Ask what it destroys.**

**Routing + speed.** The UI is a real SPA with real URLs: `/library`,
`/card/<id>[/<step>][?side=N]`, `/search?q=…`, `/import`, `/sheet`,
`/print[?p=<profile>]`, `/settings`, driven by the History API
(`navigate`/`routeFromUrl`/`popstate` in `webui.html`, plus per-entry scroll
restore). `webui.py`'s `spa` catch-all — **registered last, or it shadows
`/api/*`** — serves the shell for those paths so a reload, bookmark or pasted
link works; an unknown root still 404s. A card is addressed by **id, not grid
index** (and a side by `?side=`), so links survive a reload and a changing
library. A view switch is a
pure client-side render: cards/meta/config/last search results are held in
`state` and never refetched to change page. Images are versioned instead of
cached-busted by hand — `/api/cards` returns a `rev` per card (its newest stage
mtime) that goes into every image URL, so responses are `immutable` and a
changed file simply becomes a different URL; derived JPEGs are memoized per
(file, mtime, box) and answer `If-None-Match` with a 304 (24ms → 1ms), and
JSON/HTML are gzipped.

The UI is styled on **Bootstrap 5.3, vendored into `src/proxdex/static/`** and
served from `/static` — never a CDN, so it works offline. On top sits a small
theme layer in `webui.html`: colourless chrome (you are judging card colour on
these screens), four surfaces one value-step apart so elevation never needs a
shadow, one 4px spacing scale, and mono type for every id, dimension and
percentage. Two accents, both borrowed from marks a press really prints —
**registration magenta** for every control (and for the crop-mark corner
brackets, `.marks`, which hover on contact-sheet tiles — never around the proof,
where nothing may compete with the card), **registration cyan** for the one thing
that is a measurement rather than a control (the target border). `/api/config`
returns an `options` map *and* a `docs` map derived from `Config`'s own field
metadata, so a setting renders as a described form row, with its unit and real
default, and no extra wiring. `webui.html` and `static/` ship via hatch
`force-include`.

**Config (`config.py`).** One `Config` dataclass whose every field carries its
own documentation: `setting(default, label=…, help=…, unit=…)` returns a
`dataclasses.field` with metadata, and `Config.describe()` serves it to
`/api/config`. That is why the settings screen can be a real form rather than a
list of raw keys, and why there is exactly one place to edit when a setting's
meaning changes. Per-library settings live in `proxdex.toml`, edited
comment-preservingly via tomlkit (in the UI). It also
owns the sheet/print/tool vocabulary enums (`PageSize`, `Orientation`, `Fit`,
`Faces`, `DuplexFlip`, `GuideStyle`, `GuidePlacement`, `RegMarks`,
`UpscaylScale`, `UpscaylModel`) — `Config.load` reads the dataclass' own type
hints and **coerces every value into its enum** (`Config.coerce`), raising
`ConfigError` naming the valid options, so a typo fails at load instead of
silently picking a default. `Config.field_name` owns the `[section] key` →
field rule, so `load` and the UI's config editor can't disagree. Only the
print profile stays `str` (a measured calibration can invent a name). Every
Upscayl setting is a closed set: model and scale are enums in config, the CLI
(`click.Choice`), the UI's dropdowns (`/api/meta`) and the `/api/step`
request boundary — so **custom Upscayl models are not supported**; add a
`UpscaylModel` member instead. The `init`-time template is `DEFAULT_TOML` in
`cli.py` — keep its defaults in sync with `config.py`.

**Grade is a look, and nothing else (`grade.py`).** Brightness, contrast,
saturation, gamma, plus an optional per-card `levels` stretch. It does **not** try
to normalise cards against each other. An earlier version did — it read each
card's frame colour and white-balanced every frame to one shared target — and that
is wrong at the premise: a card frame is yellow on a Pokémon card, black on an MTG
one and absent on a full-art print, so there is no common baseline. Measured, with
a mixed library the shared target came out olive and a neutral grey inside a
yellow-bordered card graded to **(19, 19, 129)** — deep blue — while the same grey
in a black-bordered card blew out to white. Do not reintroduce it. Matching the
*medium* is real but belongs at print time, where the paper is the same for every
card on the sheet.

**Print profiles (`profiles.py`, `media.py`).** A profile is one medium you own:
a name, **the user's notes**, the starting `media.Recipe` it came from, and the
calibration `Round`s measured on it, stored as `<root>/profiles/<name>.json`.
`[print] profile` names the active one and `sheet --profile` overrides per run. It
is a file rather than config keys because a medium is a *thing you own* (it wants
notes — the paper, the printer setting, whether colour management was off), its
correction is measured data rather than a preference, and two media coexist.
**Nothing ships pre-filled, and there are no presets.** One built-in name,
`profiles.NONE` = `none`, resolves without a file and is the identity; it is
reserved, so a real profile can never shadow "leave my cards alone", and it cannot
be edited or calibrated. A new profile starts at identity too. Numbers proxdex
invented for a printer it has never seen are a guess wearing a label — "foil needs
saturation 1.38" described exactly one setup — and shipping them as a "starting
point" invites people to print through a correction nobody measured.

**A profile is defined one of two ways, and `Profile.how` says which:**
`measured` (calibration rounds), `by hand` (a non-neutral `media.Recipe`), or
`identity`. A measurement **supersedes** the numbers entirely — one was printed and
scanned, the other was typed — and the by-hand numbers are then kept only as the
record of where it started.

**The by-hand route has to be judged on paper, so it gets tools for that.**
`profile strip` (`media.vary` → `sheet.labelled_page`) prints one page of the same
card at a row of values for **one** knob, each tile at true card size and labelled
with its value: you print it on the medium, read the label under the one that looks
right, and `profile set` it. `profile preview` is the cheap screen version — good
for direction, not for amount, and it says so. Vary one knob at a time; a page
where two things changed tells you which page you like, not which value to keep.

**Fronts and backs can be different media** (`[print] back_profile`,
`sheet --back-profile`, `profiles.active_back`, `_Repro.cell(back=...)`). Unset
means "the same as the fronts", which is right for duplex — one sheet of paper —
but it is not *always* one medium: the reverse of a one-sided glossy stock is a
different surface, and a backs-only run often goes on other paper. Card backs take
the back profile whatever the faces mode; a fronts-only run prints no backs, so
`/api/sheet/plan` reports no back profile at all rather than naming a correction
that never happens.

**Calibration is a loop on one sheet (`calibrate.py`).** `chart_page` renders a
page with the chart in **one `Slot`** of a 2×3 grid, so you print, scan, record,
then feed the *same paper* back in and print the next slot — six rounds per A4.
Every round keeps both halves of the evidence (`sent` and `scanned` patches) and
the correction is refitted over **all** rounds at once (`calibrate.fit`), so a
round makes it truer instead of replacing what came before; round 1 prints the raw
target, every later round prints the target through what is known, which samples
the space where the cards live. Nothing is cached that cannot be rederived from
the measurements — `Profile.correction` refits on read.

**The chart's patches are placed by measurement (`calibrate.Chart`, `CHART`).**
80 patches — a 16-step neutral ramp plus a 4×4×4 lattice over 50..200 — and every
part of that is a measured choice, not taste. The lattice is pulled *inside* the
printable box because a patch at pure red or 255 white clips and is dropped from
the fit: an earlier 36-patch chart of primaries wasted 24 that way, leaving ~12
usable samples for a 10-parameter model (same press, same code path: it settled at
2.31 mean RGB, this one at 1.36). Density is bounded by patch *area*, not by how
many colours you can name — 228 patches measured worse than 80, and 512 worse than
36, because read noise and neighbour bleed grow faster than coverage helps. A
continuous gradient is worse than either: nothing flat to average, and 1% of
geometric error is a correlated 2.3 levels in every reading. A 3-D LUT lost to the
polynomial at every density tried. **Do not "just add patches", and do not reach
for a spectrum.** Changing the patch set invalidates stored rounds — `_read_patches`
rejects any whose shape no longer matches, and `Profile.unreadable` counts them so
it is never silent.

**One medium, one gamut, and the loop says when it is done.** Reachability is a
fact about the paper and the inks, not about one sheet, so it is read from every
live round pooled (`Profile.gamut`) and every round is scored against that same
mask (`Profile.score` → `calibrate.score`, which takes the mask as an argument
precisely so *whose* gamut it is has to be stated). Scoring each round against its
own scan made the trend compare means over different patch sets — 63 to 68 of 80 on
a real matte — so the number moved when the set moved rather than when the print
improved. `Profile.plateau` then names the tail of rounds that bought nothing
(`_FLAT_ROUNDS` in a row under `_FLAT_GAIN` mean RGB each, judged on the *best*
round either side so one bad sheet is not read as progress stopping), and
`calibrate add` says so **instead of** inviting the next round. That threshold is a
judgement about your paper and your afternoon, not a noise floor — read noise
barely survives a mean over ~70 patches (one level per patch moves it by 0.1).

**A round is switched off, never deleted (`Round.enabled`, `Profile.live`).** The
correction fits over the live rounds and refits on every read, so switching a
round back on restores exactly what it was doing — which is the only way to
compare with and without. `Profile.influence(n)` refits without one round and
reports how far the correction moves (its *pull*): a round pulling much harder
than its neighbours is either the most informative measurement or an outlier.
Measured on a deliberately botched scan: pull 17.7 against 5.6 / 5.4 / 4.4.
Numbering never shifts, because nothing is removed.

Three things there were got wrong once and must stay right: the ridge is **small
and absolute** (`_RIDGE`, not proportional to the sample count — a ridge that
grows with the data damps every round you add, which is backwards for a loop that
exists to improve with measurement); samples whose *send* value is pinned at 0 or
255 are **dropped from the fit** (`usable` — several wanted colours clip to the
same send, so the pair says nothing about the invertible response and only drags
the polynomial); and the reported error covers only the patches the medium can
**reach**, with the clipped count named separately — paper is not 255 and ink is
not 0, and averaging in colours that can never be hit gives a number that can
never fall. **A gamut is a solid, not a box**: `in_gamut` decides reachability by
inverting the print's *own* response (fit its pairs, ask what send each target
would need, reachable iff that lands in 0..255). Comparing each channel against
the print's extremes — which is what it did first — passes every colour that is
inside the box on all three channels and still unprintable, i.e. every saturated
one: a real matte profile reported 17.7 mean RGB over "76 reachable" patches while
the 67 it could actually hit sat at 12.7, blaming the calibration for missing
colours the inks do not contain. Verified end to end
against a simulated press: `21.5 → 5.0 → 4.0 → 3.7 → 3.3 → 2.9` mean RGB over six
rounds, and `~4.6` with scanner noise. A bad round (a crooked scan) is ruinous at
any ridge weight, so the defence is naming it (`calibrate add` compares against
the best round so far) and being able to hold it out (`calibrate disable`).

## Conventions

- **Type as strictly as the language allows.** Every closed set of values is a
  `StrEnum`/`IntEnum` or a `Literal` — never a bare `str` holding `"cover"`,
  `"pokemon"`, `"done"`. Untrusted input (TOML, CLI args, JSON bodies) is
  *coerced into the enum at the boundary* (`Config.load`, `click.Choice`,
  request parsing) and the enum is what travels inwards; a stringly-typed
  comparison that silently falls through to a plausible default is exactly the
  bug class this prevents. `StrEnum` members serialize themselves for JSON,
  TOML and filenames, so the boundary stays cheap.
- **Absolute imports only.** `from proxdex.config import Config`, never
  `from .config import …` or `from . import frames`. ruff enforces it
  (`flake8-tidy-imports.ban-relative-imports = "all"`, TID252).
- **No auto mode.** Steps are per-card, per-side do/skip; do not add batch
  auto-runners. (The UI's bulk actions are still *one* step, chosen explicitly,
  over a selection the user made — that is an id list, not an auto-runner.)
- **A step is declared once**, in `steps.py`. Do not add a parallel step list to
  the CLI, the API or the JS; derive from the registry instead.
- **Faces are a `face: int` parameter defaulting to `FRONT`**, never a separate
  code path. A card is one card whether it has one side or two, and at most two
  sides are supported (`sources._MAX_FACES`, `webui._MAX_FACE`).
- Grade is stage 4 (**after** upscale) so it's WYSIWYG — Upscayl shifts contrast/
  saturation itself.
- **Grade never compares one card to another**, and the medium is never baked into
  a master. If a colour problem is the same for every card on the sheet, it is the
  paper's, and it belongs to a print profile.
- ruff `select = ALL` with a curated ignore list (see `pyproject.toml`); pyright
  `strict` (numpy/pillow `reportUnknown*` disabled). Both must pass.
- Frame specs must stay honest, and the mechanism is the **note**, not a grade: a
  `FrameGuide` records where its numbers came from in words, and every surface
  prints that sentence. Do not reintroduce a confidence level or a `measured`
  boolean — a border read off a publisher's scan inherits the scan's crop, which no
  sample size detects, so any grade that calls such a reading trustworthy is the bug
  this replaced. `borders.detect_inset` stays honest the other way, by reporting
  per-edge support so the UI/CLI can name the edges that disagreed rather than
  presenting four numbers as equally sure.
- **Never reference a top-level object by name from an inline HTML handler.** An
  inline handler's scope chain starts at the *element*, and an `HTMLElement` has a
  legacy `align` property — so `onchange="align.show=…"` silently writes to a
  discarded `String` wrapper. Every handler calls a named function
  (`toggleMarks(this)`, `resetMarks()`, `setSearchRelated(...)`).
