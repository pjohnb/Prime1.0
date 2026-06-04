// PRIME v1.0 In-App Help System (Sprint 21 Item 1)
// ? modal, strategy popovers, Start of Day checklist.

// ---------------------------------------------------------------------------
// Content data
// ---------------------------------------------------------------------------

const HELP_GLOSSARY = [
  { term: "beaten path",     def: "Repeated CONFIRMING DK signal on the same symbol across multiple scan windows; high-conviction institutional accumulation pattern." },
  { term: "CONFIRMING",      def: "DK three-state: institutional dark-pool buying aligns with signal direction. Auto-upgrades WATCH tier to STRONG; lean HOLD on open positions." },
  { term: "DK",              def: "Dark pool / dark-pool signal layer. Off-exchange block trades that reveal institutional intent before it appears in public order flow." },
  { term: "dk_conviction",   def: "Confidence score (0.0–1.0) for the DK classification. Higher = more decisive institutional data behind the CONFIRMING or NULLIFYING verdict." },
  { term: "DTE",             def: "Days To Expiration. UOA trigger requires DTE ≤ 30 — near-dated puts/calls carry the strongest directional intent." },
  { term: "IDX",             def: "Index Trader strategy. Trades index ETFs (SPY, QQQ, IWM) on sector-rotation momentum signals." },
  { term: "IRA",             def: "IRA-type account in Schwab. MATA routing excludes IRA accounts from short selling and options strategies." },
  { term: "MATA",            def: "Multi-Account Trade Allocator. Routes orders to the correct Schwab account based on strategy type, direction, and instrument." },
  { term: "MTS",             def: "Multi-Timeframe Scanner. Momentum signals confirmed across 5-min, 15-min, and 60-min bars." },
  { term: "NEUTRAL",         def: "DK three-state: no significant dark-pool activity detected. Signal passes through unchanged — no upgrade or suppression." },
  { term: "NULLIFYING",      def: "DK three-state: institutional dark-pool selling opposes signal direction. Suppresses long signals; upgrades short signals WATCH→STRONG." },
  { term: "PAPER",           def: "Paper trading mode. All orders simulated — no real executions. PRIME v1.0 runs PAPER only." },
  { term: "PEAD",            def: "Post-Earnings Announcement Drift. Earnings-beat (long) or earnings-miss + guidance cut (short) signals with predictable multi-day drift." },
  { term: "PEAD_BEAT",       def: "PEAD trigger: EPS surprise > 0 within the last 5 trading sessions. Initiates a PSA long candidate." },
  { term: "PEAD_MISS",       def: "PEAD trigger: earnings miss + guidance cut within the last 5 sessions, stock still elevated vs pre-earnings. Initiates a short candidate." },
  { term: "PSA",             def: "Prime Segment Analysis. A-B-C-D momentum scanner. Requires a predictive trigger (UOA_CALL or PEAD_BEAT) before any candidate reaches APPROVED." },
  { term: "RTH",             def: "Regular Trading Hours. 09:30–16:00 ET Mon–Fri. Short entries are restricted to RTH only." },
  { term: "SHORT",           def: "Short-selling strategy. Requires a primary trigger (UOA_PUT or PEAD_MISS), borrow confirmation, 50% of normal size, RTH only, no IRA." },
  { term: "SRS",             def: "Short-term Reversal Signal. Mean-reversion scanner for overbought/oversold conditions with reversal confirmation." },
  { term: "STRONG",          def: "Signal tier: both primary triggers fired AND technical confirmation passes AND DK CONFIRMING (or NEUTRAL with high conviction)." },
  { term: "SUPPRESSED",      def: "Signal status set by DK NULLIFYING on a PSA candidate. The technical setup is valid but institutional flow opposes the trade." },
  { term: "trigger_source",  def: "The predictive event that initiated the signal. Values: UOA_CALL (call surge), UOA_PUT (put surge), PEAD_BEAT (earnings beat), PEAD_MISS (earnings miss)." },
  { term: "UOA",             def: "Unusual Options Activity. Unusual call or put volume that predicts a directional move before the price move occurs." },
  { term: "UOA_CALL",        def: "UOA trigger: call-dominant unusual activity (call/put ratio ≥ 2.0). Initiates a PSA long candidate." },
  { term: "UOA_PUT",         def: "UOA trigger: put-dominant unusual activity (put/call ratio ≥ 2.0 + premium > $250k + DTE ≤ 30 + volume surge). Initiates a short candidate." },
  { term: "WATCH",           def: "Signal tier: one primary trigger fired with technical confirmation. Actionable but lower conviction than STRONG." },
].sort((a, b) => a.term.localeCompare(b.term));

