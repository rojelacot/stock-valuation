/* Long-Term Value Screener — frontend logic. Vanilla JS, no build step. */

const $ = (id) => document.getElementById(id);
const charts = {}; // Chart.js instances (destroyed on re-render)

// ---------- formatting ----------
const fmtPct = (x, dp = 1) => (x == null ? "—" : (x * 100).toFixed(dp) + "%");
const fmtNum = (x, dp = 2) => (x == null ? "—" : Number(x).toFixed(dp));
function fmtMoney(x) {
  if (x == null) return "—";
  const a = Math.abs(x);
  if (a >= 1e12) return (x / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return (x / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (x / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (x / 1e3).toFixed(2) + "K";
  return x.toFixed(2);
}
const price = (x, cur = "$") => (x == null ? "—" : cur + Number(x).toFixed(2));
const signPct = (x) => (x == null ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(0) + "%");
// Chart-axis money formatter: always rounds (kills floating-point tick labels
// like "$-0.4000000000000001") and keeps the sign in front of the currency.
const axisMoney = (v, cur = "$") => (v == null ? "" : (v < 0 ? "-" : "") + cur + fmtMoney(Math.abs(v)));

const RATING_STYLE = {
  "BUY": { c: "good", bg: "bg-good/15", br: "border-good/50", ring: "#22c55e" },
  "HOLD / WATCH": { c: "warn", bg: "bg-warn/15", br: "border-warn/50", ring: "#f59e0b" },
  "AVOID": { c: "bad", bg: "bg-bad/15", br: "border-bad/50", ring: "#ef4444" },
};
const scoreColor = (s) => (s >= 70 ? "good" : s >= 50 ? "warn" : "bad");

// ---------- assumptions ----------
// slider id -> {default (in slider units), toFraction}. projection_years stays integer.
const ASSUME = {
  discount_rate: { def: 10, unit: "%", frac: (v) => v / 100 },
  terminal_growth: { def: 2.5, unit: "%", frac: (v) => v / 100 },
  projection_years:{ def: 10, unit: "yr", frac: (v) => v },
  inflation_hurdle:{ def: 3, unit: "%", frac: (v) => v / 100 },
  margin_of_safety:{ def: 25, unit: "%", frac: (v) => v / 100 },
  margin_normalization:{ def: 0, unit: "%", frac: (v) => v / 100 },
};

const STRATEGY_SHORT = { balanced: "", deep_value: "deep value", quality: "quality", garp: "GARP", conservative: "conservative" };

function initAssumptions() {
  for (const [id, cfg] of Object.entries(ASSUME)) {
    const el = $(id);
    el.value = cfg.def;
    el.addEventListener("input", () => { updateAssumeLabels(); });
    el.addEventListener("change", () => { onAssumptionsChanged(); });
  }
  const strat = $("strategy");
  if (strat) strat.addEventListener("change", () => { updateAssumeLabels(); onAssumptionsChanged(); });
  updateAssumeLabels();
}
function updateAssumeLabels() {
  const parts = [];
  for (const [id, cfg] of Object.entries(ASSUME)) {
    const v = $(id).value;
    $("v_" + id).textContent = v + cfg.unit;
    // Keep the collapsed summary uncluttered: only show normalization when it's on.
    if (id === "margin_normalization" && Number(v) === 0) continue;
    const short = { discount_rate: "DR", terminal_growth: "TG", projection_years: "", inflation_hurdle: "infl", margin_of_safety: "MoS", margin_normalization: "norm" }[id];
    parts.push(id === "projection_years" ? `${v}yr` : `${short} ${v}${cfg.unit}`);
  }
  const strat = $("strategy") ? $("strategy").value : "balanced";
  if (STRATEGY_SHORT[strat]) parts.unshift(STRATEGY_SHORT[strat]);
  $("assumeSummary").textContent = parts.join(" · ");
}
function readAssumptions() {
  const out = {};
  for (const [id, cfg] of Object.entries(ASSUME)) out[id] = cfg.frac(parseFloat($(id).value));
  return out;
}
function assumptionsQS() {
  const a = readAssumptions();
  let qs = Object.entries(a).map(([k, v]) => `&${k}=${v}`).join("");
  const strat = $("strategy") ? $("strategy").value : "balanced";
  if (strat) qs += `&strategy=${strat}`;
  return qs;
}
function resetAssumptions() {
  for (const [id, cfg] of Object.entries(ASSUME)) $(id).value = cfg.def;
  if ($("strategy")) $("strategy").value = "balanced";
  updateAssumeLabels();
  onAssumptionsChanged();
}
// Re-run only the single-analysis view live (cheap: backend caches data + AI).
let lastTicker = null, currentMode = "analyze";
function onAssumptionsChanged() {
  if (currentMode === "analyze" && lastTicker && !$("results").classList.contains("hidden")) {
    analyze(lastTicker);
  }
}

// ---------- mode / tabs ----------
function switchMode(mode) {
  currentMode = mode;
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.mode === mode));
  ["analyze", "compare", "screen", "history", "watchlist", "guide"].forEach(m => $("mode-" + m).classList.toggle("hidden", m !== mode));
  $("results").classList.add("hidden");
  $("error").classList.add("hidden");
  $("loading").classList.add("hidden");
  if (mode === "watchlist") loadWatchlist();
  if (mode === "guide") loadGuide();
  if (mode === "history") loadHistory();
  if (mode === "screen") resumeScreen();
}

// ---------- Guide tab (capabilities & limitations + live config) ----------
async function loadGuide() {
  const el = $("guideBody");
  el.innerHTML = `<div class="text-muted text-sm py-8 text-center">Loading…</div>`;
  let d;
  try { d = await getJSON("/api/about"); }
  catch (e) { el.innerHTML = `<div class="card rounded-2xl p-6 text-bad">${e.message}</div>`; return; }
  const live = d.live || {};
  const listCard = (title, groups, accent, mark) => `
    <section class="card rounded-2xl p-6">
      <h3 class="font-semibold mb-4 text-${accent}">${title}</h3>
      <div class="space-y-4">
        ${groups.map(g => `
          <div>
            <div class="font-medium text-sm mb-1.5">${g.title}</div>
            <ul class="space-y-1">
              ${g.items.map(it => `<li class="text-sm text-muted flex gap-2"><span class="text-${accent} shrink-0">${mark}</span><span>${it}</span></li>`).join("")}
            </ul>
          </div>`).join("")}
      </div>
    </section>`;
  const s = live.scoring || {}, vd = live.valuation_defaults || {}, un = live.universe_sizes || {};
  const chip = (label, val) => `<div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">${label}</div><div class="text-sm font-semibold">${val}</div></div>`;
  const pills = (s.pillars || []).map(p => `<div class="flex justify-between items-center bg-ink/40 rounded-lg px-3 py-1.5 text-xs"><span>${p.name}</span><span class="text-brand font-mono ml-2 shrink-0">${p.max} pts</span></div>`).join("");
  const liveCard = `
    <section class="card rounded-2xl p-6">
      <h3 class="font-semibold mb-1">Live configuration snapshot</h3>
      <p class="text-xs text-muted mb-4">${live.note || ""}</p>
      <div class="text-sm font-medium mb-2">Data sources</div>
      <ul class="space-y-1 mb-5">${(live.data_sources || []).map(x => `<li class="text-xs text-muted flex gap-2"><span class="text-brand shrink-0">•</span><span>${x}</span></li>`).join("")}</ul>
      <div class="text-sm font-medium mb-2">Key settings</div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-5">
        ${chip("Buy bar", "score ≥ " + s.buy_bar)}
        ${chip("Hold bar", "score ≥ " + s.hold_bar)}
        ${chip("Monte-Carlo runs", (vd.monte_carlo_runs || 0).toLocaleString())}
        ${chip("Red-flag max penalty", "−" + s.forensic_max_penalty + " pts")}
        ${chip("Default discount rate", fmtPct(vd.discount_rate, 0))}
        ${chip("Terminal growth", fmtPct(vd.terminal_growth, 1))}
        ${chip("Projection years", vd.projection_years)}
        ${chip("Margin of safety", fmtPct(vd.margin_of_safety, 0))}
      </div>
      <div class="text-sm font-medium mb-2">Screening universes</div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-5">
        ${chip("Core", (un.core || "—") + " names")}
        ${chip("Full", (un.full || "—") + " names")}
        ${chip("Large", (un.large || "—") + " names")}
        ${chip("All US-listed", (un.all_us_listed || "—") + " names")}
      </div>
      <div class="text-sm font-medium mb-2">Scoring pillars (weights, out of 100)</div>
      <div class="grid sm:grid-cols-2 gap-2 mb-5">${pills || '<span class="text-muted text-xs">n/a</span>'}</div>
      <div class="text-sm font-medium mb-1">Decision thresholds — the parameters that turn analysis into a verdict</div>
      <p class="text-[11px] text-muted mb-2">Read live from the code. It's DCF- and score-driven, so there's no fixed "P/E 10–15 = buy" rule; these are the actual bars.</p>
      <div class="divide-y divide-line/50">
        ${(live.decision_thresholds || []).map(t => `<div class="flex flex-col sm:flex-row sm:justify-between gap-0.5 sm:gap-4 py-1.5"><span class="text-xs font-medium shrink-0 sm:w-56">${t.name}</span><span class="text-xs text-muted sm:text-right">${t.value}</span></div>`).join("")}
      </div>
    </section>`;
  el.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "space-y-6 fade-in";
  wrap.innerHTML =
    `<div class="card rounded-2xl p-6"><h2 class="text-xl font-semibold mb-1">What this tool does — and doesn't</h2>
      <p class="text-sm text-muted">Professional-grade fundamental analysis and valuation on individual US stocks, for free, with strong guardrails against value traps — but every number is an informed estimate, not a promise, and the final decision is yours. The snapshot at the bottom is read live from the running code, so this page stays accurate as the app is revised.</p></div>`
    + (d.thesis ? listCard("The philosophy it's built on", d.thesis, "brand", "•") : "")
    + listCard("What it does", d.capabilities, "good", "✓")
    + (d.guardrails ? listCard("Guardrails against value traps", d.guardrails, "good", "✓") : "")
    + listCard("Limitations", d.limitations, "warn", "•")
    + liveCard;
  el.append(wrap);
}

// ---------- shared fetch helpers ----------
function showLoading(msg) {
  $("results").classList.add("hidden");
  $("error").classList.add("hidden");
  $("hint")?.classList.add("hidden");
  $("loadingMsg").innerHTML = msg;
  $("loading").classList.remove("hidden");
}
function showError(msg) {
  $("loading").classList.add("hidden");
  $("errorMsg").textContent = msg;
  $("error").classList.remove("hidden");
}
async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const e = await res.json().catch(() => ({ detail: "Request failed." }));
    throw new Error(e.detail || "Something went wrong.");
  }
  return res.json();
}

// ================= ANALYZE (single) =================
async function analyze(ticker) {
  ticker = (ticker || $("ticker").value).trim().toUpperCase();
  if (!ticker) return;
  $("ticker").value = ticker;
  lastTicker = ticker;
  const msgs = ["Pulling financials…", "Running the DCF model…",
    "Scoring quality &amp; balance sheet…", "Asking Claude for the qualitative read…"];
  let mi = 0; showLoading(msgs[0]);
  const timer = setInterval(() => { mi = (mi + 1) % msgs.length; $("loadingMsg").innerHTML = msgs[mi]; }, 1400);
  try {
    const useAi = $("useAi").checked;
    const d = await getJSON(`/api/analyze?ticker=${encodeURIComponent(ticker)}&use_ai=${useAi}${assumptionsQS()}`);
    clearInterval(timer); $("loading").classList.add("hidden");
    renderAnalysis(d);
  } catch (e) {
    clearInterval(timer); showError(e.message);
  }
}

function renderAnalysis(d) {
  const cur = d.info.currency === "USD" ? "$" : (d.info.currency || "") + " ";
  const rs = RATING_STYLE[d.verdict.rating] || RATING_STYLE["HOLD / WATCH"];
  const el = $("results"); el.innerHTML = "";
  el.append(
    verdictCard(d, rs, cur), watchlistControl(d), metricsGrid(d, cur), dcfSection(d, cur),
    monteCarloSection(d, cur), scenariosSection(d, cur), forensicsSection(d),
    refinancingSection(d, cur), leverageTrendSection(d), workingCapitalSection(d),
    dividendCoverageSection(d, cur), intangiblesSection(d, cur), ddSection(d, cur), divSafetySection(d, cur),
    analystSection(d, cur), earningsQualitySection(d, cur), segmentsSection(d),
    returnSection(d), dupontSection(d, cur), sectorRelativeSection(d), pillarsSection(d), flagsSection(d),
    chartsSection(d), qualitativeSection(d), peersSection(d),
    summarySection(d), linksSection(d),
  );
  el.classList.remove("hidden"); el.classList.add("fade-in");
  drawCharts(d, cur);
  wireWatchlistControl(d);
  wirePeers();
  loadSegments(d.ticker);
}

function h(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }

// Confidence caveats under the verdict. Two independent triggers: thin history,
// and free-source disagreement (SimFin vs Yahoo on a foreign filer). Either can
// fire; show both when both apply.
function confidenceBanner(dc) {
  if (!dc || !dc.low) return "";
  const parts = [];
  const div = dc.source_divergence;
  if (div && div.material) {
    const pct = Math.round(div.max_divergence * 100);
    const rows = Object.entries(div.metrics || {}).map(([k, m]) => {
      const label = k === "net_income" ? "net income" : k;
      const fmt = (x) => (x == null ? "—" : (Math.abs(x) >= 1e9 ? "$" + (x / 1e9).toFixed(1) + "B" : "$" + (x / 1e6).toFixed(0) + "M"));
      return `${label} (${m.year}): ${div.primary} ${fmt(m[div.primary])} vs ${div.peer} ${fmt(m[div.peer])}`;
    }).join("; ");
    parts.push(`<div class="mt-4 text-xs bg-bad/10 border border-bad/40 text-bad rounded-lg p-2.5 leading-relaxed"><strong>Data sources disagree</strong> — ${div.primary} and ${div.peer} differ by ~${pct}% on recent fundamentals (${rows}). This name isn't covered by SEC EDGAR (a foreign 20-F filer), so there's no authoritative statement set to reconcile against. Any fair value here is unreliable — treat the score as untrustworthy until you check the filings yourself.</div>`);
  }
  if (dc.years != null && dc.years < 6) {
    parts.push(`<div class="mt-4 text-xs bg-warn/10 border border-warn/40 text-warn rounded-lg p-2.5 leading-relaxed"><strong>Low confidence</strong> — only ${dc.years} year${dc.years === 1 ? "" : "s"} of financial history available (a recent listing, or a foreign filer on shallow data). Growth rates, through-cycle medians and the DCF are all less reliable with this little history, so weight the score accordingly.</div>`);
  }
  if (dc.debt_estimated) {
    parts.push(`<div class="mt-4 text-xs bg-warn/10 border border-warn/40 text-warn rounded-lg p-2.5 leading-relaxed"><strong>Debt estimated</strong> — this filer's XBRL debt tags understate its borrowings (typically a finance subsidiary, e.g. Ford Credit), so total debt was estimated from interest expense. Leverage, net-cash and refinancing figures here are approximate — verify against the balance sheet.</div>`);
  }
  return parts.join("");
}

function verdictCard(d, rs, cur) {
  const v = d.verdict, info = d.info;
  const dash = 264, off = dash - (dash * v.score) / 100;
  return h(`
  <section class="card rounded-2xl p-6">
    <div class="flex flex-col md:flex-row md:items-center gap-6">
      <div class="flex-1">
        <div class="flex items-center gap-3 flex-wrap">
          <h2 class="text-2xl font-semibold">${info.name}</h2>
          <span class="text-muted text-sm font-mono px-2 py-0.5 rounded bg-ink/60 border border-line">${d.ticker}</span>
          ${d.data_source ? `<span class="text-[10px] px-2 py-0.5 rounded ${d.data_source === "SimFin" ? "bg-good/15 text-good border border-good/40" : "bg-ink/60 text-muted border border-line"}">${d.data_source}</span>` : ""}
        </div>
        <p class="text-muted text-sm mt-1">${[info.sector, info.industry].filter(Boolean).join(" · ") || ""}</p>
        <div class="flex items-end gap-4 mt-4 flex-wrap">
          <div><div class="text-xs text-muted">Price</div><div class="text-2xl font-semibold">${price(info.current_price, cur)}</div></div>
          <div><div class="text-xs text-muted">Market cap</div><div class="text-lg">${cur}${fmtMoney(info.market_cap)}</div></div>
          ${info.analyst_target != null ? `<div><div class="text-xs text-muted">Analyst target</div><div class="text-lg">${price(info.analyst_target, cur)}</div></div>` : ""}
        </div>
      </div>
      <div class="flex items-center gap-5">
        <div class="relative w-28 h-28 grid place-items-center">
          <svg class="w-28 h-28 -rotate-90" viewBox="0 0 100 100">
            <circle cx="50" cy="50" r="42" fill="none" stroke="#26334a" stroke-width="9"/>
            <circle cx="50" cy="50" r="42" fill="none" stroke="${rs.ring}" stroke-width="9" stroke-linecap="round" stroke-dasharray="${dash}" stroke-dashoffset="${off}"/>
          </svg>
          <div class="absolute text-center"><div class="text-3xl font-bold">${v.score}</div><div class="text-[10px] text-muted -mt-1">/ 100</div></div>
        </div>
        <div class="text-center md:text-left max-w-[220px]">
          <div class="inline-block px-3 py-1 rounded-lg ${rs.bg} border ${rs.br} text-${rs.c} font-bold text-lg">${v.rating}</div>
          <p class="text-sm text-muted mt-2 leading-snug">${v.stance}</p>
        </div>
      </div>
    </div>
    ${confidenceBanner(d.metrics.data_confidence)}
  </section>`);
}

