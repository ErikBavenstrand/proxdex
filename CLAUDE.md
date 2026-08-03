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

**`uv.lock` is committed and CI checks it** (`uv lock --check`, in the lint job). Without
that check the lock rots silently: a dependency added to `pyproject.toml` and never locked
keeps working everywhere, because `uv run` re-resolves on the fly — right up until somebody
installs from the lock and gets a different set. It is also what gives `setup-uv` a cache
key; before the lock was tracked the cache was created, never invalidated and never hit.

```bash
uv lock --check                           # the lock agrees with pyproject (CI runs this)
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
- `tests/test_coverage.py` — `inventory.assess`, because **the number is the whole
  report**: "21 of 174 sets covered" decides whether there is an afternoon of measuring
  left, and nothing on screen can contradict it. It pins the thing the deleted coverage
  report got wrong (each game asked the question its own border followed) and the count
  that follows — MTG is *5 of 5 frame generations*, never the 12 of 12 the first version
  reported by adding its four set exceptions in with its generations. Plus what may not
  pass for coverage: a numbers rule that claims part of a set, a rule pointing at a spec
  that is gone, and the `ex4`/`ex5` boundary a prefix key would have over-claimed.
- `tests/test_upscale.py` — that a missing upscaler disables *running* the step
  and nothing else (the stage, the skip and an already-upscaled image all
  survive), that the probe answers instead of raising, and **which factor a card
  gets**: the derivation's output is a file size, and a file size looks fine at any
  value, so the reference row is a real card (`base3-4`, 627px → 2508px) and the
  ladder's cliff is pinned by the 600px case that fell off it.
- `tests/test_config_prune.py` — that pruning removes exactly the keys nothing reads
  **and their comments**, leaves every live setting and its prose alone, and yields a
  file `Config.load` still agrees with. It rewrites the one file a person typed by
  hand, and each way it can go wrong (a live setting silently reverted, a file left
  unparseable, prose left describing a deleted feature) surfaces much later.
- `tests/test_profiles.py` — `profiles.named`/`dangling`, for the same reason as the
  import plan: one answer, four consumers (`where`, `profile list`'s marker *and* its
  legend, `/api/profiles`), and a failure invisible until the end of a print run —
  a `[print] profile` naming a profile that is gone.
- `tests/test_browse.py` — `browse.gather`/`Query`/`Page` and the two provider query
  spellings, for the import-plan reason (one pure function, several consumers) plus one
  of its own: **a paging window that is off by one silently loses or repeats a card.**
  Ten pages of thumbnails say nothing about it. It pins Scryfall's fixed 175-card pages
  being cut into display pages (`sources.mtg_page_span`), that `set.releaseDate:[a TO b]`
  is *not* how a year is filtered (it is a real 400; a prefix wildcard is), and that a
  colour filter is **ORed** — Scryfall's bare `c:wu` means a card that is both, which for
  two colours is a handful of cards and reads as a broken filter.
- `tests/test_art.py` — the provider-art cache, and **not for its speed**: it is a
  fetcher that takes a URL, so the host check is the only thing between "shrink a set
  logo" and "GET anything you like from a process on your machine" (a lookalike host
  and the allowed host as a *path segment* are both pinned); a logo's alpha must
  survive the re-encode or every set tile gets a grey box behind it, which no test
  that only measured bytes would catch; and the cache is keyed by URL **and** size, so
  the wrong size for the right picture — invisible, since CSS scales either — cannot
  be served.
- `tests/test_progress.py` — that a running command's count reaches *another process*,
  which is where every failure mode is invisible. A sink silently on would litter a
  terminal user's directory; silently off would leave the UI with the spinner this
  replaced; and **an unknown total stays unknown** (`fraction` is `None`, never a
  guess), which is the same rule as every other number here. The reader is total,
  because it reads a file something else is mid-write on.
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

**Every card is imposed — and fitted, and examined — at its own size**
(`sheet.trim_mm`, aliased `cli._trim_mm`; also `bleed.fit`, `doctor`'s aspect check
and `/api/frame`, so nothing downstream reshapes a card to a size it will not be
printed at).
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
An `optional` option carries its own `auto_label`, because what "unset" *means* differs per
option — border's frame falls back to the card's set, upscale's factor to whatever reaches
the target resolution — and the UI used to hardcode the frame's wording for both. And a
flag is spelled from `StepOption.flag` (`target_dpi` → `--target-dpi`) in **one** place, so
`click_options` and `argv` cannot disagree; the first multi-word key found that they had,
and the UI would have sent the CLI an option it does not have.
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

**Finding a card is two questions, and `browse.py` is the second one.** proxdex could
always *search* — a name, and the API's answer — which is useless when what you know is
the set, which is most of the time. So there is a **set index**: `Expansion` (id, name,
group, counts, release date, logo and symbol), `Group`, and one request per game for the
lot, cached a day.

**The scrydex API is not what this reads, and it does not need to be.** Its endpoints
(`/cards`, `/expansions`, `/sealed`, `/listings`, `/price-history`) are gated behind
`X-Api-Key` + `X-Team-ID`, and its expansion *pages* are a rendered site — scraping one
would be a fourth data path that breaks silently on a redesign and reports a plausible
wrong number until somebody notices, which is the deleted border detector's exact failure
mode. Every fact those pages show is already documented JSON on the two APIs proxdex
already talks to: pokemontcg.io `/v2/sets` carries `series` — which **is** the grouping
scrydex's Pokémon page shows — plus `printedTotal`/`total`, `releaseDate` and
`images.logo`/`images.symbol`; Scryfall `/sets` carries `set_type` — which **is** the
grouping its Magic page shows — plus `card_count`, `released_at` and `icon_svg_uri`. The
set art is therefore the provider's own URL out of the same response, never one guessed
from an id pattern: a guessed URL 404s as a *blank tile* rather than as an error.
(scrydex's set art is reachable unauthenticated at
`images.scrydex.com/pokemon/<set>-logo/logo`, and `Config.scrydex_url` does use that host
for the card scans themselves — but for set art it would be the same picture behind a
guess.)

**The two groupings are not the same kind of fact**, which is the whole of `Grouping`. A
Pokémon series is an **era**: it has a date, Sword & Shield really does come after XY, so
the groups sort by date with the newest first. An MTG `set_type` is a **kind of product**:
a Commander deck is not "later" than a core set, so those follow a curated order and a
date would reshuffle them meaninglessly every release. An unlisted `set_type` sorts last
and keeps a titled version of its own name — Scryfall adds one every few years, and hiding
a whole kind of product is worse than showing it in the wrong place.

**Browse and search are one query, and that is why there is one of everything.**
`browse.Query` carries the text *and* every filter *and* the page; browsing a set is a
Query with a set and no text. So `/api/search` answers both, `search` and `browse` are the
same CLI call with the set arriving as a flag or an argument, and the UI's two screens
share `state.find`, one filter bar, one result grid and one pager. Same argument as
`imports.plan` and `sheet.plan`. A query that narrows *nothing* returns an empty page
rather than the first screenful of every card ever printed.

**A typed name is *one* wildcarded term, never one term per word (`sources._pokemon_query`).**
A space separates *terms* in pokemontcg.io's syntax, so one term per word turned `Moo Moo`
into `name:*Moo* name:*Moo*` — two identical substring tests that any name holding "moo"
once satisfies, making it exactly equivalent to searching `Moo`. It answered with
Amoonguss, Bloodmoon Ursaluna and Roaring Moon and buried the card asked for under
everything printed since. `name:*Moo*Moo*` returns the five that exist. Joining is also
what copes with Pokémon's unreliable separator: the card is **Moo-Moo Milk** in Neo and
**Moomoo Milk** in HeartGold, so neither `name:"Moo Moo"` nor `name:"Moo Moo Milk"` matches
anything at all (both measured at 0), while a wildcard where the user typed a space matches
a space, a hyphen, a dot or nothing — `mr mime` finds *Mr. Mime* (35), `char zard` finds
*Charizard* (108). **Scryfall is deliberately left per-word**, and that is measured rather than assumed: the
phrase form is *more* precise where it works (`name:"Serra Angel"` is 1 hit against the
per-word 2) but `name:"kiki jiki"` returns **0** where `name:"Kiki" name:"Jiki"` finds
Kiki-Jiki — it breaks on exactly the hyphens the Pokémon join exists to cope with. Its own
flaw is real and unavoidable: repeated words collapse there too (`Moo Moo` is `Moo`, 138
cards), and no Scryfall spelling fixes that without the hyphen regression. So the two
providers' spellings differ because the providers do — the same reason `sources._MTG_THUMB_KEYS` is a
key list and Pokémon's thumb is a URL template.

**Every filter is pushed to the provider, because a local sieve cannot count.** The old
`search` fetched a hundred rows and filtered them in Python, which could only ever report
"100 results" for a set of 553 — and page 2 re-fetched the same hundred and filtered it
again. Both APIs filter and count server-side, so both are asked to: `Page.total` is the
provider's own count of everything that matched, which is what lets a screen say "60 of
553" and offer a last page. It is **-1** when a provider will not say, so a caller can
tell "none" from "unknown" instead of reading a confident 0.

**Scryfall pages at a fixed 175 and will not be talked down**, so a display page of 60
straddles a boundary two times in three and `sources.mtg_page_span` decides which of its
pages the window covers (at most two for a 60-card page, three at the 250 maximum, all
cached). Verified end to end: four pages of 60 over `dft` return 240 rows, 240 of them
unique. pokemontcg.io takes any page size up to 250 and reports `totalCount`, so that side
is one request. **A page past the end is a nearly-right link, not an empty answer** — the
CLI names the last real page and the UI silently retries at it, because a blank grid
saying "nothing matched" over 92 cards that did is a lie about the query.

**The filter vocabulary is the provider's, served not spelled.** `browse.facets` reads
pokemontcg.io's `/v2/rarities`, `/v2/supertypes`, `/v2/types` and `/v2/subtypes` — that
question, already answered — and Scryfall's `/catalog/card-types`, with MTG's rarities and
colours as enums here because Scryfall publishes no catalog of them. `/api/facets` serves
it per game and the UI builds its dropdowns from that, exactly as the step panels come
from `/api/meta`. **A facet whose catalog request failed is dropped**, because an empty
dropdown reads as "this game has no rarities" rather than "that request failed" — and
these are asked in order to *draw a control*, long before anyone has searched, so the
reader is total like `upscale.availability`.

**Picking cards is state, not DOM, and that was a bug worth naming.** The selection was
read straight out of the grid (`#results input:checked`), so **turning the page threw it
away without saying so** — you would tick eight cards across three pages and add the last
two. It is `state.pick`, a Map id → row, so it survives paging, filtering, switching
between Browse and Search, and (via `sessionStorage`) a reload, which is the property
every other screen here already has. It holds the *row* rather than just the id so the
tray can show you the thumbnails: a count alone does not tell you whether the eight you
picked are the eight you meant.

