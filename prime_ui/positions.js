// Sprint 16 Item 5: Positions tab -- live P&L, stop badges, hold time, Close.
// Reads enriched positions from GET /positions; closes via authenticated
// POST /trades/close (confirmation modal). Auth token via window.PRIME_CONFIG.

function _posApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}
function _posToken() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiToken) || '';
}

// Stop badge color -> existing badge CSS class.
const _STOP_CLASS = { GREEN: 'confirming', AMBER: 'unavailable', RED: 'nullifying' };

function _fmtMoney(v) {
  const n = Number(v || 0);
  const sign = n > 0 ? '+' : '';
  return sign + '$' + n.toFixed(2);
}

async function loadPositions() {
  try {
    const resp = await fetch(_posApi() + '/positions');
    const data = await resp.json();
    const tbody = document.getElementById('pos-body');
    tbody.innerHTML = '';
    const positions = data.positions || [];
    if (!positions.length) {
      tbody.innerHTML = '<tr><td colspan="10" class="empty-state">No open positions</td></tr>';
      return;
    }
    positions.forEach(p => {
      const entry = p.entry_price || p.price_at_scan || 0;
      const price = p.current_price || entry;
      const pnl = Number(p.unrealized_pnl || 0);
      const pnlPct = Number(p.unrealized_pnl_pct || 0);
      const pnlColor = p.pnl_color === 'green' ? 'var(--green)'
        : (p.pnl_color === 'red' ? 'var(--red)' : 'var(--text2)');
      const stopCls = _STOP_CLASS[p.stop_badge] || 'neutral';
      const holdColor = p.time_stop_exceeded ? 'var(--amber)' : 'var(--text2)';
      const logId = p.log_id || '';
      tbody.innerHTML += `<tr>
        <td style="font-weight:600">${p.symbol || '--'}</td>
        <td>${p.strategy || '--'}</td>
        <td style="font-family:var(--mono)">${p.shares || 0}</td>
        <td style="font-family:var(--mono)">$${Number(entry).toFixed(2)}</td>
        <td style="font-family:var(--mono)">$${Number(price).toFixed(2)}</td>
        <td style="font-family:var(--mono);color:${pnlColor}">${_fmtMoney(pnl)} (${pnlPct.toFixed(1)}%)</td>
        <td><span class="badge ${stopCls}">${p.stop_badge || 'GREEN'}</span></td>
        <td style="font-family:var(--mono);color:${holdColor}">${p.hold_time || '--'}</td>
        <td>${p.status || '--'}</td>
        <td><button class="btn-sell" style="padding:4px 12px;font-size:12px"
              onclick="openCloseConfirm('${logId}','${p.symbol || ''}',${Number(p.shares || 0)},${Number(price)})">Close</button></td>
      </tr>`;
    });
  } catch(e) {
    console.error('loadPositions:', e);
    document.getElementById('pos-body').innerHTML =
      '<tr><td colspan="10" class="empty-state">Failed to load positions</td></tr>';
  }
}

// Holds the position pending close between confirm-open and submit.
let _pendingClose = null;

function openCloseConfirm(logId, symbol, shares, price) {
  if (!logId) return;
  _pendingClose = { log_id: logId, symbol, shares, exit_price: price };
  document.getElementById('close-modal-details').innerHTML =
    `<div>Close <b>${shares}</b> ${symbol}</div>` +
    `<div>Exit price: <b>$${Number(price).toFixed(2)}</b></div>` +
    `<div>Mode: <b>PAPER</b></div>`;
  document.getElementById('close-modal').classList.add('open');
}

function closeCloseConfirm() {
  document.getElementById('close-modal').classList.remove('open');
  _pendingClose = null;
}

async function submitClose() {
  if (!_pendingClose) return;
  const c = _pendingClose;
  const btn = document.getElementById('close-confirm-btn');
  btn.disabled = true;
  try {
    const resp = await fetch(_posApi() + '/trades/close', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + _posToken(),
      },
      body: JSON.stringify({
        log_id: c.log_id,
        exit_price: c.exit_price,
        exit_reason: 'MANUAL',
      }),
    });
    await resp.json().catch(() => ({}));
    closeCloseConfirm();
    loadPositions();
  } catch (e) {
    console.error('submitClose:', e);
    btn.disabled = false;
  } finally {
    btn.disabled = false;
  }
}
