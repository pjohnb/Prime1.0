// Sprint 23 Item 2: General Settings Tab
// Loads from GET /api/v1/settings, saves via POST /api/v1/settings.
// All settings persist to ops_config.json; changes take effect on next scan.

function _settApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
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

async function loadSettings() {
  try {
    const resp = await fetch(_settApi() + '/settings');
    _settingsData = await resp.json();
    _renderSettings();
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
      </div>
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
