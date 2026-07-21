// Sprint 25 Item 1: Scan Control tab
// Sprint 25 Item 3: Scan Schedule section

function _scansApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}

let _scanLogInterval = null;
let _scanStatusInterval = null;
let _runAllActive = false;

// TT-03 (Sprint 30 Thread 3): scanner name tooltips. Keyed by uppercase scanner code.
const _SCANNER_TOOLTIPS = {
  PSA:   'PSA: Price &amp; Signal Action — monitors intraday price acceleration and volume for momentum breakouts. Requires UOA or PEAD trigger confirmation before approving.',
  UOA:   'UOA: Unusual Options Activity — detects anomalous options volume vs. open interest. High sizzle index = institutional positioning signal.',
  MMR:   'MMR: Metals Mean-Reversion — precious metals mean-reversion scanner. Uses RSI(14) to identify oversold bounce and overbought reversal conditions. Universe: SLV, GLD, GDX, GDXJ, NEM, WPM, AG, PAAS, HL, FR.',
  PEAD:  'PEAD: Post-Earnings Announcement Drift — identifies stocks drifting after earnings beats or misses. Classifies guidance flags.',
  DK:    'DK: Dark Pool — detects off-exchange institutional accumulation or distribution. Confirms or nullifies other scanner signals.',
  IDX:   'IDX: Index &amp; Sector — tracks relative strength across sector ETFs vs. S&amp;P 500. Provides market regime context.',
  SHORT: 'SHORT: Short-Selling — identifies bearish setups combining put-heavy UOA with borrow availability and DK nullification confirmation.',
  SRS:   'SRS: Sector Recovery Scanner — monitors sector ETFs (XLK, XLV, XLF, XLY, XLP, XLE, XLI, XLB, XLRE, XLU, XLC, SPY) for drawdown, stabilization, and recovery phases. Writes a LONG signal when a sector enters RECOVERING phase (2-day gain ≥ +1.5% with volume confirmation after drawdown). 0 signals is normal when no sector currently meets the recovery threshold.',
  MTFA:  'MTFA: Multi-Timeframe Analysis — scores trend alignment across intraday (5-min), weekly (5 sessions), and annual (252 sessions) timeframes. Score 100 = all 3 aligned (STRONG). Also flags proximity to 52-week and session high/low.',
};

// ── Scan trigger buttons ─────────────────────────────────────────────────────

async function triggerScan(scanner, btnId) {
  const btn = document.getElementById(btnId);
  const msgEl = document.getElementById('scan-msg-' + scanner);
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  if (msgEl) { msgEl.textContent = ''; msgEl.style.color = 'var(--text3)'; }

  try {
    const resp = await fetch(_scansApi() + '/scans/' + scanner, { method: 'POST' });
    const data = await resp.json();
    if (resp.status === 202) {
      if (msgEl) { msgEl.textContent = 'Running…'; msgEl.style.color = 'var(--amber)'; }
      _startLogPolling();
      _pollUntilIdle(scanner, btnId, msgEl);
    } else if (resp.status === 409) {
      if (msgEl) { msgEl.textContent = 'Already running'; msgEl.style.color = 'var(--amber)'; }
      if (btn) { btn.disabled = false; btn.textContent = 'Run ' + scanner.toUpperCase(); }
    } else {
      if (msgEl) { msgEl.textContent = data.error || 'Error'; msgEl.style.color = 'var(--red)'; }
      if (btn) { btn.disabled = false; btn.textContent = 'Run ' + scanner.toUpperCase(); }
    }
  } catch (e) {
    if (msgEl) { msgEl.textContent = 'API offline'; msgEl.style.color = 'var(--red)'; }
    if (btn) { btn.disabled = false; btn.textContent = 'Run ' + scanner.toUpperCase(); }
  }
}

