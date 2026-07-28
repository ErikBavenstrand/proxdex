# Measuring frame specs

A frame spec is four numbers: how far the printed border reaches in from each cut
edge. proxdex reshapes every card until its border matches those numbers, so they
decide what comes off the printer. Nothing else in the pipeline can compensate for
them being wrong, and nothing on screen shows it — a card fitted to a border 0.4mm
too thin looks perfect until it is cut and laid beside a real one.

The numbers that ship are **provisional**. They were read off Scryfall's own card
scans with `scripts/mtg-census.py`, and that method has a limit no amount of
sampling fixes: **a scan carries its own crop.** If a scan is trimmed 0.3mm inside
the card's real cut edge, every border read from it is 0.3mm too narrow, every
card agrees with every other card, and nothing in the image says so. It is a
systematic error, not noise. (Pokémon's scans are visibly worse about this than
Scryfall's, which is why `pokemon-generic` ships as an admitted guess rather than
a survey.)

So the survey establishes the *shape* — which generations differ from each other,
and how — and calipers on a real card establish the *size*. This page is the list
of cards that settles it.

## What to buy

MTG's border changed with the **frame generation**, not with the set. Scryfall
names five, and that list is closed (`frames.FRAME_GENERATIONS`, pinned by a
test), so five cards cover every Magic card ever printed.

Border *colour* is not border *geometry* — a white-bordered Revised card and a
black-bordered Beta card came off the same die — so pick whichever is cheapest.
Same for foil vs non-foil, except that **non-foil is easier to measure** because
foils bow.

| # | Spec | Frame | Buy any common from | Roughly | Covers |
|---|---|---|---|---|---|
| 1 | `mtg-m15` | `2015` | **any set from Magic 2015 (2014) onward** — a basic land is ideal | you already own dozens | ~71,000 prints, two thirds of all Magic |
| 2 | `mtg-2003` | `2003` | **8th–10th Edition**, or Mirrodin → Dragon's Maze | bulk, pennies | 8th Edition to M14 |
| 3 | `mtg-1997` | `1997` | **5th, 6th or 7th Edition**, or Tempest → Urza's | bulk, pennies | Mirage to 7th Edition |
| 4 | `mtg-1993` | `1993` | **Revised or 4th Edition** | bulk, pennies | Alpha to 4th Edition |
| 5 | `mtg-future` | `future` | **Future Sight only** — Scryfall `set:fut frame:future` | ~$0.25, but a deliberate order | 81 prints. Lowest priority. |

Two exclusions that matter, because measuring the wrong card gives a wrong answer
that looks fine:

- **Not Alpha (`lea`).** Its print run was cut differently — the survey reads it
  ~0.65mm off the rest of its own generation. It is 302 of the 5,318 `frame:1993`
  prints, so it gets pinned per card, not made the spec.
- **Not oversized and not a token.** An oversized Planechase or Vanguard card is
  89×127mm, so its border as a fraction of a normal card is meaningless.
- **Not extended-art and not a yellow box topper**, which have their own specs
  already (see below).

Full-art *is* fine and is worth knowing: a full-art M15-frame card keeps its
generation's black border at the normal width — surveyed at 2.42 against an ordinary
2.45. (proxdex once read `full_art` as "borderless" and printed the art into the cut
line. It only reads `border_color == "borderless"` now.)

### What you do *not* need to measure

Every combination of Scryfall's three frame fields has been surveyed
(`scripts/mtg-variants.py`: 114 combinations, the 54 with 20+ printings measured over
15 cards each, sampled proportionally). **31 of them measure at their own
generation's border**, so there is nothing extra to measure and nothing to configure:

- treatments: `legendary`, `inverted`, `enchantment`, `etched`, `snow`, `devoid`,
  `miracle`, `companion`, `draft`, `spree`, `colorshifted`, `tombstone`, `fullart`
- border colours: `white`, `gold`, `silver`

Two do change the geometry, and both are handled already — read straight off the
printing, like `borderless`:

- **extended art** → `mtg-extended-art`: the art runs off the left and right edges, so
  those borders do not exist (`sides = 0`) while the top and bottom keep the M15
  border.
