// PRIME v1.0 Portfolio Tab (Sprint 24 Items 2, 3, 5)
// Consolidated holdings across all Schwab accounts, MATA sell, risk warnings.

function _portApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}
function _portToken() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiToken) || '';
}

// ── Sort state ──────────────────────────────────────────────────────────────
let _portRows      = [];
let _portSortKey   = 'market_value';
let _portSortAsc   = false;
let _pendingSell   = null;

// ── Load ────────────────────────────────────────────────────────────────────

async function loadPortfolio() {
  document.getElementById('portfolio-rows').innerHTML =
    '<tr><td colspan="10" style="text-align:center;color:var(--text3);padding:20px">Loading…</td></tr>';
  try {
    const r   = await fetch(_portApi() + '/portfolio');
    const d   = await r.json();
    if (!r.ok) throw new Error(d.error || r.status);
    _portRows = d.rows || [];
    _renderPortfolio(d);
  } catch(e) {
    document.getElementById('portfolio-rows').innerHTML =
      `<tr><td colspan="10" style="color:var(--red);padding:16px">${e.message}</td></tr>`;
  }
  // PM-HEALTH-05: re-evaluate the RED position banner on every load.
  _renderHealthBanner();
}

// PM-HEALTH-05: amber banner above Holdings when any position thesis is RED.
// Links to the Health tab. Degrades silently if the health endpoint errors.
async function _renderHealthBanner() {
  const el = document.getElementById('portfolio-health-banner');
  if (!el) return;
  try {
    const r = await fetch(_portApi() + '/positions/health');
    const d = await r.json();
    if (!r.ok) throw new Error('health unavailable');
    const red = d.red_count || 0;
    if (red > 0) {
      const noun = red === 1 ? 'position' : 'positions';
      el.innerHTML =
        `<div style="background:#451a03;color:#fde68a;border:1px solid #d97706;border-radius:6px;` +
        `padding:10px 14px;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between;gap:12px">` +
        `<span style="cursor:pointer;flex:1" onclick="showView('health')">` +
        `${red} ${noun} showing reversal signals — view Health tab for details →</span>` +
        `<button onclick="_dismissHealthBanner()" title="Dismiss" ` +
        `style="background:none;border:none;color:#fde68a;font-size:16px;cursor:pointer;line-height:1;padding:0 4px">×</button>` +
        `</div>`;
      el.style.display = 'block';
    } else {
      _dismissHealthBanner();
    }
  } catch (e) {
    _dismissHealthBanner();  // silent degrade — no banner on error
  }
}

function _dismissHealthBanner() {
  const el = document.getElementById('portfolio-health-banner');
  if (el) { el.style.display = 'none'; el.innerHTML = ''; }
}

// PORT-01: Refresh button triggers /sync/schwab before reloading portfolio
async function refreshPortfolio() {
  const btn = document.getElementById('portfolio-refresh-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Syncing...'; }
  let syncWarning = null;
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 15000);
    const r = await fetch(_portApi() + '/sync/schwab', { signal: ctrl.signal });
    clearTimeout(timeout);
    const d = await r.json();
    if (d.imported > 0) {
      _showPortToast(d.imported + ' new position(s) imported from Schwab.', 'green');
    } else if (d.errors && d.errors.length) {
      syncWarning = 'Schwab sync completed with warnings — some positions may be stale.';
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      syncWarning = 'Schwab sync timed out — showing cached positions.';
    } else {
      syncWarning = 'Schwab sync failed — showing cached positions.';
    }
  }
  if (syncWarning) _showPortToast(syncWarning, 'amber');
  if (btn) { btn.disabled = false; btn.textContent = 'Refresh'; }
  loadPortfolio();
}

function _showPortToast(msg, color) {
  const existing = document.getElementById('port-toast');
  if (existing) existing.remove();
  const bg = color === 'green' ? '#052e16' : '#451a03';
  const fg = color === 'green' ? '#86efac' : '#fde68a';
  const border = color === 'green' ? '#16a34a' : '#d97706';
  const el = document.createElement('div');
  el.id = 'port-toast';
  el.style.cssText = `position:fixed;top:60px;right:20px;background:${bg};color:${fg};border:1px solid ${border};` +
    'border-radius:6px;padding:10px 16px;font-size:13px;font-family:var(--mono);z-index:500;' +
    'max-width:360px;box-shadow:0 4px 12px rgba(0,0,0,0.4)';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => { if (el.parentNode) el.remove(); }, 5000);
}

function _renderPortfolio(data) {
  _renderSummary(data.summary || {});
  _renderWarnings(data.warnings || []);
  _renderSectorBreakdown(data.summary || {});
  _renderRows(_portRows);
}

