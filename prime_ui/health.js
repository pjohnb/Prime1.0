// PRIME v1.0 Health Tab (Sprint 32 Thread 2, PM-HEALTH-04)
// Position thesis-status dashboard backed by GET /api/v1/positions/health.
// The endpoint contract is owned by Thread 1; this UI is built against it and
// degrades gracefully when it is unavailable.

function _healthApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}

// Auto-refresh handle (PM-HEALTH-04). One interval at a time; cleared on tab hide.
let _healthRefreshTimer = null;

// ── Load ─────────────────────────────────────────────────────────────────────

async function loadHealth() {
  const body = document.getElementById('health-rows');
  try {
    const r = await fetch(_healthApi() + '/positions/health');
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.status);
    _renderHealthSummary(d);
    _renderHealthRows(d.positions || []);
    _updateTabBadge(d.red_count || 0);
  } catch (e) {
    const summary = document.getElementById('health-summary');
    if (summary) summary.innerHTML = '';
    if (body) {
      body.innerHTML =
        `<tr><td colspan="11" style="color:var(--red);padding:16px;text-align:center">` +
        `Failed to load position health — ${e.message}</td></tr>`;
    }
  }
}

// ── Render: summary badges ───────────────────────────────────────────────────

function _renderHealthSummary(data) {
  const el = document.getElementById('health-summary');
  if (!el) return;
  const positions = data.positions || [];
  const red   = data.red_count   != null ? data.red_count   : 0;
  const amber = data.amber_count != null ? data.amber_count : 0;
  const green = data.green_count != null
    ? data.green_count
    : Math.max(positions.length - red - amber, 0);
  el.innerHTML =
    `${_thesisBadge('RED')} <b style="font-family:var(--mono)">${red}</b>` +
    `<span style="color:var(--text3);margin:0 10px">·</span>` +
    `${_thesisBadge('AMBER')} <b style="font-family:var(--mono)">${amber}</b>` +
    `<span style="color:var(--text3);margin:0 10px">·</span>` +
    `${_thesisBadge('GREEN')} <b style="font-family:var(--mono)">${green}</b>`;
}

// ── Render: thesis status badge ──────────────────────────────────────────────
// Colors match the existing toast / order-mode-banner palette in index.html.

function _thesisBadge(status) {
  const palette = {
    GREEN:   { bg: '#052e16', fg: '#86efac' },
    AMBER:   { bg: '#451a03', fg: '#fde68a' },
    RED:     { bg: '#7f1d1d', fg: '#fca5a5' },
    UNKNOWN: { bg: '#1a1a2e', fg: '#888888' },
  };
  // Any value outside the four known states displays as UNKNOWN (grey).
  const s = (status || 'UNKNOWN').toUpperCase();
  const label = palette[s] ? s : 'UNKNOWN';
  const c = palette[label];
  return `<span class="thesis-badge" style="background:${c.bg};color:${c.fg};` +
    `font-family:var(--mono);font-size:12px;font-weight:700;padding:3px 8px;` +
    `border-radius:3px;min-width:64px;display:inline-block;text-align:center">${label}</span>`;
}

// ── Render: position rows ────────────────────────────────────────────────────

function _renderHealthRows(positions) {
  const body = document.getElementById('health-rows');
  if (!body) return;
  if (!positions.length) {
    body.innerHTML =
      `<tr><td colspan="11" class="empty-state" style="color:var(--text3);text-align:center;padding:24px">` +
      `No open positions to monitor.</td></tr>`;
    return;
  }
  body.innerHTML = positions.map(p => {
    const dir   = (p.direction || 'LONG').toUpperCase();
    const dirCls = dir === 'SHORT' ? 'nullifying' : 'confirming';
    const entry = p.entry_price != null ? `$${Number(p.entry_price).toFixed(2)}` : '--';
    const cur   = p.current_price != null ? `$${Number(p.current_price).toFixed(2)}` : '--';
    const pnlPct = p.pnl_pct != null ? Number(p.pnl_pct) : null;
    const pnlColor = pnlPct == null ? 'var(--text2)'
      : pnlPct > 0 ? 'var(--green)' : pnlPct < 0 ? 'var(--red)' : 'var(--text2)';
    const pnlStr = pnlPct == null ? '--' : `${pnlPct.toFixed(1)}%`;
    const days  = p.days_held != null ? p.days_held
      : (p.hold_minutes != null ? Math.floor(Number(p.hold_minutes) / 1440) : '--');
    // _dkBadge is a shared global defined in positions.js.
    const dkBadge = (typeof _dkBadge === 'function')
      ? _dkBadge(p.dk_status, p.dk_conviction)
      : (p.dk_status || '--');
    const evaluated = p.evaluated_at ? formatET(p.evaluated_at, true) : '--';
    return `<tr>
      <td style="font-weight:600">${p.symbol || '--'}</td>
      <td><span class="badge ${dirCls}">${dir}</span></td>
      <td style="font-family:var(--mono)">${entry}</td>
      <td style="font-family:var(--mono)">${cur}</td>
      <td style="font-family:var(--mono);color:${pnlColor}">${pnlStr}</td>
      <td style="font-family:var(--mono)">${days}</td>
      <td>${p.scanner || '--'}</td>
      <td>${dkBadge}</td>
      <td style="font-size:12px;color:var(--text2)">${p.latest_signal || '--'}</td>
      <td>${_thesisBadge(p.thesis_status)}</td>
      <td style="font-family:var(--mono);font-size:12px;color:var(--text3)">${evaluated}</td>
    </tr>`;
  }).join('');
}

// ── Tab RED badge ────────────────────────────────────────────────────────────
// Appends a red "*N" indicator to the Health nav button when red_count > 0.

function _updateTabBadge(redCount) {
  const btn = document.getElementById('health-tab-btn');
  if (!btn) return;
  if (redCount > 0) {
    btn.innerHTML = `Health <span class="health-red-badge" style="color:#fca5a5;font-weight:700">*${redCount}</span>`;
  } else {
    btn.innerHTML = 'Health';
  }
}

// ── Auto-refresh (RTH-only, every 5 min) ─────────────────────────────────────

// Regular Trading Hours check: 09:30–16:00 ET, Mon–Fri. Uses the same ET
// conversion trick as dashboard.js:updateMarketStatus() (DST-safe via Intl).
function _isRTH() {
  const et = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const day = et.getDay();              // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;
  const mins = et.getHours() * 60 + et.getMinutes();
  return mins >= (9 * 60 + 30) && mins <= (16 * 60);
}

function _startHealthAutoRefresh() {
  _stopHealthAutoRefresh();
  _healthRefreshTimer = setInterval(() => {
    if (_isRTH()) loadHealth();
  }, 300000);  // 5 minutes
}

function _stopHealthAutoRefresh() {
  if (_healthRefreshTimer !== null) {
    clearInterval(_healthRefreshTimer);
    _healthRefreshTimer = null;
  }
}

// Node export shim so the logic can be unit-tested without a browser/bundler.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    loadHealth, _renderHealthSummary, _renderHealthRows, _thesisBadge,
    _updateTabBadge, _isRTH, _startHealthAutoRefresh, _stopHealthAutoRefresh,
  };
}
