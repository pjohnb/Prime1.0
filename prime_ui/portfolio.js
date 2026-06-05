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
}

function _renderPortfolio(data) {
  _renderSummary(data.summary || {});
  _renderWarnings(data.warnings || []);
  _renderRows(_portRows);
}

function _renderSummary(s) {
  const el = document.getElementById('portfolio-summary');
  if (!el) return;
  const pnlColor = (s.total_unrealized_pnl || 0) >= 0 ? '#22c55e' : '#ef4444';
  el.innerHTML =
    _summCard('Total Market Value', '$' + _fmt(s.total_market_value)) +
    _summCard('Unrealized P&L',
      `<span style="color:${pnlColor}">$${_fmt(s.total_unrealized_pnl)}</span>`) +
    _summCard('Cost Basis', '$' + _fmt(s.total_cost_basis)) +
    _summCard('Positions', s.position_count || 0);
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
    const warnIcon = row.position_warning ? ' ⚠' : '';
    return `<tr>
      <td style="font-family:var(--mono);font-weight:700">${row.symbol}${warnIcon}</td>
      <td style="font-family:var(--mono)">${row.total_shares}</td>
      <td style="font-family:var(--mono)">$${_fmt(row.avg_entry_price)}</td>
      <td style="font-family:var(--mono)">$${_fmt(row.current_price)}</td>
      <td style="font-family:var(--mono)">$${_fmt(row.market_value)}</td>
      <td style="font-family:var(--mono);color:${pnlColor}">$${_fmt(row.unrealized_pnl)}</td>
      <td style="font-family:var(--mono);color:${pnlPctColor}">${row.unrealized_pnl_pct.toFixed(2)}%</td>
      <td style="font-size:12px;color:var(--text3)">${accounts}</td>
      <td><span style="${dkStyle}">${row.dk_status}</span></td>
      <td><button class="btn-sell" style="padding:3px 10px;font-size:12px"
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
  document.getElementById('sell-qty-input').value = '';
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

async function requestRebalance() {
  const panel = document.getElementById('rebalance-panel');
  if (!panel) return;
  panel.style.display = 'block';
  panel.innerHTML = '<div style="color:var(--text3);font-size:13px">Requesting AI rebalance suggestions…</div>';
  try {
    const r = await fetch(_portApi() + '/portfolio/rebalance', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.status);
    const suggestions = d.suggestions || d.rebalance_suggestions || [];
    if (!suggestions.length) {
      panel.innerHTML = '<div style="color:var(--text3);font-size:13px">No rebalance suggestions at this time.</div>';
      return;
    }
    panel.innerHTML = '<div style="font-size:12px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;font-family:var(--mono)">AI Rebalance Suggestions (Advisory Only)</div>' +
      suggestions.map(s => `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:13px">
        <span style="font-family:var(--mono);font-weight:700;color:var(--amber)">${s.symbol || s.action || ''}</span>
        <span style="color:var(--text2);margin-left:8px">${s.action || s.recommendation || ''}</span>
        <span style="color:var(--text3);font-size:12px;margin-left:8px">${s.reason || ''}</span>
      </div>`).join('');
    if (d._fallback) {
      panel.innerHTML += '<div style="color:var(--text3);font-size:11px;margin-top:8px">Deterministic fallback (AI unavailable)</div>';
    }
  } catch(e) {
    panel.innerHTML = `<div style="color:var(--red);font-size:13px">${e.message}</div>`;
  }
}
