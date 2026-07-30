# TODO

Compact backlog. `- [ ]` = to do, `- [x]` = done. Keep each item to one terse line.

## Border step

- [x] Border auto-detection **removed entirely** — `borders.detect_inset`, `--auto`, `/api/detect`, the bulk Border action and test_borders.py are gone. It over-read a dark frame's black border, read past a decorated frame's keylines and found a border on a full-bleed card; four plausible numbers presented as a measurement look finished
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
- [x] `Match.EFFECT` + game-wide rules (empty set) so a treatment can be assigned at all; `frame:future` re-aliased to `mtg-2003` (surveys within 0.05mm)
- [x] All scan-derived MTG specs withdrawn; a printing with no measured spec resolves to nothing (`Via.NONE`) and `border` refuses it instead of falling back
- [x] `mtg-1993` from a pixel reading of the Beta Sol Ring scan (672x936: sides 21.5px, t/b 28.5px), stored as exact fractions of 63.5x88.9
- [x] `pokemon-wotc` scoped to the WOTC era only — base/gym/neo, stopping where e-Card begins
- [x] `mtg-1997` from a pixel reading of the me4 Sol Ring image (745x1040: sides 36px, t/b 41px); a colour scan of the same file agrees (top 40, sides 37-40)
- [x] `mtg-2003` from c13-259 (35px all round), confirmed by a scan of the same file and by the white-bordered 8ed-274 at the same 35px; covers the `future` frame too, since mb2-233 measures 35px as well
- [x] `mtg-m15` from msc-211 (30px all round, a plain untreated card); four treated cards of the generation read 28-29px, so it sits in a 0.17mm band
- [x] `mtg-yellow-band` from dft-501 (sides 50px, t/b 44px), wired to `border_color: yellow` in `sources.mtg_frame`
- [x] Extended art needs **no** spec — the survey's "sides = 0" was the old detector failing on dark art; cmr-700 carries 27-28px sides over 240 rows, i.e. the M15 border with a wider picture
- [x] Border step reworked: opens on the spec's own outline over the original, then Skip or **Align the border** for draggable marks starting at the spec's numbers; Run is held shut until a fit solves, and a borderless printing needs no marks at all
- [x] Fixed: the cyan target band never drew — it was written as percentage `border-width`, which CSS does not accept; it is an inset element now
- [x] One accent (registration magenta) everywhere — cyan removed; overlay lines differ by dash vs solid, which survives them landing on the same pixel
- [x] `--card-radius` measured off 15 Scryfall PNGs' alpha: 32px = 4.30%/3.08% = 2.73mm, not the nominal 3mm that bit a wedge out of every MTG corner
- [x] Border step keeps the card radius (it moved onto `.al-work`, so the ghost can still overflow) instead of dropping to a flat 4px
- [x] Target band vs marks: fixed `outline` painting outside its box (`outline-offset: -.5px`) and the band being drawn on the trim box, which put the whole trim-minus-image difference on the right and bottom edges
- [x] skipped vs done vs pending are three renderings now: done = output + compare + the target outline over it (the cheapest check the fit landed), skipped = the master it stands as, solid frame, no overlay, pending = dashed + badge
- [x] The overlay is a statement about the card and is always drawn; the draggable marks belong to the align action alone
- [x] `afr-353`/`sld-912` keep their metadata answer — treating a borderless card as bordered is cheap, the reverse throws a border fit away and looks perfect
- [x] Everything sticky pins to a measured `--bar` (ResizeObserver on the topbar), not a hardcoded 4.5rem that sat 66px under a wrapped bar at 560px and left a 19px gap at desktop widths
- [x] One `.tabbar` class for a section tab strip under the topbar — opaque and sticky; the frames Specs/Rules/Warnings bar was transparent and static, and the settings/print sidebars become that strip below lg
- [x] The stage stepper stays put while scrolling: below lg the rail is `display: contents` so the strip's containing block is the page, and the four stages share the width instead of side-scrolling
- [x] The align layer leaked into the next step: `startAlign` awaits `/api/frame` then writes to whatever viewer is on screen, so running border injected marks over the upscale proof and hid its image (which is also why it showed the pre-border picture until you clicked away). Both async readers now check `viewerOwner` — card, side **and step** — across the await
- [x] `align.show` survived a run, so re-opening a done border step came up on the draggable marks instead of the outline; `afterStep` puts them down
- [x] The reuse render path ran `repaintProof` before `renderStepPanel`, so it blanked the `#alpanel` it had just filled — a done border step lost its target outline, on that path only
- [x] Calipers dropped from the plan — they only ever answered whether every spec is uniformly a hair narrow (a *shared* crop, so it lands on every card equally and no image can detect it). Every question left is one printing *against another*, where a common crop cancels, so a pixel reading settles it. The worksheet is in `docs/measuring-frames.md`
- [x] `mtg-1993` re-read at 745×1040 (was 672×936, a size Scryfall no longer serves) — every spec is now on the same divisor
- [x] `mtg-1997` vs `mtg-2003` are **not** one spec, checked: the *sides* agree (3.07-3.24 vs 2.98, 1-3px) but the *tops* do not — 3.42-3.50 vs 2.99, a 0.43mm / 5-6px gap, unanimous across three cards each way. 1997 has a thicker top and bottom than its sides (40-41 vs 36); the 2003 redesign is what made all four edges equal (35/35, confirmed in black *and* white). Merging to the larger would put a 3.50mm top target on every 8th Edition-M14 card whose border is 2.99mm
- [ ] **Pokémon past `neo4` has no spec at all** — e-Card, EX, SWSH, SV. Now the only real gap left, and answerable exactly the way the MTG ones were: one common per era, read in pixels off the provider's image, divided by that file's own width. The MTG work says to expect *bands* rather than a number per set, so a handful of cards may cover everything; `pokemon-wotc` (the one measured Pokémon spec, calipers) is the comparison target
- [x] **The 1993 frame is three specs, settled.** Lightning Bolt in all five printings (the one card they share, so the art is identical): Alpha/Beta 23px sides, Unlimited/Revised 35px, 4th Edition 30px — a full 1mm apart, with Sol Ring agreeing exactly and a run-length scan reproducing the white trio to the pixel. Keyed by **set** (`lea`/`leb`, `2ed`/`3ed`, `4ed`), since Scryfall calls them one frame. Revised and 4th are both *white* and differ by 5px — the sharpest proof yet that colour is not geometry
- [x] **Oversized cards have their own specs.** `mtg-scheme` (`oarc-1★`, 35px at 1040×1490 = 2.98/3.00mm of an 89×127 card) and `mtg-vanguard` (`pvan-101`, 63/48 at 1060×1510 = 5.30/4.03mm), read from the **layout** in `sources.mtg_frame`. A scheme's border is *physically identical* to a 2003-frame card's but a different **fraction**, so the old resolution asked for 1.2mm too much on every edge and looked right doing it. Planes are unreadable (art to the edges, uneven) → `borderless`
- [x] A spec is four numbers and nothing else — `note` and `ref_mm` gone, one `frames.CARD_MM` (63.5×88.9, both games), provenance is a comment above the spec plus a row in `docs/measuring-frames.md`
- [x] One typed baseline table (`frames.BASELINE`) replaced `ERAS` + `FRAME_GENERATIONS` — same purpose, two keys, and neither is a bare `str` now (`Generation` StrEnum, `GuideId` values)
- [x] **Tokens need no spec, measured not assumed.** `tmsh-3` and the emblem `tdft-13` read 30px all round = `mtg-m15` to the pixel; `p03-6` and `pcsp-1` (2003-frame) read 35px = `mtg-2003`. A double-faced punchcard has no border and its layout says so. Nothing added — a token spec would be a duplicate
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

