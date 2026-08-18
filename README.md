# Long-Term Value Screener

A local web app that values a stock the way a patient, index-ignoring value investor would:
pull ~10 years of fundamentals, run a discounted-cash-flow intrinsic value, score business
quality / growth / balance-sheet strength, check whether the expected 10–15yr return beats
inflation, layer in an AI qualitative read (moat, management, risks), and output a
**Buy / Hold / Avoid** verdict — with all the reasoning shown.

> ⚠️ For research and education only. Not investment advice. The single-stock Analyze view pulls
> **10–19 years of as-filed 10-K fundamentals straight from SEC EDGAR** (free, no key) and pairs
> them with Yahoo Finance for live price, market data and analyst sentiment. DCF and expected-return
> numbers are model estimates sensitive to assumptions. Always do your own due diligence.
>
> Foreign filers (which file 20-F, not 10-K) and the bulk screener fall back to Yahoo's ~4-year
> free statement history; an optional SimFin key adds ~7yr for those names. Nothing downstream is
> coupled to a single provider — sources are swappable in `backend/data.py`.

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

## Four modes
- **Analyze one** — the full single-stock report (see the feature list below).
- **Compare** — up to 15 tickers ranked side-by-side under the same assumptions.
- **Weekly buy screen** — scans a universe (Core/Full/Large) and surfaces names clearing your
  buy bar (score ≥ 80 by default), grouped by sector, with a week-over-week diff.
- **Watchlist** — save any analysis with notes/buy-price/shares; tracked live against its
  buy-below price, with a portfolio roll-up. See "Watchlist & portfolio" below.

Every mode obeys the shared **valuation-assumptions** panel — drag the sliders (discount rate,
terminal growth, projection years, inflation hurdle, margin of safety) and the single-ticker view
recomputes live (no re-fetch, no re-call to Claude).

## What the single-stock analysis shows
- **Verdict** — 0–100 score → Buy / Hold / Avoid, with the reasoning and strengths/watch-outs.
- **Intrinsic value range** — conservative (FCF) → adjusted (owner-earnings) DCF; scores off the midpoint.
- **Scenarios & reverse DCF** — bear/base/bull fair values, a discount-rate × growth **sensitivity grid**,
  and the growth rate the current price *implies* ("what does the market expect?").
- **Monte-Carlo intrinsic value** — 2,000 simulations sampling growth, discount rate, terminal growth
  and starting cash flow, reported as a P10–P90 fair-value range and the **probability the price is
  below fair value** — the honest alternative to a single false-precision "worth $X".
