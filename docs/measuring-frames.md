# Measuring frame specs

A frame spec is four numbers: how far the printed border reaches in from each cut
edge. proxdex reshapes every card until its border matches those numbers, so they
decide what comes off the printer. Nothing downstream can compensate for them being
wrong, and nothing on screen shows it — a card fitted to a border 0.4mm too thin looks
perfect until it is cut and laid beside a real one.

**Only measured specs ship.** Everything read off a publisher's scan has been removed.
A printing whose spec nobody has measured resolves to **nothing**: `proxdex frames
check` names it, and `border` refuses to run on it until someone measures a spec or
passes `--frame` for the run. Stopping is the right answer — the alternative is
reshaping a card to somebody else's numbers, which looks perfect and is wrong.

## Why a scan cannot settle this

Two independent reasons, the second found the hard way.

**A scan carries its own crop.** If a scan is trimmed 0.3mm inside the real cut edge,
every border read from it is 0.3mm too narrow, every card in the sample agrees with
every other, and nothing in the image says so. No sample size fixes a systematic
error. (Pokémon's scans are visibly worse about this than Scryfall's.)

**The detector that read them was wrong in three ways, so it has been removed.**
`borders.detect_inset` scanned each edge inward and pre-placed the align marks. It is gone
— module, `--auto` flag, `/api/detect` route and all — because every one of these was found
only by comparing it against a hand reading:

1. **A black border under a dark frame.** It wants a *luminance* step, so on a Beta Sol
   Ring (stone frame at 37, black border at 27) it walked on to the light text box and
   reported 37–41px where the border ends at 23px. 65% too far.
2. **A decorated frame's keylines.** On `dft-501` the flat yellow band ends at 50px and it
   answered 56, having crossed a black keyline, a thin yellow line and a second black one.
3. **A card with no border at all.** `sld-912` is full-bleed; its dark art read as a border
   at T4.04 R6.45% and the fit cropped into the picture on all four edges.

Each produces four plausible numbers, and a plausible number presented as a measurement is
worse than none: it looks finished. **So nothing measures the border for you.** The border
step opens showing where the border *should* be — the spec's outline over the untouched
original — and you place the marks yourself, or skip.

A scan survey is still good for one thing: deciding **which populations differ from
each other**, because a bias common to every scan cancels in that comparison. That is
what `scripts/mtg-variants.py` is for, and its groupings are kept below as comparison
targets. Absolute widths come from a person reading one card at a time — which is what the
log below is.

## What ships now

| spec | source | top mm | sides mm | top % | sides % | covers |
|---|---|---|---|---|---|---|
| `pokemon-wotc` | calipers | 3.45 | 3.15 | 3.920 | 5.000 | `base1`–`base6`, `gym1`–`gym2`, `neo1`–`neo4`, and `basep` **inherited** (see below) |
| `pokemon-ecard` | 2 cards, hand px | 3.12 | 3.24 | 3.509 | 5.100 | `ecard1`–`ecard3` — **asymmetric**; bottom 6.76mm, left 7.16mm (see below) |
| `pokemon-ecard-deep-top` | 1 card, hand px | 9.10 | 2.61 | 10.238 | 4.116 | `ecard1`–`ecard3`, a **second** frame — same dot code (bottom 6.76mm, left 7.16mm shared), top 6mm deeper; top/right derived to hold the reading's sums |
| `pokemon-ecard-ex` | 2 cards, hand px | 3.26 | 2.33 / 2.76 | 3.666 | 3.675 / 4.340 | `ex1`–`ex4`, `np` — the dot code moves to the **bottom alone** (6.01mm); sides not collapsed |
| `pokemon-ex-plain` | 1 card, hand px | 2.66 | 2.64 | 2.991 | 4.152 | `ex1`–`ex4`, `np` too, on the cards printed with **no dot code** — square border, the thinnest Pokémon spec |
| `mtg-1993` | 18 sets, median px | 2.74 | 2.47 | 3.077 | 3.893 | frame `1993` — Arabian Nights to 4th Edition and the 1995-96 reprints |
| `mtg-1993-alpha` | `lea-161`/`leb-162`, px | 2.74 | 1.96 | 3.077 | 3.087 | sets `lea`, `leb`, `ced`, `cei` |
| `mtg-1993-unlimited` | `2ed-162`/`3ed-162`, px | 3.63 | 2.98 | 4.087 | 4.698 | sets `2ed`, `3ed` |
| `mtg-1997` | `sld-1664`, px | 3.42 | 3.07 | 3.288 | 4.839 | frame `1997` — Mirage to 7th Edition, **and set `4bb`** |
| `mtg-2003` | `c13-259`, px | 2.99 | 2.98 | 3.365 | 4.698 | frames `2003` **and** `future` |
| `mtg-m15` | `msc-211`, px | 2.56 | 2.56 | 2.885 | 4.032 | frame `2015` — Magic 2015 onward, ⅔ of all Magic |
| `mtg-yellow-band` | `dft-501`, px | 3.76 | 4.27 | 4.231 | 6.720 | `border_color: yellow` — Aetherdrift box toppers |
| `mtg-scheme` | `oarc-1★`, px | 2.98 | 3.00 | 2.349 | 3.365 | `layout: scheme` — **of an 89×127mm card** |
| `mtg-vanguard` | `pvan-101`, px | 5.30 | 4.03 | 4.172 | 4.528 | `layout: vanguard` — **of an 89×127mm card** |
| `borderless` | definition | 0.00 | 0.00 | 0.000 | 0.000 | `border_color: borderless`, art series, `layout: planar` |

Top and bottom are one number, and left and right are another: the cutting error is
cancelled rather than recorded (step 5 below). **The e-Reader specs are the exceptions, and
they are real ones** — Expedition, Aquapolis and Skyridge carry the Nintendo e-Reader
dot-code strip down the *left* edge and along the *bottom*, so those two edges are about
twice the other two and collapsing opposite edges would split the difference on all four.
Its four numbers are top 3.509%, right 5.100%, bottom 7.608%, left 11.269%; the table above
shows the two ordinary edges, which land within a few tenths of a millimetre of WOTC's
border — the same operation, the same border, plus a strip.

`pokemon-ecard-ex` is the other, and it is asymmetric a **different way**: across `ex1`–`ex4`
and `np` the strip runs along the bottom *only* (6.01mm against a 3.26mm top), so three edges
are ordinary. Its left and right are not collapsed either, which is the one place this log
keeps a side difference: both cards read left wider than right by the *same* 0.67pp, and a
difference that reproduces in one direction across a 1.64× scale gap is not a cutting error.

`pokemon-ex-plain` is the counterpart to it and needs no exception at all: those same sets
printed cards with **no dot code**, and those have a square 23px border with nothing to be
asymmetric about. Opposite edges are collapsed there the ordinary way — one number per axis,
because one number per axis was read.

Percentages are the stored form (the inset
as a fraction of the card); millimetres are those percentages of **63.5×88.9mm** — the
2.5×3.5in poker size both games print at, which is *also* what proxdex trims to, so the
millimetres here are the ones that come off the printer. The exceptions are the two
oversized specs, whose percentages are of the 88.9×127mm card they were read from.

That is deliberately the **published spec and not a measured card.** Calipers on real cards
read a little under — one report of 63×87.9mm — and 63×88 is widely quoted as a rounded
metric figure. But a caliper reading is one card off one print run inside a ±0.5mm cutting
tolerance, so the number both publishers state is the stable one to build on.

**Every one of Scryfall's five frame generations resolves.** `1993` is the one that needs
help from the set id as well, because it is three bands a millimetre apart — but its
*ordinary* band is a generation entry covering 18 sets, so nothing in that frame refuses.
`future` shares `mtg-2003` because a card of it *measured* the same, not because it was
assumed to.

