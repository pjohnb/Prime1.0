// WO-PRIME-SCENARIOS-01 Phase 2 — Scenarios Tab UI

const _SCENARIO_MODAL_COPY = {
  "0": {
    title: "Unknown — Signal Combination Under Review",
    body: "This symbol has multiple active signals that together do not match any of the 10 defined PRIME scenario types. The signals are listed below for manual review. If this convergence pattern repeats, it may represent a new scenario type worth defining. No trade action is recommended until the pattern is classified. Flag this for P review."
  },
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

// WO-PRIME-SCENARIO-EXECUTE-01: default budget by scenario type.
const _SCENARIO_BUDGETS = {
  "1": 500, "2": 500, "3": 750, "4": 1000, "4+": 1500,
  "6": 500, "7": 500, "8": 500, "9": 750,
};

// Registry of rendered scenario objects (scenario_id → sc) for the Execute dialog.
const _scenarioRegistry = {};
let _pendingScenarioExec = null;

// WO-PRIME-EXECUTE-MATA-01: MATA account list cache (loaded once from settings).
// Each entry: {name, type, buying_power, margin_available, weight}.
// Ordered to match the dropdown option order: Joint(926), Custodial(461), IRA(779).
let _mataAccountsCache = null;
const _MATA_ACCT_SUFFIXES = ['926', '461', '779'];

async function _loadMataAccounts() {
  if (_mataAccountsCache !== null) return _mataAccountsCache;
  try {
    const API = (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
    const resp = await fetch(API + '/settings');
    const data = resp.ok ? await resp.json() : {};
    _mataAccountsCache = Array.isArray(data.mata_accounts) && data.mata_accounts.length
      ? data.mata_accounts
      : [];
  } catch (e) {
    _mataAccountsCache = [];
  }
  return _mataAccountsCache;
}

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
  return '<span style="color:#22c55e;font-size:10px" title="FRESH">&#9679;</span>';
}

// WO-PRIME-SCENARIOS-BADGE-01: show scenario name on badge, not type number.
// Colors follow conviction level: green=HIGHEST, gray=Watch(LOW), amber=HIGH.
function _scTypeBadge(typeNum, typeName, conviction) {
  const isHighest = conviction === 'HIGHEST' || typeNum === '4' || typeNum === '4+' || typeNum === '10';
  const isLow = conviction === 'LOW' || String(typeNum) === '5';
  const bg = isHighest ? '#14532d' : (isLow ? '#1e2128' : '#78350f');
  const fg = isHighest ? '#86efac' : (isLow ? '#9ba8c4' : '#fcd34d');
  const br = isHighest ? '#16a34a' : (isLow ? '#374151' : '#d97706');
  const label = typeName || 'TYPE ' + typeNum;
  return `<span style="background:${bg};color:${fg};border:1px solid ${br};padding:3px 10px;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:.05em;font-family:var(--mono)">${label}</span>`;
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

function _scGetFillData(scenarioId) {
  try {
    const raw = sessionStorage.getItem('prime_scen_exec_' + scenarioId);
    return raw ? JSON.parse(raw) : null;
  } catch (e) { return null; }
}

function _renderExecutedCard(sc, fill) {
  const borderColor = '#22c55e';
  const acctLabels  = { '926': 'Joint (...926)', '461': 'Custodial (...461)', '779': 'IRA (...779)' };
  const acctLabel   = acctLabels[fill.account] || '...' + fill.account;
  const stopLabel   = fill.stop_type === 'TRAILING'
    ? 'Trailing ' + fill.stop_pct + '%'
    : 'Fixed ' + fill.stop_pct + '%';
  return `<div style="background:var(--bg3);border:1px solid var(--border);border-left:3px solid ${borderColor};border-radius:6px;padding:14px 16px">
  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px">
    <span style="background:#14532d;color:#86efac;border:1px solid #16a34a;padding:3px 10px;border-radius:4px;font-size:11px;font-weight:700;font-family:var(--mono)">EXECUTED</span>
    <span style="font-weight:700;font-size:13px;color:var(--text)">${sc.type_name || '--'}</span>
    ${_scDirTag(sc.direction)}
    <span style="font-size:11px;color:var(--text3)">${fill.mode || 'PAPER'}</span>
  </div>
  <div style="display:flex;align-items:baseline;gap:20px;margin-bottom:10px;flex-wrap:wrap">
    <span style="font-size:18px;font-weight:700;color:var(--text);font-family:var(--mono)">${sc.primary_symbol || '--'}</span>
    <span style="font-size:12px;color:var(--text3)">Fill <span style="color:#86efac;font-weight:600;font-family:var(--mono)">$${Number(fill.fill_price || 0).toFixed(2)}</span></span>
    <span style="font-size:12px;color:var(--text3)">Qty <span style="color:var(--text2);font-weight:600">${fill.qty}</span></span>
    <span style="font-size:12px;color:var(--text3)">Acct <span style="color:var(--text2)">${acctLabel}</span></span>
    <span style="font-size:12px;color:var(--text3)">Stop <span style="color:var(--amber)">${stopLabel}</span></span>
  </div>
  <div style="display:flex;gap:8px;align-items:center">
    <button onclick="openScenarioInfo('${sc.scenario_id}')"
      style="background:transparent;border:1px solid var(--border);color:var(--text3);padding:4px 10px;border-radius:4px;font-size:12px;cursor:pointer;min-width:32px"
      title="Learn about this scenario type">ⓘ</button>
    <button onclick="showView('portfolio')"
      style="background:var(--bg3);border:1px solid var(--border);color:var(--text2);padding:4px 14px;border-radius:4px;font-size:12px;cursor:pointer">Close Position &rarr;</button>
  </div>
</div>`;
}

function _renderScenarioCard(sc) {
  _scenarioRegistry[sc.scenario_id] = sc;

  const fill = _scGetFillData(sc.scenario_id);
  if (fill) return _renderExecutedCard(sc, fill);

  const constituents = sc.constituent_signals || [];
  const ep = _scEntryPrice(constituents);
  const entryStr = ep != null ? '$' + Number(ep).toFixed(2) : '—';
  const borderColor = _scBorderColor(sc.conviction);
  const isWatch = String(sc.type_num) === '5';
  const isUnknown = String(sc.type_num) === '0';

  const execBtn = (isWatch || isUnknown) ? '' :
    `<button onclick="openScenarioExecute('${sc.scenario_id}')"
      style="background:#14532d;border:1px solid #16a34a;color:#86efac;padding:4px 14px;border-radius:4px;font-size:12px;font-weight:700;cursor:pointer">Execute &#9654;</button>`;

  const unknownNote = isUnknown
    ? `<div style="margin-bottom:12px;padding:8px 10px;background:var(--bg4);border:1px solid var(--border);border-radius:4px;font-size:11px;color:var(--text3)">Signal combination not matching any defined scenario type — review for potential new scenario definition</div>`
    : '';

  return `<div style="background:var(--bg3);border:1px solid var(--border);border-left:3px solid ${borderColor};border-radius:6px;padding:14px 16px">
  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px">
    ${_scTypeBadge(sc.type_num, sc.type_name, sc.conviction)}
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
  ${unknownNote}<div style="display:flex;gap:8px;align-items:center">
    <button onclick="openScenarioInfo('${sc.scenario_id}')"
      style="background:transparent;border:1px solid var(--border);color:var(--text3);padding:4px 10px;border-radius:4px;font-size:12px;cursor:pointer;min-width:32px"
      title="Learn about this scenario type">ⓘ</button>
    ${execBtn}
  </div>
</div>`;
}

async function loadScenarios() {
  const container = document.getElementById('scen-cards');
  if (!container) return;
  // WO-PRIME-SCENARIOS-PHASE3-01: clear narration cache on refresh
  Object.keys(_scenarioNarrationCache).forEach(k => delete _scenarioNarrationCache[k]);
  _scenRenderFilterBar();
  container.innerHTML = '<div class="empty-state" style="padding:24px 0;color:var(--text3)">Loading scenarios…</div>';
  try {
    const limit = getScenarioMaxRows();
    const resp = await fetch(API + '/scenarios?limit=' + limit);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    _allScenarios = (data.scenarios || []).slice(0, limit);
    const filtered = _applyScenarioFilters(_allScenarios);
    _scenUpdateCount(filtered.length, _allScenarios.length);
    if (!_allScenarios.length) {
      container.innerHTML = '<div class="empty-state" style="padding:24px 0;color:var(--text3)">Checking scan state…</div>';
      try {
        const todayET2 = new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
        const _toETDate = ts => { const d = _parseUtc(ts); return d ? d.toLocaleDateString('en-CA', { timeZone: 'America/New_York' }) : null; };
        const [sigsR, scanR] = await Promise.all([
          fetch('/api/v1/signals'),
          fetch('/api/v1/scans/status'),
        ]);
        const sigsD2  = sigsR.ok ? await sigsR.json() : {};
        const scanD2  = scanR.ok ? await scanR.json() : {};
        const todaySigs  = (sigsD2.signals || []).filter(s => s.scan_ts && _toETDate(s.scan_ts) === todayET2);
        const scanners2  = scanD2.scanners || [];
        const anyRanToday = scanners2.some(sc => sc.last_run && _toETDate(sc.last_run) === todayET2);
        let innerHtml;
        if (!anyRanToday) {
          innerHtml = `<div style="font-size:14px">No scans have run today. Click Run All to begin scenario detection.</div>`;
        } else if (todaySigs.length === 0) {
          innerHtml = `<div style="font-size:14px">Scans completed — no signals produced. Market may be pre-open or scanners may need attention. Check Health tab.</div>`;
        } else {
          const psaSigs2  = todaySigs.filter(s => s.strategy === 'PSA');
          const idxSigs2  = todaySigs.filter(s => s.strategy === 'IDX');
          const uoaSigs2  = todaySigs.filter(s => s.strategy === 'UOA');
          const mtfaSigs2 = todaySigs.filter(s => s.strategy === 'MTFA');
          // AUDIT-005: bridge always writes status='APPROVED' regardless of
          // approval tier — use tier (WATCH vs APPROVED/STRONG) not status.
          const psaApprv    = psaSigs2.filter(s => s.tier === 'APPROVED' || s.tier === 'STRONG').length;
          const psaWatch    = psaSigs2.length - psaApprv;
          const idxTierLbl  = idxSigs2.length === 0 ? '0'
            : (idxSigs2.find(s => s.tier === 'STRONG_LONG' || s.tier === 'STRONG_SHORT') ? 'STRONG' : 'WEAK');
          const uoaStrongCt  = uoaSigs2.filter(s => s.tier && s.tier.toUpperCase().includes('STRONG')).length;
          const mtfaStrongCt = mtfaSigs2.filter(s => s.tier && s.tier.toUpperCase().includes('STRONG')).length;
          const badges = `[PSA: ${psaWatch} WATCH, ${psaApprv} APPROVED] [IDX: ${idxTierLbl}] [UOA: ${uoaStrongCt} STRONG] [MTFA: ${mtfaStrongCt} STRONG]`;
          innerHtml = `<div style="font-size:14px;margin-bottom:8px">Scans completed — signals present but none at required tier for scenario detection.</div>
            <div style="font-size:12px;color:var(--text2);margin-bottom:8px">${badges}</div>
            <div style="font-size:12px">No scenario threshold met. <a href="javascript:void(0)" onclick="openScanSummary()" style="color:var(--accent,#6366f1);text-decoration:underline">Open Scan Summary</a> for full details.</div>`;
        }
        container.innerHTML = `<div style="padding:48px 0;text-align:center;color:var(--text3)">${innerHtml}</div>`;
      } catch (_diagErr) {
        container.innerHTML = `<div style="padding:48px 0;text-align:center;color:var(--text3)">
          <div style="font-size:15px;margin-bottom:6px">No active scenarios detected.</div>
          <div style="font-size:12px">Scenarios will appear here automatically after the next scan run.</div>
        </div>`;
      }
      return;
    }
    container.innerHTML = filtered.map(_renderScenarioCard).join('') ||
      '<div style="padding:32px 0;text-align:center;color:var(--text3)">No scenarios match the current filters.</div>';
  } catch(e) {
    container.innerHTML = `<div style="color:var(--red);padding:16px 0">Error loading scenarios: ${e.message}</div>`;
  }
}

// WO-PRIME-SCENARIOS-PHASE3-01: session narration cache (scenario_id → narration text)
const _scenarioNarrationCache = {};
let _scenInfoCurrentId = null;

function openScenarioInfo(scenarioId) {
  const sc = _scenarioRegistry[scenarioId];
  if (!sc) return;
  const modal = document.getElementById('scen-info-modal');
  if (!modal) return;

  _scenInfoCurrentId = scenarioId;

  const copy = _SCENARIO_MODAL_COPY[String(sc.type_num)];
  document.getElementById('scen-info-title').textContent =
    copy ? copy.title : (sc.type_name || 'Scenario Details');

  const metaEl = document.getElementById('scen-info-meta');
  if (metaEl) {
    metaEl.innerHTML =
      `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px">` +
        _scConvictionBadge(sc.conviction) +
        _scDirTag(sc.direction) +
        `<span style="font-size:12px;color:var(--text3);font-family:var(--mono);font-weight:700">${sc.primary_symbol || '--'}</span>` +
      `</div>` +
      _scConstituentRows(sc.constituent_signals || []);
  }

  modal.style.display = 'flex';

  if (_scenarioNarrationCache[scenarioId]) {
    _scenSetNarration(_scenarioNarrationCache[scenarioId]);
    return;
  }
  _scenSetNarrationLoading();
  _scenFetchNarration(sc);
}

function closeScenarioInfo() {
  const modal = document.getElementById('scen-info-modal');
  if (modal) modal.style.display = 'none';
  _scenInfoCurrentId = null;
}

function _scenSetNarrationLoading() {
  const el = document.getElementById('scen-info-narration');
  if (!el) return;
  el.innerHTML =
    `<div style="display:flex;align-items:center;gap:10px;color:var(--text3);padding:8px 0">` +
      `<div style="width:15px;height:15px;border:2px solid var(--border);border-top-color:var(--accent,#6366f1);` +
           `border-radius:50%;animation:prime-spin 0.8s linear infinite;flex-shrink:0"></div>` +
      `<span>Analyzing scenario…</span>` +
    `</div>`;
}

function _scenSetNarration(text) {
  const el = document.getElementById('scen-info-narration');
  if (!el) return;
  el.innerHTML = `<p style="margin:0">${_escHtml(text)}</p>`;
}

function _scenSetNarrationError(scenarioId) {
  const el = document.getElementById('scen-info-narration');
  if (!el) return;
  el.innerHTML =
    `<div style="color:var(--red,#ef4444);font-size:13px">Unable to generate analysis. Please try again.` +
      `<button onclick="_scenRetryNarration('${scenarioId}')"` +
        ` style="margin-left:10px;background:transparent;border:1px solid var(--border);` +
               `color:var(--text2);padding:3px 10px;border-radius:4px;font-size:12px;cursor:pointer">Retry</button>` +
    `</div>`;
}

function _scenRetryNarration(scenarioId) {
  const sc = _scenarioRegistry[scenarioId];
  if (!sc) return;
  _scenInfoCurrentId = scenarioId;
  _scenSetNarrationLoading();
  _scenFetchNarration(sc);
}

async function _scenFetchNarration(sc) {
  try {
    const scenarioId = sc.scenario_id;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 30000);
    let resp;
    try {
      resp = await fetch(API + '/scenarios/narrate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
        body: JSON.stringify({
          scenario_id:        sc.scenario_id,
          type_num:           sc.type_num,
          type_name:          sc.type_name,
          direction:          sc.direction,
          conviction:         sc.conviction,
          primary_symbol:     sc.primary_symbol,
          constituent_signals: sc.constituent_signals || []
        })
      });
    } finally {
      clearTimeout(timer);
    }
    const data = await resp.json();
    if (!resp.ok || data.error) throw new Error(data.error || 'HTTP ' + resp.status);
    const narration = data.narration || '';
    _scenarioNarrationCache[scenarioId] = narration;
    if (_scenInfoCurrentId === scenarioId) _scenSetNarration(narration);
  } catch (_e) {
    if (_scenInfoCurrentId === sc.scenario_id) _scenSetNarrationError(sc.scenario_id);
  }
}

