// Sprint 14 Item 2: Order entry -> confirmation modal -> authenticated POST.
// Reads/writes via window.PRIME_CONFIG (apiBase + apiToken from /ui-config.js).

function _orderApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}
function _orderToken() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiToken) || '';
}

function _readOrderForm(side) {
  const symbol = (document.getElementById('ord-symbol').value || '').trim().toUpperCase();
  const qty = parseInt(document.getElementById('ord-qty').value, 10);
  const price = parseFloat(document.getElementById('ord-price').value);
  const strategy = document.getElementById('ord-strategy').value;
  const account = (document.getElementById('ord-account').value || '').trim();
  return { symbol, qty, price, strategy, account, direction: side };
}

function _setMsg(text, ok) {
  const el = document.getElementById('ord-msg');
  el.textContent = text;
  el.className = 'order-msg ' + (ok ? 'ok' : 'err');
}

// Holds the validated order between confirm-open and submit.
let _pendingOrder = null;

function openOrderConfirm(side) {
  const o = _readOrderForm(side);
  if (!o.symbol) { _setMsg('Symbol is required', false); return; }
  if (!o.qty || o.qty <= 0) { _setMsg('Qty must be a positive integer', false); return; }
  if (!o.price || o.price <= 0) { _setMsg('Price must be a positive number', false); return; }

  _pendingOrder = o;
  document.getElementById('modal-details').innerHTML =
    `<div><b>${side}</b> <b>${o.qty}</b> ${o.symbol} @ $${o.price.toFixed(2)}</div>` +
    `<div>Strategy: <b>${o.strategy}</b></div>` +
    `<div>Account: <b>${o.account || '--'}</b></div>` +
    `<div>Mode: <b>PAPER</b></div>`;
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
  try {
    const resp = await fetch(_orderApi() + '/trades', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + _orderToken(),
      },
      body: JSON.stringify(o),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.status === 201) {
      _setMsg(`Paper trade submitted (${data.log_id ? data.log_id.substring(0, 8) : 'ok'})`, true);
      document.getElementById('ord-symbol').value = '';
      document.getElementById('ord-qty').value = '';
      document.getElementById('ord-price').value = '';
      loadPositions();
    } else {
      _setMsg('Rejected (' + resp.status + '): ' + (data.error || 'unknown'), false);
    }
  } catch (e) {
    _setMsg('Network error: ' + e.message, false);
  } finally {
    btn.disabled = false;
    closeOrderConfirm();
  }
}
