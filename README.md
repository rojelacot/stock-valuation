# Long-Term Value Screener

A local web app that values a stock the way a patient, index-ignoring value investor would:
pull ~10 years of fundamentals, run a discounted-cash-flow intrinsic value, score business
quality / growth / balance-sheet strength, check whether the expected 10–15yr return beats
inflation, layer in an AI qualitative read (moat, management, risks), and output a
**Buy / Hold / Avoid** verdict — with all the reasoning shown.

> ⚠️ For research and education only. Not investment advice. Data comes from Yahoo Finance's
> public JSON endpoints and can be delayed or incomplete — **the free tier only returns ~4 years
> of annual financial statements** (price history is a full 10 years). DCF and expected-return
> numbers are model estimates sensitive to assumptions. Always do your own due diligence.
>
> For a true 10-year statement history, swap in a paid data source — see "Swapping the data
> source" below. Nothing downstream touches Yahoo directly, so it's a one-file change.

## Philosophy baked in
- **Hold 10–15 years**, so durability and returns on capital matter more than this quarter.
- **Beat inflation** (~3% bar) — the benchmark is inflation, *not* an index.
- **Margin of safety** — prefer paying ≤ 75% of estimated intrinsic value.
- **Quality first** — high ROIC, real growth, strong balance sheet, expanding margins.

## Quick start
```bash
cd stock-valuation
# (one-time) create venv + install
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# (optional) enable Claude's qualitative analysis
cp .env.example .env    # then paste your ANTHROPIC_API_KEY into .env

# run it
./run.sh                # or: .venv/bin/uvicorn main:app --app-dir backend --port 8000
```
Then open http://127.0.0.1:8000 and type a ticker (AAPL, MSFT, KO, GOOGL, JNJ, BRK-B…).

## Three modes
- **Analyze one** — full report for a single ticker (verdict, DCF, scorecard, charts, AI read).
- **Compare** — up to 15 tickers ranked side-by-side under the same assumptions.
- **Weekly buy screen** — scans a curated universe of ~55 quality names and surfaces the
  ones clearing your buy bar (score ≥ 70 by default). Run it about once a week.

Every mode obeys the shared **valuation-assumptions** panel — drag the sliders (discount rate,
terminal growth, projection years, inflation hurdle, margin of safety) and the single-ticker view
recomputes live (no re-fetch, no re-call to Claude).

## What each part does
| File | Role |
|---|---|
| `backend/data.py` | Fetch + normalize price history (10yr) + financials (~4yr) + key stats, direct from Yahoo's JSON endpoints via `curl_cffi` |
| `backend/valuation.py` | Growth CAGRs, ROE/ROIC, margins, **DCF**, multiples, expected return — all under tunable assumptions |
| `backend/scoring.py` | 6-pillar composite score → Buy/Hold/Avoid with reasons |
| `backend/qualitative.py` | Claude's moat / management / risk assessment (optional) |
| `backend/main.py` | FastAPI: `/api/analyze`, `/api/compare`, `/api/screen` + serves the dashboard |
| `frontend/` | Single-page dashboard, 3 tabs (Tailwind + Chart.js via CDN, no build step) |
| `weekly_screen.py` | Headless weekly screen — prints the buy list and saves a dated report to `reports/` (no server needed) |

## Screening universes
The screener can scan four preset universes (or your own pasted list):

| Scope | Size | Source | Typical time |
|---|--:|---|---|
| `core` | ~57 | hand-picked compounders | ~1 min |
| `full` | ~207 | quality large/mid-caps, all sectors (`universe.py`) | ~5–7 min |
| `large` | ~900 | **S&P 500 + S&P 400 ≈ Russell 1000** (`large_universe.py`) | ~10 min |
| `all` | ~1,950 | **all US-listed common stocks** with a market-cap/price floor | ~25–30 min |

- The **`all`** scope pulls every US-listed common stock (~5,000, `all_us_symbols.py`), then applies a
  cheap batched market-cap lookup to keep only names ≥ $2B and ≥ $5 (≈1,950) before any deep analysis —
  so it never wastes the expensive per-stock work on penny stocks. Big scans **checkpoint to disk**
  (`reports/.scan_checkpoint.json`) and resume if interrupted.
- The in-app *Weekly buy screen* tab offers Core / Full / Large. The **`all`** scope is CLI/weekly-job
  only (too long for an interactive request).
- Refresh the generated lists when index membership drifts:
  `python tools/refresh_universe.py` (large) and `python tools/refresh_symbols.py` (all).

## Weekly buy list (headless + scheduling)
Run the screen from the terminal any time — it doesn't need the web server:
```bash
.venv/bin/python weekly_screen.py                    # large (~900), buy bar 70
.venv/bin/python weekly_screen.py --scope all        # all US-listed above the floor
.venv/bin/python weekly_screen.py --scope core       # quick ~57-name check
.venv/bin/python weekly_screen.py AAPL MSFT KO       # your own list
```
It prints the candidates and writes `reports/screen-YYYY-MM-DD.md`. The installed **LaunchAgent**
(`launchd/…weeklyscreen.plist`, loaded in `~/Library/LaunchAgents/`) runs `--scope all` every
**Monday 8am**. Edit the plist's `StartCalendarInterval` / `--scope` and reload
(`launchctl unload … && launchctl load -w …`) to change the time or breadth.

