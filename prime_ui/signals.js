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

// Sprint 22 Item 1: relative time formatting.
function _fmtRelTime(scanTs) {
  if (!scanTs) return '--';
  try {
    const d = new Date(scanTs.replace(' ', 'T'));
    const now = new Date();
    const todayStr = now.toISOString().substring(0, 10);
    const tsStr = scanTs.substring(0, 10);
    const timeStr = scanTs.substring(11, 16);
    if (tsStr === todayStr) return 'Today ' + timeStr;
    // Within last 7 days: show "Jun 2 10:12"
    const diffMs = now - d;
    if (diffMs < 7 * 86400 * 1000) {
      const mo = d.toLocaleString('en-US', { month: 'short' });
      return mo + ' ' + d.getDate() + ' ' + timeStr;
    }
    return scanTs.substring(0, 16);
  } catch (e) {
    return (scanTs || '').substring(0, 16);
  }
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
      tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No signals found</td></tr>';
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
      const trigger = s.trigger_source || _triggerSource(s);
      const triggerTooltip = trigger === 'UOA_CALL'
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
      const isSuppressed = status.toUpperCase() === 'SUPPRESSED';
      const rowStyle = isSuppressed
        ? ' style="opacity:0.55;border-left:3px solid #C00000"' : '';
      tbody.innerHTML += `<tr${rowStyle}>
        <td style="font-family:var(--mono);font-size:13px" title="${s.scan_ts || ''}">${relTime}</td>
        <td style="font-weight:600">${s.symbol || '--'}</td>
        <td>${s.strategy || '--'}</td>
        <td style="font-family:var(--mono);font-size:12px;color:var(--amber)" title="${triggerTooltip}">${trigger || '--'}</td>
        <td title="${tierTooltip}">${tier}</td>
        <td><span class="badge ${dkClass}" title="${dkTooltip}">${dkLabel}</span></td>
        <td style="font-family:var(--mono)" title="Price at time of scan — not a limit order price">$${(s.entry_price || 0).toFixed(2)}</td>
        <td title="${statusTooltip}">${status}</td>
      </tr>`;
    });
  } catch(e) {
    console.error('loadSignals:', e);
    document.getElementById('sig-body').innerHTML = '<tr><td colspan="8" class="empty-state">Failed to load signals</td></tr>';
  }
}