function _escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── WO-PRIME-SCENARIO-EXECUTE-01: Execute dialog ─────────────────────────────

async function openScenarioExecute(scenarioId) {
  const sc = _scenarioRegistry[scenarioId];
  if (!sc) return;
  _pendingScenarioExec = sc;

  // Pre-load MATA accounts so _scExecUpdate() can render the breakdown synchronously.
  await _loadMataAccounts();

  const constituents   = sc.constituent_signals || [];
  const ep             = _scEntryPrice(constituents) || 0;
  const dir            = sc.direction || 'LONG';
  const typeNum        = String(sc.type_num);
  const defaultBudget  = _SCENARIO_BUDGETS[typeNum] || 500;

  document.getElementById('scen-exec-badge').innerHTML = _scTypeBadge(typeNum, sc.type_name, sc.conviction);
  document.getElementById('scen-exec-symbol').textContent = sc.primary_symbol || '--';
  document.getElementById('scen-exec-dir').innerHTML      = _scDirTag(dir);
  document.getElementById('scen-exec-price').textContent  = ep ? '$' + Number(ep).toFixed(2) : '--';

  // WO-PRIME-EXECUTE-STOP-01: default Stop % by conviction tier.
  // Tighter stop for higher conviction (less tolerance for adverse move).
  const _STOP_DEFAULTS = { 'HIGHEST': '2', 'HIGH': '3', 'LOW': '5' };
  const defaultStopPct = _STOP_DEFAULTS[sc.conviction] || '3';

  document.getElementById('scen-exec-budget').value     = defaultBudget;
  document.getElementById('scen-exec-account').value    = (_mataAccountsCache && _mataAccountsCache.length) ? 'MATA' : '';
  document.getElementById('scen-exec-stop-type').value  = 'TRAILING';
  document.getElementById('scen-exec-stop-pct').value   = defaultStopPct;

  // Rollover IRA cannot hold short positions.
  const iraOpt = document.getElementById('scen-exec-ira-opt');
  if (iraOpt) iraOpt.disabled = (dir === 'SHORT');

  const msgEl = document.getElementById('scen-exec-msg');
  if (msgEl) { msgEl.textContent = ''; }

  _scExecUpdate();
  document.getElementById('scen-exec-modal').style.display = 'flex';
}

