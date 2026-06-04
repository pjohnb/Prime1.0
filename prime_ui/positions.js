// Sprint 16 Item 5: Positions tab -- live P&L, stop badges, hold time, Close.
// Sprint 22 Item 2: DK badge per row, P&L color coding, human-readable hold time,
//                   red Close button styling, symbol search/filter.

function _posApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}
function _posToken() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiToken) || '';
}

const _STOP_CLASS = { GREEN: 'confirming', AMBER: 'unavailable', RED: 'nullifying' };

function _fmtMoney(v) {
  const n = Number(v || 0);
  const sign = n > 0 ? '+' : '';
  return sign + '$' + n.toFixed(2);
}

// Sprint 22 Item 2: human-readable hold time (raw minutes -> "2d 3h" or "47m").
function _fmtHold(rawHold) {
  if (!rawHold || rawHold === '--') return rawHold || '--';
  // rawHold may be a pre-formatted string from the API (e.g. "2d 3h") or plain minutes.
  if (typeof rawHold === 'string' && !/^\d+$/.test(rawHold)) return rawHold;
  const mins = parseInt(rawHold, 10);
  if (isNaN(mins)) return '--';
  if (mins < 60) return mins + 'm';
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h < 24) return h + 'h' + (m ? ' ' + m + 'm' : '');
  const d = Math.floor(h / 24);
  const rh = h % 24;
  return d + 'd' + (rh ? ' ' + rh + 'h' : '');
}

// Sprint 22 Item 2: DK badge for positions.
function _dkBadge(dkStatus, dkConviction) {
  const dk = (dkStatus || 'NEUTRAL').toUpperCase();
  const cls = dk === 'CONFIRMING' ? 'confirming' : dk === 'NULLIFYING' ? 'nullifying' : 'neutral';
  const label = dk === 'CONFIRMING' ? 'CONFIRM' : dk === 'NULLIFYING' ? 'NULLIFY' : 'NEUTRAL';
  const title = (dkConviction != null) ? ` title="Conviction: ${Number(dkConviction).toFixed(2)}"` : '';
  return `<span class="badge ${cls}"${title}>${label}</span>`;
}

async function loadPositions() {
  try {
    const resp = await fetch(_posApi() + '/positions');
    const data = await resp.json();
    const tbody = document.getElementById('pos-body');
    tbody.innerHTML = '';
    let positions = data.positions || [];

    // Sprint 22 Item 2: symbol search filter.
    const searchEl = document.getElementById('pos-search');
    const q = searchEl ? searchEl.value.trim().toUpperCase() : '';
    if (q) positions = positions.filter(p =>
      (p.symbol || '').toUpperCase().includes(q) ||
      (p.strategy || '').toUpperCase().includes(q));

    if (!positions.length) {
      tbody.innerHTML = '<tr><td colspan="12" class="empty-state">No open positions</td></tr>';
      return;
    }
    positions.forEach(p => {
      const entry = p.entry_price || p.price_at_scan || 0;
      const price = p.current_price || entry;
      const pnl = Number(p.unrealized_pnl || 0);
      const pnlPct = Number(p.unrealized_pnl_pct || 0);
      // Sprint 22 Item 2: explicit P&L color coding (green positive / red negative).
      const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text2)';
      const stopCls = _STOP_CLASS[p.stop_badge] || 'neutral';
      const holdRaw = p.hold_minutes != null ? p.hold_minutes : p.hold_time;
      const holdFmt = _fmtHold(holdRaw);
      const holdColor = p.time_stop_exceeded ? 'var(--amber)' : 'var(--text2)';
      const logId = p.log_id || '';
      const dir = (p.direction || 'LONG').toUpperCase();
      const dirCls = dir === 'SHORT' ? 'nullifying' : 'confirming';
      const dkBadge = _dkBadge(p.dk_status, p.dk_conviction);
      tbody.innerHTML += `<tr>
        <td style="font-weight:600">${p.symbol || '--'}</td>
        <td><span class="badge ${dirCls}">${dir}</span></td>
        <td>${p.strategy || '--'}</td>
        <td style="font-family:var(--mono)">${p.shares || 0}</td>
        <td style="font-family:var(--mono)">$${Number(entry).toFixed(2)}</td>
        <td style="font-family:var(--mono)">$${Number(price).toFixed(2)}</td>
        <td style="font-family:var(--mono);color:${pnlColor}">${_fmtMoney(pnl)} (${pnlPct.toFixed(1)}%)</td>
        <td><span class="badge ${stopCls}">${p.stop_badge || 'GREEN'}</span></td>
        <td style="font-family:var(--mono);color:${holdColor}">${holdFmt}</td>
        <td>${dkBadge}</td>
        <td>${p.status || '--'}</td>
        <td><button class="btn-close-pos"
              onclick="openCloseConfirm('${logId}','${p.symbol || ''}',${Number(p.shares || 0)},${Number(price)})">Close</button></td>
      </tr>`;
    });
  } catch(e) {
    console.error('loadPositions:', e);
    document.getElementById('pos-body').innerHTML =
      '<tr><td colspan="12" class="empty-state">Failed to load positions</td></tr>';
  }
}

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
