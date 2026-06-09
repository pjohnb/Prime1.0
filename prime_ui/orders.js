// PRIME v1.0 Order Entry (Sprint 14 Item 2 + Sprint 24 Item 1).
// Reads/writes via window.PRIME_CONFIG (apiBase + apiToken from /ui-config.js).
// Sprint 24: supports LIVE mode with explicit confirmation modal + trailing stop.

function _orderApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}
function _orderToken() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiToken) || '';
}

// Sprint 27 Item 3: toggle MARKET/LIMIT order type fields.
// Sprint 28 Item 5: limit price row wraps input + % toggle button.
function toggleOrderTypeFields() {
  const orderType = (document.getElementById('ord-order-type')?.value || 'MARKET');
  const limitRowEl = document.getElementById('ord-limit-row');
  if (limitRowEl) {
    limitRowEl.style.display = orderType === 'LIMIT' ? 'flex' : 'none';
    if (orderType === 'LIMIT') {
      const limitPriceEl = document.getElementById('ord-limit-price');
      if (limitPriceEl && !limitPriceEl.value) {
        const priceEl = document.getElementById('ord-price');
        if (priceEl && priceEl.value) limitPriceEl.value = priceEl.value;
      }
    }
    const derivedEl = document.getElementById('ord-limit-derived');
    if (derivedEl && orderType !== 'LIMIT') derivedEl.style.display = 'none';
  }
}

// Sprint 28 Item 5: limit price entry mode ($ vs %).
let _limitPriceMode = '$';

function toggleLimitPriceMode() {
  _limitPriceMode = _limitPriceMode === '$' ? '%' : '$';
  const btn   = document.getElementById('ord-limit-mode-btn');
  const input = document.getElementById('ord-limit-price');
  if (btn) btn.textContent = _limitPriceMode;
  if (input) {
    if (_limitPriceMode === '%') {
      input.placeholder = '% e.g. +1.5';
      input.step = '0.1';
      input.removeAttribute('min');
    } else {
      input.placeholder = 'Limit $';
      input.step = '0.01';
      input.setAttribute('min', '0');
    }
    input.value = '';
  }
  const derived = document.getElementById('ord-limit-derived');
  if (derived) { derived.style.display = 'none'; derived.textContent = ''; }
}

function updateLimitDerived() {
  if (_limitPriceMode !== '%') return;
  const derived = document.getElementById('ord-limit-derived');
  const pct   = parseFloat(document.getElementById('ord-limit-price')?.value);
  const price = parseFloat(document.getElementById('ord-price')?.value);
  if (!derived) return;
  if (isNaN(pct) || isNaN(price) || price <= 0) { derived.style.display = 'none'; return; }
  const calc = price * (1 + pct / 100);
  derived.textContent = `Limit: $${calc.toFixed(2)}`;
  derived.style.display = 'block';
}

// Sprint 27 Item 2: toggle FIXED/TRAILING stop fields.
function toggleStopTypeFields() {
  const stopType = (document.getElementById('ord-stop-type')?.value || 'FIXED');
  const fixedEl  = document.getElementById('ord-stop-pct');
  const trailEl  = document.getElementById('ord-trailing-stop');
  const hintEl   = document.getElementById('ord-stop-hint');
  if (fixedEl) fixedEl.style.display  = stopType === 'TRAILING' ? 'none' : '';
  if (trailEl) trailEl.style.display  = stopType === 'TRAILING' ? '' : 'none';
  if (hintEl)  hintEl.style.display   = stopType === 'TRAILING' ? 'none' : '';
}

function _readOrderForm(side) {
  const symbol    = (document.getElementById('ord-symbol').value || '').trim().toUpperCase();
  const qty       = parseInt(document.getElementById('ord-qty').value, 10);
  const price     = parseFloat(document.getElementById('ord-price').value);
  const strategy  = document.getElementById('ord-strategy').value;
  const account   = (document.getElementById('ord-account').value || '').trim();

  // Sprint 27 Item 3: order type (MARKET/LIMIT) + limit price.
  // Sprint 28 Item 5: % mode calculates actual dollar limit from current price.
  const orderType     = (document.getElementById('ord-order-type')?.value || 'MARKET');
  const limitPriceRaw = document.getElementById('ord-limit-price')?.value;
  let limitPrice = null;
  if (orderType === 'LIMIT' && limitPriceRaw) {
    if (_limitPriceMode === '%') {
      const pct = parseFloat(limitPriceRaw);
      if (!isNaN(pct) && price > 0) limitPrice = parseFloat((price * (1 + pct / 100)).toFixed(2));
    } else {
      const raw = parseFloat(limitPriceRaw);
      if (raw > 0) limitPrice = raw;
    }
  }

  // Sprint 27 Item 2: stop type selector.
  const stopType  = (document.getElementById('ord-stop-type')?.value || 'FIXED');
  const trailRaw  = document.getElementById('ord-trailing-stop')?.value;
  const trailingStopPct = stopType === 'TRAILING' && trailRaw && parseFloat(trailRaw) > 0
    ? parseFloat(trailRaw) / 100.0
    : null;

  // Sprint 26 Item 2: stop/target/time fields.
  const stopPctRaw   = document.getElementById('ord-stop-pct')?.value;
  const targetPctRaw = document.getElementById('ord-target-pct')?.value;
  const timeDaysRaw  = document.getElementById('ord-time-stop-days')?.value;
  const stopPct    = stopType !== 'TRAILING' && stopPctRaw && parseFloat(stopPctRaw) > 0
    ? parseFloat(stopPctRaw) : null;
  const targetPct  = targetPctRaw && parseFloat(targetPctRaw) > 0 ? parseFloat(targetPctRaw) : null;
  const timeDays   = timeDaysRaw  && parseFloat(timeDaysRaw)  > 0 ? parseFloat(timeDaysRaw)  : null;

  return { symbol, qty, price, strategy, account, direction: side,
           orderType, limitPrice, stopType, trailingStopPct, stopPct, targetPct, timeDays };
}