**A result tile is a *cell*, not a picture with things loose beneath it.** The meta text
sat between the image and the Add button and wrapped to two, three or four lines
depending on the card — so every button in a row landed at a different height and read as
floating between two cards rather than belonging to one. Three things fix it together:
every meta line is truncated to exactly one (the full text is on the tile's `title`), the
cell is a flex column whose meta area takes up the slack, and the grid stretches the row.
The **hover lights the whole cell**, which is what says those three things are one card,
and the picture's border takes the same two states the contact sheet's tiles use — so a
picked card looks picked the same way everywhere in the app. The whole tile is the pick
target, with `pickHit` ignoring any click that came from a control inside it.

**The contact-sheet tile is a *docket*, and it took two collisions and a redesign to get
there.** It was the screen that "looked weird": tiles of different heights in one row, and
a card's id and pin chip printed *outside* the tile's own box. Two of the three causes were
the `.setgrid` bug again (see the conventions), found by the duplicate-selector sweep:

- **`.slot` was defined twice** — the contact-sheet tile, and the calibration screen's
  slot map. The later rule wins, so **every library tile was being forced to the slot
  map's `aspect-ratio: 1200/1350` with `justify-content: center`**, which made the tile
  *shorter than its own contents*: that is what put text outside the box, and centring is
  what made each tile in a row a different height. The slot map is now `.calslot`.
- **`.sheetgrid` was defined twice** — the contact sheet's density grid and the sheet
  builder's two-column page. The builder's is now `.sheetpage`. Milder (the density rules
  are two classes, so they still won `grid-template-columns`) but it took over the contact
  sheet's row gap, and `$('.sheetgrid')` in `filterInPlace` matched whichever came first.
- **The third cause was real fragility, not a collision**: the meta was two free-flowing
  chip rows, so `pin: pokemon-ex-plain` — the widest string on the screen by a factor of
  three — ran **113px past the tile** at `sm` density.

What replaced it is a shape that cannot do any of that: **two docket lines of fixed
height, each a flex row where exactly one child may grow and truncate** (`.dline > .grow`,
`min-width: 0` on both, `white-space: nowrap`). Nothing wraps, so no tile can differ in
height from the one beside it, and nothing can leave the box. Measured 0px of row spread
and 0 overflowing elements across three densities and four viewport widths down to 420px.
Four decisions in it:

- **A card needs a *seat*, and that is the one thing the two games really differ on.** An
  MTG card's border is black on a `#0c0c10` page, so at the hairline `--rule` it had no
  visible edge at all — a grid of Commander Legends read as art floating on the ground
  while the Pokémon sets read as cards. Both tiles now ring the picture in `--rule-2`, the
  token whose documented job is "a rule that has to be seen": darker than Pokémon's
  yellow, lighter than MTG's black, one ring for both.
- **What the *printing* is goes on the picture's foot, not in the docket** (`.slot .flags`,
  `kindFlags`): two-sided, oversized, borderless, pinned. Several can apply at once, so
  down there a fourth is clipped by the card's own edge instead of making the tile taller.
  A pin says `pin`, not `pin: pokemon-ex-plain` — *which* spec is a card-page question, and
  the name was what overflowed. `kindChips` still spells them in full on the card page,
  where there is room and a `flex-wrap` to hold them.
- **The game chip is shown only when the library holds more than one game.** Fifteen
  identical `POKEMON` chips were the least surprising fact winning the row the card's own
  id has to fit in. It is worth ink when it varies.
- **Inside a set, the set is not news — and it was the fact getting the room.** Every tile
  of a set view read `Commander Legends · 2020 · 7…`: two values identical on all sixty
  tiles, with the collector number, the one thing that tells this printing from the next,
  truncated off the end. `hitsHtml` now leads with the number inside a set and with the set
  across a search — three lines either way, since a tile that changed height with the query
  would ripple through a grid that is finally level.

**The crop marks belong to the card, not to the tile.** `.marks::after` draws at
`inset: -7px` around whatever carries the class, and that was the whole `.slot` — so the
brackets framed the picture *and* the two lines of text under it, which is not something a
press marks the corners of. They sit on the picture wrapper now, inside the tile's own
padding, which is what makes that padding load-bearing.

**And a 13px checkbox is not a selection.** It sat in the corner of the art, invisible on
a busy Charizard and uncountable over sixty tiles. A picked card now takes the accent on
its own frame, lifts, and carries a badge big enough to read — with the same badge slot in
green meaning "already filed", so the eye reads the *colour* rather than hunting for two
different marks.

**Two ways to add, because they are two intentions.** `+ Add` under one card fetches it
now (and becomes `✓ In your library` when it lands); the tray's one button fetches
everything picked. The tray **groups by the row's own game** and makes one call per game:
`/api/fetch` takes a single game for all its ids — correctly, since that is the CLI's
shape — and the basket outliving the screen means it can hold a Pokémon card picked in
Search while you browse Magic sets, so the *screen's* game would have sent Scryfall a
pokemontcg id.

**Both `have` and the owned counts are derived from `state.cards`, never from the
response.** The server answers them when it draws a page, and that answer is stale the
moment you add something: adding three cards left every set tile and the "n of m in
library" line showing the old number until something re-fetched the whole set list.
`state.cards` is refreshed by every mutation, so counting it client-side is both correct
and free — the same call `haveCard` makes for the tick on a hit.

**Whether you already hold a card is answered locally, per row.** `have` on every search
hit and an `owned` count on every set tile, straight off the card folders — free, and the
reason the index is worth re-opening. `browse.owned` takes *ids* rather than cards, so
this module never imports `library`, the same call `specs.audit` makes.

**Paging the library is not the same problem, and is done by slicing.** `ls --per-page`
and the contact sheet page what is already read: a library is local, so every card is in
hand the moment the first one is, asking a server for a window would be slower *and* could
disagree with itself between two pages, and server-side paging would break the instant
filter (`filterInPlace`). It is a rendering concern, so it is rendered — and `ls` defaults
to no paging at all, because a listing of your own cards should print in full unless you
ask otherwise. `ls --json` therefore keeps serving the bare list `/api/cards` mirrors, and
only wraps it in a page envelope when paging was actually asked for: a shape that changes
with a flag is one every reader has to branch on.

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

**The image host cannot validate a card id, which is why the metadata API is asked
first.** A Pokémon card's picture needs *nothing* from pokemontcg.io — the URL is
`Config.scrydex_url.format(id=...)`, derivable from the id alone — so it looks as though a
metadata outage should never cost a download. It has to, and the reason is that
`images.scrydex.com` answers **HTTP 200 for an id it does not have**, serving a
byte-identical grey placeholder (verified: `base1-999` and `zzzz-1` return the same file).
The status code therefore cannot tell a hit from a miss, and a mistyped id would file a
grey rectangle that `border`, `upscale`, `grade` and `sheet` all process without
complaint — discovered on paper. The lookup is what proves the id exists.

The placeholder is still worth *detecting*, and `sources.is_placeholder` does: it is
**640×892 in palette mode**, where every real scan is 600×825 RGB (the older sets) or
~734×1024 RGBA (the modern ones). `download` refuses it rather than filing it. Both
conditions are required deliberately — a real card must never be refused, so the check
going out of date costs a placeholder filed as it would have been anyway, not a good card
rejected. Coverage is good in practice (30 real ids across ten sets, 1999-2026, all real),
so this is insurance rather than a routine path. Pinned by `tests/test_browse.py`.

**How hard to try depends on what giving up costs (`net.PATIENT_ATTEMPTS`).** Drawing a
screen and fetching a card are different errands: a facet dropdown that arrives late is
worth less than the four seconds it would spend waiting, while a card lost out of a batch
of fifty costs a re-run and a hunt for which one. So `net.get` takes an `attempts=`, the
default (4) stays for reads that draw a screen, and the *work* reads — a card's metadata
and its image — ask for **7**. At the measured 500 rate that takes the loss from roughly
one card in sixteen to about one in a thousand, for a worst case of 23.5s spent only when
a host really is down (and `get` still serves a stale cache entry before it raises, so the
wait buys an answer whenever there has ever been one).

**Metadata a caller already has is not asked for again (`sources.known_meta`).** The row
somebody clicked in Search or Browse was described by the provider a moment earlier — name,
set, rarity, subtypes — and `fetch` was asking for all of it a second time, which is
precisely the request that fails on a bad afternoon. `fetch --name/--set-name/--rarity/
--subtypes` files the card from what is already known, `FetchBody.known` carries it from
the browser, and `/api/fetch` spends one CLI call per described card while the batch keeps
its single call. **Pokémon only, and not an oversight**: a Magic card's image URL is a uuid
path only Scryfall's answer carries, so an MTG card genuinely cannot be filed without
asking — the CLI refuses `--name` for it rather than pretending.

It is safe *because* of the placeholder check. Skipping the lookup skips the proof that the
id exists, so the honesty of the whole path rests on `download` refusing a grey card. The
supplied fields are bounded, untrusted text that becomes a folder name; nothing a client
sends can fake a picture, because the image URL is derived by the server from the id.

**A card folder with no picture in it is a card as far as everything else is concerned.**
`_acquire` used to create the folder, write `.game`/`.layout`, then fail on the download and
leave it — so `ls` counted a card that could never become ready, the contact sheet drew a
tile for it and the tally read "4 of 5 originals". It now rolls back a folder **this call
created** when nothing landed in it, and leaves an existing card alone when a re-download
fails. Reachable from the API the moment a client may supply metadata, which is what
surfaced it.

**A failed request names what it was for, not just the host.** `net.NetworkError` reads
`api.pokemontcg.io: HTTP 500 after 4 tries`, which surfaced in a batch as
`SKIPPED api.pokemontcg.io` — leaving you unable to tell *which card* to try again, which
is most of the annoyance of a flaky API. `sources._get` takes a `what=` (the card id) and
`fetch` collects the ids that failed and prints the command that retries exactly those.
Worth the plumbing because pokemontcg.io answers 500 often enough to lose a card out of a
batch — measured at one in two on a bad afternoon.

**HTTP (`net.py`).** Every provider request goes through `net.get`: rate-limited
per host (Scryfall asks for it), retried with exponential backoff on 429/5xx and
transport errors (`Retry-After` honoured), and JSON responses cached on disk
(`$PROXDEX_CACHE`, else `~/.cache/proxdex`) — a *stale* cache entry is served
when every attempt failed, so a degraded API still lists cards. Each host's last
behaviour is recorded in `health.json`, so `net.incidents()` (this process → the
CLI's `⚠` line) and `net.health()` (the shared file → `/api/health` and the UI's
topbar pill) can say *which* API is misbehaving; the UI needs the file because
mutations run in a CLI subprocess. `proxdex where --clear-cache` empties it —
**via `net._CACHED`, one list of every kind of cached thing**, so a second kind (the
art cache below) cannot be added and quietly become unclearable.

**A picture host is not an API, and the interval that is manners at one is a stall at
the other (`net.CDN_INTERVAL`).** The 100ms `MIN_INTERVAL` is what Scryfall asks for
*its API*; nothing asks it of a static image CDN, and a browser fetching the same art
asks for six at once. Held per host at the API's interval, one screen of set logos —
174 of them, measured — would be **17s of pure waiting** before a byte was needed. So
`get` takes an `interval=` and art reads pass 0; the limit on them is the pool that
issues them, which is the honest place for it.

**Browse was slow, and it was never the JSON (`art.py`).** Measured: the set index
answers in **9ms warm** and then pulls **174 logo PNGs at ~139 KB each — 24.7 MB** into
a slot 2.25rem tall, while one 60-card page pulls **45 MB** of full-size scans into
tiles 190px wide, all of it again on the next visit. So provider art asked for by a
*screen* goes through `/api/art`: fetched once, resampled to the size it is drawn at,
kept in `net.cache_dir()`, served immutable. End to end that is **4.17 MB → 215 KB**
for the first screenful of logos, **45.1 MB → 2.10 MB** for a card page, and **0 bytes
and 0ms** on the second visit. Four things about it are deliberate:

