"""Weekly digest — turn a screen run into a short, scannable summary and deliver it,
so the scheduled Monday run *reaches* you instead of just sitting in a report file.

Delivery is best-effort and credential-free by default:
  1. Always writes reports/digest-<date>.md (persistent, cross-platform).
  2. Fires a native macOS notification banner (osascript) when available.
  3. Optionally emails the digest IF the DIGEST_SMTP_* env vars are set — opt-in,
     nothing is required, and no secrets ever live in the repo.

The watchlist section leads, because it's about what you already HOLD (trim / sell /
add) — the most actionable part of the week.
"""
from __future__ import annotations

import json
import os
import smtplib
import subprocess
import sys
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _usd(x):
    if x is None:
        return "—"
    return f"${x:,.2f}" if abs(x) < 1000 else f"${x:,.0f}"


def _pct(x):
    return "—" if x is None else f"{x*100:+.0f}%"


def build_digest(date_str, candidates, diff, vetoed, wl_buyzone, wl_sell, report_path):
    """Return (subject, markdown_body). `wl_sell` are held names now flagged trim/sell;
    `wl_buyzone` are held names that fell into their buy-below zone."""
    added = diff.get("added") or []
    dropped = diff.get("dropped") or []
    prev = diff.get("prev_date")
    n = len(candidates)

    bits = [f"{n} buys"]
    if prev:
        bits.append(f"+{len(added)}/-{len(dropped)} vs {prev[5:]}")
    wl_n = len(wl_buyzone) + len(wl_sell)
    if wl_n:
        bits.append(f"{wl_n} watchlist alert{'s' if wl_n != 1 else ''}")
    headline = " · ".join(bits)
    subject = f"Weekly screen {date_str} — {headline}"

    L = [f"# Weekly screen — {date_str}", "", f"**{headline}**", ""]

    if wl_sell or wl_buyzone:
        L.append("## Your watchlist")
        for r in wl_sell:
            icon = "🔴 SELL" if r.get("sell_action") == "sell" else "🟠 TRIM"
            L.append(f"- {icon} **{r['ticker']}** — {r.get('sell_reason', '')}")
        for r in wl_buyzone:
            L.append(f"- 🟢 BUY ZONE **{r['ticker']}** — {_usd(r['price'])} ≤ buy-below "
                     f"{_usd(r['buy_below'])} ({_pct(r['upside'])} upside)")
        L.append("")

    if prev:
        L.append(f"## Changes since {prev}")
        L.append(f"- New ({len(added)}): {', '.join(added) or '—'}")
        L.append(f"- Dropped ({len(dropped)}): {', '.join(dropped) or '—'}")
        L.append("")

    if vetoed:
        L.append(f"## Vetoed by the qualitative read ({len(vetoed)})")
        for r in vetoed:
            L.append(f"- {r['ticker']} — {r.get('ai_veto', '')}")
        L.append("")

    L.append(f"## Buy list ({n})")
    for r in candidates:
        L.append(f"- **{r['ticker']}** · score {r['score']} · {_pct(r.get('upside'))} upside · "
                 f"buy-below {_usd(r.get('buy_below'))}")
    L.append("")
    L.append(f"_Full report: {report_path}_")
    return subject, "\n".join(L)


def deliver(subject, body_md, date_str, verbose=True):
    """Write the digest file, fire a macOS notification, and email if configured."""
    out = ROOT / "reports" / f"digest-{date_str}.md"
    out.write_text(body_md)
    if verbose:
        print(f"Digest saved: {out}")
    _macos_notify(subject)
    _maybe_email(subject, body_md, verbose)
    return out


def _macos_notify(headline):
    """Native banner — best-effort. LaunchAgents run in the user session, so this
    shows around Monday 8am; silently skipped off macOS or if osascript fails."""
    if sys.platform != "darwin":
        return
    safe = headline.replace('"', "'").replace("\\", "")
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe}" with title "Long-Term Value Screener"'],
            check=False, capture_output=True, timeout=10)
    except Exception:  # noqa: BLE001
        pass


def _maybe_email(subject, body_md, verbose=True):
    """Send the digest by email only if all DIGEST_SMTP_* env vars are present.
    Opt-in; the password is read from the environment and never logged or stored."""
    host = os.environ.get("DIGEST_SMTP_HOST")
    user = os.environ.get("DIGEST_SMTP_USER")
    pw = os.environ.get("DIGEST_SMTP_PASS")
    to = os.environ.get("DIGEST_EMAIL_TO")
    if not (host and user and pw and to):
        return
    port = int(os.environ.get("DIGEST_SMTP_PORT", "587"))
    msg = MIMEText(body_md, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = os.environ.get("DIGEST_EMAIL_FROM", user)
    msg["To"] = to
    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, pw)
            s.send_message(msg)
        if verbose:
            print(f"Digest emailed to {to}")
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"Digest email failed: {e}")
