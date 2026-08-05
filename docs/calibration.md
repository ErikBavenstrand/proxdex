# Rebuilding print calibration

A print profile decides what ink goes on paper for every card proxdex imposes. Nothing
downstream can compensate for it being wrong, and — this is the part that bit — nothing
proxdex currently reports can even *see* the commonest way it goes wrong. The real
`holo-plain` profile has been getting **more** yellow every round while its own residual
fell, and every number on screen called that convergence.

This document is the plan to replace the method. Each stage can be built, measured and
landed on its own, in the order given.

Every number quoted here was measured — either off the real `holo-plain` profile's four
stored rounds, or against a simulated press and scanner. Nothing here is an estimate.

> **Status: all nine stages are built.** The stage-by-stage record is in §4; what each
> one actually measured, once built, is recorded there beside what it was expected to.
> Three defects were found by building it, all of them by *driving the loop end to end*
> rather than by reading the code, and all three are pinned:
>
> - **a survey round could not be stored at all** — every patch array was validated
>   against one global chart length, so a 468-patch round was written and read back as
>   *unreadable*. A `Round` records its `ChartId` now, and a `Pending` records **what
>   went on the paper** rather than letting `calibrate add` recompute it;
> - **gamut compression only fired when the *send* overflowed**, so a transform
>   extrapolating past the region it was fitted over returned a perfectly in-range send
>   for a colour the paper cannot make: a wanted dark orange went out as (21, 66, 95) —
>   heavy ink for a light colour — and came back **blue** at ΔE00 63.6. Reachability is
>   two questions now, and the model carries the region it was measured over;
> - **the lightness give-up guessed one direction.** It inferred "go lighter" from which
>   end the send overflowed, which says nothing about a colour whose send is in range;
>   on a medium whose white is L\* 74 that pushed such colours further outside every
>   time. Both directions are tried and the smaller move wins.
>
> And one reporting defect worth the same billing: **the cast averaged in neutrals the
> medium cannot print.** The ramp is L\*-spaced from 2 to 98 by design, so its ends clip
> to the ink floor and carry *that* hue — on the simulated sticker the reported cast read
> a\* +5.10 (red) where the printable neutrals read a\* +0.86 (neutral). The number on
> screen contradicted the sheet in hand. It is masked by the same reachability the error
> is.

### Existing profiles are re-measured, not migrated

**Decided, and it is what sets the order below.** A stored round is a pair — what was
*sent* and what came *back* — and what was sent was chosen by the method being replaced.
`holo-plain`'s rounds 2–4 were printed through a correction that was already driving toward
yellow (§1.2), so they sample the wrong part of the space, on a patch set with no substrate
reading, no per-channel ramps and a neutral ramp spaced in the wrong units. Refitting them
better would be fitting a better model to evidence gathered for a worse one.

So `holo-plain` is measured again from a fresh chart, and the four stored rounds are kept
only as the **record of the defect** — they are the proof in §1.2 and they belong in this
document, not in a live fit.

Two consequences, both good:

- **The chart comes first.** Nothing has to be kept readable, so the patch set is designed
  for the model rather than around it, and the awkward interim of estimating a substrate
  from the lightest printed patch disappears.
- **`Profile.unreadable` handles it already.** A round whose patch shape no longer matches
  is counted, never dropped in silence, so the old rounds stay visible in the file with the
  count saying why they are out. No migration code, and nothing is deleted behind anyone's
  back.

---

## 1. What is actually wrong

### 1.1 The root cause: two different colour spaces are treated as one

```python
calibrate.fit(scanned, sent)      # learns: scanner reading -> send value
Correction.apply_to_image(im)     # is fed:  sRGB pixels from a provider PNG
```

`fit` builds its features from `scanned`, so **the correction's input domain is scanner
RGB**. `apply_to_image` hands it card pixels, which are sRGB. Nothing establishes that
those are the same space, and they are not.

This is a named method with a published guarantee. Ostromoukhov, Hersch, Péraire, Emmel
and Amidror (*Two Approaches in Scanner-Printer Calibration: Colorimetric Space-Based vs.
"Closed-Loop"*, IS&T/SPIE Vol. 2170, 1994, pp. 133–142) compare exactly the two options
proxdex has. The **closed-loop** or *scan-back* approach maps scanner RGB straight to
printer output with no colorimetric space anywhere — which is precisely what proxdex
does — and its accuracy holds only "for input samples having the same characteristics
(halftone dot, ink spectral reflectance) as the printed samples used for the calibration
process". A provider PNG is not that.