function closeScenarioExecute() {
  _pendingScenarioExec = null;
  const modal = document.getElementById('scen-exec-modal');
  if (modal) modal.style.display = 'none';
}

function _scExecUpdate() {
  if (!_pendingScenarioExec) return;
  const sc  = _pendingScenarioExec;
  const ep  = _scEntryPrice(sc.constituent_signals || []) || 0;
  const dir = sc.direction || 'LONG';

  const budget  = parseFloat(document.getElementById('scen-exec-budget').value) || 0;
  const qty     = (ep > 0 && budget > 0) ? Math.floor(budget / ep) : 0;
  const stopPct = parseFloat(document.getElementById('scen-exec-stop-pct').value) || 3;
  const acct    = (document.getElementById('scen-exec-account').value || '').trim();

  // MATA breakdown
  const qtyEl       = document.getElementById('scen-exec-qty');
  const breakdownEl = document.getElementById('scen-exec-mata-breakdown');
  if (acct === 'MATA') {
    const mataAccts = _mataAccountsCache || [];
    if (mataAccts.length) {
      const parts = mataAccts.map((a, i) => {
        const w      = parseFloat(a.weight) || 0;
        const shares = qty > 0 ? Math.floor(qty * w / 100) : 0;
        const label  = a.name + ' (' + w + '%)';
        return shares > 0 ? label + ': ' + shares + ' sh' : label;
      });
      if (breakdownEl) { breakdownEl.textContent = parts.join(' · '); breakdownEl.style.display = 'block'; }
    } else {
      if (breakdownEl) { breakdownEl.textContent = 'Configure MATA distribution in Settings first.'; breakdownEl.style.display = 'block'; }
    }
    if (qtyEl) qtyEl.textContent = qty > 0 ? qty + ' total' : '—';
  } else {
    if (breakdownEl) breakdownEl.style.display = 'none';
    if (qtyEl) qtyEl.textContent = qty > 0 ? String(qty) : '—';
  }

  let stopPrice = 0;
  if (ep > 0 && stopPct > 0) {
    stopPrice = dir === 'SHORT' ? ep * (1 + stopPct / 100) : ep * (1 - stopPct / 100);
  }
  const stopEl = document.getElementById('scen-exec-stop-price');
  if (stopEl) stopEl.textContent = stopPrice > 0 ? '$' + stopPrice.toFixed(2) : '—';

  const confirmBtn = document.getElementById('scen-exec-confirm-btn');
  if (confirmBtn) confirmBtn.disabled = !acct;
}