- **The host is checked against a list** (`art.hosts`, the providers' four plus this
  library's own `scrydex_url`). An open fetcher is a hole even on a machine only you
  can reach, and the check is a full-host match — a lookalike domain and the allowed
  host as a path segment are both refused, and both are pinned.
- **`art.Size` is a closed set of the places proxdex draws provider art** (`logo`,
  `symbol`, `card`), not a width in the URL. A width would be untrusted input that
  becomes a resample and a cache file; this way the cache holds one file per picture
  per *use*.
- **WebP, because alpha is load-bearing here.** A set logo is transparent around its
  wordmark, and unlike a *filed* card — which `sources.flatten` deliberately fills from
  its own border (`tests/test_flatten.py`) — a logo on a screen must keep it or every
  tile gets a grey box behind the set name. A format that dropped it would look
  perfectly healthy in any test that measured only bytes.
- **One fetch per picture, not one per asker.** `/api/expansions` and `/api/search`
  *warm* their whole page in a pool of 6 while the browser lazily asks for what is on
  screen, so without a per-picture lock the first screen would fetch everything visible
  twice. `art.load` takes that lock, re-checks the disk inside it, and warming goes
  through the same path.

A vector passes through untouched: Scryfall's set symbols are ~2 KB of SVG, already
the smallest they will be, and rasterizing them would cost sharpness to save nothing.
The one URL that stays the provider's own is a hit's `full ↗` link, which exists
precisely to offer the original.

**And a tile asks the provider for a smaller picture in the first place, which the cache
could not do for it.** Shrinking on arrival fixes what the *browser* pulls; the fetch
that fills the cache still pulled 825 KB a card, so the first visit stayed the slow one.
`SearchResult.thumb_url` (Pokémon: `[sources] scrydex_thumb_url`, `/small`, 245px and
~30 KB) is what a tile draws — a cold 60-card page went **3.49s → 0.72s**. Three things
about it:

- **`thumb` is a separate field, not a suffix swapped where it is used.** Nothing
  downstream may *file* a thumbnail: a 245px master would border, upscale, grade and
  impose without a word of complaint and be wrong only on paper — the grey placeholder's
  failure mode exactly. `image_url` is what `fetch` downloads and is untouched, and
  `tests/test_browse.py` asserts that for both filing paths (`to_meta`, `known_meta`).
- **It is a config *pair*.** A library repointed at a mirror has to move both, or its
  tiles come from one host and its files from another — and `art.hosts` would allow only
  the one `scrydex_url` names.
- **`thumb` falls back to `image_url`** when a provider publishes no smaller size, so a
  reader that sets nothing still draws a tile.
- **Both games do it, and the mechanism differs because the providers do.** Pokémon's
  image URL is derivable from the card id, so the variant is a configurable *template*
  (`scrydex_thumb_url`). Scryfall publishes its sizes as *keys* on the card's own
  response, so the only choice is which key to read — `sources._MTG_THUMB_KEYS`, and
  deliberately not a setting, since there is no template to point anywhere.

MTG's numbers are worth keeping, because a single card misleads. Measured over 32 cards
across four sets: `png` (correct to *file* — Scryfall's only lossless size) is **1657 KB
median, range 331-2206**, because an old card's scan is far heavier than a modern one's;
`image_uris.normal` is 488×680 at **120 KB, range 78-146**. That is ~14x at the median
and only 3x on the lightest set — but the better property is that the tile cost is now
**flat**: a page of Alpha costs what a page of Aetherdrift costs, where before it was
102 MB against 23. `normal` rather than `small` (146×204) because 146px is softer than
the ~190px tile it fills, and 488px is sharper than Pokémon's 245px.

**Waiting has a shape, and only where something really knows it (`progress.py`).**
Every batch verb already counted its items — `cli._each` does, and rich draws a bar —
and **none of it reached the browser**, for two good reasons: rich turns its live
display off when stdout is not a terminal, and the UI reads that stdout as the
command's log. So a browser got one full-screen spinner whether it was filing one card
(a second) or imposing a 4-page duplex sheet (47s, measured).

The channel is a file, named by `$PROXDEX_PROGRESS`, and the sink is a **no-op when
that is unset** — so a terminal user's output is byte-for-byte what it was, and
nothing is parsed out of human text that a wording change would break. `webui.run_cli`
is the one place a mutation happens, so it is the one place a job is registered
(`_Watched`, a context manager because a command that raises must still leave the list
empty); `/api/progress` reads the file while that call is still blocking, which works
because FastAPI answers sync handlers on a threadpool.

- **There is no job id, deliberately.** This is a single-user console on localhost and
  every mutation happens behind one overlay, so "what is running" is a question about
  the *server*. It also means a second tab sees the upscale you started in the first,
  which beats a spinner that knows nothing.
- **Two reporters, because two things know a total.** `_each` reports items (a card id
  per note); `sheet._pages_to_pdf` reports pages — and it, rather than `_iter_pages`,
  because both halves of that wait live there. Reported from the generator the bar
  filled to the last page and then **fell back to a spinner** for the img2pdf embed;
  now the last step is `writing the PDF` at 4 of 4. That total comes from
  `sheet.pages_for`, which `plan` also calls, so the count promised by `--dry-run` and
  the one the imposition walks cannot drift.
- **An unknown total stays unknown** (`progress.UNKNOWN`, `fraction is None`). One card
  through Upscayl is one item and the model reports nothing of its own, so there is no
  fraction to be had. The UI shows a sweep, an elapsed clock and — once you have run
  that command twice — `~41s typical`, the **median of your own last runs** held in that
  browser and labelled as an estimate. It is never turned into a bar position: a
  fraction nobody measured, sitting at 90%, is the same lie as a border nobody measured.
- **A count of one is not a position** (`progress.Report.positional`, pinned). A single
  card is a *real* count whose fraction is a real 0.0, and the bar over it could only
  read 0% and then be gone — which is exactly what upscaling or grading one card looked
  like: an empty bar beside a running clock, reported as a broken bar and fairly so. Two
  steps is the least that can show movement, so at one item a reader falls back to what
  it does for an unknown total. The rule lives on `Report` because both readers need the
  same one; the CLI's rich bar already draws nothing below three items.
- **An item names itself, and `cli._each` has no default for it.** `name` was `str(item)`,
  which for the three verbs whose items are `Card`s put the whole dataclass —
  `Card(id='ecard3-141', dir=PosixPath('/Users/…` — in the panel as the progress note,
  and the same thing in `_last_failed`, where a bare id is what a retry needs. Every
  caller knows what its items are called and nothing in `_each` can guess, so the
  parameter is **required**: a forgotten one is a type error, not a long line in a
  browser.
- **Two kinds of estimate, and each says which it is.** `Report.remaining` is measured —
  the command's *own* elapsed time over the items it has really finished, so `~14s left`
  is a rate this run achieved. It refuses to be a guess in four places: before the first
  item (a division by zero wearing a forecast's clothes), at a total of one, with no
  clock, and **once the count is complete** — `sheet` reports its last page and *then*
  embeds the PDF, and `0s left` beside a full bar for the length of that embed is the
  spinner-that-knows-nothing with more confidence. It is timed from when the *counting*
  began (`Report.started`), not from process launch: a CLI subprocess spends a second or
  two importing before it knows what it is counting, and measured here that was 5.2s of
  a 15s run charged to the first page. Where nothing counts, the *remembered* estimate
  above applies instead — and only runs it would be shown for are recorded, so one card
  through Upscayl and forty of them never pool into a median describing neither. Well
  past that median the panel says `longer than usual`, which is the difference between a
  slow step and a stopped one.
- **Three kinds of wait get three different things.** A *read* (a set list, a page of
  hits, the facets) gets a 2px sweep at the top of the window, because a modal spinner
  for a 9ms answer is in the way — which is why reads used to show nothing at all. A
  *job with a count* gets the real bar. A *job without one* gets the sweep and the
  estimate above.
- **One indicator at a time, and the nearer one wins.** There were three that could
  overlap, and every overlap made the reader work out which bar was about the thing they
  clicked. Now: a read with a strip of its own suppresses the top sweep (`paintTopload`
  is the one place it is toggled, off `_reads > 0 && !_rd.at`); a mutation's sweep is
  the *pre-panel* acknowledgement only and is **handed over** when the panel appears
  (`holdSweep`), rather than staying lit underneath it; and the top sweep survives as
  the fallback for a read with nowhere of its own to show — the facets, a card's data
  sheet, the boot reads — because deleting it puts those back to no indication at all.
  Measured over 67 samples across a page turn and two overlapping adds: never two at
  once. Two bugs fell out of it, both of which *looked* like a second indicator:
  - **The sweep leaked on permanently.** `showBusy` incremented `_reads`
    unconditionally while `hideBusy` decremented only `if(_busy)`, so two overlapping
    mutations added two and took one away — and the 2px bar stayed lit for the rest of
    the session with nothing running, indistinguishable from a real one. `holdSweep` is
    idempotent, so no ordering of clicks can leak it.
  - **The sweep sat under the busy panel** for the whole of every counted job, which is
    the same wait drawn twice.
- **A read that *replaces* what you are looking at needs more than the top sweep, and
  paging is what proved it.** Turning the page left the previous page's 60 cards on
  screen and printed `Searching…` **below** them — off the bottom of the window on any
  real page — so the only word about it was where nobody was looking and the cards in
  front of you were the wrong page's. Clicking Next twice read as doing nothing. So
  `beginRead(host, key)` clears the container and puts the wait *there*: **ghost cells in
  the grid's own layout** (`ghostGrid`), a `.bzbar.sweep`, an elapsed clock and this
  browser's median for the same kind of read. Four things about it:
  - **A ghost is a `.hit` with the words taken out** — the real tile's own `.hitshot`,
    `.hitmeta` and `.addbtn` boxes, each empty line taking its height from an `\a0`. So
    the height matches **by construction**: the first version was a plate and two bars at
    297px against a real tile's 359, and the grid grew by a fifth when the cards landed,
    which is the jump ghosts exist to prevent. Measured at exactly 0px of movement now.
  - **How many is answered honestly or not at all.** Paging inside a query the server has
    already counted knows the number — a last page of 18 draws 18 — and that is the case
    this was built for. A first search knows nothing and draws one screenful
    (`GHOSTS_UNKNOWN`), because three hits behind sixty ghosts is a shape nobody promised.
  - **The bar is indeterminate on purpose.** One HTTP request has no countable parts, so
    a bar creeping to 90% of a provider round-trip is the invention this whole layer
    refuses. The clock is measured and the median says it is a median.
  - **A superseded read is not recorded** (`endRead(ok)`). `runFind` drops an answer a
    later query overtook, and timing that half-read would drag the median below anything
    the provider ever did.
- **A count split into one-item calls has no position in it** (`webui._Counted`).
  `/api/fetch` spends one CLI call per *described* card, so a tray of four Pokémon cards
  was four jobs of one item each — and one item is not `positional`, so every one of them
  fell back to the sweep with a note flickering between four ids, for work that knew
  exactly how many cards it was filing. The count belongs to the **request**, which is
  the thing that knows the total, so that is where it is kept: `_Counted` writes through
  the same `progress.Sink` a command uses (the browser cannot tell, and need not, whether
  a count came from a subprocess or from the server), and the inner calls run
  `run_cli(..., watch=False)` — no job, and **no `$PROXDEX_PROGRESS` in the child**,
  because two jobs for one wait is what put the uncounted one on screen. Measured: `0 of
  4` → `1 of 4` at 25% with `~1.0s left`. A request with nothing described is untouched —
  the batch is one call and `cli._each` counts it, as it always did.
- **And a wait too short to be one gets no panel at all.** Skipping a step, flipping a
  card or setting a config key is over in ~20ms, and an overlay that appears and
  vanishes reads as a glitch rather than as progress. The panel is held back for
  `GRACE_MS` (320) while the top sweep — which is not in the way — acknowledges the
  click synchronously. The note is one line, truncated with the full text on its own
  `title`, because the panel is a fixed size and a wrapping note pushed the clock
  around underneath the bar.

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
`unreadable`, and `aspect`: a **bordered** master that is not the aspect of the trim
*this card* prints at (`sheet.trim_mm`, so an oversized master is right at 88.9×127
and would be a finding at every other size), which `sheet` would `cover`-crop,
losing border off two edges silently. Only the bordered stage is measured for
aspect — every later stage inherits it, and reporting one cause three times is not
a better report. Each check owns its own
label, *why* and hint, so the CLI's terminal output and the settings screen's
**stored images** panel (`/api/doctor`, `/api/doctor/fix`) explain a defect with the
same sentence. Two things it must keep doing: examining reads **headers only** and
writes nothing without `--fix`, and a repair **never invalidates downstream** — the
picture does not change, so throwing away an upscale over a corner fill would
destroy work to fix a corner. What it cannot repair it does not touch: re-fitting a
border needs to know where the border is, which is a decision (the align marks), not
a repair.

**There is no border auto-detection, and removing it was the point.** There was:
`borders.detect_inset` scanned each edge inward and pre-placed the align marks, behind
`border --auto`, `/api/detect` and a bulk Border action. The whole module, its route, its
flag and its test file are **gone**, and nothing may reintroduce them. It was wrong in
three ways that no screen showed, each found only by comparing it against a hand reading:

1. **It over-read a black border whose frame was also dark.** It wants a *luminance* step,
   so on a Beta Sol Ring (stone frame at 37 against a black border at 27) it walked past
   the real edge to the light text box — 37-41px where the border ends at 23px, 65% too
   far.
2. **It read past a decorated frame's keylines.** On `dft-501` the flat yellow band ends
   at 50px and it answered 56px, having crossed a black keyline, a thin yellow line and a
   second black keyline.
3. **It found a border on a card that has none.** `sld-912` is full-bleed; its dark art
   read as a border at T4.04 R6.45%, and the fit cropped into the picture on all four
   edges.

Each of those produces four plausible numbers, and a plausible number presented as a
measurement is worse than no number at all: it looks finished. Where the printed border
sits is a **reading**, and proxdex asks for it rather than inventing it. The measurements
that replaced it are in `docs/measuring-frames.md`, one row per card, taken by hand.

**The overlay and the marks are different things, and only the marks are modal.** The
target outline is a *statement about the card* — where its border belongs — so it is drawn
whenever there is something to draw it on: over the original while the step is pending, and
over the **finished master** once it is done, where it is the cheapest check there is that
the fit landed (a correct master has its border exactly on that line, and you can see it in
one look). The draggable marks belong to the **act of aligning** and to nothing else: they
appear when you open them and not before.

So the align *layer* — the one that swaps the viewer for an undistorted source with marks on
it — owns the viewer only when there is a border to place: *pending* yes, *done* only if you
asked for the marks again, **skipped never**. On a done step with the marks down the ordinary
proof stands, compare tool and all, with the outline laid over it (`.al-band` alone, no
layer). That works because a bordered master is exactly 63:88 by construction, so it fills
the viewer with no letterboxing to correct for. Two things this got wrong on the way: a
finished border step once showed the *original* with a target over it instead of its own
output, and an overlay for placing a border you decided not to place is an invitation to do
work that will not happen.

**Skipped is settled, and it has to look settled** (`renderViewer`). It used to render
exactly like *pending* — the dashed `is-input` frame, "Showing bordered — the input" — which
reads as unfinished when it is a decision the pipeline honours: `sheet.print_ready` prints a
skipped step's input *as the master*. So the three states are now three renderings: **done**
shows its output in a solid frame with the compare tool; **skipped** shows the picture that
will be printed, also solid, plated `<stage> — stands as the master` with a `skipped` pill
and no overlay of any kind; **pending** keeps the dashed frame and the "not …d yet" badge,
because that one really is unfinished.

**So the border step opens on the target, not on an answer.** Focus Border and the align
layer draws the *original*, untouched, with the frame spec's own dashed outline over it —
where this card's border **should** be, from its set and id. From there: **Skip**, or
**Align the border**, which puts the four draggable marks up starting at the spec's own
numbers, so the gesture is moving them to where this card's border really is. Run is
**held shut** until the marks are up and a fit solves (`borderReady()`), because a reshape
with no idea where the border is has nothing to reshape against. Two consequences worth
keeping straight:

- **A borderless printing needs none of it.** There is no frame to put a mark on, so the
  fit is pure aspect correction: the panel says so, Run is live from the start, and `border`
  fills the marks in itself (`marks = (0, 0, 0, 0)`) rather than reporting "nothing to
  expand". It is the one case that needs no reading, which is why it is the one case that
  runs unasked — and `--frame borderless` is therefore the whole fix for a print whose
  metadata is wrong about its own border.
- **Border left the bulk actions.** It was only ever bulk-runnable *because* `--auto`
  would measure each card, so with that gone it is one card at a time. Bulk **Skip** stays:
  skipping fifty cards is a decision, not a reading.

**The target band is drawn against the image, and three one-pixel bugs lived in it** —
each of which read as bad geometry rather than as bad drawing, which is what makes them
worth naming. (1) `border-width` does not accept a percentage, so the band written as
`borderTopWidth: '2.88%'` drew *nothing at all*; `setBandLine` insets an inner `<i>`
instead. (2) An `outline` paints entirely **outside** its box, so the band's line sat half
a CSS pixel further out than the mark's hairline, which is centred on its value —
`outline-offset: -.5px` puts both centres on one line. (3) The band used to be positioned
on the **trim** box while the marks were up; since that box is anchored top-left, the whole
trim-minus-image difference landed on the *right and bottom* edges, so the two lines
agreed on two edges and disagreed on the other two. `drawBand()` always uses the image —
the trim is the ghost's job, and the band's job is to be comparable with the marks.

**Where a border is *widened*, the added area is invented — and the border step is
therefore two steps.** cardbleed synthesizes the new border, and how it does that is a
judgement about one picture: a fill that continues a texture is right on a flat border and
wrong on one carrying printed marks.

- **Step one is the reading.** Place the marks on the card's own border edge and Run,
  with cardbleed's defaults. That reading is the expensive part and proxdex will not guess
  at it, so it is **recorded** (`.marks-bordered[_f2]`) and never has to be taken again.
  `border` with no `--inner-*` re-uses it, which is what makes `border --force --tune
  mode=smart` a whole workflow rather than a re-alignment.
- **Step two is the fill**, and it exists only once step one is done: the panel offers
  cardbleed's thirteen settings and changing one **re-borders the card there and then**.
  What you are looking at *is* the output. There is deliberately no preview — that was
  built first and removed: a preview is a picture of a fit you have not made, so it has to
  be invalidated when the marks move, explained as not-the-real-thing, and kept in step
  with both. Re-running the actual verb has none of those problems and invalidates the
  later stages correctly, because it is the same call any other run makes.

Four things make it honest rather than a wall of sliders:

- **Everything closed is an enum** (`bleed.KnobId`, `FillMode`, `Halo`, `EdgeFill`), and
  everything numeric is a `Range` with `holds()`. A knob is a closed set **or** a range,
  never both and never neither — pinned — so `_coerce` has no third path where a value
  slips through unchecked. A choice knob's values come *from* its enum rather than being
  restated beside it, and `Tuning` is keyed by `KnobId`, so nothing downstream can hold a
  setting proxdex never validated.
- **proxdex validates every knob, because cardbleed does not.** Measured: it accepts
  `jittter=0.1`, `mode="nonsense"` and `jitter="lots"` without a word and carries on with
  its defaults. `tests/test_bleed_tuning.py` also holds the declaration against
  `cardbleed.Params` itself — a renamed field fails the suite instead of becoming an
  ignored override — and defaults are *read* from `Params`, never restated. **proxdex
  substitutes no baseline of its own**: `mode=smart` was briefly made the default and
  taken back out, because a second set of numbers here is one more thing to keep in step
  with a library that already ships considered ones, and it silently re-borders every
  card with no marker differently from the day it was filed.
- **A fill setting only matters where something is added, and the panel says so when it
  is not.** With a card's marks on its spec, `solve_fit` returns extensions of ~1e-13px —
  nothing is invented, the whole change is the stretch to the trim aspect — and all three
  `mode` values give a **byte-identical** file. `bleed.extends` (half a pixel, because
  that noise is not zero) is the predicate; the CLI warns and the panel points at the
  stretch instead. Nearly shipped the other way: three modes were compared by eye on a
  scaled screenshot and *read* as different. On a perfectly **flat** border they make no
  difference either, which is why a synthetic flat card is the wrong thing to test a fill
  with — grain is what they disagree about.
- **A tuning is a decision, so it is kept**: `.tune-bordered[_f2]`, `key=value` per line,
  like `.pin` and unlike the derived `.fit`. Only the non-defaults are stored, so the
  record reads `mode=smart` rather than restating thirteen. `--no-tune` returns to the
  defaults, and an absent `--tune` means "keep what the card has" — which is why the API
  sends `{}` for one and omits the field for the other.

**The align sidebar is a label, not a lesson.** It carried four editable `%` fields and
three explanatory paragraphs, which together were most of its height — above the card you
are trying to look at, restated on every visit. The fields are **gone**: nobody types a
border inset, it is *read off the picture* by dragging, which is the whole argument for the
marks over a detector — and the number under the pointer is on the loupe while you drag and
in the readout below when you stop. The prose is one line per state. Every step option's
`help` moved from a printed paragraph to the row's `title` (`cursor: help`), the affordance
the fill knobs already used; `steps.py` stays the single declaration, and the CLI's
`--help` prints the same sentence at length where there is room for it. What is left is the
spec label (or its chooser, below) and a Pin. Measured on a done border step: **605px →
505px**, and the align section itself from eleven rows to four.

**A choice between borders looks like a choice, and before it did not.** The two-candidate
case was a via chip beside a mono spec id, then a row of same-weight outline buttons for the
others — which showed neither which one was in force (only the chip's *presence* said so,
two elements away) nor what it was **called**, because the buttons carried the names and the
selected spec carried only its id. So it is a **radio group** (`.fspick`/`.fsopt`), one row
per applicable spec, and five things about it are deliberate:

- **The row is the label**, so the hit area is the whole row rather than a 3rem button in a
  row of them, and arrow keys move between them because these are real radios.
- **Each row carries its four border widths**, which is what is actually being chosen
  between: "e-Card" against "e-Card, deep top" says nothing about how they differ, while
  `3.12 / 3.24 / 6.76 / 7.16` against `9.10 / 2.61 / 6.76 / 7.16` says it at a glance.
  That is why `specs.Candidate.json` serves `mm` — and why it is **not** added to
  `FrameGuide.json`, which is also the on-disk format for `frames/<id>.json`, where a
  derived width beside the fractions it comes from is one a hand edit silently contradicts.
  Opposite edges collapse to one number when equal (`2.56 / 2.56` is one fact) and never
  when they differ, since an asymmetric spec's *shape* is the whole reason for two rows.
- **The selected row takes the accent on its own frame** — the same two states `.hit` and the
  contact-sheet tiles use, so a chosen thing looks chosen the same way everywhere.
- **The rows are sorted by id, not winner-first.** `resolve` returns the spec in force plus
  the others, so rendering in that order moved whichever row you clicked to the top the
  instant you clicked it: a radio group rearranging under the pointer, with the thing you
  just chose no longer where you chose it. The set of rows does not depend on the choice, so
  neither does their order.
- **One spec gets no selection styling at all** (`.fsone`): an accented frame on the only row
  spends the "this one, not that one" signal where there is no other one. It is a label —
  name, widths, how it was reached, Pin — and the name is the thing the old bar left out.

The via chip is on the row in the single case and in the **footer** in the chooser, because
there it is the same value on every row (they were all reached) and as a per-row badge it
competed with the names for a narrow row while distinguishing nothing.

**A printing nobody has measured must still be alignable, and for a while it was not.** The
CLI has always had the escape hatch — `border --frame <spec>` fits against any spec for the
run — and the UI's equivalent is the step's own Frame setting. Four separate faults meant it
could not be reached, all of them on the cards where it is the *only* way through
(everything Pokémon past the e-Reader era):

- **`renderAlignPanel` read `g.frameless` on a null guide and threw**, which took the whole
  panel with it: no explanation *and* no `Align the border` button, so the card looked
  un-alignable when the fix was one dropdown away. `computeExtend` would have thrown the
  same way inside `solveFit`. Both now treat "no spec yet" as the state it is.
- **`whyThisSpec` returned an empty string when nothing resolved**, so the one card that
  needed explaining got silence. It now prints `Resolution.note` — the same sentence the CLI
  prints — and names the control that fixes it.
- **A step setting lived only in the DOM.** Every control rendered from `o.default`, so any
  re-render reset it — invisible for settings read only when Run is pressed, and fatal for
  the Frame, which the align layer reads: choosing a spec and then clicking `Align the
  border` (a re-render) reverted to Automatic and the card went straight back to "no spec".
  `stepMem` holds the panel's values, keyed by **card, side and step** and dropped whole when
  that key changes — a per-run override leaking onto the next card is the one thing worse
  than forgetting it.
- **`onSetting` returned early with the layer down**, so on a *done* border step — where the
  target outline is drawn over the master and re-running with another spec is the whole
  reason to be there — choosing a Frame changed nothing on screen.

Two smaller things fall out of it. The panel reports **what the fit will use**, so a chosen
Frame is what it names (`chosen for this run`, with `Keep` to pin it) rather than the spec
the rules resolved and the run is about to ignore. And an **untouched** rectangle follows a
change of spec, because the marks *start* on the target: seeded at the 8% fallback with no
spec, they otherwise stayed there and reported "shaves the over-target" on all four edges
the moment a real spec was picked. `align.seeded` is what makes that safe — dragging moves
`blue` and not `seeded`, so a rectangle somebody has read off the picture is never moved.

**The panel's position is part of the design, and so is the order it is painted in.** It
sits under the step's own settings and *above* the align section: on a done border step
tuning is the job you came back for, while re-placing the marks is the step-one thing you
rarely return to. Below the align panel it was 795px down a 1000px viewport — scroll to
reach it, scroll back to see the card. And because `renderStepPanel` emits an **empty**
`#alpanel`, anything that restates it must re-render the align panel afterwards, which is
why there is one `paintBorderPanel()` rather than two calls at each site — the same
ordering trap that once blanked a done border step's target outline, from the other side.

**Rebuilding from a step is no longer confirmed.** `okCascade` asked before throwing away
a done upscale, and the dialog was in the way of the very thing it protected: tuning a
border means re-running it over and over. The cascade is still *reported* (`cli._cascade`,
and the rail shows it), which is the honest version — you are told what happened rather
than asked to predict it, and nothing is unrecoverable because every stage after the
original is derived. Deleting a card or a profile still asks, because those remove files
nothing can rebuild.

**Frame specs (`frames.py`) are geometry; `specs.py` decides which one a card
gets.** A `FrameGuide` is **four numbers plus one flag** — id, name, game, inset, and
`oversized`. It carries no note and no reference size: where a shipped spec's numbers came
from is prose **above it in the source** and one row per card in
`docs/measuring-frames.md`, which is a record no screen can render as a verdict.

**There is exactly one card size, `games.CARD_W_MM`/`CARD_H_MM` = 63.5×88.9mm, and it is
both the trim and the size a spec's millimetres are fractions of.** 2.5×3.5in, the
poker-size standard; Wizards states it for Magic and The Pokémon Company states the same for
Pokémon, so **the two games are identical** and this is one constant rather than one per
game. Deliberately the **published spec and not a measured card**: calipers on real cards
read a little under (one reported 63×87.9mm) and 63×88 is widely quoted as a rounded metric
figure, but a caliper reading is one card off one print run inside a ±0.5mm cutting
tolerance, and pinning proxdex to somebody's off-cut is worse than using the number both
publishers state.

It is **one** number for a reason. It was briefly two — the trim at 63×88 and a separate
"real card" at 63.5×88.9 that specs' millimetres were fractions of, on the sound reasoning
that a caliper reading is a fraction of the true card. The reasoning was right and the split
was not: it made `frames show` report a width 0.8% off the one being printed. With them
identical, **a caliper reading of a 3.45mm border prints as a 3.45mm border**. Insets are
*fractions*, so they travel between sizes untouched; the only genuinely different card is
the oversized one, and that is a **boolean** (`FrameGuide.oversized`), not a size pair.
`FrameGuide.mm()` reports the millimetres of the card the spec is *about* — dropping that
entirely was a step too far, caught by `frames show mtg-vanguard` reporting 3.71/2.88mm "of
a 63.5×88.9mm card", a width of a card that spec does not describe.

A short measured set ships (`frames.SHIPPED` — one per geometry somebody has read, and
the count grows a card at a time), so a fresh library borders a Base Set card with
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

**One set can hold more than one border, and proxdex offers the choice rather than
making it (`specs.Candidate`, `Resolution.alternatives`).** Pokémon's e-Card sets are the
case: the same set printed Pokémon cards and Trainer/Energy cards whose frames differ, and
nothing in the metadata says which in terms anybody has measured — `supertype` exists on
pokemontcg.io and would be the hook, but a rule pointing at a number nobody read is worse
than no rule. So `resolve` **does not stop at the winner**: it collects every applicable
spec in one walk, uses the most specific exactly as before, and offers the rest. Picking
one writes the card's `.pin`, which is what a decision about a card already is, and the
offer is **symmetric** — the spec you moved off is then the alternative, so it is a choice
and not a one-way door.

Deliberately unlike `border --frame`, which forces *any* spec, matched or not: a candidate
is one the rules really reach. Five things about it:

- **One walk, not two.** A separate "what else applies" pass would be a second
  implementation of the question every fitting surface asks, free to disagree — the same
  argument as `imports.plan` and `sheet.plan`.
- **`missing` and `undecided` still stop at the winner**, which is what the
  early-returning version reported: a pinned card is not warned about a trait rule
  further down that the pin settled. Pinned by two tests, because that is what the change
  could quietly have broken.
- **A second whole-set rule is no longer deleted.** `assign` used to remove the previous
  one because it "could never be reached, and a rule that can never fire is worse than no
  rule". It can be reached now — by being picked — so both stand. What is still refused is
  a rule saying exactly what the file already says.
- **Deduped by spec id**: the same four numbers arrived at two ways is one choice, named
  by the way with the most precedence.
- **It is a state, not a fault.** `frames check` does not report it: every fault there is
  a broken reference or an unanswerable question, and this is a question with two good
  answers. `border` says it on every run (a border fitted to the wrong one of two looks
  perfect on screen), the `frames` table carries an **Or** column, and the align panel
  lists the alternatives as buttons that pin.

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
**prose in the repository, not a field**. There *was* a `FrameGuide.note` carrying that
sentence, and it is gone too: a per-spec provenance string is still a slot every screen
renders beside the numbers, which is how a grade gets reinvented. So the account lives
where it cannot be rendered as a verdict — a comment above each shipped spec in
`frames.py`, and `docs/measuring-frames.md` as the log, **one row per card**, each row
carrying the hand reading, a second independent measurement of the same image, and what
it decided. One verb records a spec, `frames set` (four millimetres, a name and a game
— nothing else), and correcting a shipped one is the *expected* path: every MTG number
is a pixel reading of the publisher's own image. **Writing the row down is the step that
used to be `--note`.**

**Calipers are deliberately not the plan any more.** They answered exactly one question —
whether every spec is uniformly a hair narrow, because each is read off an image carrying
its own crop — and that error is *shared*, so it lands on every card equally and changes no
card's border relative to another's. Every question actually open is a **comparison**
(Alpha against Unlimited, an oversized card's fraction, a token against its generation), and
a common crop cancels in a comparison, so a pixel count settles those outright. All three
questions on that worksheet came back answered, which is the argument for the approach.
Do not reintroduce "confirm on paper" as a blocker: it blocked answerable questions behind
a purchase.

**The `1993` frame is three bands, and it took 26 sets to say so.** Scryfall calls Alpha,
Unlimited, 4th Edition and every 1993-96 expansion one frame; the printings do not share a
border. Every set of it has now been read by hand, and **they collapse into three bands rather
than 26 numbers** — which is the whole point, because the code carries three rules and no table
of sets:

| band | sides / top px | mm | covers | spec |
|---|---|---|---|---|
| narrow | 23 / 32 | 1.96 / 2.74 | `lea`, `leb`, `ced`, `cei` | `mtg-1993-alpha` |
| **ordinary** | 29 / 32 | 2.47 / 2.74 | **18 sets**, Arabian Nights → 4th Edition and the 1995-96 reprints | `mtg-1993` |
| wide | 35 / 42.5 | 2.98 / 3.63 | `2ed`, `3ed` | `mtg-1993-unlimited` |

The bands are separated by far more than the noise inside them: the ordinary band spans 27-32px
(0.43mm) with a median of 29, band 1 sits 6px below its narrowest member, and band 3's top is
6.5px clear of anything else in the frame. One pixel is 0.085mm, so **the boundaries are not a
matter of taste** — and a card a pixel or two off its band is reading noise, not a spec.

Three results worth keeping. The **Collectors' Editions confirm band 1 independently** (`ced`
and `cei` read 23/32, Alpha to the pixel, and nothing about them fed into that number). Band 2
**absorbed a briefly-separate `mtg-1993-4ed`** — 4th Edition reads 30/33 against the median's
29/32, and one pixel per edge is not a spec. And 18 sets landing inside 0.43mm is what let
`frame: 1993` have a **generation entry** again: with only Alpha and Revised read it resolved to
nothing, because a generation-wide number would have been a coin flip across a 1mm spread —
3,080 prints, 3.2% of Magic. That gap is closed.

`4bb` is the one printing that fits no band: 36/40, which is **`mtg-1997`'s numbers exactly**,
so `BASELINE` points its set there rather than inventing a spec (the basis on which `future`
shares `mtg-2003`). A run-length scan reads it wider still, so it is not a misreading — the
Foreign Black Border sets were printed in Belgium, a separate run. But its sibling `fbb` went
the *other* way into band 2 at 27/30, so **there is no "foreign" rule to write**, and no colour
rule either: Revised and 4th are both white and 5px apart, `fbb` and `4bb` are both black and
9px apart in opposite directions.

**What did not merge is worth knowing too**, at 0.085mm per pixel. `mtg-1993` (29/32) and
`mtg-m15` (30/30) agree on the sides to within a pixel and stay separate, because M15's top
*equals* its sides while the 1993 frame's exceeds them by a consistent 3px — that is the
border's **shape**, not its size. `mtg-1993-unlimited` and `mtg-2003` have *identical* sides
(35px) and a top 7.5px apart. `mtg-1997` and `mtg-2003` differ by 5px on top alone. Going the
other way, the seven `2015` readings (28-30px: plain, etched, silver, extended-art,
legendary-crown, token, emblem) sit in a 0.17mm band and are deliberately **one** spec, as are
the five `2003` readings at 35/35 (black, white, `future`, two tokens).

**Only measured specs ship, and they arrive one card at a time.** `pokemon-wotc`
(calipers, covering `base1-6`/`gym1-2`/`neo1-4`), the three e-Reader specs (below),
`borderless`,
and one spec per MTG geometry somebody has read by hand:
the three 1993 bands above, `mtg-1997` (`sld-1664` — a card that physically exists, where `me4-227` was an MTGO-only *render* of the frame template reading 1px wider), `mtg-2003` (`c13-259`, and it
covers the `future` frame too because `mb2-233` measured the same), `mtg-m15` (`msc-211`),
`mtg-yellow-band` (`dft-501`), and the two oversized ones below.

**The e-Reader specs are the ones whose *shape* is the finding**, and there are two shapes.
Expedition, Aquapolis and Skyridge (`ecard1`/`ecard2`/`ecard3`, 2002-03) carry
the Nintendo e-Reader dot-code strip **down the left edge and along the bottom**, so
`pokemon-ecard` is top 3.509% / right 5.100% / bottom 7.608% / left 11.269% — 3.12 / 3.24 /
6.76 / 7.16mm. Every other spec deliberately collapses opposite edges, which is how a
cutting error cancels; doing it here would split the difference on all four and ask ~2.5mm
too much border on two edges and too little on the others, looking right on screen because
the overlay is drawn in fractions too.

Read by hand off **two** cards, 337×467 and 737×1036, and the reason to believe the
asymmetry is the *card's* rather than a crop's is that it **reproduced**: the edges agree to
0.014pp (left), 0.112 (right), 0.227 (bottom) and 0.262 (top) across a 2.2× scale
difference. A crop shifts the two opposite edges *against* each other, so a lopsided crop
cannot give the same lopsided reading twice at two scales, while a real asymmetric frame
does exactly that. Corroboration from the other direction: the two ordinary edges — top
3.12mm, right 3.24 — are `pokemon-wotc`'s 3.45/3.15 within a third of a millimetre, taken by
a different method (calipers there, pixels here). Same operation, same border, plus a strip.

A run-length scan over ten e-Card scans was tried as a check and is **deliberately not
recorded as one**: it read the left edge from 19px to 61px (3.2% to 10.2%) and the top from
2.06% to 11.64%, because an e-Card's art runs into the strip and much of it is yellow. That
is the deleted detector's third failure mode reproduced once more, and it is an argument for
hand reading rather than against these numbers.

**The ex era moved the strip to the bottom alone, so that is a different shape and a fourth
Pokémon spec.** `pokemon-ecard-ex` covers set `np` (Nintendo Black Star Promos, 40 cards,
2003-10-01): top 3.666% / right 3.675% / bottom 6.762% / left 4.340% — 3.26 / 2.33 / 6.01 /
2.76mm, three ordinary edges and one strip. Fitting one of those to `pokemon-ecard` would
have asked 7.16mm of left border where the card has 2.76 — 4.4mm of picture cropped, and
invisible on screen for the usual reason. Read off two cards (747×1040, 455×642) that agree
per edge to **0.17pp** across a 1.64× scale gap, with the independently stated edge totals
landing exactly on three of four (the fourth is a 1px slip in the reading, worth 0.14mm, so
the edges are what is stored). **Left and right are deliberately not collapsed** — the one
spec here that keeps a side difference: both cards read left wider by the *same* 0.67pp, and
a difference reproducing in one direction at two scales is not the cutting error that
collapsing opposite edges exists to cancel. It is keyed to **`np` alone**, though the strip
ran on through ex: one card of `ex1` would say whether the geometry extends, and "very
likely" is not what `BASELINE` carries.

**That set also printed cards with no dot code, so it holds two frames and needs no
exception for the second.** `pokemon-ex-plain` is one card, 554×769, **23px on every edge**:
top 2.991% / sides 4.152% — 2.66 / 2.64mm, the plainest spec in the file and the thinnest
Pokémon one (WOTC's yellow is 3.45/3.15; this is nearer `mtg-m15`'s 2.56). Opposite edges are
collapsed the ordinary way, because one number per axis is what was read. Being square in
*millimetres* is a separate fact from being square in pixels and worth stating: 2.991 and
4.152 are different fractions and only meet once each is taken of its own axis, the 0.02mm
between them being the file's aspect sitting 0.85% wide of the card's. Nothing in the
metadata says whether a promo carries the strip, so both resolve as candidates and a person
picks — the e-Card shape exactly. `pokemon-ecard-ex` is the default on **weight of evidence**
(two cards read against one), explicitly *not* a claim about which is commoner in the set.

**The dot code outlived the e-Card sets, so both ex-era shapes cover the same five sets**:
`ex1` Ruby & Sapphire, `ex2` Sandstorm, `ex3` Dragon, `ex4` Team Magma vs Team Aqua and `np`
— 2003-07 to 2004-03. Each printed cards with an e-Reader strip along the bottom *and* cards
with a plain square border, which is the two-candidate situation the e-Card sets already
have, answered the same way.

**Then the strip stops, and the rest of the ex series takes `pokemon-ex-plain` alone —
inherited, not read.** From `ex5` Hidden Legends (2005-06) to `ex16` Power Keepers (2007-05),
plus the four Trainer Kits (`tk1a`/`tk1b`/`tk2a`/`tk2b`, the same printings boxed
differently), there is one shape and nothing to pick between, so those sixteen sets get the
plain border as their **only** candidate. The number is still the one `np` card (554×769, 23px
all round): sixteen ids now rest on a reading none of them contributed to, which is exactly
the standing `basep` has and is recorded the same way — **inherited rather than measured**, in
`docs/measuring-frames.md`, out loud. The grounds are same era, same operation, the same
border with the strip left off; it is a decision to let these sets border at their own era's
number instead of refusing them, and one card of `ex10` would confirm or split it. Keys are
**exact ids** precisely so the claim is reviewable — a prefix would have swept all sixteen in
without a word.

**A `Baseline.sets` entry is an exact id, and it used to be a prefix.** A prefix over-claims
silently, and there is no prefix that covers `ex1` and not `ex10` — Unseen Forces through
Power Keepers, 2005-07, seven sets of another era that a `("ex1",)` key would have claimed
without a word. `("base",)` was already doing it to `basep`, the Wizards Black Star Promos,
which nobody measured. Pokémon's eras are closed sets, so enumerating them costs a dozen
strings and buys a table whose every claim is visible — including the ones that are
deliberate. Those sixteen ex-era ids *are* claimed now, but by sixteen strings somebody wrote
down rather than by a prefix nobody noticed, which is the difference. `basep` **stays** on
`pokemon-wotc`, because the prefix was already claiming it and dropping it would stop a card
that borders today — recorded as *inherited* rather than measured, said out loud in
`docs/measuring-frames.md` rather than passing for a reading.

So **Pokémon from Diamond & Pearl (2007-05) onward is the only real gap left**, and it still
resolves to nothing and refuses to be bordered.

**An oversized card needs its own spec even when its border is the same width, and that is
the clearest thing in the whole file.** An Archenemy scheme measures 2.98/3.00mm —
*physically identical* to an ordinary 2003-frame card's 2.99/2.98. But a spec is a
**fraction** and the card is 89×127mm, so the same millimetres are 2.35%/3.37% against
`mtg-2003`'s 3.37%/4.70%. Resolving a scheme by its frame generation, which is what happened
before, asked for **4.27/4.18mm — 1.2mm too wide on every edge**, and looked right on screen
because the overlay is drawn in fractions too. So `mtg-oversized` (`oarc-1★`, 1040×1490, covering **planes and phenomena as well as
schemes**) and
`mtg-vanguard` (`pvan-101`, **1060×1510**, a third size and genuinely thicker at 5.30/4.03mm)
are read from the **layout** in `sources.mtg_frame`, like the yellow band, because the layout
settles the geometry. A **plane** could not be read directly — art to the edges, uneven border — so it takes the
number measured off the same stock rather than being called borderless. That is the safe
direction under the project's own asymmetry (calling a bordered card borderless throws its fit
away and looks perfect), and it is not a geometry guess: same product line, same 89×127mm
stock, same era, and the scheme's 2.98/3.00mm *is* an ordinary 2003-frame card's border.

**Tokens need no spec, and that is now measured rather than assumed.** A token's layout is
bespoke (no mana cost, larger art), so "same stock, same die" was not good enough. Read by
hand: `tmsh-3` and the emblem `tdft-13` are 30px all round, which is `mtg-m15` to the pixel;
`p03-6` and `pcsp-1` (2003-frame tokens) are 35px, which is `mtg-2003`. A double-faced
punchcard token has no border and its layout already says so. **Do not add a token spec** —
it would be a duplicate of a number already here, and `TestTokensNeedNoSpec` says so. Each is stored as the **exact pixel fractions** of the
image it was read off rather than converted through millimetres — Scryfall's images are
opaque at all four mid-edges, so a pixel count is a fraction of the card directly, and each
divides by the width of *its own* file (some come back 744 rather than 745). Adding one is
purely additive: the generation resolved to nothing before, so nothing that resolved
changes. Every spec that was read off the publisher's scans *by the old detector* was
**withdrawn** first, because two independent problems say that cannot fix an absolute
width:

1. **A scan carries its own crop.** Trimmed 0.3mm inside the cut edge, every border
   read from it is 0.3mm narrow, every card agrees, and nothing in the image says so.
2. **The detector that read them was wrong in three ways**, which is why it no longer
   exists — see the border-step section above. Its worst case was a black border under a
   dark frame: 37-41px where the border ends at 23px, 65% too far.

**The e-Card sets ship *two* frames, which is the first time `BASELINE` answers twice**
(the Black Star Promos are the second, above).
A third hand reading (468×650: left 52, right 20, top 67, bottom 49px) landed on
`pokemon-ecard`'s left and bottom to 0.158pp and 0.070pp — the dot-code strip is in the same
place — with the **top 6mm deeper**. So `pokemon-ecard-deep-top` **shares** left and bottom
with the existing spec rather than restating them a tenth of a millimetre off, and its top
and right are re-derived to hold the reading's **sums** (17.846% vertically, 15.385%
horizontally) rather than its individual edges. That is the same argument the e-Card
asymmetry rests on — a crop shifts two *opposite* edges against each other, so a pair's sum
survives a crop that neither edge alone does — and it makes the substitution lossless: the
design's own height and width as a fraction of the card are exactly what was measured.
`right` was deliberately *not* replaced by the existing 5.100% (0.53mm away, seven times the
agreement the first two cards reached on that edge, and holding the sum would then have
forced `left` to 10.28%, contradicting the edge that did agree). **Which printing the deeper
top belongs to is not recorded**, because it was not read — a guess at *why* would be
provenance prose asserting more than the measurement, which is how a confidence grade grows
back. Both resolve as candidates on every e-Card set (`frames.baselines` returns a tuple now,
`baseline` is its first) and a person picks per card.

**So a printing with no measured spec resolves to nothing** — `Via.NONE`,
`Resolution.spec is None` — and that is a *state*, not a failure. `frames check` names
it, `border` **refuses** the card, and `--frame` is the escape hatch. The per-game
`FALLBACK` that used to fill the gap is gone: it silently reshaped a card of an
unmeasured generation to another generation's numbers, which looks perfect and is
wrong. `Resolution.have` is the predicate; every consumer checks it.

**The shipped baseline is one table with two typed keys (`frames.BASELINE`).** There were
two — `ERAS` (set-id prefixes) and `FRAME_GENERATIONS` (the printing's frame) — and they
were the same thing keyed two ways: both are the baseline a library's own rules are
consulted *before*, and both answer `Via.ERA`. What differs is only *which fact about a
card decides*, and that follows the game: Pokémon's yellow border ran for a known list of
**sets**, MTG's changed with the printing's **frame generation** (which is why a modern set
holding a retro-frame card resolves per card, never per set). So a `Baseline` is
`(spec, sets, frames)` and a game fills in whichever key its border actually followed —
pinned by `tests/test_frames.py`, which asserts each entry has exactly one. `baseline()`
tries sets first and generations second in **two passes**, so which kind of key wins is a
property of the function rather than of the order somebody listed the table in. The set key
is matched **exactly** — see the ex-era note above for the prefix that claimed seven sets of
the wrong era.

Neither key is a bare `str` any more. `Generation` is a `StrEnum` of Scryfall's five
documented values, coerced at the boundary by `frames.parse_generation` (the trait was
written out of untyped JSON, so an unknown generation is **no answer** rather than a
traceback or a stringly-typed fall-through), and a `Baseline.spec` is a `GuideId` because
code names it. All five generations map, and `tests/test_frames.py` pins that the enum *is*
Scryfall's list and that every member is claimed by an entry — so a sixth would resolve to
nothing rather than take a stand-in, and widening it is a deliberate edit, never a silent one.
`sources.mtg_frame` returns `borderless` **and** `mtg-yellow-band`: a yellow `border_color`
is not an ink colour but Aetherdrift's box-topper band, 1.7mm wider on the sides than the
generation it sits in, and the only combination in the survey where colour and geometry
travel together.

**Extended art needs no spec, and that was a correction.** The survey reported its sides at
0 — "the art runs off the card" — and that was the old detector failing on dark art. Measured
over 240 rows of `cmr-700`, the black border is 27-28px on both sides against a plain
card's 29-30. It is the same border with a wider picture. The rest of the survey's negative
result stands and is now checked against hand readings: **31 of the 54 measured
combinations of `frame` × `border_color` × `frame_effects` sit on their own generation's
border**, and the five treated cards read by hand confirm it (etched 29, silver 28, gold at
1997's width, white at 2003's exact 35px against black's 35px — the cleanest demonstration
that colour is not geometry). `docs/measuring-frames.md` keeps the survey's numbers beside
the hand readings: it came out 0.03-0.11mm narrow on four generations of five, as a common
crop predicts, and 0.76mm out on `1993`, which is the generation still under question.

**A full-bleed printing that says `black` keeps the metadata's answer, deliberately.**
`afr-353` (showcase) and `sld-912` (full art) have art at pixel 0 on three edges and
Scryfall calls both `border_color: black`, so they resolve to `mtg-m15` like their
neighbours. That stands: **the two errors are not equally bad.** Treating a borderless card
as bordered costs a hair of border added outside a picture that already reaches the edge —
you see it on the overlay and can pick `borderless` for the run. Treating a *bordered* card
as borderless throws its border fit away entirely and looks perfect on screen. So nothing
here guesses borderless from `full_art`, `showcase` or a dark edge; only
`border_color == "borderless"` and the art-series layout say it, and `--frame borderless`
or a `.pin` is there for the rest.

One open question the scans cannot answer: Alpha and Beta read ~1mm narrower on the
sides (1.88-2.05mm) than white-bordered Unlimited and Revised (2.98mm), and it is *not*
a crop artifact — Sol Ring's art box sits at the same pixels in both scans, so they are
at the same scale. `mtg-1993` currently describes the black-bordered printings.

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

`inventory.py` holds `preview`: **cards read one set at a time**
(`frames preview <set>`, `/api/frames/preview`), only to show which cards a rule
catches. A rule that cannot be previewed is a rule nobody should trust, and "every
card of every set" is minutes of API traffic to answer a question nobody asked. It
caches in `net.cache_dir()`, never in the library.

**And it holds `coverage`, which is the *other* question — what has nobody measured
yet? — and is deliberately not the report that was deleted.** `frames coverage`, the
frames screen's Coverage tab and `/api/frames/coverage` ask each game the question
**its own border followed** (`frames.keyed`, derived from `BASELINE` so it cannot
drift from the table it describes): a row per **set** for Pokémon, a row per **frame
generation** for MTG. That asymmetry is the whole design. The deleted report graded
MTG per set, which has no printing to read, and called 1046 sets unmeasured while
every card in them resolved exactly; asking per set there is not a rougher answer but
a wrong one. Five things about it:

- **The headline counts one kind of thing** (`Coverage.primary`). MTG's baseline also
  keys four sets — the three 1993 bands and `4bb` — as exceptions *to* a generation,
  and summing those in with the generations read **"12 of 12 frame generations
  covered" for a game that has five**: two units added into one confident total. They
  are still rows, still listed, still gaps if nothing answers; they are just not the
  unit. `per_printing` says out loud how many sets the report declined to judge, so
  the silence is counted rather than passing for coverage.
- **Only a *whole-set* answer counts** (`_answers_for_set`): the shipped baseline, or
  a `Match.SET` rule of this library's own. A rule on a number range, a rarity or a
  trait claims *some* cards, so counting it would report a set as answered while its
  ordinary cards still resolve to nothing and refuse to border — a number that looks
  finished, which is what this whole area is careful about. A rule naming a spec that
  is gone is not coverage either; the fit falls through it, so this does too.
- **Two answers is covered, not a fault.** The e-Card sets hold two measured frames
  and a person picks per card — a question with two good answers, exactly as
  `Resolution.ambiguous` is a state rather than a `Fault`.
- **It reads the providers' set lists, not the library** (one cached request per game,
  the same read Browse makes), because a printing nobody has measured is mostly one
  you do not own yet — which is the point of asking. `Row.owned` carries the count you
  *do* hold, so `owned_gaps` is the urgent number: cards already filed that `border`
  refuses. `assess` is the pure half, so the counts are testable without a provider.
- **The note travels with the number** (`Coverage.note`). A reader who does not know
  MTG resolves per printing reads five rows as five sets and concludes the opposite of
  the truth, so the reason is on the report and both surfaces print the same sentence.

This is not a grade and must not become one: every row says whether a spec *exists*,
never whether its numbers are good. That second question is about a physical card, it
is answered in `docs/measuring-frames.md`, and the confidence levels that tried to
answer it on screen are the thing this area deleted.

**The border step's frame setting is the one `OptKind.OPEN` option.** Its values
live in a library that is not open when `steps.click_options` runs, so `--frame` is
**not** a `click.Choice`: the command validates against that library's registry and
names the options in the error (`cli._spec`), `/api/meta` serves the list per
library, and `/api/step` checks it at the boundary (`webui._bad_setting`). Adding a
spec is visible immediately, with nothing restarted.

**cardbleed integration (`bleed.py`, `frames.py`).** proxdex reshaping runs
**in-process** over `cardbleed.bleed_card` (no subprocess). proxdex owns the
*inputs* — the era's target border widths (`frames.FrameGuide.inset`), where
the border currently sits (align marks / `--inner-*`) and **the size this card
prints at** — and cardbleed does the fit (`cardbleed.geometry.solve_fit`):
reshape to exactly the trim aspect with correct borders, optional `stretch` to
hit them precisely. Never store card sizes/border % in cardbleed — they're
proxdex's. The border master is exactly the trim aspect by construction, so
**`sheet` must never stretch** (default `fit = cover`).

**The trim is a `Trim` argument, not `cfg.card_w_mm`, because it is per card.**
Every entry point (`fit`, `fit_plan`, `grow`, `cut_bleed`) takes it and the caller
passes `sheet.trim_mm(card, cfg)` — the same call `sheet` groups pages by and
`upscale` derives its factor from. Reading the config pair here reshaped an
oversized printing to 63.5:88.9, and `sheet` — which *does* impose it at 88.9×127
— then `cover`-cropped 0.91mm off each side to make it fit its own cell: a
2.15mm side border where `mtg-oversized` asks for 2.99, invisible on screen
because the overlay is drawn in fractions too. `doctor`'s aspect check and
`/api/frame` (the align ghost, whose `solveFit` mirrors `solve_fit` against
whatever size it is handed) ask the same function, so all four agree per card.

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

**The import finder read `/api/search` as a bare list, and a wrong shape read as "no
results".** `/api/search` answers with a page envelope (`{total, items: [...]}`) since
Browse and Search were unified onto one query; the wizard's *Which card is …?* panel still
did `Array.isArray(r) ? r : []`, which is always false against an object — so it returned
nothing for **every** query, silently, for as long as the envelope has existed. Nothing
errored: "No matches. Try fewer words" is what a working finder says when it genuinely
finds nothing, so the two states were indistinguishable and it hid. The fix is `r.items`,
and the lesson is the third state: `IMP().results` is now `undefined` (nothing asked, or in
flight), `null` (**the answer was not a shape this panel reads** — said out loud, in red),
or an array (a real answer, possibly empty). A reader that quietly degrades to "empty" on
an unexpected shape cannot be told from a correct one, which is the whole reason to
distinguish them. It also asks for the 24 hits it draws rather than the default 60, which
was warming the art of 36 cards nobody sees.

**A filed row leaves the import list, because the list is what is still *to* import.**
Left in, a successfully imported row re-planned as `replace` — the destination now exists
and `OnExisting` defaults to overwrite — so the Import button went straight back to
offering the same count: the same files, ready to be imported over themselves. Only rows
that really landed are dropped; a **failed** row stays with its error visible, which is the
point of keeping it (fix the id and run again), and blocked and skipped rows stay because
they were never imported at all. The `Just imported` panel still names every card touched,
so nothing about the run is lost by the rows disappearing — and it counts only the cards
that really got a file, not everything attempted. The object URLs of dropped rows are
revoked (each holds the file's bytes alive, and a folder of two hundred scans is not
small), and the finder's row index is cleared rather than re-mapped onto a list that just
changed shape.

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
`ls --json`, the same shape `/api/cards` serves); its Browse screen is `proxdex sets` + `proxdex browse SET`, and its search filter bar is
their shared flags (`--rarity/--year/--type/--supertype/--subtype/--color/--sort/--page/--per-page`);
its data sheet is `proxdex show`;
its settings screen is `proxdex config show|set|prune` (tomlkit, comment-preserving,
every value through `Config.coerce`); its batch list is `proxdex batches`; its
delete is `proxdex rm`; its frame-specs screen is the `frames` group — specs, rules, coverage and warnings, with
`frames preview` behind every Preview button, `frames check` behind the Warnings tab,
`frames coverage` behind the Coverage tab, and a Pin control on the align panel; its
stored-images screen is `proxdex doctor` (the report read directly, the repair
shelled out as `doctor --fix --yes`); its **sheet
builder** is `sheet` with copies and per-run overrides (its live page count is
`--dry-run`); its **import wizard** is `import` with `--dry-run`/`--on-existing`
(its review table *is* the dry run, and every row is one `import <file> --id …
--stage … --face …` call, so the CLI stays the only implementation); its **print
screen** is `proxdex profile` + `proxdex calibrate`, one control per verb. Going the other way, `SheetBody.cards` lets the UI impose a
*selection with copies* (`sheet <name> <id[:n]...>`), `FetchBody.related` is
`fetch --related`. Border is the one step with **no** bulk action, on both sides: it has
to be told where each card's border is, one card at a time.
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
selected mark and four numeric fields take an exact inset. A **dashed** ghost
outlines the trim the fit will produce and, inside it, the border it is aiming at;
the marks you drag are **solid** with grips. Same accent, different line style —
see the theme note below for why that replaced a second hue.
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

