// Sprint 16 Item 5: Positions tab -- live P&L, stop badges, hold time, Close.
// Sprint 22 Item 2: DK badge per row, P&L color coding, human-readable hold time,
//                   red Close button styling, symbol search/filter.
// Sprint 23 Item 1: Sync Schwab button.
// Sprint 23 Item 3: Tooltips on Hold, Stop, DK columns.
// Sprint 23 Item 4: Delete button for manual PAPER trades.

function _posApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}
function _posToken() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiToken) || '';
}

const _STOP_CLASS = { GREEN: 'confirming', AMBER: 'unavailable', RED: 'nullifying' };

// Sprint 26 Item 3: live price cache keyed by symbol.
let _livePrices = {};
let _priceTs = null;
let _pricePollTimer = null;

async function refreshPrices() {
  try {
    const resp = await fetch(_posApi() + '/positions/prices');
    if (!resp.ok) return;
    const data = await resp.json();
    _livePrices = data.prices || {};
    _priceTs = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    const tsEl = document.getElementById('prices-updated-ts');
    if (tsEl) tsEl.textContent = 'Prices updated: ' + _priceTs;
    loadPositions();
  } catch (e) {
    console.debug('refreshPrices:', e);
  }
}

function startPricePoll() {
  if (_pricePollTimer) return;
  refreshPrices();
  _pricePollTimer = setInterval(refreshPrices, 60000);
}

function _fmtMoney(v) {
  const n = Number(v || 0);
  const sign = n > 0 ? '+' : '';
  return sign + '$' + n.toFixed(2);
}

// Sprint 22 Item 2: human-readable hold time (raw minutes -> "2d 3h" or "47m").
function _fmtHold(rawHold) {
  if (!rawHold || rawHold === '--') return rawHold || '--';
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

// Sprint 22 Item 2 / Sprint 23 Item 3: DK badge with tooltip.
function _dkBadge(dkStatus, dkConviction) {
  const dk = (dkStatus || 'NEUTRAL').toUpperCase();
  const cls = dk === 'CONFIRMING' ? 'confirming' : dk === 'NULLIFYING' ? 'nullifying' : 'neutral';
  const label = dk === 'CONFIRMING' ? 'CONFIRM' : dk === 'NULLIFYING' ? 'NULLIFY' : 'NEUTRAL';
  const convStr = (dkConviction != null) ? `  Conviction: ${Number(dkConviction).toFixed(2)}` : '';
  const tooltip = dk === 'CONFIRMING'
    ? `CONFIRMING: Institutional dark-pool buying aligns with position direction.${convStr}`
    : dk === 'NULLIFYING'
    ? `NULLIFYING: Institutional selling opposes position direction — tighten stop.${convStr}`
    : 'NEUTRAL: No significant dark-pool activity. Manage by standard rules.';
  return `<span class="badge ${cls}" title="${tooltip}">${label}</span>`;
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
      // Sprint 26 Item 3: use live price from polling cache when available.
      const livePrice = _livePrices[(p.symbol || '').toUpperCase()];
      const price = livePrice || p.current_price || entry;
      // Recalculate P&L from live price.
      const dir = (p.direction || 'LONG').toUpperCase();
      const shares = Number(p.shares || 0);
      const pnlRaw = dir === 'SHORT'
        ? (Number(entry) - price) * shares
        : (price - Number(entry)) * shares;
      const pnl = livePrice ? pnlRaw : Number(p.unrealized_pnl || 0);
      const pnlPct = (entry > 0 && shares > 0) && livePrice
        ? pnl / (Number(entry) * shares) * 100
        : Number(p.unrealized_pnl_pct || 0);
      const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text2)';
      const stopBadge = p.stop_badge || 'GREEN';
      const stopCls = _STOP_CLASS[stopBadge] || 'neutral';
      // Sprint 26 Item 2: show stop price value below badge.
      const stopPrice = p.stop_price ? `$${Number(p.stop_price).toFixed(2)}` : '';
      const targetPrice = p.target_price ? ` T:$${Number(p.target_price).toFixed(2)}` : '';
      // Sprint 26 Item 8: trailing stop T badge.
      const trailBadge = p.trailing_stop_pct
        ? `<span class="badge neutral" style="font-size:10px;padding:1px 4px;margin-left:3px"
             title="Trailing stop active — trail: ${(Number(p.trailing_stop_pct) * 100).toFixed(1)}%">T</span>`
        : '';
      // Sprint 23 Item 3: Stop tooltip.
      const stopTooltip = stopBadge === 'GREEN'
        ? 'GREEN: more than 1% from stop level — position safe'
        : stopBadge === 'AMBER'
        ? 'AMBER: within 1% of stop — monitor closely'
        : 'RED: stop level breached — exit recommended';
      const holdRaw = p.hold_minutes != null ? p.hold_minutes : p.hold_time;
      const holdFmt = _fmtHold(holdRaw);
      const holdColor = p.time_stop_exceeded ? 'var(--amber)' : 'var(--text2)';
      // Sprint 23 Item 3: Hold tooltip.
      const holdMins = typeof holdRaw === 'number' ? holdRaw : parseInt(holdRaw, 10);
      const holdTooltip = isNaN(holdMins)
        ? 'Time held since entry'
        : `Hold time: ${holdFmt} since entry${p.time_stop_exceeded ? ' — time stop exceeded' : ''}`;
      const logId = p.log_id || '';
      const dirCls = dir === 'SHORT' ? 'nullifying' : 'confirming';
      const dkBadge = _dkBadge(p.dk_status, p.dk_conviction);
      const isSchwabImport = (p.trade_source || '').toUpperCase() === 'SCHWAB_IMPORT';
      // Sprint 23 Item 4: delete button — only on PAPER manual trades (not Schwab imports).
      const deleteBtn = !isSchwabImport
        ? `<button class="btn-sell" style="font-size:11px;padding:3px 8px;margin-left:4px"
             title="Delete this paper trade"
             onclick="openDeleteConfirm('${logId}','${p.symbol || ''}',${Number(p.shares || 0)})">✕</button>`
        : '';
      tbody.innerHTML += `<tr>
        <td style="font-weight:600">${p.symbol || '--'}</td>
        <td><span class="badge ${dirCls}">${dir}</span></td>
        <td>${p.strategy || '--'}</td>
        <td style="font-family:var(--mono)">${p.shares || 0}</td>
        <td style="font-family:var(--mono)" title="Price at time of scan / order entry">$${Number(entry).toFixed(2)}</td>
        <td style="font-family:var(--mono)">$${Number(price).toFixed(2)}</td>
        <td style="font-family:var(--mono);color:${pnlColor}">${_fmtMoney(pnl)} (${pnlPct.toFixed(1)}%)</td>
        <td>
          <span class="badge ${stopCls}" title="${stopTooltip}">${stopBadge}</span>${trailBadge}
          ${stopPrice ? `<div style="font-family:var(--mono);font-size:10px;color:var(--text3);margin-top:2px">${stopPrice}${targetPrice}</div>` : ''}
        </td>
        <td style="font-family:var(--mono);color:${holdColor}" title="${holdTooltip}">${holdFmt}</td>
        <td>${dkBadge}</td>
        <td>${p.status || '--'}</td>
        <td>
          <button class="btn-close-pos"
            onclick="openCloseConfirm('${logId}','${p.symbol || ''}',${Number(p.shares || 0)},${Number(price)})">Close</button>
          ${deleteBtn}
        </td>
      </tr>`;
    });
  } catch(e) {
    console.error('loadPositions:', e);
    document.getElementById('pos-body').innerHTML =
      '<tr><td colspan="12" class="empty-state">Failed to load positions</td></tr>';
  }
}

