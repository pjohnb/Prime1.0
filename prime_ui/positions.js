async function loadPositions() {
  try {
    const resp = await fetch(API + '/positions');
    const data = await resp.json();
    const tbody = document.getElementById('pos-body');
    tbody.innerHTML = '';
    const positions = data.positions || [];
    if (!positions.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No open positions</td></tr>';
      return;
    }
    positions.forEach(p => {
      const entry = p.entry_price || p.price_at_scan || 0;
      const src = p.trade_source || 'PAPER';
      tbody.innerHTML += `<tr>
        <td style="font-weight:600">${p.symbol || '--'}</td>
        <td>${p.strategy || '--'}</td>
        <td>${p.mode || '--'}</td>
        <td style="font-family:var(--mono)">${p.shares || 0}</td>
        <td style="font-family:var(--mono)">$${entry.toFixed(2)}</td>
        <td><span class="badge ${src === 'LEGACY' ? 'neutral' : src === 'LIVE' ? 'confirming' : 'unavailable'}">${src}</span></td>
        <td>${p.status || '--'}</td>
      </tr>`;
    });
  } catch(e) {
    console.error('loadPositions:', e);
    document.getElementById('pos-body').innerHTML = '<tr><td colspan="7" class="empty-state">Failed to load positions</td></tr>';
  }
}