async function submitScenarioExecute() {
  if (!_pendingScenarioExec) return;

  // Addendum: debounce — disable immediately; re-enable only on error or after close.
  const confirmBtn = document.getElementById('scen-exec-confirm-btn');
  if (confirmBtn) confirmBtn.disabled = true;

  const sc           = _pendingScenarioExec;
  const constituents = sc.constituent_signals || [];
  const ep           = _scEntryPrice(constituents) || 0;
  const dir          = sc.direction || 'LONG';
  const msgEl        = document.getElementById('scen-exec-msg');

  // Prefer PSA signal as execution anchor; fall back to first constituent.
  const psa      = constituents.find(c => c.strategy === 'PSA');
  const anchor   = psa || constituents[0] || {};
  const signalId = anchor.signal_id || sc.scenario_id || '';

  const budget   = parseFloat(document.getElementById('scen-exec-budget').value) || 0;
  const qty      = (ep > 0 && budget > 0) ? Math.floor(budget / ep) : 0;
  const acct     = (document.getElementById('scen-exec-account').value || '').trim();
  const stopType = document.getElementById('scen-exec-stop-type').value || 'TRAILING';
  const stopPct  = parseFloat(document.getElementById('scen-exec-stop-pct').value) || 3;
  const rth      = typeof _isRTH === 'function' ? _isRTH() : true;

  if (!acct) {
    if (msgEl) { msgEl.textContent = 'Account selection is required.'; msgEl.style.color = 'var(--red)'; }
    if (confirmBtn) confirmBtn.disabled = false;
    return;
  }
  if (qty <= 0) {
    if (msgEl) { msgEl.textContent = 'Budget too low — cannot purchase one share at this price.'; msgEl.style.color = 'var(--red)'; }
    if (confirmBtn) confirmBtn.disabled = false;
    return;
  }

  const API     = (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
  const token   = typeof _sigToken === 'function' ? _sigToken() : '';
  const payload = {
    confirmed:      true,
    qty,
    order_type:     rth ? 'MARKET' : 'LIMIT',
    direction:      dir,
    target_account: acct,
    stop_type:      stopType,
    stop_pct:       stopPct,
  };
  if (stopType === 'TRAILING') payload.trailing_stop_pct = stopPct / 100.0;
  if (!rth && ep > 0) payload.limit_price = ep;

  // WO-PRIME-EXECUTE-MATA-01: when MATA is selected, compute per-account share
  // quantities from configured weights and send as mata_qtys so the backend can
  // place the correctly-sized order on each account without needing weight logic.
  if (acct === 'MATA' && _mataAccountsCache && _mataAccountsCache.length) {
    const mataQtys = {};
    _mataAccountsCache.forEach((a, i) => {
      const w = parseFloat(a.weight) || 0;
      const sfx = a.suffix || _MATA_ACCT_SUFFIXES[i];
      if (sfx) mataQtys[sfx] = Math.floor(qty * w / 100);
    });
    payload.mata_qtys = mataQtys;
  }

  try {
    const resp = await fetch(API + '/signals/' + encodeURIComponent(signalId) + '/execute', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
      body:    JSON.stringify(payload),
    });
    const data = await resp.json().catch(() => ({}));

    if (resp.ok) {
      const mode       = data.mode || 'PAPER';
      const fillPrice  = data.execution_price || ep;
      const acctLabels = { '926': 'Joint (...926)', '461': 'Custodial (...461)', '779': 'IRA (...779)' };
      if (msgEl) {
        let successMsg;
        if (acct === 'MATA') {
          const n = Array.isArray(data.orders_placed) ? data.orders_placed.length : 0;
          successMsg = mode + ': MATA — ' + n + ' order' + (n !== 1 ? 's' : '') + ' placed';
        } else {
          successMsg = mode + ': ' + qty + ' shares @ $' + Number(fillPrice).toFixed(2) +
            ' — ' + (acctLabels[acct] || '...' + acct);
        }
        msgEl.textContent = successMsg;
        msgEl.style.color = 'var(--green)';
      }
      _scExecMarkExecuted(sc.scenario_id, {
        mode, qty, fill_price: fillPrice, account: acct,
        stop_type: stopType, stop_pct: stopPct,
      });
      setTimeout(() => closeScenarioExecute(), 1800);
    } else if ((data.error || '') === 'after_hours') {
      if (msgEl) {
        msgEl.textContent = 'After-hours — limit order required. Scan price pre-filled.';
        msgEl.style.color = 'var(--amber)';
      }
      if (confirmBtn) confirmBtn.disabled = false;
    } else {
      if (msgEl) { msgEl.textContent = 'Error: ' + (data.error || resp.status); msgEl.style.color = 'var(--red)'; }
      if (confirmBtn) confirmBtn.disabled = false;
    }
  } catch (e) {
    if (msgEl) { msgEl.textContent = 'Network error: ' + e.message; msgEl.style.color = 'var(--red)'; }
    if (confirmBtn) confirmBtn.disabled = false;
  }
}

function _scExecMarkExecuted(scenarioId, fillData) {
  try {
    sessionStorage.setItem('prime_scen_exec_' + scenarioId, JSON.stringify(fillData));
  } catch (e) {}
  loadScenarios();
}

// ── WO-PRIME-SCENARIOS-REFRESH-01: two-mode status poller ────────────────────
// Idle:       poll /scenarios/status every 60 s
// Aggressive: poll every 3 s for 30 s after a detection fires (or while scan running)
// On timestamp change: immediately fetch /scenarios and re-render cards

let _scenDetectionTs  = null;
let _scenPollTimer    = null;
let _scenAggrUntil    = 0;         // epoch ms — aggressive polling until this time

const _SCEN_AGGR_MS   = 3000;     // 3 s aggressive interval
const _SCEN_IDLE_MS   = 60000;    // 60 s idle interval
const _SCEN_AGGR_DUR  = 30000;    // 30 s aggressive window duration

async function _checkScenarioStatus() {
  try {
    const resp = await fetch(API + '/scenarios/status');
    if (!resp.ok) return;
    const data = await resp.json();

    // Scan running → extend aggressive window so we're ready when detection fires
    if (data.scan_running) {
      if (Date.now() >= _scenAggrUntil) {
        // Transition to aggressive mode
        _scenAggrUntil = Date.now() + _SCEN_AGGR_DUR;
        _reschedScenPoll();
      } else {
        // Already aggressive — just extend the window
        _scenAggrUntil = Date.now() + _SCEN_AGGR_DUR;
      }
      return;
    }

    // Detection timestamp changed → load immediately and go aggressive
    const newTs = data.last_detection_completed_at;
    if (newTs && newTs !== _scenDetectionTs) {
      _scenDetectionTs = newTs;
      _scenAggrUntil   = Date.now() + _SCEN_AGGR_DUR;
      _reschedScenPoll();
      loadScenarios();
    }
  } catch(e) { /* API offline — keep current interval */ }
}

