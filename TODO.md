# TODO

Compact backlog. `- [ ]` = to do, `- [x]` = done. Keep each item to one terse line.

## Border step

- [x] Auto detection reworked: per-line reference (gradient/holo/silver frames), scan starts past the cut edge, densest-cluster answer
- [x] No stage image keeps an alpha channel — import/upscale/border flatten like fetch does
- [x] A `doctor` verb: report (and offer to repair) stored images a newer proxdex would have written differently
- [x] Auto-detect where the border actually is (any colour) and pre-place the align lines from it — per-edge support, never silent
- [x] Say when a set has no measured target border — both CLI and UI, not just a dim note
- [x] Draw the target border in the viewer while the border step is focused
- [x] Put the border settings + Run in the right panel like every other step — no click-through to align
- [x] Rework the align lines: crisp constant-width marks, handles clear of the border, precise placement (nudge/zoom) instead of hover-thickening

## Games & specs

- [x] MTG borders censused from Scryfall `frame`/`border_color` and measured per generation (1993/1997/2003/M15, ~0.65mm M15 drop); `scripts/mtg-census.py` re-runs it
- [x] Confidence levels and spec origins deleted — a scan-derived number cannot be graded trustworthy, since a scan's crop shifts every reading systematically
- [x] Coverage report deleted; `frames check` + a Warnings tab report four real faults instead (unreadable/missing/undecided/unknown)
- [x] One spec per Scryfall frame generation, `mtg-future` no longer aliased to `mtg-2003`; MTG fallback is `mtg-m15` (2/3 of prints)
- [x] Surveyed all 114 (frame × border × effects) combinations: 31 measure at their generation's border (no spec needed), `extendedart` and the yellow band do not
- [x] `mtg-extended-art` (sides 0) and `mtg-yellow-band` ship; both answered from the printing like `borderless`
- [x] `Match.EFFECT` + game-wide rules (empty set) so a treatment can be assigned at all; `frame:future` re-aliased to `mtg-2003` (surveys within 0.05mm)
- [ ] **Waiting on calipers**: measure the 5 MTG cards in `docs/measuring-frames.md`, then `frames set` each — shipped numbers are provisional
- [ ] `frame:1993` has ±0.39mm internal spread (Alpha/Beta/Unlimited/Revised/4ed + foreign black-border runs all differ) — that era may want per-printing pins rather than one spec
- [ ] Measure a modern Pokémon card (SV + SWSH) and add specs — 160 sets still on `pokemon-generic`, which is the WOTC numbers reused on an unchecked assumption
- [x] Frame specs are library data: `specs.py` registry + rules (ranges/traits), per-card pin, `frames` group + UI screen, set coverage from the provider lists, `doctor` stale-spec
- [x] Full MTG parity with Pokémon: config, card specs, fetch, backs, set ids
- [x] Detect MTG borderless prints from Scryfall `border_color`/`full_art` — recorded in the card's `.frame`
- [x] MTG double-faced cards: both sides fetched, per-side pipeline, flip which side prints
- [x] Meld cards: three ordinary cards + a recorded relation, `fetch --related` gets the lot
- [ ] Read https://scrydex.com/docs/pokemon/api-reference in detail, and ensure we are using as much as relevent like set icons, set metadata, rarity or other types etc. But we cant use the actaul API, i think we need to simply look at scrydex set pages and figure out how to get the data we need from there. I want the search part of the application to support browsing by set like in scrydex for both pokemon and mtg but for mtg I guess we fetch the actual card images from elsewhwere if better quality is in scryfall etc. https://scrydex.com/pokemon/expansions and https://scrydex.com/magicthegathering/expansions Basically I want a complete rework of the search and browse functionality of the entire application to provide an almost native experience with best in class UX and features to help users find and discover cards AS WELL AS providing actual search and filtering capabilities somehow. For both library AND search AND browse we need to implement pagination if needed and supported. For both I think also look at overall groupings of sets as well, just like scrydex does.

