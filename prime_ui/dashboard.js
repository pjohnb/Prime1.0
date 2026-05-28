async function loadDashboard() {
  try {
    const [posResp, sumResp] = await Promise.all([
      fetch(API + '/positions'),
      fetch(API + '/analytics/summary'),
    ]);
    const pos = await posResp.json();
    const sum = await sumResp.json();

    document.getElementById('d-open').textContent = pos.count || 0;

    const strategies = sum.strategies || [];
    const totalWins = strategies.reduce((s, x) => s + (x.wins || 0), 0);
    const totalLosses = strategies.reduce((s, x) => s + (x.losses || 0), 0);
    const totalTrades = totalWins + totalLosses;
    const winRate = totalTrades > 0 ? Math.round(totalWins / totalTrades * 100) : 0;
    document.getElementById('d-winrate').textContent = winRate + '%';

    const totalPnl = sum.total_pnl || 0;
    const pnlEl = document.getElementById('d-upnl');
    pnlEl.textContent = '~$' + totalPnl.toLocaleString();
    pnlEl.className = 'card-val ' + (totalPnl >= 0 ? 'gain' : 'loss');

    document.getElementById('d-signals').textContent = sum.total_signals || 0;

    const tbody = document.getElementById('dash-strategy-body');
    tbody.innerHTML = '';
    strategies.forEach(s => {
      const pnlClass = s.total_pnl >= 0 ? 'color:var(--green)' : 'color:var(--red)';
      const pnlStr = s.total_pnl >= 0 ? '+$' + s.total_pnl.toLocaleString() : '-$' + Math.abs(s.total_pnl).toLocaleString();
      tbody.innerHTML += `<tr>
        <td style="font-weight:600">${s.strategy}</td>
        <td>${s.signal_count}</td>
        <td>${s.traded_count}</td>
        <td>${s.win_rate}%</td>
        <td style="${pnlClass};font-family:var(--mono)">${pnlStr}</td>
      </tr>`;
    });
    if (!strategies.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No analytics data yet</td></tr>';
    }
  } catch(e) {
    console.error('loadDashboard:', e);
  }
}