// ---------------------------------------------------------------------------
// Sprint 23 Item 1: Sync Schwab
// ---------------------------------------------------------------------------

async function syncSchwab() {
  const btn = document.getElementById('sync-schwab-btn');
  const msgEl = document.getElementById('sync-msg');
  if (btn) { btn.disabled = true; btn.textContent = 'Syncing…'; }
  if (msgEl) msgEl.textContent = 'Syncing Schwab positions…';
  try {
    const resp = await fetch(_posApi() + '/sync/schwab');
    const result = await resp.json();
    const errors = result.errors || [];
    if (msgEl) {
      if (errors.length) {
        msgEl.style.color = 'var(--amber)';
        msgEl.textContent = `Imported: ${result.imported}  Skipped: ${result.skipped}  Warning: ${errors[0]}`;
      } else {
        msgEl.style.color = 'var(--green)';
        msgEl.textContent = `Sync complete — imported: ${result.imported}  already present: ${result.skipped}`;
      }
    }
    loadPositions();
  } catch (e) {
    console.error('syncSchwab:', e);
    if (msgEl) { msgEl.style.color = 'var(--red)'; msgEl.textContent = 'Sync failed — API offline?'; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Sync Schwab'; }
    setTimeout(() => { if (msgEl) msgEl.textContent = ''; }, 8000);
  }
}

// ---------------------------------------------------------------------------
// Close position
// ---------------------------------------------------------------------------

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
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Sprint 23 Item 4: Delete paper trade
// ---------------------------------------------------------------------------

let _pendingDelete = null;

function openDeleteConfirm(logId, symbol, shares) {
  if (!logId) return;
  _pendingDelete = { log_id: logId };
  document.getElementById('delete-modal-details').innerHTML =
    `<div>Delete paper trade: <b>${shares} ${symbol}</b></div>`;
  document.getElementById('delete-modal').classList.add('open');
}

function closeDeleteConfirm() {
  document.getElementById('delete-modal').classList.remove('open');
  _pendingDelete = null;
}

async function submitDelete() {
  if (!_pendingDelete) return;
  const btn = document.getElementById('delete-confirm-btn');
  btn.disabled = true;
  try {
    const resp = await fetch(_posApi() + '/trades/' + _pendingDelete.log_id, {
      method: 'DELETE',
      headers: { 'Authorization': 'Bearer ' + _posToken() },
    });
    await resp.json().catch(() => ({}));
    closeDeleteConfirm();
    loadPositions();
  } catch (e) {
    console.error('submitDelete:', e);
  } finally {
    btn.disabled = false;
  }
}