function _renderSummary(s) {
  const el = document.getElementById('portfolio-summary');
  if (!el) return;
  const pnlColor = (s.total_unrealized_pnl || 0) >= 0 ? '#22c55e' : '#ef4444';
  const cashVal = s.cash_available != null ? '$' + _fmt(s.cash_available) : '--';
  el.innerHTML =
    _summCard('Total Market Value', '$' + _fmt(s.total_market_value)) +
    _summCard('Unrealized P&L',
      `<span style="color:${pnlColor}">$${_fmt(s.total_unrealized_pnl)}</span>`) +
    _summCard('Cost Basis', '$' + _fmt(s.total_cost_basis)) +
    _summCard('Positions', s.position_count || 0) +
    _summCard('Cash Available', cashVal);
}

function _renderSectorBreakdown(s) {
  const el = document.getElementById('portfolio-sector-breakdown');
  if (!el) return;
  const breakdown = s.sector_breakdown || {};
  const entries = Object.entries(breakdown).filter(([, pct]) => pct > 0)
    .sort((a, b) => b[1] - a[1]);
  if (!entries.length) { el.style.display = 'none'; return; }
  const maxSectorPct = 30;
  el.style.display = 'block';
  el.innerHTML = '<div style="font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:.06em;font-family:var(--mono);margin-bottom:8px">Sector Exposure</div>' +
    entries.map(([sec, pct]) => {
      const over = pct > maxSectorPct;
      const barColor = over ? 'var(--amber)' : 'var(--blue)';
      const label = over ? `<span style="color:var(--amber);font-weight:700">${sec}</span>` : sec;
      return `<div data-tooltip="Sector exposure as % of total portfolio. Amber = above 30% concentration limit." style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
        <div style="width:130px;font-size:12px;color:var(--text2);font-family:var(--mono);text-align:right">${label}</div>
        <div style="flex:1;background:var(--bg4);border-radius:3px;height:10px;overflow:hidden">
          <div style="width:${Math.min(pct, 100)}%;background:${barColor};height:100%;border-radius:3px"></div>
        </div>
        <div style="width:42px;font-size:12px;font-family:var(--mono);color:${over ? 'var(--amber)' : 'var(--text3)'};text-align:right">${pct.toFixed(1)}%</div>
      </div>`;
    }).join('');
}

function _summCard(label, val) {
  return `<div style="background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:10px 16px;min-width:140px">
    <div style="font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;font-family:var(--mono)">${label}</div>
    <div style="font-size:18px;font-weight:700;color:var(--text);margin-top:4px">${val}</div>
  </div>`;
}

function _renderWarnings(warnings) {
  const el = document.getElementById('portfolio-warnings');
  if (!el) return;
  if (!warnings || !warnings.length) { el.style.display = 'none'; return; }
  el.style.display = 'block';
  el.innerHTML = warnings.map(w => {
    if (w.type === 'SECTOR_CONCENTRATION')
      return `${w.sector} sector at ${w.pct}% — above ${w.limit_pct}% limit`;
    if (w.type === 'POSITION_SIZE')
      return `${w.symbol} position at ${w.pct}% of portfolio — above ${w.limit_pct}% limit`;
    return JSON.stringify(w);
  }).join(' &nbsp;|&nbsp; ');
}

function _renderRows(rows) {
  const sorted = [...rows].sort((a, b) => {
    const av = a[_portSortKey] ?? 0;
    const bv = b[_portSortKey] ?? 0;
    if (typeof av === 'string') return _portSortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
    return _portSortAsc ? av - bv : bv - av;
  });

  const tbody = document.getElementById('portfolio-rows');
  if (!sorted.length) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text3);padding:20px">No open positions</td></tr>';
    return;
  }
  tbody.innerHTML = sorted.map(row => {
    const pnlColor = row.unrealized_pnl >= 0 ? '#22c55e' : '#ef4444';
    const pnlPctColor = row.unrealized_pnl_pct >= 0 ? '#22c55e' : '#ef4444';
    const dkStyle = _dkStyle(row.dk_status);
    const accounts = (row.accounts || []).join(' · ') || '--';
    // TT-01: warning triangle carries a concentration-limit tooltip.
    const warnIcon = row.position_warning
      ? ' <span data-tooltip="This position exceeds the 15% concentration limit. Consider trimming or using the Rebalance advisor.">⚠</span>'
      : '';
    return `<tr>
      <td style="font-family:var(--mono);font-weight:700">${row.symbol}${warnIcon}</td>
      <td style="font-family:var(--mono)">${row.total_shares}</td>
      <td style="font-family:var(--mono)">$${_fmt(row.avg_entry_price)}</td>
      <td style="font-family:var(--mono)">$${_fmt(row.current_price)}</td>
      <td style="font-family:var(--mono)">$${_fmt(row.market_value)}</td>
      <td style="font-family:var(--mono);color:${pnlColor}">$${_fmt(row.unrealized_pnl)}</td>
      <td style="font-family:var(--mono);color:${pnlPctColor}">${row.unrealized_pnl_pct.toFixed(2)}%</td>
      <td style="font-size:12px;color:var(--text3)">${accounts}</td>
      <td><span style="${dkStyle}" data-tooltip="CONFIRMING = institutional dark pool buying detected (bullish). NULLIFYING = institutional selling detected (bearish). NEUTRAL = no significant dark pool activity.">${row.dk_status}</span></td>
      <td><button class="btn-sell" style="padding:3px 10px;font-size:12px"
           data-tooltip="Close this position via proportional sell across all accounts. A confirmation dialog will appear before any order is placed."
           onclick='openSellModal(${JSON.stringify(row)})'>Sell</button></td>
    </tr>`;
  }).join('');
}

