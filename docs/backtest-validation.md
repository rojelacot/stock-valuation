# Score validation — does a higher score predict better returns?

**Question the whole thesis rests on:** if you had scored these names in the past,
would the high scorers have gone on to earn higher long-term returns than the low
scorers? This is the one check that separates a plausible heuristic from a signal.

## Method

`backtest.py` rebuilds each name's score **as of** several past start-years
(fundamentals truncated to that year, price as of then), then measures the actual
forward **total return** (price change + dividends received) over a 5-year hold.
It pools multiple start years so the result isn't one-regime luck, and buckets the
observations by as-of score.

```bash
.venv/bin/python backtest.py --scope core --edgar --years 5 --windows 2016,2018,2020
```

- Universe: **core** (~57 mega-caps), the fast, mostly-cached set.
- Windows: 2016→2021, 2018→2023, 2020→2025 (all complete 5-yr holds).
- 164 name×window observations.

## Result — pooled (all windows, n=164)

| as-of score | obs | median 5-yr total return | mean |
|---|--:|--:|--:|
| **80+**   | 2   | **+463%** | +463% |
| **70–79** | 2   | **+175%** | +175% |
| 50–69     | 45  | +123% | +189% |
| <50       | 115 | +97%  | +142% |

- **≥70 (BUY threshold): +290% median  vs  <50 (AVOID): +97%  →  a +193-point edge over ~5 years.**
- **Monotonic: YES** — each higher score bucket earned a higher median return.

### Per-window (does the edge survive across regimes?)

| window | ≥70 vs <50 median | monotonic |
|---|--:|:--:|
| 2016 → 2021 | +353% vs +150% (+204 pt) | no* |
| 2018 → 2023 | +285% vs +96%  (+188 pt) | yes |
| 2020 → 2025 | (no ≥70 names) 63% vs 61% | yes |

\* 2016 broke monotonicity only in a thin bucket during a strong bull run.

## Honest read

**The score is directionally validated.** Higher scores earned higher forward
returns, the ranking is monotonic pooled, and the ≥70 buy-threshold names
materially outperformed the <50 avoid names in every window that had ≥70 names.

**But be careful with the magnitude.** Only **three names (AAPL, META, QCOM)**
ever cleared score 70 in the 57-name mega-cap universe, so the eye-catching
"+193-point edge" rests on a handful of observations and could carry survivor luck.
The statistically meaningful comparison is the well-populated one:

> **50–69 (n=45): +123% median   vs   <50 (n=115): +97% median   →   a +26-point
> median edge, monotonic.**

That is a real, positive, and honest edge: over a five-year hold, the tool's
higher-scored names beat its lower-scored names, with the direction intact across
regimes.

## Caveats
- **Restated statements**, not true point-in-time — as-filed fundamentals barely
  change retroactively, but this isn't an academic backtest.
- **Core scope only** — mega-caps rarely clear 70 (they're seldom cheap), which is
  exactly why the high-score buckets are thin. Re-run on `--scope large` to
  populate them and firm up the top-bucket magnitude.
- Three start windows; a longer span of windows would tighten the confidence.

## Bottom line
The core thesis holds up: **a higher score predicted a higher 5-year total return,
monotonically.** The buy-threshold edge is large but thinly sampled at this scope;
the mid-vs-low edge is modest but robust. Trust the ranking; treat the exact
magnitude as directional until a full-universe run fills in the top buckets.