So the loop converges, correctly, on **"the print *scans as* the target's numbers"**. What
we want is **"the print *looks like* the card"**. On ordinary matte a decent scanner is
close enough to sRGB that the two objectives nearly coincide, which is why matte profiles
work. On a coloured or specular substrate they diverge, and the divergence is the cast.

The size of the gap is measured in the same paper: patches with **identical scanned RGB
can differ by more than ΔE 10** in CIELAB, and patches with identical Lab can give quite
different RGB, because a flatbed's R/G/B sensitivities are not a linear transform of the
CIE colour matching functions.

**The fix is to name the missing transform, not to tune the fit.** See §4.7.

### 1.2 Confirmed on the real profile: the loop diverged while reporting progress

`holo-plain` — *"Plain Holographic Sticker / High Quality / Glassy Photo Paper"*, four
rounds, all enabled.

The substrate scans **(144, 189, 208)**: blue is **+64** above red. Strongly bluish. And
its influence is **coverage-dependent**, which is the important part:

| region of the neutral ramp | scanned blue − red |
|---|---|
| highlights (little ink) | **+57.75** |
| shadows (heavy ink) | **+5.50** |

Ink covers the substrate where the card is dark and the substrate shows through where it
is light. That gradient is not something a global colour transform can represent well.

Aiming at an absolute neutral, the fit fought the paper and pushed harder every round.
Blue-minus-red of what it **sent** for a neutral:

| round | sent B−R | reported L\* error |
|---|---|---|
| 1 | 0.00 *(raw target)* | −12.73 |
| 2 | −28.33 | −3.65 |
| 3 | −30.75 | −2.73 |
| 4 | **−32.42** | −4.90 |

Monotonically more yellow ink, while the number the loop watches improved. `Profile.plateau`
would have certified this as converged.

**The cast, in one line.** The correction that profile applies to cards today sends a
200-grey as **(255, 255, 217)** — red and green pinned at the ceiling, blue below them.
That is yellow, and no one chose it: it is the per-channel clip in `Correction.apply`. A
mid-grey goes out as **(232, 189, 166)**. **48 of 80** patches clip.

### 1.3 The other defects, each measured

| # | Defect | Evidence |
|---|---|---|
| 1 | **Per-channel clipping shifts hue.** `.clip(0, 255)` per channel, so a colour needing more of one ink than exists loses only that channel. | The highlight wanted send (273, 264, 299) and got (255, 255, 255) — **44 of blue refused against 18 of red**. Per-channel clipping "favors preserving high saturation… the lightness of the color is not well preserved". |
| 2 | **The gamut is circular.** `Profile.gamut` is `reachable(self.correction)` and `score` measures only over that mask — both from the same fit. The docstring claims it is "a fact about the paper and the inks"; it is not. | Refitting over 20 patches instead of 80 moved the reported gamut from **37 to 49 patches** on identical scan data. Residuals are therefore not comparable across refits or profiles. |
| 3 | **The metric cannot see a cast.** Euclidean RGB over the reachable set. | A grey b\* of +8.66 does not move it. This is how §1.2 happened. |
| 4 | **Nothing normalises between rounds.** Rounds are pooled as if scanned in the same instrument state. | The four `holo-plain` rounds' white drifts **9 levels (6%)**. Second-order here — normalising alone moved the send tilt only −34.23 → −33.61 — but it is unmeasured error being pooled as signal. |
| 5 | **No substrate anywhere.** The chart has no bare-paper patch and no max-ink patch; the ramp's ends are ink values, not the paper. | §1.2's whole mechanism is invisible without it. |
| 6 | **No per-channel ramps at all.** A neutral ramp and an interior lattice only. | So R, G and B **cannot** be linearized separately, which is step two of every industry workflow. |
| 7 | **The neutral ramp is `linspace(4, 252)` in device code values.** | After a printer's tone response the visual steps are badly uneven, and the highlights — where the eye is most critical and where §1.2's error is largest (+57.75) — are undersampled. |
| 8 | **Round 1 filters nothing.** `usable()` drops samples whose *send* is pinned, and round 1's sends *are* the target. | So the first and most influential fit trains on every unreachable patch. On foil that is 43–48 of 80. |
| 9 | **One degree-2 polynomial does everything** — tone response, grey balance and colour matrix at once, by unweighted least squares. | Grey is 16 of 80 patches, so the axis the eye judges is outvoted 4:1. The literature is explicit: errors from cells "lying on different sides of the gray axis… can be diminished by densely populating areas close to the gray axis". |

