// Sprint 20 Item 3: DK STATUS badge colors -- NEUTRAL default (PENDING retired).
// Sprint 22 Item 1: date scope, tier filter, trigger_source column, relative time,
//                   SUPPRESSED row styling, Score column hidden.

function dkBadgeClass(dk) {
  if (dk === 'CONFIRMING') return 'confirming';   // green
  if (dk === 'NULLIFYING') return 'nullifying';   // red
  return 'neutral';                               // NEUTRAL / unknown -> grey
}

function dkBadgeLabel(dk) {
  if (dk === 'CONFIRMING') return 'CONFIRM';
  if (dk === 'NULLIFYING') return 'NULLIFY';
  return 'NEUTRAL';
}

// CIL-075 / CIL-NEW-06: bearer token for write-side endpoints (require_local_token).
function _sigToken() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiToken) || '';
}

// CIL-NEW-06: after-hours detection (09:30–16:00 ET Mon–Fri).
function _isRTH() {
  const etStr = new Date().toLocaleString('en-US', { timeZone: 'America/New_York' });
  const et = new Date(etStr);
  const day = et.getDay();
  if (day === 0 || day === 6) return false;
  const mins = et.getHours() * 60 + et.getMinutes();
  return mins >= 9 * 60 + 30 && mins <= 16 * 60;
}

// CIL-NEW-06: pending buy-signal state.
let _pendingBuySignal = null;

function openBuySignalConfirm(signalId, symbol, tier, price) {
  const rth = _isRTH();
  _pendingBuySignal = { signalId, symbol, tier, price, orderType: rth ? 'MARKET' : 'LIMIT' };

  const detailEl = document.getElementById('buy-signal-details');
  const limitRow = document.getElementById('buy-signal-limit-row');
  const warnEl   = document.getElementById('buy-signal-ah-warn');

  if (detailEl) {
    detailEl.innerHTML =
      `<div>Symbol: <b>${symbol}</b></div>` +
      `<div>Tier: <b>${tier || '--'}</b></div>` +
      `<div>Scan Price: <b>$${Number(price || 0).toFixed(2)}</b></div>` +
      `<div>Execution: <b>MATA across all configured accounts</b></div>` +
      `<div>Order Type: <b>${rth ? 'MARKET' : 'LIMIT (after-hours)'}</b></div>`;
  }
  if (warnEl)   warnEl.style.display = rth ? 'none' : 'block';
  if (limitRow) limitRow.style.display = rth ? 'none' : 'block';

  const modal = document.getElementById('buy-signal-modal');
  if (modal) modal.classList.add('open');
}

function closeBuySignalModal() {
  _pendingBuySignal = null;
  const modal = document.getElementById('buy-signal-modal');
  if (modal) modal.classList.remove('open');
  const limitInput = document.getElementById('buy-signal-limit-price');
  if (limitInput) limitInput.value = '';
  const msgEl = document.getElementById('buy-signal-msg');
  if (msgEl) msgEl.textContent = '';
}

async function submitBuySignal() {
  if (!_pendingBuySignal) return;
  const { signalId, symbol, orderType } = _pendingBuySignal;

  const limitPriceEl = document.getElementById('buy-signal-limit-price');
  const limitPrice   = limitPriceEl ? parseFloat(limitPriceEl.value) : null;
  const msgEl        = document.getElementById('buy-signal-msg');
  const confirmBtn   = document.getElementById('buy-signal-confirm-btn');

  if (orderType === 'LIMIT' && (!limitPrice || limitPrice <= 0)) {
    if (msgEl) { msgEl.textContent = 'Limit price is required for after-hours orders.'; msgEl.style.color = 'var(--red)'; }
    return;
  }

  if (confirmBtn) confirmBtn.disabled = true;

  const API = (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
  const payload = {
    order_type: orderType,
    confirmed: true,
  };
  if (orderType === 'LIMIT' && limitPrice > 0) payload.limit_price = limitPrice;

  try {
    const resp = await fetch(API + '/signals/' + encodeURIComponent(signalId) + '/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + _sigToken() },
      body: JSON.stringify(payload),
    });
    const data = await resp.json().catch(() => ({}));

    if (resp.ok) {
      const total = data.allocated_total || 0;
      const mode  = data.mode || 'PAPER';
      if (msgEl) { msgEl.textContent = `${mode}: ${total} shares of ${symbol} ordered. Signal marked EXECUTED.`; msgEl.style.color = 'var(--green)'; }
      setTimeout(() => { closeBuySignalModal(); loadSignals(); }, 1800);
    } else if (data.error === 'after_hours') {
      // Server confirmed after-hours — switch to LIMIT mode without closing.
      _pendingBuySignal.orderType = 'LIMIT';
      const warnEl   = document.getElementById('buy-signal-ah-warn');
      const limitRow = document.getElementById('buy-signal-limit-row');
      if (warnEl)   warnEl.style.display = 'block';
      if (limitRow) limitRow.style.display = 'block';
      if (msgEl) { msgEl.textContent = data.message || 'After-hours: LIMIT order required.'; msgEl.style.color = 'var(--amber)'; }
    } else {
      if (msgEl) { msgEl.textContent = `Error: ${data.error || resp.status}`; msgEl.style.color = 'var(--red)'; }
    }
  } catch (e) {
    if (msgEl) { msgEl.textContent = 'Network error: ' + e.message; msgEl.style.color = 'var(--red)'; }
  } finally {
    if (confirmBtn) confirmBtn.disabled = false;
  }
}

