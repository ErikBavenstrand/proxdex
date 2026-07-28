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

**`borders.detect_inset` over-reads a black border when the frame is also dark.** It
looks for a *luminance* step. On a Beta Sol Ring — an artifact, whose stone frame is
itself dark — black border (luminance 27) to frame (37) is too small a step, so it
walks on to the light text box and reports **37–41px where the border ends at 23px**.
65% too far. Judging by *colour* instead finds it correctly (the border is neutral,
the frame is a coloured texture), and white-bordered cards were never affected, which
is why the census used them. But it means no absolute number taken off a
black-bordered scan can be trusted.

A scan survey is still good for one thing: deciding **which populations differ from
each other**, because a bias common to every scan cancels in that comparison. That is
what `scripts/mtg-variants.py` is for, and its groupings are kept below as comparison
targets. Absolute widths come from calipers.

## What ships now

| spec | source | top mm | right mm | bottom mm | left mm | top % | right % | bottom % | left % |
|---|---|---|---|---|---|---|---|---|---|
| `pokemon-wotc` | calipers | 3.45 | 3.15 | 3.45 | 3.15 | 3.920 | 5.000 | 3.920 | 5.000 |
| `mtg-1993` | Beta scan, px | 2.71 | 2.03 | 2.71 | 2.03 | 3.045 | 3.199 | 3.045 | 3.199 |
| `borderless` | definition | 0.00 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.000 | 0.000 |

Percentages are the stored form (the inset as a fraction of the card); millimetres are
those percentages of the card each was taken from — 63×88 for `pokemon-wotc`, and
**63.5×88.9** for `mtg-1993`, since a real Magic card is 2.5×3.5 inches.

`pokemon-wotc` covers **only** the WOTC era and stops where the e-Card series begins:
set ids `base1`–`base6`, `gym1`–`gym2`, `neo1`–`neo4`. Everything from `ecard1` onward
resolves to nothing.

## The measurement log

One row per card measured, newest first. A **pixel** reading is taken off Scryfall's
own scan and inherits the crop; a **caliper** reading is taken off the physical card
and is what a spec should eventually rest on.

### `mtg-1993` — MTG 1993 frame (Alpha–4th Edition)