What still resolves to nothing: **Pokémon from Diamond & Pearl onward** (2007-05 and
everything after — DP, HeartGold, Black & White, XY, Sun & Moon, Sword & Shield, Scarlet &
Violet), which is now the only real gap left, and any MTG card whose `.traits` were never
recorded (a re-fetch settles that). The e-Card series closed with the readings at the top of
the log below; the whole ex series answers, though only its first five sets were read (see
below).

**`basep` is the one id in the table that was not separately read.** The baseline used to key
Pokémon on set-id *prefixes*, and `"base"` claimed the Wizards Black Star Promos along with
`base1`–`base6`. Set keys are exact now, which forced the question, and it stays on
`pokemon-wotc`: it is the same operation and the same yellow border, and dropping it would
stop a card that borders today. Recorded here as **inherited** rather than measured, because
the alternative is letting an accident pass for a reading. One card of it with calipers would
settle it either way.

The **first five sets of that gap are answered**, and it took two specs, because those sets
printed two borders. `pokemon-ecard-ex` carries the e-Reader dot code along the bottom and
`pokemon-ex-plain` is the plain square border of the cards printed without one; both cover
`ex1` Ruby & Sapphire, `ex2` Sandstorm, `ex3` Dragon, `ex4` Team Magma vs Team Aqua and `np`
— 2003-07 to 2004-03, the run over which the dot code appears and then stops.

Nothing in the metadata says which of the two a given card is, so both resolve as candidates
and a person picks per card — the same shape as the two e-Card frames. `pokemon-ecard-ex` is
what a fit uses with nothing chosen, on weight of evidence (two cards read against one) and
**not** as a claim about which is commoner; nobody counted.

**The rest of the ex series takes `pokemon-ex-plain`, and it is inherited rather than read.**
The strip stops after `ex4`, so from `ex5` Hidden Legends (2005-06) to `ex16` Power Keepers
(2007-05) — and the four Trainer Kits `tk1a`/`tk1b`/`tk2a`/`tk2b`, which are those printings
boxed differently — there is one shape and nothing to pick between: the plain square border,
so `pokemon-ex-plain` is those sixteen sets' *only* candidate. That is the difference from
the five sets above, where two borders genuinely coexist.

The number is still one card of `np` (554×769, 23px all round). Sixteen set ids now rest on
it, none of them separately read, which is the same standing as `basep` above and is recorded
the same way: **inherited, not measured.** The grounds are the same too — same era, same
operation, the same border with the strip left off — and so is the caveat, that it is a
decision and not a reading. One card of `ex10` or so would confirm it or split it, and it is
worth doing precisely because a single 554px file is now carrying an era.

The keys are **exact ids** rather than a prefix, which is what makes the claim reviewable:
`("ex1",)` would have swept all sixteen in silently, and enumerating them is how a later
reader can see exactly what was decided and where the measurement stops. Diamond & Pearl
(2007-05) onward is untouched by this and still resolves to nothing.

## The measurement log

**One row per card measured**, newest first. A **pixel** reading is taken off Scryfall's
own image and inherits whatever crop it has; a **caliper** reading is taken off the
physical card and is what a spec should eventually rest on. Every reading is stored as
the exact pixel fractions rather than converted through millimetres, so nothing rounds —
Scryfall's images are opaque at all four mid-edges (only the rounded corners are
transparent), which makes a pixel count a fraction of the card directly.

The hand reading is the number, always. The **check** column is a second, independent
look at the same image — a dark-run or band-colour scan over 200–240 lines per edge, run
once to see whether it agreed — kept because a row that was corroborated is worth knowing
about. Where the two differ, the hand reading is what ships.

The two **e-Card** rows are checked differently, and for a reason worth writing down. Their
corroboration is *each other*: two cards, read by hand, on images whose widths differ by a
factor of 2.2, agreeing per edge to 0.014pp (left), 0.112 (right), 0.227 (bottom) and 0.262
(top). The left edge was **re-read** after a first pass had it at 42/92px, and the revision
is itself evidence: it tightened the agreement between the two cards from 0.020pp to
0.014pp, and drawn over a finished master the new line lands on the inner edge of the flat
yellow band where the old one sat ~7px past it, inside the frame. That is a stronger statement than a scan of either would be, because a crop shifts
the two opposite edges *against* each other — so a lopsided crop cannot produce the same
lopsided reading twice at two different scales, while a real asymmetric frame does exactly
that. A run-length scan **was** run, over ten other e-Card scans, and is deliberately not
recorded as a check: it read the left edge anywhere from 19px to 61px (3.2% to 10.2%) and
the top from 2.06% to 11.64%, because an e-Card's art runs into the strip and much of it is
yellow. That is the deleted auto-detector's third failure mode — a border found where the
picture is dark, or missed where the picture matches it — reproduced once more, and it is
the argument for hand reading rather than against these numbers.

**A third e-Card reading found a second frame in the same sets**, 468×650: left 52px, right
20px, top 67px, bottom 49px. Left and bottom land on `pokemon-ecard` to 0.158pp and 0.070pp
(0.10mm and 0.06mm) — the dot-code strip is in the same place — and the **top is 6.04mm
deeper**, which is the finding. So `pokemon-ecard-deep-top` **shares** left and bottom with
the existing spec rather than restating them a tenth of a millimetre off, and its top and
right are re-derived to hold this reading's *sums* (17.846% vertically, 15.385%
horizontally) instead of its individual edges: top = 17.846 − 7.608 = 10.238%,
right = 15.385 − 11.269 = 4.116%. Deriving from the sums is the same argument the asymmetry
above rests on — a crop shifts two opposite edges against each other, so a pair's sum
survives a crop that neither edge alone does — and it makes the substitution lossless, since
the design's own height and width as a fraction of the card are exactly what was measured.
`right` was **not** replaced by the existing 5.100%: that is 0.827pp (0.53mm) away, seven
times the agreement the first two cards reached on that edge, and holding the horizontal sum
would then have forced `left` to 10.28%, contradicting the edge that did agree. Which
printing the deeper top belongs to is **not recorded**, because it was not read — both specs
resolve as candidates on every e-Card set and a person picks per card.

**The ex era moved the strip, and that is a shape rather than a renumbering.** Two cards of
`np` (Nintendo Black Star Promos, 40 cards, 2003-10-01), read by hand at 747×1040 and
455×642, put the e-Reader dot code along the **bottom alone**: bottom 6.01mm against a
3.26mm top, 2.76mm left and 2.33mm right. So `pokemon-ecard-ex` is a fourth Pokémon spec
rather than a variant of the e-Card ones — fitting an `np` card to `pokemon-ecard` would ask
for 7.16mm of left border where the card has 2.76, which is 4.4mm of picture cropped away
and invisible on screen, since the overlay is drawn in fractions too.

Its corroboration is the same kind the e-Card rows have — the two cards are each other's
check, agreeing per edge to **0.17pp or better** across a 1.64× scale gap (top 3.750/3.583,
right 3.614/3.736, bottom 6.827/6.698, left 4.284/4.396) — plus the independently stated
totals, which land exactly on three of four: 59px of 747 and 37px of 455 across, 110px of
1040 down. The fourth is a 1px slip in the reading (card 2's vertical total given as 65
where its own 23 + 43 is 66, worth 0.156pp / 0.14mm), so the **edges** are what is stored,
being what was read off the picture. Each stored number is the per-edge average of the two.

**Left and right are kept apart here**, which no other row does: both cards read left wider
than right by the *same* 0.67pp. Collapsing opposite edges is how a cutting error cancels,
and a difference reproducing in one direction at two scales is not a cutting error. Nothing
is recorded about *why* the strip moved, or about which later ex sets share this geometry —
neither was read.

