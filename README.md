# Long-Term Value Screener

A local web app that values a stock the way a patient, index-ignoring value investor would:
pull ~10-19 years of fundamentals, estimate intrinsic value with the model that fits the
business (DCF, justified-P/E earnings-power, two-stage price-to-book, or FFO — routed by SIC
code, [see below](#valuation-models--the-right-one-per-business)), score business
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
- **The margin of safety is both the edge and the cushion.** A backtest of ~900 names
  ([`docs/backtest-validation.md`](docs/backtest-validation.md)) says it plainly: buying
  *below* a certainty-scaled fair value is what separated winners from losers over 5 years
  **and** what protected capital in the 2022 bear — names cheap going in were *up* ~14% that
  year while expensive ones fell ~14%, a clean monotonic gradient. Price discipline carries
  the return and the downside protection; the quality score, on its own, does neither.
- **Margin of safety that scales with certainty** — from ~12% below intrinsic value for a
  fortress compounder up to ~45% for the least certain names.
- **Quality is the filter, not the ranker.** High ROIC, real growth, a strong balance sheet
  and expanding margins *don't* predict returns on their own — expensive quality mean-reverts.
  They earn their keep by ranking expensive junk *down* the composite, and — on a principle the
  backtest can't test — by keeping the cheap names that go to zero off the buy list. A
  survivor-only backtest actually shows cheap-**and-ugly** winning (mean reversion among the
  names that lived), because the traps quality is meant to catch already delisted and are missing
  from the sample. So "quality at a fair price" is a discipline, not a proven return lift; the
  guardrail against **value traps** is worth keeping precisely where the data can't vouch for it.
- **A shrinking business is never a bargain.** Cheap-and-declining is a value trap, not a margin
  of safety: a sustained revenue decline is flagged and kept out of the buy list, however good the
  trailing margins and ROE still look.
- **The score is a filter, not a forecast.** Use it to weed out low-quality and richly-priced
  names; the buy-below gate — a BUY must trade below fair value — does the actual work.

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

## Run with Docker (self-host)
Runs anywhere Docker does — no Python setup, one command. Each instance is a single
user, so it stays within the data sources' free personal-use limits.
```bash
cp .env.example .env     # optional — add SEC_EDGAR_UA and any keys you have
docker compose up -d     # build + run; web UI at http://localhost:8000
```
- **No keys required** — it works on SEC EDGAR + Yahoo out of the box. Each key in `.env`
  just lights up more: `SEC_EDGAR_UA` (recommended SEC contact), `SIMFIN_API_KEY` (foreign
  filers + the data cross-check), `ANTHROPIC_API_KEY` (the AI read).
- **State persists** in `./reports/` (watchlist, weekly track record, dated reports) via a
  bind-mounted volume.
- **Weekly auto-screen** (optional) — the cross-platform replacement for the macOS launchd
  job: `docker compose --profile scheduler up -d` runs `weekly_screen.py --scope large`
  every Monday 8am (tune via `TZ` / `SCHEDULE_HOUR` / `SCHEDULE_WEEKDAY` / `SCREEN_SCOPE`).
- **Run a screen by hand:** `docker compose exec app python weekly_screen.py --scope large`.

> Self-host only. This tool scrapes Yahoo's public endpoints for price/market data, which is
> fine for personal use but **not licensed for redistribution** — don't run it as a public,
> multi-user service without swapping in a licensed data provider in `backend/data.py`.

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
- **Intrinsic value** — conservative (FCF) → adjusted (owner-earnings) DCF, scored off the midpoint; smoothly blends into a **justified-P/E earnings-power** model for mature wide-moat compounders (where a strict DCF extrapolates to an artifact), a **two-stage justified price-to-book** for banks/insurers, and **FFO** for REITs. The model is chosen by SIC code — [see Valuation models](#valuation-models--the-right-one-per-business).
- **Quality trajectory** — ROIC, margins and free cash flow over the full history, read as *widening moat / mixed / eroding*.
- **Decision strip** — buy-zone status vs the certainty-scaled buy-below, the fair-value gap, and expected return vs inflation, right under the verdict.
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

## Valuation models — the right one per business
No single model fits every company, so each name is routed to the model its economics call for.
**Routing is deterministic**: it keys off the company's **SEC SIC code** (the industry the SEC has on
file, fetched from EDGAR and cached), not a data-provider sector label that can arrive differently
from one fetch to the next and flip a name between models. Yahoo's sector/industry is only the
fallback when no SIC is available (e.g. foreign filers).

- **Operating companies → discounted cash flow.** Two DCFs: *conservative* discounts free cash flow
  (penalizes all capex); *adjusted* discounts **owner earnings** = net income + D&A − maintenance
  capex (credits growth capex). Fair value is the range between them — the width **is** the capex
  distortion, made visible. Growth is a **log-linear (least-squares) trend** over the full earnings
  history rather than a two-point CAGR, so a single noisy or partially-restated year can't swing it.
- **Mature wide-moat compounders → justified-P/E earnings power.** A strict DCF misprices a stable,
  high-return compounder on the low side (a depressed trailing growth rate compounded through a full
  discount and a Gordon terminal reads as an artifact-low value). These are valued on **normalized
  earnings × a justified P/E** = (1 − g/ROE)/(r − g), on through-cycle ROE and a capped growth rate,
  with the multiple clamped so it can never justify a bubble. The **DCF ↔ earnings-power handoff is a
  smooth blend, not a hard cliff**: where the two models are close the fair value is a weighted
  average, so a name whose DCF wobbles near the threshold no longer jumps between two fair values run
  to run. A **faded-from-peak guard** denies the growth premium to a business earning well below its
  multi-year earnings peak (a secular decliner isn't a compounder), and a **boom-fade rule** values a
  demand-boom name on its latest actual earnings rather than an inflated multi-year average.
- **Banks & insurers → two-stage justified price-to-book.** A financial's "free cash flow" is
  meaningless, so it's valued on the model bank/insurance analysts use — the **residual-income
  (excess-return)** form of price-to-book: value = book + the present value of `(ROE − r) × book`,
  with book compounding at a near-term rate for ~10 years before fading to the terminal rate. This
  two-stage form (vs the single-stage `(ROE − g)/(r − g)`, which can only use a terminal growth below
  the discount rate) correctly credits a **fast-compounding** insurer like Progressive or Kinsale
  instead of under-pricing it — while a hard P/B cap and through-cycle (7-yr median) ROE keep it
  bounded and stop a cyclical peak from inflating it.
- **REITs → funds from operations (FFO).** Real-estate depreciation crushes GAAP earnings and book
  value understates the property, so both a cash-flow DCF and price-to-book mislead. REITs are valued
  on **FFO** (net income + real-estate D&A), discounted as an equity-level stream.
- **Capital-light "financials" → the operating path.** Exchanges, index/data/ratings shops, payment
  networks and asset managers earn huge returns on trivial balance sheets, so book value is
  meaningless (an asset manager is a fee stream, not its equity). By SIC they route to the ordinary
  DCF → earnings-power path instead of price-to-book.

Every model feeds the **same** downstream panel — scenarios, Monte-Carlo, and the reverse model (what
ROE/growth the price implies) — so bear/base/bull and the probability-of-undervaluation are always
computed on whichever model set the headline, never a mismatched one.

## What each part does
| File | Role |
|---|---|
| `backend/data.py` | Source orchestration + Yahoo JSON fetch (via `curl_cffi`) + normalize; EDGAR/SimFin dispatch + fallback; bulk market-cap prefilter; free analyst/sentiment supplement |
| `backend/edgar.py` | SEC EDGAR XBRL adapter — 10–19yr as-filed 10-K statements (default single-stock source), same normalized shape |
| `backend/simfin.py` | SimFin v3 adapter — fallback for names EDGAR doesn't cover (~7yr statements) |
| `backend/valuation.py` | Growth trends, ROE/ROIC, margins, **model routing** (DCF ↔ earnings-power blend, book-value, FFO), **DCF value range**, scenarios, reverse, sensitivity grid, Monte-Carlo, expected return |
| `backend/earnings_power.py` | Justified-P/E earnings-power model for mature compounders (value / scenarios / Monte-Carlo / reverse) |
| `backend/financials.py` | Two-stage justified price-to-book (residual income) + FFO for banks/insurers/REITs |
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
It prints the candidates and writes `reports/screen-YYYY-MM-DD.md`. Like the web app, it runs a
**two-pass screen**: a fast Yahoo sweep of the whole universe, then a deep-verify of every name
within 15 points of the bar on EDGAR (10–19yr as-filed) + SimFin — so candidate scores match the
single-stock Analyze view and the SimFin-vs-Yahoo cross-check downgrades unreliable foreign filers
out of the buy list. The installed **LaunchAgent**
(`launchd/…weeklyscreen.plist`, loaded in `~/Library/LaunchAgents/`) runs `--scope large`
(~900 curated large-caps) every **Monday 8am** — it finishes in ~30–40 min, whereas
`--scope all` (~2,000 names) gets rate-limited by Yahoo into a multi-hour crawl. Edit the
plist's `StartCalendarInterval` / `--scope` and reload
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
2. **Financials & REITs.** Banks/insurers/brokers and REITs have no meaningful "free cash flow", so
   an FCF-DCF wildly misprices them. Which model each gets is decided by its **SEC SIC code**
   (deterministic — a provider's sector label can arrive differently between fetches and flip the
   model): banks/insurers on a **two-stage justified price-to-book** (residual income on through-cycle
   ROE, crediting near-term book compounding so a fast-grower isn't under-priced), REITs on **FFO**.
   Capital-light "financials" — exchanges, index/data/ratings shops, payment networks, asset managers,
   insurance brokers — carry trivial balance sheets, so book value is meaningless; by SIC they route
   to the ordinary DCF → earnings-power path instead. See [Valuation models](#valuation-models--the-right-one-per-business).
3. **Sanity caps.** Growth is extrapolated cautiously (tighter stage-1 cap, extra caution when only
   ~4yr of history exists), and any DCF implying **>100% upside** is flagged *suspect* — its valuation
   pillar is capped, it's downgraded out of BUY, and it's excluded from the buy-candidate list (a
   liquid large-cap rarely trades at half its conservative fair value, so it's almost always an
   artifact). Suspect names still appear in the full ranked list, marked 🚩, for you to verify.
   The mirror image is caught too: when a profitable, stable-or-growing mature business's DCF implies
   an **implausibly low** fair value (below ~7× normalized earnings) and the earnings-power model
   doesn't cover it, the DCF is flagged a likely artifact rather than presented as a real bear case.

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
`backtest.py` rebuilds each name's score **as of several past start-years** (statements truncated to
that year, price then, look-ahead fields blanked) and measures the actual forward **total return**
(price change **+ dividends received**) over the horizon, bucketed by score. Pooling multiple
regime-diverse windows and counting dividends avoids the two biases of a naive test — one lucky
5-year stretch, and denying the high-yield financials/REITs the dividends they actually pay. It
leads with the **median** (outlier-robust) and reports the edge both pooled and per-window.
```bash
.venv/bin/python backtest.py --scope large --edgar                      # 3 windows, total return
.venv/bin/python backtest.py --scope large --edgar --windows 2016,2018,2020 --years 5
.venv/bin/python backtest.py --scope core --edgar                       # quick, small sample
```
Honest read (large universe, 3 windows 2016/2018/2020, total return): the **top bucket (score ≥80,
the buy bar) is consistently the best (~+106% median vs ~+77% for &lt;50)**, but the aggregate
≥70-vs-&lt;50 edge is **modest (~+5 pts) and regime-dependent** — negative in the 2016→2021 growth run,
positive in 2018→2023 and 2020→2025. The signal is real and concentrated at the high-conviction end,
not a blanket "higher score = higher return" across every bucket. Directional (restated statements,
not point-in-time), not an academic backtest.

## Tests
All offline (no network); each exits non-zero on failure.
- `python tests/smoke.py` — robustness: the valuation/scoring pipeline must survive degenerate data
  (empty/zero/negative/missing) without crashing and keep scores in range.
- `python tests/test_features.py` — targeted feature checks (e.g. charge-robust earnings stability).
- `python tests/test_regressions.py` — one case per valuation-integrity fix (share-scale repair,
  split-adjusted dilution, the uniform >100% suspect cap, the buy-zone rating gate, the
  insurance-float DCF cap, the ADR currency-mismatch flag, and the share-count reconciliation), so
  none can silently come back.

## Tuning
Assumptions (discount rate, terminal growth, inflation hurdle, margin of safety) live at the
top of `backend/valuation.py` and `scoring.py`, or drag the sliders live in the app.

## Data sources
- **Single-stock Analyze** uses **SEC EDGAR** — the XBRL `companyfacts` API returns 10–19 years of
  as-filed 10-K line items for any US filer, free and without a key — for the statement history, plus
  the company's **SIC code** (from the EDGAR submissions endpoint) that deterministically routes it to
  the right valuation model. It pairs this with **Yahoo** for live price, market cap, ratios and
  analyst sentiment. This hybrid is the deepest free data available and is the default (`backend/edgar.py`).
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
