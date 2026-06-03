async function loadDashboard() {
  try {
    const [posResp, sumResp] = await Promise.all([
      fetch(API + '/positions'),
      fetch(API + '/analytics/summary'),
    ]);
    const pos = await posResp.json();
    const sum = await sumResp.json();

    // Sprint 17 Item 5d: OPEN POSITIONS count includes shorts (pos.count) and
    // UNREALIZED P&L aggregates every open position direction-correctly (the
    // /positions feed already computes inverse P&L for SHORT).
    const positions = pos.positions || [];
    document.getElementById('d-open').textContent = pos.count || 0;

    const strategies = sum.strategies || [];
    const totalWins = strategies.reduce((s, x) => s + (x.wins || 0), 0);
    const totalLosses = strategies.reduce((s, x) => s + (x.losses || 0), 0);
    const totalTrades = totalWins + totalLosses;
    const winRate = totalTrades > 0 ? Math.round(totalWins / totalTrades * 100) : 0;
    document.getElementById('d-winrate').textContent = winRate + '%';

    const totalUpnl = positions.reduce((s, p) => s + (Number(p.unrealized_pnl) || 0), 0);
    const pnlEl = document.getElementById('d-upnl');
    const upnlSign = totalUpnl > 0 ? '+' : '';
    pnlEl.textContent = upnlSign + '$' + totalUpnl.toLocaleString(undefined, {maximumFractionDigits: 0});
    pnlEl.className = 'card-val ' + (totalUpnl >= 0 ? 'gain' : 'loss');

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
    loadAdvisory();
    loadBriefing();
  } catch(e) {
    console.error('loadDashboard:', e);
  }
}

// Sprint 15 Item 4: AI Briefing card -- headline + recommended actions.
async function loadBriefing() {
  const headEl = document.getElementById('briefing-headline');
  const actEl = document.getElementById('briefing-actions');
  const detEl = document.getElementById('briefing-detail');
  if (!headEl) return;
  try {
    const resp = await fetch(API + '/advisory/briefing');
    const b = await resp.json();
    headEl.textContent = b.headline || 'No briefing available';
    const actions = b.recommended_actions || [];
    actEl.innerHTML = actions.map(a => `<li>${a}</li>`).join('');
    const warns = b.concentration_warnings || [];
    const parts = [];
    if (b.positions_summary) parts.push(b.positions_summary);
    if (b.signals_summary) parts.push(b.signals_summary);
    if (warns.length) parts.push('Warnings: ' + warns.join('; '));
    detEl.textContent = parts.join('  ·  ');
  } catch(e) {
    console.error('loadBriefing:', e);
    headEl.textContent = 'AI briefing unavailable';
    actEl.innerHTML = '';
    detEl.textContent = '';
  }
}

// Sprint 15 Item 2: AI Position Advisor panel (HOLD/TRIM/EXIT per open position).
function advisoryBadgeClass(rec) {
  if (rec === 'HOLD') return 'confirming';   // green
  if (rec === 'TRIM') return 'unavailable';  // amber
  if (rec === 'EXIT') return 'nullifying';   // red
  return 'neutral';                          // UNAVAILABLE / unknown -> grey
}

async function loadAdvisory() {
  const body = document.getElementById('ai-advisor-body');
  if (!body) return;
  try {
    const resp = await fetch(API + '/advisory/positions');
    const data = await resp.json();
    const items = data.advisories || [];
    if (!items.length) {
      body.innerHTML = '<div class="empty-state" style="padding:14px">No open positions to advise on</div>';
      return;
    }
    body.innerHTML = items.map(a => `
      <div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid var(--border)">
        <span style="font-weight:600;min-width:60px">${a.symbol || '--'}</span>
        <span class="badge ${advisoryBadgeClass((a.recommendation || '').toUpperCase())}">${a.recommendation || '--'}</span>
        <span style="color:var(--text2);font-size:13px">${a.reasoning || ''}</span>
      </div>`).join('');
  } catch(e) {
    console.error('loadAdvisory:', e);
    body.innerHTML = '<div class="empty-state" style="padding:14px">Advisory unavailable</div>';
  }
}