function metricsGrid(d, cur) {
  const m = d.metrics, mm = m.multiples, g = m.growth, r = m.returns, b = m.balance;
  const eq = m.earnings_quality || {};
  const cell = (label, val, sub = "", subColor = "") => `
    <div class="bg-ink/40 rounded-xl p-3 border border-line/60">
      <div class="text-xs text-muted">${label}</div><div class="text-lg font-semibold mt-0.5">${val}</div>
      ${sub ? `<div class="text-[11px] ${subColor || "text-muted"} mt-0.5">${sub}</div>` : ""}
    </div>`;
  const peCaveat = eq.heavy_capex ? "flattered by capex cycle" : "";
  return h(`
  <section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-4 flex items-center gap-2">Key metrics <span class="text-xs text-muted font-normal">(${g.years_of_data || "?"}yr of statements · free data tier)</span></h3>
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
      ${cell("Trailing P/E", fmtNum(mm.trailing_pe, 1), peCaveat, "text-warn")}
      ${cell("Forward P/E", fmtNum(mm.forward_pe, 1))}
      ${mm.peg_ratio != null
        ? cell("PEG", fmtNum(mm.peg_ratio, 2))
        : cell("PEG", fmtNum(mm.peg_trailing, 2), mm.peg_trailing != null ? "trailing · vs past growth" : "")}
      ${cell("Price / Book", fmtNum(mm.price_to_book, 2))}
      ${cell("Price / FCF", fmtNum(mm.price_to_fcf, 1))}
      ${cell("Dividend yield", fmtPct(mm.dividend_yield))}
      ${cell("Revenue CAGR", fmtPct(g.revenue_cagr), `${g.years_of_data || "?"}yr`)}
      ${cell("EPS CAGR", fmtPct(g.eps_cagr), `${g.years_of_data || "?"}yr`)}
      ${cell("FCF CAGR", fmtPct(g.fcf_cagr), `${g.years_of_data || "?"}yr`)}
      ${cell("ROE (avg)", fmtPct(r.roe_avg))}
      ${cell("ROIC (avg)", fmtPct(r.roic_avg))}
      ${cell("Net margin", fmtPct(m.margins.net.latest))}
      ${cell("Debt / Equity", fmtNum(b.debt_to_equity, 2))}
      ${cell("Interest cov.", b.interest_coverage != null ? fmtNum(b.interest_coverage, 1) + "×" : "—")}
      ${cell("Current ratio", fmtNum(b.current_ratio, 2))}
      ${cell("Net cash", b.net_cash != null ? cur + fmtMoney(b.net_cash) : "—")}
    </div>
  </section>`);
}