## UI

- [x] Adopt a component library and compact the markup — responsive, premium, sellable-SaaS feel on every page
- [x] Richer card page: everything the APIs return, plus links to the official card page
- [x] Ensure library pages have correct borders on cards so it looks like we have cards, not just a list of images. Use the image on the last step run
- [x] Modern Pokémon source images arrive RGBA — `_flatten` now fills from the card's own edge colour
- [x] Real SPA: per-screen URLs + back/forward, no refetch on view switch, permanently cacheable images
- [x] Every Upscayl setting enum/Literal-typed in config, CLI and UI
- [x] All stages show their settings from the start, consistently — one declarative step registry drives CLI, API and UI
- [x] Border dragging shows a zoomed loupe of the mark with its live position
- [x] Lightbox removed — the proof is as large as the viewport allows, with a link to the full-res file
- [x] Not-yet-run stages show their input undimmed; the state is stated in the chrome, not over the card
- [x] Card page redesign: filmstrip stepper, togglable compare tool (result/wipe/fade), reworked controls
- [x] Library page: filter/sort, selection + bulk one-step actions, per-stage tally, tile density, two-sided badge
- [x] Settings page: section nav, described fields with units and defaults, sticky dirty save bar
- [x] Keyboard shortcut overlay (`?`), one table driving the sheet and the handler

## CLI / UI parity

- [x] Every text file written/read as UTF-8 + LF — `init` crashed on Windows writing its own config
- [x] CLI output no longer dies on a non-UTF-8 stream (Windows pipes, LC_ALL=C)
- [x] Verified on macOS, Linux and Windows: CI runs the suite + a real library end to end on all three
- [x] cardbleed 0.4.1: same audit upstream — redirected stream reported finished cards as errors, refused inputs leaked a handle; CI now covers Windows there too
- [x] Pin `cardbleed>=0.4.1` — it prints into proxdex's own stdout, so its stream fix is proxdex's too
- [x] `tomlkit` is a core dependency, not a `[ui]` one — `sheet` and `config set` import it, so a bare install died after writing the PDF
- [x] CI installs the built wheel bare and drives the pipeline through it — `uv run` has the dev group, so it could never see a missing dependency
- [x] Error text goes through `rich.markup.escape` — the `proxdex ui` hint printed `install "proxdex"`, minus the `[ui]`
- [x] cardbleed has proxdex's release system: `scripts/release.sh`, annotated-tag notes, tag/version check, three-platform gate
- [x] Upscaling behind an `Upscaler` backend; the step reports whether its tool is installed (CLI, `where`, /api/meta, UI) instead of failing per card
- [x] Find Upscayl on Windows and Linux too, not just macOS — paths from its own electron-builder layout, env-derived on Windows
- [x] No `[upscale]` extra — Upscayl is not a Python package and an extra would install nothing; documented in the README
- [x] `show`, `rm`, `batches`, `config show|set`, `ls` filters + `--json` — the UI could, the CLI couldn't
- [x] Sheet a selection from the UI (`SheetBody.ids`); `fetch --related`; bulk Border measures each card
- [x] Frame specs screen in the UI — `proxdex frames`, both tables
- [x] Import a folder from the UI: an `imports.plan` shared by `import --dry-run` and the wizard, per-row id/stage/side + Find…, duplicate handling, per-file progress
- [ ] Import wizard: bulk-set a set/id prefix over the unmatched rows, remember the last folder
- [x] `search --open` / `sheet --open`: browser-native equivalents (`full ↗` per hit, the written PDF linked), `sheet --open/--no-open`, and the UI forces `--no-open` so the server never pops a viewer

## Colour: grade, print profiles, calibration