### 1.4 Two hypotheses tested and **rejected** — do not build these

- **Non-monotonicity.** The fitted polynomial was suspected of reversing a gradient. It
  does not: **0 reversing steps** on the neutral, red and blue axes, on both simulated
  presses. The ridge toward identity keeps it well behaved.
- **Scrambling the patch layout** (what ArgyllCMS `printtarg` does, to decorrelate flare
  from the ramp). Measured: flare error on the greys **10.54 → 10.43 levels**, and ΔE
  slightly *worse*. It does not apply here because every patch is already surrounded by a
  white gutter, which makes flare near-uniform across patches — and that uniformity is
  precisely *why* substrate-relative aiming works (§2.2).
- **Fitting the forward model and inverting it numerically**, to avoid regression dilution
  from a noisy independent variable. A wash: 12.24 vs 13.36 ΔE at the worst noise level
  tested, and worse at realistic noise. The re-projection cost exceeds the dilution.
- **Black point compensation.** Actively harmful here — it lifts the whole shadow end and
  roughly doubled ΔE (7.56 → 16.92). Map the white; do not map the black.

---

## 2. What the new method is

The industry order, which proxdex currently collapses into one step:

```
ink limit  ->  linearization  ->  grey balance  ->  colour transform  ->  gamut mapping
```

Each stage does one job, is separately measurable, and is inspectable on screen. This is
the sequence every RIP and profiling tool uses, and the reason for it is that a stage
downstream cannot repair a stage upstream: a colour transform fitted over a non-linear,
grey-unbalanced response has to spend its parameters undoing them.

### 2.1 The substrate is a first-class measurement, not something the fit infers

Bare paper is the reference the whole profile hangs from, and **the chart is already
covered in it** — every white gutter and margin is unprinted substrate. Sampling it gives
three separate things, all free and all scanner-independent:

1. **Substrate colour** — what the paper is, before any ink. This is what relative aiming
   needs (§2.2). Truer than the lightest ramp patch, which is printed at 252 rather than
   bare.
2. **A per-round white** — puts every round in the same instrument state, fixing §1.3 #4.
3. **A per-position white across the sheet** — the only handle on the flatbed's
   centre-to-edge non-uniformity, which the SPIE paper measures at up to **ΔE 5** across
   the platen.

Show-through is **coverage-weighted** (the Murray–Davies / Kubelka–Munk picture), which is
exactly the +57.75-vs-+5.50 gradient of §1.2. So the substrate enters the model as its own
term rather than as a bend the polynomial has to learn.

### 2.2 Aim relative to the substrate, with adaptation as a knob

A "white" on a blue holographic sticker **is** blue-white. No ink makes it whiter, so
demanding an absolute neutral demands the impossible — and §1.2 is the bill for it, paid
in yellow across the whole tone range including midtones that never needed it. Your eye
adapts to the sheet in your hand; this is ordinary relative-colorimetric rendering.

Measured by refitting `holo-plain`'s **existing** rounds, nothing new printed or scanned:

| | mid-grey sent | ramp B−R | clipping |
|---|---|---|---|
| today (absolute aim) | (232, 189, 166) | −34.23 | 48/80 |
| \+ rounds normalised to one white | (227, 186, 165) | −33.61 | 46/80 |
| **\+ aim relative to the substrate** | **(139.5, 137.5, 144.4)** | **+2.06** | **5/80** |

A neutral send again, and clipping all but gone.

Confirmed independently on the simulator: foil greys a\* +2.26 / b\* −3.01 → **+0.48 /
+0.36**, and matte ΔE 3.95 → **1.04**.