function dcfSection(d, cur) {
  const dcf = d.metrics.dcf, val = d.metrics.valuation;
  if (!val || !val.ok) {
    return h(`<section class="card rounded-2xl p-6"><h3 class="font-semibold mb-2">Discounted cash flow</h3>
      <p class="text-muted text-sm">${(dcf && dcf.reason) || "DCF unavailable — no positive cash flow to project."}</p></section>`);
  }
  const a = dcf.assumptions || {};
  const px = val.current_price, up = val.upside_mid;
  const upColor = up == null ? "muted" : up >= 0.15 ? "good" : up >= 0 ? "warn" : "bad";
  const lo = val.low, hi = val.high, mid = val.mid;
  const wide = (val.spread || 0) >= 0.4;
  // bar scaled to max of (high value, price)
  const barMax = Math.max(hi || 0, px || 0) * 1.1 || 1;
  const pos = (x) => Math.min(100, Math.max(0, ((x || 0) / barMax) * 100));
  const methodNote = val.method === "ffo"
    ? "Valued on <span class='text-slate-300'>funds from operations (FFO)</span> — the REIT standard. GAAP earnings are crushed by real-estate depreciation and book value understates the property, so FFO (net income + real-estate D&amp;A) is discounted as an equity-level stream. The debt isn't subtracted again — FFO is already after interest."
    : val.method === "book-value"
    ? "Valued on the <span class='text-slate-300'>justified price-to-book</span> model — the tool bank &amp; insurance analysts actually use: fair P/B = (ROE − g) / (r − g), applied to book value per share. A financial is worth a premium to book only insofar as its return on equity beats the return you require. Uses through-cycle (7-yr median) ROE so a cyclical peak doesn't inflate it."
    : val.method === "earnings"
    ? "Valued on <span class='text-slate-300'>earnings power</span> — a free-cash-flow DCF doesn't fit banks / insurers / REITs."
    : "Two DCFs: <span class='text-slate-300'>conservative</span> discounts free cash flow (penalizes all capex); <span class='text-slate-300'>adjusted</span> discounts owner earnings (credits growth capex). The truth sits between.";
  const mn = d.metrics.margin_normalization || {};
  const mnBanner = mn.applied ? `<div class="text-xs bg-warn/10 border border-warn/40 text-warn rounded-lg p-2.5 my-2 leading-relaxed"><strong>Stress test active:</strong> earnings normalized to a ${fmtPct(mn.target_margin, 1)} net margin (${Math.round(mn.factor * 100)}% of the way from the current ${fmtPct(mn.latest_margin, 1)} toward the ${fmtPct(mn.avg_margin, 1)} long-run average). The earnings base is scaled ${mn.ratio.toFixed(2)}×, so this valuation and the score below reflect that assumption — not as-reported earnings.</div>` : "";
  return h(`
  <section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-1">Intrinsic value ${val.method === "ffo" ? "(funds from operations)" : val.method === "book-value" ? "(book value &amp; ROE)" : val.method === "earnings" ? "(earnings power)" : "(range)"}</h3>
    ${mnBanner}
    ${val.suspect ? `<div class="text-xs bg-bad/10 border border-bad/40 text-bad rounded-lg p-2.5 my-2 leading-relaxed"><strong>Valuation flagged unreliable.</strong> ${val.suspect_reason || "Data or model doesn't fit this company."} It's excluded from buy candidates — verify the numbers yourself before trusting them.</div>` : ""}
    <p class="text-xs text-muted mb-4">${methodNote}</p>
    ${val.method === "ffo" ? `
    <div class="grid md:grid-cols-3 gap-3 mb-4">
      <div class="bg-ink/40 rounded-xl p-4 border border-brand/40"><div class="text-xs text-muted">Fair value (FFO-based)</div><div class="text-2xl font-bold text-brand">${price(mid, cur)}</div></div>
      <div class="bg-ink/40 rounded-xl p-4 border border-line/60"><div class="text-xs text-muted">Upside</div><div class="text-2xl font-bold text-${upColor}">${up == null ? "—" : signPct(up)}</div></div>
      <div class="bg-ink/40 rounded-xl p-4 border border-line/60"><div class="text-xs text-muted">FFO / share</div><div class="text-2xl font-bold text-slate-300">${price(val.ffo_per_share, cur)}</div></div>
    </div>
    <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-4">
      <div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">Current P/FFO</div><div class="text-sm font-semibold">${fmtNum(val.current_pffo, 1)}×</div></div>
      <div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">Fair P/FFO (implied)</div><div class="text-sm font-semibold text-brand">${fmtNum(val.fair_pffo, 1)}×</div></div>
      <div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">FFO growth used</div><div class="text-sm font-semibold">${fmtPct(val.ffo_growth, 1)}</div></div>
    </div>` : val.method === "book-value" ? `
    <div class="grid md:grid-cols-3 gap-3 mb-4">
      <div class="bg-ink/40 rounded-xl p-4 border border-brand/40"><div class="text-xs text-muted">Fair value (justified P/B)</div><div class="text-2xl font-bold text-brand">${price(mid, cur)}</div></div>
      <div class="bg-ink/40 rounded-xl p-4 border border-line/60"><div class="text-xs text-muted">Upside</div><div class="text-2xl font-bold text-${upColor}">${up == null ? "—" : signPct(up)}</div></div>
      <div class="bg-ink/40 rounded-xl p-4 border border-line/60"><div class="text-xs text-muted">Book value / share</div><div class="text-2xl font-bold text-slate-300">${price(val.bvps, cur)}</div></div>
    </div>
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
      <div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">Current P/B</div><div class="text-sm font-semibold">${fmtNum(val.current_pb, 2)}×</div></div>
      <div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">Justified P/B</div><div class="text-sm font-semibold text-brand">${fmtNum(val.justified_pb, 2)}×</div></div>
      <div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">Sustainable ROE <span class="opacity-70">(${val.roe_basis || "7yr"})</span></div><div class="text-sm font-semibold">${fmtPct(val.roe_used, 1)}</div></div>
      <div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">Required return</div><div class="text-sm font-semibold">${fmtPct(val.cost_of_equity, 1)}</div></div>
    </div>
    ${val.implied_roe != null ? `<p class="text-[11px] text-muted mb-4 leading-relaxed">At today's ${fmtNum(val.current_pb, 1)}× book, the market is implying a sustainable ROE of about <span class="text-slate-200">${fmtPct(val.implied_roe, 1)}</span> — versus the <span class="text-slate-200">${fmtPct(val.roe_used, 1)}</span> the business has actually earned through the cycle. Above it = the market expects returns to improve; below = it's skeptical.</p>` : ""}` : val.method === "earnings" ? `
    <div class="grid md:grid-cols-2 gap-3 mb-5">
      <div class="bg-ink/40 rounded-xl p-4 border border-brand/40"><div class="text-xs text-muted">Fair value (earnings power)</div><div class="text-2xl font-bold text-brand">${price(mid, cur)}</div></div>
      <div class="bg-ink/40 rounded-xl p-4 border border-line/60"><div class="text-xs text-muted">Upside</div><div class="text-2xl font-bold text-${upColor}">${up == null ? "—" : signPct(up)}</div></div>
    </div>` : `
    <div class="grid md:grid-cols-4 gap-3 mb-5">
      <div class="bg-ink/40 rounded-xl p-4 border border-line/60"><div class="text-xs text-muted">Conservative (FCF)</div><div class="text-xl font-bold text-slate-300">${price(val.conservative_iv, cur)}</div></div>
      <div class="bg-ink/40 rounded-xl p-4 border border-brand/40"><div class="text-xs text-muted">Midpoint (fair value)</div><div class="text-2xl font-bold text-brand">${price(mid, cur)}</div></div>
      <div class="bg-ink/40 rounded-xl p-4 border border-line/60"><div class="text-xs text-muted">Adjusted (owner earnings)</div><div class="text-xl font-bold text-slate-300">${price(val.adjusted_iv, cur)}</div></div>
      <div class="bg-ink/40 rounded-xl p-4 border border-line/60"><div class="text-xs text-muted">Upside to midpoint</div><div class="text-2xl font-bold text-${upColor}">${up == null ? "—" : signPct(up)}</div></div>
    </div>`}
    <div class="relative h-10 bg-ink/50 rounded-lg overflow-hidden border border-line/60 mb-2">
      <div class="absolute top-0 bottom-0 bg-brand/20" style="left:${pos(lo)}%;width:${Math.max(0, pos(hi) - pos(lo))}%"></div>
      <div class="absolute top-0 bottom-0 w-1 bg-brand" style="left:calc(${pos(mid)}% - 2px)"></div>
      <div class="absolute -top-0.5 bottom-0 w-0.5 bg-white" style="left:${pos(px)}%"></div>
      <div class="absolute top-0 bottom-0 border-r-2 border-dashed border-good/70" style="left:${pos(val.buy_below)}%"></div>
    </div>
    <div class="flex justify-between text-[11px] text-muted mb-4 flex-wrap gap-1">
      <span>Value range: ${price(lo, cur)} – ${price(hi, cur)}</span>
      <span class="text-white">▏ Price ${price(px, cur)}</span>
      <span class="text-good">▏ Buy-below (${fmtPct(a.margin_of_safety, 0)} MoS): ${price(val.buy_below, cur)}</span>
    </div>
    ${wide ? `<div class="text-xs bg-warn/10 border border-warn/30 text-warn rounded-lg p-2 mb-3">Wide value range — this company's cash flow and earnings diverge (usually heavy capex). Read the Earnings-quality section below before trusting any single number.</div>` : ""}
    <details class="text-sm">
      <summary class="cursor-pointer text-muted hover:text-brand">DCF assumptions (tune them in the panel above)</summary>
      <div class="grid grid-cols-2 sm:grid-cols-5 gap-3 mt-3 text-xs">
        <div class="bg-ink/40 rounded-lg p-2"><div class="text-muted">Base FCF</div><div>${cur}${fmtMoney(a.base_fcf)}</div></div>
        <div class="bg-ink/40 rounded-lg p-2"><div class="text-muted">Stage-1 growth</div><div>${fmtPct(a.stage1_growth)}</div></div>
        <div class="bg-ink/40 rounded-lg p-2"><div class="text-muted">Terminal growth</div><div>${fmtPct(a.terminal_growth)}</div></div>
        <div class="bg-ink/40 rounded-lg p-2"><div class="text-muted">Discount rate</div><div>${fmtPct(a.discount_rate)}</div></div>
        <div class="bg-ink/40 rounded-lg p-2"><div class="text-muted">Years</div><div>${a.years}</div></div>
      </div>
      ${(d.metrics.risk_premium && Math.abs(d.metrics.risk_premium.premium) >= 0.001) ? `<p class="text-[11px] text-muted mt-2">Discount rate is risk-adjusted per stock by fundamental risk (not beta): base ${fmtPct((d.metrics.assumptions_used || {}).discount_rate, 0)} ${d.metrics.risk_premium.premium >= 0 ? "+" : "−"} ${fmtPct(Math.abs(d.metrics.risk_premium.premium), 1)} (${d.metrics.risk_premium.reasons.join(", ")}) = ${fmtPct(d.metrics.effective_discount_rate, 1)}.</p>` : ""}
      ${(() => { const m = d.metrics.margin_of_safety_scaling; if (!m || Math.abs(m.effective - m.base) < 0.005) return ""; const dir = m.effective < m.base ? "tightened" : "widened"; return `<p class="text-[11px] text-muted mt-1">Margin of safety ${dir} to ${fmtPct(m.effective, 0)} (from ${fmtPct(m.base, 0)}) — certainty ${m.certainty}${m.reasons && m.reasons.length ? " (" + m.reasons.join(", ") + ")" : ""}. Higher-certainty businesses need less discount; shakier ones need more.</p>`; })()}
      ${(dcf.ok && dcf.terminal_pct != null) ? `<p class="text-[11px] ${dcf.terminal_pct >= 0.7 ? "text-warn" : "text-muted"} mt-1"><span class="font-medium">${fmtPct(dcf.terminal_pct, 0)}</span> of the estimate comes from the terminal value${dcf.terminal_pct >= 0.7 ? " — most of the value sits beyond the projection window, so it's highly sensitive to the terminal-growth and discount-rate assumptions." : " (the rest from the explicitly projected years)."}</p>` : ""}
    </details>
  </section>`);
}

function scenariosSection(d, cur) {
  const s = d.metrics.scenarios, r = d.metrics.reverse_dcf;
  if (!s) return h(`<div class="hidden"></div>`);
  const px = s.current_price;
  const row = (label, sc, color) => {
    const up = sc.upside;
    const upc = up == null ? "muted" : up >= 0 ? "good" : "bad";
    return `<tr class="border-b border-line/40 text-sm">
      <td class="py-2 font-medium text-${color}">${label}</td>
      <td class="text-right px-2">${price(sc.fair_value, cur)}</td>
      <td class="text-right px-2 text-${upc}">${up == null ? "—" : signPct(up)}</td></tr>`;
  };
  const impl = r && r.ok ? r.implied_growth : null;
  return h(`
  <section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-4">Scenarios &amp; reverse DCF</h3>
    <div class="grid md:grid-cols-2 gap-6">
      <div>
        <div class="text-sm text-muted mb-2">Bear / base / bull fair value</div>
        <table class="w-full">
          <tr class="text-xs text-muted border-b border-line"><th class="text-left py-1">Scenario</th><th class="text-right px-2">Fair value</th><th class="text-right px-2">Upside</th></tr>
          ${row("Bear", s.bear, "bad")}
          ${row("Base", s.base, "brand")}
          ${row("Bull", s.bull, "good")}
          <tr class="text-sm"><td class="py-2 text-muted">Current price</td><td class="text-right px-2 font-semibold">${price(px, cur)}</td><td></td></tr>
        </table>
        <p class="text-[11px] text-muted mt-2">Bear/bull flex growth, discount rate &amp; terminal growth around the base case.</p>
      </div>
      ${r && r.method === "book-value" ? `
      <div>
        <div class="text-sm text-muted mb-2">Reverse model — what the price implies</div>
        <div class="bg-ink/40 rounded-xl p-4 border border-line/60">
          <div class="text-xs text-muted">Sustainable ROE the market is pricing in</div>
          <div class="text-3xl font-bold text-brand mt-1">${r.implied_roe == null ? "—" : fmtPct(r.implied_roe, 1)}</div>
          <p class="text-sm text-muted mt-2 leading-relaxed">Versus the ${fmtPct((d.metrics.valuation || {}).roe_used, 1)} this financial has actually earned through the cycle. ${r.implied_roe != null && (d.metrics.valuation || {}).roe_used != null ? (r.implied_roe > d.metrics.valuation.roe_used ? "The market expects returns to <span class='text-slate-200'>improve</span> — a demanding bar." : "The market is <span class='text-slate-200'>skeptical</span> — pricing in returns below the historical level.") : ""}</p>
        </div>
      </div>` : `
      <div>
        <div class="text-sm text-muted mb-2">Reverse DCF — what the price implies</div>
        <div class="bg-ink/40 rounded-xl p-4 border border-line/60">
          <div class="text-xs text-muted">Growth the market is pricing in</div>
          <div class="text-3xl font-bold text-brand mt-1">${impl == null ? "—" : (impl >= 0.40 ? "≥40%" : fmtPct(impl, 1)) + "/yr"}</div>
          <p class="text-sm text-muted mt-2 leading-relaxed">Compare with the company's history: revenue ${fmtPct(d.metrics.growth.revenue_cagr, 0)}, FCF ${fmtPct(d.metrics.growth.fcf_cagr, 0)}. ${impl != null && impl >= 0.30 ? "The bar the market sets is demanding — priced for strong growth." : impl != null && impl <= 0.02 ? "The market expects little to no growth — a low bar to beat." : "Ask whether the business can realistically exceed this."}</p>
        </div>
      </div>`}
    </div>
    ${sensitivityGrid(d, cur)}
  </section>`);
}

function sensitivityGrid(d, cur) {
  const s = d.metrics.sensitivity;
  if (!s || !s.ok) return "";
  const upColor = (u) => u == null ? "bg-ink/40 text-muted"
    : u >= 0.25 ? "bg-good/30 text-good" : u >= 0 ? "bg-good/10 text-slate-200"
    : u >= -0.25 ? "bg-bad/10 text-slate-200" : "bg-bad/30 text-bad";
  const isBase = (dr, g) => Math.abs(dr - s.base_discount) < 1e-6 && Math.abs(g - s.base_growth) < 1e-6;
  const head = `<tr><th class="text-[10px] text-muted p-1 text-left">disc ↓ / growth →</th>${s.growth_rates.map(g => `<th class="text-[10px] text-muted p-1">${fmtPct(g, 0)}</th>`).join("")}</tr>`;
  const body = s.cells.map((row, i) => `<tr>
    <td class="text-[10px] text-muted p-1 font-mono">${fmtPct(s.discount_rates[i], 0)}</td>
    ${row.map((c, j) => `<td class="p-1 text-center text-[11px] rounded ${upColor(c.upside)} ${isBase(s.discount_rates[i], s.growth_rates[j]) ? "ring-1 ring-brand" : ""}">
      <div class="font-semibold">${price(c.iv, cur)}</div><div class="text-[9px] opacity-80">${c.upside == null ? "" : signPct(c.upside)}</div></td>`).join("")}
  </tr>`).join("");
  return `<div class="mt-6">
    <div class="text-sm text-muted mb-2">Fair-value sensitivity — discount rate × growth <span class="text-[11px]">(box = base case; green = upside, red = overvalued)</span></div>
    <div class="overflow-x-auto"><table class="w-full border-separate" style="border-spacing:3px">${head}${body}</table></div>
  </div>`;
}

function monteCarloSection(d, cur) {
  const mc = d.metrics.monte_carlo;
  if (!mc || !mc.ok) return h(`<div class="hidden"></div>`);
  const px = mc.current_price, prob = mc.prob_undervalued;
  const probColor = prob >= 0.6 ? "good" : prob >= 0.35 ? "warn" : "bad";
  const lo = mc.p10, hi = mc.p90;
  const base = Math.min(lo, px) * 0.9, span = (Math.max(hi, px) * 1.1 - base) || 1;
  const pos = (x) => Math.min(100, Math.max(0, ((x - base) / span) * 100));
  return h(`
  <section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-1">Monte-Carlo intrinsic value</h3>
    <p class="text-xs text-muted mb-4">${mc.iterations.toLocaleString()} simulations sampling growth, discount rate, terminal growth &amp; starting cash flow. The single DCF above is just one point in this cloud — this shows the whole spread and how often the price looks cheap.</p>
    <div class="grid md:grid-cols-3 gap-3 mb-5">
      <div class="bg-ink/40 rounded-xl p-4 border border-${probColor}/40">
        <div class="text-xs text-muted">Chance it's undervalued</div>
        <div class="text-3xl font-bold text-${probColor}">${fmtPct(prob, 0)}</div>
        <div class="text-[11px] text-muted mt-1">of runs put fair value above ${price(px, cur)}</div>
      </div>
      <div class="bg-ink/40 rounded-xl p-4 border border-brand/40">
        <div class="text-xs text-muted">Median fair value (P50)</div>
        <div class="text-2xl font-bold text-brand">${price(mc.p50, cur)}</div>
        <div class="text-[11px] text-muted mt-1">${signPct(mc.median_upside)} vs price</div>
      </div>
      <div class="bg-ink/40 rounded-xl p-4 border border-line/60">
        <div class="text-xs text-muted">80% range (P10–P90)</div>
        <div class="text-lg font-bold text-slate-300">${price(mc.p10, cur)} – ${price(mc.p90, cur)}</div>
        <div class="text-[11px] text-muted mt-1">width = how assumption-sensitive</div>
      </div>
    </div>
    <div class="relative h-10 bg-ink/50 rounded-lg overflow-hidden border border-line/60 mb-2">
      <div class="absolute top-0 bottom-0 bg-brand/10" style="left:${pos(lo)}%;width:${Math.max(0, pos(hi) - pos(lo))}%"></div>
      <div class="absolute top-0 bottom-0 bg-brand/25" style="left:${pos(mc.p25)}%;width:${Math.max(0, pos(mc.p75) - pos(mc.p25))}%"></div>
      <div class="absolute top-0 bottom-0 w-1 bg-brand" style="left:calc(${pos(mc.p50)}% - 2px)"></div>
      <div class="absolute -top-0.5 bottom-0 w-0.5 bg-white" style="left:${pos(px)}%"></div>
    </div>
    <div class="flex justify-between text-[11px] text-muted flex-wrap gap-1">
      <span>P10 ${price(lo, cur)}</span><span>P25 ${price(mc.p25, cur)}</span>
      <span class="text-brand">P50 ${price(mc.p50, cur)}</span>
      <span>P75 ${price(mc.p75, cur)}</span><span>P90 ${price(hi, cur)}</span>
      <span class="text-white">▏Price ${price(px, cur)}</span>
    </div>
  </section>`);
}

function forensicsSection(d) {
  const fx = d.metrics.forensics;
  if (!fx) return h(`<div class="hidden"></div>`);
  if (!fx.applicable) {
    return h(`<section class="card rounded-2xl p-6">
      <h3 class="font-semibold mb-1">Forensic checks (distress &amp; manipulation)</h3>
      <p class="text-muted text-sm">${fx.reason}</p></section>`);
  }
  const az = fx.altman, bm = fx.beneish;
  const zc = { safe: "good", grey: "warn", distress: "bad" };
  const azCard = az ? `
    <div class="bg-ink/40 rounded-xl p-4 border border-${zc[az.zone]}/40">
      <div class="flex items-center justify-between">
        <div class="text-sm text-muted">Altman Z-score</div>
        <div class="text-[10px] uppercase px-2 py-0.5 rounded bg-${zc[az.zone]}/15 text-${zc[az.zone]}">${az.zone}</div>
      </div>
      <div class="text-3xl font-bold text-${zc[az.zone]} mt-1">${fmtNum(az.z, 2)}</div>
      <p class="text-[11px] text-muted mt-2 leading-relaxed">Distance-to-bankruptcy. &gt;2.99 safe · 1.81–2.99 grey · &lt;1.81 distress. ${az.distress ? "<span class='text-bad'>In the distress zone — elevated failure risk over a long hold.</span>" : az.zone === "grey" ? "<span class='text-warn'>Grey zone — not clearly safe.</span>" : "Comfortably in the safe zone."}</p>
    </div>` : `<div class="bg-ink/40 rounded-xl p-4 border border-line/60"><div class="text-sm text-muted">Altman Z-score</div><div class="text-muted text-sm mt-2">Not enough balance-sheet data to compute.</div></div>`;
  const bc = bm ? (bm.manipulator ? "bad" : bm.level === "elevated" ? "warn" : "good") : "muted";
  const bmCard = bm ? `
    <div class="bg-ink/40 rounded-xl p-4 border border-${bc}/40">
      <div class="flex items-center justify-between">
        <div class="text-sm text-muted">Beneish M-score</div>
        <div class="text-[10px] uppercase px-2 py-0.5 rounded bg-${bc}/15 text-${bc}">${bm.manipulator ? "flagged" : bm.level}</div>
      </div>
      <div class="text-3xl font-bold text-${bc} mt-1">${fmtNum(bm.m, 2)}</div>
      <p class="text-[11px] text-muted mt-2 leading-relaxed">Earnings-manipulation profile. &gt;−1.78 resembles manipulators. ${bm.manipulator ? "<span class='text-bad'>Above the threshold — scrutinize revenue recognition &amp; accruals.</span>" : "Below the manipulation threshold."}${bm.sga_used ? "" : " <span class='opacity-70'>(SG&amp;A unavailable — that index neutralized.)</span>"}</p>
    </div>` : `<div class="bg-ink/40 rounded-xl p-4 border border-line/60"><div class="text-sm text-muted">Beneish M-score</div><div class="text-muted text-sm mt-2">Needs two comparable years of data — not available.</div></div>`;
  return h(`
  <section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-1">Forensic checks (distress &amp; manipulation)</h3>
    <p class="text-xs text-muted mb-4">Two classic screens for the failure modes a DCF and a quality score miss — bankruptcy risk and cooked books. A red flag here docks the score directly.</p>
    <div class="grid md:grid-cols-2 gap-3">${azCard}${bmCard}</div>
  </section>`);
}

function refinancingSection(d, cur) {
  const rf = d.metrics.refinancing;
  if (!rf) return h(`<div class="hidden"></div>`);
  if (!rf.applicable) {
    return h(`<section class="card rounded-2xl p-6">
      <h3 class="font-semibold mb-1">Debt maturities &amp; refinancing risk</h3>
      <p class="text-muted text-sm">${rf.reason}</p></section>`);
  }
  const money = v => v == null ? "—" : (Math.abs(v) >= 1e9 ? cur + (v / 1e9).toFixed(1) + "B"
    : Math.abs(v) >= 1e6 ? cur + (v / 1e6).toFixed(0) + "M" : cur + Math.round(v));
  const lvlColor = { low: "good", moderate: "muted", elevated: "warn", high: "bad" }[rf.level] || "muted";
  const lvlText = { low: "Low", moderate: "Moderate", elevated: "Elevated", high: "High" }[rf.level] || rf.level;

  // Maturity ladder bars (near-term buckets highlighted).
  const lad = rf.ladder || {};
  const buckets = [["1y", lad.debt_mat_y1, true], ["2y", lad.debt_mat_y2, true],
    ["3y", lad.debt_mat_y3], ["4y", lad.debt_mat_y4], ["5y", lad.debt_mat_y5], ["5y+", lad.debt_mat_beyond]];
  const maxB = Math.max(1, ...buckets.map(b => b[1] || 0));
  const ladderViz = rf.has_ladder ? `
    <div class="mt-4">
      <div class="text-xs text-muted mb-2">Principal coming due (from the 10-K maturities table) — <span class="text-warn">amber = near-term wall</span></div>
      <div class="flex items-end gap-3 h-28">
        ${buckets.map(([lbl, v, near]) => `
          <div class="flex-1 flex flex-col items-center justify-end h-full">
            <div class="text-[10px] text-muted mb-1">${v ? money(v) : ""}</div>
            <div class="w-full rounded-t" style="height:${Math.max(2, Math.round((v || 0) / maxB * 88))}px;background:${near ? "#f59e0b" : "#4f9dff"}"></div>
            <div class="text-[10px] text-muted mt-1">${lbl}</div>
          </div>`).join("")}
      </div>
    </div>` : `<p class="text-xs text-muted mt-3">The filer doesn't tag a maturity ladder; using the current portion of long-term debt as the near-term proxy.</p>`;

  const stat = (label, val, hint) => `<div class="bg-ink/40 rounded-lg p-2.5">
    <div class="text-[11px] text-muted">${label}</div><div class="text-lg font-bold">${val}</div>
    ${hint ? `<div class="text-[10px] text-muted mt-0.5">${hint}</div>` : ""}</div>`;
  const cov = rf.coverage;
  const covTxt = cov == null ? "—" : cov.toFixed(1) + "×";
  const icTxt = rf.base_interest_coverage == null ? "n/a"
    : `${rf.base_interest_coverage.toFixed(1)}× → ${rf.stress_interest_coverage.toFixed(1)}×`;
  const bullets = (rf.reasons && rf.reasons.length)
    ? `<ul class="mt-4 space-y-1 text-sm text-${lvlColor}">${rf.reasons.map(r => `<li>• ${r}</li>`).join("")}</ul>`
    : (rf.positive ? `<p class="mt-4 text-sm text-good">✓ ${rf.positive}</p>` : "");

  return h(`
  <section class="card rounded-2xl p-6">
    <div class="flex items-center justify-between mb-1">
      <h3 class="font-semibold">Debt maturities &amp; refinancing risk</h3>
      <div class="text-[10px] uppercase px-2 py-0.5 rounded bg-${lvlColor}/15 text-${lvlColor}">${lvlText} risk</div>
    </div>
    <p class="text-xs text-muted mb-4">Not just how much debt, but <em>when it comes due</em> — can cash + free cash flow cover the near-term wall, and does rolling it at +300bps break interest coverage? A real risk this docks the score.</p>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-2.5">
      ${stat("Near-term wall (≤2yr)", money(rf.near_term_wall), rf.near_term_pct != null ? `${Math.round(rf.near_term_pct * 100)}% of total debt` : "")}
      ${stat("Covered by cash + 2yr FCF", covTxt, cov != null && cov < 1 ? "shortfall" : "")}
      ${stat("Interest cover (base → +300bps)", icTxt, rf.base_interest_coverage == null ? "REIT — n/a" : "EBITDA basis")}
      ${stat("Implied rate on debt", rf.implied_rate != null ? (rf.implied_rate * 100).toFixed(1) + "%" : "—", "interest ÷ total debt")}
    </div>
    ${ladderViz}
    ${bullets}
  </section>`);
}

function dividendCoverageSection(d, cur) {
  const dc = d.metrics.dividend_coverage;
  if (!dc) return h(`<div class="hidden"></div>`);
  if (!dc.applicable) {
    // Non-payers are common — render nothing rather than an empty card.
    if (/doesn't pay/i.test(dc.reason || "")) return h(`<div class="hidden"></div>`);
    return h(`<section class="card rounded-2xl p-6">
      <h3 class="font-semibold mb-1">Dividend coverage</h3>
      <p class="text-muted text-sm">${dc.reason}</p></section>`);
  }
  const col = { comfortable: "good", tight: "warn", uncovered: "bad" }[dc.level] || "muted";
  const txt = { comfortable: "Well covered", tight: "Tight", uncovered: "Uncovered" }[dc.level] || dc.level;
  const money = v => v == null ? "—" : (Math.abs(v) >= 1e9 ? cur + (v / 1e9).toFixed(1) + "B"
    : Math.abs(v) >= 1e6 ? cur + (v / 1e6).toFixed(0) + "M" : cur + Math.round(v));
  const covTxt = dc.fcf_negative ? "FCF negative" : (dc.cum_coverage != null ? dc.cum_coverage.toFixed(2) + "×" : "—");

  const stat = (label, val, hint) => `<div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">${label}</div><div class="text-lg font-bold">${val}</div>${hint ? `<div class="text-[10px] text-muted mt-0.5">${hint}</div>` : ""}</div>`;
  const pct = v => v == null ? "—" : Math.round(v * 100) + "%";

  // FCF vs dividend bars, per year (green if covered, red if not).
  const ser = dc.series || [];
  const maxV = Math.max(1, ...ser.map(p => Math.max(p.fcf, p.dividend)));
  const bars = ser.map(p => `
    <div class="flex-1 flex flex-col items-center justify-end h-full gap-0.5">
      <div class="w-full flex items-end justify-center gap-0.5 h-full">
        <div class="w-2.5 rounded-t" title="${p.year} FCF ${money(p.fcf)}" style="height:${Math.max(1, Math.round(Math.max(p.fcf, 0) / maxV * 70))}px;background:${p.covered ? "#22c55e" : "#4f9dff"}"></div>
        <div class="w-2.5 rounded-t" title="${p.year} dividend ${money(p.dividend)}" style="height:${Math.max(1, Math.round(p.dividend / maxV * 70))}px;background:${p.covered ? "#4f9dff" : "#ef4444"}"></div>
      </div>
      <div class="text-[9px] text-muted">${p.year.slice(2)}</div>
    </div>`).join("");

  // A comfortable dividend keeps its ✓ even when a secondary buyback note is
  // present — show the positive, then any notes.
  const bullets =
    (dc.positive ? `<p class="mt-4 text-sm text-good">✓ ${dc.positive}</p>` : "")
    + ((dc.reasons && dc.reasons.length)
        ? `<ul class="mt-${dc.positive ? "2" : "4"} space-y-1 text-sm text-${col}">${dc.reasons.map(r => `<li>• ${r}</li>`).join("")}</ul>`
        : "");

  return h(`
  <section class="card rounded-2xl p-6">
    <div class="flex items-center justify-between mb-1">
      <h3 class="font-semibold">Dividend coverage (from free cash flow)</h3>
      <div class="text-[10px] uppercase px-2 py-0.5 rounded bg-${col}/15 text-${col}">${txt}</div>
    </div>
    <p class="text-xs text-muted mb-4">Is the dividend funded by the business or by debt / asset sales? Measured on cumulative free cash flow over ${dc.years_window || 5} years, so a one-off heavy-capex year doesn't masquerade as a chronic shortfall.</p>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-2.5">
      ${stat("FCF ÷ dividends (5yr)", covTxt, `${dc.years_covered}/${dc.years_window} years covered`)}
      ${stat("FCF payout ratio", dc.fcf_negative ? ">100%" : pct(dc.fcf_payout_pct), "dividends ÷ FCF")}
      ${stat("Total payout (+ buybacks)", pct(dc.total_payout_pct), "vs free cash flow")}
      ${stat("Earnings payout", pct(dc.earnings_payout_pct), "dividends ÷ net income")}
    </div>
    <div class="mt-4">
      <div class="text-xs text-muted mb-1">Free cash flow vs dividends by year — <span class="text-good">green FCF</span> covers the <span class="text-brand">dividend</span>; <span class="text-bad">red</span> = uncovered</div>
      <div class="flex items-end gap-2 h-24">${bars}</div>
    </div>
    ${bullets}
  </section>`);
}

function leverageTrendSection(d) {
  const lt = d.metrics.leverage_trend;
  if (!lt) return h(`<div class="hidden"></div>`);
  if (!lt.applicable) {
    return h(`<section class="card rounded-2xl p-6">
      <h3 class="font-semibold mb-1">Leverage trend &amp; covenant headroom</h3>
      <p class="text-muted text-sm">${lt.reason}</p></section>`);
  }
  const col = { improving: "good", none: "good", stable: "muted", deteriorating: "warn", stressed: "bad" }[lt.level] || "muted";
  const txt = { improving: "Improving", none: "No leverage", stable: "Stable", deteriorating: "Deteriorating", stressed: "Stressed" }[lt.level] || lt.level;

  // Leverage line: Net Debt/EBITDA over time with threshold markers.
  const lev = lt.leverage;
  const th = lt.thresholds || {};
  let levViz = "";
  if (lev && lev.series && lev.series.length) {
    const xs = lev.series;
    const maxX = Math.max(th.lev_stress || 5, ...xs.map(p => p.x));
    const bar = p => {
      const bad = p.x >= (th.lev_stress || 5), warn = p.x >= (th.lev_elevated || 4);
      return `<div class="flex-1 flex flex-col items-center justify-end h-full">
        <div class="text-[10px] text-muted mb-0.5">${p.x.toFixed(1)}</div>
        <div class="w-full rounded-t" style="height:${Math.max(2, Math.round(p.x / maxX * 70))}px;background:${bad ? "#ef4444" : warn ? "#f59e0b" : "#4f9dff"}"></div>
        <div class="text-[9px] text-muted mt-0.5">${p.year.slice(2)}</div></div>`;
    };
    levViz = `<div class="mt-3">
      <div class="text-xs text-muted mb-1">Net Debt / EBITDA over time — <span class="text-warn">amber ≥${(th.lev_elevated || 4)}×</span> (covenant zone), <span class="text-bad">red ≥${(th.lev_stress || 5)}×</span></div>
      <div class="flex items-end gap-2 h-24">${xs.map(bar).join("")}</div></div>`;
  }

  const stat = (label, val) => `<div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">${label}</div><div class="text-lg font-bold">${val}</div></div>`;
  const cov = lt.coverage;
  const tiles = `<div class="grid grid-cols-2 md:grid-cols-3 gap-2.5">
    ${lev ? stat("Net Debt/EBITDA (now)", lev.latest.toFixed(1) + "×") : ""}
    ${lev ? stat(`vs ${lev.prior_year}`, lev.prior.toFixed(1) + "×") : ""}
    ${cov ? stat("Interest coverage (EBITDA)", cov.latest.toFixed(1) + "×") : ""}
    ${lt.de && !lev ? stat("Debt/Equity (now)", lt.de.latest.toFixed(1) + "×") : ""}
    ${lt.de && !lev ? stat(`vs ${lt.de.prior_year}`, lt.de.prior.toFixed(1) + "×") : ""}
  </div>`;

  const bullets = (lt.reasons && lt.reasons.length)
    ? `<ul class="mt-4 space-y-1 text-sm text-${col}">${lt.reasons.map(r => `<li>• ${r}</li>`).join("")}</ul>`
    : (lt.positive ? `<p class="mt-4 text-sm text-good">✓ ${lt.positive}</p>` : "");

  return h(`
  <section class="card rounded-2xl p-6">
    <div class="flex items-center justify-between mb-1">
      <h3 class="font-semibold">Leverage trend &amp; covenant headroom</h3>
      <div class="text-[10px] uppercase px-2 py-0.5 rounded bg-${col}/15 text-${col}">${txt}</div>
    </div>
    <p class="text-xs text-muted mb-4">The <em>trajectory</em> static leverage misses: is Net Debt/EBITDA climbing and coverage compressing toward the levels where lenders set covenants? (Thresholds are generic loan-market conventions, not this filer's actual terms.)</p>
    ${tiles}
    ${levViz}
    ${bullets}
  </section>`);
}

function intangiblesSection(d, cur) {
  const ig = d.metrics.intangibles;
  if (!ig || !ig.applicable) return h(`<div class="hidden"></div>`);  // most names: skip quietly
  if (ig.level === "low") return h(`<div class="hidden"></div>`);      // clean: no need for a card
  const col = { moderate: "muted", elevated: "warn", high: "bad" }[ig.level] || "muted";
  const txt = { moderate: "Some", elevated: "Elevated", high: "High" }[ig.level] || ig.level;
  const money = v => v == null ? "—" : (Math.abs(v) >= 1e9 ? cur + (v / 1e9).toFixed(1) + "B"
    : Math.abs(v) >= 1e6 ? cur + (v / 1e6).toFixed(0) + "M" : cur + Math.round(v));
  const stat = (label, val, hint) => `<div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">${label}</div><div class="text-lg font-bold">${val}</div>${hint ? `<div class="text-[10px] text-muted mt-0.5">${hint}</div>` : ""}</div>`;
  const bullets = (ig.reasons && ig.reasons.length)
    ? `<ul class="mt-4 space-y-1 text-sm text-${col}">${ig.reasons.map(r => `<li>• ${r}</li>`).join("")}</ul>` : "";
  return h(`
  <section class="card rounded-2xl p-6">
    <div class="flex items-center justify-between mb-1">
      <h3 class="font-semibold">Acquisition accounting &amp; impairment risk</h3>
      <div class="text-[10px] uppercase px-2 py-0.5 rounded bg-${col}/15 text-${col}">${txt}</div>
    </div>
    <p class="text-xs text-muted mb-4">How much of the balance sheet is acquired goodwill vs real, built assets. When goodwill exceeds equity, one writedown erases the shareholders' cushion — the roll-up / impairment pattern behind many blow-ups.</p>
    <div class="grid grid-cols-2 md:grid-cols-3 gap-2.5">
      ${stat("Goodwill + intangibles", `${Math.round((ig.gi_to_assets || 0) * 100)}%`, "of total assets")}
      ${stat("Tangible book equity", money(ig.tangible_equity), ig.tangible_negative ? "negative — goodwill > equity" : "positive")}
      ${stat("Impairment vs equity", ig.impair_vs_equity != null ? ig.impair_vs_equity.toFixed(1) + "×" : "—", "a full writedown vs book")}
    </div>
    ${bullets}
  </section>`);
}

function workingCapitalSection(d) {
  const wc = d.metrics.working_capital;
  if (!wc) return h(`<div class="hidden"></div>`);
  if (!wc.applicable) {
    return h(`<section class="card rounded-2xl p-6">
      <h3 class="font-semibold mb-1">Working-capital quality</h3>
      <p class="text-muted text-sm">${wc.reason}</p></section>`);
  }
  const lvlColor = { low: "good", moderate: "warn", elevated: "bad" }[wc.level] || "muted";
  const lvlText = { low: "Clean", moderate: "Watch", elevated: "Concern" }[wc.level] || wc.level;

  const comp = (c, label) => {
    if (!c) return `<div class="bg-ink/40 rounded-xl p-4 border border-line/60">
      <div class="text-sm text-muted">${label}</div>
      <div class="text-muted text-sm mt-2">Not separately reported.</div></div>`;
    const col = c.level === "elevated" ? "bad" : c.level === "moderate" ? "warn" : "good";
    const arrow = c.ratio >= 1.12 ? "↑" : c.ratio <= 0.92 ? "↓" : "→";
    // mini day-trend sparkline
    const ser = c.series || [];
    const maxD = Math.max(1, ...ser.map(p => p.days));
    const spark = ser.map(p => `<span class="inline-block align-bottom rounded-t" title="${p.year}: ${p.days.toFixed(0)}d" style="width:8px;background:#4f9dff;height:${Math.max(2, Math.round(p.days / maxD * 30))}px"></span>`).join("");
    return `<div class="bg-ink/40 rounded-xl p-4 border border-${col}/40">
      <div class="flex items-center justify-between">
        <div class="text-sm text-muted">${label} <span class="opacity-70">(days per ${c.denom})</span></div>
        <div class="text-[10px] uppercase px-2 py-0.5 rounded bg-${col}/15 text-${col}">${c.level}</div>
      </div>
      <div class="text-3xl font-bold text-${col} mt-1">${c.days_latest.toFixed(0)}<span class="text-sm text-muted font-normal"> days ${arrow}</span></div>
      <div class="text-[11px] text-muted mt-1">vs a ~${c.days_base.toFixed(0)}-day recent norm (${c.ratio >= 1 ? "+" : ""}${((c.ratio - 1) * 100).toFixed(0)}%)</div>
      <div class="flex items-end gap-0.5 h-8 mt-2">${spark}</div>
    </div>`;
  };

  const bullets = (wc.reasons && wc.reasons.length)
    ? `<ul class="mt-4 space-y-1 text-sm text-${lvlColor}">${wc.reasons.map(r => `<li>• ${r}</li>`).join("")}</ul>`
    : (wc.positive ? `<p class="mt-4 text-sm text-good">✓ ${wc.positive}</p>` : "");

  return h(`
  <section class="card rounded-2xl p-6">
    <div class="flex items-center justify-between mb-1">
      <h3 class="font-semibold">Working-capital quality</h3>
      <div class="text-[10px] uppercase px-2 py-0.5 rounded bg-${lvlColor}/15 text-${lvlColor}">${lvlText}</div>
    </div>
    <p class="text-xs text-muted mb-4">Receivables or inventory growing faster than sales is an early warning — cash trapped in working capital, channel-stuffing / aggressive revenue recognition, or inventory that isn't selling. It's the trend that matters, not the absolute level.</p>
    <div class="grid md:grid-cols-2 gap-3">${comp(wc.receivables, "Days sales outstanding (receivables)")}${comp(wc.inventory, "Days inventory outstanding")}</div>
    ${bullets}
  </section>`);
}

function ddSection(d, cur) {
  const dd = d.metrics.due_diligence;
  if (!dd) return h(`<div class="hidden"></div>`);
  const cell = (label, val, sub = "", color = "") => `
    <div class="bg-ink/40 rounded-xl p-3 border border-line/60">
      <div class="text-xs text-muted">${label}</div><div class="text-lg font-semibold mt-0.5 ${color}">${val}</div>
      ${sub ? `<div class="text-[11px] text-muted mt-0.5">${sub}</div>` : ""}</div>`;
  const dil = dd.dilution_cagr;
  return h(`
  <section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-4">Valuation multiples &amp; capital efficiency</h3>
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
      ${cell("EV / EBITDA", fmtNum(dd.ev_to_ebitda, 1))}
      ${cell("EV / EBIT", fmtNum(dd.ev_to_ebit, 1))}
      ${cell("EV / Revenue", fmtNum(dd.ev_to_revenue, 1))}
      ${cell("FCF yield", fmtPct(dd.fcf_yield), "FCF ÷ market cap")}
      ${cell("Net debt / EBITDA", dd.net_debt_to_ebitda != null ? fmtNum(dd.net_debt_to_ebitda, 2) + "×" : "—",
             "", dd.net_debt_to_ebitda != null && dd.net_debt_to_ebitda > 3 ? "text-warn" : "")}
      ${cell("ROIC (NOPAT)", fmtPct(dd.roic_nopat_avg), "avg, on invested capital")}
      ${cell("WACC", fmtPct(dd.wacc), "cost of capital")}
      ${cell("ROIC − WACC", dd.roic_vs_wacc_spread != null ? signPct(dd.roic_vs_wacc_spread) : "—",
             dd.creates_value ? "creates value ✓" : "destroys value",
             dd.creates_value ? "text-good" : (dd.roic_vs_wacc_spread != null ? "text-bad" : ""))}
      ${cell("FCF margin", fmtPct(dd.fcf_margin_avg), "FCF ÷ revenue")}
      ${cell("FCF / share", dd.fcf_per_share != null ? price(dd.fcf_per_share, cur) : "—")}
      ${cell("Share count trend", dil != null ? signPct(dil) + "/yr" : "—",
             dil != null ? (dil > 0.01 ? "dilution" : dil < -0.01 ? "buybacks ✓" : "flat") : "",
             dil != null && dil > 0.02 ? "text-warn" : dil != null && dil < -0.005 ? "text-good" : "")}
      ${cell("Insider ownership", fmtPct(dd.held_percent_insiders, 1))}
      ${cell("Institutional own.", fmtPct(dd.held_percent_institutions, 0))}
      ${cell("Effective tax rate", fmtPct(dd.effective_tax_rate, 0))}
      ${cell("Price / Sales", fmtNum(dd.price_to_sales, 1))}
      ${cell("Return on Assets", fmtPct(dd.return_on_assets))}
      ${(() => {
        const p = dd.piotroski;
        if (!p) return cell("Piotroski F-Score", "—", "financial health");
        const c = p.score >= 7 ? "text-good" : p.score <= 3 ? "text-bad" : "";
        return cell("Piotroski F-Score", `${p.score}/9`, p.score >= 7 ? "strong" : p.score <= 3 ? "weak" : "moderate", c);
      })()}
      ${(() => {
        const cr = dd.capital_returns || {};
        return cell("Shareholder yield", fmtPct(cr.shareholder_yield, 1),
          cr.buyback_yield != null || cr.dividends_paid != null ? `${fmtPct(cr.buyback_yield, 1)} buyback + div` : "dividends + buybacks");
      })()}
      ${cell("Payout ratio", fmtPct((dd.capital_returns || {}).payout_ratio, 0), "dividends ÷ earnings")}
    </div>
    ${(() => {
      const vh = dd.valuation_vs_history || {};
      const row = (label, m) => {
        if (!m) return "";
        const p = m.premium_to_avg;
        const c = p == null ? "muted" : p <= -0.1 ? "good" : p >= 0.1 ? "bad" : "muted";
        const tag = p == null ? "" : p <= -0.1 ? "cheap vs its history" : p >= 0.1 ? "rich vs its history" : "in line";
        return `<div class="flex items-center justify-between text-sm py-1.5 border-b border-line/40">
          <span class="text-muted">${label}</span>
          <span>now <span class="font-semibold">${fmtNum(m.current, 1)}</span> · ${m.years}yr avg ${fmtNum(m.avg, 1)} (range ${fmtNum(m.min, 1)}–${fmtNum(m.max, 1)})
          <span class="text-${c} ml-2">${p == null ? "" : signPct(p) + " · " + tag}</span></span></div>`;
      };
      const rows = row("P/E", vh.pe) + row("P/FCF", vh.pfcf);
      return rows ? `<div class="mt-5"><div class="text-sm font-medium mb-1">Valuation vs its own history</div>${rows}
        <p class="text-[11px] text-muted mt-1.5">Trading below its own multi-year average can signal a better-than-usual entry (or a broken thesis — check why).</p></div>` : "";
    })()}
  </section>`);
}

function analystSection(d, cur) {
  const i = d.info;
  const hasAny = i.analyst_target != null || i.num_analysts != null ||
    i.short_pct_float != null || i.insider_net_shares != null;
  if (!hasAny) return h(`<div class="hidden"></div>`);
  const px = i.current_price;
  const tgtUp = (i.analyst_target && px) ? (i.analyst_target - px) / px : null;
  const upc = tgtUp == null ? "muted" : tgtUp >= 0 ? "good" : "bad";
  const net = i.insider_net_shares;
  const insiderColor = net == null ? "muted" : net > 0 ? "good" : net < 0 ? "bad" : "muted";
  const insiderLabel = net == null ? "—" : net > 0 ? "net buying" : net < 0 ? "net selling" : "neutral";
  const cell = (label, val, sub = "", color = "") => `
    <div class="bg-ink/40 rounded-xl p-3 border border-line/60">
      <div class="text-xs text-muted">${label}</div><div class="text-lg font-semibold mt-0.5 ${color}">${val}</div>
      ${sub ? `<div class="text-[11px] text-muted mt-0.5">${sub}</div>` : ""}</div>`;
  return h(`
  <section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-1">Analyst view &amp; sentiment <span class="text-xs text-muted font-normal">· free Yahoo data</span></h3>
    <p class="text-xs text-muted mb-4">Wall Street's estimates and market positioning — useful as a *contrarian* cross-check, not a mandate (the crowd is often wrong on 10-15yr value).</p>
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
      ${cell("Analyst target (mean)", price(i.analyst_target, cur), tgtUp != null ? signPct(tgtUp) + " to target" : "", tgtUp != null ? "text-" + upc : "")}
      ${cell("Target range", (i.target_low != null && i.target_high != null) ? `${price(i.target_low, cur)}–${price(i.target_high, cur)}` : "—", i.num_analysts != null ? `${Math.round(i.num_analysts)} analysts` : "")}
      ${cell("Recommendation", i.recommendation ? i.recommendation.replace("_", " ") : "—", i.recommendation_mean != null ? `${fmtNum(i.recommendation_mean, 1)}/5 (1=buy)` : "")}
      ${cell("Forward P/E", fmtNum(i.forward_pe, 1), "on est. next-yr EPS")}
      ${cell("Short interest", fmtPct(i.short_pct_float, 1), i.short_ratio != null ? `${fmtNum(i.short_ratio, 1)} days to cover` : "of float")}
      ${cell("Insider activity (6mo)", insiderLabel, net != null ? `net ${Math.abs(Math.round(net)).toLocaleString()} sh` : "", "text-" + insiderColor)}
    </div>
  </section>`);
}

function divSafetySection(d, cur) {
  const ds = (d.metrics.due_diligence || {}).dividend_safety;
  if (!ds || !ds.pays_dividend) return h(`<div class="hidden"></div>`);
  const cov = ds.fcf_coverage, pay = ds.payout_ratio;
  const safe = cov == null ? null : (cov >= 2 && (pay == null || pay < 0.6));
  const badge = safe == null ? { t: "—", c: "muted" } : safe ? { t: "Well covered", c: "good" } : { t: "Watch coverage", c: "warn" };
  const cell = (label, val, sub = "", color = "") => `
    <div class="bg-ink/40 rounded-xl p-3 border border-line/60"><div class="text-xs text-muted">${label}</div>
      <div class="text-lg font-semibold mt-0.5 ${color}">${val}</div>${sub ? `<div class="text-[11px] text-muted mt-0.5">${sub}</div>` : ""}</div>`;
  return h(`<section class="card rounded-2xl p-6">
    <div class="flex items-center justify-between mb-4"><h3 class="font-semibold">Dividend safety</h3>
      <span class="text-xs px-2 py-0.5 rounded bg-${badge.c}/15 text-${badge.c} border border-${badge.c}/40">${badge.t}</span></div>
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
      ${cell("Dividend / share", price(ds.dps_latest, cur))}
      ${cell("Dividend growth", fmtPct(ds.dividend_growth_cagr), `${ds.years}yr CAGR`)}
      ${cell("FCF coverage", cov != null ? fmtNum(cov, 1) + "×" : "—", "FCF ÷ dividends", cov != null && cov < 1.2 ? "text-warn" : "")}
      ${cell("Payout ratio", fmtPct(pay, 0), "dividends ÷ earnings", pay != null && pay > 0.8 ? "text-warn" : "")}
    </div></section>`);
}

// Revenue (and, when disclosed, operating income) by segment — parsed live from
// the latest 10-K's XBRL. Auto-loaded because it's a heavy per-filing fetch.
function segmentsSection(d) {
  return h(`<section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-1">Revenue &amp; profit by segment <span class="text-xs text-muted font-normal">· from the latest 10-K</span></h3>
    <p class="text-xs text-muted mb-4">Where the money actually comes from — and, when the filing discloses it, where the <em>profit</em> comes from (often a very different picture).</p>
    <div id="segmentsBody" class="text-sm text-muted">Loading segment breakdown from the latest 10-K…</div>
  </section>`);
}

async function loadSegments(ticker) {
  const body = $("segmentsBody");
  if (!body) return;
  let r;
  try { r = await getJSON(`/api/segments?ticker=${encodeURIComponent(ticker)}`); }
  catch (e) { body.innerHTML = `<span class="text-muted">Segment data unavailable for this name.</span>`; return; }
  if (!r || r.error || !r.breakdowns || !r.breakdowns.length) {
    body.innerHTML = `<span class="text-muted">No segment disclosure found in the filing — the company reports as a single segment, or isn't a US 10-K filer.</span>`;
    return;
  }
  const money = (x) => x == null ? "—" : "$" + fmtMoney(x);
  const parts = r.breakdowns.map(b => {
    const totalOI = b.has_oi ? b.segments.reduce((s, x) => s + (x.operating_income || 0), 0) : 0;
    const rows = b.segments.map(s => {
      const pct = Math.round(s.revenue_pct * 100);
      const om = (s.operating_income != null && s.revenue) ? s.operating_income / s.revenue : null;
      return `<div class="mb-2.5">
        <div class="flex justify-between items-baseline text-sm mb-1 gap-3">
          <span class="truncate">${s.name}</span>
          <span class="text-slate-300 shrink-0 text-right">${money(s.revenue)} <span class="text-muted text-xs">${pct}%</span>${b.has_oi && s.operating_income != null ? ` · <span class="text-${om != null && om >= 0 ? "good" : "bad"}">op ${money(s.operating_income)}${om != null ? ` @ ${fmtPct(om, 0)}` : ""}</span>` : ""}</span>
        </div>
        <div class="h-2 bg-ink/60 rounded-full overflow-hidden"><div class="h-full bg-brand rounded-full" style="width:${pct}%"></div></div>
      </div>`;
    }).join("");
    let insight = "";
    if (b.has_oi && totalOI > 0) {
      const top = b.segments.filter(s => s.operating_income != null).sort((a, c) => c.operating_income - a.operating_income)[0];
      if (top) {
        const ps = Math.round(top.operating_income / totalOI * 100), rs = Math.round(top.revenue_pct * 100);
        if (ps - rs >= 10) insight = `<p class="text-[11px] text-warn mt-1"><strong>${top.name}</strong> is only ${rs}% of revenue but ~${ps}% of segment operating profit — the profit engine.</p>`;
      }
    }
    return `<div class="mb-5"><div class="text-sm font-medium mb-2">${b.label}${b.has_oi ? "" : ' <span class="text-[11px] text-muted font-normal">(revenue only — segment profit not disclosed)</span>'}</div>${rows}${insight}</div>`;
  }).join("");
  body.innerHTML = `<div class="text-[11px] text-muted mb-4">FY${r.fiscal_year} · parsed from ${r.ticker}'s 10-K filing</div>${parts}`;
}

function peersSection(d) {
  return h(`<section class="card rounded-2xl p-6">
    <div class="flex items-center justify-between"><h3 class="font-semibold">Same-sector peers</h3>
      <button id="loadPeers" data-ticker="${d.ticker}" class="text-sm bg-brand/20 text-brand border border-brand/40 px-3 py-1.5 rounded-lg hover:bg-brand/30 transition">Load peers</button></div>
    <div id="peersBody" class="mt-3 text-sm text-muted">Compare ${d.ticker} against curated same-sector names on score, valuation, ROIC and growth.</div>
  </section>`);
}

function benchmarkTable(subject, peers) {
  const median = (arr) => {
    const v = arr.filter(x => x != null).sort((a, b) => a - b);
    if (!v.length) return null;
    const m = Math.floor(v.length / 2);
    return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
  };
  const metrics = [
    { label: "Score", key: "score", higher: true, fmt: v => v == null ? "—" : v },
    { label: "ROIC", key: "roic", higher: true, fmt: v => fmtPct(v, 0) },
    { label: "Net margin", key: "net_margin", higher: true, fmt: v => fmtPct(v, 0) },
    { label: "Revenue CAGR", key: "revenue_cagr", higher: true, fmt: v => fmtPct(v, 0) },
    { label: "P/E", key: "trailing_pe", higher: false, fmt: v => fmtNum(v, 1) },
  ];
  const rows = metrics.map(m => {
    const sv = subject[m.key], med = median(peers.map(p => p[m.key]));
    const better = (sv != null && med != null) ? (m.higher ? sv >= med : sv <= med) : null;
    return `<tr class="border-b border-line/40 text-sm">
      <td class="py-1.5 text-muted">${m.label}</td>
      <td class="text-right px-3 font-semibold">${m.fmt(sv)}</td>
      <td class="text-right px-3 text-muted">${m.fmt(med)}</td>
      <td class="text-right pl-3 text-${better == null ? "muted" : better ? "good" : "bad"}">${better == null ? "—" : better ? "▲ better" : "▼ worse"}</td></tr>`;
  }).join("");
  return h(`<div class="mb-4">
    <div class="text-sm font-medium mb-1">${subject.ticker} vs sector median <span class="text-muted font-normal">(${peers.length} peers)</span></div>
    <table class="w-full"><tr class="text-xs text-muted border-b border-line"><th class="text-left py-1">Metric</th><th class="text-right px-3">${subject.ticker}</th><th class="text-right px-3">Sector median</th><th class="text-right pl-3">vs peers</th></tr>${rows}</table>
  </div>`);
}

function wirePeers() {
  const btn = $("loadPeers");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const body = $("peersBody");
    body.innerHTML = `<span class="text-muted">Loading peers…</span>`;
    btn.disabled = true;
    try {
      const r = await getJSON(`/api/peers?ticker=${encodeURIComponent(btn.dataset.ticker)}`);
      if (!r.rows.length) { body.innerHTML = `<span class="text-muted">${r.note || "No curated peers found."}</span>`; return; }
      body.innerHTML = "";
      const subject = r.rows.find(x => x.ticker === r.peers_of);
      const peers = r.rows.filter(x => x.ticker !== r.peers_of);
      if (subject && peers.length) body.appendChild(benchmarkTable(subject, peers));
      body.appendChild(bareTable(r.rows, true));
      body.querySelectorAll("tr[data-ticker]").forEach(tr => tr.addEventListener("click", () => {
        const t = tr.dataset.ticker; switchMode("analyze"); $("ticker").value = t; analyze(t); window.scrollTo({ top: 0, behavior: "smooth" });
      }));
    } catch (e) { body.innerHTML = `<span class="text-bad">${e.message}</span>`; }
    finally { btn.disabled = false; }
  });
}

