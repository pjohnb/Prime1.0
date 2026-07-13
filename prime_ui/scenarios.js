// WO-PRIME-SCENARIOS-01 Phase 2 — Scenarios Tab UI

const _SCENARIO_MODAL_COPY = {
  "1": {
    title: "Type 1 — Sniper: Pure",
    body: "The sector this symbol belongs to is trending strongly in one direction AND trading volume confirms the move — institutional and retail participation both present. This is the cleanest single-signal setup PRIME produces. No individual stock confirmation required — the sector itself is the thesis. Enter in the direction of the IDX signal. Stop placement: 3% default trailing."
  },
  "2": {
    title: "Type 2 — Sniper: Confirmed",
    body: "The sector is trending in the right direction but hasn’t yet attracted confirming volume — it’s early. However, this specific stock’s price and momentum are already moving in the same direction as the sector signal. PSA confirmation means the thesis isn’t just sector-level theory — it’s showing up in actual price action on this stock. Two independent lenses agreeing without volume crowd confirmation yet. Early entry, higher reward potential, slightly higher risk than a volume-confirmed setup."
  },
  "3": {
    title: "Type 3 — Sniper: Institutional",
    body: "Three independent signals agree on this stock — but none require the broad market crowd to have shown up yet. The sector is trending in the right direction (without volume confirmation), institutional money is positioned via either unusual options activity or post-earnings announcement drift, and price momentum is already responding. This is a movement play, not a volume play — you are entering before the crowd arrives. Highest risk/reward ratio of the IDX-backed scenarios. If the crowd does arrive and volume confirms, this setup upgrades toward Trifecta conviction."
  },
  "4": {
    title: "Type 4 — Sniper: Trifecta",
    body: "The full confirmation stack — sector volume confirmed, institutional money positioned via unusual options activity or post-earnings announcement drift, and individual stock price momentum responding. All three independent lenses agree and the crowd has shown up. This is the highest conviction IDX-backed scenario PRIME produces. Enter with confidence, size at the upper end of your per-trade budget, stop placement standard."
  },
  "4+": {
    title: "Type 4+ — Sniper: Ultimate",
    body: "Every confirmation layer PRIME has is aligned on this stock simultaneously — sector volume confirmed, institutional money positioned, price momentum responding, and all timeframes (intraday, weekly, annual) pointing in the same direction. This is an extraordinary convergence event. Four independent lenses, zero contradiction. Size at maximum Sniper budget."
  },
  "5": {
    title: "Type 5 — Watch",
    body: "A single scanner has flagged this symbol but no confirming signal has arrived yet from another source. The thesis is unconfirmed — one lens is pointing at something interesting but the other lenses haven’t agreed. Monitor this symbol. If a second signal converges within the session, this Watch may upgrade to an actionable Sniper scenario. No action recommended on a Watch alone."
  },
  "6": {
    title: "Type 6 — Sniper: Anomalous",
    body: "This stock is moving on its own logic — the broader sector is not participating. Institutional money is positioned via unusual options activity or post-earnings announcement drift, and price momentum confirms the move is already underway. PSA stands in for the missing sector confirmation here: if the stock is already moving in the same direction as the institutional signal, that is sufficient conviction without sector backing. Classic territory for stocks like TSLA that routinely decouple from their sector. Applies equally to SHORT setups where institutional put activity and downward momentum converge without sector participation. Treat stop placement tightly — without sector confirmation, reversals can be sharp."
  },
  "7": {
    title: "Type 7 — Sniper: Sector Phase",
    body: "The Sector Recovery Scanner has detected a phase change in this stock’s sector — either a sector in drawdown beginning to stabilize and recover (LONG) or a sector in deterioration continuing to decline (SHORT). IDX is not required here because SRS is the sector signal — it fires precisely in the window before IDX catches up, making this an early-phase entry. PSA confirms the individual stock is already moving in the direction of the phase change. You are positioned ahead of the broader sector trend confirmation. Allow more time for this thesis to develop than a standard IDX-backed scenario."
  },
  "8": {
    title: "Type 8 — Sniper: Metals Mean-Reversion",
    body: "The Metals Mean-Reversion scanner has detected a two-phase setup in a precious metals instrument. TRANCHE_1 identified the initial condition — either significantly oversold (LONG: price ≤ -5% from SMA20, RSI ≤ 35) or significantly overbought (SHORT: price ≥ +5% from SMA20, RSI ≥ 65) — with volume surging in both cases. TRANCHE_2 confirms the directional turn is underway — RSI rising (LONG) or RSI falling (SHORT). PSA confirms price momentum is responding. This is a mean-reversion play, not a trend-following play — the thesis is that an extended move is exhausting and price is returning toward equilibrium. Universe: full metals universe eligible for LONG; SHORT restricted to liquid ETFs only (SLV, GLD, GDX, GDXJ)."
  },
  "9": {
    title: "Type 9 — Sniper: Timeframe Confluence",
    body: "All three timeframes — intraday, weekly, and annual — are aligned in the same direction on this stock, and at least one additional scanner confirms the move. Timeframe confluence means the trade is not fighting any higher timeframe trend: the short-term move is consistent with the weekly direction, which is consistent with the annual trend. When all timeframes agree, the probability of a sustained move increases significantly. The confirming signal (IDX, UOA, PSA, or PEAD) adds an independent lens to the timeframe alignment thesis."
  }
};