**Adaptation is a 0..1 knob (`Intent.adaptation`), defaulting to 1.0 (fully relative).**
Wanting *some* of the substrate's colour neutralised is a legitimate preference — that is
what a perceptual intent is for — and it must be a choice rather than an assumption,
because at 0.0 you get today's behaviour and it should be reachable deliberately.

### 2.3 Gamut mapping, not per-channel clipping

Out-of-gamut colours are compressed **toward the neutral axis at constant hue**, in Lab,
rather than clamped per channel. The CIE's own anchors for this are HPMINDE
(hue-preserving minimum colour difference) and SGCK (sigmoidal lightness mapping plus knee
scaling toward the cusp); we implement a hue-preserving chroma+lightness compression in
that family. Clipping is only defensible when the two gamuts nearly coincide, which on a
holographic sticker is not the case.

### 2.4 Measure in a perceptual space and report the cast separately

Everything is scored as **ΔE00 in CIELAB**, relative to the substrate white when
adaptation is on. Plus two numbers that a mean hides and that would have caught §1.2 on
round 2:

- **`cast_a` / `cast_b`** — mean a\* and b\* over the neutral ramp alone.
- **`tilt`** — the blue-minus-red of what is being *sent* for a neutral. This is the
  diagnostic that went 0 → −28 → −31 → −32 while everything else looked fine.

`plateau` may not certify a profile whose cast is still moving.

### 2.5 The gamut is measured from the scans

Reachability comes from what the paper actually returned across every live round, not from
inverting the fit that is about to be scored against it.

---

## 3. The chart, and how many sheets it costs

Two charts, because characterizing a medium and confirming a correction are different
errands — and the six-rounds-on-one-sheet loop existed only because 80 patches cannot
characterize anything.

### 3.0 The new loop: one survey, one verification, refinement optional

```
survey   (one full sheet, ~457 patches)  -> the model
verify   (one slot, ~80 patches)         -> ΔE00 + cast, on the model's own predictions
refine   (one slot each, optional)       -> adaptive, only while it still buys something
```

This is the industry shape — `targen → printtarg → scanin → colprof` is a single pass, with
a second `targen -c` pass as an optional refinement — and it replaces "print six charts and
watch a number fall" with "measure once properly, then check".

**Verification is not the same as another round, and this is the point.** A refinement round
re-fits the model with more data, so it can only ever agree with itself. A verification
round prints colours the model *predicts* and asks how far off they landed — the first
number in this whole system that can fail. §1.2 happened because there was no such number.

### 3.1 Survey chart — one full sheet, printed once

The characterization run. It gets the whole sheet because this is the measurement that
decides everything, and because the trade's advice for a scanner-read target is explicitly
*"a lower patch count and measuring twice or making the patches wider"* rather than more
patches. IT8.7/3 is 928 patches and IT8.7/4 is 1617, but those are instrument-read;
proxdex's own earlier measurement (228 patches worse than 80, 512 worse than 36) is the
same finding from the other side.

Geometry, at a 5mm margin on A4 (200 × 287mm printable):

- patch **8mm**, gutter **3mm** → pitch 11mm → **18 × 26 = 468 cells**
- 8mm at 600dpi is 189px; sampling the centre 50% is a median over ~9,000 pixels
- the gutter is 3/8 of the patch, which is what keeps flare near-uniform (§1.4)

Budget:

| patches | what | why |
|---|---|---|
| 24 | **bare substrate**, on a lattice across the page | §2.1 — substrate colour, per-round white, per-position uniformity |
| 3 × 17 = 51 | **single-channel ramps** R, G, B | linearization (§1.3 #6). 17 steps is near QTR's 21-step wedge |
| 21 | **neutral ramp, spaced in L\*** | grey balance (§1.3 #7) |
| 6 | **max ink** — each channel at 0, and composite black | ink limit and black point |
| 12 | **repeats** of patches already present, placed far apart | read noise and cross-sheet uniformity, measured rather than assumed |
| 343 | **7³ interior lattice** | the colour transform |
| **457** | total, 11 cells spare for fiducials and the label | |

For scale: the SPIE paper's printer-only calibration used 9³ = 729 patches read with a
spectrophotometer and achieved **mean ΔE 1.6, σ 0.7**. That is the ceiling, not a target.

### 3.1a A full sheet of holographic sticker is not free, so density is a choice

The survey wants a whole sheet, and on the stock this was found on that is a real cost. So
the size is a setting with its consequence stated, not a constant:

| survey | area | lattice | patches | what it costs you |
|---|---|---|---|---|
| `full` | 1 sheet | 7³ = 343 | ~457 | the recommended one |
| `half` | 3 slots | 5³ = 125 | ~215 | coarser interior; ramps and substrate unaffected |
| `quarter` | 2 slots | 4³ = 64 | ~150 | at the floor — below this the model is guessing between samples |

The ramps, the neutral ramp, the substrate patches and the max-ink patches are **the same at
every size**, because those are what the linearization, the grey balance and the substrate
term are built from and they are not where a patch can be saved. Only the interior lattice
shrinks. `quarter` on one sheet leaves four slots for verification and refinement, which is
the cheap way through if paper is short.

### 3.2 Verification chart — one slot, six to a sheet

Keeps today's geometry: a small chart in one slot of the 2×3 grid, feed the same paper back
in for the next one. Three changes:

- it prints **what the model predicts**, and is scored on how far those landed (§3.0) — a
  number that can fail
- it is **adaptive** — patches placed where the model is least certain and inside the
  measured gamut, which is the `targen -c` mechanism (ArgyllCMS raises its sampling
  adaptation from 0.1 to 1.0 once a prior profile exists). Today's fixed lattice spends
  43–48 of 80 patches on colours the paper cannot make
- it carries **bare-substrate patches too**, so every round has its own white (§2.1)

### 3.3 Scanning rules the chart should state on itself

- scanner auto-correction **off** (already documented; the chart should print the reminder)
- **scan the middle of the platen** — up to ΔE 5 drift centre-to-edge
- let the lamp warm up; scan all rounds of a sheet in one session where possible (§1.3 #4)
- ink from successive cartridges varies enough that a profile has a shelf life — record
  the cartridge in the profile's notes

---

## 4. Implementation stages

Nothing has to stay readable (§0), so the order is the technical one: **you cannot model
what the chart does not measure, and you cannot judge a model without a metric.** So metric,
then chart, then the things the chart makes possible.

Each stage is landable on its own and has an acceptance test measured against the stage-0
harness. The medium is unusable for real printing until stage 4; `profile use none` is the
honest state in the meantime, and that is the same identity that already exists.

### Stage 0 — the harness ✅

`tests/press_sim.py`: a `SimPress` (substrate white/black, per-channel tone response, ink
crosstalk) and a `SimScanner` (per-channel gain, additive offset, noise, optional flare),
with a warm matte, a blue holographic sticker matching §1.2's measured numbers, and an
honest and a biased scanner. The acceptance tests are in
`tests/test_calibrate_model.py`.

This is first because every later stage has to be *measured*, and a colour bug that only
shows on paper is exactly what the suite exists for. It also pins the two rejected
hypotheses of §1.4 so nobody rebuilds them.

**Measured:** the press reflectance follows Murray–Davies, so the paper shows through in
proportion to how little ink covers it — the sticker reads **+49.9** blue-minus-red in
the highlights against **+2.0** in the shadows, against the real profile's +57.75 / +5.50.
That is the same phenomenon, which is what ties the simulator to the medium the defect was
found on. Both rejected hypotheses reproduce as rejected: **0** reversing steps on the
neutral, red and blue axes across all four press × scanner combinations, and reading a
scrambled patch layout back gives the same numbers to within **0.5 ΔE00**.

### Stage 1 — `proxdex/colour.py`, and an honest metric ✅

New module: sRGB ↔ XYZ ↔ Lab, `delta_e00`, and `adapt(rgb, white)` (von Kries scaling
toward a reference white).

`calibrate.Error` gains `de00_mean`, `de00_max`, `cast_a`, `cast_b`; `score` computes them.
`Profile.gamut` is measured from pooled scans (§2.5). `plateau` refuses to certify while
the cast is moving.

**Acceptance:** re-scoring `holo-plain`'s stored rounds reports a moving cast and does
*not* report convergence. ✅

### Stage 2 — chart v3 ✅

The survey chart and the verification chart of §3: per-channel R/G/B ramps, an L\*-spaced
neutral ramp, bare-substrate patches on a lattice across the page, max-ink patches, repeats,
and the `full`/`half`/`quarter` density setting.

`Chart` stops being one module constant and becomes a described patch set — each patch
carries **what it is for** (`Role`: `substrate`, `ramp_r/g/b`, `neutral`, `max_ink`,
`repeat`, `lattice`), because every later stage selects patches by role rather than by index
arithmetic. Today's `_GREYS = slice(0, 16)` convention is exactly the kind of implicit index
that a chart change breaks silently.

`Profile.unreadable` counts the old rounds. No migration.

**Acceptance:** the survey renders at all three densities inside the printable box, every
role is present at every density, and a rendered-then-read round-trip recovers every patch
to within the sampling window. ✅

Two things this needed that the plan did not foresee. The chart's **canvas height follows
its patch grid** rather than being a second constant — one fixed 1200×1350 drew an 18×26
survey's patches half as tall as they are wide — and `survey_rect` is **one function the
writer and the reader both use**, because a chart is letterboxed inside its box when its
grid is not the box's shape, and a reader cropping the *box* hands fiducial detection a
band of blank paper.

### Stage 3 — the substrate ✅

`calibrate.Substrate` (white, black, per-position map), read from the bare patches. Recorded
on each `Round`. Per-round white normalisation before pooling.

**Acceptance:** on the harness's blue-sticker press, the substrate is recovered to within
read noise, and a simulated 6% inter-round lamp drift is removed by normalisation. ✅ —
recovered to **1.0 level** of (144, 189, 208) through an honest scanner.

### Stage 4 — intent and substrate-relative aiming ✅

`calibrate.Intent(adaptation: float = 1.0)`, `aim(target, substrate, intent)`. A per-profile
setting, surfaced in `profile` and the print screen. No black point compensation (§1.4).

**This is the stage that makes the medium printable.** After it, a fresh survey on the
holographic stock should reproduce §2.2's shape: a near-neutral send for a neutral, a tilt
near zero, and clipping in single digits rather than 48 of 80.

**Acceptance:** greys land within ΔE00 2 of neutral relative to the substrate white on the
blue-sticker press; `adaptation = 0` reproduces today's behaviour exactly, so the old
behaviour is reachable deliberately and never by default. ✅ — a ten-step grey ramp comes
off the blue sticker with **no visible cast** through an honest scanner.

It also changed how a print is *scored*, which the plan implied and did not say: a check
printed through the model reads **ΔE00 3.37, cast a\* +0.07 b\* +2.06** judged relative to
the paper and **15.94, a\* -5.15 b\* -5.00** judged absolutely. Two verdicts on one sheet,
and the second one is about the stock. Scoring relative only makes sense for a print that
*aimed* relative, though: on a raw survey the relative reading is worse, because a flat
division by the white cannot undo show-through that is coverage-dependent. Aiming is what
removes it.

### Stage 5 — the staged press model ✅

`proxdex/press.py`, replacing the single degree-2 polynomial:

- **5a — ink limit**, per channel and total, from the survey ramps and the max-ink patches
- **5b — linearization**: three monotone 1-D curves (monotone PCHIP), from the
  single-channel ramps
- **5c — grey balance**: the neutral axis forced neutral relative to the substrate white,
  at every lightness, from the L\*-spaced ramp
- **5d — colour transform**: the 3→3 on the lattice residual, after 5a–5c have removed
  everything they can. Tetrahedral LUT or polynomial, **decided by measurement on the
  harness, not by taste** — and if a LUT, degenerate cells at gamut concavities must be
  removed, which the SPIE paper found the hard way
- `PressModel.forward(send) -> reference` and `.raw/send(want) -> send`, composed from the
  stages

Each stage is separately inspectable, which is the whole reason for the split: a cast is
now attributable to *one* of them.

**Acceptance:** on the harness, each stage measurably reduces the residual the next one
sees, and the composition beats the single polynomial on both presses. ✅

| | linearization | grey balance | colour transform | staged | one polynomial |
|---|---|---|---|---|---|
| matte / honest | 23.30 → 6.34 | → 3.03 | → 2.68 | **1.34** | 2.22 |
| matte / biased | 22.14 → 5.89 | → 3.66 | → 2.74 | **3.87** | 4.99 |
| holo / honest | 23.23 → 8.41 | → 5.72 | → 5.06 | **3.64** | 4.98 |
| holo / biased | 21.81 → 7.76 | → 6.64 | → 4.90 | **6.42** | 10.05 |

The first three columns are the model's prediction error with one more stage switched on;
the last two are ΔE00 on a neutral ramp really printed and scanned. **5d stayed a
polynomial** — the LUT question was already settled by measurement (a 3-D LUT lost at
every density tried), so nothing here re-opened it.

Two decisions the plan left open, settled while building:

- **the inverse is arithmetic, not a second fit.** Each stage's `inverse` is the exact
  inverse of its `forward` — bisection on a monotone curve, the mirrored solve on the grey
  axis — because two independently fitted directions are free to disagree and the
  round-trip error nobody measured is a fit that lies about what it will print. Only 5d
  has no closed-form inverse, so it is fitted both ways and **reports** its round-trip
  error (measured: under 0.05 in response units);
- **only an uncorrected round can linearize.** A ramp printed through a correction is no
  longer a sweep of one channel, so the patch a chart labels `ramp-r` did not measure
  red's own response. That is why the survey prints raw, and a profile with no direct
  round gets identity curves and is **told** so (`PressModel.staged`) rather than getting
  a linearization inferred from the wrong patches.

### Stage 6 — gamut mapping ✅

`calibrate.compress(lab, gamut)` — hue-preserving chroma and lightness compression toward
the neutral axis, replacing `.clip(0, 255)`.

**Acceptance:** no out-of-range send is ever resolved by moving one channel alone; the
(273, 264, 299) case maps at constant hue. ✅ — and see the two defects at the top of this
document, both of which live here: reachability is **two** questions (the send fits *and*
the colour is inside the measured region) and the lightness give-up tries **both**
directions.

### Stage 7 — verification and adaptive refinement ✅

`calibrate verify` prints what the model predicts and scores how far it landed (§3.0) — the
first number in the system that can fail. Then `targen -c`-style adaptive placement for
optional refinement rounds, and `plateau` judged on the **verification** error rather than
on the fit's own residual.

**Acceptance:** on the harness, a deliberately under-fitted model is caught by verification
while its own residual still looks healthy — i.e. this stage detects §1.2. ✅ — a model
fitted on one press verifies at **1.6** on that press and **18.4** on another, which is
the shape of the property: a model is always consistent with the data it was fitted on.

`Purpose` on a round is what carries it, deliberately *not* the chart id — a refinement
round and a verification round print the same small chart, and what differs is what is
done with the answer. A check is scored and kept **out** of the fit, because a model that
trains on its own exam cannot fail it. Adaptive placement moves **only the lattice**, so
two checks stay comparable on the greys, the ramps and the substrate, which is where a
trend is read from.

### Stage 8 — `Reference`, the optional missing piece ✅

`calibrate.Reference`: scanner reading → reference space. `Reference.identity()` with
`assumed=True`, which is what every profile has until a reference target is read, and
which **every surface reports** — because §1.1 is an assumption and an unstated assumption
is how this happened. `Reference.from_target(readings, known_lab)` for an IT8.7/2 or
ColorChecker, which is the industry answer for profiling without a spectrophotometer
(SilverFast's ICC printer calibration is exactly this: IT8-calibrate the scanner, then use
it as the instrument).

Deliberately last. Everything above is worth having without it, and it is the only stage
that needs a purchase.

Built with the **ColorChecker** and deliberately *not* IT8.7/2: an IT8's 264 patches are
batch-specific and ship with a data file per production run, so hardcoding "the" IT8
values would be inventing a measurement — the exact thing this area exists to stop. The
published values are D50/2° and everything here is D65, so `colour.from_lab_d50` adapts
them through Bradford: measured, that moves the chart's **blue** patch 4.82 ΔE00 and its
white 0.16, which is precisely the region a blue substrate is already the whole discussion
about. Ignoring it would have been a systematic error in the one place it hurts.

### Surfaces — what each stage owes the CLI and the UI

Parity here is two-way and load-bearing, so each stage lands its verb and its screen in the
same change, not afterwards.

| stage | CLI | UI (print screen) | landed as |
|---|---|---|---|
| 1 | `profile show` reports ΔE00 + cast | the round table's ΔE00 and cast columns | ✅ |
| 2 | `calibrate survey [--size full\|half\|quarter]` | the survey button with its density choice | ✅ |
| 3 | `profile show` names the substrate | a paper **swatch** and its reading | ✅ |
| 4 | `profile intent <0..1>` | a slider saying what each end means | ✅ |
| 5 | `profile show --stages` | one row per stage, so a cast is attributable | ✅ |
| 6 | reported when a colour needs compression | — | ✅ |
| 7 | `calibrate verify`, `plateau` judged on it | a verification line distinct from the rounds | ✅ |
| 8 | `calibrate reference --scan <f>` / `--clear` | the assumed-reference warning, and a way out | ✅ |

Two departures from that table, both deliberate. `[calibrate] survey_size` was **not**
added: the density is a decision about one sheet of paper, which is a per-run choice like
every page setting and not a library preference — so it is a flag and a dropdown, and
nothing stores it. And stage 6 has no screen of its own, because "how many pixels of a
card were out of gamut" is a number about *a card*, which is the border step's kind of
question; what the print screen shows instead is the gamut the medium was measured to
have, which is the fact compression is derived from.

Two things every surface has to carry, because they are the lessons of §1:

- **`assumed` is never silent.** A profile whose reference is the identity says so wherever
  it is named — the same treatment a dangling `[print] profile` already gets.
- **Three numbers, never one.** ΔE00, the cast, and the verification error. One number is
  what let a diverging profile look converged.

---

## 5. What this still cannot do, and must say so

- **No absolute colorimetry without a reference target.** The ΔE-10 floor of §1.1 stays.
  Stages 1–7 remove a large, systematic, *correctable* error; they do not make a flatbed a
  colorimeter. Every profile states whether its reference is measured or assumed.
- **A gonioapparent substrate cannot be measured at one geometry.** A holographic or
  metallic stock changes colour with illumination and view angle, which is why the trade
  uses sphere or multi-angle instruments; a flatbed sees one slice. A scan that comes back
  flat and self-consistent off a substrate that visibly shimmers is **not** evidence the
  reading is good. proxdex should detect the case (within-patch spread, which
  `sample_patches` currently measures and throws away) and say so.
- **Ink varies between cartridges.** A profile describes the cartridge that printed it.
- **The residual is not a promise about a card.** It is the error over the patches this
  medium can reach, in ΔE00, with the cast reported beside it — three numbers, because one
  number is what let a diverging profile look converged.

---

## 6. Sources

- Ostromoukhov, Hersch, Péraire, Emmel, Amidror, *Two Approaches in Scanner-Printer
  Calibration: Colorimetric Space-Based vs. "Closed-Loop"*, IS&T/SPIE Vol. 2170 (1994),
  pp. 133–142 — the closed-loop guarantee, scanner boundary effects (ΔE 5), non-colorimetric
  RGB sensitivities (ΔE 10), grey-axis density, cartridge variation, 9³/spectro at ΔE 1.6
- Paul Roark, *Making QTR Profiles with a Flatbed Scanner* — ink limit first, off a
  single-channel step wedge
- ArgyllCMS: [`targen`](https://www.argyllcms.com/doc/targen.html) (adaptive placement,
  OFPS, adaptation 0.1 → 1.0 with `-c`), [usage scenarios](http://www.argyllcms.com/doc/Scenarios.html)
- Inèdit, Caldera — ink limit → linearization → profile, in that order
- [G7 Method](https://en.wikipedia.org/wiki/G7_Method) and Techkon's G7 guide — grey balance
  as the perceptual foundation, a\*=b\*=0, "casted" as the failure mode
- CIE gamut mapping anchors: HPMINDE and SGCK; [chroma clipping evaluation](https://www.cis.rit.edu/people/faculty/montag/PDFs/057.PDF)
- [SilverFast ICC printer calibration](https://www.silverfast.com/about-silverfast-why-scanning-basics-of-scanning/why-silverfast/silverfast-feature-highlights/icc-affordable-printer-calibration-precise-colors-profiling/) — IT8-calibrated scanner as the measuring device
- Colorimetric characterization of flatbed scanners — uncorrected ΔE00 12.3 → 4.9 with an
  IT8.7/1 matrix profile
- Patch counts: IT8.7/3 928, IT8.7/4 1617, ECI2002; and the trade's own "lower patch count,
  measure twice, or make the patches wider"