function linksSection(d) {
  const t = d.ticker;
  const edgar = `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&ticker=${t}&type=10-K&dateb=&owner=include&count=40`;
  const q = `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&ticker=${t}&type=10-Q&dateb=&owner=include&count=40`;
  const proxy = `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&ticker=${t}&type=DEF+14A&dateb=&owner=include&count=40`;
  const link = (href, label) => `<a href="${href}" target="_blank" rel="noopener" class="text-brand hover:underline">${label}</a>`;
  const gaps = [
    ["Debt maturity schedule", "10-K → notes on long-term debt"],
    ["Insider transaction detail (who / when / price)", "SEC Form 4 (EDGAR) — the 6-month net is shown above"],
    ["Segment revenue, customer / supplier concentration", "10-K → business & MD&A"],
    ["Management guidance & tone, analyst Q&A", "earnings-call transcripts"],
    ["Deeper automated history (10-20yr)", "free to view on macrotrends.net; $15/mo SimFin to automate here"],
  ];
  return h(`
  <section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-2">Primary sources (do the reading)</h3>
    <p class="text-xs text-muted mb-3">The model can't read filings or earnings calls for you — these are the documents to go through before buying.</p>
    <div class="flex flex-wrap gap-x-5 gap-y-1.5 text-sm mb-5">
      ${link(edgar, "10-K (annual) ↗")} ${link(q, "10-Q (quarterly) ↗")} ${link(proxy, "DEF 14A (proxy) ↗")}
      ${d.info.website ? link(d.info.website, "Company site ↗") : ""}
      ${link(`https://www.google.com/search?q=${t}+earnings+call+transcript`, "Earnings call transcripts ↗")}
    </div>
    <div class="text-sm font-medium mb-2 text-muted">Not in free data — check these yourself:</div>
    <ul class="space-y-1">
      ${gaps.map(([what, where]) => `<li class="text-xs flex gap-2"><span class="text-warn">•</span><span><span class="text-slate-300">${what}</span> — ${where}</span></li>`).join("")}
    </ul>
  </section>`);
}

function earningsQualitySection(d, cur) {
  const e = d.metrics.earnings_quality;
  if (!e) return h(`<div class="hidden"></div>`);
  const phaseLabel = {
    intense_buildout: { t: "Intense build-out", c: "bad" },
    investing: { t: "Investing / building", c: "warn" },
    steady_state: { t: "Steady state", c: "good" },
    harvesting: { t: "Harvesting (under-investing)", c: "warn" },
    unknown: { t: "Unknown", c: "muted" },
  }[e.phase] || { t: e.phase, c: "muted" };
  const ratio = e.capex_to_dep || e.capex_to_dep_avg;
  const cell = (label, val, sub = "", color = "") => `
    <div class="bg-ink/40 rounded-xl p-3 border border-line/60">
      <div class="text-xs text-muted">${label}</div>
      <div class="text-lg font-semibold mt-0.5 ${color}">${val}</div>
      ${sub ? `<div class="text-[11px] text-muted mt-0.5">${sub}</div>` : ""}
    </div>`;
  return h(`
  <section class="card rounded-2xl p-6">
    <div class="flex items-center justify-between flex-wrap gap-2 mb-1">
      <h3 class="font-semibold">Earnings quality &amp; capex cycle</h3>
      <span class="text-xs px-2 py-0.5 rounded bg-${phaseLabel.c}/15 text-${phaseLabel.c} border border-${phaseLabel.c}/40">${phaseLabel.t}</span>
    </div>
    <p class="text-xs text-muted mb-4">Capex hits earnings slowly (as depreciation) but cash immediately. When a company builds hard, earnings look flattered (P/E cheap) and FCF looks depressed — this is where you check for that.</p>
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 mb-4">
      ${cell("Capex ÷ depreciation", ratio != null ? ratio.toFixed(1) + "×" : "—",
             "≈1× steady · >1.5× building", ratio >= 2.5 ? "text-bad" : ratio >= 1.5 ? "text-warn" : "")}
      ${cell("Capex ÷ op. cash flow", fmtPct(e.capex_intensity, 0), "how cash-hungry")}
      ${cell("Maintenance capex", e.maintenance_capex != null ? cur + fmtMoney(e.maintenance_capex) : "—", "to sustain the business")}
      ${cell("Growth capex", e.growth_capex != null ? cur + fmtMoney(e.growth_capex) : "—", "discretionary expansion")}
      ${cell("Owner earnings", e.owner_earnings != null ? cur + fmtMoney(e.owner_earnings) : "—", "NI + D&A − maint. capex")}
      ${cell("Net income", e.net_income_latest != null ? cur + fmtMoney(e.net_income_latest) : "—", "reported")}
      ${cell("Cash conversion", fmtPct(e.cash_conversion_avg, 0), "FCF ÷ net income",
             e.cash_conversion_avg != null && e.cash_conversion_avg < 0.7 ? "text-warn" : "")}
      ${cell("Stock-based comp", e.stock_based_comp != null ? cur + fmtMoney(e.stock_based_comp) : "—",
             e.sbc_pct_ocf != null ? fmtPct(e.sbc_pct_ocf, 0) + " of op. cash flow" : "dilution cost",
             e.sbc_pct_ocf != null && e.sbc_pct_ocf >= 0.15 ? "text-warn" : "")}
    </div>
    ${(() => {
      const notes = [...(e.notes || []), ...((d.metrics.due_diligence || {}).accrual_flags || [])];
      return notes.length ? `<div class="space-y-2">${notes.map(n =>
        `<div class="text-xs bg-warn/10 border border-warn/30 text-warn rounded-lg p-2.5 leading-relaxed">${n}</div>`).join("")}</div>`
        : `<p class="text-xs text-good">No major earnings-quality red flags — capex, depreciation, accruals and cash flow are broadly in line.</p>`;
    })()}
  </section>`);
}

function returnSection(d) {
  const e = d.metrics.expected_return;
  const er = e.expected_annual_return, beats = e.beats_inflation;
  const c = er == null ? "muted" : beats ? "good" : "bad";
  return h(`
  <section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-4">Expected return vs inflation <span class="text-xs text-muted font-normal">(~${e.horizon_years}yr hold)</span></h3>
    <div class="flex flex-wrap items-center gap-6">
      <div><div class="text-xs text-muted">Est. annual return</div><div class="text-3xl font-bold text-${c}">${er == null ? "—" : fmtPct(er)}</div></div>
      <div class="text-muted text-2xl">vs</div>
      <div><div class="text-xs text-muted">Inflation hurdle</div><div class="text-3xl font-bold text-muted">${fmtPct(e.inflation_hurdle)}</div></div>
      <div class="flex-1 min-w-[180px] text-right">
        <div class="inline-block px-3 py-1.5 rounded-lg ${beats ? "bg-good/15 text-good border border-good/40" : "bg-bad/15 text-bad border border-bad/40"} font-medium">
          ${er == null ? "Not enough data" : beats ? `Beats inflation by ${fmtPct(e.real_return_vs_inflation)} real` : "Does NOT beat inflation"}
        </div>
      </div>
    </div>
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-5 text-xs">
      <div class="bg-ink/40 rounded-lg p-2"><div class="text-muted">Underlying growth</div><div>${fmtPct(e.underlying_growth)}</div></div>
      <div class="bg-ink/40 rounded-lg p-2"><div class="text-muted">FCF yield</div><div>${fmtPct(e.fcf_yield)}</div></div>
      <div class="bg-ink/40 rounded-lg p-2"><div class="text-muted">Dividend yield</div><div>${fmtPct(e.dividend_yield)}</div></div>
      <div class="bg-ink/40 rounded-lg p-2"><div class="text-muted">Mispricing reversion</div><div>${fmtPct(e.reversion_return)}</div></div>
    </div>
  </section>`);
}

function pillarsSection(d) {
  const v = d.verdict;
  const pillars = v.pillars || [];
  const raw = pillars.reduce((s, p) => s + p.points, 0);
  const maxTotal = pillars.reduce((s, p) => s + p.max, 0) || 100;
  const penalty = v.forensic_penalty || 0;
  const final = v.score;
  const rc = { "BUY": "good", "HOLD / WATCH": "warn", "AVOID": "bad" }[v.rating] || "warn";

  const weighted = v.strategy && v.strategy !== "balanced";
  const rows = pillars.map(p => {
    const pct = p.max ? Math.round((p.points / p.max) * 100) : 0;
    const col = pct >= 66 ? "good" : pct >= 40 ? "warn" : "bad";
    return `<div class="mb-3">
      <div class="flex justify-between text-sm mb-1"><span>${p.name}</span><span class="font-mono text-${col}">${weighted && p.weight != null ? `<span class="text-[10px] text-brand mr-2">weight ${p.weight}</span>` : ""}${p.points}<span class="text-muted">/${p.max}</span></span></div>
      <div class="h-2 bg-ink/60 rounded-full overflow-hidden"><div class="h-full bg-${col} rounded-full" style="width:${pct}%"></div></div>
      <p class="text-[11px] text-muted mt-1">${p.note}</p></div>`;
  }).join("");

  const strongest = pillars.reduce((a, b) => (b.points > a.points ? b : a), pillars[0] || {});
  const biggestGap = pillars.map(p => ({ p, gap: p.max - p.points }))
    .reduce((a, b) => (b.gap > a.gap ? b : a), { p: pillars[0] || {}, gap: 0 }).p;

  // 0–100 band: AVOID (<50) | HOLD (50–69) | BUY (≥70), with a marker at the score.
  const band = `
    <div class="relative h-7 rounded-lg overflow-hidden border border-line/60 flex text-[10px] font-semibold">
      <div class="bg-bad/25 text-bad grid place-items-center" style="width:50%">AVOID</div>
      <div class="bg-warn/25 text-warn grid place-items-center" style="width:20%">HOLD</div>
      <div class="bg-good/25 text-good grid place-items-center" style="width:30%">BUY</div>
      <div class="absolute -top-0.5 -bottom-0.5 w-1 bg-white rounded" style="left:calc(${Math.min(100, Math.max(0, final))}% - 2px)"></div>
    </div>
    <div class="flex justify-between text-[10px] text-muted mt-1 mb-5"><span>0</span><span>50</span><span>70</span><span>100</span></div>`;

  return h(`
  <section class="card rounded-2xl p-6">
    <div class="flex items-center justify-between mb-1 flex-wrap gap-2">
      <h3 class="font-semibold">Why this score</h3>
      <div class="text-sm">Final <span class="font-bold text-${rc}">${final}/100</span> · <span class="text-${rc} font-medium">${v.rating}</span></div>
    </div>
    <p class="text-xs text-muted mb-4">Six pillars, weighted by the <span class="text-slate-200">${v.strategy_label || "Balanced"}</span> strategy${weighted ? " (the weight column shows the tilt)" : ""}; red flags — distress, manipulation, a refinancing wall, deteriorating leverage or an uncovered dividend — dock the score.</p>
    ${band}
    <div class="grid grid-cols-3 gap-2 mb-5 text-center">
      <div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">${weighted ? "Weighted score" : "Pillar points"}</div><div class="text-lg font-bold">${Math.min(100, final + penalty)}<span class="text-muted text-sm">/100</span></div></div>
      <div class="bg-ink/40 rounded-lg p-2.5"><div class="text-[11px] text-muted">Red-flag penalty</div><div class="text-lg font-bold ${penalty > 0 ? "text-bad" : "text-muted"}">${penalty > 0 ? "−" + penalty : "0"}</div></div>
      <div class="bg-ink/40 rounded-lg p-2.5 border border-${rc}/40"><div class="text-[11px] text-muted">Final score</div><div class="text-lg font-bold text-${rc}">${final}</div></div>
    </div>
    ${rows}
    <div class="mt-4 pt-3 border-t border-line/40 grid sm:grid-cols-2 gap-2 text-[11px]">
      <div><span class="text-good font-medium">Carrying the score:</span> <span class="text-muted">${strongest.name || "—"} (${strongest.points ?? "—"}/${strongest.max ?? "—"})</span></div>
      <div><span class="text-bad font-medium">Biggest drag:</span> <span class="text-muted">${biggestGap.name || "—"} (${biggestGap.points ?? "—"}/${biggestGap.max ?? "—"})</span></div>
    </div>
  </section>`);
}

function flagsSection(d) {
  const g = d.verdict.green_flags, r = d.verdict.red_flags;
  if (!g.length && !r.length) return h(`<div class="hidden"></div>`);
  const list = (items, color, icon) => (Array.isArray(items) && items.length)
    ? `<ul class="space-y-1.5">${items.map(x => `<li class="flex gap-2 text-sm"><span class="text-${color}">${icon}</span><span>${x}</span></li>`).join("")}</ul>`
    : `<p class="text-muted text-sm">None flagged.</p>`;
  return h(`<section class="grid md:grid-cols-2 gap-6">
    <div class="card rounded-2xl p-6"><h3 class="font-semibold mb-3 text-good">✓ Strengths</h3>${list(g, "good", "✓")}</div>
    <div class="card rounded-2xl p-6"><h3 class="font-semibold mb-3 text-bad">Watch-outs</h3>${list(r, "bad", "")}</div></section>`);
}

// DuPont — decomposes ROE into profitability x efficiency x leverage, so you can
// see whether a high return is earned (margins/efficiency) or juiced by debt.
function dupontSection(d, cur) {
  const dp = (d.metrics.due_diligence || {}).dupont;
  if (!dp || !dp.latest) return h(`<div class="hidden"></div>`);
  const L = dp.latest, drv = dp.driver;
  const box = (label, sub, val, color) => `
    <div class="bg-ink/40 rounded-xl px-3 py-3 border border-line/60 text-center min-w-[92px]">
      <div class="text-[10px] text-muted">${label}</div>
      <div class="text-lg font-bold ${color || ""}">${val}</div>
      <div class="text-[9px] text-muted mt-0.5">${sub}</div></div>`;
  const op = (s) => `<div class="text-muted text-lg font-semibold px-0.5">${s}</div>`;
  const x = (v) => fmtNum(v, 2) + "×";
  const driverNote = drv ? `Over ${L.year - drv.prior_year} years ROE went ${drv.prior_roe < L.roe ? "up" : "down"} from ${fmtPct(drv.prior_roe, 1)} (${drv.prior_year}) to ${fmtPct(L.roe, 1)}, driven mainly by <span class="text-slate-200">${drv.direction} ${drv.factor}</span>.` : "";
  const leverageWarn = L.equity_multiplier >= 3
    ? `<p class="text-[11px] text-warn mt-2">Leverage (${x(L.equity_multiplier)}) is doing a lot of the work here — a high ROE built on debt is lower-quality than one built on margins or efficiency.</p>` : "";
  const fivePart = L.operating_margin != null ? `
    <details class="text-xs mt-4">
      <summary class="cursor-pointer text-muted hover:text-brand">5-factor breakdown (splits the margin into tax, interest &amp; operating)</summary>
      <div class="flex flex-wrap items-center justify-center gap-2 mt-3">
        ${box("Tax burden", "NI/pretax", fmtNum(L.tax_burden, 2))}${op("×")}
        ${box("Interest burden", "pretax/EBIT", fmtNum(L.interest_burden, 2))}${op("×")}
        ${box("Op. margin", "EBIT/rev", fmtPct(L.operating_margin, 1))}${op("×")}
        ${box("Asset turnover", "rev/assets", x(L.asset_turnover))}${op("×")}
        ${box("Equity mult.", "assets/equity", x(L.equity_multiplier))}
      </div></details>` : "";
  return h(`
  <section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-1">DuPont — what drives the ROE</h3>
    <p class="text-xs text-muted mb-4">Return on equity split into its three levers: how profitable, how hard the assets work, and how much leverage. A high ROE from margins or efficiency is higher quality than one juiced by debt.</p>
    <div class="flex flex-wrap items-center justify-center gap-2">
      ${box("ROE", L.year, fmtPct(L.roe, 1), "text-brand")}${op("=")}
      ${box("Net margin", "profitability", fmtPct(L.net_margin, 1))}${op("×")}
      ${box("Asset turnover", "efficiency", x(L.asset_turnover))}${op("×")}
      ${box("Equity mult.", "leverage", x(L.equity_multiplier))}
    </div>
    ${driverNote ? `<p class="text-xs text-muted mt-4">${driverNote}</p>` : ""}
    ${leverageWarn}
    ${fivePart}
  </section>`);
}

// How the name stacks up against its own sector's medians — because a metric
// that's elite for a utility is mediocre for software.
function sectorRelativeSection(d) {
  const sr = d.metrics.sector_relative;
  if (!sr || !sr.covered || !Object.keys(sr.metrics || {}).length) return h(`<div class="hidden"></div>`);
  const labels = { roic: "ROIC", net_margin: "Net margin", revenue_cagr: "Revenue growth", trailing_pe: "P/E", price_to_fcf: "P/FCF" };
  const isMult = (k) => k === "trailing_pe" || k === "price_to_fcf";
  const vcolor = { well_above: "good", above: "good", inline: "muted", below: "warn", well_below: "bad" };
  const qLabel = { well_above: "well above", above: "above", inline: "in line", below: "below", well_below: "well below" };
  const mLabel = { well_above: "much cheaper", above: "cheaper", inline: "in line", below: "pricier", well_below: "much pricier" };
  const fmtv = (k, x) => isMult(k) ? fmtNum(x, 1) + "×" : fmtPct(x, 1);
  const rows = Object.entries(sr.metrics).map(([k, c]) => `
    <div class="flex items-center justify-between bg-ink/40 rounded-lg px-3 py-2 text-sm">
      <span>${labels[k] || k}</span>
      <span class="flex items-center gap-3">
        <span class="text-slate-300">${fmtv(k, c.value)}</span>
        <span class="text-[11px] text-muted">sector ${fmtv(k, c.median)}</span>
        <span class="text-[10px] px-2 py-0.5 rounded bg-${vcolor[c.verdict]}/15 text-${vcolor[c.verdict]} w-[76px] text-center">${(isMult(k) ? mLabel : qLabel)[c.verdict]}</span>
      </span></div>`).join("");
  return h(`
  <section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-1">Versus its sector <span class="text-xs text-muted font-normal">· ${sr.sector}</span></h3>
    <p class="text-xs text-muted mb-4">Each metric against the median ${sr.sector} company — a 13% ROIC is elite for a utility, mediocre for software. Context only; it doesn't move the score (backtesting showed a sector nudge added no forward-return signal).</p>
    <div class="space-y-2">${rows}</div>
  </section>`);
}

// Synthesized "Bottom line" — composes the key model outputs into one readable
// conclusion. Deterministic (no AI), so it's always present.
function summarySection(d) {
  const v = d.verdict, m = d.metrics;
  const val = m.valuation || {}, exp = m.expected_return || {}, mc = m.monte_carlo || {};
  const cur = d.info.currency === "USD" ? "$" : (d.info.currency || "") + " ";
  const rc = { "BUY": "good", "HOLD / WATCH": "warn", "AVOID": "bad" }[v.rating] || "warn";
  const name = d.info.name || d.ticker;

  const methodWord = val.method === "ffo" ? "on funds from operations (FFO)"
    : val.method === "book-value" ? "on book value &amp; through-cycle ROE"
    : val.method === "earnings" ? "on earnings power" : "on discounted cash flow";
  const up = val.upside_mid;
  let valSentence;
  if (val.suspect) {
    valSentence = `The valuation is <span class="text-bad">flagged unreliable</span> (${val.suspect_reason || "the model doesn't fit this company"}), so the upside shouldn't be trusted at face value.`;
  } else if (val.ok && up != null) {
    valSentence = `Fair value lands near <span class="text-slate-200">${price(val.mid, cur)}</span> (valued ${methodWord}) — ${Math.abs(up * 100).toFixed(0)}% ${up >= 0 ? "above" : "below"} today's ${price(val.current_price, cur)}${mc.ok ? `, and Monte-Carlo puts the odds it's undervalued at <span class="text-slate-200">${fmtPct(mc.prob_undervalued, 0)}</span>` : ""}.`;
  } else {
    valSentence = "A reliable intrinsic value couldn't be computed here.";
  }

  const er = exp.expected_annual_return;
  const retSentence = er != null
    ? ` Expected long-term return is ~<span class="text-slate-200">${fmtPct(er, 1)}/yr</span>, which ${exp.beats_inflation ? "clears" : "does <span class='text-bad'>not</span> clear"} the ${fmtPct(exp.inflation_hurdle, 0)} inflation bar.`
    : "";

  const strengths = (v.green_flags || []).slice(0, 3);
  const risks = (v.red_flags || []).slice(0, 3);

  // The crux: the single most decision-relevant takeaway.
  const pillars = v.pillars || [];
  const drag = pillars.map(p => ({ p, gap: p.max - p.points }))
    .reduce((a, b) => (b.gap > a.gap ? b : a), { p: pillars[0] || {}, gap: -1 }).p;
  let crux;
  if (val.suspect) {
    crux = "Verify the underlying data before acting — the model doesn't fit this name cleanly.";
  } else if (m.data_confidence && m.data_confidence.source_divergence && m.data_confidence.source_divergence.material) {
    const dv = m.data_confidence.source_divergence;
    crux = `${dv.primary} and ${dv.peer} disagree by ~${Math.round(dv.max_divergence * 100)}% on recent fundamentals and there's no EDGAR filing to arbitrate — don't trust any fair value here until you reconcile the numbers against the company's own reports.`;
  } else if (m.data_confidence && m.data_confidence.low) {
    crux = `Only ${m.data_confidence.years} years of financial history — treat the score as tentative, lean on the qualitative read, and wait for more of a track record before a decade-plus commitment.`;
  } else if (m.cyclical_peak && m.cyclical_peak.peak && v.rating !== "AVOID") {
    crux = "The upside leans on currently-elevated profitability. Use the margin-normalization slider above to see whether the case survives margins reverting to their long-run average.";
  } else if (v.rating === "BUY") {
    crux = "Clears the quality-at-a-fair-price bar for a decade-plus hold — but read the watch-outs and do your own diligence before buying.";
  } else if (v.rating === "HOLD / WATCH") {
    crux = "A decent business, but the current price (or one weak pillar) leaves too little margin of safety — one to watch for a better entry, not to buy today.";
  } else {
    crux = `Falls short of the bar for a decade-plus hold${drag && drag.name ? `, weakest on <span class="text-slate-200">${drag.name.toLowerCase()}</span>` : ""}. Better opportunities likely exist.`;
  }

  const bullets = (items, color, icon, empty) => items.length
    ? `<ul class="space-y-1">${items.map(s => `<li class="text-xs text-muted flex gap-2"><span class="text-${color} shrink-0">${icon}</span><span>${s}</span></li>`).join("")}</ul>`
    : `<p class="text-xs text-muted">${empty}</p>`;

  return h(`
  <section class="card rounded-2xl p-6 border-${rc}/40">
    <h3 class="font-semibold mb-3">Bottom line</h3>
    <p class="text-sm leading-relaxed mb-4">
      <span class="font-semibold text-${rc}">${name}: ${v.rating} · ${v.score}/100.</span>
      ${valSentence}${retSentence}
    </p>
    <div class="grid sm:grid-cols-2 gap-4 mb-4">
      <div><div class="text-xs text-good font-medium mb-1.5">What's working</div>${bullets(strengths, "good", "✓", "—")}</div>
      <div><div class="text-xs text-bad font-medium mb-1.5">What to watch</div>${bullets(risks, "bad", "!", "Nothing major flagged.")}</div>
    </div>
    <p class="text-sm text-slate-200 bg-ink/40 rounded-lg p-3 border border-${rc}/30"><span class="text-${rc} font-medium">The crux:</span> ${crux}</p>
    <p class="text-[10px] text-muted mt-3">Auto-generated synthesis of the analysis above — not new information, and not investment advice.</p>
  </section>`);
}