**And the same set holds a card with no strip at all**, 554×769 with **23px on every edge**.
That is `pokemon-ex-plain`, the plainest spec in the file and the thinnest Pokémon one:
2.64mm sides, 2.66mm top and bottom, nearer `mtg-m15` than WOTC's 3.45/3.15. The mm figures
being equal is a separate fact from the pixels being equal and worth stating — 2.991% and
4.152% are different fractions, and they only land on one width once each is taken of the
axis it belongs to. The 0.02mm between them is this file's aspect sitting 0.85% wide of the
card's (554/769 = 0.7204 against 0.7143), which is what a genuinely square border read off
this image looks like. Opposite edges are collapsed the ordinary way, since one number per
axis is what was read; the fractions are of its own file, not rounded through a millimetre.

| card | frame · border · effects | read | image px | top/bot px | sides px | mm t/sides | check | spec |
|---|---|---|---|---|---|---|---|---|
| `np`, card 3 | ex era · yellow · **no dot code** | **hand** | 554 × 769 | **23 / 23** | **23 / 23** | 2.66 / 2.64 | square in mm as well as px — the 0.02mm is this file's aspect being 0.85% wide of the card's | **`pokemon-ex-plain`** |
| `np`, card 2 | ex era · yellow · dot code **bottom only** | **hand** | 455 × 642 | **23 / 43** | **20 / 17** | 3.18 / 2.79 · 2.37 (t/l·r) | agrees with card 1 to **0.12–0.17pp** per edge; stated totals 37 across ✓, 65 down (edges sum 66) | **`pokemon-ecard-ex`** |
| `np`, card 1 | ex era · yellow · dot code **bottom only** | **hand** | 747 × 1040 | **39 / 71** | **32 / 27** | 3.33 / 2.72 · 2.30 (t/l·r) | stated totals 59 across ✓, 110 down ✓ | **`pokemon-ecard-ex`** |
| e-Card, card 3 | e-Card · yellow · dot code · **deep top** | **hand** | 468 × 650 | **67 / 49** | **52 / 20** | 9.10 / 2.61 (t/r) | left & bottom agree with cards 1-2 to **0.16pp / 0.07pp**; top **+6.04mm** | **`pokemon-ecard-deep-top`** |
| e-Card, card 2 | e-Card · yellow · dot code | **hand** | 737 × 1036 | **35 / 80** | **83 / 38** | 3.12 / 3.24 (t/r) | see note | **`pokemon-ecard`** |
| e-Card, card 1 | e-Card · yellow · dot code | **hand** | 337 × 467 | **17 / 35** | **38 / 17** | 3.12 / 3.24 (t/r) | agrees with card 2 to **0.01–0.27pp** per edge | **`pokemon-ecard`** |
| [Sol Ring `c13-259`](https://scryfall.com/card/c13/259/sol-ring) | `2003` · black · – | pixel | 745 × 1040 | **35** | **35** | 2.99 / 2.98 | scan reads 35 all round | **`mtg-2003`** |
| [Sol Ring `msc-211`](https://scryfall.com/card/msc/211/sol-ring) | `2015` · black · – | pixel | 744 × 1040 | **30** | **30** | 2.56 / 2.56 | scan reads top 30 at every percentile | **`mtg-m15`** |
| [Bleachbone Verge `dft-501`](https://scryfall.com/card/dft/501/bleachbone-verge) | `2015` · **yellow** · inverted | pixel | 744 × 1040 | **44** | **50** | 3.76 / 4.27 | band colour reaches 44 / 50 at every percentile | **`mtg-yellow-band`** |
| [Sol Ring `mb2-233`](https://scryfall.com/card/mb2/233/sol-ring) | `future` · black · – | pixel | 744 × 1040 | **35** | **35** | 2.99 / 2.98 | scan 35 / 36 / 35 | `mtg-2003` — identical, so shared |
| [Sol Ring `me4-227`](https://scryfall.com/card/me4/227/sol-ring) | `1997` · black · – | pixel | 745 × 1040 | **41** | **36** | 3.50 / 3.07 | scan top 40, sides 37–40 | **`mtg-1997`** |
| Lightning Bolt `4bb-208` | 1993 · black · FBB | pixel | 745 × 1040 | **40** | **36** | 3.42 / 3.07 | scan 41 top / 38-39 sides | **→ `mtg-1997`** — fits no 1993 band |
| Lightning Bolt `fbb-162` | 1993 · black · FBB | pixel | 745 × 1040 | **30** | **27** | 2.56 / 2.30 | scan 29 / 28-29 | `mtg-1993` — band 2, its narrow end |
| Lightning Bolt `sum-162` | 1993 · white · Summer | pixel | 745 × 1040 | **32** | **29** | 2.74 / 2.47 | scan 32-33 / 27-29 | `mtg-1993` — band 2, on the median |
| Benalish Hero `ced-4` | 1993 · black · CE | pixel | 745 × 1040 | **32** | **23** | 2.74 / 1.96 | scan 32-36 / 24 | `mtg-1993-alpha` — **confirms band 1** |
| Benalish Hero `cei-4` | 1993 · black · IE | pixel | 745 × 1040 | **32** | **23** | 2.74 / 1.96 | none | `mtg-1993-alpha` |
| Adarkar Unicorn `ice-1` | 1993 · black | pixel | 745 × 1040 | **32** | **29** | 2.74 / 2.47 | none | `mtg-1993` |
| Active Volcano `leg-130` | 1993 · black | pixel | 745 × 1040 | **31** | **29** | 2.65 / 2.47 | none | `mtg-1993` |
| Arenson's Aura `ptc-shr3sb` | 1993 · **gold** | pixel | 745 × 1040 | **32** | **29** | 2.74 / 2.47 | none | `mtg-1993` — gold is not geometry either |
| Aesthir Glider `all-116a` | 1993 · black | pixel | 745 × 1040 | **32** | **28** | 2.74 / 2.39 | none | `mtg-1993` |
| Armor Thrull `fem-33a` | 1993 · black | pixel | 745 × 1040 | **32** | **28** | 2.74 / 2.39 | none | `mtg-1993` |
| Abbey Matron `hml-2a` | 1993 · black | pixel | 745 × 1040 | **32** | **28** | 2.74 / 2.39 | none | `mtg-1993` |
| Active Volcano `chr-43` | 1993 · white | pixel | 745 × 1040 | **32** | **30** | 2.74 / 2.56 | none | `mtg-1993` |
| Active Volcano `bchr-43` | 1993 · black | pixel | 745 × 1040 | **36** | **30** | 3.08 / 2.56 | scan 36 / 32-33 | `mtg-1993` — same set as `chr`, 4px of scan scatter |
| Alabaster Potion `ren-2` | 1993 · black | pixel | 745 × 1040 | **30** | **30** | 2.56 / 2.56 | scan 30 / 28-29 | `mtg-1993` |
| Ashes to Ashes `drk-39` | 1993 · black | pixel | 745 × 1040 | **32** | **28** | 2.74 / 2.39 | none | `mtg-1993` |
| Amulet of Kroog `atq-36` | 1993 · black | pixel | 745 × 1040 | **32** | **28** | 2.74 / 2.39 | none | `mtg-1993` |
| Army of Allah `arn-2` | 1993 · black | pixel | 745 × 1040 | **32** | **28** | 2.74 / 2.39 | none | `mtg-1993` |
| Amulet of Kroog `rin-99` | 1993 · black | pixel | 745 × 1040 | **30** | **28** | 2.56 / 2.39 | none | `mtg-1993` |
| Alabaster Potion `itp-1` | 1993 · white | pixel | 745 × 1040 | **32** | **32** | 2.74 / 2.73 | scan 32 / 32 | `mtg-1993` — band 2, its wide end |
| Alabaster Potion `rqs-1` | 1993 · white | pixel | 745 × 1040 | **32** | **30** | 2.74 / 2.56 | none | `mtg-1993` |
| [Lightning Bolt `lea-161`](https://scryfall.com/card/lea/161/lightning-bolt) | `1993` · black · – | pixel | 745 × 1040 | **32** | **23** | 2.74 / 1.96 | run-length q1 33 / 24 (dark art inflates the median) | **`mtg-1993`** |
| [Lightning Bolt `leb-162`](https://scryfall.com/card/leb/162/lightning-bolt) | `1993` · black · – | pixel | 745 × 1040 | **32** | **23** | 2.74 / 1.96 | q1 38 / 25, same artifact | `mtg-1993` |
| [Sol Ring `leb-270`](https://scryfall.com/card/leb/270/sol-ring) | `1993` · black · – | pixel | 745 × 1040 | **32** | **23** | 2.74 / 1.96 | agrees with Bolt exactly; supersedes a 672 × 936 reading | `mtg-1993` |
| [Lightning Bolt `2ed-162`](https://scryfall.com/card/2ed/162/lightning-bolt) | `1993` · **white** · – | pixel | 745 × 1040 | **42.5** | **35** | 3.63 / 2.98 | scan 42 / 35 at every quartile | **`mtg-1993-unlimited`** |
| [Sol Ring `2ed-270`](https://scryfall.com/card/2ed/270/sol-ring) | `1993` · white · – | pixel | 745 × 1040 | **42.5** | **35** | 3.63 / 2.98 | agrees with Bolt exactly | `mtg-1993-unlimited` |
| [Lightning Bolt `3ed-162`](https://scryfall.com/card/3ed/162/lightning-bolt) | `1993` · white · – | pixel | 745 × 1040 | **42.5** | **35** | 3.63 / 2.98 | scan 42 / 35 at every quartile | `mtg-1993-unlimited` |
| [Sol Ring `3ed-274`](https://scryfall.com/card/3ed/274/sol-ring) | `1993` · white · – | pixel | 745 × 1040 | **42.5** | **35** | 3.63 / 2.98 | agrees with Bolt exactly | `mtg-1993-unlimited` |
| [Lightning Bolt `4ed-208`](https://scryfall.com/card/4ed/208/lightning-bolt) | `1993` · white · – | pixel | 745 × 1040 | **33** | **30** | 2.82 / 2.56 | scan 33 / 30 at every quartile | **`mtg-1993-4ed`** |
| [All in Good Time `oarc-1★`](https://scryfall.com/card/oarc/1%E2%98%85/all-in-good-time) | `2003` · black · – | pixel | **1040 × 1490** | **35** | **35** | 2.98 / 3.00 of 89×127 | none | **`mtg-scheme`** |
| [Ertai `pvan-101`](https://scryfall.com/card/pvan/101/ertai) | `1993` · black · – | pixel | **1060 × 1510** | **63** | **48** | 5.30 / 4.03 of 89×127 | none | **`mtg-vanguard`** |
| [Academy at Tolaria West `ohop-1`](https://scryfall.com/card/ohop/1/academy-at-tolaria-west) | `2003` · black · – | pixel | 1040 × 1490 | uneven | uneven | — | unreadable: art runs to the edges | `borderless` |
| [Academy at Tolaria West `opca-9`](https://scryfall.com/card/opca/9/academy-at-tolaria-west) | `2015` · black · – | pixel | 1040 × 1490 | uneven | uneven | — | same | `borderless` |
| [Soldier `tmsh-3`](https://scryfall.com/card/tmsh/3/soldier) | `2015` · black · token | pixel | 744 × 1040 | **30** | **30** | 2.56 / 2.56 | scan 29–30 on T/R/L | `mtg-m15` — **no token spec needed** |
| [Chandra Emblem `tdft-13`](https://scryfall.com/card/tdft/13/chandra-spark-hunter-emblem) | `2015` · black · emblem | pixel | 744 × 1040 | **30** | **30** | 2.56 / 2.56 | scan 30 / 30 | `mtg-m15` |
| [Demon `p03-6`](https://scryfall.com/card/p03/6/demon) | `2003` · black · token | pixel | 745 × 1040 | **35** | **35** | 2.99 / 2.98 | none | `mtg-2003` — matches to the pixel |
| [Marit Lage `pcsp-1`](https://scryfall.com/card/pcsp/1/marit-lage) | `2003` · black · token | pixel | 745 × 1040 | **35** | **35** | 2.99 / 2.98 | none | `mtg-2003` |
| [Punchcard `tsos-14`](https://scryfall.com/card/tsos/14/punchcard-punchcard) | `2015` · black · dft | pixel | 745 × 1040 | **0** | **0** | 0 / 0 | no border at all | `borderless` |
| [Rampant Growth `8ed-274`](https://scryfall.com/card/8ed/274/rampant-growth) | `2003` · **white** · – | pixel | 745 × 1040 | **35** | **35** | 2.99 / 2.98 | scan 35 all round | `mtg-2003` — colour is not geometry |
| [Sol Ring `sld-1664`](https://scryfall.com/card/sld/1664/sol-ring) | `1997` · black · – | pixel | 744 × 1040 | **40** | **36** | 3.42 / 3.07 | scan T41 R36 B42 L35 | `mtg-1997` — a retro frame in a 2021 set |
| [Arcane Signet `p30m-1F★`](https://scryfall.com/card/p30m/1F%E2%98%85/arcane-signet) | `2015` · black · inverted,**etched** | pixel | 745 × 1040 | **29** | **29** | 2.48 / 2.47 | scan 29 all round | `mtg-m15` |
| [Sol Ring `cmr-700`](https://scryfall.com/card/cmr/700/sol-ring) | `2015` · black · **extendedart** | pixel | 745 × 1040 | **28** | **28** | 2.39 / 2.39 | dark run ≥27–28px on both sides over 240 rows | `mtg-m15` — **overturns "sides = 0"** |
| [Water Gun Balloon Game `und-85`](https://scryfall.com/card/und/85/water-gun-balloon-game) | `2015` · **silver** · – | pixel | 745 × 1040 | **28** | **28** | 2.39 / 2.39 | none — a colour scan cannot read a silver border | `mtg-m15` |
| [Swords to Plowshares `wc97-jk54`](https://scryfall.com/card/wc97/jk54/swords-to-plowshares) | `1997` · **gold** · – | pixel | 745 × 1040 | **40** | **38** | 3.42 / 3.24 | none — same reason | `mtg-1997` |
| [Urborg `tsr-287`](https://scryfall.com/card/tsr/287/urborg-tomb-of-yawgmoth) | `2015` · black · **legendary** | pixel | 745 × 1040 | **28** | **28** | 2.39 / 2.39 | sides 28 confirmed by scan | `mtg-m15` |
| [Evolving Wilds `afr-353`](https://scryfall.com/card/afr/353/evolving-wilds) | `2015` · black · **showcase** | pixel | 745 × 1040 | **0** | **0** | 0 / 0 | art at pixel 0 on T/R/L, all 200 lines | none — **metadata says black** |
| [Sol Ring `sld-912`](https://scryfall.com/card/sld/912/sol-ring) | `2015` · black · inverted, full-art | pixel | 745 × 1040 | **0** | **0** | 0 / 0 | art at pixel 0 on T/R/L | none — **metadata says black** |
| [Sol Ring `sld-2807`](https://scryfall.com/card/sld/2807/sol-ring) | `2015` · **borderless** · – | pixel | 745 × 1040 | **0** | **0** | 0 / 0 | no border; `border_color` already says so | `borderless` |
| [Aang and Katara `atle-8`](https://scryfall.com/card/atle/8/aang-and-katara-aang-and-katara) | `2015` · **borderless** · art series | pixel | 745 × 1040 | **0** | **0** | 0 / 0 | no border | `borderless` |
| a Base Set common | Pokémon WOTC | caliper | — | 3.3 / 3.6 mm | 3.1–3.2 mm | 3.45 / 3.15 | — | **`pokemon-wotc`** |

Millimetres are of a 63.5 × 88.9mm card for the MTG rows and of 63 × 88 for the Pokémon
one (that spec predates recording the reference card and its calipers were in mm anyway).
Rows in **bold** in the spec column are the card each spec was actually taken from; the
rest are the corroboration, and they are worth as much — five of them are what says a
treatment needs no spec of its own.

### Notes per row

**`c13-259` → `mtg-2003`, the best-corroborated spec here.** Three independent things
agree on 35px: the hand reading, a dark-run scan of the same file on all four edges, and a
*white*-bordered card of the same generation read by hand (`8ed-274`) — which is the
cleanest demonstration in this whole document that border **colour is not border
geometry**. Both scan surveys said 2.92–3.00mm too. It is also the generation whose
redesign made all four edges equal, and the readings show exactly that.

**`mb2-233` → shares `mtg-2003`.** The `future` frame measures at 35px, the same as 2003,
so one spec covers both generations. That is a *measurement*, not an assumption, and it is
recorded as an alias rather than a duplicated spec so there is one number to correct. If
calipers ever split them, `mtg-future` is one new entry.

**`msc-211` → `mtg-m15`, and the 0.17mm band.** A plain card with no treatment, which is
what the spec should describe. Four *treated* cards of the same generation read 28–29px
against its 30: extended art 28, etched 29, silver 28, legendary sides 28. So the whole
generation sits inside 0.17mm, no treatment needs a spec of its own, and the 1–2px scatter
is most likely each scan's own crop — the error a survey cannot remove. Calipers on any
post-2014 card would settle it; the spread is smaller than the two questions below.

**`cmr-700` → `mtg-m15`, and this overturns a documented claim.** The scan survey reported
extended art's sides at **0** — "the art runs off the card" — and that was the old
detector failing on dark art, not the card. Measured over 240 rows, the black border is 27–28px on
both sides against a plain card's 29–30, and it never bleeds (fewer than 2% of rows).
Extended art is the same border with a wider picture, so it needs no spec, and
`sources.mtg_frame` no longer has a hole where one was planned.

**`dft-501` → `mtg-yellow-band`.** The flat yellow band ends at 50px on the sides and 44px
top, confirmed at every percentile by measuring how far the band's own colour reaches. The
old detector read 56 and 50 instead, because just inside the flat band sit a **black
keyline, a thin yellow line and a second black keyline** — one of the three failures that
retired it. The flat band is the border; the keylines are decoration on the frame.

**`tsr-287` → `mtg-m15`.** 28px on every edge, so a legendary crown is its generation's
border with a crown drawn inside it — which is what the survey concluded from the sides
alone, now confirmed on the top as well. A dark-run scan of the top wanders between 18 and
34px across the width, but that is the crown's own filigree being dark, not the border
moving: the crown is a decorative silhouette and a scan has no way to tell it from the
margin above it. One more reason nothing measures a border here.

**`afr-353` and `sld-912` — physically borderless, and Scryfall says `black`.** Both have
art at pixel 0 on the top, left and right across all 200 sampled lines. Neither can be
identified from metadata: `border_color` says `black`, `afr-353` is not even marked
`full_art`, and neither `full_art` nor `showcase` implies borderless in general (a
full-art Zendikar land has a normal black border, and most showcase cards do too). So
there is no rule to write, and two escape hatches instead:

Say it explicitly instead. A frameless spec has no marks to place, so it fills them in
itself and the flag is the whole fix — verified on both cards:

```bash
proxdex border afr-353 --frame borderless
#   ⌖ afr-353: Borderless (no printed frame) — no border to align, reshaping to
#     the card aspect only
#   ✓ afr-353: fit → 745×1040px  T0.00 R0.00 B0.00 L0.00%
```

In the web UI, pick `borderless` in the Frame spec dropdown — the panel then says there is
nothing to align and Run is live. To make the decision outlive the run, **pin** it
(`proxdex frames pin`, or Pin this spec on the align panel). A pin is the only reliable
answer for a printing whose metadata is wrong about its own border.

**`me4-227` → `mtg-1997`.** An independent colour scan of the same file agrees: top 40px,
sides 37–40px. It reads the bottom at 42–51px, but that is the copyright line inside the
border rather than the border, so the hand reading's symmetric 41px is the one kept —
which is what step 5 below asks for anyway. Masters Edition IV was an MTGO-only set, so
this image is a render of the frame template rather than a scan of paper: no crop error
by construction, but also no guarantee the paper printing matched the template. A caliper
reading of a 5th/6th/7th Edition card is still worth taking.

The sides land 1.04mm wider than `mtg-1993`'s, and both scan surveys below put 1997's
sides at 3.04–3.05mm — so this reading agrees with them and it is the 1993 number that is
the odd one out. See the next row.

**`lea-161` / `leb-162` / `2ed-162` / `3ed-162` / `4ed-208` → the 1993 frame, split three
ways. Settled.** This was the project's longest-running open question and the answer is that
the ~1mm gap is real geometry, not a reading artifact.

The reason it is now settled rather than suspected is the **control card**: Lightning Bolt is
printed in all five of those sets, so the art is identical and nothing but the border can
differ. The earlier colour survey had reached the same three groupings, and Sol Ring agrees
with Bolt exactly in every printing where both exist:

| printing | top/bot px | sides px | top mm | sides mm | spec |
|---|---|---|---|---|---|
| [Alpha `lea-161`](https://scryfall.com/card/lea/161/lightning-bolt) | 32 | **23** | 2.74 | 1.96 | `mtg-1993` |
| [Beta `leb-162`](https://scryfall.com/card/leb/162/lightning-bolt) | 32 | **23** | 2.74 | 1.96 | `mtg-1993` |
| [Unlimited `2ed-162`](https://scryfall.com/card/2ed/162/lightning-bolt) | 42.5 | **35** | 3.63 | 2.98 | `mtg-1993-unlimited` |
| [Revised `3ed-162`](https://scryfall.com/card/3ed/162/lightning-bolt) | 42.5 | **35** | 3.63 | 2.98 | `mtg-1993-unlimited` |
| [4th Edition `4ed-208`](https://scryfall.com/card/4ed/208/lightning-bolt) | 33 | **30** | 2.82 | 2.56 | `mtg-1993-4ed` |

(px of the 745×1040 PNG; mm of 63.5×88.9.) Three geometries, 5–7px apart on the sides — far
past any reading noise.

**Two things confirm it independently.** A run-length scan reproduces the white-bordered trio
*to the pixel* — 2ed 42/35, 3ed 42/35, 4ed 33/30 at every quartile. On the black-bordered
pair the same scan's lowest quartile lands on the hand reading (33/24 against 32/23) while its
median runs deep, which is exactly the dark-frame-under-black-border failure that removed the
auto-detector: there is nothing wrong with the card, only with reading a black border by
luminance. And the earlier scale check still holds — Sol Ring's art box occupies pixels 90–656
in the Beta scan and 91–655 in the Unlimited one, so the two are at the same scale and the
border difference cannot be a crop.

**Colour is still not geometry, and this is the sharpest proof of it.** Revised and 4th
Edition are *both* white-bordered `1993`-frame cards, and they differ by 5px on the sides.
So the split is keyed by **set**, never by `border_color` — the 2003 pair (`c13-259` black and
`8ed-274` white, both exactly 35px) already showed colour does not bias the reading, and this
shows two same-coloured printings of one frame can still differ.

**What this leaves open.** 21 of the ~26 sets reporting `frame: 1993` have never been read
(`arn`, `atq`, `leg`, `drk`, `fem`, `ice`, `hml`, `all`, `chr`, `ren`, `sum`, `fbb`, `4bb`,
`ced`, `cei`, …). The three measured answers are 1mm apart, so handing one of them over would
be a coin flip with the largest error in the project — they resolve to **nothing** and
`border` refuses them. Several are variants of measured sets (`fbb` is Revised with a black
border, `4bb` is 4th, `sum` is a Revised print run), so a set-level assignment is a reasonable
thing for a *library* to add; it is not a thing to ship as a guess.

The old survey's Legends (24–25px) and Ice Age (27–28px) readings sit between Alpha and
Revised, which suggests the expansions are their own values again rather than matching either
end. One card each would settle it.

## What to measure next

**Every MTG frame generation now has a spec**, so nothing is blocked. What is left is a
short list of **comparisons**, and comparisons are what a pixel reading is actually good
at.

**Calipers are not on this list, deliberately.** They were only ever needed for one thing —
whether every spec here is uniformly a hair narrow, because each is read off an image that
carries its own crop. That error is *shared*, so it lands on every card equally, and it is
the one thing no reading of any image can detect. Every question below is instead a
question of one printing *against another*, where a common crop cancels: is Alpha's border
the same as Unlimited's, is an oversized card's border the same fraction, does a token match
its generation. Those a pixel count settles outright. So the absolute widths stay as read
and the open questions get answered now, rather than waiting on a purchase.

### The worksheet

Read every edge **at the mid-point**, in pixels, and record the file's own `w × h` beside
it — the divisor is that file, never a nominal size. Front face only.

### The 1993 frame, settled over 26 sets

Every set reporting `frame: 1993` has now been read by hand — 26 of them. **They collapse
into three bands, not 26 numbers**, which is why the code carries three rules and no table
of sets. A card sitting a pixel or two off its band is reading noise; one pixel is 0.085mm.

| band | sides | top/bot | mm | sets | spec |
|---|---|---|---|---|---|
| narrow | **23** | 32 | 1.96 / 2.74 | `lea`, `leb`, `ced`, `cei` | `mtg-1993-alpha` |
| ordinary | **29** | 32 | 2.47 / 2.74 | 18 sets: `arn` `atq` `leg` `drk` `fem` `ice` `hml` `all` `chr` `bchr` `ren` `rin` `sum` `fbb` `itp` `rqs` `ptc` `4ed` | `mtg-1993` |
| wide | **35** | 42.5 | 2.98 / 3.63 | `2ed`, `3ed` | `mtg-1993-unlimited` |
| — | 36 | 40 | 3.07 / 3.42 | `4bb` alone | → `mtg-1997` |

**The bands are separated by far more than the noise inside them.** The ordinary band spans
27–32px on the sides (0.43mm) with a median of 29; band 1 sits 6px below its narrowest member
and band 3 sits 3px above its widest, with a top border 6.5px clear of anything else in the
frame. So the split is not a matter of taste at the boundaries.

**Three results worth naming.**

*The Collectors' Editions confirm band 1 independently.* `ced` and `cei` read 23/32 — Alpha and
Beta to the pixel — and nothing about them was used to arrive at that number. They are
Beta-derived printings, so that is exactly what should happen.

*Band 2 absorbed what was briefly `mtg-1993-4ed`.* 4th Edition reads 30/33 against the band's
29/32 median: one pixel per edge is not a spec. Eighteen sets inside 0.43mm is a
**measurement** of a generation-wide border, which is what let `frame: 1993` have a generation
entry again — before these readings, only Alpha and Revised were known and any generation-wide
number would have been a coin flip across a 1mm spread, so 3,080 prints (3.2% of Magic)
resolved to nothing. **That gap is now closed.**

*`4bb` is the one printing that fits no band* — 36/40, which is the **1997** frame's numbers
exactly, so it is pointed there rather than given a spec of its own (the same basis on which
`future` shares `mtg-2003`). A run-length scan reads it wider still (38–39 sides, 41 top), so
it is not a misreading. The Foreign Black Border sets were printed in Belgium, a separate
print run, which makes a genuinely different border plausible — but its sibling `fbb` went the
*other* way and sits in band 2 at 27/30, so there is no "foreign" rule to write. Two facts,
not a pattern.

### What did not merge, and why

With one pixel at 0.085mm it is worth stating which near-misses are real distinctions:

| pair | how close | verdict |
|---|---|---|
| `mtg-1993` (29/32) vs `mtg-m15` (30/30) | sides within 1px | **separate** — M15's top equals its sides, the 1993 frame's top exceeds them by a consistent 3px. That is the border's *shape*, not its size |
| `mtg-1993-unlimited` (35/42.5) vs `mtg-2003` (35/35) | **sides identical** | **separate** — the top differs by 7.5px (0.64mm) |
| `mtg-1997` (36/40) vs `mtg-2003` (35/35) | sides within 1px | **separate** — the top differs by 5px (0.43mm); the 2003 redesign is what made all four edges equal |
| the seven `2015` readings (28–30) | inside 2px | **one spec** — plain, etched, silver, extended-art, legendary-crown and two tokens all sit in a 0.17mm band |
| the five `2003` readings (35/35) | identical | **one spec** — black, white, `future` frame and two tokens |

---

**All three questions on this worksheet have been answered** — the readings are in the log
above and the specs are shipped. What they settled:

**1. The 1993 split: real, and three-way.** Lightning Bolt in all five printings, so the art
is identical and nothing but the border can differ, with Sol Ring agreeing exactly wherever
both exist:

| printing | top/bot px | sides px | mm | spec |
|---|---|---|---|---|
| Alpha `lea-161`, Beta `leb-162` | 32 | **23** | 2.74 / 1.96 | `mtg-1993` |
| Unlimited `2ed-162`, Revised `3ed-162` | 42.5 | **35** | 3.63 / 2.98 | `mtg-1993-unlimited` |
| 4th Edition `4ed-208` | 33 | **30** | 2.82 / 2.56 | `mtg-1993-4ed` |

A full millimetre across, on cards Scryfall calls one frame — so they are keyed by **set**
rather than by generation, which is what the unified `frames.BASELINE` table is for. An
independent run-length scan reproduced the white-bordered trio to the pixel (2ed 42/35, 3ed
42/35, 4ed 33/30); on the black-bordered pair its lowest quartile matched (33/24) while its
median ran deep, which is precisely the dark-frame-under-black-border artifact that killed
the auto-detector. And **Revised and 4th Edition are both white-bordered `1993`-frame cards
that differ by 5px**, which is the cleanest demonstration in this document that a border
colour says nothing about a border width.

Note `leb-270` now serves at 745 × 1040; the shipped spec had been read at 672 × 936, so it
was the one spec on a different divisor from every other. That is fixed.

**2. Oversized: its own spec, even where the border is the same width.** These files come at
their own size, so a pixel count is the fraction of the oversized card directly.

| card | layout | file | reading | spec |
|---|---|---|---|---|
| [`oarc-1★`](https://scryfall.com/card/oarc/1%E2%98%85/all-in-good-time) | scheme | 1040 × 1490 | 35 all round → 2.98 / 3.00mm | `mtg-scheme` |
| [`pvan-101`](https://scryfall.com/card/pvan/101/ertai) | vanguard | **1060 × 1510** | 63 / 48 → 5.30 / 4.03mm | `mtg-vanguard` |
| [`ohop-1`](https://scryfall.com/card/ohop/1/academy-at-tolaria-west), [`opca-9`](https://scryfall.com/card/opca/9/academy-at-tolaria-west) | planar | 1040 × 1490 | uneven, art to the edges | `borderless` |

A scheme's border is **2.98 / 3.00mm — physically identical to an ordinary 2003-frame
card's 2.99 / 2.98.** But a spec is a *fraction*, and the card is 89 × 127mm, so the same
millimetres are 2.35% / 3.37% here against `mtg-2003`'s 3.37% / 4.70%. Resolving a scheme to
its frame generation, which is what happened before these existed, asked for **4.27 /
4.18mm — 1.2mm too wide on every edge**, and looked right on screen because the overlay is
drawn in fractions too. Vanguard is a third size again and genuinely thicker; it reports
`frame: 1993`, which would otherwise have handed it Alpha's 1.96mm sides. Both are read from
the **layout** (`sources.mtg_frame`), like the yellow band, because the layout settles the
geometry.

Planes could not be read — the art runs to the edges and what border there is comes out
uneven — so they are treated as `borderless`, which at least stops a 63.5mm card's fraction
being applied to an 89mm one. `--frame` overrides either way.

**3. Tokens: no spec needed, and now that is measured rather than assumed.** A token's layout
is bespoke (no mana cost, larger art), so "same stock, same die" was not enough to take on
trust.

| card | kind | reading | matches |
|---|---|---|---|
| [`tmsh-3`](https://scryfall.com/card/tmsh/3/soldier) | token, M15 frame | 30 all round | `mtg-m15` (30/744) exactly |
| [`tdft-13`](https://scryfall.com/card/tdft/13/chandra-spark-hunter-emblem) | emblem | 30 all round | `mtg-m15` |
| [`p03-6`](https://scryfall.com/card/p03/6/demon), [`pcsp-1`](https://scryfall.com/card/pcsp/1/marit-lage) | token, 2003 frame | 35 all round | `mtg-2003` (35/745) exactly |
| [`tsos-14`](https://scryfall.com/card/tsos/14/punchcard-punchcard) | double-faced token | no border at all | `borderless`, from its layout |

Every one lands on its generation's number to the pixel, so **nothing was added** — a token
spec would have been a duplicate. Real game tokens are almost entirely `frame:2015` (2888
prints against 298 for 2003); the 1993 and 1997 "tokens" are World Championship blank and
bio cards, not game pieces.

And Pokémon, which is now the biggest gap by a distance — past `neo4` only the three e-Card
sets and `np` resolve:

| spec to create | buy any common from | covers |
|---|---|---|
| `pokemon-ex` | an EX-era set other than `np` | 2003–2007 |
| `pokemon-dp` | a Diamond & Pearl / HGSS set | 2007–2011 |
| `pokemon-swsh` | a Sword & Shield set | 2020–2022 |
| `pokemon-sv` | a Scarlet & Violet set | current |

The ex-era row is the narrowest of these now: `pokemon-ecard-ex` and `pokemon-ex-plain`
describe `np` between them, and the strip ran on into the series, so one card of `ex1` or
`pop1` would say whether that
geometry extends or whether the era holds a second one — which is the cheapest reading left
on this list.

Two exclusions that matter, because measuring the wrong card gives a wrong answer that
looks fine: **not oversized** (a Planechase or Vanguard card is 89×127mm, so its
border as a fraction of a normal card is meaningless) and **not a token** (own frames).

## How to measure

Digital calipers, millimetres, two decimals.

1. **Measure the card itself first** — width and height. It should come out near
   **63.5 × 88.9mm**, *not* the 63×88 proxdex trims to. The spec is stored as a
   fraction, so dividing by the wrong card size puts 0.8% of error into every border.
   Record what you actually measured and pass it as `--card-w` / `--card-h`.
2. **For each edge, measure from the cut edge to the inner edge of the printed
   border** — where the black (or yellow, or white) stops and the frame or art begins.
3. **Measure at the middle of the edge, never near a corner.** Corners are rounded, so
   the border appears to widen there and you will read 0.3–0.5mm too much.
4. **Three readings per edge, keep the median.** Calipers on cardstock are easy to read
   0.1mm off by pressing.
5. **Cancel the cutting error.** Cards are cut with real tolerance — the picture is
   often 0.2–0.4mm off centre, so left and right genuinely differ *on that card* while
   their **sum** stays constant. Take `(left + right) / 2` for both sides and
   `(top + bottom) / 2` for top and bottom. That is the spec: the design, not one
   card's trim. Where a generation's top really is thicker than its sides, averaging
   top with bottom is still right; averaging top with the *sides* is not.
6. **Record it:**

   ```bash
   proxdex frames set mtg-m15 --game mtg \
     --top 2.42 --right 2.44 --bottom 2.42 --left 2.44
   ```

   Millimetres of a real card — 63.5×88.9mm, which is what both games print on and
   what `frames.CARD_MM` says. Add `--oversized` for a plane, scheme or Vanguard card
   (88.9×127mm), since the same border is a smaller *fraction* of a bigger card. Or the
   **Specs** tab of `proxdex ui` → Edit.

7. **Write the row down here**, in the table below. A spec is four numbers and
   nothing else — it carries no provenance field, deliberately, because a field that
   ranks a reading is the confidence grade this document exists to replace. So *this
   file* is the record: which card, which method, what it decided.

### The fill-in sheet

```
card measured:  ____________________   (set-number, e.g. m15-284)
frame:          ____________________   (Scryfall's `frame` — 1993/1997/2003/2015/future)
foil?           ____________________

card width  (mm): ______   card height (mm): ______

border, median of 3, middle of each edge:
  top    ______   right  ______   bottom ______   left  ______
```

## Grouped scan estimates — comparison targets only

**These are withdrawn from the code.** They are the scan survey's own numbers, kept
here so a caliper reading has something to be checked against. A reading more than
~0.3mm off one of these is worth a second look — in either direction, since these
carry both the crop error and, on black-bordered rows, the detector's over-read.

Per generation, plain black-bordered, no treatments, sampled proportionally over every
printing that carries the frame (15 cards each):

| frame | top | sides | bottom | note |
|---|---|---|---|---|
| `1993` | 3.15 | 2.79 | 3.41 | **now known to be three specs** — Alpha/Beta 1.96, 4th 2.56, Unlimited/Revised 2.98 on the sides. The survey's single 2.79 is a mean over all three, which is why it matched none of them |
| `1997` | 3.37 | 3.04 | 3.38 | top and bottom thicker than the sides |
| `2003` | 2.96 | 2.92 | 2.94 | the redesign made all four edges equal |
| `future` | 2.94 | 2.95 | 2.99 | within 0.05mm of 2003 |
| `2015` | 2.48 | 2.45 | 2.88 | bottom unreadable — the collector strip sits in it |

A second, independent reading of the same generations off **one white-bordered core
set each** (support 1.00 on every edge, n=10), which is the method least affected by
the detector bug:

| frame | read off | top | sides | bottom |
|---|---|---|---|---|
| `1993` | 2ed | 3.55 | 2.96 | 3.55 |
| `1997` | 5ed | 3.38 | 3.05 | 3.38 |
| `2003` | 8ed | 3.00 | 3.00 | 3.00 |
| `2015` | — | 2.45 | 2.45 | assumed = top |

**Where the two methods agree, believe them more.** 1997, 2003 and 2015 agree to within
0.08mm across two different methods and samples. 1993 does not — 3.55 vs 3.15 at the
top — which is the same disagreement the log above is about.

**How the survey scored against the hand readings, now that both exist.** This is the
fairest test the survey will ever get, and it is worth reading before trusting any of the
numbers above:

| generation | survey sides | hand sides | off by |
|---|---|---|---|
| `2003` | 2.92 | 2.98 | 0.06mm — very good |
| `future` | 2.95 | 2.98 | 0.03mm — very good |
| `2015` | 2.45 | 2.56 | 0.11mm — good |
| `1997` | 3.04 | 3.07 | 0.03mm — very good |
| `1993` | 2.79 | 2.03 | **0.76mm — the outlier, and it is the generation the log is about** |

So the survey's *shape* was right and its absolute widths were 0.03–0.11mm narrow on four
generations out of five — consistent with a small common crop, exactly as predicted.

The two treatments it called their own geometry, and how they came out:

| variant | survey | hand | verdict |
|---|---|---|---|
| extended art | top 2.40, **sides 0 — "art runs off the card"** | 2.39 / 2.39 | **wrong.** The sides carry a normal 27–28px border; the survey's detector was fooled by dark art. No spec needed |
| yellow band | top 4.15–4.23, sides **4.70** | 3.76 / 4.27 | **right that it is distinct**, 0.4mm out on the amount. The survey read past two keylines |

And the useful negative result: **31 of the 54 measured combinations of
`frame` × `border_color` × `frame_effects` sit on their own generation's border.** A
`legendary` crown, an `inverted` text box, the Nyx `enchantment` treatment, an `etched`
foil, `snow`, `devoid`, `miracle`, `companion`, `draft`, `spree`, `colorshifted`,
`tombstone` and `fullart` change the picture and not the border; so do `white`, `gold`
and `silver` borders. None of those needs a spec of its own. Raw data in
`docs/mtg-variants.json`; regenerate with `scripts/mtg-variants.py --group`.

## One card per category

Six geometries, not six appearances: there are hundreds of border *designs* and a small
number of distinct answers to "how far in from each cut edge does the border reach".
Sol Ring alone has been printed in seven of these categories, which makes it a good way
to see that most of the variety is picture rather than border.

| category | one card | `frame` | `border_color` | `frame_effects` | resolves to |
|---|---|---|---|---|---|
| 1993 frame | [Sol Ring `leb-270`](https://scryfall.com/card/leb/270/sol-ring) | 1993 | black | – | `mtg-1993` |
| 1997 frame | [Sol Ring `me4-227`](https://scryfall.com/card/me4/227/sol-ring) | 1997 | black | – | `mtg-1997` |
| 2003 frame | [Sol Ring `c13-259`](https://scryfall.com/card/c13/259/sol-ring) | 2003 | black | – | `mtg-2003` |
| M15 frame | [Sol Ring `msc-211`](https://scryfall.com/card/msc/211/sol-ring) | 2015 | black | – | `mtg-m15` |
| future frame | [Sol Ring `mb2-233`](https://scryfall.com/card/mb2/233/sol-ring) | future | black | – | `mtg-2003` |
| extended art | [Sol Ring `cmr-700`](https://scryfall.com/card/cmr/700/sol-ring) | 2015 | black | `extendedart` | `mtg-m15` — measured, shares it |
| yellow band | [Bleachbone Verge `dft-501`](https://scryfall.com/card/dft/501/bleachbone-verge) | 2015 | yellow | `inverted` | `mtg-yellow-band` |
| borderless | [Sol Ring `sld-2807`](https://scryfall.com/card/sld/2807/sol-ring) | 2015 | borderless | – | `borderless` |

So the answer is now **five geometries, not eight**: the five frame generations collapse to
four specs (`future` measured the same as 2003), plus the yellow band, plus `borderless`.
Extended art was the eighth and it turned out to be the M15 frame with a wider picture.

And the near misses — each one measured, each sharing a generation's border rather than
needing one of its own:

| looks like its own border | card | measured | shares |
|---|---|---|---|
| retro frame in a modern set | [Sol Ring `sld-1664`](https://scryfall.com/card/sld/1664/sol-ring) | 40 / 36px | the 1997 frame |
| silver border | [Water Gun Balloon Game `und-85`](https://scryfall.com/card/und/85/water-gun-balloon-game) | 28 / 28px | the M15 frame |
| gold border | [Swords to Plowshares `wc97-jk54`](https://scryfall.com/card/wc97/jk54/swords-to-plowshares) | 40 / 38px | the 1997 frame |
| white border | [Rampant Growth `8ed-274`](https://scryfall.com/card/8ed/274/rampant-growth) | 35 / 35px | the 2003 frame |
| etched foil | [Arcane Signet `p30m-1F★`](https://scryfall.com/card/p30m/1F%E2%98%85/arcane-signet) | 29 / 29px | the M15 frame |
| legendary crown | [Urborg `tsr-287`](https://scryfall.com/card/tsr/287/urborg-tomb-of-yawgmoth) | 28 / 28px | the M15 frame |

**Two of these were previously listed here wrongly**, and the measurements are what found
it. [Evolving Wilds `afr-353`](https://scryfall.com/card/afr/353/evolving-wilds) (showcase)
and [Sol Ring `sld-912`](https://scryfall.com/card/sld/912/sol-ring) (full art) were listed
as sharing the M15 frame. They do not share any frame: **they have no border at all**, on
every edge, and Scryfall calls both `border_color: black`. See their row in the log: both
want `--frame borderless`, or a pin. And
[Aang and Katara `atle-8`](https://scryfall.com/card/atle/8/aang-and-katara-aang-and-katara)
is an art-series card, which resolves to `borderless` from its layout and needs nothing.

**Where the count is still a simplification.** `future` merges into 2003 on a measurement
now rather than on a tolerance. **1997 and 2003 are not candidates for the same treatment,
and that was checked rather than assumed** — an earlier note here said they sat 0.09mm
apart and might collapse under calipers, which was true of the *sides* alone and read as
though it were true of the spec:

| edge | 1997 | 2003 | gap |
|---|---|---|---|
| top / bottom | 3.42–3.50mm (40–41px) | 2.99mm (35px) | **0.43mm, 5–6px** |
| left / right | 3.07–3.24mm (36–38px) | 2.98mm (35px) | 0.08mm, 1–3px |

Three cards each way, read independently, unanimous within each generation. The 1997 frame
has a thicker top and bottom than its sides; the 2003 redesign is what made all four edges
equal, which `c13-259` (black) and `8ed-274` (white) both show at 35px all round. Merging
them to the larger would put a 3.50mm top target on every 8th Edition–M14 card whose border
is 2.99mm — six source pixels on both long edges, invisible on screen and obvious once two
cards are cut. Calipers may move either number; they will not close a 0.43mm gap that three
readings agree on. Showcase is left sharing its generation because a
showcase frame is a different bespoke design *per set*: that is variance across designs,
not an offset worth encoding, and it is a judgement rather than a proof they are identical.

**Two things genuinely wrong rather than simplified**, both on TODO.md:
[`opca-9`](https://scryfall.com/card/opca/9/academy-at-tolaria-west) is an 89×127mm
Planechase card, and an oversized card has no spec of its own — a 63×88 spec's inset
applied to it targets the wrong width. And token frames were never surveyed at all.

## Checking it landed

Two checks, cheap then real:

- **Open the card's Border step in `proxdex ui`.** It draws the spec's outline over the
  untouched original in cyan, which is the cheapest possible check that a number is right:
  the line either sits on the printed border or it does not. That is also the only check
  that needed no code — it is just the target, drawn where the target is.
- **Print one card and measure the paper.** The only ground truth, and the only check
  that catches a printer scaling the page — which no spec can fix and which looks
  exactly like a bad spec.

Changing a spec makes every master already fitted to the old numbers stale, and they
look perfect. `proxdex doctor` reports those (`stale-spec`) by comparing what each
master recorded it was fitted to against what the rules resolve today, so re-run
`border` on the cards it names.