async function _scenPollTick() {
  await _checkScenarioStatus();
  // Revert to idle once aggressive window expires
  if (_scenPollTimer !== null && Date.now() >= _scenAggrUntil) {
    clearInterval(_scenPollTimer);
    _scenPollTimer = setInterval(_scenPollTick, _SCEN_IDLE_MS);
  }
}

function _reschedScenPoll() {
  clearInterval(_scenPollTimer);
  const ms = Date.now() < _scenAggrUntil ? _SCEN_AGGR_MS : _SCEN_IDLE_MS;
  _scenPollTimer = setInterval(_scenPollTick, ms);
}

function startScenarioPoll() {
  if (_scenPollTimer) return;            // already running
  _checkScenarioStatus();                // immediate check on tab activation
  _scenPollTimer = setInterval(_scenPollTick, _SCEN_IDLE_MS);
}

function stopScenarioPoll() {
  clearInterval(_scenPollTimer);
  _scenPollTimer = null;
}

// ── WO-PRIME-SCENARIOS-FILTERS-01: filter bar ─────────────────────────────────

const _SCEN_FILTER_KEY = 'prime_scen_filters_v1';

// Must match SCENARIO_TYPES["name"] values in prime_scenario_engine.py
const _SCEN_TYPE_NAMES = [
  'Watch',
  'Unknown',
  'Sniper — Pure',
  'Sniper — Confirmed',
  'Sniper — Institutional',
  'Sniper — Trifecta',
  'Sniper — Ultimate',
  'Sniper — Anomalous',
  'Sniper — Sector Phase',
  'Sniper — Metals MR',
  'Timeframe Confluence',
];

function _scenDefaultFilters() {
  return {
    staleness: 'All',
    direction: 'Both',
    conviction: 'All',
    types: _SCEN_TYPE_NAMES.slice(),
  };
}

// Initialise from localStorage on script load so filters survive refresh.
let _scenFilters = (() => {
  const d = _scenDefaultFilters();
  try {
    const raw = localStorage.getItem(_SCEN_FILTER_KEY);
    if (raw) {
      const s = JSON.parse(raw);
      if (s.staleness)           d.staleness = s.staleness;
      if (s.direction)           d.direction  = s.direction;
      if (s.conviction)          d.conviction = s.conviction;
      if (Array.isArray(s.types)) d.types     = s.types;
    }
  } catch(e) {}
  return d;
})();

let _allScenarios = [];

function _scenSaveFilters() {
  try { localStorage.setItem(_SCEN_FILTER_KEY, JSON.stringify(_scenFilters)); } catch(e) {}
}

function _applyScenarioFilters(scenarios) {
  return scenarios.filter(sc => {
    if (_scenFilters.staleness !== 'All' &&
        (sc.staleness_status || 'FRESH') !== _scenFilters.staleness) return false;
    if (_scenFilters.direction !== 'Both' &&
        (sc.direction || 'LONG') !== _scenFilters.direction) return false;
    if (_scenFilters.conviction !== 'All' &&
        (sc.conviction || 'HIGH') !== _scenFilters.conviction) return false;
    if (_scenFilters.types.length < _SCEN_TYPE_NAMES.length &&
        !_scenFilters.types.includes(sc.type_name || '')) return false;
    return true;
  });
}

function _scenUpdateCount(filtered, total) {
  const el = document.getElementById('scen-count');
  if (el) el.textContent = total > 0 ? ' (' + filtered + ' of ' + total + ')' : '';
}

function _scenSetStaleness(val) { _scenFilters.staleness = val; _scenFilterChanged(); }
function _scenSetDirection(val) { _scenFilters.direction = val; _scenFilterChanged(); }
function _scenSetConviction(val) { _scenFilters.conviction = val; _scenFilterChanged(); }

function _scenToggleType(name) {
  const idx = _scenFilters.types.indexOf(name);
  if (idx >= 0) _scenFilters.types.splice(idx, 1);
  else _scenFilters.types.push(name);
  _scenFilterChanged();
}

function clearScenarioFilters() {
  _scenFilters = _scenDefaultFilters();
  _scenSaveFilters();
  _scenRenderFilterBar();
  const filtered = _applyScenarioFilters(_allScenarios);
  _scenUpdateCount(filtered.length, _allScenarios.length);
  const container = document.getElementById('scen-cards');
  if (container) container.innerHTML = filtered.map(_renderScenarioCard).join('') ||
    '<div style="padding:32px 0;text-align:center;color:var(--text3)">No scenarios match the current filters.</div>';
}

function _scenFilterChanged() {
  _scenSaveFilters();
  _scenRenderFilterBar();
  const filtered = _applyScenarioFilters(_allScenarios);
  _scenUpdateCount(filtered.length, _allScenarios.length);
  const container = document.getElementById('scen-cards');
  if (container) container.innerHTML = filtered.map(_renderScenarioCard).join('') ||
    '<div style="padding:32px 0;text-align:center;color:var(--text3)">No scenarios match the current filters.</div>';
}

function _scFiltBtn(label, active, onclick) {
  const bg    = active ? 'var(--bg2)'  : 'var(--bg4)';
  const bdr   = active ? '#4a9eff'     : 'var(--border)';
  const color = active ? '#4a9eff'     : 'var(--text3)';
  return '<button onclick="' + onclick + '" style="background:' + bg +
    ';border:1px solid ' + bdr + ';color:' + color +
    ';padding:3px 9px;border-radius:3px;font-size:11px;cursor:pointer;' +
    'font-family:var(--mono);white-space:nowrap">' + label + '</button>';
}

