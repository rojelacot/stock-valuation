"""Segment breakdown — revenue (and operating income) by reportable segment and
by product/service, parsed from the raw XBRL instance of the latest 10-K.

Why the raw XBRL: the companyfacts API strips the segment *dimension*, so a
segment value comes through with no way to tell which segment it belongs to. The
attribution only exists in the filing's XBRL instance, where each fact carries a
context whose `explicitMember` names the segment. So we fetch the instance, map
contexts → segment members, and join the revenue / operating-income facts.

This is a heavier, per-filing fetch (~1-2 MB) than the rest of the app, so it's
exposed as its own lazy endpoint and only run for the single-stock view.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import edgar

# The segment axes worth breaking out, best-first, with a friendly label.
_SEG_AXES = [
    ("us-gaap:StatementBusinessSegmentsAxis", "Reportable segments"),
    ("srt:ProductOrServiceAxis", "By product / service"),
    ("us-gaap:ProductOrServiceAxis", "By product / service"),
    ("srt:StatementGeographicalAxis", "By geography"),
]
_REV_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
             "RevenueFromContractWithCustomerIncludingAssessedTax"]


def _clean_member(m: str) -> str:
    """'aapl:GreaterChinaSegmentMember' -> 'Greater China'."""
    name = m.split(":")[-1]
    for suf in ("SegmentMember", "Member"):
        if name.endswith(suf):
            name = name[: -len(suf)]
            break
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)      # camelCase -> words
    name = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    for a, b in (("I Phone", "iPhone"), ("I Pad", "iPad"), ("I Mac", "iMac"),
                 ("I OS", "iOS"), (" And ", " and "), ("Homeand", "Home and"),
                 ("Three Six Five", "365"), ("X BOX", "Xbox"), ("XBOX", "Xbox"),
                 ("Linked In", "LinkedIn"), ("Non Us", "Non-US"), ("EMEA", "EMEA")):
        name = name.replace(a, b)
    return name.strip()


def _latest_10k_instance(session, cik: int):
    sub = edgar._get(session, f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    rec = (sub or {}).get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    for i, f in enumerate(forms):
        if f == "10-K":
            acc = rec["accessionNumber"][i].replace("-", "")
            doc = rec["primaryDocument"][i]
            base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
            inst = doc.rsplit(".htm", 1)[0] + "_htm.xml"
            return f"{base}/{inst}", rec["accessionNumber"][i]
    return None, None


def _parse_contexts(xml: str) -> dict[str, dict]:
    ctx = {}
    for m in re.finditer(r'<(?:\w+:)?context id="([^"]+)">(.*?)</(?:\w+:)?context>', xml, re.S):
        cid, body = m.group(1), m.group(2)
        dims = dict(re.findall(r'dimension="([^"]+)">\s*([^<\s][^<]*?)\s*<', body))
        end = re.search(r"<(?:\w+:)?endDate>([^<]+)<", body)
        start = re.search(r"<(?:\w+:)?startDate>([^<]+)<", body)
        inst = re.search(r"<(?:\w+:)?instant>([^<]+)<", body)
        ctx[cid] = {"dims": dims,
                    "end": (end.group(1) if end else (inst.group(1) if inst else None)),
                    "start": start.group(1) if start else None}
    return ctx


def _facts(xml: str, tag: str) -> list[tuple[str, float]]:
    # Attribute order varies by filer (contextRef isn't always first), so match
    # the tag, then find contextRef anywhere in its attributes.
    out = []
    for m in re.finditer(rf'<us-gaap:{tag}\b([^>]*)>([-0-9.]+)</us-gaap:{tag}>', xml):
        cref = re.search(r'contextRef="([^"]+)"', m.group(1))
        if not cref:
            continue
        try:
            out.append((cref.group(1), float(m.group(2))))
        except ValueError:
            pass
    return out


def _annual(ctx_entry) -> bool:
    """A ~full-year duration context (300-400 days)."""
    s, e = ctx_entry.get("start"), ctx_entry.get("end")
    if not s or not e:
        return False
    d = edgar._daydiff(s, e)
    return d is not None and 300 <= d <= 400


def fetch_segments(ticker: str) -> dict[str, Any]:
    cik = edgar.cik_for(ticker)
    if cik is None:
        return {"error": f"{ticker}: not a US 10-K filer"}
    session = edgar._session()
    try:
        url, acc = _latest_10k_instance(session, cik)
        if not url:
            return {"error": "no 10-K found"}
        r = session.get(url, timeout=45)
        if r.status_code != 200:
            return {"error": f"instance HTTP {r.status_code}"}
        xml = r.text
    except Exception as e:  # noqa: BLE001
        return {"error": f"fetch failed: {e}"}

    ctx = _parse_contexts(xml)
    rev_facts, oi_facts = [], []
    for t in _REV_TAGS:
        rev_facts += _facts(xml, t)
    oi_facts = _facts(xml, "OperatingIncomeLoss")

    # The fiscal-year end = the latest end-date on an annual revenue context.
    fye = None
    for cid, _ in rev_facts:
        c = ctx.get(cid)
        if c and _annual(c) and c["end"] and (fye is None or c["end"] > fye):
            fye = c["end"]
    if not fye:
        return {"error": "no annual period found"}

    # True total revenue (dimensionless annual fact) — used to de-nest breakdowns
    # where the filing tags both a parent aggregate and its children.
    total_rev = None
    for cid, val in rev_facts:
        c = ctx.get(cid)
        if c and c["end"] == fye and _annual(c) and not c["dims"]:
            total_rev = max(total_rev or 0, val)

    def _ok_others(others):
        # The fact may carry the segment axis alone, or also a
        # ConsolidationItemsAxis=OperatingSegments qualifier (how many filers —
        # GOOGL, AAPL — tag segment operating income). Anything else (intersegment
        # eliminations, corporate/non-segment, product sub-splits) is rejected.
        if not others:
            return True
        if len(others) != 1:
            return False
        (k, v), = others.items()
        return k.endswith("ConsolidationItemsAxis") and v.endswith("OperatingSegmentsMember")

    def _by_axis(facts, axis):
        out = {}
        for cid, val in facts:
            c = ctx.get(cid)
            if not c or c["end"] != fye or not _annual(c):
                continue
            mem = c["dims"].get(axis)
            if mem and _ok_others({k: v for k, v in c["dims"].items() if k != axis}):
                out.setdefault(_clean_member(mem), val)
        return out

    breakdowns = []
    seen_labels = set()
    for axis, label in _SEG_AXES:
        if label in seen_labels:
            continue
        rev_by = _by_axis(rev_facts, axis)
        if len(rev_by) < 2:
            continue
        oi_by = _by_axis(oi_facts, axis)
        segs = [{"name": n, "revenue": rv, "operating_income": oi_by.get(n)}
                for n, rv in rev_by.items()]
        segs.sort(key=lambda s: -s["revenue"])
        # De-nest: if members sum well above the true total, the filing tagged
        # parent aggregates too (e.g. AAPL 'Product' = iPhone+Mac+iPad+Wearables).
        # Drop the largest until the sum lines up with total revenue.
        if total_rev:
            while len(segs) > 2 and sum(s["revenue"] for s in segs) > 1.12 * total_rev:
                segs.pop(0)
        denom = total_rev or sum(s["revenue"] for s in segs) or 1
        for s in segs:
            s["revenue_pct"] = s["revenue"] / denom
        breakdowns.append({"label": label,
                           "has_oi": any(s["operating_income"] is not None for s in segs),
                           "segments": segs})
        seen_labels.add(label)

    if not breakdowns:
        return {"error": "no segment disaggregation in the filing"}
    return {"ticker": ticker.upper(), "fiscal_year": fye[:4],
            "accession": acc, "breakdowns": breakdowns}