async function _pollUntilIdle(scanner, btnId, msgEl) {
  const cancelBtn = document.getElementById('cancel-btn-' + scanner);
  if (cancelBtn) cancelBtn.style.display = '';

  for (let i = 0; i < 120; i++) {
    await new Promise(r => setTimeout(r, 2500));
    try {
      const resp = await fetch(_scansApi() + '/scans/status');
      const data = await resp.json();
      const row = (data.scanners || []).find(s => s.scanner.toLowerCase() === scanner);
      if (!row) break;
      if (row.status === 'complete') {
        const sig = row.signals != null ? row.signals + ' new signals' : 'done';
        if (msgEl) { msgEl.textContent = sig; msgEl.style.color = 'var(--green)'; }
        break;
      }
      if (row.status === 'cancelled') {
        if (msgEl) { msgEl.textContent = 'Cancelled'; msgEl.style.color = 'var(--text3)'; }
        break;
      }
      if (row.status === 'error') {
        if (msgEl) { msgEl.textContent = 'Error — check log'; msgEl.style.color = 'var(--red)'; }
        break;
      }
    } catch (e) { break; }
  }
  if (cancelBtn) cancelBtn.style.display = 'none';
  const btn = document.getElementById(btnId);
  if (btn) { btn.disabled = false; btn.textContent = 'Run ' + scanner.toUpperCase(); }
  loadScanStatus();
}

// WO-PRIME-CANCEL-SCAN-01: cancel individual or all running scans
async function cancelScan(scanner) {
  const cancelBtn = document.getElementById('cancel-btn-' + scanner);
  if (cancelBtn) cancelBtn.disabled = true;
  try {
    await fetch(_scansApi() + '/scans/' + scanner + '/cancel', { method: 'POST' });
  } catch (e) {}
  if (cancelBtn) { cancelBtn.disabled = false; cancelBtn.style.display = 'none'; }
}

async function cancelAllScans() {
  const btn = document.getElementById('cancel-all-btn');
  if (btn) btn.disabled = true;
  try {
    const resp = await fetch(_scansApi() + '/scans/status');
    const data = await resp.json();
    const running = (data.scanners || []).filter(x => x.status === 'running');
    await Promise.all(running.map(s =>
      fetch(_scansApi() + '/scans/' + s.scanner.toLowerCase() + '/cancel', { method: 'POST' }).catch(() => {})
    ));
  } catch (e) {}
  if (btn) { btn.disabled = false; btn.style.display = 'none'; }
}

// ── Run All ──────────────────────────────────────────────────────────────────

async function runAllScans() {
  if (_runAllActive) return;
  _runAllActive = true;
  const btn = document.getElementById('run-all-btn');
  const prog = document.getElementById('run-all-progress');
  if (btn) btn.disabled = true;
  if (prog) prog.textContent = 'Starting parallel scan coordinator…';
  _startLogPolling();

  try {
    const resp = await fetch(_scansApi() + '/scans/all', { method: 'POST' });
    if (resp.status === 409) {
      if (prog) prog.textContent = 'Scan already running — check status below.';
      if (btn) btn.disabled = false;
      _runAllActive = false;
      return;
    }
    if (!resp.ok) {
      if (prog) prog.textContent = 'Error starting scan — check API.';
      if (btn) btn.disabled = false;
      _runAllActive = false;
      return;
    }
  } catch (e) {
    if (prog) prog.textContent = 'API offline.';
    if (btn) btn.disabled = false;
    _runAllActive = false;
    return;
  }

  if (prog) prog.textContent = 'Running: All Scanners…';
  const cancelAllBtn = document.getElementById('cancel-all-btn');
  if (cancelAllBtn) cancelAllBtn.style.display = '';

  // Poll until all scanners are idle (complete, error, or cancelled); timeout at ~10 min
  for (let i = 0; i < 240; i++) {
    await new Promise(r => setTimeout(r, 2500));
    try {
      const sr = await fetch(_scansApi() + '/scans/status');
      const sd = await sr.json();
      const running = (sd.scanners || []).filter(x => x.status === 'running');
      if (running.length === 0) break;
      if (prog) prog.textContent = 'Running: All Scanners…';
    } catch (e) { break; }
  }

  if (cancelAllBtn) cancelAllBtn.style.display = 'none';
  if (prog) prog.textContent = 'All scans complete.';
  if (btn) btn.disabled = false;
  _runAllActive = false;
  loadScanStatus();
}

// ── Last Scan Results table ───────────────────────────────────────────────────

