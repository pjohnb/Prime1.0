// Sprint 15 Item 4: AI Briefing card -- headline + recommended actions.
// Sprint 22 Item 3: DK activity chips, P&L sparkline (SVG), strategy bar chart,
//                   market status in topbar.

// ---------------------------------------------------------------------------
// Sprint 22 Item 3: Market status in topbar (ET time check).
// ---------------------------------------------------------------------------
function updateMarketStatus() {
  const el = document.getElementById('market-status');
  if (!el) return;
  const now = new Date();
  // Convert to ET using toLocaleString trick (handles DST automatically).
  const etStr = now.toLocaleString('en-US', { timeZone: 'America/New_York' });
  const et = new Date(etStr);
  const day = et.getDay();   // 0=Sun, 6=Sat
  const h = et.getHours();
  const m = et.getMinutes();
  const mins = h * 60 + m;
  let status, color;
  if (day === 0 || day === 6) {
    status = 'WEEKEND'; color = 'var(--gray)';
  } else if (mins < 9 * 60 + 30) {
    status = 'PRE-MKT'; color = 'var(--amber)';
  } else if (mins <= 16 * 60) {
    status = 'OPEN'; color = 'var(--green)';
  } else {
    status = 'CLOSED'; color = 'var(--gray)';
  }
  el.textContent = status;
  el.style.color = color;
}

// ---------------------------------------------------------------------------
// Sprint 22 Item 3: P&L sparkline (7-day SVG).
// ---------------------------------------------------------------------------
async function loadSparkline() {
  const el = document.getElementById('pnl-sparkline');
  if (!el) return;
  try {
    const resp = await fetch(API + '/analytics/pnl-history');
    const data = await resp.json();
    const hist = data.history || [];
    if (hist.length < 2) {
      // Sprint 23 Item 5: show $0.00 with "no history" subtitle when no data.
      el.innerHTML = '<div style="text-align:center"><div style="font-family:var(--mono);font-size:13px">$0.00</div><div style="font-size:11px;color:var(--text3)">no history</div></div>';
      return;
    }
    const vals = hist.map(h => Number(h.pnl) || 0);
    const mn = Math.min(...vals);
    const mx = Math.max(...vals);
    const range = mx - mn || 1;
    const W = 80, H = 28, pad = 2;
    const pts = vals.map((v, i) => {
      const x = pad + (i / (vals.length - 1)) * (W - pad * 2);
      const y = H - pad - ((v - mn) / range) * (H - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    const lastPnl = vals[vals.length - 1];
    const lineColor = lastPnl >= 0 ? '#22c55e' : '#ef4444';
    el.innerHTML = `<svg width="${W}" height="${H}" style="display:block">
      <polyline points="${pts}" fill="none" stroke="${lineColor}" stroke-width="1.5" stroke-linejoin="round"/>
    </svg>`;
  } catch (e) {
    if (el) el.innerHTML = '';
  }
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Sprint 22 Item 3 / Sprint 23 Item 5: DK activity chips.
// Sprint 23 Item 5: count=0 chips are grey regardless of type.
// ---------------------------------------------------------------------------
function renderDkChips(confirming, neutral, nullifying) {
  const el = document.getElementById('dk-activity-chips');
  if (!el) return;
  // Sprint 23 Item 5: chips with count=0 are always grey.
  const confCls = confirming > 0 ? 'confirming' : 'neutral';
  const nullCls = nullifying > 0 ? 'nullifying' : 'neutral';
  el.innerHTML = `
    <span class="badge ${confCls}" style="margin-right:6px"
      title="CONFIRMING: Institutional dark-pool buying aligns with signal direction — upgrades WATCH to STRONG">${confirming} CONFIRM</span>
    <span class="badge neutral" style="margin-right:6px"
      title="NEUTRAL: No significant dark-pool activity — signals pass through unchanged">${neutral} NEUTRAL</span>
    <span class="badge ${nullCls}"
      title="NULLIFYING: Institutional selling opposes signal direction — suppresses long signals">${nullifying} NULLIFY</span>`;
}

// ---------------------------------------------------------------------------
// Sprint 22 Item 3: strategy bar chart (CSS-only, no library).
// ---------------------------------------------------------------------------
function renderStrategyChart(strategies) {
  const el = document.getElementById('strategy-chart');
  if (!el || !strategies.length) return;
  const maxCount = Math.max(...strategies.map(s => s.signal_count || 0), 1);
  // Sprint 23 Item 5: column headers above bar chart.
  const header = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
    <span style="font-family:var(--mono);font-size:11px;min-width:52px;color:var(--text3)"></span>
    <div style="flex:1"></div>
    <span style="font-family:var(--mono);font-size:11px;min-width:52px;text-align:right;color:var(--text3)">P&amp;L</span>
    <span style="font-family:var(--mono);font-size:11px;min-width:30px;text-align:right;color:var(--text3)">Win Rate</span>
  </div>`;
  const rows = strategies.map(s => {
    const pct = Math.round(((s.signal_count || 0) / maxCount) * 100);
    const pnlColor = (s.total_pnl || 0) >= 0 ? '#22c55e' : '#ef4444';
    const pnlStr = (s.total_pnl || 0) >= 0 ? '+$' + s.total_pnl.toLocaleString() : '-$' + Math.abs(s.total_pnl).toLocaleString();
    return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">
      <span style="font-family:var(--mono);font-size:12px;min-width:52px;color:var(--text2)">${s.strategy}</span>
      <div style="flex:1;background:var(--bg4);border-radius:2px;height:12px;overflow:hidden">
        <div style="width:${pct}%;height:100%;background:var(--amber);opacity:0.7;border-radius:2px"></div>
      </div>
      <span style="font-family:var(--mono);font-size:11px;min-width:52px;text-align:right;color:${pnlColor}">${pnlStr}</span>
      <span style="font-family:var(--mono);font-size:11px;min-width:30px;text-align:right;color:var(--text3)">${s.win_rate}%</span>
    </div>`;
  }).join('');
  el.innerHTML = header + rows;
}

// ---------------------------------------------------------------------------
// Main dashboard load
// ---------------------------------------------------------------------------
async function loadDashboard() {
  try {
    const [posResp, sumResp] = await Promise.all([
      fetch(API + '/positions'),
      fetch(API + '/analytics/summary'),
    ]);
    const pos = await posResp.json();
    const sum = await sumResp.json();

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

    // Sprint 22 Item 3: strategy chart (replaces bare table).
    const chartEl = document.getElementById('strategy-chart');
    const tableEl = document.getElementById('dash-strategy-body');
    if (chartEl) {
      renderStrategyChart(strategies);
    } else if (tableEl) {
      tableEl.innerHTML = '';
      strategies.forEach(s => {
        const pnlClass = s.total_pnl >= 0 ? 'color:var(--green)' : 'color:var(--red)';
        const pnlStr = s.total_pnl >= 0 ? '+$' + s.total_pnl.toLocaleString() : '-$' + Math.abs(s.total_pnl).toLocaleString();
        tableEl.innerHTML += `<tr>
          <td style="font-weight:600">${s.strategy}</td>
          <td>${s.signal_count}</td>
          <td>${s.traded_count}</td>
          <td>${s.win_rate}%</td>
          <td style="${pnlClass};font-family:var(--mono)">${pnlStr}</td>
        </tr>`;
      });
      if (!strategies.length) {
        tableEl.innerHTML = '<tr><td colspan="5" class="empty-state">No analytics data yet</td></tr>';
      }
    }

    loadAdvisory();
    loadBriefing();
    loadSparkline();
    updateMarketStatus();
    loadAiCostCard();
  } catch(e) {
    console.error('loadDashboard:', e);
  }
}

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
    if (b.dk_summary) parts.push(b.dk_summary);
    if (warns.length) parts.push('Warnings: ' + warns.join('; '));
    detEl.textContent = parts.join('  ·  ');

    // Sprint 22 Item 3: render DK activity chips from briefing snapshot.
    const snap = b.snapshot || {};
    const dka = snap.dk_activity || {};
    renderDkChips(dka.confirming || 0, dka.neutral || 0, dka.nullifying || 0);
  } catch(e) {
    console.error('loadBriefing:', e);
    headEl.textContent = 'AI briefing unavailable';
    actEl.innerHTML = '';
    detEl.textContent = '';
  }
}

