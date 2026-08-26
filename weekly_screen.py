#!/usr/bin/env python3
"""Weekly buy-candidate screen — run headless (no server/browser needed).

Scans the curated universe under the default assumptions, prints a ranked
buy list to the terminal, and saves a dated Markdown report under reports/.
Intended to be run about once a week (see README for scheduling).

Usage:
    .venv/bin/python weekly_screen.py # curated universe, buy bar 70
    .venv/bin/python weekly_screen.py --min-score 65
    .venv/bin/python weekly_screen.py AAPL MSFT KO # your own tickers
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env") # so the AI read can see ANTHROPIC_API_KEY
except ImportError:
    pass

from data import fetch_stock, prefilter_by_size # noqa: E402
from valuation import compute_metrics, resolve_assumptions # noqa: E402
from scoring import score # noqa: E402
from qualitative import analyze as ai_analyze # noqa: E402
import universe as _universe # noqa: E402 (single source of truth)
import all_us_symbols # noqa: E402
import diffstate # noqa: E402 (shared with the app)

CHECKPOINT = ROOT / "reports" / ".scan_checkpoint.json"


def pct(x, dp=0):
    return "—" if x is None else f"{x*100:.{dp}f}%"


def usd(x):
    return "—" if x is None else f"${x:.2f}"


def _analyze(sym, assumptions, deep=False):
    # Fast pass: Yahoo only (use_edgar=False) for a quick full-universe sweep.
    # Deep pass: EDGAR (10-19yr as-filed) + SimFin, which also runs the
    # SimFin-vs-Yahoo cross-check so unreliable foreign filers get flagged and
    # downgraded — the same two-pass the app's screener uses.
    stock = fetch_stock(sym, use_edgar=deep, use_simfin=deep)
    if stock.get("error"):
        return None, stock["error"]
    m = compute_metrics(stock, assumptions)
    v = score(m)
    val = m.get("valuation", {})
    return {
        "deep_verified": deep,
        "ticker": sym,
        "name": stock["info"].get("name"),
        "sector": stock["info"].get("sector") or "Other / Unknown",
        "score": v["score"],
        "rating": v["rating"],
        "price": stock["info"].get("current_price"),
        "iv": val.get("mid"), # capex-adjusted range midpoint
        "iv_low": val.get("low"),
        "iv_high": val.get("high"),
        "buy_below": val.get("buy_below"),
        "upside": val.get("upside_mid"),
        "certainty": (m.get("margin_of_safety_scaling") or {}).get("certainty"),
        "mos": (m.get("margin_of_safety_scaling") or {}).get("effective"),
        "heavy_capex": m.get("earnings_quality", {}).get("heavy_capex"),
        "suspect": val.get("suspect", False),
        "method": val.get("method"),
        "exp_return": m["expected_return"].get("expected_annual_return"),
        "roic": m["returns"].get("roic_avg") or m["returns"].get("roic_latest"),
        "roe": m["returns"].get("roe_avg") or m["returns"].get("roe_latest"),
    }, None


def scan(symbols, assumptions, signature=None, workers=6):
    """Scan symbols. If `signature` is given, checkpoint progress to disk so a
    crashed/killed run resumes where it left off (important for multi-hour scans).

    Fetches run in a thread pool (network-I/O-bound), so the wall-clock is a
    fraction of the sequential time. Results are collected in the MAIN thread as
    futures complete, so the shared rows/errors/done + checkpoint stay consistent
    without locks. `workers` is capped conservatively — Yahoo throttles, and the
    query1<->query2 host-failover absorbs the occasional 429."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    rows, errors, done = [], [], set()
    if signature and CHECKPOINT.exists():
        try:
            ck = json.loads(CHECKPOINT.read_text())
            if ck.get("signature") == signature:
                rows = ck.get("rows", [])
                errors = [tuple(e) for e in ck.get("errors", [])]
                done = set(ck.get("done", []))
                if done:
                    print(f" ↻ resuming — {len(done)} already scanned")
        except Exception: # noqa: BLE001
            pass

    def save():
        if not signature:
            return
        CHECKPOINT.parent.mkdir(exist_ok=True)
        CHECKPOINT.write_text(json.dumps(
            {"signature": signature, "rows": rows, "errors": errors,
             "done": sorted(done)}))

    todo = [s for s in symbols if s not in done]
    total, completed = len(symbols), len(done)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_analyze, sym, assumptions): sym for sym in todo}
        for fut in as_completed(futs):
            sym = futs[fut]
            completed += 1
            try:
                row, err = fut.result()
                if err:
                    errors.append((sym, err)); tag = "skipped"
                else:
                    rows.append(row); tag = f"{row['score']} {row['rating']}"
            except Exception as e:  # noqa: BLE001
                errors.append((sym, str(e))); tag = "error"
            done.add(sym)
            print(f" [{completed}/{total}] {sym} … {tag}", flush=True)
            if signature and completed % 25 == 0:
                save()
    save()
    rows.sort(key=lambda r: -(r["score"] or 0))
    return rows, errors