- [x] Stretch-to-spec on by default (`[border] stretch`), CLI + UI
- [x] Grade is the look only — drop the frame white-balance, it turned neutral grey into deep blue
- [x] Grade's whole recipe is per-run settings (brightness/contrast/saturation/gamma/levels), not one mystery toggle
- [x] Medium correction belongs to the print step, chosen per sheet run
- [x] Named print profiles with notes: new/rename/rm/use/set, CLI + UI
- [x] Calibration is iterative: many rounds on one sheet, a new slot each round, all rounds fitted together
- [x] Convergence is visible — per-round residual, target-vs-scanned swatches
- [x] A print screen in the UI: profiles, notes, the round table, chart download, scan upload

## Sheet building

- [x] A real sheet builder: pick cards, copies, per-run overrides, page plan before writing
- [x] `sheet --dry-run` / `/api/sheet/plan` — page count and what is not ready, without writing
- [x] Copies per card (`id:4`) and `--copies`, recorded in the batch manifest

## Colour / sheet — still open

- [x] No invented presets — one built-in identity, every real profile is one you made
- [x] Two honest routes to a profile: the scan loop, or four numbers judged off a printed strip (`profile strip`/`preview`)

- [x] Rounds are switched off, never deleted — plus a per-round _pull_ (how far the correction moves without it), which fingered a botched scan at 17.7 vs 5.6/5.4/4.4
- [x] Chart v2: 16-step grey ramp + 4³ interior lattice, 80 patches — 12/36 usable became 76/80, and 2.31 → 1.36 mean RGB on the same press
- [x] Density settled by measurement, not taste: 228 patches is worse than 80, 512 worse than 36, a gradient worse again, and a 3-D LUT loses to the polynomial at every density
- [x] Chart versioning removed with the rest of the back-compat — one chart, and a round whose shape no longer matches is counted as unreadable rather than dropped in silence
- [x] Fronts and backs can be different media (`[print] back_profile`, `sheet --back-profile`) — unset means one profile, which is right for duplex
- [x] `in_gamut` tests reachability by inverting the print's own response (needs a send in 0..255), not by a per-channel box — a box counts saturated corners no ink can reach; matte-300 read 17.7 mean over "76 reachable" and reads 12.7 over the 67 it can really hit
- [x] One medium, one gamut: `Profile.gamut` pools every live round and scores them all against it, so the trend moves when the print improves, not when the patch set does
- [x] The loop says when it is done (`Profile.plateau`) — three rounds under 0.5 RGB each and `calibrate add` stops inviting the next chart; matte-300 reads converged at rounds 4–6
- [x] Releasing is one command (`scripts/release.sh`), the tag message is the release notes, and `Release` re-checks the gate + tag/version match before publishing
- [x] GitHub releases exist for every tag (v0.1.0 … v0.5.0), with the artifacts attached — v0.5.0 rebuilt from its tag matched PyPI's sha256

## Robustness

- [x] Retry + cache Scrydex calls (500s are frequent) and show when the API is degraded
- [x] `--root` only works before the subcommand, but the "no library here" error suggests it as if it were global
- [x] Update the README to the per-step pipeline — its table still lists the removed `build`
- [x] `.playwright-mcp/` is not gitignored — screenshots land in the repo
- [x] Every web API request body is a validated pydantic model (`extra="forbid"`, pattern-checked ids)
- [x] Inline HTML handlers shadowed `align` by the element's legacy `align` property — the marks checkbox never worked
- [x] Settings sections beginning `_` (backs, frames, calibration) were reset to the first TOML table on click
- [x] Batch manifests written via tomlkit — a quote or backslash in a note broke the hand-rolled TOML
- [x] Card page rebuilt itself on every step focus, and the library grid on every keystroke — the image flicker
- [x] Automated suite for the three things eyes can't recheck: the faces model, `detect_inset`, `solveFit`/`solve_fit` parity (the UI's JS run in node) — in the CI and release gates
- [x] Oversized cards imposed at their own trim, on their own pages, by default in CLI and UI
