// Sprint 20 Item 3: DK STATUS badge -- three-state (PENDING / CONFIRMED /
// NULLIFIED retired; Sprint 20 vocabulary: CONFIRMING / NEUTRAL / NULLIFYING).
function dkBadgeClass(dk) {
  if (dk === 'CONFIRMING') return 'confirming';   // green
  if (dk === 'NULLIFYING') return 'nullifying';   // red
  return 'neutral';                               // NEUTRAL / unknown -> grey
}

// Map raw dk_status to a short badge label (fits badge width).
function dkBadgeLabel(dk) {
  if (dk === 'CONFIRMING') return 'CONFIRM';
  if (dk === 'NULLIFYING') return 'NULLIFY';
  return 'NEUTRAL';
}

// Item 3c: populate the strategy filter from the actual strategies in the DB
// instead of a hardcoded list. Preserves the current selection.
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
    sel.value = current; // keep selection if still present
  } catch (e) {
    console.error('populateStrategyFilter:', e);
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
    const signals = data.signals || [];
    if (!signals.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No signals found</td></tr>';
      return;
    }
    signals.forEach(s => {
      // Sprint 20 Item 3: NEUTRAL is the default (PENDING retired).
      const dk = (s.dk_status || 'NEUTRAL').toUpperCase();
      const dkClass = dkBadgeClass(dk);
      const dkLabel = dkBadgeLabel(dk);
      // Tooltip: show dk_conviction when available (e.g. "Conviction: 0.82").
      const convTitle = (s.dk_conviction != null)
        ? ` title="Conviction: ${Number(s.dk_conviction).toFixed(2)}"` : '';
      // Item 3a: show "--" instead of 0 when score is null/zero (no ML score yet)
      const scoreVal = s.score ? s.score : null;
      const scoreStr = scoreVal ? scoreVal : '--';
      tbody.innerHTML += `<tr>
        <td style="font-family:var(--mono);font-size:13px">${(s.scan_ts || '').substring(0, 16)}</td>
        <td style="font-weight:600">${s.symbol || '--'}</td>
        <td>${s.strategy || '--'}</td>
        <td style="font-family:var(--mono)">${scoreStr}</td>
        <td>${s.tier || '--'}</td>
        <td><span class="badge ${dkClass}"${convTitle}>${dkLabel}</span></td>
        <td style="font-family:var(--mono)">$${(s.entry_price || 0).toFixed(2)}</td>
        <td>${s.status || '--'}</td>
      </tr>`;
    });
  } catch(e) {
    console.error('loadSignals:', e);
    document.getElementById('sig-body').innerHTML = '<tr><td colspan="8" class="empty-state">Failed to load signals</td></tr>';
  }
}