- [x] **The 1993 frame settled over 26 sets, and the 3.2% gap is closed.** Every set reporting it read by hand; they collapse into three *bands*, not 26 numbers — narrow 23/32 (`lea`/`leb`/`ced`/`cei`, and the Collectors' Editions confirm band 1 independently), ordinary 29/32 (**18 sets**, Arabian Nights → 4th Edition and the 1995-96 reprints, spanning just 0.43mm) and wide 35/42.5 (`2ed`/`3ed`). Band 2 is a *generation* entry, so nothing in the frame refuses; it absorbed the briefly-separate `mtg-1993-4ed` (30/33 against a 29/32 median — one pixel per edge is not a spec). `4bb` alone fits no band at 36/40, which is `mtg-1997`'s numbers exactly, so its set points there; its sibling `fbb` went the other way into band 2, so there is no "foreign" rule and no colour rule

- [x] `mtg-1997` now rests on `sld-1664` (40/36 at 744×1040), a card that physically exists — `me4-227` was an MTGO-only render of the frame template and its 41px top was the odd one out against both real 1997-frame prints
- [x] Planes and phenomena share `mtg-oversized` (renamed from `mtg-scheme`) rather than being called borderless — same product line, same 89×127mm stock, same era, and the scheme's 2.98/3.00mm *is* the physical border an ordinary 2003-frame card carries. Unreadable directly, but the safe direction: calling a bordered card borderless throws its fit away and looks perfect
- [x] Showcase frames take their generation's spec, and that is right rather than merely untested: a showcase is the same die and the same printed border, only the *interior* art and frame treatment differ. The survey's 31-of-54 result already said treatments sit on their generation's border, and five treated cards read by hand confirmed it

- [x] **A dangling `[print] profile` is reported before print time.** `profiles.dangling` is one pure function over the config and the profiles dir, reported by `proxdex where`, `profile list` and `/api/profiles` (the print screen's banner) — the same broken reference `frames check` calls `Fault.MISSING`. Both keys, since `back_profile` raises just as late. Unset never dangles: it means the identity for fronts and "the same medium" for backs
- [x] `profile list`'s legend only describes a marker that is on the page. The `→` is placed against what `[print] profile` *resolves* to (`profiles.named`, so unset marks `none` and a name is slug-matched), which means no marker happens for exactly one reason — and the warning under the table now names it instead of a legend for an absent arrow. The UI's `active` chip was string-comparing the raw name for the same reason and now reads `active_name`

- [x] **An oversized card is reshaped to its own size.** `bleed.fit`/`fit_plan`/`grow`/`cut_bleed` take a `trim` argument instead of reading `cfg.card_w_mm/card_h_mm`, `doctor`'s aspect check asks `sheet.trim_mm`, and `/api/frame` hands the align ghost the card's trim — one call, the same one `sheet` already grouped pages by. Verified: `oarc-1` now fits to 1043×1490px (aspect 1.42857 = 127/88.9) with its border at 2.98/2.99mm, where before it fitted to 1064×1490 (1.40000) and `sheet`'s `cover` cropped 0.91mm off each side, printing a 2.15mm side border against the spec's 2.99
- [x] `/api/frame` stopped serving `card_aspect`: nothing read it, and it was the same fact as `card_w_mm`/`card_h_mm` rounded to three decimals — `solveFit` divides them itself and is held to cardbleed within 1e-12, so the rounded copy could only ever be the wrong one
- [x] **One card size, and it is the published spec.** `games.CARD_W_MM`/`CARD_H_MM` = 63.5×88.9mm (2.5×3.5in, poker size) is now the trim *and* the card a spec's millimetres are fractions of — Wizards and The Pokémon Company state the same size, so the games are identical and it is one constant. Not a measured card: calipers read ~63×87.9 and 63×88 is a common rounding, but one reading inside a ±0.5mm cutting tolerance is somebody's off-cut. With trim and reference identical, a caliper reading of a 3.45mm border prints as 3.45mm, and `frames show` stopped reporting a width 0.8% off the printed one

## Pipeline defaults

- [x] Grade defaults to identity — a look proxdex invented for every card is the same mistake as an invented print profile
- [x] Upscale factor derived per card from `[tools] upscayl_min_dpi` (1000, which reproduces `base3-4`'s 2508×3504 exactly), not a fixed 2×+double that scattered results from 592 to 1011 dpi. `--scale` overrides; 0 turns it off
- [x] A **minimum**, not a target, and always cleared: `sheet_dpi` renders at 1400, so a master under it is plainly upsampled at print — the work the upscaler was run to avoid. The doubled ladder (1/4/9/16) means a 600px master jumps to 5400px, taken deliberately. Every real source width (405-744px) clears 1000 dpi. A *target* with nearest-by-ratio was tried and was worse: every target from 1050-1450 gave byte-identical output, so the setting looked precise and was not
- [x] `config prune` removes keys nothing reads, with their comments — the real library carried 7 from the deleted auto-detector and the deleted grade white-balance, and both surfaces called them ignored without offering to remove them
- [x] **Decided:** the factor ladder stays coarse. Deriving `double` as well as `scale` would give 1/2/3/4/9/16 instead of 1/4/9/16, but `double` is a *quality* knob ("sharper on small sources"), so it stays the user's choice — and `--scale`/`--no-double` are there per run. The cost is a step: a 600px master goes to 5400px to clear 1000 dpi
- [x] **Decided, not fixable mechanically:** a real library's `[print]` still carries prose about the deleted `none|paper|foil` presets, sitting above *live* keys. `config prune` removes dead keys and the comments belonging to them; a stale comment beside a key that still exists is indistinguishable from a good one, so it is a hand edit or nothing

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
- [x] Automated suite for the things eyes can't recheck: the faces model, `solveFit`/`solve_fit` parity (the UI's JS run in node) — in the CI and release gates
- [x] Oversized cards imposed at their own trim, on their own pages, by default in CLI and UI