function getScenarioMaxRows() {
  return parseInt(localStorage.getItem('prime_scenario_max_rows') || '20', 10);
}

function _scFormatTS(ts) {
  // scan_ts is stored in ET — parse as local time (assumes browser is in ET)
  if (!ts) return '--';
  try {
    const s = ts.replace(' ', 'T');  // no Z — local parse preserves ET value
    const dt = new Date(s);
    if (isNaN(dt)) return ts.substring(0, 5);
    return dt.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false }) + ' ET';
  } catch(e) { return ts.substring(0, 5); }
}

function _scFormatDetected(ts) {
  // detected_at is UTC from Python datetime.utcnow() — add 'Z' so JS parses as UTC, display in ET
  if (!ts) return '--';
  try {
    let s = ts.replace(' ', 'T');
    if (!s.endsWith('Z') && !s.includes('+')) s += 'Z';
    const dt = new Date(s);
    if (isNaN(dt)) return ts.substring(0, 16);
    return dt.toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', timeZone: 'America/New_York'
    }) + ' ' + dt.toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York', hour12: false
    }) + ' ET';
  } catch(e) { return ts.substring(0, 16); }
}

function _scStaleDot(status) {
  if (status === 'VETOED')
    return '<span style="color:#ef4444;font-size:10px" title="VETOED — stale">&#9679;</span>';
  if (status === 'SOFT_STALE')
    return '<span style="color:#f59e0b;font-size:10px" title="SOFT_STALE — aging">&#9679;</span>';
  return '<span style="color:#22c55e;font-size:10px" title="FRESH">&#9679;</span>';
}

function _scTypeBadge(typeNum) {
  const isHighest = typeNum === '4' || typeNum === '4+';
  const bg = isHighest ? '#14532d' : '#78350f';
  const fg = isHighest ? '#86efac' : '#fcd34d';
  const br = isHighest ? '#16a34a' : '#d97706';
  return `<span style="background:${bg};color:${fg};border:1px solid ${br};padding:3px 10px;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:.05em;font-family:var(--mono)">TYPE ${typeNum}</span>`;
}

function _scConvictionBadge(conviction) {
  if (conviction === 'HIGHEST')
    return `<span style="background:#14532d;color:#86efac;padding:2px 8px;border-radius:3px;font-size:10px;font-weight:700;letter-spacing:.05em">HIGHEST</span>`;
  if (conviction === 'LOW')
    return `<span style="background:#1e2128;color:#9ba8c4;padding:2px 8px;border-radius:3px;font-size:10px;font-weight:700;letter-spacing:.05em">LOW</span>`;
  return `<span style="background:#78350f;color:#fcd34d;padding:2px 8px;border-radius:3px;font-size:10px;font-weight:700;letter-spacing:.05em">HIGH</span>`;
}

function _scDirTag(direction) {
  if (direction === 'SHORT')
    return `<span style="color:#ef4444;font-weight:700;font-size:12px;font-family:var(--mono)">SHORT &#8595;</span>`;
  return `<span style="color:#22c55e;font-weight:700;font-size:12px;font-family:var(--mono)">LONG &#8593;</span>`;
}

function _scConstituentRows(constituents) {
  if (!constituents || !constituents.length)
    return '<span style="color:var(--text3);font-size:11px">—</span>';
  return constituents.map(c => {
    const vetoed = c.staleness === 'VETOED';
    const lineStyle = vetoed ? 'text-decoration:line-through;opacity:.5;' : '';
    const dot = _scStaleDot(c.staleness || 'FRESH');
    const ts  = _scFormatTS(c.scan_ts);
    const scoreStr = c.score != null
      ? `<span style="color:var(--text3);font-size:10px;min-width:36px;text-align:right">${Number(c.score).toFixed(1)}</span>`
      : '<span style="min-width:36px"></span>';
    return `<div style="${lineStyle}display:flex;align-items:center;gap:8px;padding:3px 0;font-size:11px;font-family:var(--mono)">
      ${dot}
      <span style="color:#4a9eff;min-width:38px">${c.strategy || '--'}</span>
      <span style="font-weight:600;min-width:52px">${c.symbol || '--'}</span>
      <span style="color:var(--text2);min-width:90px">${c.tier || '--'}</span>
      ${scoreStr}
      <span style="color:var(--text3)">${ts}</span>
    </div>`;
  }).join('');
}

function _scBorderColor(conviction) {
  if (conviction === 'HIGHEST') return '#22c55e';
  if (conviction === 'LOW')     return '#4a9eff';
  return '#f59e0b';
}