def enrich_with_ai(candidates, assumptions):
    """Run Claude's qualitative read on each buy candidate (moat / management /
    risks / verdict). Adds an 'ai' dict per candidate. Needs ANTHROPIC_API_KEY."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(" (AI read skipped — no ANTHROPIC_API_KEY)")
        return False
    for i, r in enumerate(candidates, 1):
        print(f" AI {i}/{len(candidates)} {r['ticker']} …", end="", flush=True)
        try:
            s = fetch_stock(r["ticker"])
            m = compute_metrics(s, assumptions)
            r["ai"] = ai_analyze(s, m)
            print(" ok")
        except Exception as e: # noqa: BLE001
            r["ai"] = {"available": False, "error": str(e)}
            print(" err")
    return True


def report_md(rows, candidates, errors, min_score, assumptions, diff=None):
    d = date.today().isoformat()
    lines = [f"# Weekly buy screen — {d}", ""]
    lines.append(f"Scanned **{len(rows)}** names · buy bar = score ≥ **{min_score}** · "
                 f"discount rate {pct(assumptions['discount_rate'])}, "
                 f"terminal growth {pct(assumptions['terminal_growth'],1)}, "
                 f"margin of safety {pct(assumptions['margin_of_safety'])}.")
    lines.append("")
    # ---- Changes since last scan ----
    if diff and diff.get("prev_date"):
        lines.append(f"## Changes since {diff['prev_date']}")
        lines.append("")
        if diff["added"]:
            lines.append(f"**New ({len(diff['added'])}):** " + ", ".join(diff["added"]))
        if diff["dropped"]:
            drop = [f"{t} (was {diff['prev_scores'].get(t,'?')})" for t in diff["dropped"]]
            lines.append(f"**Dropped ({len(diff['dropped'])}):** " + ", ".join(drop))
        if not diff["added"] and not diff["dropped"]:
            lines.append("No changes — same candidates as last scan.")
        lines.append("")
    if candidates:
        lines.append(f"## {len(candidates)} candidate(s) clearing the bar — grouped by sector")
        lines.append("")
        # group by sector, sectors ordered by their best score, names by score desc
        by_sector: dict[str, list] = {}
        for r in candidates:
            by_sector.setdefault(r.get("sector") or "Other / Unknown", []).append(r)
        ordered = sorted(by_sector.items(),
                         key=lambda kv: -max((x["score"] or 0) for x in kv[1]))
        for sector, names in ordered:
            names.sort(key=lambda x: -(x["score"] or 0))
            lines.append(f"### {sector} ({len(names)})")
            lines.append("")
            lines.append("| Ticker | Name | Score | Rating | Price | Fair value (mid) | Buy-below | Upside | Exp. return |")
            lines.append("|---|---|---:|---|---:|---:|---:|---:|---:|")
            for r in names:
                tick = r['ticker'] + (" " if r.get("heavy_capex") else "")
                lines.append(f"| {tick} | {r['name']} | {r['score']} | {r['rating']} | "
                             f"{usd(r['price'])} | {usd(r['iv'])} | {usd(r['buy_below'])} | "
                             f"{pct(r['upside'])} | {pct(r['exp_return'])} |")
            lines.append("")

        # ---- AI qualitative read (moat / management / thesis / bull / bear / risks) ----
        if any(r.get("ai") for r in candidates):
            lines.append("## AI qualitative read (candidates)")
            lines.append("")
            for r in sorted(candidates, key=lambda x: -(x["score"] or 0)):
                ai = r.get("ai") or {}
                if not ai.get("available"):
                    continue
                moat = ai.get("moat", {}); mgmt = ai.get("management", {})
                lines.append(f"### {r['ticker']} — {r['name']}")
                lines.append(f"**{moat.get('rating','?')} moat · {mgmt.get('rating','?')} management"
                             + (f" · {ai['cyclicality']}" if ai.get("cyclicality") else "") + "**")
                lines.append("")
                if ai.get("investment_thesis"):
                    lines.append(f"- **Thesis:** {ai['investment_thesis'].strip()}")
                if moat.get("reasoning"):
                    lines.append(f"- **Moat ({moat.get('rating','?')}):** {moat['reasoning'].strip()}")
                if mgmt.get("reasoning"):
                    lines.append(f"- **Management ({mgmt.get('rating','?')}):** {mgmt['reasoning'].strip()}")
                bull = ai.get("bull_case") or []
                bear = ai.get("bear_case") or []
                if bull:
                    lines.append(f"- **Bull:** {bull[0].strip()}")
                if bear:
                    lines.append(f"- **Bear:** {bear[0].strip()}")
                risks = ai.get("risks") or []
                if risks:
                    lines.append("- **Key risks:** " + "; ".join(x.strip() for x in risks[:3]))
                breakers = ai.get("thesis_breakers") or []
                if breakers:
                    lines.append(f"- **Thesis-breaker:** {breakers[0].strip()}")
                lines.append("")
    else:
        lines.append("## No names cleared the bar this week")
        lines.append("")
        lines.append("That's normal for a strict margin-of-safety screen in a richly-priced "
                     "market — the patient move is to wait. The highest scorers below are the "
                     "closest to a buy; watch them for a pullback.")
    lines.append("")
    lines.append("## Full ranked list")
    lines.append("")
    lines.append("| Ticker | Score | Rating | Price | Fair value | Upside | ROIC |")
    lines.append("|---|---:|---|---:|---:|---:|---:|")
    for r in rows:
        tick = r['ticker'] + (" " if r.get("heavy_capex") else "")
        lines.append(f"| {tick} | {r['score']} | {r['rating']} | {usd(r['price'])} | {usd(r['iv'])} | "
                     f"{pct(r['upside'])} | {pct(r['roic'])} |")
    if errors:
        lines.append("")
        lines.append("## Couldn't load")
        for sym, err in errors:
            lines.append(f"- {sym}: {err}")
    lines.append("")
    lines.append("_For research/education only — not investment advice._")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", help="Optional custom tickers (default: full universe)")
    ap.add_argument("--min-score", type=int, default=80)
    ap.add_argument("--scope", choices=["core", "full", "large", "all"], default="large",
                    help="core (~57) · full (~207) · large (~900 ≈ Russell 1000) · "
                         "all (~5000 US-listed, market-cap floor applied)")
    ap.add_argument("--min-cap", type=float, default=2e9,
                    help="market-cap floor for --scope all (default $2B)")
    ap.add_argument("--min-price", type=float, default=5.0,
                    help="price floor for --scope all (default $5)")
    ap.add_argument("--no-ai", action="store_true",
                    help="skip Claude's qualitative read on the candidates")
    ap.add_argument("--workers", type=int, default=6,
                    help="concurrent fetch workers (default 6; raise cautiously — "
                         "Yahoo throttles)")
    ap.add_argument("--deep-cap", type=int, default=200,
                    help="max names to deep-verify on EDGAR+SimFin in the second "
                         "pass (default 200; the fast pass under-scores, so a "
                         "generous cap catches quality names ranked below the bar)")
    args = ap.parse_args()

    assumptions = resolve_assumptions() # defaults

    if args.tickers:
        symbols = [t.upper() for t in args.tickers]
    elif args.scope == "all":
        raw = all_us_symbols.ALL_US
        print(f"Pre-filtering {len(raw)} US-listed names "
              f"(cap ≥ ${args.min_cap/1e9:.0f}B, price ≥ ${args.min_price:.0f})…")
        symbols = prefilter_by_size(raw, args.min_cap, args.min_price,
                                    progress=lambda d, t: print(f" quoted {d}/{t}", flush=True)
                                    if d % 1000 == 0 or d >= t else None)
        print(f" → {len(symbols)} names pass the floor.\n")
    else:
        symbols = _universe.get(args.scope)

    # Checkpoint big scans so a crash/kill resumes instead of restarting.
    signature = (f"{args.scope}:{args.min_score}:{date.today().isoformat()}"
                 if len(symbols) > 300 else None)

    print(f"Scanning {len(symbols)} names (buy bar ≥ {args.min_score})…\n")
    rows, errors = scan(symbols, assumptions, signature=signature, workers=args.workers)

    # Second pass: deep-verify the near-and-above-the-bar names on EDGAR + SimFin
    # so candidates rest on the same 10-19yr as-filed data the app's Analyze view
    # uses (no more "screen says 89, deep-dive says 60"), and so the SimFin-vs-
    # Yahoo cross-check downgrades unreliable foreign filers (e.g. DLocal, whose
    # Yahoo-only fast score of 93/BUY collapses to 50/HOLD on cross-check).
    # Cap generous: the Yahoo fast pass under-scores, so a quality name can rank
    # well past the top 80 on its noisy fast score yet deep-verify into a BUY
    # (e.g. SYF, ACN, GL sat at fast ~67-69 but score 73-76 on EDGAR).
    verify_floor = max(45, args.min_score - 15)
    # STICKY: always deep-verify last week's candidates. The fast (Yahoo-only) score
    # is noisy — free-tier statement data drifts run-to-run — so a name sitting near
    # the verify gate can fall below it on pure noise and silently drop off the buy
    # list without ever being deep-scored (ESNT/MMS: fast 48/44 one week vs a deep
    # BUY the week before). Re-verifying every prior candidate guarantees a name
    # leaves the list only when its STABLE deep re-score genuinely falls, never from
    # fast-pass wobble — the churn fix. Cheap: only ~10-15 extra names.
    import diffstate as _diffstate
    _scope_key = "custom" if args.tickers else args.scope
    _prev_candidates = set(((_diffstate._load().get(_scope_key) or {}).get("candidates") or {}))
    # ALSO deep-verify high-return names below the floor. The earnings-power model
    # (justified P/E) needs 7+ yr of history, which the Yahoo-only fast pass doesn't
    # have — so a mature quality compounder falls back to a shallow 4-yr DCF and
    # under-scores badly (CTSH: fast 58 -> deep 88, a 30-pt swing the 15-pt floor
    # buffer can't catch). Strong ROE *and* ROIC on the fast data is the tell that a
    # name is exactly such a candidate, so pull those into the deep pass regardless
    # of fast score. The deep re-score still decides candidacy, so the only cost is a
    # few extra EDGAR fetches.
    def _worth_verifying(r):
        if r.get("ticker") in _prev_candidates:
            return True
        if (r.get("score") or 0) >= verify_floor:
            return True
        return ((r.get("roe") or 0) >= 0.15 and (r.get("roic") or 0) >= 0.12
                and (r.get("score") or 0) >= 45 and not r.get("suspect"))
    to_verify = [r for r in rows if _worth_verifying(r)][:args.deep_cap]
    if to_verify:
        print(f"\nDeep-verifying {len(to_verify)} near-the-bar names on EDGAR + SimFin…")
        by_t = {r["ticker"]: r for r in rows}
        from concurrent.futures import ThreadPoolExecutor, as_completed
        n, done = len(to_verify), 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(_analyze, r["ticker"], assumptions, True): r["ticker"]
                    for r in to_verify}
            for fut in as_completed(futs):
                t = futs[fut]; done += 1
                try:
                    deep, err = fut.result()
                    if deep:
                        by_t[t] = deep; tag = f"{deep['score']} {deep['rating']}"
                    else:
                        tag = f"kept fast ({err})"
                except Exception as e:  # noqa: BLE001
                    tag = f"error ({e})"
                print(f" [{done}/{n}] {t} … {tag}", flush=True)
        rows = sorted(by_t.values(), key=lambda r: -(r["score"] or 0))

    # Candidates must clear the score bar AND be rated BUY (a high score a
    # guardrail downgraded — overvalued / suspect / distressed — is not a buy).
    candidates = [r for r in rows
                  if (r["score"] or 0) >= args.min_score
                  and r.get("rating") == "BUY" and not r.get("suspect")]

    # ---- Week-over-week diff (shared with the app, keyed by scope) ----
    scope_key = "custom" if args.tickers else args.scope
    cur_map = {r["ticker"]: r["score"] for r in candidates}
    today_iso = date.today().isoformat()
    diff = diffstate.compute_diff(scope_key, cur_map, today_iso)
    # Append to the multi-week track record (the app's "Track record" tab).
    import history as _history
    _history.record(scope_key, today_iso, args.min_score, candidates)

    # ---- Watchlist buy-zone alerts ----
    import watchlist as _wl
    wl_alerts = []
    for tk in _wl.load():
        try:
            row, err = _analyze(tk, assumptions)
            if row and row.get("buy_below") and row.get("price") and row["price"] <= row["buy_below"]:
                wl_alerts.append(row)
        except Exception: # noqa: BLE001
            pass
    if wl_alerts:
        print(f"\n{len(wl_alerts)} WATCHLIST NAME(S) IN BUY ZONE:",
              ", ".join(f"{r['ticker']} ({usd(r['price'])} ≤ {usd(r['buy_below'])})" for r in wl_alerts))

    # ---- AI qualitative read on the candidates (the value-trap / moat check) ----
    if candidates and not args.no_ai:
        print(f"\nRunning AI qualitative read on {len(candidates)} candidate(s)…")
        enrich_with_ai(candidates, assumptions)

    print("\n" + "=" * 56)
    if diff["prev_date"]:
        print(f"Δ vs {diff['prev_date']}: +{len(diff['added'])} new, -{len(diff['dropped'])} dropped")
        if diff["added"]:
            print(" NEW:", ", ".join(diff["added"]))
        if diff["dropped"]:
            print(" DROPPED:", ", ".join(diff["dropped"]))
        print("=" * 56)
    if candidates:
        print(f"{len(candidates)} BUY CANDIDATE(S):\n")
        for r in candidates:
            mark = " " if r.get("heavy_capex") else ""
            print(f" {r['ticker']:6} score {r['score']:>3} {r['rating']:14} "
                  f"price {usd(r['price'])} fair {usd(r['iv'])} "
                  f"buy-below {usd(r['buy_below'])} upside {pct(r['upside'])}{mark}")
    else:
        print("No names cleared the bar this week. Top 5 closest:\n")
        for r in rows[:5]:
            print(f" {r['ticker']:6} score {r['score']:>3} {r['rating']:14} upside {pct(r['upside'])}")
    print("=" * 56 + "\n")

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    out = reports / f"screen-{date.today().isoformat()}.md"
    md = report_md(rows, candidates, errors, args.min_score, assumptions, diff)
    if wl_alerts:
        alert_md = ["## Watchlist — in the buy zone", "",
                    "| Ticker | Name | Price | Buy-below | Upside |", "|---|---|---:|---:|---:|"]
        for r in wl_alerts:
            alert_md.append(f"| {r['ticker']} | {r['name']} | {usd(r['price'])} | "
                            f"{usd(r['buy_below'])} | {pct(r['upside'])} |")
        md = md.split("\n", 2)
        md = md[0] + "\n" + md[1] + "\n\n" + "\n".join(alert_md) + "\n\n" + (md[2] if len(md) > 2 else "")
    out.write_text(md)
    print(f"Report saved: {out}")
    if CHECKPOINT.exists():
        CHECKPOINT.unlink() # run completed cleanly — clear resume state


if __name__ == "__main__":
    main()