function chartsSection(d) {
  return h(`<section class="card rounded-2xl p-6"><h3 class="font-semibold mb-4">Trends</h3>
    <div class="grid md:grid-cols-2 gap-6">
      <div><div class="text-sm text-muted mb-2">Price (10yr)</div><canvas id="c_price" height="150"></canvas></div>
      <div><div class="text-sm text-muted mb-2">Revenue</div><canvas id="c_rev" height="150"></canvas></div>
      <div><div class="text-sm text-muted mb-2">Free cash flow</div><canvas id="c_fcf" height="150"></canvas></div>
      <div><div class="text-sm text-muted mb-2">EPS &amp; ROE</div><canvas id="c_eps" height="150"></canvas></div>
      <div><div class="text-sm text-muted mb-2">Margins (gross / operating / net)</div><canvas id="c_margins" height="150"></canvas></div>
      <div><div class="text-sm text-muted mb-2">ROIC &amp; ROE over time</div><canvas id="c_roic" height="150"></canvas></div>
    </div></section>`);
}

function drawCharts(d, cur) {
  Object.values(charts).forEach(c => c && c.destroy());
  const grid = "#1f2b3e", tick = "#8ba0bd";
  const baseOpts = (fmt) => ({
    responsive: true,
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: (c) => (c.dataset.label ? c.dataset.label + ": " : "") + fmt(c.parsed.y) } },  // same clean format as the axis
    },
    scales: { x: { grid: { color: grid }, ticks: { color: tick, maxTicksLimit: 8, font: { size: 10 } } },
      y: { grid: { color: grid }, ticks: { color: tick, font: { size: 10 }, callback: fmt } } },
  });
  const bar = (id, labels, data, color, fmt) => {
    const ctx = document.getElementById(id); if (!ctx) return;
    charts[id] = new Chart(ctx, { type: "bar",
      data: { labels, datasets: [{ data, backgroundColor: color, borderRadius: 4 }] }, options: baseOpts(fmt) });
  };
  const ph = d.price_history || [];
  const pctx = document.getElementById("c_price");
  if (pctx) charts.c_price = new Chart(pctx, { type: "line",
    data: { labels: ph.map(p => p.date.slice(0, 7)), datasets: [{ data: ph.map(p => p.close), borderColor: "#4f9dff", backgroundColor: "rgba(79,157,255,.12)", fill: true, pointRadius: 0, borderWidth: 2, tension: .25 }] },
    options: baseOpts(v => axisMoney(v, cur)) });
  const s = d.metrics.series;
  bar("c_rev", s.revenue.map(x => x.year), s.revenue.map(x => x.value), "#4f9dff", v => axisMoney(v, cur));
  bar("c_fcf", s.fcf.map(x => x.year), s.fcf.map(x => x.value), s.fcf.map(x => x.value < 0 ? "#ef4444" : "#22c55e"), v => axisMoney(v, cur));
  const epsCtx = document.getElementById("c_eps");
  if (epsCtx) {
    const years = s.eps.map(x => x.year);
    const roeMap = Object.fromEntries(s.roe.map(x => [x.year, x.value]));
    charts.c_eps = new Chart(epsCtx, {
      data: { labels: years, datasets: [
        { type: "bar", data: s.eps.map(x => x.value), backgroundColor: "#4f9dff", borderRadius: 4, yAxisID: "y" },
        { type: "line", data: years.map(y => roeMap[y] ?? null), borderColor: "#22c55e", borderWidth: 2, pointRadius: 0, tension: .25, yAxisID: "y1" }] },
      options: { responsive: true,
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: (c) => c.dataset.yAxisID === "y1"
            ? "ROE " + (c.parsed.y * 100).toFixed(1) + "%"
            : "EPS " + axisMoney(c.parsed.y, cur) } } },
        scales: { x: { grid: { color: grid }, ticks: { color: tick, font: { size: 10 } } },
          y: { grid: { color: grid }, ticks: { color: tick, font: { size: 10 }, callback: v => axisMoney(v, cur) } },
          y1: { position: "right", grid: { drawOnChartArea: false }, ticks: { color: "#22c55e", font: { size: 10 }, callback: v => (v * 100).toFixed(0) + "%" } } } },
    });
  }

  // Align each series to a shared year axis (nulls for missing years) so
  // datasets with differing year sets don't shift against the x-axis.
  const alignPct = (id, defs) => { // defs: [{label, series, color}]
    const ctx = document.getElementById(id); if (!ctx) return;
    const years = [...new Set(defs.flatMap(d => (d.series || []).map(p => p.year)))].sort();
    if (!years.length) return;
    const opts = baseOpts(v => (v * 100).toFixed(0) + "%");
    opts.plugins.legend = { display: true, labels: { color: tick, font: { size: 10 }, boxWidth: 10 } };
    charts[id] = new Chart(ctx, {
      type: "line",
      data: { labels: years, datasets: defs.map(d => {
        const m = Object.fromEntries((d.series || []).map(p => [p.year, p.value]));
        return { label: d.label, data: years.map(y => y in m ? m[y] : null), borderColor: d.color, pointRadius: 0, borderWidth: 2, tension: .25 };
      }) },
      options: opts,
    });
  };
  alignPct("c_margins", [
    { label: "Gross", series: s.gross_margin, color: "#4f9dff" },
    { label: "Operating", series: s.operating_margin, color: "#f59e0b" },
    { label: "Net", series: s.net_margin, color: "#22c55e" }]);
  alignPct("c_roic", [
    { label: "ROIC", series: s.roic, color: "#4f9dff" },
    { label: "ROE", series: s.roe, color: "#22c55e" }]);
}