**And ask what order it runs in.** The reuse path must do `renderStepPanel` *then*
`repaintProof`, matching the full build — because `renderStepPanel` emits an **empty**
`#alpanel` and a fresh `#runbtn`, and it is `wireViewer` (inside `repaintProof`) that fills
the align panel in and enables Run. Reversed, it blanked the panel it had just written: a
done border step lost the target outline that is the whole check the fit landed, and only
on that path, so it looked like a bug in the overlay rather than in the sequence.

**Two async readers own the viewer, and both re-check they still do.** `startAlign` and
`loadSpecFacts` await `/api/frame` and then write into `.viewer`, so each captures
`viewerOwner(c)` — card, side **and step** — before the await and bails if it changed.
Without the step in that key, running Border advanced focus while the request was in
flight, and `buildAlignLayer` then grabbed the *upscale* step's viewer, hid its image and
drew marks over it. That surfaced as three separate complaints — marks on the upscale
proof, marks on a done border step, and the upscale proof still showing the pre-border
picture until you clicked away — and was one missing guard plus `align.show` surviving the
run (`afterStep` now puts the marks down: the act of aligning is over, and re-opening them
is one click).

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
percentage.

**Everything that sticks pins to `--bar`, which is measured.** The topbar is sticky, and
four things sit below it: the card page's rail and step panel, the settings/frames tab bar,
and the sheet builder's summary column. They each hardcoded `top: 4.5rem` — a *guess* at a
bar whose height depends on whether its nav has wrapped. It is 53px at desktop widths and
**138px at 560px**, so at narrow widths every one of them sat 66px *underneath* the bar it
was supposed to sit below, and at wide widths it left a 19px gap. `syncBarHeight()` sets
`--bar` from the bar's own `getBoundingClientRect` and keeps it there with a
**ResizeObserver** — not a resize listener, because the bar changes height without the
window changing size (a longer library path, a wrapped nav). `Math.ceil`, because half a
pixel short is still underneath.