- **the yellow band** (Aetherdrift box toppers) → `mtg-yellow-band`: 4.70mm sides
  against an ordinary 2.45, which is the largest single error the survey found.

So the five cards above really are the whole list.

One caveat it turned up: **`frame:1993` has a ±0.39mm internal spread** — Alpha, Beta,
Unlimited, Revised, 4th Edition and the foreign black-bordered runs genuinely differ
from each other, against ±0.07mm for `frame:1997`. One spec cannot describe all of
them, so if you care about a specific old card, pin it.

### Worth adding while the calipers are out

Pokémon is the bigger real gap: **160 sets** currently resolve to
`pokemon-generic`, which is the WOTC vintage numbers reused on the assumption that
twenty years of frame revisions never moved the border. Nobody has checked.

| # | Spec | Buy any common from | Covers |
|---|---|---|---|
| 6 | a new `pokemon-sv` | a Scarlet & Violet set | the current era |
| 7 | a new `pokemon-swsh` | a Sword & Shield set | 2020–2022 |

Then `proxdex frames assign` them to the sets they cover — `frames preview <set>`
shows which cards each rule catches before you trust it.

## How to measure

Digital calipers, millimetres, two decimals.

1. **Measure the card itself first** — its width and its height. It should come
   out near **63.5 × 88.9mm** (2.5 × 3.5 inches), *not* the 63 × 88 proxdex trims
   to. This matters: the spec is stored as a fraction, so dividing by the wrong
   card size puts a 0.8% error into every border. Record what you actually
   measured and pass it as `--card-w` / `--card-h`.

2. **For each edge, measure from the cut edge to the inner edge of the printed
   border** — where the black (or yellow, or white) stops and the frame or art
   begins.

3. **Measure at the middle of the edge, never near a corner.** The corners are
   rounded, so the border appears to widen there and you will read 0.3–0.5mm too
   much.

4. **Three readings per edge, keep the median.** Calipers on cardstock are easy to
   read 0.1mm off by pressing.

5. **Cancel the cutting error.** Cards are cut with real tolerance — the picture
   is often 0.2–0.4mm off centre, so left and right genuinely differ *on that
   card* while their **sum** stays constant. Take `(left + right) / 2` for both
   side numbers and `(top + bottom) / 2` for both top and bottom. That is the
   spec: the design, not one card's trim.

   **Except for `mtg-1993` and `mtg-1997`,** where the top and bottom really are
   thicker than the sides by design (the survey says ~0.4–0.6mm). Averaging top
   with bottom is still right there; averaging top with the *sides* is not. If
   you can, measure two or three cards of those two generations so the top/bottom
   figure is not one card's trim.

6. **Record it.** Either verb works; they are the same call:

   ```bash
   proxdex frames set mtg-m15 --game mtg \
     --top 2.42 --right 2.44 --bottom 2.42 --left 2.44 \
     --card-w 63.48 --card-h 88.86 \
     --note "calipers on m15-284 Forest, non-foil, median of 3 per edge, L/R averaged"
   ```

   or the **Specs** tab of `proxdex ui` → the frame specs screen → Edit.

   Write the note. It is the only record of where the numbers came from — there is
   no confidence field, deliberately, because the grade that used to sit there
   called a scan reading trustworthy.

## The fill-in sheet

Copy this, fill it in, hand it back and the specs get set from it.

```
card measured:  ____________________   (set-number, e.g. m15-284)
frame:          ____________________   (Scryfall's `frame` — 1993/1997/2003/2015/future)
foil?           ____________________

card width  (mm): ______   card height (mm): ______

border, median of 3, middle of each edge:
  top    ______   right  ______   bottom ______   left  ______
```

Five of those blocks — one per row of the buy list — is everything. If a reading is
more than ~0.3mm off the table above, say so rather than assuming the calipers were
wrong: the 1993 row is known to be shaky, and the 2015 bottom is a guess.

## What proxdex currently thinks — compare your readings against these

**These are scan-derived and provisional.** They are here so you can see, per edge,
what your calipers are replacing. Percentages are the stored form (the inset as a
fraction of the card); millimetres are those percentages of a 63×88mm card, which is
what `proxdex frames list` prints.