// CIL-075: soft-delete a PEAD signal, then reload the table so it drops out of
// the queue. The row is preserved server-side (status=DISMISSED) for ML training.
async function dismissSignal(signalId) {
  if (!signalId) return;
  try {
    const resp = await fetch(API + '/signals/' + encodeURIComponent(signalId) + '/dismiss', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + _sigToken() },
    });
    await resp.json().catch(() => ({}));
    loadSignals();
  } catch (e) {
    console.error('dismissSignal:', e);
  }
}

// Item 3c: populate the strategy filter from the actual strategies in the DB.
async function populateStrategyFilter() {
  try {
    const resp = await fetch(API + '/strategies');
    const data = await resp.json();
    const sel = document.getElementById('sig-strategy');
    const current = sel.value;
    sel.innerHTML = '<option value="">All Strategies</option>';
    (data.strategies || []).forEach(s => {
      const opt = document.createElement('option');
      opt.value = s; opt.textContent = s;
      sel.appendChild(opt);
    });
    sel.value = current;
  } catch (e) {
    console.error('populateStrategyFilter:', e);
  }
}

// SIG-01: populate the tier filter from the actual tier values in the DB so any
// tier present in the data (e.g. WEAK-LONG, TRANCHE_1) is always selectable.
async function populateTierFilter() {
  try {
    const resp = await fetch(API + '/tiers');
    const data = await resp.json();
    const sel = document.getElementById('sig-tier');
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = '<option value="">All Tiers</option>';
    (data.tiers || []).forEach(t => {
      const opt = document.createElement('option');
      opt.value = (t || '').toUpperCase();
      opt.textContent = t;
      sel.appendChild(opt);
    });
    sel.value = current;
  } catch (e) {
    console.error('populateTierFilter:', e);
  }
}

// Sprint 28 Item 7: relative time uses formatETFull from tz.js (UTC->ET conversion).
// _fmtRelTime retained as alias for backward compat with any external callers.
function _fmtRelTime(scanTs) {
  return typeof formatETFull === 'function' ? formatETFull(scanTs) : (scanTs || '').substring(0, 16);
}

// Sprint 22 Item 1: date scope filter.
function _dateScopeFilter(signals) {
  const scope = document.getElementById('sig-date-scope');
  const val = scope ? scope.value : 'ALL';
  if (val === 'ALL') return signals;
  const now = new Date();
  const todayStr = now.toISOString().substring(0, 10);
  return signals.filter(s => {
    const ts = (s.scan_ts || '').substring(0, 10);
    if (val === 'TODAY') return ts === todayStr;
    if (val === '7D') {
      const d = new Date(ts);
      return (now - d) <= 7 * 86400 * 1000;
    }
    return true;
  });
}

// Sprint 22 Item 1: tier filter.
function _tierFilter(signals) {
  const sel = document.getElementById('sig-tier');
  const val = sel ? sel.value : '';
  if (!val) return signals;
  return signals.filter(s => (s.tier || '').toUpperCase() === val);
}