async function loadScanStatus() {
  try {
    const resp = await fetch(_scansApi() + '/scans/status');
    const data = await resp.json();
    const tbody = document.getElementById('scan-status-body');
    if (!tbody) return;
    tbody.innerHTML = '';
    const anyRunning = (data.scanners || []).some(x => x.status === 'running');
    const cancelAllBtn = document.getElementById('cancel-all-btn');
    if (cancelAllBtn && !_runAllActive) cancelAllBtn.style.display = anyRunning ? '' : 'none';
    (data.scanners || []).forEach(s => {
      const cancelBtn = document.getElementById('cancel-btn-' + s.scanner.toLowerCase());
      if (cancelBtn) cancelBtn.style.display = s.status === 'running' ? '' : 'none';
      const statusColor = s.status === 'running' ? 'var(--amber)'
        : s.status === 'error' ? 'var(--red)'
        : s.status === 'complete' ? 'var(--green)'
        : s.status === 'cancelled' ? 'var(--text3)' : 'var(--text3)';
      const lastRunFmt = s.last_run
        ? (typeof formatET === 'function' ? formatET(s.last_run, true) : s.last_run)
        : '--';
      const canAsk = s.status === 'complete' || s.status === 'error';
      const askBtn = canAsk
        ? `<button class="btn-refresh" style="padding:2px 8px;font-size:11px"
             onclick="askPrimeScanner('${s.scanner}','${s.last_run || ''}',${s.signals ?? 0})">Ask PRIME</button>`
        : '';
      const scanTip = _SCANNER_TOOLTIPS[(s.scanner || '').toUpperCase()] || '';
      tbody.innerHTML += `<tr id="scan-row-${s.scanner}">
        <td style="font-family:var(--mono);font-weight:600"${scanTip ? ` data-tooltip="${scanTip}"` : ''}>${s.scanner}</td>
        <td style="font-family:var(--mono);font-size:13px" title="${s.last_run || ''}">${lastRunFmt}</td>
        <td style="color:${statusColor};font-family:var(--mono);font-size:12px">${s.status || 'idle'}</td>
        <td style="font-family:var(--mono)">${s.signals != null ? s.signals : '--'}</td>
        <td>${askBtn}</td>
      </tr>
      <tr id="scan-explain-${s.scanner}" style="display:none">
        <td colspan="5" style="padding:0">
          <div id="scan-explain-panel-${s.scanner}"
               style="background:var(--bg4);border-top:1px solid var(--border);padding:12px 16px;font-size:13px;color:var(--text2);line-height:1.6;white-space:pre-wrap"></div>
        </td>
      </tr>`;
    });
  } catch (e) {
    const tbody = document.getElementById('scan-status-body');
    if (tbody) tbody.innerHTML = '<tr><td colspan="5" class="empty-state">API offline</td></tr>';
  }
}

// UI-AskPrime-01: fetch scan log and POST to /advisory/scan-explain
async function askPrimeScanner(scanner, runTs, signalCount) {
  const panelRow = document.getElementById('scan-explain-' + scanner);
  const panel = document.getElementById('scan-explain-panel-' + scanner);
  if (!panelRow || !panel) return;

  // Toggle off if already open
  if (panelRow.style.display !== 'none') {
    panelRow.style.display = 'none';
    return;
  }
  panelRow.style.display = 'table-row';
  panel.textContent = 'Asking PRIME AI...';

  // Fetch last 50 lines of scan log
  let logExcerpt = '';
  try {
    const lr = await fetch(_scansApi() + '/scans/log?lines=50');
    const ld = await lr.json();
    logExcerpt = (ld.lines || []).join('\n');
  } catch (e) {}

  try {
    const r = await fetch(_scansApi() + '/advisory/scan-explain', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scanner: scanner,
        run_ts: runTs,
        signal_count: signalCount,
        log_excerpt: logExcerpt,
        rejection_summary: '',
      }),
    });
    const d = await r.json();
    panel.textContent = d.explanation || 'No explanation returned.';
  } catch (e) {
    panel.textContent = 'Advisory unavailable — check API connection.';
  }
}

// ── Live Scan Log ─────────────────────────────────────────────────────────────