const STRATEGY_INFO = {
  PSA: {
    trigger: "UOA_CALL (call surge in last 2 sessions) or PEAD_BEAT (earnings beat in last 5 sessions). No trigger = technical-only candidate stays WATCH.",
    confirmation: "A-B-C-D momentum ratio above threshold; B-D direction positive; consecutive positive bars; max drawdown within limits.",
    dk: "CONFIRMING → WATCH upgraded to STRONG. NULLIFYING → SUPPRESSED (institutional selling opposes the setup).",
    hold: "Intraday to 1 session (5-min bars).",
    stop: "–5% from entry price.",
    good: "A-B-C-D momentum ≥ 70%, UOA_CALL trigger, DK CONFIRMING → tier STRONG.",
    bad: "Momentum barely above threshold, no trigger (WATCH), DK NEUTRAL at best. Reduce size or skip.",
  },
  PEAD: {
    trigger: "Earnings beat (EPS surprise > 0, long) or earnings miss + guidance cut (short) within the last 5 trading sessions.",
    confirmation: "Stock still within post-earnings drift window; price not fully gapped out; daily volume elevated.",
    dk: "CONFIRMING on the earnings drift confirms institutional positioning in the expected direction. NULLIFYING suggests smart money is fading the earnings reaction.",
    hold: "2–5 sessions (drift window closes day 5).",
    stop: "–5% long / +5% short from entry.",
    good: "Large EPS surprise (≥ 5%), price not exhausted, DK CONFIRMING, within first 2 sessions.",
    bad: "Day 4–5 of drift window, stock already moved 80% of typical PEAD range. Risk/reward is poor.",
  },
  UOA: {
    trigger: "Self-triggering: unusual call volume IS the trigger. Call/put ratio ≥ 2.0 for longs. Put/call ratio ≥ 2.0 + premium > $250k + DTE ≤ 30 for shorts.",
    confirmation: "Institutional-sized premium; near-term expiry (DTE ≤ 30); volume surge > 3× 20-day average.",
    dk: "CONFIRMING aligns the off-exchange equity flow with the options signal — very high conviction. NULLIFYING means the equity dark pool is fading the options move.",
    hold: "1–3 sessions.",
    stop: "–5% equity position; options position is risk-defined by premium paid.",
    good: "10× average volume, DTE < 14, DK CONFIRMING on the underlying.",
    bad: "Moderate volume (1.5× average), long-dated options (DTE > 30), DK NEUTRAL or NULLIFYING.",
  },
  SRS: {
    trigger: "Overbought/oversold extreme on the short-term momentum indicator with early reversal confirmation (volume or price action).",
    confirmation: "RSI or momentum extreme; volume spike at the reversal point.",
    dk: "CONFIRMING supports the reversal thesis (institutional accumulation at the low / distribution at the high). NULLIFYING means the institutional flow continues the trend, not a reversal.",
    hold: "1–2 sessions.",
    stop: "–5% from entry.",
    good: "RSI extreme + volume surge at the reversal candle, DK CONFIRMING.",
    bad: "Moderate momentum reading, no volume confirmation, DK NEUTRAL.",
  },
  MTS: {
    trigger: "Momentum alignment across all three timeframes simultaneously (5-min, 15-min, 60-min).",
    confirmation: "Trend consistency; all timeframes pointing in the same direction; volume confirming.",
    dk: "CONFIRMING elevates a multi-timeframe alignment from WATCH to STRONG. NULLIFYING signals that institutional flow is not participating in the visible trend.",
    hold: "1–2 sessions.",
    stop: "–5% from entry.",
    good: "Strong trend on all three timeframes, DK CONFIRMING, clean breakout structure.",
    bad: "Two timeframes aligned, one lagging. Lower conviction — stay WATCH.",
  },
  IDX: {
    trigger: "Sector-rotation signal on an index ETF (SPY, QQQ, IWM). Relative sector strength divergence above threshold.",
    confirmation: "Index volume expansion; price above 50-SMA; sector rotation confirmed in breadth data.",
    dk: "DK activity on an index ETF represents very large institutional positioning — CONFIRMING is a very high conviction signal.",
    hold: "1–5 sessions.",
    stop: "–3% (index ETFs have lower realized volatility than equities).",
    good: "Clear sector-rotation signal, index volume 2× average, DK CONFIRMING.",
    bad: "Weak rotation signal, low volume, DK NEUTRAL.",
  },
  DK: {
    trigger: "Self-triggering: a qualifying dark-pool print IS the signal. Volume ratio > threshold, price proximity < 0.5%, and/or repeat activity detected.",
    confirmation: "Print size (volume ratio vs ADV), price proximity to last public trade, repeat activity across multiple print windows.",
    dk: "DK IS the signal — no external DK modifier applies. SIGNAL tier = CONFIRMING direction; NULLIFIER tier = NULLIFYING direction.",
    hold: "1–3 sessions (institutional accumulation/distribution typically plays out over 1–3 days).",
    stop: "–5% from entry for SIGNAL (long) DK rows.",
    good: "Volume ratio > 5×, price proximity < 0.2%, repeat prints on multiple days, SIGNAL tier.",
    bad: "Single print, low volume ratio, no repeat activity.",
  },
  SHORT: {
    trigger: "UOA_PUT (put/call ratio ≥ 2.0 + premium > $250k + DTE ≤ 30 + volume surge > 3×) or PEAD_MISS (earnings miss + guidance cut + still elevated + within 5 sessions). At least one required.",
    confirmation: "Price below 50-SMA; relative strength vs SPY < 0.95 (underperforming by > 5%); borrow available via Schwab locate.",
    dk: "CONFIRMING = danger — institutional buying actively opposes the short thesis. Signal blocked. NULLIFYING = institutional selling confirms short → WATCH upgraded to STRONG.",
    hold: "1–5 sessions.",
    stop: "+5% from entry (short-side stop).",
    good: "Both UOA_PUT + PEAD_MISS triggers, price well below 50-SMA, RS ratio < 0.90, DK NULLIFYING → STRONG.",
    bad: "Single trigger, RS ratio borderline (0.93–0.95), DK CONFIRMING (block it — do not short into institutional buying).",
  },
};