- **Forensic checks** — **Altman Z-score** (distance-to-bankruptcy) and **Beneish M-score** (earnings-
  manipulation profile). A distress or manipulation flag docks the numeric score and blocks a Buy —
  the failure modes a DCF and a quality score miss. Suppressed for financial firms (where they're invalid).
- **Valuation multiples & capital efficiency** — P/E, forward P/E, PEG (trailing proxy when needed),
  P/B, P/FCF, P/S, EV/EBITDA·EBIT·Sales, FCF yield, **ROIC (NOPAT) vs WACC** value-creation spread,
  **Piotroski F-Score**, shareholder yield / payout, dilution, **valuation vs its own history**.
- **Earnings quality & capex cycle** — capex-to-depreciation, maintenance/growth capex split, owner
  earnings vs net income, cash conversion, SBC, accruals.
- **Analyst view & sentiment** — price targets, coverage, recommendation, short interest, net insider
  activity (free Yahoo data).
- **Dividend safety** — DPS, dividend growth, FCF coverage, payout (for payers).
- **Trends** — price, revenue, FCF, EPS/ROE, margins, and ROIC/ROE over time.
- **AI read (Claude)** — moat, management, risks, bull/bear, catalysts, investment thesis, thesis-breakers.
- **Peers & sector benchmarking** — same-sector comparison and metrics vs the sector median.
- **Primary sources** — direct links to the 10-K / 10-Q / DEF 14A / transcripts, plus what to check yourself.

## What each part does
| File | Role |
|---|---|
| `backend/data.py` | Source orchestration + Yahoo JSON fetch (via `curl_cffi`) + normalize; EDGAR/SimFin dispatch + fallback; bulk market-cap prefilter; free analyst/sentiment supplement |
| `backend/edgar.py` | SEC EDGAR XBRL adapter — 10–19yr as-filed 10-K statements (default single-stock source), same normalized shape |
| `backend/simfin.py` | SimFin v3 adapter — fallback for names EDGAR doesn't cover (~7yr statements) |
| `backend/valuation.py` | Growth CAGRs, ROE/ROIC, margins, **DCF value range**, scenarios, reverse DCF, sensitivity grid, **Monte-Carlo DCF**, expected return |
| `backend/duediligence.py` | EV multiples, NOPAT-ROIC vs **WACC**, Piotroski, capital returns, dividend safety, valuation-vs-history, accruals |
| `backend/forensics.py` | **Altman Z** (distress) + **Beneish M** (manipulation) forensic scores; suppressed for financials |
| `backend/earnings_quality.py` | Capex-cycle / owner-earnings / cash-conversion analysis |
| `backend/scoring.py` | 6-pillar composite score → Buy/Hold/Avoid with reasons + guardrail flags |
| `backend/qualitative.py` | Claude's moat / management / risks / catalysts / thesis (optional) |
| `backend/watchlist.py` + `diffstate.py` | Persistent watchlist/journal + week-over-week candidate diffs |
| `backend/main.py` | FastAPI: `/api/analyze`, `/api/compare`, `/api/screen`, `/api/peers`, `/api/watchlist` + serves the dashboard |
| `frontend/` | Single-page dashboard, 4 tabs (Tailwind + Chart.js via CDN, no build step) |
| `weekly_screen.py` | Headless weekly screen — buy list + AI reads + diff + watchlist alerts, saved to `reports/` |
| `backtest.py` | Median-weighted validation — as-of score vs forward return |
| `tests/smoke.py` | Offline robustness tests for the valuation/scoring pipeline |

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

## Watchlist & portfolio
Save any analysis to the **⭐ Watchlist** tab with your notes/thesis, a buy price, share count, and
an owned flag. Each entry is checked live against its buy-below price and flagged **IN BUY ZONE**
when it drops in. Owned positions roll up into a **portfolio** view: market value / cost / unrealized
gain (with shares) or equal-weighted, sector allocation, value-weighted quality score, and a
concentration/diversification read. The weekly job also flags watchlist names in the buy zone.
State lives in `reports/watchlist.json`.

## Backtest / validation
`backtest.py` rebuilds each name's score **as of ~N years ago** (statements truncated to that year,
price then, look-ahead fields blanked) and measures the actual forward return, bucketed by score.
It leads with the **median** (outlier-robust) and reports whether higher scores earned higher returns.
```bash
.venv/bin/python backtest.py --years 2                 # core universe, Yahoo
.venv/bin/python backtest.py --years 3 --scope full    # deeper lookback
.venv/bin/python backtest.py --years 5 --edgar         # SEC EDGAR's 10–19yr (free, deepest)
.venv/bin/python backtest.py --years 3 --simfin        # SimFin's 7yr (spends credits)
```
Directional only (restated statements, no dividends), but across runs it consistently shows the
score sorts stocks by typical forward return.

## Tests
`python tests/smoke.py` runs offline robustness checks — the valuation/scoring pipeline must survive
degenerate data (empty/zero/negative/missing) without crashing and keep scores in range.

## Tuning
Assumptions (discount rate, terminal growth, inflation hurdle, margin of safety) live at the
top of `backend/valuation.py` and `scoring.py`, or drag the sliders live in the app.

## Data sources
- **Single-stock Analyze** uses **SEC EDGAR** — the XBRL `companyfacts` API returns 10–19 years of
  as-filed 10-K line items for any US filer, free and without a key — for the statement history, and
  pairs it with **Yahoo** for live price, market cap, ratios and analyst sentiment. This hybrid is
  the deepest free data available and is the default (`backend/edgar.py`).
- **Fallback order** when EDGAR doesn't cover a name (e.g. a foreign private issuer that files 20-F):
  **SimFin** if `SIMFIN_API_KEY` is set (~7yr), else **Yahoo** (~4yr). Fully automatic; the source is
  shown on every result.
- **Compare and the bulk screener** always use Yahoo's cheap batched endpoints (an EDGAR fetch per
  name across a thousand-name universe would be far too heavy).
- **SEC etiquette:** EDGAR asks for a descriptive `User-Agent` with a contact — set `SEC_EDGAR_UA`
  in `.env` (default works out of the box). We stay well under SEC's 10 req/sec limit.
- `data.py` returns a normalized dict; nothing downstream depends on a specific provider. To add
  another source (FMP / Tiingo / EODHD), implement a `fetch_stock()` returning the same shape — see
  `edgar.py` / `simfin.py` as templates.
- The backtest can run on EDGAR's deep history with `--edgar` — the fix for the short-lookback data
  starvation that Yahoo's ~4yr window causes.