**The stepper stays put, and below `lg` that needed `display: contents`.** The filmstrip is
how you navigate between steps, so it has to stay reachable while you scroll the proof and
the card-data sheet. On a wide screen the whole rail is sticky and it rides along. Below
that the rail is a short full-width block at the top of a long page — and a sticky element
**cannot outlive its containing block**, so however sticky the strip was it scrolled away
with the rail. `\.rail { display: contents }` makes the rail's children items of the card
grid, whose height is the page, which is the distance the strip has to stay put for. Only
the *navigation* is pinned (`.stepnav` = face tabs + filmstrip); the card's name, the
oversized note and Delete card stay in the flow. The strip lays out horizontally there,
with the four stages **sharing** the width rather than side-scrolling: a strip you have to
swipe hides half the pipeline behind a gesture, which for the thing you navigate with is
worse than a truncated label.

**A sticky strip must be opaque, or it is not a strip.** The frames screen's
Specs/Rules/Warnings bar was transparent *and* static: it scrolled away, and the moment it
was made sticky the page showed straight through it. `.tabbar` is the one class for "a
section tab strip that sits under the topbar and stays there" — sticky at `var(--bar)`,
`background: var(--ink)`, a hairline under it because it spans the page — and the settings
and print sidebars become exactly that strip once they collapse below `lg` (row, opaque, the
vertical divider hidden and the count un-floated, since neither means anything in a row).
Those are direct children of their grid, so unlike the card rail they needed no
`display: contents`.

