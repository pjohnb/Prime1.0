// PRIME v1.0 Order History Tab (Sprint 26 Item 7).
// Shows all CLOSED trades with entry/exit details, P&L, and summary stats.

function _histApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}

let _historyData = [];

function _fmtHistMoney(v) {
  const n = Number(v || 0);
  const sign = n > 0 ? '+' : '';
  return sign + '$' + n.toFixed(2);
}

function _fmtHistPct(v) {
  const n = Number(v || 0);
  return (n > 0 ? '+' : '') + n.toFixed(1) + '%';
}

function _histHold(mins) {
  if (!mins) return '--';
  const m = parseInt(mins, 10);
  if (m < 60) return m + 'm';
  const h = Math.floor(m / 60); const rm = m % 60;
  if (h < 24) return h + 'h' + (rm ? ' ' + rm + 'm' : '');
  const d = Math.floor(h / 24); const rh = h % 24;
  return d + 'd' + (rh ? ' ' + rh + 'h' : '');
}

async function loadHistory() {
  const tbody = document.getElementById('hist-body');
  const summaryEl = document.getElementById('hist-summary');
  if (!tbody) return;

  const strategy  = document.getElementById('hist-strategy')?.value || '';
  const direction = document.getElementById('hist-direction')?.value || '';
  let url = _histApi() + '/trades/history?limit=500';
  if (strategy)  url += '&strategy=' + encodeURIComponent(strategy);
  if (direction) url += '&direction=' + encodeURIComponent(direction);

  try {
    const resp = await fetch(url);
    const data = await resp.json();
    _historyData = data.trades || [];
    const summary = data.summary || {};

    // Summary row
    if (summaryEl) {
      const pnlColor = (summary.total_pnl || 0) >= 0 ? 'var(--green)' : 'var(--red)';
      summaryEl.innerHTML = `
        <span style="font-family:var(--mono);margin-right:16px">Closed trades: <b>${summary.total || 0}</b></span>
        <span style="font-family:var(--mono);margin-right:16px">Win rate: <b>${summary.win_rate || 0}%</b></span>
        <span style="font-family:var(--mono);margin-right:16px;color:${pnlColor}">Realized P&L: <b>${_fmtHistMoney(summary.total_pnl)}</b></span>
        <span style="font-family:var(--mono)">Avg hold: <b>${_histHold(summary.avg_hold_minutes)}</b></span>`;
    }

    tbody.innerHTML = '';
    if (!_historyData.length) {
      tbody.innerHTML = '<tr><td colspan="11" class="empty-state">No closed trades yet</td></tr>';
      return;
    }

    _historyData.forEach(t => {
      const pnl = Number(t.pnl_dollars || 0);
      const pnlPct = Number(t.pnl_pct || 0);
      const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text2)';
      const dir = (t.direction || 'LONG').toUpperCase();
      const dirCls = dir === 'SHORT' ? 'nullifying' : 'confirming';
      const exitTs = t.exit_time ? t.exit_time.substring(0, 16) : '--';
      tbody.innerHTML += `<tr>
        <td style="font-family:var(--mono);font-size:11px">${exitTs}</td>
        <td style="font-weight:600">${t.symbol || '--'}</td>
        <td>${t.strategy || '--'}</td>
        <td><span class="badge ${dirCls}">${dir}</span></td>
        <td style="font-family:var(--mono)">${t.shares || 0}</td>
        <td style="font-family:var(--mono)">$${Number(t.entry_price || 0).toFixed(2)}</td>
        <td style="font-family:var(--mono)">$${Number(t.exit_price  || 0).toFixed(2)}</td>
        <td style="font-family:var(--mono);color:${pnlColor}">${_fmtHistMoney(pnl)}</td>
        <td style="font-family:var(--mono);color:${pnlColor}">${_fmtHistPct(pnlPct)}</td>
        <td style="font-family:var(--mono)">${_histHold(t.hold_minutes)}</td>
        <td>${t.exit_reason || '--'}</td>
      </tr>`;
    });
  } catch (e) {
    console.error('loadHistory:', e);
    tbody.innerHTML = '<tr><td colspan="11" class="empty-state">Failed to load history — API offline?</td></tr>';
  }
}

function exportHistoryCsv() {
  if (!_historyData.length) return;
  const cols = ['exit_time','symbol','strategy','direction','shares',
                'entry_price','exit_price','pnl_dollars','pnl_pct',
                'hold_minutes','exit_reason'];
  const header = cols.join(',') + '\n';
  const body = _historyData.map(t =>
    cols.map(c => JSON.stringify(t[c] ?? '')).join(',')
  ).join('\n');
  const blob = new Blob([header + body], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'prime_trade_history.csv'; a.click();
  URL.revokeObjectURL(url);
}
