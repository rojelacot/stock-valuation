#!/usr/bin/env python3
"""Post-hoc slices on a backtest dump (from `backtest.py --dump`).

Answers two questions the pooled bucket table can't:
  1. cheap x quality 2x2 — does the quality score add anything *within* the
     cheap names, or is cheapness doing all the work? (Isolates the claim that
     quality "screens value traps out of the cheap bucket".)
  2. index benchmark — did the screen's output actually beat just buying the
     whole universe? Equal-weight and cap-weight, per window and pooled, from
     the same observations (so survivorship bias is shared and cancels).

    .venv/bin/python analyze_backtest.py /tmp/bt_dump.json
"""
from __future__ import annotations

import json
import statistics
import sys

CHEAP = 0.30      # upside_mid >= 30% == a real margin of safety
QUALITY = 60      # score >= 60 == passes the quality filter


def med(xs):
    return statistics.median(xs) if xs else None


def pct(x):
    return "—" if x is None else f"{x*100:+.0f}%"


def load(path):
    rows = json.load(open(path))
    # keep only rows with the fields these slices need
    return [r for r in rows if r.get("total_return") is not None]


def cheap_x_quality(rows):
    print("\n" + "=" * 68)
    print("1. CHEAP x QUALITY 2x2  —  does quality add anything within cheap names?")
    print("=" * 68)
    have_upside = [r for r in rows if r.get("upside") is not None]
    print(f"(observations with a usable upside estimate: {len(have_upside)} of {len(rows)})\n")

    def cell(cheap, quality):
        grp = [r["total_return"] for r in have_upside
               if (r["upside"] >= CHEAP) == cheap
               and (r["score"] >= QUALITY) == quality]
        return grp

    print(f"  {'':22}{'quality (score>=60)':>22}{'low (score<60)':>18}")
    for cheap, label in [(True, f"CHEAP (upside>=30%)"), (False, "not cheap")]:
        q = cell(cheap, True)
        l = cell(cheap, False)
        qs = f"{pct(med(q))} (n={len(q)})"
        ls = f"{pct(med(l))} (n={len(l)})"
        print(f"  {label:22}{qs:>22}{ls:>18}")

    # the load-bearing comparison
    cq = cell(True, True)
    cl = cell(True, False)
    print()
    if cq and cl:
        d = med(cq) - med(cl)
        verdict = ("quality HELPS within cheap" if d > 0.03 else
                   "quality HURTS within cheap" if d < -0.03 else
                   "quality ~neutral within cheap")
        print(f"  Within the cheap names: quality {pct(med(cq))} vs low-quality "
              f"{pct(med(cl))}  =  {d*100:+.0f} pt  → {verdict}")
    # and does cheap beat expensive within high quality? (cheapness within quality)
    hq_cheap = cell(True, True)
    hq_exp = cell(False, True)
    if hq_cheap and hq_exp:
        d2 = med(hq_cheap) - med(hq_exp)
        print(f"  Within high-quality names: cheap {pct(med(hq_cheap))} vs not-cheap "
              f"{pct(med(hq_exp))}  =  {d2*100:+.0f} pt  → cheapness "
              f"{'helps' if d2 > 0.03 else 'hurts' if d2 < -0.03 else 'neutral'} within quality")


def _weighted_mean(pairs):
    """pairs: list of (value, weight). Returns weighted mean or None."""
    num = sum(v * w for v, w in pairs if w)
    den = sum(w for _, w in pairs if w)
    return (num / den) if den else None


def index_benchmark(rows):
    print("\n" + "=" * 68)
    print("2. INDEX BENCHMARK  —  did the screen beat just buying the universe?")
    print("=" * 68)
    print("Equal-weight = buy all names equally. Cap-weight = weight by as-of market")
    print("cap (~ a real index). Screen output proxy = score>=80 AND cheap (in-zone).\n")

    windows = sorted({r["window"] for r in rows})

    def universe_eq(subset):
        vals = [r["total_return"] for r in subset]
        return med(vals), (sum(vals) / len(vals) if vals else None), len(vals)

    def universe_cap(subset):
        pairs = [(r["total_return"], r.get("market_cap"))
                 for r in subset if r.get("market_cap")]
        return _weighted_mean(pairs), len(pairs)

    def screen_output(subset):
        sel = [r["total_return"] for r in subset
               if r["score"] >= 80 and (r.get("upside") or -9) >= CHEAP]
        return med(sel), (sum(sel) / len(sel) if sel else None), len(sel)

    hdr = f"  {'window':10}{'UNIVERSE eq (med/mean)':>26}{'cap-wt mean':>14}{'SCREEN>=80&cheap':>20}"
    print(hdr)
    for w in windows + ["POOLED"]:
        subset = rows if w == "POOLED" else [r for r in rows if r["window"] == w]
        emed, emean, en = universe_eq(subset)
        cmean, cn = universe_cap(subset)
        smed, smean, sn = screen_output(subset)
        uni = f"{pct(emed)}/{pct(emean)} (n={en})"
        cap = f"{pct(cmean)}"
        scr = f"{pct(smed)} (n={sn})" if sn else f"— (n=0)"
        label = str(w) if w != "POOLED" else "POOLED"
        print(f"  {label:10}{uni:>26}{cap:>14}{scr:>20}")

    # pooled verdict: screen vs equal-weight and vs cap-weight
    emed, emean, _ = universe_eq(rows)
    cmean, _ = universe_cap(rows)
    smed, smean, sn = screen_output(rows)
    print()
    if sn and smed is not None:
        print(f"  Screen (score>=80 & cheap): median {pct(smed)}, mean {pct(smean)}  (n={sn})")
        print(f"  vs equal-weight universe:  median {pct(emed)}  → "
              f"{(smed-emed)*100:+.0f} pt")
        if cmean is not None and smean is not None:
            print(f"  vs cap-weight universe:    mean   {pct(cmean)}  → "
                  f"{(smean-cmean)*100:+.0f} pt (mean-vs-mean, cap-weight has no median)")
    # also: the broader "cheap" slice vs the universe (the real edge the doc claims)
    cheap = [r["total_return"] for r in rows if (r.get("upside") or -9) >= CHEAP]
    if cheap:
        print(f"\n  (For reference — the broad cheap slice, upside>=30%: median "
              f"{pct(med(cheap))} vs universe {pct(emed)} = {(med(cheap)-emed)*100:+.0f} pt, n={len(cheap)})")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/bt_dump.json"
    rows = load(path)
    print(f"Loaded {len(rows)} observations from {path}")
    cheap_x_quality(rows)
    index_benchmark(rows)
    print("\n" + "=" * 68)
    print("Both slices share the universe's survivorship bias, so the index")
    print("comparison isolates selection skill (delisted traps are absent from")
    print("BOTH the screen and the benchmark). Directional, not academic.")


if __name__ == "__main__":
    main()