**One accent**, borrowed from the marks a press really prints —
**registration magenta** — for every control, the crop-mark corner brackets
(`.marks`, which hover on contact-sheet tiles — never around the proof, where
nothing may compete with the card) and every overlay. There were two: cyan meant
"a measurement rather than a control", which was a real distinction while the
border was measured off the image and stopped being one when that was removed.
Overlay lines that must be told apart differ by **line style** instead — dashed is
what is being aimed at, solid is what you drag — which survives them landing on
the same pixel, as they do at the moment the marks come up. A second hue did not:
two differently-coloured hairlines a pixel apart read as a misalignment rather
than as agreement.

**Two one-pixel traps in that overlay, both of which looked like bad geometry.**
CSS `border-width` does not accept a percentage, so the band written as
`borderTopWidth: '2.88%'` silently drew *nothing*; it insets an inner element
(`setBandLine`) now. And an `outline` paints entirely **outside** its box, so the
band's line sat half a CSS pixel further out than the mark's hairline, which is
centred on its value — 1-2 device pixels of disagreement between two things that
are the same number. `outline-offset: -.5px` puts both centres on one line; it is
load-bearing, not cosmetic.

**`--card-radius` is measured, not nominal.** The alpha channel of 15 Scryfall
PNGs — every frame generation, every border colour — starts its straight edge at
exactly 32px on both axes: 4.30% of the width, 3.08% of the height, **2.73mm**, not
varying by a pixel between them. It was the nominal 3mm, and that is the wrong
number here because proxdex flattens the transparent corner to the card's own
border colour when it files an image: the corner reaching the browser is *square*,
and this radius is the only thing rounding it. Cutting 0.27mm harder than the
die-cut leaves a wedge of page background inside the card's own black border,
which on an MTG card reads as a bite out of each corner. One number, both games,
used by the contact-sheet tiles, the proof viewer and the search results.
`/api/config`
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

