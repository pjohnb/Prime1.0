// Sprint 25 Item 1: Scan Control tab
// Sprint 25 Item 3: Scan Schedule section

function _scansApi() {
  return (window.PRIME_CONFIG && window.PRIME_CONFIG.apiBase) || 'http://localhost:5001/api/v1';
}

let _scanLogInterval = null;
let _scanStatusInterval = null;
let _runAllActive = false;

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
      if (row.status === 'error') {
        if (msgEl) { msgEl.textContent = 'Error — check log'; msgEl.style.color = 'var(--red)'; }
        break;
      }
    } catch (e) { break; }
  }
  const btn = document.getElementById(btnId);
  if (btn) { btn.disabled = false; btn.textContent = 'Run ' + scanner.toUpperCase(); }
  loadScanStatus();
}

// ── Run All ──────────────────────────────────────────────────────────────────

const _RUN_ALL_SEQ = ['psa', 'pead', 'uoa', 'srs', 'idx', 'short'];

async function runAllScans() {
  if (_runAllActive) return;
  _runAllActive = true;
  const btn = document.getElementById('run-all-btn');
  const prog = document.getElementById('run-all-progress');
  if (btn) btn.disabled = true;
  _startLogPolling();

  for (let i = 0; i < _RUN_ALL_SEQ.length; i++) {
    const s = _RUN_ALL_SEQ[i];
    if (prog) prog.textContent = `Running ${i + 1}/${_RUN_ALL_SEQ.length}: ${s.toUpperCase()}…`;
    try {
      const resp = await fetch(_scansApi() + '/scans/' + s, { method: 'POST' });
      if (resp.status === 202 || resp.status === 409) {
        // Wait for this scanner to complete before starting next
        for (let w = 0; w < 120; w++) {
          await new Promise(r => setTimeout(r, 2500));
          const sr = await fetch(_scansApi() + '/scans/status');
          const sd = await sr.json();
          const row = (sd.scanners || []).find(x => x.scanner.toLowerCase() === s);
          if (!row || row.status === 'complete' || row.status === 'error') break;
        }
      }
    } catch (e) {}
    if (i < _RUN_ALL_SEQ.length - 1) await new Promise(r => setTimeout(r, 5000));
  }

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
    (data.scanners || []).forEach(s => {
      const statusColor = s.status === 'running' ? 'var(--amber)'
        : s.status === 'error' ? 'var(--red)'
        : s.status === 'complete' ? 'var(--green)' : 'var(--text3)';
      tbody.innerHTML += `<tr>
        <td style="font-family:var(--mono);font-weight:600">${s.scanner}</td>
        <td style="font-family:var(--mono);font-size:13px">${s.last_run || '--'}</td>
        <td style="color:${statusColor};font-family:var(--mono);font-size:12px">${s.status || 'idle'}</td>
        <td style="font-family:var(--mono)">${s.signals != null ? s.signals : '--'}</td>
      </tr>`;
    });
  } catch (e) {
    const tbody = document.getElementById('scan-status-body');
    if (tbody) tbody.innerHTML = '<tr><td colspan="4" class="empty-state">API offline</td></tr>';
  }
}

// ── Live Scan Log ─────────────────────────────────────────────────────────────

async function loadScanLog() {
  try {
    const resp = await fetch(_scansApi() + '/scans/log?lines=50');
    const data = await resp.json();
    const el = document.getElementById('scan-log-area');
    if (!el) return;
    el.textContent = (data.lines || []).join('\n') || '(no log entries yet)';
    el.scrollTop = el.scrollHeight;
  } catch (e) {}
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
  el.innerHTML = `
    <div style="background:#1a1f2b;border:1px solid var(--border);border-radius:6px;padding:12px;margin-bottom:14px;font-size:13px;color:var(--amber)">
      PRIME now manages its own schedule via APScheduler. If you previously used Windows Task Scheduler, disable those jobs to avoid double-firing.
    </div>
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
      <label style="font-size:13px;color:var(--text3)">Schedule enabled:</label>
      <select id="sched-enabled" style="background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:5px 10px;border-radius:4px;font-size:14px">
        <option value="true"${enabled ? ' selected' : ''}>Enabled</option>
        <option value="false"${!enabled ? ' selected' : ''}>Disabled</option>
      </select>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-bottom:16px">
      ${_schedRow('psa_time',          'PSA time (ET)',            sched.psa_time,          nextRuns.psa)}
      ${_schedRow('uoa_pead_srs_time', 'UOA + PEAD + SRS time (ET)', sched.uoa_pead_srs_time, nextRuns.uoa)}
      ${_schedRow('idx_time',          'IDX time (ET)',            sched.idx_time,          nextRuns.idx)}
      ${_schedRow('short_time',        'SHORT time (ET)',          sched.short_time,        nextRuns.short)}
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <button class="btn-confirm" onclick="saveScanSchedule()">Save Schedule</button>
      <span id="sched-msg" style="font-family:var(--mono);font-size:13px;min-height:16px"></span>
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
  ['psa_time','uoa_pead_srs_time','idx_time','short_time'].forEach(k => {
    const el = document.getElementById('sched-' + k);
    if (el && el.value) payload[k] = el.value;
  });
  const enEl = document.getElementById('sched-enabled');
  if (enEl) payload.schedule_enabled = enEl.value === 'true';

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

// ── Tab initialisation ────────────────────────────────────────────────────────

function loadScans() {
  loadScanStatus();
  loadScanLog();
  loadScanSchedule();
  // Auto-refresh scan status every 30s
  if (!_scanStatusInterval) {
    _scanStatusInterval = setInterval(loadScanStatus, 30000);
  }
}