// ---------------------------------------------------------------------------
// Sprint 23 Item 4: Exit PRIME button handlers.
// ---------------------------------------------------------------------------

function openExitConfirm() {
  document.getElementById('exit-modal').classList.add('open');
}

function closeExitConfirm() {
  document.getElementById('exit-modal').classList.remove('open');
}

async function submitExit() {
  const btn = document.getElementById('exit-confirm-btn');
  btn.disabled = true;
  btn.textContent = 'Stopping…';
  closeExitConfirm();
  try {
    await fetch(API + '/shutdown', { method: 'POST' });
  } catch (e) {
    // Expected: server stops mid-response; swallow network error.
  }
  const overlay = document.getElementById('shutdown-overlay');
  if (overlay) { overlay.style.display = 'flex'; }
}

// ---------------------------------------------------------------------------
// Sprint 26 Item 6: AI Cost KPI card.
// ---------------------------------------------------------------------------
async function loadAiCostCard() {
  const el = document.getElementById('d-ai-cost');
  const alertEl = document.getElementById('d-ai-cost-alert');
  if (!el) return;
  try {
    const resp = await fetch(API + '/ai/usage');
    if (!resp.ok) { el.textContent = '$--'; return; }
    const data = await resp.json();
    const today = data.today_cost || 0;
    const week  = data.week_cost  || 0;
    const month = data.month_cost || 0;
    el.textContent = '$' + today.toFixed(4);
    el.title = `Week: $${week.toFixed(4)}  Month: $${month.toFixed(4)}`;

    // Budget alert chip
    if (alertEl && data.budget_alert) {
      const a = data.budget_alert;
      alertEl.textContent = a.message;
      alertEl.style.color = a.level === 'RED' ? 'var(--red)' : 'var(--amber)';
      alertEl.style.display = 'block';
    } else if (alertEl) {
      alertEl.style.display = 'none';
    }
  } catch (e) {
    if (el) el.textContent = '$--';
  }
}

function advisoryBadgeClass(rec) {
  if (rec === 'HOLD') return 'confirming';
  if (rec === 'TRIM') return 'unavailable';
  if (rec === 'EXIT') return 'nullifying';
  return 'neutral';
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