// Sprint 26 Item 2: update derived price hints when inputs change.
function updateOrderDerivedPrices() {
  const price  = parseFloat(document.getElementById('ord-price')?.value || 0);
  const stopPct   = parseFloat(document.getElementById('ord-stop-pct')?.value || 0);
  const targetPct = parseFloat(document.getElementById('ord-target-pct')?.value || 0);
  const stopHint   = document.getElementById('ord-stop-hint');
  const targetHint = document.getElementById('ord-target-hint');
  if (stopHint)   stopHint.textContent   = (price > 0 && stopPct   > 0) ? `$${(price * (1 - stopPct / 100)).toFixed(2)}`   : '';
  if (targetHint) targetHint.textContent = (price > 0 && targetPct > 0) ? `$${(price * (1 + targetPct / 100)).toFixed(2)}` : '';
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
  const limitLine  = o.orderType === 'LIMIT' && o.limitPrice
    ? `<div>Order Type: <b>LIMIT</b> @ $${o.limitPrice.toFixed(2)}</div>`
    : `<div>Order Type: <b>MARKET</b></div>`;
  const trailLine = o.stopType === 'TRAILING' && o.trailingStopPct
    ? `<div>Trailing Stop: <b>${(o.trailingStopPct * 100).toFixed(1)}%</b> (trailing)</div>`
    : '';
  const stopLine  = o.stopType !== 'TRAILING' && o.stopPct
    ? `<div>Stop Loss: <b>${o.stopPct}%</b> → $${(o.price * (1 - o.stopPct / 100)).toFixed(2)}</div>`
    : '';
  const targetLine = o.targetPct ? `<div>Take Profit: <b>${o.targetPct}%</b> → $${(o.price * (1 + o.targetPct / 100)).toFixed(2)}</div>` : '';
  const timeLine   = o.timeDays  ? `<div>Time Stop: <b>${o.timeDays}d</b></div>` : '';

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
    limitLine + trailLine + stopLine + targetLine + timeLine +
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
    // Sprint 27 Item 3: order type + limit price
    order_type:  o.orderType || 'MARKET',
    limit_price: o.orderType === 'LIMIT' ? (o.limitPrice || o.price) : undefined,
    // Sprint 26 Item 2: stop/target/time fields
    stop_pct:        o.stopPct   || undefined,
    target_pct:      o.targetPct || undefined,
    time_stop_days:  o.timeDays  || undefined,
    // Sprint 27 Item 2: stop type + trailing pct
    stop_type:         o.stopType || 'FIXED',
    trailing_stop_pct: o.stopType === 'TRAILING' ? (o.trailingStopPct || 0.05) : undefined,
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

      document.getElementById('ord-symbol').value = '';
      document.getElementById('ord-qty').value = '';
      document.getElementById('ord-price').value = '';
      // Reset order type selector back to MARKET
      const otEl = document.getElementById('ord-order-type');
      if (otEl) { otEl.value = 'MARKET'; toggleOrderTypeFields(); }
      // Reset stop type selector back to FIXED
      const stEl = document.getElementById('ord-stop-type');
      if (stEl) { stEl.value = 'FIXED'; toggleStopTypeFields(); }
      // Reset limit price mode to $
      _limitPriceMode = '$';
      const lmBtn = document.getElementById('ord-limit-mode-btn');
      if (lmBtn) lmBtn.textContent = '$';
      const lmInput = document.getElementById('ord-limit-price');
      if (lmInput) { lmInput.placeholder = 'Limit $'; lmInput.step = '0.01'; lmInput.setAttribute('min', '0'); }
      const lmDerived = document.getElementById('ord-limit-derived');
      if (lmDerived) { lmDerived.style.display = 'none'; lmDerived.textContent = ''; }
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