function qualitativeSection(d) {
  const q = d.qualitative;
  if (q.skipped) return h(`<div class="hidden"></div>`);
  const moatColor = { Wide: "good", Narrow: "warn", None: "bad", Unknown: "muted" }[q.moat?.rating] || "muted";
  const mgmtColor = { Strong: "good", Adequate: "warn", Concerns: "bad", Unknown: "muted" }[q.management?.rating] || "muted";
  const banner = !q.available ? `<div class="mb-4 text-xs bg-warn/10 border border-warn/30 text-warn rounded-lg p-2">${q.error ? q.error : "AI analysis is off — set ANTHROPIC_API_KEY to enable Claude's qualitative read."}</div>` : "";
  const chips = (arr, color) => (Array.isArray(arr) && arr.length) ? arr.map(x => `<li class="flex gap-2 text-sm mb-1.5"><span class="text-${color} mt-0.5">•</span><span>${x}</span></li>`).join("") : "";
  return h(`
  <section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-4">Qualitative read ${q.available ? '<span class="text-xs text-brand font-normal">· Claude</span>' : ""}</h3>${banner}
    <p class="text-sm leading-relaxed mb-5">${q.business_summary || ""}</p>
    <div class="grid md:grid-cols-2 gap-4 mb-5">
      <div class="bg-ink/40 rounded-xl p-4 border border-line/60">
        <div class="flex items-center justify-between mb-1"><span class="text-sm font-medium">Economic moat</span>
          <span class="text-xs px-2 py-0.5 rounded bg-${moatColor}/15 text-${moatColor} border border-${moatColor}/40">${q.moat?.rating || "—"}</span></div>
        <p class="text-xs text-muted leading-relaxed">${q.moat?.reasoning || ""}</p></div>
      <div class="bg-ink/40 rounded-xl p-4 border border-line/60">
        <div class="flex items-center justify-between mb-1"><span class="text-sm font-medium">Management</span>
          <span class="text-xs px-2 py-0.5 rounded bg-${mgmtColor}/15 text-${mgmtColor} border border-${mgmtColor}/40">${q.management?.rating || "—"}</span></div>
        <p class="text-xs text-muted leading-relaxed">${q.management?.reasoning || ""}</p></div>
    </div>
    ${(q.bull_case?.length || q.bear_case?.length) ? `<div class="grid md:grid-cols-2 gap-4 mb-5">
      <div><div class="text-sm font-medium text-good mb-2">Bull case</div><ul>${chips(q.bull_case, "good")}</ul></div>
      <div><div class="text-sm font-medium text-bad mb-2">Bear case</div><ul>${chips(q.bear_case, "bad")}</ul></div></div>` : ""}
    ${q.risks?.length ? `<div class="mb-5"><div class="text-sm font-medium text-warn mb-2">Key risks (10–15yr)</div><ul>${chips(q.risks, "warn")}</ul></div>` : ""}
    ${(q.catalysts?.length || q.thesis_breakers?.length) ? `<div class="grid md:grid-cols-2 gap-4 mb-5">
      ${q.catalysts?.length ? `<div><div class="text-sm font-medium text-good mb-2">Catalysts</div><ul>${chips(q.catalysts, "good")}</ul></div>` : "<div></div>"}
      ${q.thesis_breakers?.length ? `<div><div class="text-sm font-medium text-bad mb-2">What would break the thesis</div><ul>${chips(q.thesis_breakers, "bad")}</ul></div>` : ""}
    </div>` : ""}
    ${q.investment_thesis ? `<div class="bg-ink/40 border border-line/60 rounded-xl p-4 mb-3"><div class="text-xs text-slate-300 font-medium mb-1">Investment thesis${q.cyclicality && q.cyclicality !== "Unknown" ? ` · <span class="text-muted">${q.cyclicality}</span>` : ""}</div><p class="text-sm leading-relaxed">${q.investment_thesis}</p></div>` : ""}
    ${q.verdict_narrative ? `<div class="bg-brand/5 border border-brand/20 rounded-xl p-4"><div class="text-xs text-brand font-medium mb-1">Value-investor take</div><p class="text-sm leading-relaxed">${q.verdict_narrative}</p></div>` : ""}
  </section>`);
}