## Earnings quality & the capex-cycle distortion
Capital expenditure hits earnings *slowly* (as depreciation) but hits cash *immediately*. So when
a company is building hard — as the AI hyperscalers are in 2025–26 — two opposite distortions appear:

- **Reported earnings look flattered** → trailing **P/E looks artificially cheap** (today's depreciation
  lags today's spend).
- **Free cash flow looks depressed** → a naive FCF-DCF makes the same company look **too expensive**.

To handle this the app:
- Computes the **capex-to-depreciation ratio** (the diagnostic) and flags a heavy build phase — but only
  when capex is a *material* share of operating cash flow (≥30%), so asset-light staples aren't false-flagged.
- Splits capex into **maintenance vs growth** (Greenwald's PP&E-to-sales method, with a depreciation floor).
- Computes **owner earnings** = net income + D&A − *maintenance* capex (Buffett's measure — it credits
  growth capex instead of penalizing it).
- Runs the **DCF twice** and shows intrinsic value as a **range**: *conservative* (discounts free cash flow)
  → *adjusted* (discounts owner earnings). The width of that range **is** the capex distortion, made visible.
  The verdict scores off the **midpoint**, so heavy-capex names are neither unfairly punished nor flattered.
- Adds an **Earnings quality & capex cycle** panel (capex/depreciation, maintenance/growth split, owner
  earnings vs net income, cash conversion) and a ⚠ caveat on the P/E tile when a build-out is detected.

See `backend/earnings_quality.py`.

## Broad-universe guardrails
Scanning thousands of names surfaces business types and data quirks the DCF can't handle. Three
guardrails keep the ranking honest (see `data.py` and `valuation.py`):

1. **Currency.** Statements come in the company's *reporting* currency but price is in the *trading*
   currency. For ADRs/foreign listings these differ (e.g. TSM reports TWD, trades USD), which would
   otherwise blow up the DCF. We detect `financialCurrency`, fetch the FX rate, and convert monetary
   figures to the trading currency — or flag the stock if we can't.
2. **Financials & REITs.** Banks, insurers, brokers and REITs have no meaningful "free cash flow", so
   an FCF-DCF wildly misprices them. These are detected by sector/industry and valued on **earnings
   power** (normalized net income) instead.
3. **Sanity caps.** Growth is extrapolated cautiously (tighter stage-1 cap, extra caution when only
   ~4yr of history exists), and any DCF implying **>100% upside** is flagged *suspect* — its valuation
   pillar is capped, it's downgraded out of BUY, and it's excluded from the buy-candidate list (a
   liquid large-cap rarely trades at half its conservative fair value, so it's almost always an
   artifact). Suspect names still appear in the full ranked list, marked 🚩, for you to verify.

## Conviction refinements
On top of the guardrails, four adjustments make the ranking more trustworthy:

1. **Stock-based comp (SBC) is subtracted from free cash flow.** OCF adds SBC back as "non-cash",
   but it's a real dilution cost — so SBC-heavy names (much of tech) no longer look cheaper than they
   are. The earnings-quality panel shows SBC and its share of operating cash flow.
2. **Risk-adjusted discount rate.** Small-cap, high-volatility, high-leverage, and emerging-market
   names get a discount-rate premium (up to +5%), so riskier businesses must clear a bigger margin of
   safety. Shown in the DCF assumptions.
3. **Cyclical-peak flag.** When current margins/ROE run well above the company's own multi-year
   average (insurers, energy, autos), the report flags that earnings may be at a peak, not durable.
4. **AI read on the shortlist.** The weekly job runs Claude's moat / management / risk / verdict on
   each buy candidate (needs `ANTHROPIC_API_KEY`; `--no-ai` to skip) — the value-trap and moat
   judgment the pure-quant screen can't provide. Plus a **week-over-week diff** (🆕 new / ❌ dropped
   candidates vs the previous run).

**Still not a substitute for your own diligence** — see the conviction checklist in the app footer /
your notes: why is it cheap, is the growth a peak, how much is SBC, is there a durable moat, do you
understand the business, and size positions as a basket.

## How the verdict is scored (100 pts)
| Pillar | Max | Looks at |
|---|---|---|
| Valuation & margin of safety | 30 | Upside to DCF intrinsic value |
| Business quality | 20 | ROIC, ROE |
| Growth durability | 15 | Revenue / EPS / FCF CAGR |
| Financial strength | 15 | Debt/equity, interest coverage, net cash |
| Beats inflation (10–15yr) | 10 | Expected annual return vs 3% |
| Profitability & margins | 10 | Net/gross margin level + trend |

≥70 → **Buy** · 50–69 → **Hold/Watch** · <50 → **Avoid**

## Tuning
Assumptions (discount rate, terminal growth, inflation hurdle, margin of safety) live at the
top of `backend/valuation.py` and `scoring.py`. Change them to match your own required return.

## Swapping the data source later
`data.py` returns a normalized dict; nothing downstream touches yfinance directly. To move to
Financial Modeling Prep / Tiingo / Polygon, reimplement `fetch_stock()` to return the same shape.