| spec | top mm | right mm | bottom mm | left mm | top % | right % | bottom % | left % |
|---|---|---|---|---|---|---|---|---|
| `mtg-1993` | 3.55 | 2.96 | 3.55 | 2.96 | 4.034 | 4.698 | 4.034 | 4.698 |
| `mtg-1997` | 3.38 | 3.05 | 3.38 | 3.05 | 3.841 | 4.841 | 3.841 | 4.841 |
| `mtg-2003` | 3.00 | 3.00 | 3.00 | 3.00 | 3.409 | 4.762 | 3.409 | 4.762 |
| `mtg-m15` | 2.45 | 2.45 | 2.45 | 2.45 | 2.784 | 3.889 | 2.784 | 3.889 |
| `mtg-extended-art` | 2.45 | 0.00 | 2.45 | 0.00 | 2.784 | 0.000 | 2.784 | 0.000 |
| `mtg-yellow-band` | 4.19 | 4.70 | 4.19 | 4.70 | 4.761 | 7.460 | 4.761 | 7.460 |
| `pokemon-wotc` | 3.45 | 3.15 | 3.45 | 3.15 | 3.920 | 5.000 | 3.920 | 5.000 |
| `pokemon-generic` | 3.45 | 3.15 | 3.45 | 3.15 | 3.920 | 5.000 | 3.920 | 5.000 |
| `borderless` | 0.00 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.000 | 0.000 |

`mtg-future` is not in the list: `frame:future` resolves to `mtg-2003`, because the two
survey within 0.05mm of each other.

### A second, independent scan reading

The table above came from **one white-bordered core set per generation** (support 1.00
on every edge, n=10). The variant survey read the same generations a different way —
**every printing that carries the frame, sampled proportionally** — and the two
disagree in a way worth knowing before you measure:

| frame | census (one white core set) | population survey | gap |
|---|---|---|---|
| `1993` | 3.55 / 2.96 / 3.55 | 3.15 / 2.79 / 3.41 | **−0.40 / −0.17 / −0.14** |
| `1997` | 3.38 / 3.05 / 3.38 | 3.37 / 3.04 / 3.38 | 0.00 / −0.01 / 0.00 |
| `2003` | 3.00 / 3.00 / 3.00 | 2.96 / 2.92 / 2.94 | −0.04 / −0.08 / −0.06 |
| `future` | — | 2.94 / 2.95 / 2.99 | — |
| `2015` | 2.45 / 2.45 / (assumed) | 2.48 / 2.45 / 2.88 | +0.03 / 0.00 / — |

(top / sides / bottom.) **1997, 2003 and 2015 agree to within 0.08mm** by two different
methods over different samples, which is the best evidence available that those three
numbers are close to right. **1993 does not agree**, by 0.4mm at the top — because that
"generation" spans Alpha through 4th Edition plus the foreign black-bordered runs, and
they genuinely differ from each other (±0.39mm internal spread). Your caliper reading
of one 4th Edition card will be right for 4th Edition and not for Alpha.

The 2015 **bottom** disagreement is not a disagreement: 2.88 is the black collector
strip being read as border. The real bottom cannot be measured off a scan, which is why
that one number is assumed symmetric with the top. **It is the single most useful thing
your calipers can settle.**

## Checking it landed

Two checks, cheap then real:

- `proxdex border <id> --auto` on a card of that generation. The readout names the
  spec it fitted to and the border it produced. `borders.detect_inset` reads the
  *scan*, so agreement here confirms the scan and the spec now describe the same
  card — a large disagreement means one of them is wrong, and it is worth knowing
  which before printing 50 cards.
- **Print one card and measure the paper.** This is the only ground truth there
  is. It is also the only check that catches a printer scaling the page, which no
  spec can fix and which looks exactly like a bad spec.

Changing a spec makes every master already fitted to the old numbers stale, and
they look perfect. `proxdex doctor` reports those (`stale-spec`) by comparing what
each master recorded it was fitted to against what the rules resolve today — so
re-run `border` on the cards it names.