function _dkStyle(status) {
  if (status === 'CONFIRMING') return 'color:#1f7a1f;font-weight:700;font-family:var(--mono);font-size:12px';
  if (status === 'NULLIFYING') return 'color:#c00000;font-weight:700;font-family:var(--mono);font-size:12px';
  return 'color:#888888;font-family:var(--mono);font-size:12px';
}

function sortPortfolio(key) {
  if (_portSortKey === key) { _portSortAsc = !_portSortAsc; }
  else { _portSortKey = key; _portSortAsc = false; }
  _renderRows(_portRows);
}

function _fmt(v) {
  if (v === null || v === undefined) return '--';
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ── MATA Sell modal ──────────────────────────────────────────────────────────

function openSellModal(row) {
  _pendingSell = row;
  const el = document.getElementById('sell-modal-details');
  if (el) {
    el.innerHTML =
      `<div><b>${row.symbol}</b> — ${row.total_shares} shares total</div>` +
      `<div>Accounts: <b>${(row.accounts || []).join(' · ') || '--'}</b></div>` +
      `<div>Avg Entry: <b>$${_fmt(row.avg_entry_price)}</b> &nbsp; Current: <b>$${_fmt(row.current_price)}</b></div>` +
      `<div>Unrealized P&amp;L: <b style="color:${row.unrealized_pnl >= 0 ? '#22c55e' : '#ef4444'}">$${_fmt(row.unrealized_pnl)}</b></div>`;
  }
  // PM-01: pre-fill qty with full position size for a one-click full close.
  const qtyInput = document.getElementById('sell-qty-input');
  qtyInput.value = row.total_shares;
  // Helper text + "Full Position" reset link, injected below the qty input (idempotent).
  let helper = document.getElementById('sell-qty-helper');
  if (!helper) {
    helper = document.createElement('div');
    helper.id = 'sell-qty-helper';
    helper.style.cssText = 'font-size:11px;color:var(--text3);margin-top:-2px;line-height:1.4';
    qtyInput.insertAdjacentElement('afterend', helper);
  }
  helper.innerHTML =
    `Full position: ${row.total_shares} shares. Edit for partial exit. ` +
    `<a href="#" id="sell-full-pos-link" style="color:var(--blue);text-decoration:none;font-weight:600">Full Position</a>`;
  document.getElementById('sell-full-pos-link').onclick = (e) => {
    e.preventDefault();
    qtyInput.value = _pendingSell ? _pendingSell.total_shares : row.total_shares;
  };
  document.getElementById('sell-order-type').value = 'MARKET';
  document.getElementById('sell-limit-row').style.display = 'none';
  document.getElementById('sell-allocation-preview').textContent = '';
  document.getElementById('sell-msg').textContent = '';
  document.getElementById('sell-modal').classList.add('open');
}

document.addEventListener('DOMContentLoaded', () => {
  const ot = document.getElementById('sell-order-type');
  if (ot) ot.addEventListener('change', () => {
    document.getElementById('sell-limit-row').style.display =
      ot.value === 'LIMIT' ? 'block' : 'none';
  });
});

function closeSellModal() {
  document.getElementById('sell-modal').classList.remove('open');
  _pendingSell = null;
}

async function submitSell() {
  if (!_pendingSell) return;
  const row      = _pendingSell;
  const qtyRaw   = document.getElementById('sell-qty-input').value.trim();
  const orderType = document.getElementById('sell-order-type').value;
  const limitPrice = parseFloat(document.getElementById('sell-limit-price')?.value || '0');
  const price    = orderType === 'LIMIT' && limitPrice > 0 ? limitPrice : (row.current_price || 0);
  const msgEl    = document.getElementById('sell-msg');
  const btn      = document.getElementById('sell-confirm-btn');

  if (!qtyRaw) { msgEl.textContent = 'Enter a quantity'; msgEl.className = 'order-msg err'; return; }

  // PM-01: block share counts above the total position. Percentages (e.g. "50%") are exempt.
  if (!qtyRaw.includes('%')) {
    const qtyNum = parseInt(qtyRaw, 10);
    if (!Number.isNaN(qtyNum) && qtyNum > row.total_shares) {
      msgEl.textContent = 'Quantity exceeds total position size';
      msgEl.className = 'order-msg err';
      return;
    }
  }

  // Build account_holdings from row
  const holdings = (row.accounts || []).map(acc => ({
    account: acc,
    account_hash: '',
    shares: Math.round(row.total_shares / (row.accounts.length || 1)),
  }));

  btn.disabled = true;
  try {
    const resp = await fetch(_portApi() + '/sell/mata', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + _portToken() },
      body: JSON.stringify({
        symbol:           row.symbol,
        total_qty:        qtyRaw,
        order_type:       orderType,
        price:            price,
        account_holdings: holdings,
        confirmed:        true,
      }),
    });
    const d = await resp.json();
    if (!resp.ok) throw new Error(d.error || resp.status);
    msgEl.textContent = `Submitted: ${d.allocated_total} shares across ${(d.orders || []).length} account(s)`;
    msgEl.className = 'order-msg ok';
    setTimeout(() => { closeSellModal(); loadPortfolio(); }, 1500);
  } catch(e) {
    msgEl.textContent = 'Error: ' + e.message;
    msgEl.className = 'order-msg err';
  } finally {
    btn.disabled = false;
  }
}