// ================= COMPARE =================
async function runCompare() {
  const raw = $("compareInput").value.trim();
  if (!raw) return;
  showLoading("Scoring your watchlist…");
  try {
    const d = await getJSON(`/api/compare?tickers=${encodeURIComponent(raw)}${assumptionsQS()}`);
    $("loading").classList.add("hidden");
    renderTable(d, "Watchlist comparison", `${d.rows.length} names, ranked best-first under your assumptions.`);
  } catch (e) { showError(e.message); }
}

// ================= SCREEN =================
// Background screen job state (survives tab switches; server keeps scanning).
let screenPoll = null, screenJob = null, screenState = null, screenResult = null;

async function runScreen() {
  const minScore = parseInt($("minScore").value) || 70;
  const universe = $("screenUniverse").value.trim();
  const scope = $("screenScope").value;
  stopScreenPoll();
  screenResult = null;
  try {
    const qs = `min_score=${minScore}&scope=${scope}${universe ? "&universe=" + encodeURIComponent(universe) : ""}${assumptionsQS()}`;
    const start = await getJSON(`/api/screen/start?${qs}`);
    screenJob = start.job_id;
    screenState = { done: 0, total: start.total, scope: universe ? "your list" : scope };
    showScreenProgress();
    screenPoll = setInterval(pollScreen, 2500);
  } catch (e) { showError(e.message); }
}

function stopScreenPoll() {
  if (screenPoll) { clearInterval(screenPoll); screenPoll = null; }
}

async function pollScreen() {
  if (!screenJob) { stopScreenPoll(); return; }
  let st;
  try { st = await getJSON(`/api/screen/status?job_id=${screenJob}`); }
  catch (e) { stopScreenPoll(); if (currentMode === "screen") showError(e.message); return; }
  screenState = { done: st.done, total: st.total, scope: st.scope, phase: st.phase, deepDone: st.deep_done, deepTotal: st.deep_total };
  if (st.status === "running") {
    if (currentMode === "screen") showScreenProgress();
    return;
  }
  // finished (done or cancelled)
  stopScreenPoll();
  screenResult = st;
  screenJob = null;
  if (currentMode === "screen") { $("loading").classList.add("hidden"); renderScreen(st); }
}

function showScreenProgress() {
  if (!screenState) return;
  const { done, total, scope, phase, deepDone, deepTotal } = screenState;
  const verifying = phase === "verifying";
  const prefiltering = phase === "prefiltering";
  const [d, t] = verifying ? [deepDone || 0, deepTotal || 0] : [done, total];
  const pct = prefiltering ? 0 : (t ? Math.round((d / t) * 100) : 0);
  const label = prefiltering
    ? `Filtering ~5,000 US-listed names by market cap…`
    : verifying
      ? `Deep-verifying top candidates on 10–19yr EDGAR data…`
      : `Scanning <span class="text-slate-200">${scope}</span> universe (fast pass)…`;
  $("results").classList.add("hidden");
  $("error").classList.add("hidden");
  $("hint")?.classList.add("hidden");
  $("loadingMsg").innerHTML = `
    <div class="max-w-md mx-auto text-left">
      <div class="flex justify-between text-sm mb-2"><span>${label}</span>${prefiltering ? `<span class="text-brand font-medium">…</span>` : `<span class="text-brand font-medium">${d} / ${t} (${pct}%)</span>`}</div>
      <div class="h-2.5 bg-ink/60 rounded-full overflow-hidden border border-line/60"><div class="h-full bg-${verifying ? "good" : "brand"} rounded-full ${prefiltering ? "animate-pulse w-1/3" : ""}" style="${prefiltering ? "" : `width:${pct}%;`}transition:width .4s"></div></div>
      <div class="text-xs text-muted mt-3">${prefiltering ? "Trimming the ~5,000 US-listed names to the investable ~2,000 (market cap ≥ $2B, price ≥ $5) before the full scan." : verifying ? "Re-scoring the plausible candidates on deep history so the final list matches the single-stock view." : "Runs in the background — you can switch tabs or keep using the app; the scan keeps going and you can come back to it."}</div>
      <button id="cancelScreen" class="mt-3 text-xs text-muted hover:text-bad underline decoration-dotted">Stop scan (keep results so far)</button>
    </div>`;
  $("loading").classList.remove("hidden");
  const c = $("cancelScreen");
  if (c) c.onclick = async () => {
    if (screenJob) { try { await getJSON(`/api/screen/cancel?job_id=${screenJob}`); } catch (e) {} }
  };
}

// Returning to the Screen tab: resume an in-flight scan's progress, or re-show
// the finished results (the server-side job kept running while you were away).
function resumeScreen() {
  if (screenResult) { $("loading").classList.add("hidden"); renderScreen(screenResult); }
  else if (screenPoll && screenState) showScreenProgress();
}

function renderScreen(d) {
  const el = $("results"); el.innerHTML = "";
  const cands = d.candidates || [];
  const header = h(`
    <section class="card rounded-2xl p-6">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div><h3 class="text-lg font-semibold">Weekly buy screen</h3>
          <p class="text-muted text-sm mt-1">Scanned ${d.scanned}/${d.universe_size} names · buy bar = score ≥ ${d.min_score}${(d.candidates || []).some(c => c.deep_verified) ? ' · candidates re-scored on 10–19yr EDGAR data' : ''}</p></div>
        <div class="text-right"><div class="text-3xl font-bold text-${cands.length ? 'good' : 'muted'}">${cands.length}</div><div class="text-xs text-muted">candidate${cands.length === 1 ? "" : "s"}</div></div>
      </div>
      ${cands.length === 0 ? `<p class="text-sm text-muted mt-4 bg-ink/40 border border-line/60 rounded-lg p-3">Nothing clears the bar right now — that's normal for a strict margin-of-safety screen in a richly-priced market. The patient move is to wait (or loosen your assumptions in the panel above). The full ranked list is below; the highest scorers are the closest to a buy.</p>` : `<p class="text-sm text-good mt-4">These names clear your buy bar today. Click any row for the full analysis before acting.</p>`}
    </section>`);
  el.append(header);
  el.append(diffCard(d.diff));
  if (cands.length) {
    // group candidates by sector; sectors ordered by their best score
    const bySector = {};
    cands.forEach(r => { (bySector[r.sector || "Other / Unknown"] ??= []).push(r); });
    const ordered = Object.entries(bySector).sort((a, b) =>
      Math.max(...b[1].map(x => x.score || 0)) - Math.max(...a[1].map(x => x.score || 0)));
    const wrap = h(`<section class="card rounded-2xl p-6"><h3 class="font-semibold mb-1">Buy candidates — by sector</h3></section>`);
    for (const [sector, names] of ordered) {
      names.sort((a, b) => (b.score || 0) - (a.score || 0));
      wrap.insertAdjacentHTML("beforeend",
        `<div class="mt-4 mb-1 text-sm font-medium text-brand">${sector} <span class="text-muted font-normal">(${names.length})</span></div>`);
      wrap.appendChild(bareTable(names, true));
    }
    el.append(wrap);
  }
  el.append(tableCard(d.rows, "All scanned names (ranked)", true));
  if (d.errors?.length) el.append(errorsCard(d.errors));
  el.classList.remove("hidden"); el.classList.add("fade-in");
  wireRowClicks();
}

function diffCard(diff) {
  if (!diff) return h(`<div class="hidden"></div>`);
  if (!diff.prev_date) {
    return h(`<section class="card rounded-2xl p-4 text-sm text-muted">First scan of this universe recorded — next time you'll see what changed.</section>`);
  }
  const added = diff.added || [], dropped = diff.dropped || [];
  const chip = (t, cls) => `<span class="inline-block px-2 py-0.5 rounded bg-${cls}/15 text-${cls} border border-${cls}/40 text-xs font-mono mr-1 mb-1">${t}</span>`;
  if (!added.length && !dropped.length) {
    return h(`<section class="card rounded-2xl p-4 text-sm text-muted">No changes since ${diff.prev_date} — same candidates.</section>`);
  }
  return h(`<section class="card rounded-2xl p-5">
    <h3 class="font-semibold mb-3">Changes since ${diff.prev_date}</h3>
    <div class="grid sm:grid-cols-2 gap-4">
      <div><div class="text-xs text-good mb-1.5">New (${added.length})</div>
        <div>${added.length ? added.map(t => chip(t, "good")).join("") : '<span class="text-muted text-xs">none</span>'}</div></div>
      <div><div class="text-xs text-bad mb-1.5">Dropped (${dropped.length})</div>
        <div>${dropped.length ? dropped.map(t => chip(t + (diff.prev_scores?.[t] ? ` ${diff.prev_scores[t]}` : ""), "bad")).join("") : '<span class="text-muted text-xs">none</span>'}</div></div>
    </div></section>`);
}

function renderTable(d, title, subtitle) {
  const el = $("results"); el.innerHTML = "";
  el.append(h(`<section class="card rounded-2xl p-6"><h3 class="text-lg font-semibold">${title}</h3><p class="text-muted text-sm mt-1">${subtitle}</p></section>`));
  el.append(tableCard(d.rows, "", true));
  if (d.errors?.length) el.append(errorsCard(d.errors));
  el.classList.remove("hidden"); el.classList.add("fade-in");
  wireRowClicks();
}

function bareTable(rows, clickable) {
  const head = `<tr class="text-xs text-muted border-b border-line">
    <th class="text-left py-2 pr-3 font-medium">Ticker</th>
    <th class="text-right px-2 font-medium">Score</th>
    <th class="text-left px-2 font-medium">Rating</th>
    <th class="text-right px-2 font-medium">Price</th>
    <th class="text-right px-2 font-medium">Fair value</th>
    <th class="text-right px-2 font-medium">Upside</th>
    <th class="text-right px-2 font-medium">Exp. return</th>
    <th class="text-right px-2 font-medium">ROIC</th>
    <th class="text-right px-2 font-medium">Rev CAGR</th>
    <th class="text-right pl-2 font-medium">P/E</th></tr>`;
  const body = rows.map(r => {
    const rs = RATING_STYLE[r.rating] || RATING_STYLE["HOLD / WATCH"];
    const upC = r.upside == null ? "muted" : r.upside >= 0.15 ? "good" : r.upside >= 0 ? "warn" : "bad";
    const capexMark = r.heavy_capex ? ` <span title="Heavy capex cycle — earnings/FCF distorted; value shown is capex-adjusted midpoint"></span>` : "";
    const suspectMark = r.suspect ? ` <span title="Valuation flagged unreliable — excluded from buy candidates; verify the data"></span>` : "";
    return `<tr class="${clickable ? "clickable" : ""} border-b border-line/40 text-sm ${r.suspect ? "opacity-60" : ""}" data-ticker="${r.ticker}">
      <td class="py-2.5 pr-3"><span class="font-semibold">${r.ticker}</span>${capexMark}${suspectMark}<div class="text-[11px] text-muted truncate max-w-[150px]">${r.name || ""}</div></td>
      <td class="text-right px-2"><span class="inline-block w-9 text-center font-bold text-${scoreColor(r.score)}">${r.score}</span></td>
      <td class="px-2"><span class="text-xs text-${rs.c}">${r.rating}</span></td>
      <td class="text-right px-2">${price(r.price)}</td>
      <td class="text-right px-2 text-brand">${r.intrinsic_value != null ? price(r.intrinsic_value) : "—"}</td>
      <td class="text-right px-2 text-${upC}">${signPct(r.upside)}</td>
      <td class="text-right px-2 ${r.beats_inflation ? "text-good" : "text-muted"}">${fmtPct(r.expected_return, 0)}</td>
      <td class="text-right px-2">${fmtPct(r.roic, 0)}</td>
      <td class="text-right px-2">${fmtPct(r.revenue_cagr, 0)}</td>
      <td class="text-right pl-2">${fmtNum(r.trailing_pe, 1)}</td></tr>`;
  }).join("");
  return h(`<div class="overflow-x-auto"><table class="w-full min-w-[720px]"><thead>${head}</thead><tbody>${body}</tbody></table></div>`);
}

function tableCard(rows, title, clickable) {
  const card = h(`<section class="card rounded-2xl p-6">
    ${title ? `<h3 class="font-semibold mb-3">${title}</h3>` : ""}
    ${clickable ? `<p class="text-[11px] text-muted mt-3 order-last">Click any row to open the full analysis.</p>` : ""}
  </section>`);
  card.insertBefore(bareTable(rows, clickable), card.querySelector("p"));
  return card;
}

function errorsCard(errors) {
  return h(`<section class="card rounded-2xl p-4 border-bad/30"><div class="text-sm text-bad mb-1">Couldn't load ${errors.length}:</div>
    <div class="text-xs text-muted">${errors.map(e => `${e.ticker} (${e.error})`).join(" · ")}</div></section>`);
}