function _scEntryPrice(constituents) {
  if (!constituents) return null;
  for (const c of constituents) {
    if (c.entry_price != null) return c.entry_price;
  }
  return null;
}

function _scExecuteArgs(sc) {
  const constituents = sc.constituent_signals || [];
  // Prefer PSA signal for execution; fall back to first constituent
  const psa = constituents.find(c => c.strategy === 'PSA');
  const anchor = psa || constituents[0] || {};
  return {
    signal_id:   anchor.signal_id || sc.scenario_id || '',
    symbol:      sc.primary_symbol || '',
    tier:        anchor.tier || sc.conviction || 'HIGH',
    entry_price: _scEntryPrice(constituents) || 0
  };
}

function _renderScenarioCard(sc) {
  const constituents = sc.constituent_signals || [];
  const ep = _scEntryPrice(constituents);
  const entryStr = ep != null ? '$' + Number(ep).toFixed(2) : '—';
  const exec = _scExecuteArgs(sc);
  const borderColor = _scBorderColor(sc.conviction);

  // Escape strings used in onclick attributes
  const safeId  = String(exec.signal_id).replace(/'/g, "\\'");
  const safeSym = String(exec.symbol).replace(/'/g, "\\'");
  const safeTier = String(exec.tier).replace(/'/g, "\\'");

  return `<div style="background:var(--bg3);border:1px solid var(--border);border-left:3px solid ${borderColor};border-radius:6px;padding:14px 16px;margin-bottom:12px">
  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px">
    ${_scTypeBadge(sc.type_num)}
    <span style="font-weight:700;font-size:13px;color:var(--text)">${sc.type_name || '--'}</span>
    ${_scDirTag(sc.direction)}
    ${_scConvictionBadge(sc.conviction)}
    <span style="display:flex;align-items:center;gap:4px;font-size:10px;color:var(--text3)">${_scStaleDot(sc.staleness_status)} ${sc.staleness_status || ''}</span>
  </div>
  <div style="display:flex;align-items:baseline;gap:24px;margin-bottom:10px;flex-wrap:wrap">
    <span style="font-size:18px;font-weight:700;color:var(--text);font-family:var(--mono)">${sc.primary_symbol || '--'}</span>
    <span style="font-size:12px;color:var(--text3)">Entry <span style="color:var(--text2);font-weight:600">${entryStr}</span></span>
    <span style="font-size:12px;color:var(--text3)">Detected <span style="color:var(--text2)">${_scFormatDetected(sc.detected_at)}</span></span>
  </div>
  <div style="border-top:1px solid var(--border);padding-top:8px;margin-bottom:12px">
    ${_scConstituentRows(constituents)}
  </div>
  <div style="display:flex;gap:8px;align-items:center">
    <button onclick="openScenarioInfo('${sc.type_num}')"
      style="background:transparent;border:1px solid var(--border);color:var(--text3);padding:4px 10px;border-radius:4px;font-size:12px;cursor:pointer;min-width:32px"
      title="Learn about this scenario type">ⓘ</button>
    <button onclick="openBuySignalConfirm('${safeId}','${safeSym}','${safeTier}',${exec.entry_price})"
      style="background:#14532d;border:1px solid #16a34a;color:#86efac;padding:4px 14px;border-radius:4px;font-size:12px;font-weight:700;cursor:pointer">Execute &#9654;</button>
  </div>
</div>`;
}

async function loadScenarios() {
  const container = document.getElementById('scen-cards');
  if (!container) return;
  container.innerHTML = '<div class="empty-state" style="padding:24px 0;color:var(--text3)">Loading scenarios…</div>';
  try {
    const limit = getScenarioMaxRows();
    const resp = await fetch(API + '/scenarios?limit=' + limit);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    const scenarios = (data.scenarios || []).slice(0, limit);
    if (!scenarios.length) {
      container.innerHTML = `<div style="padding:48px 0;text-align:center;color:var(--text3)">
        <div style="font-size:15px;margin-bottom:6px">No active scenarios detected.</div>
        <div style="font-size:12px">Run a scan to detect signal convergence, then POST to /api/v1/scenarios/detect.</div>
      </div>`;
      return;
    }
    container.innerHTML = scenarios.map(_renderScenarioCard).join('');
  } catch(e) {
    container.innerHTML = `<div style="color:var(--red);padding:16px 0">Error loading scenarios: ${e.message}</div>`;
  }
}

function openScenarioInfo(typeNum) {
  const info = _SCENARIO_MODAL_COPY[String(typeNum)];
  if (!info) return;
  const modal = document.getElementById('scen-info-modal');
  if (!modal) return;
  document.getElementById('scen-info-title').textContent = info.title;
  document.getElementById('scen-info-body').textContent  = info.body;
  modal.style.display = 'flex';
}

function closeScenarioInfo() {
  const modal = document.getElementById('scen-info-modal');
  if (modal) modal.style.display = 'none';
}