// ── ML-17 Rebalance ─────────────────────────────────────────────────────────

// PORT-03: Rebalance button calls /advisory/rebalance (ML-17)
async function requestRebalance() {
  const panel = document.getElementById('rebalance-panel');
  if (!panel) return;
  panel.style.display = 'block';
  panel.innerHTML = '<div style="color:var(--text3);font-size:13px">Requesting AI rebalance suggestions…</div>';
  try {
    const r = await fetch(_portApi() + '/advisory/rebalance', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.status);
    const suggestions = d.suggestions || [];
    if (!suggestions.length) {
      panel.innerHTML = '<div style="color:var(--text3);font-size:13px">No rebalance suggestions at this time.</div>';
      return;
    }
    const urgencyColor = u => u === 'HIGH' ? 'var(--red)' : u === 'MEDIUM' ? 'var(--amber)' : 'var(--text3)';
    const actionBadge = a => {
      const c = a === 'TRIM' ? '#92400e' : a === 'EXIT' ? '#7f1d1d' : '#1e3a5f';
      const t = a === 'TRIM' ? '#fde68a' : a === 'EXIT' ? '#fca5a5' : '#93c5fd';
      return `<span style="background:${c};color:${t};padding:2px 7px;border-radius:3px;font-size:11px;font-family:var(--mono);font-weight:700">${a}</span>`;
    };
    let html = '<div style="font-size:12px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;font-family:var(--mono)">AI Rebalance Suggestions</div>';
    html += suggestions.map(s =>
      `<div style="padding:7px 0;border-bottom:1px solid var(--border);font-size:13px;display:flex;gap:8px;align-items:flex-start">
        <span style="font-family:var(--mono);font-weight:700;color:var(--text);min-width:70px">${s.symbol || ''}</span>
        ${actionBadge(s.action || 'HOLD')}
        <span style="color:var(--text2);flex:1">${s.rationale || ''}</span>
        <span style="font-size:11px;font-family:var(--mono);color:${urgencyColor(s.urgency)}">${s.urgency || ''}</span>
      </div>`
    ).join('');
    html += '<div style="color:var(--text3);font-size:11px;margin-top:10px;font-style:italic">These are AI-generated suggestions only. No orders will be placed automatically.</div>';
    if (d._stale) {
      html += `<div style="color:var(--amber);font-size:11px;margin-top:4px;font-family:var(--mono)">[STALE — from ${d._stale_from || 'prior run'}]</div>`;
    }
    if (d._fallback) {
      html += '<div style="color:var(--text3);font-size:11px;margin-top:4px">Deterministic fallback (AI unavailable)</div>';
    }
    panel.innerHTML = html;
  } catch(e) {
    panel.innerHTML = `<div style="color:var(--red);font-size:13px">${e.message}</div>`;
  }
}