function _scenRenderFilterBar() {
  const bar = document.getElementById('scen-filter-bar');
  if (!bar) return;

  // Group label: 10px monospace uppercase muted text above the button row
  const lbl = 'font-size:10px;color:var(--text3);font-family:var(--mono);' +
              'text-transform:uppercase;letter-spacing:.05em;white-space:nowrap';
  // Each group: column flex so label sits above its buttons; flex-shrink:0 keeps
  // the group intact when the bar wraps to a second line (AC5).
  const grp = 'display:flex;flex-direction:column;gap:3px;flex-shrink:0';
  const row = 'display:flex;gap:4px;align-items:center';
  // Thin vertical rule drawn via border-left on groups 2-4 to separate them from
  // the preceding group without creating orphaned divider elements during wrap.
  const grpDiv = grp + ';border-left:1px solid var(--border);padding-left:12px';

  const stale = ['FRESH', 'All'].map(v =>
    _scFiltBtn(v, _scenFilters.staleness === v, "_scenSetStaleness('" + v + "')")
  ).join('');

  const dirs = ['LONG', 'SHORT', 'Both'].map(v =>
    _scFiltBtn(v, _scenFilters.direction === v, "_scenSetDirection('" + v + "')")
  ).join('');

  const convs = ['LOW', 'HIGH', 'HIGHEST', 'All'].map(v =>
    _scFiltBtn(v, _scenFilters.conviction === v, "_scenSetConviction('" + v + "')")
  ).join('');

  const typeActive = _scenFilters.types.length < _SCEN_TYPE_NAMES.length;
  const typesLabel = typeActive
    ? 'Types (' + _scenFilters.types.length + '/' + _SCEN_TYPE_NAMES.length + ') ▾'
    : 'All Types ▾';
  const typeBg    = typeActive ? 'var(--bg2)'  : 'var(--bg4)';
  const typeBdr   = typeActive ? '#4a9eff'     : 'var(--border)';
  const typeColor = typeActive ? '#4a9eff'     : 'var(--text3)';

  const typeCheckboxes = _SCEN_TYPE_NAMES.map(n => {
    const checked = _scenFilters.types.includes(n);
    const esc = n.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    return '<label style="display:flex;align-items:center;gap:6px;padding:4px 8px;cursor:pointer;' +
      'font-size:11px;font-family:var(--mono);color:var(--text2);white-space:nowrap;user-select:none">' +
      '<input type="checkbox"' + (checked ? ' checked' : '') +
      ' onchange="_scenToggleType(\'' + esc + '\')" style="accent-color:#4a9eff;cursor:pointer"> ' +
      n + '</label>';
  }).join('');

  const typePanel =
    '<div id="scen-type-panel" style="display:none;position:absolute;z-index:200;top:100%;left:0;' +
    'margin-top:4px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;' +
    'padding:4px 2px;min-width:210px;box-shadow:0 4px 14px rgba(0,0,0,.55)">' +
    typeCheckboxes + '</div>';

  const typeBtnStyle = 'background:' + typeBg + ';border:1px solid ' + typeBdr +
    ';color:' + typeColor + ';padding:3px 9px;border-radius:3px;font-size:11px;' +
    'cursor:pointer;font-family:var(--mono);white-space:nowrap';

  const clearBtn =
    '<button onclick="clearScenarioFilters()" style="background:transparent;border:none;' +
    'color:var(--text3);font-size:11px;cursor:pointer;padding:3px 6px;font-family:var(--mono);' +
    'text-decoration:underline;align-self:flex-end">Clear filters</button>';

  bar.innerHTML =
    '<div style="display:flex;flex-wrap:wrap;gap:6px 16px;align-items:flex-end;' +
    'padding:8px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px">' +
    '<div style="' + grp + '">' +
      '<span style="' + lbl + '">Staleness</span>' +
      '<div style="' + row + '">' + stale + '</div>' +
    '</div>' +
    '<div style="' + grpDiv + '">' +
      '<span style="' + lbl + '">Direction</span>' +
      '<div style="' + row + '">' + dirs + '</div>' +
    '</div>' +
    '<div style="' + grpDiv + '">' +
      '<span style="' + lbl + '">Conviction</span>' +
      '<div style="' + row + '">' + convs + '</div>' +
    '</div>' +
    '<div style="' + grpDiv + ';position:relative">' +
      '<span style="' + lbl + '">Type</span>' +
      '<div style="' + row + ';position:relative">' +
        '<button style="' + typeBtnStyle + '" onclick="(function(){' +
          'var p=document.getElementById(\'scen-type-panel\');' +
          'if(p)p.style.display=p.style.display===\'none\'?\'block\':\'none\';})()">' +
          typesLabel + '</button>' +
        typePanel +
      '</div>' +
    '</div>' +
    clearBtn +
    '</div>';

  // Close type panel when clicking outside the filter bar
  document.removeEventListener('click', _scenTypePanelClose);
  document.addEventListener('click', _scenTypePanelClose);
}

function _scenTypePanelClose(e) {
  const bar = document.getElementById('scen-filter-bar');
  if (!bar || bar.contains(e.target)) return;
  const panel = document.getElementById('scen-type-panel');
  if (panel) panel.style.display = 'none';
}

// WO-PRIME-SCENARIOS-UX-01 Phase 2: scenario type reference panel
function openScenarioTypes() {
  document.getElementById('scen-types-modal').style.display = 'flex';
  document.addEventListener('keydown', _scenTypesEscHandler);
}

function closeScenarioTypes() {
  document.getElementById('scen-types-modal').style.display = 'none';
  document.removeEventListener('keydown', _scenTypesEscHandler);
}

function _scenTypesEscHandler(e) {
  if (e.key === 'Escape') closeScenarioTypes();
}

// CIL-40: Signal Legend modal
function openSignalLegend() {
  document.getElementById('signal-legend-modal').style.display = 'flex';
  document.addEventListener('keydown', _signalLegendEscHandler);
}

function closeSignalLegend() {
  document.getElementById('signal-legend-modal').style.display = 'none';
  document.removeEventListener('keydown', _signalLegendEscHandler);
}

function _signalLegendEscHandler(e) {
  if (e.key === 'Escape') closeSignalLegend();
}

// WO-PRIME-SCAN-SUMMARY-01: Scan Summary modal
function evaluateScenarioAchievability(signals) {
  const A = 'ACHIEVABLE', P = 'PARTIAL', N = 'NOT_MET';

  const idxSigs = signals.filter(s => s.strategy === 'IDX');
  const psaSigs = signals.filter(s => s.strategy === 'PSA');
  const uoaSigs = signals.filter(s => s.strategy === 'UOA');
  const peadSigs = signals.filter(s => s.strategy === 'PEAD');
  const mtfaSigs = signals.filter(s => s.strategy === 'MTFA');
  const srsSigs  = signals.filter(s => s.strategy === 'SRS');
  const mmrSigs  = signals.filter(s => s.strategy === 'MMR');

  const isIdxStrong  = s => s.tier === 'STRONG_LONG' || s.tier === 'STRONG_SHORT';
  const isTierStrong = s => s.tier && s.tier.toUpperCase().includes('STRONG');

  const idxAny    = idxSigs.length > 0;
  const idxStrong = idxSigs.some(isIdxStrong);
  const psaAny    = psaSigs.length > 0;
  const psaApproved = psaSigs.some(s => s.tier === 'APPROVED' || s.tier === 'STRONG');
  const uoaAny    = uoaSigs.length > 0;
  const uoaStrong = uoaSigs.some(isTierStrong);
  const peadAny   = peadSigs.length > 0;
  const peadStrong = peadSigs.some(isTierStrong);
  const mtfaAny   = mtfaSigs.length > 0;
  const mtfaStrong = mtfaSigs.some(isTierStrong);
  const srsAny       = srsSigs.length > 0;
  const srsRecovering = srsSigs.some(s => s.tier === 'RECOVERING');
  const mmrAny       = mmrSigs.length > 0;
  const mmrTranche2  = mmrSigs.some(s => s.tier === 'TRANCHE_2');

  return [
    // Type 1: IDX STRONG
    idxStrong ? A : idxAny ? P : N,
    // Type 2: IDX any + PSA APPROVED
    (idxAny && psaApproved) ? A : (idxAny || psaAny) ? P : N,
    // Type 3: IDX any + PSA APPROVED + (UOA STRONG or PEAD STRONG)
    (idxAny && psaApproved && (uoaStrong || peadStrong)) ? A
      : (idxAny || psaAny || uoaAny || peadAny) ? P : N,
    // Type 4: IDX STRONG + PSA APPROVED + (UOA STRONG or PEAD STRONG)
    (idxStrong && psaApproved && (uoaStrong || peadStrong)) ? A
      : (idxAny || psaAny || uoaAny || peadAny) ? P : N,
    // Type 5: any scanner fired
    signals.length > 0 ? A : N,
    // Type 6: PSA APPROVED + (UOA STRONG or PEAD STRONG) — no IDX required
    (psaApproved && (uoaStrong || peadStrong)) ? A
      : (psaAny || uoaAny || peadAny) ? P : N,
    // Type 7: SRS RECOVERING + PSA APPROVED
    (srsRecovering && psaApproved) ? A : (srsAny || psaAny) ? P : N,
    // Type 8: MMR TRANCHE_2 + PSA APPROVED
    (mmrTranche2 && psaApproved) ? A : (mmrAny || psaAny) ? P : N,
    // Type 9: MTFA STRONG + any confirming signal (IDX, UOA, PSA, PEAD)
    (mtfaStrong && (idxAny || uoaAny || psaAny || peadAny)) ? A
      : (mtfaAny || idxAny || uoaAny || psaAny || peadAny) ? P : N,
    // Type 10: IDX any + PSA APPROVED + UOA STRONG + PEAD STRONG
    (idxAny && psaApproved && uoaStrong && peadStrong) ? A
      : (idxAny || psaAny || uoaAny || peadAny) ? P : N,
    // Type 11: always NOT MET (Post-Beta)
    N,
  ];
}