| | value |
|---|---|
| card | [Sol Ring · Beta `leb-270`](https://scryfall.com/card/leb/270/sol-ring) |
| method | pixel reading of the 672×936 scan |
| image | 672 × 936 px |
| sides | **21.5 px** → 21.5 / 672 = **3.199%** of width → 2.03 mm of 63.5 |
| top and bottom | **28.5 px** → 28.5 / 936 = **3.045%** of height → 2.71 mm of 88.9 |
| stored as | the exact fractions, not converted through millimetres, so nothing rounds |

The scan is the whole card — its alpha channel is opaque from pixel 0 on all four
straight edges — which is what makes a pixel count a fraction of the card directly.

**Still open on this one.** Alpha and Beta measure about 1mm narrower on the sides
than white-bordered Unlimited and Revised, and that is not a crop artifact: Sol Ring's
art box occupies pixels 90–656 in the Beta scan and 91–655 in the Unlimited one, so
both are at the same scale. Measured by colour across the generation:

| card | top px | sides px | top mm | sides mm |
|---|---|---|---|---|
| [Alpha `lea-263`](https://scryfall.com/card/lea/263/sol-ring) | 32 | 22–24 | 2.74 | 1.88–2.05 |
| [Beta `leb-270`](https://scryfall.com/card/leb/270/sol-ring) | 32 | 23 | 2.74 | 1.96 |
| Legends `leg-310` | 31 | 24–25 | 2.65 | 2.05–2.13 |
| Ice Age `ice-383` | 32 | 27–28 | 2.74 | 2.30–2.39 |
| [Unlimited `2ed-270`](https://scryfall.com/card/2ed/270/sol-ring) | 42 | 35 | 3.59 | 2.98 |
| [Revised `3ed-270`](https://scryfall.com/card/3ed/270/sol-ring) | 42 | 35 | 3.59 | 2.98 |
| 4th Edition `4ed-297` | 32 | 28–30 | 2.74 | 2.39–2.56 |

(px of the 745×1040 PNG; mm of 63.5×88.9.) So `mtg-1993` as one spec spans a ~1mm
range, and the number stored describes the **black-bordered** printings. Unlimited,
Revised and 4th Edition probably want their own.

**The one thing a scan cannot answer here:** on Unlimited you can see white → a thin
dark line → stone texture. On a black-bordered card that dark line, if it exists, is
invisible. So when measuring a Beta or Alpha card, please record **both**:

- where the flat black stops and the grey/brown stone texture starts
- whether a distinguishable darker line sits just before the texture, and if so where
  the *black* ends

If those come back ~2.0mm sides / ~2.75mm top, the pixel reading is confirmed and
Alpha/Beta keep this spec while Unlimited/Revised get their own. If they come back
~3.0mm, something is fooling the scan that has not been identified, and none of the
readings above should be trusted until it is.

### Nothing else measured yet

## What to measure next

MTG's border changed with the **frame generation**, not with the set — Scryfall names
five, and that list is closed. One card covers each. Border *colour* is not border
geometry (a white Revised card and a black Beta card came off the same die, colour
aside), so pick whichever is cheapest, and **non-foil**, because foils bow.

| spec to create | frame | buy any common from | roughly | covers |
|---|---|---|---|---|
| `mtg-m15` | `2015` | **any set from Magic 2015 (2014) onward** — a basic land is ideal | you own dozens | ~71,000 prints, ⅔ of all Magic |
| `mtg-2003` | `2003` | **8th–10th Edition**, or Mirrodin → Dragon's Maze | bulk, pennies | 8th Edition to M14 |
| `mtg-1997` | `1997` | **5th, 6th or 7th Edition**, or Tempest → Urza's | bulk, pennies | Mirage to 7th Edition |
| `mtg-future` | `future` | **Future Sight only** — Scryfall `set:fut frame:future` | ~$0.25, a deliberate order | 81 prints. Lowest priority. |
| `mtg-1993-white` | `1993` | **Unlimited, Revised or 4th Edition** | bulk, pennies | the ~1mm question above |

Then two treatments that really are their own geometry, so they need their own spec
once there is a generation spec to hang them off:

| spec to create | select on | one card |
|---|---|---|
| `mtg-extended-art` | `frame_effects` contains `extendedart` | [Sol Ring `cmr-700`](https://scryfall.com/card/cmr/700/sol-ring) |
| `mtg-yellow-band` | `border_color` is `yellow` | [Bleachbone Verge `dft-501`](https://scryfall.com/card/dft/501/bleachbone-verge) |

And Pokémon, which is the biggest gap by set count — everything past `neo4` resolves
to nothing:

| spec to create | buy any common from | covers |
|---|---|---|
| `pokemon-ecard` | an e-Card set (`ecard1`–`ecard3`) | 2002–2003 |
| `pokemon-ex` | an EX-era set | 2003–2007 |
| `pokemon-swsh` | a Sword & Shield set | 2020–2022 |
| `pokemon-sv` | a Scarlet & Violet set | current |

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
     --top 2.42 --right 2.44 --bottom 2.42 --left 2.44 \
     --card-w 63.48 --card-h 88.86 \
     --note "calipers on m15-284 Forest, non-foil, median of 3 per edge, L/R averaged"
   ```

   or the **Specs** tab of `proxdex ui` → Edit. Write the note: it is the only record
   of where the numbers came from, since there is deliberately no confidence field.

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
| `1993` | 3.15 | 2.79 | 3.41 | spans a real ~1mm range; see the log above |
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

The two treatments that genuinely change the geometry:

| variant | top | sides | bottom | prints |
|---|---|---|---|---|
| extended art | 2.40 | **0 — art runs off the card** | – | 2,824 |
| yellow band | 4.15–4.23 | **4.70** | – | 79 |

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
| 1997 frame | [Sol Ring `me4-227`](https://scryfall.com/card/me4/227/sol-ring) | 1997 | black | – | *nothing yet* |
| 2003 frame | [Sol Ring `c13-259`](https://scryfall.com/card/c13/259/sol-ring) | 2003 | black | – | *nothing yet* |
| M15 frame | [Sol Ring `msc-211`](https://scryfall.com/card/msc/211/sol-ring) | 2015 | black | – | *nothing yet* |
| future frame | [Sol Ring `mb2-233`](https://scryfall.com/card/mb2/233/sol-ring) | future | black | – | *nothing yet* |
| extended art | [Sol Ring `cmr-700`](https://scryfall.com/card/cmr/700/sol-ring) | 2015 | black | `extendedart` | *nothing yet* |
| yellow band | [Bleachbone Verge `dft-501`](https://scryfall.com/card/dft/501/bleachbone-verge) | 2015 | yellow | `inverted` | *nothing yet* |
| borderless | [Sol Ring `sld-2807`](https://scryfall.com/card/sld/2807/sol-ring) | 2015 | borderless | – | `borderless` |

And the near misses — each measured, each sharing a generation's border rather than
needing one of its own:

| looks like its own border | card | shares |
|---|---|---|
| showcase | [Evolving Wilds `afr-353`](https://scryfall.com/card/afr/353/evolving-wilds) | the M15 frame |
| retro frame in a modern set | [Sol Ring `sld-1664`](https://scryfall.com/card/sld/1664/sol-ring) | the 1997 frame |
| full art | [Sol Ring `sld-912`](https://scryfall.com/card/sld/912/sol-ring) | the M15 frame |
| silver border | [Water Gun Balloon Game `und-85`](https://scryfall.com/card/und/85/water-gun-balloon-game) | the M15 frame |
| gold border | [Swords to Plowshares `wc97-jk54`](https://scryfall.com/card/wc97/jk54/swords-to-plowshares) | the 1997 frame |
| white border | [Rampant Growth `8ed-274`](https://scryfall.com/card/8ed/274/rampant-growth) | the 2003 frame |
| etched foil | [Arcane Signet `p30m-1F★`](https://scryfall.com/card/p30m/1F%E2%98%85/arcane-signet) | the M15 frame |
| legendary crown | [Urborg `tsr-287`](https://scryfall.com/card/tsr/287/urborg-tomb-of-yawgmoth) | the M15 frame |
| art series | [Aang and Katara `atle-8`](https://scryfall.com/card/atle/8/aang-and-katara-aang-and-katara) | `borderless` |

**Where the count is a simplification rather than a fact.** It depends on the 0.15mm
tolerance the grouping uses: loosen it and 1997 / 2003 / future merge, tighten it and
showcase (0.2–0.3mm off its generation) becomes another. Showcase is excluded because a
showcase frame is a different bespoke design *per set*, so that is variance across
designs and not an offset worth encoding — a judgement, not a proof they are identical.

**Two things genuinely wrong rather than simplified**, both on TODO.md:
[`opca-9`](https://scryfall.com/card/opca/9/academy-at-tolaria-west) is an 89×127mm
Planechase card, and an oversized card has no spec of its own — a 63×88 spec's inset
applied to it targets the wrong width. And token frames were never surveyed at all.

## Checking it landed

Two checks, cheap then real:

- `proxdex border <id> --auto` on a card of that generation. The readout names the spec
  it fitted to and the border it produced. Remember `detect_inset` over-reads a dark
  frame's black border, so a disagreement there may be the detector rather than the
  spec.
- **Print one card and measure the paper.** The only ground truth, and the only check
  that catches a printer scaling the page — which no spec can fix and which looks
  exactly like a bad spec.

Changing a spec makes every master already fitted to the old numbers stale, and they
look perfect. `proxdex doctor` reports those (`stale-spec`) by comparing what each
master recorded it was fitted to against what the rules resolve today, so re-run
`border` on the cards it names.
