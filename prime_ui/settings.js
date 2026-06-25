// Sprint 23 Item 2: General Settings Tab
// Sprint 25 Item 2: Broker Connection panel
// Sprint 27 Item 4: mode pill + order-mode banner
// Loads from GET /api/v1/settings, saves via POST /api/v1/settings.
// All settings persist to ops_config.json; changes take effect on next scan.

function _settApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}

// Sprint 27 Item 4: update topbar mode pill and order-entry banner.
// CIL-NEW-03: also update the persistent PAPER mode amber banner on all tabs.
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

    // CIL-NEW-03: persistent amber banner visible on all tabs in PAPER mode.
    const paperBanner = document.getElementById('paper-mode-banner');
    if (paperBanner) {
      paperBanner.style.display = isLive ? 'none' : 'flex';
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
        ${_field('max_trades', 'Max Trades', d.max_trades, 'number', null, 'Maximum simultaneous open positions across all strategies')}
        ${_field('mata_profile', 'MATA Profile', d.mata_profile, 'select', ['Joint Brokerage','Custodial','Rollover IRA'], 'Active multi-account profile — sets which accounts receive trade allocations')}
        ${_field('analysis_mode', 'Analysis Mode', d.analysis_mode, 'select', ['Universe','Manual'], 'Universe: scan full S&P 500; Manual: scan specified symbols only')}
        ${_toggleField('use_ai_ranker', 'AI Ranker', d.use_ai_ranker, 'Enable Claude AI for PSA scanner signal scoring and ranking')}
        ${_field('long_stop_loss_pct', 'Long Stop Loss %', _pct(d.long_stop_loss_pct), 'number', null, 'Default stop loss % for LONG positions (e.g. 5 = 5% below entry price)')}
        ${_field('short_stop_loss_pct', 'Short Stop Loss %', _pct(d.short_stop_loss_pct), 'number', null, 'Default stop loss % for SHORT positions (e.g. 5 = 5% above entry price)')}
        ${_field('time_stop_minutes', 'Time Stop (min)', d.time_stop_minutes, 'number', null, 'Auto-close LONG positions after this many minutes (e.g. 1950 = 4 trading days)')}
        ${_field('short_size_multiplier', 'Short Size Multiplier', d.short_size_multiplier, 'number', null, 'Position size multiplier for SHORT trades (e.g. 0.5 = half the size of a LONG)')}
        ${_field('stop_monitor_interval_seconds', 'Stop Monitor Interval (sec)', d.stop_monitor_interval_seconds || 60, 'number', null, 'How often the stop monitor checks positions for stop breaches (seconds)')}
        ${_field('monthly_ai_budget', 'Monthly AI Budget ($)', d.monthly_ai_budget != null ? d.monthly_ai_budget : 10.0, 'number', null, 'Monthly AI API spending limit in USD — dashboard alert fires when exceeded')}
      </div>
      <div style="margin-top:14px">
        <button class="btn-refresh" onclick="openMataEditor()" style="font-size:12px;padding:5px 14px">Edit MATA Distribution</button>
        <span style="font-size:12px;color:var(--text3);margin-left:10px">Set % allocation per account in the active MATA profile</span>
      </div>
    </div>

    <div class="order-panel" style="margin-bottom:20px">
      <div class="panel-title">EXIT MANAGEMENT</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px;margin-top:8px">
        ${_field('exit_gain_trigger_pct', 'Trailing Gain Trigger %', d.exit_gain_trigger_pct != null ? d.exit_gain_trigger_pct : 3.0, 'number', null, 'Arm the trailing stop once a LONG gains this % above entry (e.g. 3 = +3%)')}
        ${_field('exit_trail_pct', 'Trail %', d.exit_trail_pct != null ? d.exit_trail_pct : 1.5, 'number', null, 'Once armed, exit when price falls this % below the rolling peak (e.g. 1.5 = 1.5%)')}
        ${_field('exit_day_count_max', 'Max Days Held', d.exit_day_count_max != null ? d.exit_day_count_max : 3, 'number', null, 'Trigger the day-count exit when a position has been held this many calendar days')}
        ${_field('exit_day_count_action', 'Day-Count Action', d.exit_day_count_action || 'ALERT', 'select', ['ALERT','AUTO_SELL'], 'ALERT: warn on the dashboard; AUTO_SELL: automatically sell at market open on Day N')}
        ${_actionField('position_monitor_action', 'Position Monitor Action', d.position_monitor_action || 'ALERT_ONLY', [['ALERT_ONLY','Alert Only'],['AUTO_SELL','Auto-Sell']], 'Alert Only = RED positions trigger a banner and ops log entry only. Auto-Sell = RED positions trigger an immediate MATA sell across all accounts.')}
      </div>
      <div style="font-size:12px;color:var(--text3);margin-top:8px;font-family:var(--mono)">Automated exits (CIL-097) run inside RTH only. Trailing stop is LONG-only.</div>
    </div>

    <div class="order-panel" style="margin-bottom:20px">
      <div class="panel-title">AI USAGE (THIS MONTH)</div>
      <div id="ai-usage-table"><div class="empty-state" style="padding:10px">Loading…</div></div>
    </div>

    <div class="order-panel" style="margin-bottom:20px">
      <div class="panel-title">POLYGON DATA FEED</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px;margin-top:8px">
        ${_field('polygon_plan', 'Polygon Plan', d.polygon_plan || 'free', 'select', ['free', 'paid'], 'free = 5 req/min (13s delay); paid = unlimited (no enforced delay)')}
        ${_field('polygon_rate_limit_delay_ms', 'Rate Limit Delay (ms)', d.polygon_rate_limit_delay_ms != null ? d.polygon_rate_limit_delay_ms : 13000, 'number', null, 'Delay between Polygon API calls in milliseconds. 13000 = 13s (free tier); 100 = paid tier. Ignored when plan = paid.')}
      </div>
      <div style="font-size:12px;color:var(--text3);margin-top:8px;font-family:var(--mono)">IDX and SHORT scanners use Polygon for daily price bars. Change takes effect on next scan.</div>
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

function _tip(text) {
  if (!text) return '';
  return `<sup title="${text}" style="cursor:help;color:var(--amber);font-size:10px;margin-left:3px;user-select:none">?</sup>`;
}

function _field(id, label, val, type, options, tooltip) {
  const tip = _tip(tooltip);
  if (type === 'select') {
    const opts = (options || []).map(o =>
      `<option value="${o}"${val === o ? ' selected' : ''}>${o}</option>`
    ).join('');
    return `<label style="display:flex;flex-direction:column;gap:4px">
      <span style="font-size:12px;color:var(--text3);font-family:var(--mono)">${label}${tip}</span>
      <select id="sett-${id}" style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:14px">${opts}</select>
    </label>`;
  }
  return `<label style="display:flex;flex-direction:column;gap:4px">
    <span style="font-size:12px;color:var(--text3);font-family:var(--mono)">${label}${tip}</span>
    <input id="sett-${id}" type="${type}" value="${val != null ? val : ''}"
      style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:14px;font-family:var(--mono);width:100%"/>
  </label>`;
}

function _toggleField(id, label, val, tooltip) {
  const tip = _tip(tooltip);
  return `<label style="display:flex;flex-direction:column;gap:4px">
    <span style="font-size:12px;color:var(--text3);font-family:var(--mono)">${label}${tip}</span>
    <select id="sett-${id}" style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:14px">
      <option value="true"${val ? ' selected' : ''}>Enabled</option>
      <option value="false"${!val ? ' selected' : ''}>Disabled</option>
    </select>
  </label>`;
}

// Sprint 32 Thread 2 (PM-HEALTH-04): select with friendly labels but token
// values. options is an array of [value, label] pairs.
function _actionField(id, label, val, options, tooltip) {
  const tip = _tip(tooltip);
  const opts = (options || []).map(([v, lbl]) =>
    `<option value="${v}"${val === v ? ' selected' : ''}>${lbl}</option>`
  ).join('');
  return `<label style="display:flex;flex-direction:column;gap:4px">
    <span style="font-size:12px;color:var(--text3);font-family:var(--mono)">${label}${tip}</span>
    <select id="sett-${id}" style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:14px">${opts}</select>
  </label>`;
}

// ── Sprint 27 Item 5: MATA Profile Distribution Editor ────────────────────────

const _MATA_DEFAULT_ACCOUNTS = [
  { name: 'Joint Brokerage', type: 'BROKERAGE', buying_power: 100000, margin_available: 50000, weight: 60 },
  { name: 'Custodial', type: 'BROKERAGE', buying_power: 40000, margin_available: 0, weight: 30 },
  { name: 'Rollover IRA', type: 'ROLLOVER_IRA', buying_power: 30000, margin_available: 0, weight: 10 },
];

let _mataEditAccounts = [];

function openMataEditor() {
  const existing = (_settingsData.mata_accounts || []);
  _mataEditAccounts = existing.length ? JSON.parse(JSON.stringify(existing))
    : JSON.parse(JSON.stringify(_MATA_DEFAULT_ACCOUNTS));
  _renderMataForm();
  document.getElementById('mata-editor-modal').classList.add('open');
}

function closeMataEditor() {
  document.getElementById('mata-editor-modal').classList.remove('open');
  const msg = document.getElementById('mata-editor-msg');
  if (msg) msg.textContent = '';
}

function _renderMataForm() {
  const form = document.getElementById('mata-accounts-form');
  if (!form) return;
  form.innerHTML = _mataEditAccounts.map((a, i) => `
    <div style="display:grid;grid-template-columns:1fr 100px 80px;gap:8px;align-items:end">
      <label style="display:flex;flex-direction:column;gap:2px">
        <span style="font-size:11px;color:var(--text3);font-family:var(--mono)">Account Name</span>
        <input type="text" value="${a.name}" id="mata-name-${i}"
          style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:5px 8px;border-radius:4px;font-size:13px;font-family:var(--mono);width:100%"/>
      </label>
      <label style="display:flex;flex-direction:column;gap:2px">
        <span style="font-size:11px;color:var(--text3);font-family:var(--mono)">Type</span>
        <select id="mata-type-${i}"
          style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:5px 8px;border-radius:4px;font-size:13px">
          <option value="BROKERAGE"${a.type==='BROKERAGE'?' selected':''}>BROKERAGE</option>
          <option value="ROLLOVER_IRA"${a.type==='ROLLOVER_IRA'?' selected':''}>ROLLOVER_IRA</option>
          <option value="ROTH_IRA"${a.type==='ROTH_IRA'?' selected':''}>ROTH_IRA</option>
        </select>
      </label>
      <label style="display:flex;flex-direction:column;gap:2px">
        <span style="font-size:11px;color:var(--text3);font-family:var(--mono)">Weight %<sup title="Allocation % for this account; all weights must sum to 100" style="cursor:help;color:var(--amber)">?</sup></span>
        <input type="number" min="0" max="100" step="1" value="${a.weight != null ? a.weight : 0}"
          id="mata-weight-${i}" oninput="updateMataSum()"
          style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:5px 8px;border-radius:4px;font-size:13px;font-family:var(--mono);width:100%"/>
      </label>
    </div>
  `).join('');
  updateMataSum();
}

function updateMataSum() {
  let total = 0;
  _mataEditAccounts.forEach((_, i) => {
    const el = document.getElementById('mata-weight-' + i);
    total += el ? (parseFloat(el.value) || 0) : 0;
  });
  const sumEl = document.getElementById('mata-weight-sum');
  if (sumEl) {
    sumEl.textContent = 'Sum: ' + total.toFixed(0) + '%';
    sumEl.style.color = Math.abs(total - 100) < 0.01 ? 'var(--green)' : 'var(--red)';
  }
}

async function saveMataDistribution() {
  // Read form values into _mataEditAccounts
  _mataEditAccounts.forEach((a, i) => {
    const nameEl   = document.getElementById('mata-name-' + i);
    const typeEl   = document.getElementById('mata-type-' + i);
    const weightEl = document.getElementById('mata-weight-' + i);
    if (nameEl)   a.name   = nameEl.value.trim();
    if (typeEl)   a.type   = typeEl.value;
    if (weightEl) a.weight = parseFloat(weightEl.value) || 0;
  });

  const total = _mataEditAccounts.reduce((s, a) => s + (a.weight || 0), 0);
  const msg = document.getElementById('mata-editor-msg');
  if (Math.abs(total - 100) > 0.5) {
    if (msg) { msg.style.color = 'var(--red)'; msg.textContent = 'Weights must sum to 100% (current: ' + total.toFixed(0) + '%)'; }
    return;
  }

  try {
    const resp = await fetch(_settApi() + '/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mata_accounts: _mataEditAccounts }),
    });
    if (resp.ok) {
      _settingsData.mata_accounts = _mataEditAccounts;
      if (msg) { msg.style.color = 'var(--green)'; msg.textContent = 'Saved'; }
      setTimeout(() => closeMataEditor(), 1000);
    } else {
      const d = await resp.json();
      if (msg) { msg.style.color = 'var(--red)'; msg.textContent = d.error || 'Save failed'; }
    }
  } catch (e) {
    if (msg) { msg.style.color = 'var(--red)'; msg.textContent = 'API error: ' + e.message; }
  }
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
  // Sprint 28 Item 4: Polygon rate limiting
  payload.polygon_plan = _v('polygon_plan');
  const polyDelay = _n('polygon_rate_limit_delay_ms');
  if (polyDelay !== null) payload.polygon_rate_limit_delay_ms = polyDelay;
  // Sprint 30 PM-04: exit management (whole-number percents, stored as-is)
  const gainTrig = _n('exit_gain_trigger_pct');
  if (gainTrig !== null) payload.exit_gain_trigger_pct = gainTrig;
  const trailPct = _n('exit_trail_pct');
  if (trailPct !== null) payload.exit_trail_pct = trailPct;
  const dayMax = _n('exit_day_count_max');
  if (dayMax !== null) payload.exit_day_count_max = dayMax;
  payload.exit_day_count_action = _v('exit_day_count_action');
  // Sprint 32 Thread 2 (PM-HEALTH-04): position monitor action (ALERT_ONLY | AUTO_SELL)
  payload.position_monitor_action = _v('position_monitor_action');

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