async function loadScanLog(date) {
  try {
    const url = _scansApi() + '/scans/log?lines=50' + (date ? '&date=' + date : '');
    const resp = await fetch(url);
    const data = await resp.json();
    const el = document.getElementById('scan-log-area');
    if (!el) return;
    el.textContent = (data.lines || []).join('\n') || '(no log entries yet)';
    el.scrollTop = el.scrollHeight;
  } catch (e) {}
}

async function loadPastLogFiles() {
  try {
    const resp = await fetch(_scansApi() + '/scans/log/files');
    const data = await resp.json();
    const sel = document.getElementById('scan-log-date-sel');
    if (!sel) return;
    const dates = data.dates || [];
    const today = new Date().toISOString().substring(0, 10);
    sel.innerHTML = '<option value="">Today</option>' +
      dates.filter(d => d !== today).map(d =>
        `<option value="${d}">${d}</option>`
      ).join('');
  } catch (e) {}
}

// ── Scan log copy / clear (Sprint 28 Item 6) ─────────────────────────────────

function copyScanLog() {
  const el = document.getElementById('scan-log-area');
  if (!el) return;
  navigator.clipboard.writeText(el.textContent).catch(() => {
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand('copy');
    sel.removeAllRanges();
  });
}

function clearScanLog() {
  const el = document.getElementById('scan-log-area');
  if (el) el.textContent = '(log cleared)';
}

function _startLogPolling() {
  if (_scanLogInterval) return;
  _scanLogInterval = setInterval(loadScanLog, 5000);
}

function _stopLogPolling() {
  if (_scanLogInterval) { clearInterval(_scanLogInterval); _scanLogInterval = null; }
}

// ── Schedule section ──────────────────────────────────────────────────────────

let _scheduleData = {};

async function loadScanSchedule() {
  try {
    const resp = await fetch(_scansApi() + '/scans/schedule');
    const data = await resp.json();
    _scheduleData = data.schedule || {};
    const nextRuns = data.next_runs || {};
    _renderSchedule(_scheduleData, nextRuns);
  } catch (e) {
    const el = document.getElementById('scan-schedule-body');
    if (el) el.innerHTML = '<div class="empty-state">Failed to load schedule</div>';
  }
}

function _renderSchedule(sched, nextRuns) {
  const el = document.getElementById('scan-schedule-body');
  if (!el) return;
  const enabled = sched.schedule_enabled !== false;
  const deepEnabled = sched.deep_scan_enabled !== false;
  el.innerHTML = `
    <div style="background:#1a1f2b;border:1px solid var(--border);border-radius:6px;padding:12px;margin-bottom:14px;font-size:13px;color:var(--amber)">
      PRIME manages its own schedule via APScheduler. If you previously used Windows Task Scheduler, disable those jobs to avoid double-firing.
    </div>
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
      <label style="font-size:13px;color:var(--text3)">Schedule enabled:</label>
      <select id="sched-enabled" style="background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:5px 10px;border-radius:4px;font-size:14px">
        <option value="true"${enabled ? ' selected' : ''}>Enabled</option>
        <option value="false"${!enabled ? ' selected' : ''}>Disabled</option>
      </select>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-bottom:16px">
      ${_schedRow('deep_scan_time',    'Pre-Market Deep Scan (ET)', sched.deep_scan_time,    nextRuns.deep)}
      ${_schedRow('psa_time',          'PSA time (ET)',             sched.psa_time,          nextRuns.psa)}
      ${_schedRow('uoa_pead_srs_time', 'UOA + PEAD + SRS time (ET)', sched.uoa_pead_srs_time, nextRuns.uoa)}
      ${_schedRow('idx_time',          'IDX time (ET)',             sched.idx_time,          nextRuns.idx)}
      ${_schedRow('short_time',        'SHORT time (ET)',           sched.short_time,        nextRuns.short)}
    </div>
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
      <label style="font-size:13px;color:var(--text3)">Deep scan enabled:</label>
      <select id="sched-deep-enabled" style="background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:5px 10px;border-radius:4px;font-size:14px">
        <option value="true"${deepEnabled ? ' selected' : ''}>Enabled</option>
        <option value="false"${!deepEnabled ? ' selected' : ''}>Disabled</option>
      </select>
    </div>
    <div style="display:flex;gap:10px;align-items:center;margin-bottom:24px">
      <button class="btn-confirm" onclick="saveScanSchedule()">Save Schedule</button>
      <span id="sched-msg" style="font-family:var(--mono);font-size:13px;min-height:16px"></span>
    </div>

    <div class="order-panel" style="margin-top:8px">
      <div class="panel-title" style="cursor:pointer" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none'">
        DISABLE WINDOWS TASK SCHEDULER JOBS ▼
      </div>
      <div style="display:none;padding:8px 0;font-size:13px;color:var(--text2);line-height:1.7">
        <p>If PRIME scans were previously scheduled in Windows Task Scheduler, disable them to prevent duplicate runs.</p>
        <ol style="padding-left:18px">
          <li>Press <b>Win+R</b>, type <code>taskschd.msc</code>, press Enter.</li>
          <li>In the left panel, click <b>Task Scheduler Library</b>.</li>
          <li>Look for tasks named <b>PRIME PSA Scan</b>, <b>PRIME UOA Scan</b>, etc.</li>
          <li>Right-click each PRIME task → click <b>Disable</b>.</li>
          <li>The task Status column will show "Disabled" — it will no longer run automatically.</li>
          <li>Return to PRIME and confirm APScheduler is running via the Schedule section above.</li>
        </ol>
        <p style="color:var(--text3);font-size:12px">Note: Disabling does not delete the tasks. You can re-enable them if needed, but they are not required when APScheduler is active.</p>
      </div>
    </div>`;
}