function _renderScanSummaryStatus(el, signals, scanners) {
  const SCANNERS = ['IDX', 'PSA', 'UOA', 'PEAD', 'MTFA', 'SRS', 'MMR'];
  const scanMap = {};
  (scanners || []).forEach(sc => { scanMap[sc.scanner] = sc; });

  el.innerHTML = SCANNERS.map(name => {
    const sc = scanMap[name] || {};
    const sigs = signals.filter(s => s.strategy === name);
    const count = sigs.length;
    const lastRun = sc.last_run ? sc.last_run.slice(11, 16) : '—';

    let tier = null, tierColor = 'var(--text3)';
    if (count > 0) {
      if (name === 'IDX') {
        const strong = sigs.find(s => s.tier === 'STRONG_LONG' || s.tier === 'STRONG_SHORT');
        tier = strong ? strong.tier : (sigs[0].tier || 'WEAK');
        tierColor = strong ? 'var(--green,#22c55e)' : 'var(--yellow,#eab308)';
      } else if (name === 'PSA') {
        const approved = sigs.find(s => s.tier === 'APPROVED' || s.tier === 'STRONG');
        tier = approved ? approved.tier : 'WATCH';
        tierColor = approved ? 'var(--green,#22c55e)' : 'var(--yellow,#eab308)';
      } else {
        const strong = sigs.find(s => s.tier && s.tier.toUpperCase().includes('STRONG'));
        tier = strong ? strong.tier : (sigs[0].tier || 'ACTIVE');
        tierColor = strong ? 'var(--green,#22c55e)' : 'var(--text2)';
      }
    }

    return `<div style="background:var(--surface2,#1e2330);border:1px solid var(--border);border-radius:6px;padding:8px 10px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
        <span style="font-size:12px;font-weight:700;color:var(--text);font-family:var(--mono)">${name}</span>
        <span style="font-size:10px;color:var(--text3)">${lastRun}</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px">
        <span style="font-size:13px;font-weight:700;color:var(--text)">${count}</span>
        <span style="font-size:10px;color:var(--text3)">signals</span>
        <span style="font-size:10px;font-weight:600;color:${tierColor};margin-left:auto">${tier || 'NO DATA'}</span>
      </div>
    </div>`;
  }).join('');
}