**A key nothing reads is worse than clutter, and `config prune` removes it.** A
`proxdex.toml` outlives the features that filled it: the real library carried `[border]
thresh` plus two target ratios from the **deleted** auto-detector, and `[grade] normalize`,
`black_pct`, `white_pct`, `match_border_target` from the **deleted** frame white-balance.
Every one of them *looks* like it is configuring something. Both surfaces already said they
were ignored — the settings screen chipped them `not a proxdex setting`, `config show`
warned — and neither offered to remove them, which is half an answer. So there is one verb,
`config prune` (`--yes`, or the settings screen's **Remove them**, which shells out to it),
and it only ever touches keys with **no** `Config` field: a real setting is changed with
`config set` and never deleted here, so this cannot quietly reset one.

It is a **line pass** rather than a tomlkit round-trip, and that is the interesting part:
deleting the key alone leaves its comment, and an orphaned comment is the same trap one
level up — "normalize: pull each card to a common baseline first" sitting above
`brightness`, describing a feature deleted for turning a neutral grey into deep blue. So a
pruned key takes the contiguous comment block above it, a table left holding nothing goes
too (`[border]` existed only for the detector), and every other byte is untouched. The
result is **re-parsed and checked** against "the old keys minus the doomed ones" before it
is written, falling back to a plain tomlkit deletion if the file is not shaped as expected
— the wrong prose beats a broken config. Pinned by `tests/test_config_prune.py`, which
earns its place because this rewrites the one file a person typed by hand and every failure
mode is invisible until much later.

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

**And every grade default is identity.** They used to lift the image (brightness 1.03,
contrast 1.06, saturation 1.10) on the reasoning that paper and ink dull it — true, and
still the wrong place, twice over. It is a fact about *your* printer and *your* paper, so
numbers proxdex invented for a press it has never seen are a guess wearing a label — the
same mistake as the print presets that were deleted for it ("foil needs saturation 1.38"
described exactly one setup). And a correction identical for every card on the sheet is by
definition the medium's, which is what a print profile is *for* and where it can be
measured rather than typed. So the pipeline with nothing configured returns the card, and
a look is something you chose. `DEFAULT_TOML` says the same and says why.

**The upscale factor is derived, because the factor is the wrong thing to hold still
(`upscale.plan`).** Sources arrive anywhere from 400 to 745px wide, so one fixed factor
scatters the masters it makes: measured on the real library, identical settings gave
**592 dpi** on one card and **1011** on another, with nothing on screen to say so. So what
is configured is a **minimum resolution** — `[tools] upscayl_min_dpi`, default 1000, which
is 2480px across a 63mm card — and the factor is arithmetic: the smallest that clears it.
`--scale` overrides for a run (the step's one `optional` option with no config default, so
"unset" means "clear the minimum"); `min_dpi = 0` falls back to `upscayl_scale`.

**A minimum and not a target, and `sheet_dpi` is why.** The page is rendered at
`sheet_dpi` — 1400 by default, 3472px across a 63mm card — so a master below that is
resampled *up* by a plain filter at print time, which is exactly the work the neural
upscaler was run to avoid. Overshooting costs disk and a little time; undershooting costs
resolution nothing downstream can restore. That does mean a **step**: the doubled ladder is
1, 4, 9, 16, so a 600px master goes to 5400px rather than the 2400px that would have landed
it at 968 dpi. Taken deliberately — the 2400px version would have been upsampled 1.45× by
the sheet renderer anyway. Every source width the real library holds (405-744px) clears
1000 dpi, and the bordered masters land at 4× just over it, so `base3-4`'s existing
2508×3504 master is reproduced exactly.

An earlier version aimed at a *target* and took the nearest factor by ratio, which was
wrong in a way worth remembering: because the ladder is multiplicatively coarse, **every
target from 1050 to 1450 dpi produced byte-identical output** across those widths — the
setting looked precise and was not — while a card landing 3% under was left there and
upsampled at print. A minimum says what it means.

**Double Upscayl squares the factor** rather than doubling it (it runs the model over its
own output, so 2× twice is 4× and 4× twice is 16×), which is why `effective()` exists rather
than a multiplication inline. And `Plan.short` means **the largest factor still could not
clear the minimum** — the one case that can happen — reported once per run rather than per
card, because a line each would be 500 warnings on a 500-card run.

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

**A profile name in the config outlives the profile, so a dangling one is reported
rather than raised.** `[print] profile = "foil"` survived the deletion of the
built-in presets in a real library, and the only thing that noticed was `sheet` — at
the end of a run, with `no print profile named 'foil'`. It is the same broken
reference `frames check` reports as `Fault.MISSING`, so it is answered the same way:
`profiles.dangling(root, cfg)` is one pure function over the config and the
profiles directory, and `proxdex where`, `profile list` and `/api/profiles` (the
print screen's banner) all print it. Both keys, since `back_profile` raises just as
late; an **unset** key never dangles, because unset means the identity for the
fronts and "the same medium as the fronts" for the backs, and both are answers.
`profiles.named` is the other half — what a configured name resolves to *here*,
slug-matched, `none` when unset, `None` when nothing answers to it (including a
string that is not a legal profile name at all, since this is asked in order to
report). That is what places `profile list`'s `→` and the UI's `active` chip, so no
marker happens for exactly one reason, and **the legend only describes a marker
that is on the page**: without it the table explained an arrow that was absent
precisely when the absence was the thing worth naming.

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
- Frame specs must stay honest, and the mechanism is **prose in the repository**, not a
  field: a `FrameGuide` is four numbers, and where they came from is a comment above the
  spec plus a row in `docs/measuring-frames.md`. Do not reintroduce a confidence level, a
  `measured` boolean **or a `note` string** — a border read off a publisher's scan
  inherits the scan's crop, which no sample size detects, so any grade that calls such a
  reading trustworthy is the bug this replaced, and a per-spec provenance slot is how a
  grade grows back. And do not reintroduce border **auto-detection**: measuring the border
  off the image was tried, shipped and removed, because four plausible numbers presented
  as a measurement is the same bug in a different place.
- **A new CSS class in `webui.html` has to be checked against the file first.** It is one
  stylesheet with no scoping, and the sheet's later rule wins: a `.setgrid` added for the
  set index collided with the settings screen's own `.setgrid` (which is one column, so the
  grid silently collapsed), and a `.chip` for a filter chip **restyled every chip in the
  app** — the `printed`/`queued` tags on library tiles, the kind tags, the frames screen.
  Both were invisible in Python and in `node --check`. Grep for the selector, and prefix a
  screen's own classes (`expgrid`, `qchip`, `bzbar`, `calslot`, `sheetpage`) when the plain
  word is taken — the progress bar wanted `.bar`, which `.tally` and `.cmp` already scope
  for their own use.

  **The signature to look for is a bare single-class selector defined twice *outside* any
  media query** — two `@media` refinements of one class are the normal mobile-first
  pattern and are fine, while two unconditional definitions are two screens fighting.
  Extract the `<style>` block, strip comments, walk it with a brace counter and report any
  class matching `^\.[\w-]+$` that appears in more than one top-level rule. Run it when
  something "looks weird" in a grid; it found both of the collisions below in one pass, and
  neither had been caught by reading the file.
- **No backticks inside a template literal in `webui.html`** — not even in an HTML
  comment. A `` ` `` in `<!-- the strip above `#results` -->` **ends the literal**, and
  everything after it is parsed as code: the failure is a `SyntaxError` hundreds of lines
  away with no hint of the comment that caused it. Nothing in Python or in a browser tab
  catches it, which is what `node --check` on the extracted script is for — run it after
  every edit to that file's `<script>`.
- **Never reference a top-level object by name from an inline HTML handler.** An
  inline handler's scope chain starts at the *element*, and an `HTMLElement` has a
  legacy `align` property — so `onchange="align.show=…"` silently writes to a
  discarded `String` wrapper. Every handler calls a named function
  (`toggleMarks(this)`, `resetMarks()`, `setSearchRelated(...)`).