// ---------------------------------------------------------------------------
// ? Help modal
// ---------------------------------------------------------------------------

const HELP_TABS = ["daily-routine", "signal-tiers", "dk-states", "risk-rules", "glossary"];

function openHelp() {
  document.getElementById("help-overlay").classList.add("open");
  showHelpTab("daily-routine");
  document.addEventListener("keydown", _helpEscHandler);
}

function closeHelp() {
  document.getElementById("help-overlay").classList.remove("open");
  document.removeEventListener("keydown", _helpEscHandler);
}

function _helpEscHandler(e) {
  if (e.key === "Escape") closeHelp();
}

function showHelpTab(id) {
  HELP_TABS.forEach(t => {
    document.getElementById("htab-" + t).classList.toggle("active", t === id);
    document.getElementById("hpane-" + t).style.display = t === id ? "block" : "none";
  });
  if (id === "glossary") renderGlossary("");
}

// ---------------------------------------------------------------------------
// Glossary
// ---------------------------------------------------------------------------

function renderGlossary(filter) {
  const q = filter.toLowerCase().trim();
  const container = document.getElementById("glossary-list");
  if (!container) return;
  const matches = q ? HELP_GLOSSARY.filter(g =>
    g.term.toLowerCase().includes(q) || g.def.toLowerCase().includes(q)
  ) : HELP_GLOSSARY;
  if (!matches.length) {
    container.innerHTML = '<p style="color:var(--text3);padding:10px 0">No matches.</p>';
    return;
  }
  container.innerHTML = matches.map(g => `
    <div style="padding:8px 0;border-bottom:1px solid var(--border)">
      <span style="font-family:var(--mono);font-weight:700;color:var(--amber)">${g.term}</span>
      <span style="color:var(--text2);font-size:14px;margin-left:8px">${g.def}</span>
    </div>`).join("");
}

