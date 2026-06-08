// Sprint 23 Item 2: General Settings Tab
// Sprint 25 Item 2: Broker Connection panel
// Sprint 27 Item 4: mode pill + order-mode banner
// Loads from GET /api/v1/settings, saves via POST /api/v1/settings.
// All settings persist to ops_config.json; changes take effect on next scan.

function _settApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}

// Sprint 27 Item 4: update topbar mode pill and order-entry banner.
async function updateModePill() {
  try {
    const resp = await fetch(_settApi() + '/schwab/status');
    if (!resp.ok) return;
    const s = await resp.json();
    const mode = (s.mode || 'PAPER').toUpperCase();
    const isLive = mode === 'LIVE';
    window._serverIsLive = isLive;

    const pill = document.getElementById('mode-pill');
    if (pill) {
      pill.textContent = mode;
      pill.className = 'mode-pill ' + (isLive ? 'live' : 'paper');
    }

    const banner = document.getElementById('order-mode-banner');
    if (banner) {
      banner.className = 'order-mode-banner ' + (isLive ? 'live' : 'paper');
      banner.textContent = isLive
        ? 'LIVE MODE — orders route to your real Schwab account. Real money at risk.'
        : 'PAPER MODE — trades are simulated, no real money at risk';
    }
  } catch (e) { /* API offline — keep defaults */ }
}

// ── Broker Connection panel (Item 2) ─────────────────────────────────────────

let _schwabStatus = {};

async function loadSchwabStatus() {
  try {
    const resp = await fetch(_settApi() + '/schwab/status');
    _schwabStatus = await resp.json();
    _renderSchwabPanel(_schwabStatus);
  } catch (e) {
    const el = document.getElementById('schwab-panel');
    if (el) el.innerHTML = '<div class="empty-state" style="padding:10px">Schwab status unavailable — API offline?</div>';
  }
}