// Sprint 22 Item 1: extract trigger_source from factors JSON.
function _triggerSource(s) {
  try {
    const f = typeof s.factors === 'string' ? JSON.parse(s.factors || '{}') : (s.factors || {});
    return f.trigger_source || s.trigger_source || '--';
  } catch (e) {
    return '--';
  }
}

async function loadSignals() {
  try {
    let url = API + '/signals';
    const params = [];
    const strat = document.getElementById('sig-strategy').value;
    const type = document.getElementById('sig-type').value;
    if (strat) params.push('strategy=' + encodeURIComponent(strat));
    if (type) params.push('instrument_type=' + encodeURIComponent(type));
    if (params.length) url += '?' + params.join('&');

    const resp = await fetch(url);
    const data = await resp.json();
    const tbody = document.getElementById('sig-body');
    tbody.innerHTML = '';
    let signals = data.signals || [];

    // Sprint 22 Item 1: client-side date scope and tier filters.
    signals = _dateScopeFilter(signals);
    signals = _tierFilter(signals);

    if (!signals.length) {
      tbody.innerHTML = '<tr><td colspan="10" class="empty-state">No signals found</td></tr>';
      return;
    }
    signals.forEach(s => {
      const dk = (s.dk_status || 'NEUTRAL').toUpperCase();
      const dkClass = dkBadgeClass(dk);
      const dkLabel = dkBadgeLabel(dk);
      const convStr = (s.dk_conviction != null)
        ? `  Conviction: ${Number(s.dk_conviction).toFixed(2)}` : '';
      const dkTooltip = dk === 'CONFIRMING'
        ? `CONFIRMING: Institutional dark-pool buying aligns with signal direction — auto-upgrades WATCH to STRONG.${convStr}`
        : dk === 'NULLIFYING'
        ? `NULLIFYING: Institutional selling opposes signal direction — long signal is SUPPRESSED.${convStr}`
        : 'NEUTRAL: No significant dark-pool activity. Signal passes through unchanged.';
      // Sprint 23 Item 3: trigger_source reads from dedicated column (bridge sets it) or factors fallback.
      // Sprint 25 Item 4: for PEAD signals, show guidance_flag alongside trigger (e.g. "PEAD · BEAT_CUT").
      const trigger = s.trigger_source || _triggerSource(s);
      const guidanceFlag = s.guidance_flag || null;
      const isPead = s.strategy === 'PEAD' || trigger === 'PEAD_BEAT' || trigger === 'PEAD_MISS';
      const triggerDisplay = (isPead && guidanceFlag && guidanceFlag !== 'UNKNOWN')
        ? `${trigger} · ${guidanceFlag}` : trigger;
      const guidanceFlagColor = (guidanceFlag === 'BEAT_CUT' || guidanceFlag === 'MISS_CUT')
        ? 'var(--amber)' : (guidanceFlag === 'BEAT_RAISE' || guidanceFlag === 'MISS_RAISE')
        ? 'var(--green)' : 'var(--amber)';
      const _guidanceTooltips = {
        BEAT_RAISE: 'BEAT_RAISE: Beat + raised guidance — STRONG long, no short',
        BEAT_HOLD:  'BEAT_HOLD: Beat + guidance unchanged — STRONG long, WATCH short',
        BEAT_CUT:   'BEAT_CUT (HPE pattern): Beat + guidance cut — WATCH long (do not auto-approve), WATCH short candidate',
        MISS_RAISE: 'MISS_RAISE: Miss + raised guidance — WATCH long (reversal potential), WATCH short',
        MISS_CUT:   'MISS_CUT: Miss + guidance cut — SUPPRESSED long, STRONG short',
        UNKNOWN:    'UNKNOWN: Guidance data unavailable — tier unchanged, treated as BEAT_HOLD',
      };
      const triggerTooltip = (isPead && guidanceFlag)
        ? (_guidanceTooltips[guidanceFlag] || guidanceFlag)
        : trigger === 'UOA_CALL'
        ? 'UOA_CALL: Unusual call volume surge (bullish) — initiates PSA long candidates'
        : trigger === 'UOA_PUT'
        ? 'UOA_PUT: Unusual put volume surge (bearish) — initiates SHORT candidates'
        : trigger === 'PEAD_BEAT'
        ? 'PEAD_BEAT: Earnings beat (EPS surprise > 0) — initiates PSA + PEAD long candidates'
        : trigger === 'PEAD_MISS'
        ? 'PEAD_MISS: Earnings miss + guidance cut — initiates PEAD + SHORT candidates'
        : trigger === 'PSA_ONLY'
        ? 'PSA_ONLY: Technical pattern only (no predictive trigger) — stays WATCH tier'
        : 'No trigger source. Possible values: UOA_CALL, UOA_PUT, PEAD_BEAT, PEAD_MISS, PSA_ONLY';
      const tier = s.tier || '--';
      const tierTooltip = tier === 'STRONG'
        ? 'STRONG: All criteria met + trigger fired. DK CONFIRMING or NEUTRAL (CONFIRMING auto-upgrades WATCH). Full-size entry.'
        : tier === 'WATCH'
        ? 'WATCH: One trigger fired, technical confirmation passes. DK NEUTRAL. Reduced size or wait for DK CONFIRMING.'
        : tier === 'SUPPRESSED'
        ? 'SUPPRESSED: Technical setup valid but DK NULLIFYING overrides — institutional selling opposes the trade. Skip.'
        : 'Tier: STRONG (high conviction), WATCH (lower conviction), SUPPRESSED (DK override)';
      const status = s.status || '--';
      const statusTooltip = status === 'APPROVED'
        ? 'APPROVED: Signal passed all filters and is actionable'
        : status === 'SUPPRESSED'
        ? 'SUPPRESSED: DK NULLIFYING override — do not trade this signal'
        : status === 'WATCH'
        ? 'WATCH: Valid signal, lower conviction — reduced size'
        : `Status: ${status}`;
      const relTime = _fmtRelTime(s.scan_ts);
      // SIG-Score-01: restore Score column — 1 decimal, color-coded by strength.
      const sc = (s.score == null) ? null : Number(s.score);
      const scColor = sc == null ? 'var(--text3)'
        : sc >= 70 ? 'var(--green)'
        : sc >= 50 ? 'var(--amber)'
        : 'var(--text3)';
      const scStr = sc == null ? '--' : sc.toFixed(1);
      const isSuppressed = status.toUpperCase() === 'SUPPRESSED';
      const rowStyle = isSuppressed
        ? ' style="opacity:0.55;border-left:3px solid #C00000"' : '';
      tbody.innerHTML += `<tr${rowStyle}>
        <td style="font-family:var(--mono);font-size:13px" title="${s.scan_ts || ''}">${relTime}</td>
        <td style="font-weight:600">${s.symbol || '--'}</td>
        <td style="font-family:var(--mono);font-weight:600;color:${scColor}">${scStr}</td>
        <td>${s.strategy || '--'}</td>
        <td style="font-family:var(--mono);font-size:12px;color:${guidanceFlagColor}" title="${triggerTooltip}">${triggerDisplay || '--'}</td>
        <td title="${tierTooltip}">${tier}</td>
        <td><span class="badge ${dkClass}" title="${dkTooltip}">${dkLabel}</span></td>
        <td style="font-family:var(--mono)" title="Price at time of scan — not a limit order price">$${(s.entry_price || 0).toFixed(2)}</td>
        <td title="${statusTooltip}">${status}</td>
        <td style="display:flex;gap:4px;align-items:center">
          ${status === 'APPROVED' && s.signal_id ? `<button onclick="openBuySignalConfirm('${s.signal_id}','${s.symbol || ''}','${s.tier || ''}',${s.entry_price || 0})" title="Execute a MATA buy order for this signal across all configured accounts." style="background:#14532d;border:1px solid #16a34a;color:#86efac;padding:2px 10px;border-radius:3px;font-size:11px;font-weight:700;cursor:pointer">Buy</button>` : ''}
          ${isPead && s.signal_id ? `<button onclick="dismissSignal('${s.signal_id}')" title="Remove this signal from the queue. Signal is preserved for ML training data and will not be re-displayed." style="background:var(--bg3);border:1px solid var(--border);color:var(--text2);padding:2px 8px;border-radius:3px;font-size:11px;cursor:pointer">Dismiss</button>` : ''}
        </td>
      </tr>`;
    });
  } catch(e) {
    console.error('loadSignals:', e);
    document.getElementById('sig-body').innerHTML = '<tr><td colspan="10" class="empty-state">Failed to load signals</td></tr>';
  }
}
