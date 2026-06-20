// PRIME v1.0 Order History Tab (Sprint 26 Item 7).
// H-01 (Sprint 29): date range filter + quick-select buttons.
// H-02 (Sprint 29): signal_id linkage — View Signal column.
// H-03 (Sprint 29): unified Open + Closed view with STATUS badge.

function _histApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}

let _historyData = [];

function _fmtHistMoney(v) {
  if (v == null) return '--';
  const n = Number(v);
  const sign = n > 0 ? '+' : '';
  return sign + '$' + n.toFixed(2);
}

function _fmtHistPct(v) {
  if (v == null) return '--';
  const n = Number(v);
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

// H-01: set default From (30 days ago) and To (today) if inputs are blank.
function _histDefaultDates() {
  const toEl = document.getElementById('hist-to');
  const fromEl = document.getElementById('hist-from');
  if (!toEl || !fromEl) return;
  if (!toEl.value) {
    const today = new Date();
    toEl.value = today.toISOString().substring(0, 10);
  }
  if (!fromEl.value) {
    const from = new Date();
    from.setDate(from.getDate() - 30);
    fromEl.value = from.toISOString().substring(0, 10);
  }
}

// H-01: quick-select buttons set From/To and reload.
function _histSetRange(range) {
  const toEl = document.getElementById('hist-to');
  const fromEl = document.getElementById('hist-from');
  if (!toEl || !fromEl) return;
  const today = new Date();
  const todayStr = today.toISOString().substring(0, 10);
  if (range === 'all') {
    fromEl.value = '';
    toEl.value = '';
  } else {
    toEl.value = todayStr;
    if (range === 'today') {
      fromEl.value = todayStr;
    } else if (range === 'week') {
      const d = new Date(); d.setDate(d.getDate() - 6);
      fromEl.value = d.toISOString().substring(0, 10);
    } else if (range === 'month') {
      const d = new Date(); d.setDate(d.getDate() - 29);
      fromEl.value = d.toISOString().substring(0, 10);
    }
  }
  loadHistory();
}

// H-02: navigate to Signals tab to show the originating signal.
function _viewSignal(signalId) {
  if (!signalId) return;
  // Switch to Signals tab. The signal may no longer be in the live table
  // (signals are point-in-time), so we show the id in a tooltip title.
  const tabs = document.querySelectorAll('.tab');
  tabs.forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const sigTab = Array.from(tabs).find(t => t.textContent.trim() === 'Signals');
  if (sigTab) sigTab.classList.add('active');
  const sigView = document.getElementById('view-signals');
  if (sigView) sigView.classList.add('active');
  loadSignals();
}

async function loadHistory() {
  const tbody = document.getElementById('hist-body');
  const summaryEl = document.getElementById('hist-summary');
  if (!tbody) return;

  // H-01: ensure date inputs have defaults on first load.
  _histDefaultDates();

  const strategy  = document.getElementById('hist-strategy')?.value || '';
  const direction = document.getElementById('hist-direction')?.value || '';
  const fromDate  = document.getElementById('hist-from')?.value || '';
  const toDate    = document.getElementById('hist-to')?.value || '';
  const status    = document.getElementById('hist-status')?.value || 'all';

  let url = _histApi() + '/trades/history?limit=500';
  if (strategy)  url += '&strategy='  + encodeURIComponent(strategy);
  if (direction) url += '&direction=' + encodeURIComponent(direction);
  if (fromDate)  url += '&from_date=' + encodeURIComponent(fromDate);
  if (toDate)    url += '&to_date='   + encodeURIComponent(toDate);
  if (status)    url += '&status='    + encodeURIComponent(status);

  try {
    const resp = await fetch(url);
    const data = await resp.json();
    _historyData = data.trades || [];
    const summary = data.summary || {};

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
      tbody.innerHTML = '<tr><td colspan="13" class="empty-state">No trades found</td></tr>';
      return;
    }

    _historyData.forEach(t => {
      const tradeStatus = (t.status || 'CLOSED').toUpperCase();
      const isOpen = tradeStatus === 'OPEN';

      // H-03: STATUS badge — green OPEN, grey CLOSED.
      // TT-04 (Sprint 30 Thread 3): tooltip explaining OPEN vs CLOSED.
      const statusTip = 'OPEN = position currently held. Values show mark-to-market P&amp;L and hold duration so far. CLOSED = position fully exited with realized P&amp;L.';
      const statusBadge = isOpen
        ? `<span class="badge confirming" style="background:#166534;color:#bbf7d0" data-tooltip="${statusTip}">OPEN</span>`
        : `<span class="badge" style="background:var(--bg3);color:var(--text2);border:1px solid var(--border)" data-tooltip="${statusTip}">CLOSED</span>`;

      // Time column: entry_time for OPEN, exit_time for CLOSED.
      const displayTs = isOpen ? t.entry_time : t.exit_time;
      const displayTsFmt = typeof formatETFull === 'function'
        ? formatETFull(displayTs) : (displayTs || '').substring(0, 16);

      const dir = (t.direction || 'LONG').toUpperCase();
      const dirCls = dir === 'SHORT' ? 'nullifying' : 'confirming';

      // P&L: null means '--' for OPEN rows without live price.
      const pnl = t.pnl_dollars != null ? Number(t.pnl_dollars) : null;
      const pnlPct = t.pnl_pct != null ? Number(t.pnl_pct) : null;
      const pnlColor = pnl == null ? 'var(--text2)'
        : pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text2)';

      // Exit price / exit time: dashes for OPEN.
      const exitPriceStr = isOpen ? '--' : '$' + Number(t.exit_price || 0).toFixed(2);

      // H-02: View Signal link for trades with signal_id; dash for SCHWAB_IMPORT or no id.
      const sigId = t.signal_id || null;
      const noSigSources = ['SCHWAB_IMPORT'];
      const showSigLink = sigId && !noSigSources.includes(t.trade_source || '');
      const sigCell = showSigLink
        ? `<span title="${sigId}" data-tooltip="View the scanner signal that triggered this trade entry. Opens or filters to the originating signal record." style="cursor:pointer;color:var(--blue,#60a5fa);font-size:12px" onclick="_viewSignal('${sigId}')">&#128279;</span>`
        : '--';

      tbody.innerHTML += `<tr>
        <td>${statusBadge}</td>
        <td style="font-family:var(--mono);font-size:11px">${displayTsFmt}</td>
        <td style="font-weight:600">${t.symbol || '--'}</td>
        <td>${t.strategy || '--'}</td>
        <td><span class="badge ${dirCls}">${dir}</span></td>
        <td style="font-family:var(--mono)">${t.shares || 0}</td>
        <td style="font-family:var(--mono)">$${Number(t.entry_price || 0).toFixed(2)}</td>
        <td style="font-family:var(--mono)">${exitPriceStr}</td>
        <td style="font-family:var(--mono);color:${pnlColor}">${_fmtHistMoney(pnl)}</td>
        <td style="font-family:var(--mono);color:${pnlColor}">${_fmtHistPct(pnlPct)}</td>
        <td style="font-family:var(--mono)">${_histHold(t.hold_minutes)}</td>
        <td>${t.exit_reason || '--'}</td>
        <td style="text-align:center">${sigCell}</td>
      </tr>`;
    });
  } catch (e) {
    console.error('loadHistory:', e);
    tbody.innerHTML = '<tr><td colspan="13" class="empty-state">Failed to load history — API offline?</td></tr>';
  }
}

function exportHistoryCsv() {
  if (!_historyData.length) return;
  const cols = ['status','exit_time','symbol','strategy','direction','shares',
                'entry_price','exit_price','pnl_dollars','pnl_pct',
                'hold_minutes','exit_reason','signal_id'];
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
