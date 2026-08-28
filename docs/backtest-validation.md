# Score validation — does a higher score predict better returns?

**The question the whole tool rests on:** if you had scored these names in the
past, would the high scorers have earned higher long-term returns than the low
scorers? This is what separates a real signal from a plausible-looking heuristic.

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

## Caveats
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

**Why:** pure cheapness pulls in value traps, distressed names and cyclically-
depressed junk that never recover. The quality pillars — negatively correlated with
returns *in isolation* — earn their keep as a **filter** on the cheap names. The
"quality at a fair price" combination beats either lever alone, and the current
balance is already at the sweet spot.

**So the margin of safety is best strengthened not in the score, but at the GATE** —
which this codebase already does: the buy-below rating gate (a BUY must trade below
its certainty-scaled fair value) and the value-trap / suspect guards enforce exactly
the "buy cheap, avoid the traps" discipline the data rewards. Weighting the score
toward cheapness would double-count valuation and, per the backtest, hurt.