// ---------------------------------------------------------------------------
// Strategy ⓘ popovers
// ---------------------------------------------------------------------------

let _activePopover = null;

function toggleStrategyInfo(stratKey, btnEl) {
  const existing = document.getElementById("strat-popover");
  if (_activePopover === stratKey && existing) {
    existing.remove();
    _activePopover = null;
    return;
  }
  if (existing) existing.remove();
  _activePopover = stratKey;

  const info = STRATEGY_INFO[stratKey];
  if (!info) return;

  const pop = document.createElement("div");
  pop.id = "strat-popover";
  pop.className = "strat-popover";
  pop.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <span style="font-weight:700;font-size:14px;color:var(--amber)">${stratKey} Strategy</span>
      <button onclick="closeStratPopover()" style="background:none;border:none;color:var(--text3);cursor:pointer;font-size:16px;line-height:1">×</button>
    </div>
    ${_popRow("Trigger", info.trigger)}
    ${_popRow("Confirmation", info.confirmation)}
    ${_popRow("DK Effect", info.dk)}
    ${_popRow("Typical Hold", info.hold)}
    ${_popRow("Stop Rule", info.stop)}
    ${_popRow("Good signal", info.good)}
    ${_popRow("Bad signal", info.bad)}`;

  // Position below the button.
  document.body.appendChild(pop);
  const rect = btnEl.getBoundingClientRect();
  pop.style.top = (rect.bottom + window.scrollY + 6) + "px";
  pop.style.left = Math.max(10, Math.min(rect.left + window.scrollX, window.innerWidth - 380)) + "px";

  // Close on outside click.
  setTimeout(() => document.addEventListener("click", _popOutsideHandler), 0);
}

function _popRow(label, value) {
  return `<div style="margin-bottom:6px"><span style="font-family:var(--mono);font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em">${label}:</span><br><span style="font-size:13px;color:var(--text2)">${value}</span></div>`;
}

function closeStratPopover() {
  const pop = document.getElementById("strat-popover");
  if (pop) pop.remove();
  _activePopover = null;
  document.removeEventListener("click", _popOutsideHandler);
}

function _popOutsideHandler(e) {
  const pop = document.getElementById("strat-popover");
  if (pop && !pop.contains(e.target) && !e.target.classList.contains("strat-info-btn")) {
    closeStratPopover();
  }
}

// ---------------------------------------------------------------------------
// Start of Day checklist
// ---------------------------------------------------------------------------

function toggleSodPanel() {
  const body = document.getElementById("sod-body");
  const arrow = document.getElementById("sod-arrow");
  const collapsed = body.style.display === "none" || body.style.display === "";
  body.style.display = collapsed ? "block" : "none";
  arrow.textContent = collapsed ? "▲" : "▼";
}