function _renderScanSummaryMatrix(el, signals) {
  const results = evaluateScenarioAchievability(signals);
  const A = 'ACHIEVABLE', P = 'PARTIAL';

  const isIdxStrong  = s => s.tier === 'STRONG_LONG' || s.tier === 'STRONG_SHORT';
  const isTierStrong = s => s.tier && s.tier.toUpperCase().includes('STRONG');
  const idxSigs  = signals.filter(s => s.strategy === 'IDX');
  const psaSigs  = signals.filter(s => s.strategy === 'PSA');
  const uoaSigs  = signals.filter(s => s.strategy === 'UOA');
  const peadSigs = signals.filter(s => s.strategy === 'PEAD');
  const mtfaSigs = signals.filter(s => s.strategy === 'MTFA');
  const srsSigs  = signals.filter(s => s.strategy === 'SRS');
  const mmrSigs  = signals.filter(s => s.strategy === 'MMR');

  const idxAny     = idxSigs.length > 0;
  const idxStrong  = idxSigs.some(isIdxStrong);
  const psaAny     = psaSigs.length > 0;
  const psaApproved = psaSigs.some(s => s.tier === 'APPROVED' || s.tier === 'STRONG');
  const uoaAny    = uoaSigs.length > 0;
  const uoaStrong = uoaSigs.some(isTierStrong);
  const peadAny   = peadSigs.length > 0;
  const peadStrong = peadSigs.some(isTierStrong);
  const mtfaAny   = mtfaSigs.length > 0;
  const mtfaStrong = mtfaSigs.some(isTierStrong);
  const srsAny       = srsSigs.length > 0;
  const srsRecovering = srsSigs.some(s => s.tier === 'RECOVERING');
  const mmrAny       = mmrSigs.length > 0;
  const mmrTranche2  = mmrSigs.some(s => s.tier === 'TRANCHE_2');

  // Per-type column states: [IDX, PSA, UOA/PEAD, MTFA, SRS, MMR]
  // Values: 'MET' | 'PARTIAL' | 'ABSENT' | 'NOT_REQ'
  const typeColStates = [
    // Type 1: IDX STRONG
    [idxStrong ? 'MET' : idxAny ? 'PARTIAL' : 'ABSENT', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ'],
    // Type 2: IDX any + PSA APPROVED
    [idxAny ? 'MET' : 'ABSENT', psaApproved ? 'MET' : psaAny ? 'PARTIAL' : 'ABSENT', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ'],
    // Type 3: IDX + PSA APPROVED + UOA/PEAD STRONG
    [idxAny ? 'MET' : 'ABSENT', psaApproved ? 'MET' : psaAny ? 'PARTIAL' : 'ABSENT',
     (uoaStrong || peadStrong) ? 'MET' : (uoaAny || peadAny) ? 'PARTIAL' : 'ABSENT', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ'],
    // Type 4: IDX STRONG + PSA APPROVED + UOA/PEAD STRONG
    [idxStrong ? 'MET' : idxAny ? 'PARTIAL' : 'ABSENT', psaApproved ? 'MET' : psaAny ? 'PARTIAL' : 'ABSENT',
     (uoaStrong || peadStrong) ? 'MET' : (uoaAny || peadAny) ? 'PARTIAL' : 'ABSENT', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ'],
    // Type 5: any signal — individual columns N/A
    ['NOT_REQ', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ'],
    // Type 6: PSA APPROVED + UOA/PEAD STRONG (no IDX)
    ['NOT_REQ', psaApproved ? 'MET' : psaAny ? 'PARTIAL' : 'ABSENT',
     (uoaStrong || peadStrong) ? 'MET' : (uoaAny || peadAny) ? 'PARTIAL' : 'ABSENT', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ'],
    // Type 7: SRS RECOVERING + PSA APPROVED
    ['NOT_REQ', psaApproved ? 'MET' : psaAny ? 'PARTIAL' : 'ABSENT', 'NOT_REQ', 'NOT_REQ',
     srsRecovering ? 'MET' : srsAny ? 'PARTIAL' : 'ABSENT', 'NOT_REQ'],
    // Type 8: MMR TRANCHE_2 + PSA APPROVED
    ['NOT_REQ', psaApproved ? 'MET' : psaAny ? 'PARTIAL' : 'ABSENT', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ',
     mmrTranche2 ? 'MET' : mmrAny ? 'PARTIAL' : 'ABSENT'],
    // Type 9: MTFA STRONG + any confirming signal (IDX, UOA, PSA, PEAD)
    [idxAny ? 'MET' : 'ABSENT', psaAny ? 'MET' : 'ABSENT',
     (uoaAny || peadAny) ? 'MET' : 'ABSENT', mtfaStrong ? 'MET' : mtfaAny ? 'PARTIAL' : 'ABSENT', 'NOT_REQ', 'NOT_REQ'],
    // Type 10: IDX + PSA APPROVED + UOA STRONG + PEAD STRONG
    [idxAny ? 'MET' : 'ABSENT', psaApproved ? 'MET' : psaAny ? 'PARTIAL' : 'ABSENT',
     (uoaStrong && peadStrong) ? 'MET' : (uoaAny || peadAny) ? 'PARTIAL' : 'ABSENT', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ'],
    // Type 11: Post-Beta
    ['NOT_REQ', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ', 'NOT_REQ'],
  ];

  const TYPES = [
    'IDX Momentum', 'IDX + PSA', 'Triple Confirm', 'Full Conviction',
    'Any Signal', 'Catalyst Only', 'Sector Phase', 'Metals MR',
    'MTFA Confirm', 'Ultimate', 'Post-Beta',
  ];

  const cellIcon = state => {
    if (state === 'MET')     return '<span style="color:var(--green,#22c55e)">&#10003;</span>';
    if (state === 'PARTIAL') return '<span style="color:var(--yellow,#eab308)">&#8764;</span>';
    if (state === 'ABSENT')  return '<span style="color:var(--text3)">&#10007;</span>';
    return '<span style="color:var(--border)">&#8212;</span>';
  };

  const convictionCell = status => {
    if (status === A) return '<span style="font-size:10px;font-weight:700;color:var(--green,#22c55e);letter-spacing:.04em">ACHIEVABLE</span>';
    if (status === P) return '<span style="font-size:10px;font-weight:700;color:var(--yellow,#eab308);letter-spacing:.04em">PARTIAL</span>';
    return '<span style="font-size:10px;color:var(--text3);letter-spacing:.04em">NOT MET</span>';
  };

  const headerCols = ['IDX', 'PSA', 'UOA/PEAD', 'MTFA', 'SRS', 'MMR'];
  const header = `<thead><tr style="border-bottom:1px solid var(--border)">
    <th style="padding:6px 8px;text-align:left;color:var(--text3);font-size:11px;font-weight:600;white-space:nowrap">#</th>
    <th style="padding:6px 8px;text-align:left;color:var(--text3);font-size:11px;font-weight:600;white-space:nowrap">Name</th>
    ${headerCols.map(c => `<th style="padding:6px 8px;text-align:center;color:var(--text3);font-size:11px;font-weight:600">${c}</th>`).join('')}
    <th style="padding:6px 8px;text-align:center;color:var(--text3);font-size:11px;font-weight:600">Conviction</th>
  </tr></thead>`;

  const rows = TYPES.map((name, i) => {
    const status = results[i];
    const cols = typeColStates[i];
    const isPostBeta = i === 10;
    const leftBorder = status === A ? '3px solid var(--green,#22c55e)'
      : status === P ? '3px solid var(--yellow,#eab308)' : '3px solid transparent';
    const opacity = (status === 'NOT_MET') ? 'opacity:0.5;' : '';
    const nameCell = isPostBeta
      ? `${name} <span style="font-size:10px;color:var(--text3)">(Post-Beta)</span>`
      : name;
    return `<tr style="border-left:${leftBorder};${opacity}border-bottom:1px solid var(--border)">
      <td style="padding:6px 8px;font-family:var(--mono);font-size:11px;color:var(--text3);white-space:nowrap">${i + 1}</td>
      <td style="padding:6px 12px 6px 8px;font-size:12px;color:var(--text2);white-space:nowrap">${nameCell}</td>
      ${cols.map(state => `<td style="padding:6px 8px;text-align:center">${cellIcon(state)}</td>`).join('')}
      <td style="padding:6px 8px;text-align:center">${convictionCell(status)}</td>
    </tr>`;
  }).join('');

  el.innerHTML = `${header}<tbody>${rows}</tbody>`;
}

async function openScanSummary() {
  const modal = document.getElementById('scan-summary-modal');
  if (!modal) return;
  modal.style.display = 'flex';
  document.addEventListener('keydown', _scanSummaryEscHandler);

  const statusEl = document.getElementById('scan-summary-status');
  const matrixEl = document.getElementById('scan-summary-matrix');
  if (statusEl) statusEl.innerHTML = '<div style="color:var(--text3);font-size:12px;padding:8px 0">Loading...</div>';
  if (matrixEl) matrixEl.innerHTML = '';

  try {
    const [sigsResp, scanResp] = await Promise.all([
      fetch('/api/v1/signals'),
      fetch('/api/v1/scans/status'),
    ]);
    const sigsData = sigsResp.ok ? await sigsResp.json() : {};
    const scanData = scanResp.ok ? await scanResp.json() : {};
    const today    = new Date().toISOString().slice(0, 10);
    const signals  = (sigsData.signals || []).filter(s => s.scan_ts && s.scan_ts.startsWith(today));
    const scanners = scanData.scanners || [];

    if (statusEl) _renderScanSummaryStatus(statusEl, signals, scanners);
    if (matrixEl) _renderScanSummaryMatrix(matrixEl, signals);
  } catch (e) {
    if (statusEl) statusEl.innerHTML = '<div style="color:var(--red,#ef4444);font-size:12px;padding:8px 0">Failed to load scan data.</div>';
  }
}

function closeScanSummary() {
  const modal = document.getElementById('scan-summary-modal');
  if (modal) modal.style.display = 'none';
  document.removeEventListener('keydown', _scanSummaryEscHandler);
}

function _scanSummaryEscHandler(e) {
  if (e.key === 'Escape') closeScanSummary();
}