function _schedRow(id, label, val, nextRun) {
  return `<label style="display:flex;flex-direction:column;gap:4px">
    <span style="font-size:12px;color:var(--text3);font-family:var(--mono)">${label}</span>
    <input id="sched-${id}" type="time" value="${val || ''}"
      style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:14px;font-family:var(--mono)"/>
    ${nextRun ? `<span style="font-size:11px;color:var(--text3)">Next run: ${nextRun}</span>` : ''}
  </label>`;
}

async function saveScanSchedule() {
  const payload = {};
  ['psa_time','uoa_pead_srs_time','idx_time','short_time','deep_scan_time'].forEach(k => {
    const el = document.getElementById('sched-' + k);
    if (el && el.value) payload[k] = el.value;
  });
  const enEl = document.getElementById('sched-enabled');
  if (enEl) payload.schedule_enabled = enEl.value === 'true';
  const deepEnEl = document.getElementById('sched-deep-enabled');
  if (deepEnEl) payload.deep_scan_enabled = deepEnEl.value === 'true';

  const msgEl = document.getElementById('sched-msg');
  try {
    const resp = await fetch(_scansApi() + '/scans/schedule', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (resp.ok) {
      if (msgEl) { msgEl.style.color = 'var(--green)'; msgEl.textContent = 'Saved — APScheduler rescheduled'; }
      setTimeout(() => { if (msgEl) msgEl.textContent = ''; }, 3000);
      loadScanSchedule();
    } else {
      if (msgEl) { msgEl.style.color = 'var(--red)'; msgEl.textContent = data.error || 'Save failed'; }
    }
  } catch (e) {
    if (msgEl) { msgEl.style.color = 'var(--red)'; msgEl.textContent = 'Save failed — API offline?'; }
  }
}

// ── Polygon rate limit indicator (Sprint 28 Item 4) ───────────────────────────

async function loadPolygonPlanIndicator() {
  try {
    const resp = await fetch(_scansApi() + '/settings');
    const data = await resp.json();
    const el = document.getElementById('polygon-rate-indicator');
    if (!el) return;
    const plan = (data.polygon_plan || 'free').toLowerCase();
    if (plan === 'free') {
      const delayMs = data.polygon_rate_limit_delay_ms != null ? data.polygon_rate_limit_delay_ms : 13000;
      el.textContent = `Rate limiting active (free tier) — ${delayMs / 1000}s between Polygon calls. IDX + SHORT scans will be slower. Upgrade to paid in Settings > Polygon.`;
      el.style.display = 'block';
    } else {
      el.style.display = 'none';
    }
  } catch (e) {}
}

// ── PSA Stage0 Rejection Distribution (WO-PRIME-PSA-CALIBRATION-02 Phase 1) ──

async function loadPsaRejectionPanel() {
  const el = document.getElementById('psa-rejection-panel');
  if (!el) return;
  try {
    const resp = await fetch(_scansApi() + '/psa/stage0-distribution');
    if (!resp.ok) { el.innerHTML = ''; return; }
    const d = await resp.json();
    if (!d.last_run) { el.innerHTML = ''; return; }

    const ts = typeof formatET === 'function' ? formatET(d.last_run, true) : d.last_run;
    const total = d.universe_size || 0;
    const s0 = d.stage0_rejected || 0;
    const s1 = d.stage1_rejected || 0;
    const sig = d.signals_found || 0;
    const by = d.by_criterion || {};

    const _bar = (label, count, pct, color) => `
      <div style="margin-bottom:5px">
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text3);margin-bottom:2px">
          <span>${label}</span><span style="font-family:var(--mono)">${count}</span>
        </div>
        <div style="background:var(--bg2);border-radius:3px;height:6px;overflow:hidden">
          <div style="background:${color};height:6px;width:${Math.min(pct,100).toFixed(1)}%;transition:width .3s"></div>
        </div>
      </div>`;

    const pctOf = (n) => total > 0 ? (n / total * 100) : 0;

    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
        <h3 style="font-size:12px;color:var(--text3);margin:0;font-family:var(--mono);letter-spacing:.06em;text-transform:uppercase">PSA Stage 0 Breakdown</h3>
        <span style="color:var(--text3);font-weight:400;font-size:11px">${ts}</span>
        <button onclick="openAbcdDiagram()" title="A-B-C-D pattern diagram" style="background:transparent;border:1px solid var(--border);color:var(--text3);border-radius:50%;width:18px;height:18px;font-size:10px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;padding:0;flex-shrink:0;font-family:var(--mono);font-weight:700;line-height:1">?</button>
      </div>
      <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:12px 16px">
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px 16px;margin-bottom:12px;font-family:var(--mono);font-size:12px">
          <div><span style="color:var(--text3)">Universe</span><br><span style="font-size:16px;color:var(--text)">${total}</span></div>
          <div><span style="color:var(--text3)">Stage 0 Rejected</span><br><span style="font-size:16px;color:var(--amber)">${s0}</span></div>
          <div><span style="color:var(--text3)">Stage 1 Rejected</span><br><span style="font-size:16px;color:var(--text2)">${s1}</span></div>
          <div><span style="color:var(--text3)">Approved</span><br><span style="font-size:16px;color:var(--green)">${sig}</span></div>
        </div>
        ${s0 > 0 ? `
        <div style="border-top:1px solid var(--border);padding-top:10px;margin-top:4px">
          <div style="font-size:11px;color:var(--text3);font-family:var(--mono);margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em">Stage 0 breakdown (first-failing criterion)</div>
          ${_bar('Price below minimum', by.min_price || 0, pctOf(by.min_price || 0), 'var(--amber)')}
          ${_bar('Price above maximum', by.max_price || 0, pctOf(by.max_price || 0), '#c084fc')}
          ${_bar('Volume below minimum', by.min_daily_volume || 0, pctOf(by.min_daily_volume || 0), 'var(--blue)')}
        </div>` : ''}
      </div>`;
  } catch (e) {
    const el2 = document.getElementById('psa-rejection-panel');
    if (el2) el2.innerHTML = '';
  }
}

// CIL-44: A-B-C-D diagram modal
function openAbcdDiagram() {
  document.getElementById('abcd-diagram-modal').style.display = 'flex';
  document.addEventListener('keydown', _abcdEscHandler);
}

function closeAbcdDiagram() {
  document.getElementById('abcd-diagram-modal').style.display = 'none';
  document.removeEventListener('keydown', _abcdEscHandler);
}

function _abcdEscHandler(e) {
  if (e.key === 'Escape') closeAbcdDiagram();
}

// ── Tab initialisation ────────────────────────────────────────────────────────

function loadScans() {
  loadScanStatus();
  loadScanLog();
  loadScanSchedule();
  loadPastLogFiles();
  loadPolygonPlanIndicator();
  loadPsaRejectionPanel();
  // Auto-refresh scan status every 30s
  if (!_scanStatusInterval) {
    _scanStatusInterval = setInterval(loadScanStatus, 30000);
  }
}
