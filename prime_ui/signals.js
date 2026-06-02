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
      const dk = (s.dk_status || 'PENDING').toUpperCase();
      const dkClass = dk === 'CONFIRMING' ? 'confirming' : dk === 'NULLIFYING' ? 'nullifying' : dk === 'UNAVAILABLE' ? 'unavailable' : 'neutral';
      tbody.innerHTML += `<tr>
        <td style="font-family:var(--mono);font-size:13px">${(s.scan_ts || '').substring(0, 16)}</td>
        <td style="font-weight:600">${s.symbol || '--'}</td>
        <td>${s.strategy || '--'}</td>
        <td style="font-family:var(--mono)">${s.score || 0}</td>
        <td>${s.tier || '--'}</td>
        <td><span class="badge ${dkClass}">${dk}</span></td>
        <td style="font-family:var(--mono)">$${(s.entry_price || 0).toFixed(2)}</td>
        <td>${s.status || '--'}</td>
      </tr>`;
    });
  } catch(e) {
    console.error('loadSignals:', e);
    document.getElementById('sig-body').innerHTML = '<tr><td colspan="8" class="empty-state">Failed to load signals</td></tr>';
  }
}
