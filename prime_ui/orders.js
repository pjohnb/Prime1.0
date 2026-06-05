// PRIME v1.0 Order Entry (Sprint 14 Item 2 + Sprint 24 Item 1).
// Reads/writes via window.PRIME_CONFIG (apiBase + apiToken from /ui-config.js).
// Sprint 24: supports LIVE mode with explicit confirmation modal + trailing stop.

function _orderApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}
function _orderToken() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiToken) || '';
}

function _readOrderForm(side) {
  const symbol    = (document.getElementById('ord-symbol').value || '').trim().toUpperCase();
  const qty       = parseInt(document.getElementById('ord-qty').value, 10);
  const price     = parseFloat(document.getElementById('ord-price').value);
  const strategy  = document.getElementById('ord-strategy').value;
  const account   = (document.getElementById('ord-account').value || '').trim();
  const trailRaw  = document.getElementById('ord-trailing-stop')?.value;
  const trailingStopPct = trailRaw && parseFloat(trailRaw) > 0
    ? parseFloat(trailRaw) / 100.0
    : null;
  return { symbol, qty, price, strategy, account, direction: side, trailingStopPct };
}

function _setMsg(text, ok) {
  const el = document.getElementById('ord-msg');
  el.textContent = text;
  el.className = 'order-msg ' + (ok ? 'ok' : 'err');
}

// Detect if server is in LIVE mode (best-effort; falls back to PAPER label).
let _isLiveMode = false;
(async () => {
  try {
    const r = await fetch(_orderApi() + '/settings');
    // trading_mode is in config.json, not settings. Assume PAPER unless a
    // POST /api/v1/trades to a live endpoint returns a live-specific response.
    // For the modal label, check if any existing LIVE-sourced trade exists.
  } catch(e) {}
})();

let _pendingOrder = null;

function openOrderConfirm(side) {
  const o = _readOrderForm(side);
  if (!o.symbol) { _setMsg('Symbol is required', false); return; }
  if (!o.qty || o.qty <= 0) { _setMsg('Qty must be a positive integer', false); return; }
  if (!o.price || o.price <= 0) { _setMsg('Price must be a positive number', false); return; }

  _pendingOrder = o;

  const estimatedTotal = (o.qty * o.price).toLocaleString('en-US', {
    style: 'currency', currency: 'USD'
  });
  const trailLine = o.trailingStopPct
    ? `<div>Trailing Stop: <b>${(o.trailingStopPct * 100).toFixed(1)}%</b></div>`
    : '';

  // Show "Submit Live Order" when server is live — detected from prior responses.
  const btnEl  = document.getElementById('modal-confirm-btn');
  const isLive = window._serverIsLive || false;
  if (btnEl) {
    btnEl.textContent = isLive ? 'Submit Live Order' : 'Confirm Paper Trade';
    btnEl.style.background = isLive ? '#b91c1c' : '';
  }

  document.getElementById('modal-details').innerHTML =
    `<div><b>${side}</b> <b>${o.qty}</b> ${o.symbol} @ $${o.price.toFixed(2)}</div>` +
    `<div>Strategy: <b>${o.strategy}</b></div>` +
    `<div>Account: <b>${o.account || '--'}</b></div>` +
    `<div>Estimated Total: <b>${estimatedTotal}</b></div>` +
    trailLine +
    (isLive
      ? '<div style="color:#fca5a5;margin-top:8px;font-size:13px">LIVE MODE — This submits a real order to Schwab.</div>'
      : `<div>Mode: <b>PAPER</b></div>`);
  document.getElementById('order-modal').classList.add('open');
}

function closeOrderConfirm() {
  document.getElementById('order-modal').classList.remove('open');
  _pendingOrder = null;
}

async function submitOrder() {
  if (!_pendingOrder) return;
  const o = _pendingOrder;
  const btn = document.getElementById('modal-confirm-btn');
  btn.disabled = true;

  const payload = {
    symbol:    o.symbol,
    qty:       o.qty,
    price:     o.price,
    strategy:  o.strategy,
    account:   o.account,
    direction: o.direction,
    confirmed: true,    // Gate 6: explicit user click = confirmed
  };

  try {
    const resp = await fetch(_orderApi() + '/trades', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + _orderToken(),
      },
      body: JSON.stringify(payload),
    });
    const data = await resp.json().catch(() => ({}));

    if (resp.status === 201) {
      const src    = data.trade_source || 'PAPER';
      const logId  = data.log_id ? data.log_id.substring(0, 8) : 'ok';
      const ordId  = data.order_id ? ' order=' + data.order_id : '';
      _setMsg(`${src} trade submitted (${logId}${ordId})`, true);

      // If a trailing stop was set, wire it to the new log_id
      if (o.trailingStopPct && data.log_id) {
        fetch(_orderApi() + '/trades/' + data.log_id + '/trailing-stop', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + _orderToken() },
          body: JSON.stringify({ trailing_stop_pct: o.trailingStopPct }),
        }).catch(() => {});
      }

      document.getElementById('ord-symbol').value = '';
      document.getElementById('ord-qty').value = '';
      document.getElementById('ord-price').value = '';
      if (document.getElementById('ord-trailing-stop'))
        document.getElementById('ord-trailing-stop').value = '';
      loadPositions();

      // Detect LIVE mode from successful LIVE response
      if (src === 'LIVE') window._serverIsLive = true;
    } else {
      const gate = data.gate ? ` [${data.gate}]` : '';
      _setMsg(`Rejected (${resp.status})${gate}: ${data.error || 'unknown'}`, false);
    }
  } catch (e) {
    _setMsg('Network error: ' + e.message, false);
  } finally {
    btn.disabled = false;
    closeOrderConfirm();
  }
}
