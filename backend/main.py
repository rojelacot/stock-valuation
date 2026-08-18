"""FastAPI app: analyze + compare endpoints, serves the static frontend.

Caching strategy: raw Yahoo data and the (expensive) AI qualitative read are
cached per-ticker, while the quantitative metrics/verdict are recomputed on
every request. That makes tuning DCF assumptions from the UI instant — no
re-fetch, no re-call to Claude — while still reflecting the new numbers.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from data import fetch_stock, fetch_market_supplement
from valuation import compute_metrics, resolve_assumptions
from scoring import score
from qualitative import analyze
import diffstate
import watchlist

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Long-Term Stock Valuation")


@app.middleware("http")
async def _no_cache(request, call_next):
    """Local, frequently-revised tool: never let the browser serve a stale
    index.html or app.js, so every revision shows up on a plain reload."""
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp

_STOCK_CACHE: dict[str, dict] = {}   # ticker -> raw fetch_stock result
_AI_CACHE: dict[str, dict] = {}      # ticker -> qualitative assessment
_SUPP_CACHE: dict[str, dict] = {}    # ticker -> free Yahoo market supplement

# Fields the free Yahoo supplement always provides (analyst estimates + sentiment).
_SUPP_ALWAYS = ("forward_pe", "peg_ratio", "target_high", "target_low",
                "num_analysts", "recommendation", "recommendation_mean",
                "short_pct_float", "short_ratio", "insider_net_shares",
                "insider_buy_shares", "insider_sell_shares", "insider_period")
# Fields to fill only when the primary source didn't provide them.
_SUPP_IFNULL = ("analyst_target", "held_percent_insiders", "held_percent_institutions")


def _enrich(stock: dict) -> None:
    """Merge free Yahoo analyst estimates + sentiment into a stock's info
    in place (single-stock view only). Cached per ticker."""
    t = stock["ticker"]
    if t not in _SUPP_CACHE:
        try:
            _SUPP_CACHE[t] = fetch_market_supplement(t)
        except Exception:  # noqa: BLE001
            _SUPP_CACHE[t] = {}
    supp, info = _SUPP_CACHE[t], stock["info"]
    for k in _SUPP_ALWAYS:
        if supp.get(k) is not None:
            info[k] = supp[k]
    for k in _SUPP_IFNULL:
        if info.get(k) is None and supp.get(k) is not None:
            info[k] = supp[k]


def _get_stock(ticker: str, refresh: bool = False, use_simfin: bool = False,
               use_edgar: bool = True) -> dict:
    ticker = ticker.strip().upper()
    if not ticker or len(ticker) > 12:
        raise HTTPException(status_code=400, detail="Invalid ticker.")
    ckey = f"{ticker}:{'sf' if use_simfin else 'yh'}:{'e' if use_edgar else 'n'}"
    if not refresh and ckey in _STOCK_CACHE:
        return _STOCK_CACHE[ckey]
    stock = fetch_stock(ticker, use_simfin=use_simfin, use_edgar=use_edgar)
    if stock.get("error"):
        raise HTTPException(status_code=404, detail=stock["error"])
    # Belt-and-suspenders: reject empty payloads (no price and no statements).
    if (stock.get("info", {}).get("current_price") is None
            and not stock.get("statements", {}).get("revenue")):
        raise HTTPException(status_code=404,
                            detail=f"No data found for '{ticker}'. Check the symbol.")
    _STOCK_CACHE[ckey] = stock
    return stock


def _assumptions_from_query(discount_rate, terminal_growth, projection_years,
                            inflation_hurdle, margin_of_safety,
                            margin_normalization=None) -> dict[str, Any]:
    return resolve_assumptions({
        "discount_rate": discount_rate,
        "terminal_growth": terminal_growth,
        "projection_years": projection_years,
        "inflation_hurdle": inflation_hurdle,
        "margin_of_safety": margin_of_safety,
        "margin_normalization": margin_normalization,
    })


def _analyze_one(ticker: str, assumptions: dict[str, Any], use_ai: bool,
                 refresh: bool = False, use_simfin: bool = False,
                 use_edgar: bool = True) -> dict:
    stock = _get_stock(ticker, refresh=refresh, use_simfin=use_simfin, use_edgar=use_edgar)
    # Single-stock view (use_simfin=True) is enriched with free Yahoo estimates +
    # sentiment; bulk paths skip it to avoid extra calls.
    if use_simfin:
        _enrich(stock)
    try:
        metrics = compute_metrics(stock, assumptions)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Valuation failed for {ticker}: {e}")
    verdict = score(metrics)

    if use_ai:
        if ticker not in _AI_CACHE or refresh:
            _AI_CACHE[ticker] = analyze(stock, metrics)
        qualitative = _AI_CACHE[ticker]
    else:
        qualitative = {"available": False, "skipped": True}

    return {
        "ticker": stock["ticker"],
        "info": stock["info"],
        "metrics": metrics,
        "verdict": verdict,
        "qualitative": qualitative,
        "price_history": stock["price_history"],
        "assumptions": assumptions,
        "data_source": stock.get("data_source", "Yahoo Finance"),
        "ai_enabled": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


@app.get("/api/analyze")
def analyze_ticker(
    ticker: str,
    use_ai: bool = True,
    refresh: bool = False,
    discount_rate: Optional[float] = Query(None),
    terminal_growth: Optional[float] = Query(None),
    projection_years: Optional[float] = Query(None),
    inflation_hurdle: Optional[float] = Query(None),
    margin_of_safety: Optional[float] = Query(None),
    margin_normalization: Optional[float] = Query(None),
):
    a = _assumptions_from_query(discount_rate, terminal_growth, projection_years,
                                inflation_hurdle, margin_of_safety, margin_normalization)
    # Single-stock view may use SimFin (if a key is set); bulk paths never do.
    return _analyze_one(ticker, a, use_ai, refresh, use_simfin=True)


import universe as _universe

# Back-compat alias (weekly_screen.py historically imported this).
CURATED_UNIVERSE = _universe.CORE


def _summary_row(sym: str, a: dict[str, Any]) -> dict[str, Any]:
    """Compact one-line summary of a ticker under assumptions `a` (no AI).

    Bulk path: Yahoo-only (use_edgar=False). A per-name SEC EDGAR fetch across a
    hundreds-to-thousand-name universe would take an hour and hammer SEC — the
    screener is meant to be fast; deep EDGAR history is for the single-stock view.
    """
    r = _analyze_one(sym, a, use_ai=False, use_edgar=False)
    m, v, info = r["metrics"], r["verdict"], r["info"]
    val = m.get("valuation", {})
    eq = m.get("earnings_quality", {})
    return {
        "ticker": sym,
        "name": info.get("name"),
        "sector": info.get("sector"),
        "price": info.get("current_price"),
        "market_cap": info.get("market_cap"),
        "score": v["score"],
        "rating": v["rating"],
        # Range-midpoint (capex-adjusted) so tables match the single-stock verdict.
        "upside": val.get("upside_mid"),
        "intrinsic_value": val.get("mid"),
        "iv_low": val.get("low"),
        "iv_high": val.get("high"),
        "buy_below": val.get("buy_below"),
        "heavy_capex": eq.get("heavy_capex"),
        "suspect": val.get("suspect", False),
        "method": val.get("method"),
        "expected_return": m["expected_return"].get("expected_annual_return"),
        "beats_inflation": m["expected_return"].get("beats_inflation"),
        "roic": m["returns"].get("roic_avg") or m["returns"].get("roic_latest"),
        "revenue_cagr": m["growth"].get("revenue_cagr"),
        "trailing_pe": m["multiples"].get("trailing_pe"),
        "net_margin": m["margins"]["net"].get("latest"),
        "years_of_data": m["growth"].get("years_of_data"),
        "green_flags": v.get("green_flags", []),
        "red_flags": v.get("red_flags", []),
    }


def _scan(symbols: list[str], a: dict[str, Any], pace: float = 0.0) -> tuple[list, list]:
    rows, errors = [], []
    for sym in symbols:
        try:
            rows.append(_summary_row(sym, a))
        except HTTPException as e:
            errors.append({"ticker": sym, "error": e.detail})
        if pace:
            time.sleep(pace)  # be gentle with Yahoo on large scans
    rows.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0)))
    return rows, errors


@app.get("/api/compare")
def compare_tickers(
    tickers: str,
    discount_rate: Optional[float] = Query(None),
    terminal_growth: Optional[float] = Query(None),
    projection_years: Optional[float] = Query(None),
    inflation_hurdle: Optional[float] = Query(None),
    margin_of_safety: Optional[float] = Query(None),
    margin_normalization: Optional[float] = Query(None),
):
    """Compact side-by-side summary for a watchlist. No AI (kept fast); uses the
    same assumptions across all names so the ranking is apples-to-apples."""
    a = _assumptions_from_query(discount_rate, terminal_growth, projection_years,
                                inflation_hurdle, margin_of_safety, margin_normalization)
    symbols = [t.strip().upper() for t in tickers.replace(",", " ").split() if t.strip()]
    symbols = list(dict.fromkeys(symbols))[:15]
    if not symbols:
        raise HTTPException(status_code=400, detail="No tickers provided.")
    rows, errors = _scan(symbols, a)
    return {"rows": rows, "errors": errors, "assumptions": a,
            "ai_enabled": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@app.get("/api/screen")
def screen_universe(
    min_score: int = 80,
    universe: Optional[str] = None,
    scope: str = "core",           # 'core' (~57, fast) or 'full' (~207)
    discount_rate: Optional[float] = Query(None),
    terminal_growth: Optional[float] = Query(None),
    projection_years: Optional[float] = Query(None),
    inflation_hurdle: Optional[float] = Query(None),
    margin_of_safety: Optional[float] = Query(None),
    margin_normalization: Optional[float] = Query(None),
):
    """Scan a universe of quality names and surface those clearing the buy bar.

    Meant to be run ~weekly. `universe` (custom tickers) overrides `scope`
    ('core' or 'full'). Returns every scanned name ranked best-first, plus the
    subset at/above `min_score` (default 70 = the BUY threshold)."""
    a = _assumptions_from_query(discount_rate, terminal_growth, projection_years,
                                inflation_hurdle, margin_of_safety, margin_normalization)
    if universe:
        symbols = [t.strip().upper() for t in universe.replace(",", " ").split() if t.strip()]
    else:
        symbols = _universe.get(scope)
    symbols = list(dict.fromkeys(symbols))[:1100]
    # Pace large scans a touch to stay under Yahoo's rate limits.
    pace = 0.15 if len(symbols) > 80 else 0.0
    rows, errors = _scan(symbols, a, pace=pace)
    # Candidates must clear the bar AND not be flagged suspect (guardrails).
    candidates = [r for r in rows if (r["score"] or 0) >= min_score and not r.get("suspect")]

    # Week-over-week diff vs the last scan of this universe (skip for custom lists).
    diff = None
    if not universe:
        cur_map = {r["ticker"]: r["score"] for r in candidates}
        diff = diffstate.compute_diff(scope, cur_map, date.today().isoformat())
    return {
        "rows": rows,
        "candidates": candidates,
        "errors": errors,
        "min_score": min_score,
        "scanned": len(rows),
        "universe_size": len(symbols),
        "assumptions": a,
        "diff": diff,
        "ai_enabled": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


# ---- Background screen jobs (so the ~900-name large scan doesn't block) ----
_SCAN_JOBS: dict[str, dict[str, Any]] = {}
_SCAN_LOCK = threading.Lock()
_SCAN_JOB_TTL = 3600  # keep a finished job's results for an hour, then prune


def _prune_jobs() -> None:
    now = time.time()
    with _SCAN_LOCK:
        for jid in [j for j, v in _SCAN_JOBS.items()
                    if v.get("finished") and now - v["finished"] > _SCAN_JOB_TTL]:
            _SCAN_JOBS.pop(jid, None)


def _run_scan_job(job_id: str, symbols: list[str], a: dict[str, Any],
                  min_score: int, scope: str, is_custom: bool) -> None:
    """Worker thread: scan each symbol, updating progress; finalize on completion."""
    job = _SCAN_JOBS[job_id]
    pace = 0.15 if len(symbols) > 80 else 0.0
    for i, sym in enumerate(symbols, 1):
        if job.get("cancelled"):
            break
        try:
            row = _summary_row(sym, a)
            with _SCAN_LOCK:
                job["rows"].append(row)
        except HTTPException as e:
            with _SCAN_LOCK:
                job["errors"].append({"ticker": sym, "error": e.detail})
        except Exception as e:  # noqa: BLE001
            with _SCAN_LOCK:
                job["errors"].append({"ticker": sym, "error": str(e)})
        with _SCAN_LOCK:
            job["done"] = i
        if pace:
            time.sleep(pace)
    with _SCAN_LOCK:
        rows = sorted(job["rows"], key=lambda x: (x["score"] is None, -(x["score"] or 0)))
        candidates = [r for r in rows if (r["score"] or 0) >= min_score and not r.get("suspect")]
        diff = None
        if not is_custom and not job.get("cancelled"):
            try:
                diff = diffstate.compute_diff(
                    scope, {r["ticker"]: r["score"] for r in candidates},
                    date.today().isoformat())
            except Exception:  # noqa: BLE001
                diff = None
        job["rows"], job["candidates"], job["diff"] = rows, candidates, diff
        job["status"] = "cancelled" if job.get("cancelled") else "done"
        job["finished"] = time.time()


@app.get("/api/screen/start")
def screen_start(min_score: int = 80, universe: Optional[str] = None,
                 scope: str = "core",
                 discount_rate: Optional[float] = Query(None),
                 terminal_growth: Optional[float] = Query(None),
                 projection_years: Optional[float] = Query(None),
                 inflation_hurdle: Optional[float] = Query(None),
                 margin_of_safety: Optional[float] = Query(None),
                 margin_normalization: Optional[float] = Query(None)):
    """Start a screen in the background; returns a job id to poll via
    /api/screen/status. Lets the large-cap (~900-name, ~25min) scan run without
    a blocking, timeout-prone request."""
    _prune_jobs()
    a = _assumptions_from_query(discount_rate, terminal_growth, projection_years,
                                inflation_hurdle, margin_of_safety, margin_normalization)
    if universe:
        symbols = [t.strip().upper() for t in universe.replace(",", " ").split() if t.strip()]
    else:
        symbols = _universe.get(scope)
    symbols = list(dict.fromkeys(symbols))[:1100]
    if not symbols:
        raise HTTPException(status_code=400, detail="No tickers to scan.")
    job_id = uuid.uuid4().hex[:12]
    _SCAN_JOBS[job_id] = {
        "status": "running", "total": len(symbols), "done": 0,
        "rows": [], "errors": [], "candidates": None, "diff": None,
        "min_score": min_score, "scope": scope, "assumptions": a,
        "is_custom": bool(universe), "started": time.time(), "finished": None,
    }
    threading.Thread(target=_run_scan_job,
                     args=(job_id, symbols, a, min_score, scope, bool(universe)),
                     daemon=True).start()
    return {"job_id": job_id, "total": len(symbols)}


@app.get("/api/screen/status")
def screen_status(job_id: str):
    """Progress while running; full results once finished."""
    job = _SCAN_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404,
                            detail="Scan job not found — it may have expired. Start a new scan.")
    with _SCAN_LOCK:
        out = {"status": job["status"], "total": job["total"], "done": job["done"],
               "min_score": job["min_score"], "scope": job["scope"],
               "ai_enabled": bool(os.environ.get("ANTHROPIC_API_KEY"))}
        if job["status"] in ("done", "cancelled"):
            out.update({
                "rows": job["rows"], "candidates": job["candidates"] or [],
                "errors": job["errors"], "diff": job["diff"],
                "assumptions": job["assumptions"],
                "scanned": len(job["rows"]), "universe_size": job["total"],
            })
    return out


@app.get("/api/screen/cancel")
def screen_cancel(job_id: str):
    """Ask a running scan to stop (it finalizes with whatever it has so far)."""
    job = _SCAN_JOBS.get(job_id)
    if job:
        job["cancelled"] = True
    return {"ok": True}


@app.get("/api/peers")
def peers(ticker: str):
    """Same-sector peers (from the curated universe) ranked side-by-side."""
    a = resolve_assumptions()
    peer_syms = _universe.peers_of(ticker)
    if not peer_syms:
        return {"rows": [], "peers_of": ticker.strip().upper(),
                "note": "No curated peers found for this ticker."}
    rows, _ = _scan([ticker.strip().upper()] + peer_syms, a)
    return {"rows": rows, "peers_of": ticker.strip().upper()}


@app.get("/api/watchlist")
def watchlist_list():
    """Return every watchlist entry enriched with a live price-vs-buy-below check."""
    data = watchlist.load()
    a = resolve_assumptions()
    rows = []
    for ticker, entry in data.items():
        row = {"ticker": ticker, **entry}
        try:
            r = _summary_row(ticker, a)   # Yahoo, no AI — fast
            row.update({
                "name": r["name"], "sector": r["sector"], "price": r["price"],
                "buy_below": r["buy_below"], "intrinsic_value": r["intrinsic_value"],
                "upside": r["upside"], "score": r["score"], "rating": r["rating"],
                "suspect": r.get("suspect"),
            })
            bb, px = r.get("buy_below"), r.get("price")
            row["in_buy_zone"] = bool(bb and px and px <= bb)
        except Exception as e:  # noqa: BLE001
            row["error"] = str(getattr(e, "detail", e))
        rows.append(row)
    rows.sort(key=lambda x: (not x.get("in_buy_zone"), -(x.get("score") or 0)))
    return {"rows": rows, "count": len(rows), "portfolio": _portfolio_summary(rows)}


def _portfolio_summary(rows: list[dict]) -> Optional[dict[str, Any]]:
    """Aggregate the owned positions: value/cost/gain, sector allocation,
    value- (or equal-) weighted score, and diversification."""
    pos = [r for r in rows if r.get("owned") and r.get("price") is not None]
    if not pos:
        return None
    has_shares = any(r.get("shares") for r in pos)
    weights, total_value, total_cost = {}, 0.0, 0.0
    for r in pos:
        if has_shares:
            sh = r.get("shares") or 0
            val = sh * r["price"]
            total_cost += sh * (r.get("buy_price") or r["price"])
        else:
            val = 1.0  # equal weight
        weights[r["ticker"]] = val
        total_value += val
    if total_value <= 0:
        return None
    sectors: dict[str, float] = {}
    wscore = 0.0
    for r in pos:
        w = weights[r["ticker"]] / total_value
        sectors[r.get("sector") or "Other"] = sectors.get(r.get("sector") or "Other", 0) + w
        wscore += w * (r.get("score") or 0)
    top = max(weights.values()) / total_value
    return {
        "n_positions": len(pos),
        "n_sectors": len(sectors),
        "value_weighted": has_shares,
        "total_value": total_value if has_shares else None,
        "total_cost": total_cost if has_shares else None,
        "gain": (total_value - total_cost) if (has_shares and total_cost) else None,
        "gain_pct": ((total_value - total_cost) / total_cost) if (has_shares and total_cost) else None,
        "weighted_score": round(wscore, 1),
        "largest_position_pct": top,
        "sector_allocation": sorted(sectors.items(), key=lambda kv: -kv[1]),
    }


@app.post("/api/watchlist")
def watchlist_add(ticker: str, notes: str = "", buy_price: Optional[float] = None,
                  owned: bool = False, thesis: str = "", shares: Optional[float] = None):
    entry = watchlist.upsert(ticker, {
        "notes": notes, "buy_price": buy_price, "owned": owned, "thesis": thesis,
        "shares": shares,
    }, date.today().isoformat())
    return {"ticker": ticker.strip().upper(), "entry": entry}


@app.delete("/api/watchlist")
def watchlist_delete(ticker: str):
    return {"removed": watchlist.remove(ticker)}


@app.get("/api/health")
def health():
    return {"ok": True, "ai_enabled": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@app.get("/api/about")
def about():
    """Guide-tab content: capabilities & limitations + a live config snapshot."""
    import about as _about
    return _about.build()


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