function _renderSchwabPanel(s) {
  const el = document.getElementById('schwab-panel');
  if (!el) return;
  const connColor = s.connected ? 'var(--green)' : 'var(--red)';
  const connLabel = s.connected ? 'Connected' : 'Disconnected';
  const modeColor = s.mode === 'LIVE' ? 'var(--red)' : 'var(--amber)';
  const tokenWarn = s.token_warning
    ? `<span style="color:var(--red);font-size:12px;margin-left:8px">Token > 23h old — run schwab_auth_v2.py</span>` : '';
  const accts = (s.accounts || []).map(a => '...' + a.suffix).join(', ') || '--';

  el.innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-bottom:14px">
      <div>
        <div style="font-size:11px;color:var(--text3);font-family:var(--mono);margin-bottom:4px">STATUS</div>
        <div style="font-weight:700;color:${connColor};font-family:var(--mono)">${connLabel}</div>
        <div style="font-size:12px;color:var(--text3);margin-top:2px">Accounts: ${accts}</div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text3);font-family:var(--mono);margin-bottom:4px">MODE</div>
        <div style="font-weight:700;color:${modeColor};font-family:var(--mono)">${s.mode || 'PAPER'}</div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text3);font-family:var(--mono);margin-bottom:4px">TOKEN AGE</div>
        <div style="font-family:var(--mono);font-size:14px">${s.token_age_hours != null ? s.token_age_hours + 'h' : '--'}${tokenWarn}</div>
      </div>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:10px">
      <button class="btn-confirm" onclick="connectSchwab()" style="font-size:12px;padding:5px 14px">Connect</button>
      <button class="btn-refresh" onclick="refreshSchwabBalances()" style="font-size:12px;padding:5px 14px">Refresh Balances</button>
      <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text3);font-weight:600">
        Trading Mode:
        <select id="schwab-mode-sel"
          style="background:${s.mode==='LIVE'?'#450a0a':'#052e16'};
                 border:2px solid ${s.mode==='LIVE'?'#b91c1c':'#16a34a'};
                 color:${s.mode==='LIVE'?'#fca5a5':'#86efac'};
                 padding:5px 12px;border-radius:6px;font-size:14px;font-weight:700;font-family:var(--mono)"
          onchange="onModeChange(this.value)">
          <option value="PAPER"${(s.mode||'PAPER')==='PAPER'?' selected':''}>PAPER</option>
          <option value="LIVE"${s.mode==='LIVE'?' selected':''}>LIVE</option>
        </select>
        <span style="font-size:11px;font-family:var(--mono);color:${s.mode==='LIVE'?'var(--red)':'var(--text3)'}">
          ${s.mode==='LIVE'?'LIVE — real money at risk':'safe — simulated trades'}
        </span>
      </label>
      <span id="schwab-conn-msg" style="font-family:var(--mono);font-size:12px;min-height:14px"></span>
    </div>
    <div id="schwab-balances" style="font-size:13px;color:var(--text3)"></div>`;
}

async function connectSchwab() {
  const msg = document.getElementById('schwab-conn-msg');
  if (msg) { msg.style.color = 'var(--amber)'; msg.textContent = 'Connecting…'; }
  try {
    const resp = await fetch(_settApi() + '/schwab/connect', { method: 'POST' });
    const data = await resp.json();
    if (data.connected) {
      if (msg) { msg.style.color = 'var(--green)'; msg.textContent = 'Connected'; }
    } else if (data.auth_required) {
      if (msg) { msg.style.color = 'var(--red)'; msg.textContent = 'Token expired — run: ' + (data.auth_command || 'schwab_auth_v2.py'); }
    } else {
      if (msg) { msg.style.color = 'var(--red)'; msg.textContent = data.error || 'Connection failed'; }
    }
    loadSchwabStatus();
  } catch (e) {
    if (msg) { msg.style.color = 'var(--red)'; msg.textContent = 'API offline'; }
  }
}

async function refreshSchwabBalances() {
  const el = document.getElementById('schwab-balances');
  if (el) el.textContent = 'Loading…';
  try {
    const resp = await fetch(_settApi() + '/schwab/balances');
    const data = await resp.json();
    if (!el) return;
    if (!data.balances || !data.balances.length) {
      el.textContent = data.error || 'No balance data';
      return;
    }
    el.innerHTML = '<div style="font-size:11px;color:var(--text3);font-family:var(--mono);margin-bottom:6px">ACCOUNT BALANCES</div>' +
      data.balances.map(b =>
        `<div style="margin-bottom:4px">…${b.suffix}: ${b.buying_power != null ? '$' + Number(b.buying_power).toLocaleString(undefined,{maximumFractionDigits:0}) + ' buying power' : 'n/a'}</div>`
      ).join('');
  } catch (e) {
    if (el) el.textContent = 'Failed to load balances';
  }
}

function onModeChange(newMode) {
  if (newMode === 'LIVE') {
    document.getElementById('live-mode-modal').classList.add('open');
  } else {
    _applyMode('PAPER');
  }
}

async function confirmLiveMode() {
  document.getElementById('live-mode-modal').classList.remove('open');
  await _applyMode('LIVE');
}

function cancelLiveMode() {
  document.getElementById('live-mode-modal').classList.remove('open');
  const sel = document.getElementById('schwab-mode-sel');
  if (sel) sel.value = 'PAPER';
}

async function _applyMode(mode) {
  const msg = document.getElementById('schwab-conn-msg');
  try {
    const resp = await fetch(_settApi() + '/schwab/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, confirmed: true }),
    });
    const data = await resp.json();
    if (resp.ok) {
      if (msg) { msg.style.color = 'var(--green)'; msg.textContent = 'Mode set to ' + mode; }
      setTimeout(() => { if (msg) msg.textContent = ''; }, 2000);
      loadSchwabStatus();
      updateModePill();
    } else {
      if (msg) { msg.style.color = 'var(--red)'; msg.textContent = data.error || 'Mode change failed'; }
    }
  } catch (e) {
    if (msg) { msg.style.color = 'var(--red)'; msg.textContent = 'API offline'; }
  }
}

let _settingsData = {};

const _STRATEGY_LABELS = {
  PSA:   { label: 'PSA — Prime Segment Analysis',   fields: [['momentum_pct','Momentum %','number'],['volume_pct','Volume %','number'],['volatility_pct','Volatility %','number'],['max_drawdown_pct','Max Drawdown %','number']] },
  UOA:   { label: 'UOA — Unusual Options Activity', fields: [['sizzle_index_min','Sizzle Index Min','number'],['put_call_ratio_max','Put/Call Ratio Max','number'],['min_premium','Min Premium ($)','number'],['max_dte','Max DTE','number']] },
  PEAD:  { label: 'PEAD — Post-Earnings Drift',     fields: [['earnings_window_days','Earnings Window (days)','number'],['beat_pct_min','Beat % Min','number'],['miss_pct_max','Miss % Max','number'],['drift_days','Drift Window (days)','number']] },
  DK:    { label: 'DK — Dark Pool',                 fields: [['volume_ratio_min','Volume Ratio Min','number'],['price_proximity_pct','Price Proximity %','number'],['conviction_min','Conviction Min','number']] },
  SHORT: { label: 'SHORT — Short Selling',          fields: [['borrow_rate_max_pct','Borrow Rate Max %','number'],['put_volume_surge','Put Volume Surge x','number']] },
  IDX:   { label: 'IDX — Index Trader',             fields: [['rs_vs_spy_min','RS vs SPY Min','number'],['sma_short','SMA Short Period','number'],['sma_long','SMA Long Period','number']] },
};

async function loadAiUsageTable() {
  const el = document.getElementById('ai-usage-table');
  if (!el) return;
  try {
    const resp = await fetch(_settApi() + '/ai/usage');
    const data = await resp.json();
    const rows = data.by_feature || [];
    if (!rows.length) {
      el.innerHTML = '<div class="empty-state" style="padding:10px">No AI calls recorded this month.</div>';
      return;
    }
    const totalCost = rows.reduce((s, r) => s + (r.cost_usd || 0), 0);
    const rowsHtml = rows.map(r => `<tr>
      <td>${r.feature}</td>
      <td style="font-family:var(--mono);text-align:right">${r.calls}</td>
      <td style="font-family:var(--mono);text-align:right">${(r.input_tokens||0).toLocaleString()}</td>
      <td style="font-family:var(--mono);text-align:right">${(r.output_tokens||0).toLocaleString()}</td>
      <td style="font-family:var(--mono);text-align:right">$${(r.cost_usd||0).toFixed(4)}</td>
    </tr>`).join('');
    el.innerHTML = `
      <table style="width:100%;font-size:13px">
        <thead><tr>
          <th style="text-align:left">Feature</th>
          <th style="text-align:right">Calls</th>
          <th style="text-align:right">Input Tokens</th>
          <th style="text-align:right">Output Tokens</th>
          <th style="text-align:right">Cost USD</th>
        </tr></thead>
        <tbody>${rowsHtml}
          <tr style="border-top:2px solid var(--border);font-weight:700">
            <td>TOTAL</td><td></td><td></td><td></td>
            <td style="font-family:var(--mono);text-align:right">$${totalCost.toFixed(4)}</td>
          </tr>
        </tbody>
      </table>
      <div style="margin-top:8px;text-align:right">
        <button class="btn-refresh" onclick="exportAiUsageCsv()" style="font-size:12px;padding:4px 10px">Export CSV</button>
      </div>`;
  } catch (e) {
    if (el) el.innerHTML = '<div class="empty-state">AI usage unavailable</div>';
  }
}

async function exportAiUsageCsv() {
  try {
    const resp = await fetch(_settApi() + '/ai/usage');
    const data = await resp.json();
    const rows = data.by_feature || [];
    const header = 'Feature,Calls,Input Tokens,Output Tokens,Cost USD\n';
    const body = rows.map(r =>
      `${r.feature},${r.calls},${r.input_tokens||0},${r.output_tokens||0},${(r.cost_usd||0).toFixed(6)}`
    ).join('\n');
    const blob = new Blob([header + body], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'prime_ai_usage.csv'; a.click();
    URL.revokeObjectURL(url);
  } catch (e) { console.error('exportAiUsageCsv:', e); }
}

async function loadSettings() {
  try {
    const resp = await fetch(_settApi() + '/settings');
    _settingsData = await resp.json();
    _renderSettings();
    loadSchwabStatus();
    loadAiUsageTable();
  } catch (e) {
    console.error('loadSettings:', e);
    document.getElementById('settings-body').innerHTML =
      '<div class="empty-state">Failed to load settings — API offline?</div>';
  }
}

function _renderSettings() {
  const d = _settingsData;
  const body = document.getElementById('settings-body');
  if (!body) return;

  const thresholds = d.strategy_thresholds || {};

  body.innerHTML = `
    <div class="order-panel" style="margin-bottom:20px">
      <div class="panel-title">BROKER CONNECTION</div>
      <div id="schwab-panel"><div class="empty-state" style="padding:10px">Loading…</div></div>
    </div>

    <div class="order-panel" style="margin-bottom:20px">
      <div class="panel-title">GLOBAL SETTINGS</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px;margin-top:8px">
        ${_field('max_trades', 'Max Trades', d.max_trades, 'number')}
        ${_field('mata_profile', 'MATA Profile', d.mata_profile, 'select', ['Joint Brokerage','Custodial','Rollover IRA'])}
        ${_field('analysis_mode', 'Analysis Mode', d.analysis_mode, 'select', ['Universe','Manual'])}
        ${_toggleField('use_ai_ranker', 'AI Ranker', d.use_ai_ranker)}
        ${_field('long_stop_loss_pct', 'Long Stop Loss %', _pct(d.long_stop_loss_pct), 'number')}
        ${_field('short_stop_loss_pct', 'Short Stop Loss %', _pct(d.short_stop_loss_pct), 'number')}
        ${_field('time_stop_minutes', 'Time Stop (min)', d.time_stop_minutes, 'number')}
        ${_field('short_size_multiplier', 'Short Size Multiplier', d.short_size_multiplier, 'number')}
        ${_field('stop_monitor_interval_seconds', 'Stop Monitor Interval (sec)', d.stop_monitor_interval_seconds || 60, 'number')}
        ${_field('monthly_ai_budget', 'Monthly AI Budget ($)', d.monthly_ai_budget != null ? d.monthly_ai_budget : 10.0, 'number')}
      </div>
    </div>

    <div class="order-panel" style="margin-bottom:20px">
      <div class="panel-title">AI USAGE (THIS MONTH)</div>
      <div id="ai-usage-table"><div class="empty-state" style="padding:10px">Loading…</div></div>
    </div>

    <div class="panel-title" style="margin-bottom:10px">STRATEGY THRESHOLDS</div>
    ${Object.entries(_STRATEGY_LABELS).map(([key, meta]) => _stratCard(key, meta, thresholds[key] || {})).join('')}

    <div style="display:flex;gap:12px;margin-top:20px;align-items:center">
      <button class="btn-confirm" onclick="saveSettings()">Save</button>
      <button class="btn-cancel" onclick="resetSettings()">Reset to Defaults</button>
      <span id="settings-msg" style="font-family:var(--mono);font-size:13px;min-height:16px"></span>
    </div>`;
}

function _pct(v) {
  if (v == null) return '';
  // Display as percentage integer (0.05 -> 5)
  return Math.round(Number(v) * 100);
}

function _field(id, label, val, type, options) {
  if (type === 'select') {
    const opts = (options || []).map(o =>
      `<option value="${o}"${val === o ? ' selected' : ''}>${o}</option>`
    ).join('');
    return `<label style="display:flex;flex-direction:column;gap:4px">
      <span style="font-size:12px;color:var(--text3);font-family:var(--mono)">${label}</span>
      <select id="sett-${id}" style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:14px">${opts}</select>
    </label>`;
  }
  return `<label style="display:flex;flex-direction:column;gap:4px">
    <span style="font-size:12px;color:var(--text3);font-family:var(--mono)">${label}</span>
    <input id="sett-${id}" type="${type}" value="${val != null ? val : ''}"
      style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:14px;font-family:var(--mono);width:100%"/>
  </label>`;
}

function _toggleField(id, label, val) {
  return `<label style="display:flex;flex-direction:column;gap:4px">
    <span style="font-size:12px;color:var(--text3);font-family:var(--mono)">${label}</span>
    <select id="sett-${id}" style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:14px">
      <option value="true"${val ? ' selected' : ''}>Enabled</option>
      <option value="false"${!val ? ' selected' : ''}>Disabled</option>
    </select>
  </label>`;
}

function _stratCard(stratKey, meta, vals) {
  const rows = meta.fields.map(([fk, fl]) =>
    `<label style="display:flex;flex-direction:column;gap:4px;min-width:160px">
      <span style="font-size:11px;color:var(--text3);font-family:var(--mono)">${fl}</span>
      <input id="sett-strat-${stratKey}-${fk}" type="number" value="${vals[fk] != null ? vals[fk] : ''}"
        style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:5px 8px;border-radius:4px;font-size:13px;font-family:var(--mono);width:110px"/>
    </label>`
  ).join('');
  return `<div class="order-panel" style="margin-bottom:8px">
    <div style="display:flex;align-items:center;gap:10px;cursor:pointer;user-select:none"
         onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'flex':'none'">
      <span class="panel-title" style="margin:0">${meta.label}</span>
      <span style="font-size:11px;color:var(--text3)">▼</span>
    </div>
    <div style="display:none;flex-wrap:wrap;gap:12px;margin-top:12px">${rows}</div>
  </div>`;
}

async function saveSettings() {
  const payload = {};

  const _v = id => {
    const el = document.getElementById('sett-' + id);
    return el ? el.value : null;
  };
  const _n = id => {
    const v = _v(id);
    return v !== null && v !== '' ? Number(v) : null;
  };

  payload.max_trades = _n('max_trades');
  payload.mata_profile = _v('mata_profile');
  payload.analysis_mode = _v('analysis_mode');
  payload.use_ai_ranker = _v('use_ai_ranker') === 'true';
  // Stop loss stored as decimal (5 -> 0.05)
  const longStop = _n('long_stop_loss_pct');
  if (longStop !== null) payload.long_stop_loss_pct = longStop / 100;
  const shortStop = _n('short_stop_loss_pct');
  if (shortStop !== null) payload.short_stop_loss_pct = shortStop / 100;
  payload.time_stop_minutes = _n('time_stop_minutes');
  payload.short_size_multiplier = _n('short_size_multiplier');
  payload.stop_monitor_interval_seconds = _n('stop_monitor_interval_seconds');
  payload.monthly_ai_budget = _n('monthly_ai_budget');

  // Strategy thresholds
  const thresholds = {};
  for (const [stratKey, meta] of Object.entries(_STRATEGY_LABELS)) {
    thresholds[stratKey] = {};
    for (const [fk] of meta.fields) {
      const el = document.getElementById(`sett-strat-${stratKey}-${fk}`);
      if (el && el.value !== '') thresholds[stratKey][fk] = Number(el.value);
    }
  }
  payload.strategy_thresholds = thresholds;

  // Remove null values
  Object.keys(payload).forEach(k => { if (payload[k] === null) delete payload[k]; });

  const msgEl = document.getElementById('settings-msg');
  try {
    const resp = await fetch(_settApi() + '/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await resp.json();
    if (resp.ok) {
      _settingsData = result;
      msgEl.style.color = 'var(--green)';
      msgEl.textContent = 'Saved';
      setTimeout(() => { msgEl.textContent = ''; }, 2000);
    } else {
      msgEl.style.color = 'var(--red)';
      msgEl.textContent = result.error || 'Save failed';
    }
  } catch (e) {
    console.error('saveSettings:', e);
    msgEl.style.color = 'var(--red)';
    msgEl.textContent = 'Save failed — API offline?';
  }
}

function resetSettings() {
  if (!confirm('Reset all settings to defaults? This cannot be undone.')) return;
  // Re-render with empty data to trigger defaults on next load
  loadSettings();
}
