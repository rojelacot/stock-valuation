# Score validation — does a higher score predict better returns?

**The question the whole tool rests on:** if you had scored these names in the
past, would the high scorers have earned higher long-term returns than the low
scorers? This is what separates a real signal from a plausible-looking heuristic.

> **The backtest is retrospective and survivorship-biased. The honest test is
> forward.** The weekly screen logs every pick with its entry price and date, and
> the **Forward performance** panel on the Track-record tab (`track_record.py`)
> scores what actually happened next — each pick's real return since it was first
> flagged, vs the S&P 500. That record is out-of-sample, free of survivorship bias
> (it's the picks exactly as made), and immune to backtest overfitting. It needs
> months of picks aging to mean anything, but it is the one validator that can't
> fool itself — and it compounds every week. Everything below is the *retrospective*
> check; treat the forward panel as the real scorecard as it matures.

## Method

`backtest.py` rebuilds each name's score **as of** several past start-years
(fundamentals truncated to that year, price as of then), then measures the actual
forward **total return** (price change + dividends) over a 5-year hold. It pools
multiple start years so the result isn't one-regime luck.

```bash
.venv/bin/python backtest.py --scope large --edgar --years 5 --windows 2016,2018,2020
```

- Universe: **large** (~900 names, ≈ Russell 1000).
- Windows: 2016→2021, 2018→2023, 2020→2025 (all complete 5-yr holds).
- **2,241** name×window observations.

## Result — the honest, full-universe answer

| as-of score | obs | median 5-yr total | mean |
|---|--:|--:|--:|
| 80+   | 37   | +100% | +137% |
| 70–79 | 55   | +80%  | +108% |
| 50–69 | 354  | +98%  | +148% |
| <50   | 1795 | +76%  | +128% |

- **≥70 vs <50: +83% vs +76% = a +7-point edge over ~5 years.**
- **Monotonic: NO.** The 50–69 band (+98%) actually *beat* the 70–79 band (+80%).
- **Per-window it's inconsistent:** 2016→2021 the high scorers *underperformed*
  (≥70: +74% vs <50: +94%, a −20pt edge); 2018→2023 was flat (+1pt); only
  2020→2025 showed a real edge (+43pt). None of the three windows were monotonic.

**So the composite score, on its own and across the broad universe, is a weak and
inconsistent predictor of 5-year returns.**

### The core-universe result was a small-sample mirage

An earlier run on the ~57-name **core** universe showed a spectacular monotonic
+193-point edge. That was luck: only **three names (AAPL, META, QCOM)** ever cleared
score 70 in that mega-cap set, and they happened to be enormous winners. With 2,241
observations instead of a handful, the effect collapses. This is the textbook
danger of a thin backtest, and it's why the full run matters.

## What actually predicts returns: the margin of safety

Slicing the same observations a different way is more revealing:

| slice | obs | median 5-yr total | mean |
|---|--:|--:|--:|
| **upside ≥ 30% (cheap — big margin of safety)** | 153 | **+105%** | **+167%** |
| slightly overvalued (0 to −30%) | 390 | +80% | +126% |
| score ≥ 80 (the screen's actual output) | 37 | +100% | +137% |
| score < 50 | 1795 | +76% | +128% |

**Cheapness is the strongest single signal here** — the deep-margin-of-safety names
(+105% median, +167% mean) beat every score bucket. The *valuation* pillar is doing
the predictive work; the quality-weighted composite adds noise (the 70–79 band
underperforms partly because it catches expensive-but-high-quality names). And the
screen's real output — **score ≥ 80, which in this data is always also in the buy
zone** — did modestly beat the bottom (+100% vs +76%, a +24pt median edge).

### Does quality filter value traps *within* the cheap names? (the 2×2)

The claim the tool leans on is that quality "screens value traps out of the cheap
bucket." Split the 2,241 observations both ways at once — cheap (upside ≥ 30%) vs
not, quality (score ≥ 60) vs not (`analyze_backtest.py`):

| median 5-yr total | quality (score ≥ 60) | low (score < 60) |
|---|--:|--:|
| **cheap (upside ≥ 30%)** | +79% (n=87) | **+170% (n=66)** |
| not cheap | +82% (n=128) | +78% (n=1685) |

**On this data the claim fails: within the cheap names, low-quality *beat* high-quality
by 91 points.** Two forces produce that:
- **Mean reversion** — among *survivors*, the cheapest-ugliest names snapped back hardest.
- **Survivorship bias, concentrated in exactly this cell** — the cheap junk that actually
  delisted or went to zero is *absent* from a today's-Russell-1000 universe, so the
  surviving cheap-low-quality names are a lucky subset. This is the one cell the bias
  most corrupts.

The honest reading: **a survivor-only sample structurally cannot test trap-avoidance** —
the traps it should catch are the names that left the index. So the quality filter is
justified by the composite's quintile spread (below) and by first principles (a
shrinking or distressed business is not a margin of safety), **not** by a return lift
*within* the cheap bucket, which this data shows going the other way. Don't over-read the
+170%: it is the reward for surviving, which you can't know in advance.

## Honest bottom line

- **The composite score is NOT a strong return predictor.** On the full universe it
  buys you a ~7pt edge, isn't monotonic, and reverses in one of three regimes.
- **The margin of safety carries the signal.** Cheap names outperformed; the screen
  works to the extent it insists on buying *below* fair value, not because the
  quality score ranks winners.
- **Treat the score as a quality *filter*, not a return *forecast*.** Use it to weed
  out low-quality and richly-priced names; rely on the buy-below gate (and the
  value-trap / suspect guards) for the actual edge.
- Every bucket returned +70–105% because 2016–2025 was a strong bull market — the
  *absolute* numbers aren't the point; the thin *relative* edge is.
- **The screen's output beats the opportunity-cost benchmark** (next section): score ≥ 80
  & cheap returned +113% median vs +79% for buying the whole universe equal-weight, and
  led in all three windows. That relative edge — not the absolute return — is the case for
  the tool.

## Did the screen beat the index? (the opportunity cost)

Beating inflation is the *goal*, but the honest benchmark for any stock picker is "why
not just buy the whole universe?" Using the same 2,241 observations as the benchmark
(so survivorship bias is shared and cancels — the delisted traps are missing from both
the screen output *and* the index proxy), comparing the screen's real output
(score ≥ 80 **and** cheap = in the buy zone) against an equal-weight and a cap-weight hold
of the universe:

| window | equal-wt universe (med) | cap-wt (mean)¹ | screen ≥80 & cheap (med, n) |
|---|--:|--:|--:|
| 2016→21 | +98% | +118% | +103% (n=7) |
| 2018→23 | +86% | +81% | +113% (n=13) |
| 2020→25 | +57% | +47% | +116% (n=7) |
| **pooled** | **+79%** | — | **+113% (n=27)** |

- **The screen output beat the equal-weight universe by +34pt median (+113% vs +79%) and
  led in every window** — most clearly in the weak 2020→25 stretch (+116% vs +57%). This
  is the first direct evidence the tool's *actual output* clears the opportunity-cost bar,
  not merely that high scores beat low scores.
- The broad cheap slice (upside ≥ 30%, n=153) also beat the universe: +105% vs +79% = +26pt.
- **Caveats:** n=27 pooled (7–13 per window) is thin — treat +34pt as directional, not
  precise. Cap-weight is noisier (the mega-caps lagged the average name over these windows,
  so equal-weight > cap-weight); ¹the *pooled* cap-weight is omitted because market caps
  grew over the decade, so later windows' larger caps would dominate it and make it
  meaningless. Still survivor-only, but the benchmark shares that bias.

## Caveats
- **Survivorship bias — the big one.** The universe is *today's* ~900-name Russell 1000,
  so names that delisted, went bankrupt, or were acquired out of distress are absent.
  Every bucket's return is inflated, and the danger of cheap-junk is *understated* — the
  worst value traps already left the sample. This is why the cheap × low-quality 2×2 cell
  can't be trusted and why trap-avoidance can't be validated here at all. Relative,
  same-universe comparisons (score-vs-score, screen-vs-universe) are far more trustworthy
  than any absolute number.
- **Restated statements**, not true point-in-time (as-filed fundamentals barely
  change retroactively, but this isn't an academic backtest).
- Three start windows over one broadly-rising decade; a bear-heavy span would test
  the downside protection the value tilt is supposed to provide.
- Score buckets ≠ the exact screen (which also requires rating BUY, not-suspect, and
  a deep EDGAR re-verify) — but the ≥80 slice is a close proxy and tells the same story.

## Can we strengthen the margin-of-safety signal in the score?

The result above (cheapness is the strongest raw slice) suggests weighting the
valuation pillar more heavily. **The backtest says: don't — it makes the tool
worse.** Re-scoring all 2,241 observations with different pillar weights and
measuring the top-quintile vs bottom-quintile 5-yr return gap:

| pillar weighting (val, quality, growth, strength, inflation, margins) | winner-vs-loser edge |
|---|--:|
| **balanced — current [30, 20, 15, 15, 10, 10]** | **+19.3 pt** |
| deep_value [45, 12, 8, 15, 10, 10] | +7 pt |
| valuation-heavy [42, 14, 10, 16, 10, 8] | +10 pt |
| valuation-dominant [55, 10, 8, 12, 10, 5] | +8 pt |

A full grid search over valuation ∈ [26–38] and the quality/growth/strength weights
finds **no weighting that beats the current balanced set** — every shift toward
valuation lowers the edge. Steepening the valuation *curve* to reward only the
deep-value tail (max points at ≥45% upside instead of ≥35%) also lowered it
(15.5 vs 17.1 pt).

**Why:** adding quality weight widens the composite's top-vs-bottom **quintile spread**
— it pushes expensive, low-quality names down where they belong, which is what lifts the
winner-vs-loser edge. Note the careful wording: the grid search shows quality helps the
*composite's ranking*, **not** that it lifts returns *within* the cheap bucket — the 2×2
above shows the opposite on survivors. The quality filter's real job — keeping the cheap
names that go to zero out of the buy list — is exactly what a survivor sample can't
measure, so it's justified on the quintile spread and on first principles, not on a
within-cheap return lift. The "quality at a fair price" combination still beats either
lever alone on the composite, and the current balance is already at the sweet spot.

**So the margin of safety is best strengthened not in the score, but at the GATE** —
which this codebase already does: the buy-below rating gate (a BUY must trade below
its certainty-scaled fair value) and the value-trap / suspect guards enforce exactly
the "buy cheap, avoid the traps" discipline the data rewards. Weighting the score
toward cheapness would double-count valuation and, per the backtest, hurt.

## Downside protection — the 2022 bear (does the value tilt cushion a drawdown?)

The multi-window runs above all landed in a rising decade, so they can't test the
other half of the value promise: protection when the market falls. The one clean
downturn in the EDGAR era is **2022**. Scoring the `full` universe **as of 2021**
(a market peak) and measuring the **2022** total return:

| as-of-2021 margin of safety | 2022 return (median) |
|---|--:|
| cheap (upside ≥ 20%) | **+14%** |
| fair (0–20%) | +10% |
| expensive (0 to −30%) | +2% |
| very expensive (< −30%) | **−14%** |

**A clean, monotonic gradient: the cheaper a name was going in, the better it held up.**
Names bought with a real margin of safety were *up* in a down year while the expensive
ones fell double digits. That is exactly the downside protection value investing
promises — and it comes from the *price discipline*, not the quality score:

- The composite **score did NOT protect** — names scoring ≥65 as of 2021 fell −16% in
  2022, *worse* than the <45 names (−9%). At a market peak "high score" mostly catches
  fairly-valued quality/growth, which was hit hardest in the 2022 de-rating.

**This closes the loop with the bull-market result.** The margin of safety is the edge
on the way up *and* the cushion on the way down; the quality-weighted score is a filter,
neither an upside forecast nor a downside shield. (Caveat: at a 2021 peak few names score
high or screen cheap, so the high-score buckets here are thin — n=1–8; the margin-of-safety
gradient rests on the well-populated n≈189 split and is the meaningful signal.)