function wireRowClicks() {
  document.querySelectorAll("tr[data-ticker]").forEach(tr => {
    tr.addEventListener("click", () => {
      const t = tr.dataset.ticker;
      switchMode("analyze");
      $("ticker").value = t;
      analyze(t);
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  });
}

// ================= WATCHLIST + JOURNAL =================
function watchlistControl(d) {
  const t = d.ticker;
  return h(`<section class="card rounded-2xl p-4">
    <div class="flex flex-wrap items-end gap-3">
      <div class="flex-1 min-w-[220px]">
        <label class="text-xs text-muted">Your thesis / notes on ${t}</label>
        <input id="wlNotes" placeholder="why you'd own it, what to watch…" class="w-full bg-ink/60 border border-line rounded-lg px-3 py-2 text-sm mt-1">
      </div>
      <div><label class="text-xs text-muted">Buy / paid price</label>
        <input id="wlBuyPrice" type="number" step="0.01" placeholder="—" class="w-24 bg-ink/60 border border-line rounded-lg px-2 py-2 text-sm mt-1"></div>
      <div><label class="text-xs text-muted">Shares</label>
        <input id="wlShares" type="number" step="0.0001" placeholder="—" class="w-24 bg-ink/60 border border-line rounded-lg px-2 py-2 text-sm mt-1"></div>
      <label class="text-sm flex items-center gap-2 pb-2"><input id="wlOwned" type="checkbox" class="accent-brand w-4 h-4"> Owned</label>
      <button id="wlSave" class="bg-brand hover:bg-blue-500 text-white text-sm font-medium px-4 py-2 rounded-lg transition active:scale-95">Save to watchlist</button>
      <span id="wlMsg" class="text-xs text-good self-center"></span>
    </div>
    <p class="text-[11px] text-muted mt-2">The weekly job checks your watchlist and flags when a name drops into its buy-below zone. Your notes + the AI thesis are saved as a journal entry.</p>
  </section>`);
}

function wireWatchlistControl(d) {
  const save = $("wlSave");
  if (!save) return;
  save.addEventListener("click", async () => {
    const bp = $("wlBuyPrice").value, sh = $("wlShares").value;
    let q = `ticker=${encodeURIComponent(d.ticker)}&notes=${encodeURIComponent($("wlNotes").value)}` +
            `&owned=${$("wlOwned").checked}&thesis=${encodeURIComponent(d.qualitative?.investment_thesis || "")}`;
    if (bp) q += `&buy_price=${bp}`;
    if (sh) q += `&shares=${sh}`;
    try { await fetch(`/api/watchlist?${q}`, { method: "POST" }); $("wlMsg").textContent = "Saved ✓"; }
    catch { $("wlMsg").textContent = "Save failed"; }
  });
}

// ---------- Track record (weekly winners over time) ----------
function histScoreColor(s) {
  if (s == null) return "transparent";
  if (s >= 85) return "#16a34a";      // strong
  if (s >= 75) return "#22c55e";
  if (s >= 65) return "#65a30d";
  if (s >= 55) return "#ca8a04";
  return "#6b7280";
}

async function loadHistory() {
  const body = $("historyBody");
  body.innerHTML = `<div class="text-muted text-sm">Loading…</div>`;
  try {
    const d = await getJSON("/api/history?scope=large");
    renderHistory(d);
  } catch (e) { body.innerHTML = `<div class="text-bad text-sm">Couldn't load history: ${e.message}</div>`; }
}

function renderHistory(d) {
  const body = $("historyBody");
  const weeks = d.weeks || [], board = d.board || [], latest = d.latest || {};
  if (!weeks.length) {
    body.innerHTML = `<div class="card rounded-2xl p-8 text-center text-muted text-sm">
      No weekly runs recorded yet. The scheduled screen (and any full scan you run from the
      <b>Weekly buy screen</b> tab) will start filling this in — each run adds a column here.</div>`;
    return;
  }
  const dates = weeks.map(w => w.date);
  const fmtDate = s => s ? s.slice(5) : "—";              // MM-DD
  const chip = (t, cls) => `<span class="inline-block px-2 py-0.5 rounded text-xs font-mono ${cls}">${t}</span>`;

  // --- Winners-per-week trend (mini bars) ---
  const counts = weeks.map(w => w.count);
  const maxC = Math.max(1, ...counts);
  const trend = weeks.map(w => `<span class="inline-flex flex-col items-center justify-end" title="${w.date}: ${w.count} winners" style="height:34px">
      <span style="width:14px;background:#4f9dff;border-radius:2px;height:${Math.max(3, Math.round(w.count / maxC * 30))}px"></span>
      <span class="text-[9px] text-muted mt-0.5">${fmtDate(w.date)}</span></span>`).join("");

  // --- Header + latest week-over-week summary ---
  let html = `<div class="card rounded-2xl p-5 mb-5">
    <div class="flex flex-wrap items-center justify-between gap-3 mb-3">
      <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span class="text-lg font-semibold">${weeks.length} weekly run${weeks.length === 1 ? "" : "s"}</span>
        <span class="text-muted text-sm">${fmtDate(dates[0])} → ${fmtDate(dates[dates.length - 1])} · latest ${weeks[weeks.length - 1].count} winners</span>
      </div>
      <div class="flex items-end gap-1.5" title="winners per week">${trend}</div>
    </div>`;
  if (latest.prev_date) {
    const mk = arr => arr.length ? arr.map(t => chip(t, "bg-good/15 text-good")).join(" ") : `<span class="text-muted text-xs">none</span>`;
    const mkd = arr => arr.length ? arr.map(t => chip(t, "bg-bad/15 text-bad")).join(" ") : `<span class="text-muted text-xs">none</span>`;
    html += `<div class="grid md:grid-cols-3 gap-3 text-sm">
      <div><div class="text-muted text-xs mb-1">＋ New this week (${latest.added.length})</div>${mk(latest.added)}</div>
      <div><div class="text-muted text-xs mb-1">− Dropped (${latest.dropped.length})</div>${mkd(latest.dropped)}</div>
      <div><div class="text-muted text-xs mb-1">＝ Held (${latest.held.length})</div><span class="text-xs text-muted">${latest.held.length} names carried over</span></div>
    </div>`;
  } else {
    html += `<div class="text-sm text-muted">First recorded week — week-over-week comparison appears once the next run lands.</div>`;
  }
  html += `</div>`;

  // --- Caveat for weeks that aren't fully comparable (e.g. backfilled/legacy) ---
  const notes = d.notes || [];
  if (notes.length) {
    html += `<div class="mb-5 text-xs bg-warn/10 border border-warn/40 text-warn rounded-lg p-3 leading-relaxed">
      ${notes.map(n => `<div><b>${fmtDate(n.date)}:</b> ${n.note}</div>`).join("")}</div>`;
  }

  // --- Conviction board: ticker × week matrix, sorted by recurrence ---
  const colH = `<th class="px-1 text-center font-mono text-[10px] text-muted" title="week of">${dates.map(fmtDate).join('</th><th class="px-1 text-center font-mono text-[10px] text-muted">')}</th>`;
  const money = (x) => x == null ? "—" : "$" + fmtMoney(x);
  const certColor = (c) => c == null ? "#334155" : c >= 0.8 ? "#16a34a" : c >= 0.6 ? "#4f9dff" : c >= 0.45 ? "#d97706" : "#dc2626";
  const rows = board.map(b => {
    const cells = dates.map(dt => {
      const s = b.scores[dt];
      const bg = histScoreColor(s);
      // Ring the square green when the name traded at/below its certainty-scaled
      // buy-below that week — a genuine buy-zone hit, not just a high score.
      const inZone = b.buyzone && b.buyzone[dt] === true;
      // Gold ring, not green — the score squares are already green for high
      // scores, so a green ring would vanish against them.
      const ring = inZone ? ";box-shadow:0 0 0 2px #fbbf24" : "";
      const zoneTitle = s == null ? "not a winner" : `score ${s}${inZone ? " · at/below buy-below (in buy zone)" : " · above buy-below"}`;
      return `<td class="px-1 text-center"><span class="inline-flex items-center justify-center rounded" style="width:26px;height:20px;background:${bg};color:${s == null ? 'transparent' : '#fff'};font-size:10px;font-weight:600${ring}" title="${dt}: ${zoneTitle}">${s == null ? '·' : s}</span></td>`;
    }).join("");
    const streakBadge = b.streak >= 2 ? `<span class="ml-1 text-[10px] px-1 rounded bg-brand/20 text-brand" title="consecutive weeks">×${b.streak}</span>` : "";
    const certTitle = "How knowable the business is (earnings steadiness, returns on capital, balance sheet, length of record). Higher certainty → smaller required margin of safety.";
    return `<tr class="border-t border-[#1b2534]">
      <td class="px-2 py-1 font-mono font-semibold whitespace-nowrap"><button class="histTicker text-brand hover:underline" data-ticker="${b.ticker}" title="Analyze ${b.ticker}">${b.ticker}</button>${streakBadge}</td>
      <td class="px-2 py-1 text-muted text-xs max-w-[180px] truncate" title="${(b.name || '').replace(/"/g, '')}">${b.name || ""}</td>
      <td class="px-2 py-1 text-center text-xs">${b.appearances}/${b.weeks_total}</td>
      <td class="px-2 py-1 text-center text-xs" title="${certTitle}"><span style="color:${certColor(b.certainty)};font-weight:600">${b.certainty == null ? "—" : b.certainty.toFixed(2)}</span></td>
      <td class="px-2 py-1 text-center text-xs" title="Certainty-scaled required discount to fair value (thesis principle 4).">${b.mos == null ? "—" : fmtPct(b.mos, 0)}</td>
      <td class="px-2 py-1 text-center text-xs font-mono" title="Fair-value midpoint discounted by the margin of safety.">${money(b.buy_below)}</td>
      ${cells}
    </tr>`;
  }).join("");

  html += `<div class="card rounded-2xl p-4 overflow-x-auto">
    <div class="flex items-center justify-between mb-1">
      <div class="text-sm font-semibold">Conviction board</div>
      <button id="histExport" class="text-xs text-muted hover:text-brand">⭳ Export CSV</button>
    </div>
    <p class="text-xs text-muted mb-3">Ranked by how many weeks each name has cleared the buy bar. Each square is one weekly run — colored by score, blank when it wasn't a winner. A <span style="box-shadow:0 0 0 2px #fbbf24;border-radius:3px;padding:0 3px">gold ring</span> marks weeks it traded at/below its certainty-scaled buy-below (a real buy-zone hit). <b>Certainty</b> and <b>MoS</b> are the new-thesis margin-of-safety scaling — higher certainty demands a smaller discount. <b>×N</b> marks an active multi-week streak. Click a ticker to analyze it.</p>
    <table class="text-sm border-collapse">
      <thead><tr class="text-muted text-xs">
        <th class="px-2 text-left">Ticker</th><th class="px-2 text-left">Name</th>
        <th class="px-2 text-center" title="weeks as a winner / total weeks">Weeks</th>
        <th class="px-2 text-center" title="How knowable the business is (0–1). Drives the margin of safety.">Certainty</th>
        <th class="px-2 text-center" title="Certainty-scaled required margin of safety.">MoS</th>
        <th class="px-2 text-center" title="Certainty-scaled buy-below price.">Buy&lt;</th>
        ${colH}
      </tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;

  body.innerHTML = html;

  // Click a ticker -> jump to its full analysis.
  body.querySelectorAll(".histTicker").forEach(btn =>
    btn.addEventListener("click", () => { switchMode("analyze"); analyze(btn.dataset.ticker); }));

  // Export the board as CSV (dates as columns).
  const exp = body.querySelector("#histExport");
  if (exp) exp.addEventListener("click", () => {
    const head = ["Ticker", "Name", "Sector", "Weeks", "Streak", "Certainty", "MoS", "BuyBelow", ...dates];
    const lines = [head.join(",")];
    for (const b of board) {
      const cells = [b.ticker, `"${(b.name || "").replace(/"/g, "'")}"`, `"${b.sector || ""}"`,
        `${b.appearances}/${b.weeks_total}`, b.streak,
        b.certainty ?? "", b.mos == null ? "" : (b.mos * 100).toFixed(1), b.buy_below == null ? "" : b.buy_below.toFixed(2),
        ...dates.map(dt => b.scores[dt] ?? "")];
      lines.push(cells.join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `track-record-${dates[dates.length - 1]}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
}

async function loadWatchlist() {
  showLoading("Checking your watchlist against buy-below prices…");
  try {
    const d = await getJSON("/api/watchlist");
    $("loading").classList.add("hidden");
    renderWatchlist(d);
  } catch (e) { showError(e.message); }
}

function renderWatchlist(d) {
  const el = $("results"); el.innerHTML = "";
  if (!d.rows.length) {
    el.append(h(`<section class="card rounded-2xl p-8 text-center text-muted">
      <p class="text-lg mb-1">Your watchlist is empty.</p>
      <p class="text-sm">Analyze a stock, then hit <span class="text-brand">Save to watchlist</span> to track it here.</p></section>`));
    el.classList.remove("hidden"); return;
  }
  if (d.portfolio) el.append(portfolioCard(d.portfolio));
  const inZone = d.rows.filter(r => r.in_buy_zone).length;
  el.append(h(`<section class="card rounded-2xl p-5">
    <div class="flex items-center justify-between"><h3 class="font-semibold">Watchlist (${d.count})</h3>
      <div class="text-right"><div class="text-2xl font-bold text-${inZone ? 'good' : 'muted'}">${inZone}</div><div class="text-xs text-muted">in buy zone</div></div></div>
    ${inZone ? `<p class="text-sm text-good mt-2">${inZone} name${inZone === 1 ? "" : "s"} trading at or below your buy-below price today.</p>` : `<p class="text-sm text-muted mt-2">Nothing in the buy zone yet — the weekly job will flag names as they drop in.</p>`}
  </section>`));
  d.rows.forEach(r => el.append(watchlistRow(r)));
  el.classList.remove("hidden"); el.classList.add("fade-in");
  document.querySelectorAll("[data-wl-remove]").forEach(b =>
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch(`/api/watchlist?ticker=${b.dataset.wlRemove}`, { method: "DELETE" });
      loadWatchlist();
    }));
  document.querySelectorAll("[data-wl-open]").forEach(row =>
    row.addEventListener("click", () => { switchMode("analyze"); $("ticker").value = row.dataset.wlOpen; analyze(row.dataset.wlOpen); window.scrollTo({ top: 0, behavior: "smooth" }); }));
}

function portfolioCard(p) {
  const cell = (label, val, sub = "", color = "") => `
    <div class="bg-ink/40 rounded-xl p-3 border border-line/60"><div class="text-xs text-muted">${label}</div>
      <div class="text-lg font-semibold mt-0.5 ${color}">${val}</div>${sub ? `<div class="text-[11px] text-muted mt-0.5">${sub}</div>` : ""}</div>`;
  const gainC = p.gain_pct == null ? "" : p.gain_pct >= 0 ? "text-good" : "text-bad";
  const conc = p.largest_position_pct;
  const alloc = p.sector_allocation.map(([sec, w]) =>
    `<div class="flex items-center gap-2 text-xs"><div class="w-28 truncate text-muted">${sec}</div>
      <div class="flex-1 h-2 bg-ink/60 rounded-full overflow-hidden"><div class="h-full bg-brand rounded-full" style="width:${(w * 100).toFixed(0)}%"></div></div>
      <div class="w-10 text-right">${fmtPct(w, 0)}</div></div>`).join("");
  return h(`<section class="card rounded-2xl p-6">
    <h3 class="font-semibold mb-4">Portfolio ${p.value_weighted ? "" : `<span class="text-xs text-muted font-normal">· equal-weighted (add shares for $ values)</span>`}</h3>
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
      ${p.value_weighted ? cell("Market value", "$" + fmtMoney(p.total_value)) : cell("Positions", p.n_positions)}
      ${p.value_weighted ? cell("Cost basis", "$" + fmtMoney(p.total_cost)) : cell("Sectors", p.n_sectors)}
      ${p.gain_pct != null ? cell("Unrealized gain", (p.gain >= 0 ? "+$" : "-$") + fmtMoney(Math.abs(p.gain)), signPct(p.gain_pct), gainC) : cell("Sectors", p.n_sectors)}
      ${cell("Weighted score", p.weighted_score, "portfolio quality")}
    </div>
    <div class="grid md:grid-cols-2 gap-6">
      <div><div class="text-sm text-muted mb-2">Sector allocation</div><div class="space-y-1.5">${alloc}</div></div>
      <div><div class="text-sm text-muted mb-2">Diversification</div>
        <div class="text-sm space-y-1">
          <div>${p.n_positions} positions across ${p.n_sectors} sector${p.n_sectors === 1 ? "" : "s"}</div>
          <div class="${conc > 0.4 ? "text-warn" : "text-muted"}">Largest position: ${fmtPct(conc, 0)}${conc > 0.4 ? " — concentrated" : ""}</div>
        </div></div>
    </div>
  </section>`);
}

function watchlistRow(r) {
  if (r.error) {
    return h(`<section class="card rounded-2xl p-4 flex items-center justify-between">
      <div><span class="font-semibold">${r.ticker}</span> <span class="text-xs text-bad ml-2">${r.error}</span></div>
      <button data-wl-remove="${r.ticker}" class="text-xs text-muted hover:text-bad">Remove</button></section>`);
  }
  const rs = RATING_STYLE[r.rating] || RATING_STYLE["HOLD / WATCH"];
  const zone = r.in_buy_zone;
  return h(`<section class="card rounded-2xl p-4 ${zone ? "border-good/50" : ""}" data-wl-open="${r.ticker}" style="cursor:pointer">
    <div class="flex flex-wrap items-center gap-x-6 gap-y-2">
      <div class="min-w-[160px]">
        <div class="flex items-center gap-2"><span class="font-semibold">${r.ticker}</span>
          ${zone ? `<span class="text-[10px] px-2 py-0.5 rounded bg-good/15 text-good border border-good/40">IN BUY ZONE</span>` : ""}
          ${r.owned ? `<span class="text-[10px] px-2 py-0.5 rounded bg-ink/60 text-muted border border-line">owned</span>` : ""}</div>
        <div class="text-[11px] text-muted truncate max-w-[160px]">${r.name || ""}</div>
      </div>
      <div class="text-sm"><span class="text-muted text-xs">Price </span>${price(r.price)}</div>
      <div class="text-sm"><span class="text-muted text-xs">Buy-below </span><span class="text-good">${price(r.buy_below)}</span></div>
      <div class="text-sm"><span class="text-muted text-xs">Fair value </span><span class="text-brand">${price(r.intrinsic_value)}</span></div>
      <div class="text-sm"><span class="text-muted text-xs">Score </span><span class="font-bold text-${scoreColor(r.score)}">${r.score}</span> <span class="text-xs text-${rs.c}">${r.rating}</span></div>
      ${r.buy_price ? `<div class="text-sm"><span class="text-muted text-xs">Your buy </span>${price(r.buy_price)}</div>` : ""}
      <button data-wl-remove="${r.ticker}" class="text-xs text-muted hover:text-bad ml-auto">Remove</button>
    </div>
    ${r.notes ? `<p class="text-xs text-muted mt-2 border-t border-line/40 pt-2">${r.notes}</p>` : ""}
  </section>`);
}

// ---------- wire up ----------
document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => switchMode(t.dataset.mode)));
$("refreshWatchlist")?.addEventListener("click", loadWatchlist);
$("refreshHistory")?.addEventListener("click", loadHistory);
$("go").addEventListener("click", () => analyze());
$("ticker").addEventListener("keydown", (e) => { if (e.key === "Enter") analyze(); });
$("goCompare").addEventListener("click", runCompare);
$("compareInput").addEventListener("keydown", (e) => { if (e.key === "Enter") runCompare(); });
$("goScreen").addEventListener("click", runScreen);
$("resetAssume").addEventListener("click", (e) => { e.preventDefault(); resetAssumptions(); });
document.querySelectorAll(".ex").forEach(b => b.addEventListener("click", () => analyze(b.textContent)));
initAssumptions();
