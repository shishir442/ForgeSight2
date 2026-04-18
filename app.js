/**
 * app.js — ForgeSight Dashboard
 * Connects to FastAPI backend via SSE streams + REST API.
 * API base: http://localhost:8000
 */

const API = 'http://localhost:8000';

const MACHINES_META = {
  CNC_01:      { name: 'CNC Mill',      failure: 'Bearing wear — vibration + temp gradually rise. Monitor closely.' },
  CNC_02:      { name: 'CNC Lathe',     failure: 'Thermal runaway — afternoon temperature spikes. Watch temp channel.' },
  PUMP_03:     { name: 'Pump',          failure: 'Cavitation + slow RPM drop (developing clog). Inspect impeller.' },
  CONVEYOR_04: { name: 'Conveyor Belt', failure: 'Mostly healthy — use as baseline reference. Low-risk machine.' },
};

const SENSOR_UNITS = { vibration: 'mm/s', temperature: '°C', rpm: 'rpm', current: 'A' };

// ── State ─────────────────────────────────────────────────────────
const STATE = {
  selected:    null,
  activeTab:   'vibration',
  latest:      {},          // mid → last full SSE payload
  history:     {},          // mid → array of sensor readings (smoothed)
  alertCount:  0,
  sseStreams:  {},          // mid → EventSource
  alertStream: null,
  connected:   {},          // mid → bool
};

// ── Chart ─────────────────────────────────────────────────────────
let trendChart = null;

function initChart() {
  const ctx = document.getElementById('trendChart').getContext('2d');
  trendChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'Sensor', data: [],
          borderColor: '#f97316', backgroundColor: 'rgba(249,115,22,0.08)',
          borderWidth: 2, pointRadius: 0, tension: 0.4, fill: true,
        },
        {
          label: 'Upper 2σ', data: [],
          borderColor: '#334155', borderWidth: 1,
          borderDash: [4, 4], pointRadius: 0, fill: false,
        },
        {
          label: 'Lower 2σ', data: [],
          borderColor: '#334155', borderWidth: 1,
          borderDash: [4, 4], pointRadius: 0, fill: false,
        },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#111318', borderColor: '#1e2330', borderWidth: 1,
          titleColor: '#94a3b8', bodyColor: '#e2e8f0',
          titleFont: { family: 'Space Mono', size: 10 },
          bodyFont:  { family: 'Space Mono', size: 12 },
        }
      },
      scales: {
        x: { grid: { color: '#1e2330' }, ticks: { color: '#475569', font: { family: 'Space Mono', size: 9 }, maxTicksLimit: 8 } },
        y: { grid: { color: '#1e2330' }, ticks: { color: '#475569', font: { family: 'Space Mono', size: 9 } } },
      }
    }
  });
}

// ── Helpers ───────────────────────────────────────────────────────
function riskClass(level) {
  return { NORMAL:'risk-normal', WARN:'risk-warn', ALERT:'risk-alert', CRITICAL:'risk-critical' }[level] || 'risk-normal';
}

function riskBarColor(level) {
  return { NORMAL:'var(--green)', WARN:'var(--amber)', ALERT:'var(--orange)', CRITICAL:'var(--red)' }[level] || 'var(--green)';
}

function sevClass(sev) {
  return { WARN:'sev-WARN', ALERT:'sev-ALERT', CRITICAL:'sev-CRITICAL' }[sev] || 'sev-WARN';
}

function sigmaColor(sigma) {
  if (sigma < 1.5) return 'var(--green)';
  if (sigma < 2.5) return 'var(--amber)';
  return 'var(--red)';
}

function sigmaClass(sigma) {
  if (sigma < 1.5) return 'sigma-ok';
  if (sigma < 2.5) return 'sigma-warn';
  return 'sigma-crit';
}

function timeAgo(iso) {
  try {
    const diff = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (diff < 5)  return 'just now';
    if (diff < 60) return `${diff}s ago`;
    return `${Math.round(diff/60)}m ago`;
  } catch { return '—'; }
}

function fmtVal(sensor, v) {
  if (sensor === 'rpm') return Math.round(v).toString();
  return (+v).toFixed(2);
}

// ── Sidebar machine cards ──────────────────────────────────────────
function buildSidebarCards() {
  const list = document.getElementById('machineList');
  list.innerHTML = '';
  Object.entries(MACHINES_META).forEach(([mid, meta]) => {
    STATE.history[mid] = [];
    const card = document.createElement('div');
    card.className = 'machine-card';
    card.id = `card-${mid}`;
    card.innerHTML = `
      <div class="mc-top">
        <span class="mc-name">${meta.name}</span>
        <span class="risk-pill risk-normal" id="pill-${mid}">—</span>
      </div>
      <div class="mc-risk-bar">
        <div class="mc-risk-fill" id="rbar-${mid}" style="width:0%;background:var(--green)"></div>
      </div>
      <div class="mc-id">${mid} · <span id="rscore-${mid}" style="color:var(--text-muted)">waiting…</span></div>
    `;
    card.onclick = () => selectMachine(mid);
    list.appendChild(card);
  });
}

function updateSidebarCard(mid, payload) {
  const pill    = document.getElementById(`pill-${mid}`);
  const bar     = document.getElementById(`rbar-${mid}`);
  const scoreEl = document.getElementById(`rscore-${mid}`);
  if (!pill) return;
  const { risk_score, risk_level } = payload;
  pill.className   = `risk-pill ${riskClass(risk_level)}`;
  pill.textContent = risk_level;
  bar.style.width  = Math.min(risk_score, 100) + '%';
  bar.style.background = riskBarColor(risk_level);
  scoreEl.style.color  = riskBarColor(risk_level);
  scoreEl.textContent  = (+risk_score).toFixed(1) + ' risk';
}

// ── Machine selection ──────────────────────────────────────────────
function selectMachine(mid) {
  STATE.selected = mid;
  document.querySelectorAll('.machine-card').forEach(c => c.classList.remove('active'));
  const card = document.getElementById(`card-${mid}`);
  if (card) card.classList.add('active');

  const meta = MACHINES_META[mid] || {};
  document.getElementById('chartTitle').textContent = `${mid} — Live Sensor Trend`;
  document.getElementById('failurePattern').innerHTML =
    `<span style="font-family:var(--mono);font-size:10px;color:var(--amber);display:block;margin-bottom:4px;letter-spacing:0.5px;">${mid}</span>${meta.failure || '—'}`;

  if (STATE.latest[mid]) {
    renderMainPanel(STATE.latest[mid]);
  } else {
    clearMainPanel();
  }
  updateTrendChart();
}

function clearMainPanel() {
  ['vibration','temperature','rpm','current'].forEach(s => {
    const mv = document.getElementById(`mv-${s}`);
    const ms = document.getElementById(`ms-${s}`);
    if (mv) mv.textContent = '—';
    if (ms) { ms.textContent = 'σ —'; ms.className = 'metric-sigma sigma-gray'; }
  });
}

// ── Main panel render (from SSE payload) ──────────────────────────
function renderMainPanel(p) {
  const sensors  = p.smoothed || p.sensors || {};
  const baseline = p.baseline || {};
  const sigmas   = {};

  // Compute σ distances from baseline envelopes
  for (const s of ['vibration','temperature','rpm','current']) {
    const bl = baseline[s];
    if (bl && bl.lower !== undefined) {
      const mean  = (bl.lower + bl.upper) / 2;
      const sigma = (bl.upper - bl.lower) / 4;
      sigmas[s] = sigma > 0 ? Math.abs((sensors[s] - mean) / sigma) : 0;
    } else {
      sigmas[s] = 0;
    }
  }

  // Metric cards
  const colors = { vibration:'#f97316', temperature:'#ef4444', rpm:'#3b82f6', current:'#10b981' };
  for (const s of ['vibration','temperature','rpm','current']) {
    const mv = document.getElementById(`mv-${s}`);
    const ms = document.getElementById(`ms-${s}`);
    if (!mv) continue;
    mv.textContent = sensors[s] !== undefined ? fmtVal(s, sensors[s]) : '—';
    mv.style.color = colors[s];
    const sg = sigmas[s] || 0;
    ms.textContent  = `σ ${sg.toFixed(1)}`;
    ms.className    = `metric-sigma ${sigmaClass(sg)}`;
  }

  // Gauge
  const score = +(p.risk_score || 0);
  const arc   = 179;
  const fill  = document.getElementById('gaugeFill');
  const txt   = document.getElementById('gaugeScoreText');
  if (fill) fill.setAttribute('stroke-dasharray', `${arc * (score / 100)} ${arc}`);
  if (txt)  {
    txt.textContent = Math.round(score);
    const c = score >= 90 ? '#ef4444' : score >= 70 ? '#f97316' : score >= 40 ? '#f59e0b' : '#10b981';
    txt.setAttribute('fill', c);
  }

  // Level pill + ML score
  const lp = document.getElementById('levelPill');
  if (lp) { lp.className = `risk-pill ${riskClass(p.risk_level)}`; lp.textContent = p.risk_level || '—'; }

  const ml = document.getElementById('mlScore');
  if (ml) {
    const ms2 = +(p.ml_score || 0);
    ml.textContent = ms2.toFixed(3);
    ml.style.color = ms2 > 0.7 ? '#ef4444' : ms2 > 0.4 ? '#f97316' : '#10b981';
  }

  const cf = document.getElementById('compoundFlag');
  if (cf) {
    if (p.is_compound) {
      cf.textContent = 'YES'; cf.style.color = 'var(--red)';
    } else {
      cf.textContent = 'NO';  cf.style.color = 'var(--text-muted)';
    }
  }

  // Sensor deviation bars
  for (const s of ['vibration','temperature','rpm','current']) {
    const bar  = document.getElementById(`sbar-${s}`);
    const val  = document.getElementById(`sval-${s}`);
    if (!bar) continue;
    const v   = sensors[s];
    const bl  = baseline[s];
    let pct   = 20;
    if (bl && v !== undefined) {
      const range = (bl.upper - bl.lower) || 1;
      pct = Math.min(100, Math.max(5, ((v - bl.lower * 0.5) / (range * 1.5)) * 100));
    }
    bar.style.width = pct + '%';
    bar.style.background = sigmaColor(sigmas[s] || 0);
    if (val) val.textContent = v !== undefined ? `${fmtVal(s, v)} ${SENSOR_UNITS[s]}` : '—';
  }

  // Baseline section
  for (const s of ['vibration','temperature','rpm','current']) {
    const bl = baseline[s];
    if (!bl) continue;
    const mean   = (bl.lower + bl.upper) / 2;
    const sigma  = (bl.upper - bl.lower) / 4;
    const envLo  = mean - 2 * sigma;
    const envHi  = mean + 2 * sigma;
    const absMin = bl.lower * 0.5;
    const absMax = bl.upper * 1.5;
    const range  = absMax - absMin || 1;
    const envLoPct = ((envLo - absMin) / range) * 100;
    const envWPct  = ((envHi - envLo) / range) * 100;
    const curPct   = ((( sensors[s] || mean) - absMin) / range) * 100;

    const mnEl  = document.getElementById(`bl-mean-${s}`);
    const rgEl  = document.getElementById(`bl-range-${s}`);
    const envEl = document.getElementById(`bl-env-${s}`);
    const curEl = document.getElementById(`bl-cur-${s}`);

    if (mnEl) mnEl.textContent = s === 'rpm' ? Math.round(mean) : mean.toFixed(1);
    if (rgEl) rgEl.textContent = s === 'rpm'
      ? `${Math.round(bl.lower)} — ${Math.round(bl.upper)}`
      : `${(+bl.lower).toFixed(1)} — ${(+bl.upper).toFixed(1)}`;
    if (envEl) { envEl.style.left = Math.max(0, envLoPct) + '%'; envEl.style.width = Math.min(100, envWPct) + '%'; }
    if (curEl) curEl.style.left = Math.min(96, Math.max(1, curPct)) + '%';
  }

  // Triggered sensors
  const tc = document.getElementById('triggeredContainer');
  if (tc) {
    const triggered = p.triggered || [];
    if (triggered.length === 0) {
      tc.innerHTML = '<span style="font-family:var(--mono);font-size:11px;color:var(--text-muted);">none</span>';
    } else {
      tc.innerHTML = triggered.map(s =>
        `<span class="triggered-chip">${s}</span>`
      ).join('');
    }
  }
}

// ── Trend chart ────────────────────────────────────────────────────
function updateTrendChart() {
  if (!trendChart || !STATE.selected) return;
  const mid  = STATE.selected;
  const sn   = STATE.activeTab;
  const hist = (STATE.history[mid] || []).slice(-40);
  if (hist.length === 0) return;

  const labels = hist.map((_, i) => i === hist.length - 1 ? 'now' : `-${hist.length - 1 - i}s`);
  const data   = hist.map(h => h[sn]);

  // Get envelope from latest payload
  const p  = STATE.latest[mid];
  const bl = p?.baseline?.[sn];
  let upper = null, lower = null;
  if (bl) {
    upper = bl.upper;
    lower = bl.lower;
  }

  const COLORS = { vibration:'#f97316', temperature:'#ef4444', rpm:'#3b82f6', current:'#10b981' };
  trendChart.data.labels            = labels;
  trendChart.data.datasets[0].data  = data;
  trendChart.data.datasets[0].borderColor    = COLORS[sn];
  trendChart.data.datasets[0].backgroundColor = COLORS[sn] + '18';
  trendChart.data.datasets[1].data  = upper !== null ? hist.map(() => upper) : [];
  trendChart.data.datasets[2].data  = lower !== null ? hist.map(() => lower) : [];
  trendChart.update('none');
}

window.switchTab = function(sensor, btn) {
  STATE.activeTab = sensor;
  document.querySelectorAll('.chart-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  updateTrendChart();
};

// ── SSE — machine streams ──────────────────────────────────────────
function connectMachineStream(mid) {
  const url = `${API}/stream/${mid}`;
  const es  = new EventSource(url);

  es.onopen = () => {
    STATE.connected[mid] = true;
    updateConnectionStatus();
  };

  es.onmessage = (e) => {
    try {
      const payload = JSON.parse(e.data);
      STATE.latest[mid] = payload;

      // Store history (smoothed readings)
      const sensors = payload.smoothed || payload.sensors || {};
      STATE.history[mid].push({ ...sensors, timestamp: payload.timestamp });
      if (STATE.history[mid].length > 60) STATE.history[mid].shift();

      // Update sidebar
      updateSidebarCard(mid, payload);

      // Update main panel if this machine is selected
      if (STATE.selected === mid) {
        renderMainPanel(payload);
        updateTrendChart();
      }

      // Update fleet stats
      updateFleetStats();
      document.getElementById('lastSync').textContent = new Date().toTimeString().slice(0,8);

    } catch (err) {
      console.error(`[SSE ${mid}] Parse error:`, err);
    }
  };

  es.onerror = () => {
    STATE.connected[mid] = false;
    updateConnectionStatus();
    // Reconnect after 3s
    setTimeout(() => connectMachineStream(mid), 3000);
  };

  STATE.sseStreams[mid] = es;
}

// ── SSE — alert stream ─────────────────────────────────────────────
function connectAlertStream() {
  const es = new EventSource(`${API}/alerts/stream`);

  es.addEventListener('alert', (e) => {
    try {
      const alert = JSON.parse(e.data);
      addAlertToPanel(alert);
    } catch (err) {
      console.error('[AlertStream] Parse error:', err);
    }
  });

  es.onerror = () => {
    setTimeout(connectAlertStream, 3000);
  };

  STATE.alertStream = es;
}

function addAlertToPanel(a) {
  const container = document.getElementById('alertsContainer');

  // Remove empty state
  const empty = container.querySelector('.empty-alerts');
  if (empty) empty.remove();

  const item = document.createElement('div');
  item.className = `alert-item alert-sev-${a.severity}`;
  item.innerHTML = `
    <div class="alert-top">
      <span class="alert-machine">${a.machine_id}</span>
      <span class="alert-sev-pill ${sevClass(a.severity)}">${a.severity}</span>
    </div>
    <div class="alert-diag">${a.diagnosis || '—'}</div>
    <div class="alert-action">${a.recommended_action || ''}</div>
    <div class="alert-time">${timeAgo(a.timestamp)} · risk ${(+a.risk_score).toFixed(1)}</div>
  `;
  container.insertBefore(item, container.firstChild);
  while (container.children.length > 10) container.removeChild(container.lastChild);

  STATE.alertCount++;
  document.getElementById('alertBadge').textContent = STATE.alertCount;
  document.getElementById('activeAlertCount').textContent = STATE.alertCount;

  // Auto-schedule maintenance ticket if ALERT or CRITICAL
  if (['ALERT','CRITICAL'].includes(a.severity)) {
    addTicket(a);
  }
}

function addTicket(a) {
  const tc = document.getElementById('ticketsContainer');
  const empty = tc.querySelector('.empty-text');
  if (empty) empty.remove();

  const priority = a.severity === 'CRITICAL' ? 'URGENT' : 'HIGH';
  const ticketId = 'FS-' + Math.random().toString(36).slice(2,8).toUpperCase();
  const item = document.createElement('div');
  item.className = 'ticket-item';
  item.innerHTML = `
    <span class="ticket-id">${ticketId}</span>
    <span class="ticket-machine">${a.machine_id} — ${a.machine_name || MACHINES_META[a.machine_id]?.name || ''}</span>
    <span class="ticket-priority priority-${priority}">${priority}</span>
  `;
  tc.insertBefore(item, tc.firstChild);
  while (tc.children.length > 5) tc.removeChild(tc.lastChild);
}

// ── REST — fetch initial dashboard snapshot ────────────────────────
async function fetchDashboard() {
  try {
    const res  = await fetch(`${API}/dashboard`);
    const data = await res.json();

    // Update system health
    const hb = document.getElementById('systemHealthBadge');
    if (data.system_health === 'RED') {
      hb.className = 'system-health health-red'; hb.textContent = 'SYSTEM: RED';
    } else if (data.system_health === 'YELLOW') {
      hb.className = 'system-health health-yellow'; hb.textContent = 'SYSTEM: YELLOW';
    } else {
      hb.className = 'system-health health-green'; hb.textContent = 'SYSTEM: GREEN';
    }

    // Seed latest data
    for (const m of data.machines || []) {
      STATE.latest[m.machine_id] = {
        risk_score: m.risk_score, risk_level: m.risk_level,
        smoothed: m.last_reading, baseline: m.baseline,
        triggered: [], is_compound: false, ml_score: 0,
      };
      updateSidebarCard(m.machine_id, STATE.latest[m.machine_id]);
    }

    // Pre-populate alerts
    for (const a of (data.recent_alerts || []).slice(0, 5).reverse()) {
      addAlertToPanel(a);
    }

    // Auto-select first machine
    if (!STATE.selected) {
      const firstMid = Object.keys(MACHINES_META)[0];
      selectMachine(firstMid);
    }

    // Update ML / baseline status
    document.getElementById('mlStatus').textContent     = 'Loaded';
    document.getElementById('baselineStatus').textContent = 'Seeded';

  } catch (err) {
    console.warn('[Dashboard] Could not fetch snapshot:', err.message);
  }
}

// ── Fleet stats ────────────────────────────────────────────────────
function updateFleetStats() {
  const online = Object.values(STATE.connected).filter(Boolean).length;
  document.getElementById('machinesOnline').textContent = `${online} / ${Object.keys(MACHINES_META).length}`;

  // Derive system health from latest risk levels
  const levels = Object.values(STATE.latest).map(p => p?.risk_level || 'NORMAL');
  const hb = document.getElementById('systemHealthBadge');
  if (levels.includes('CRITICAL')) {
    hb.className = 'system-health health-red';    hb.textContent = 'SYSTEM: RED';
  } else if (levels.some(l => ['ALERT','WARN'].includes(l))) {
    hb.className = 'system-health health-yellow'; hb.textContent = 'SYSTEM: YELLOW';
  } else {
    hb.className = 'system-health health-green';  hb.textContent = 'SYSTEM: GREEN';
  }
}

function updateConnectionStatus() {
  const anyConnected = Object.values(STATE.connected).some(Boolean);
  const allConnected = Object.values(STATE.connected).filter(Boolean).length === Object.keys(MACHINES_META).length;
  const badge = document.getElementById('liveBadge');

  if (allConnected) {
    badge.className = 'live-badge';
    badge.innerHTML = '<div class="pulse-dot"></div>LIVE';
  } else if (anyConnected) {
    badge.className = 'live-badge connecting';
    badge.innerHTML = '<div class="pulse-dot"></div>PARTIAL';
  } else {
    badge.className = 'live-badge error';
    badge.innerHTML = '<div class="pulse-dot"></div>OFFLINE';
  }
}

// ── REST — poll /maintenance for work order sync ───────────────────
async function pollMaintenance() {
  try {
    const res  = await fetch(`${API}/maintenance`);
    const data = await res.json();
    const tc   = document.getElementById('ticketsContainer');
    if (!data.tickets || data.tickets.length === 0) return;

    tc.innerHTML = '';
    for (const t of data.tickets.slice(0, 5)) {
      const item = document.createElement('div');
      item.className = 'ticket-item';
      item.innerHTML = `
        <span class="ticket-id">${t.ticket_id}</span>
        <span class="ticket-machine">${t.machine_id} — ${t.machine_name}</span>
        <span class="ticket-priority priority-${t.priority}">${t.priority}</span>
      `;
      tc.appendChild(item);
    }
  } catch { /* server may not be running yet */ }
}

// ── Boot ───────────────────────────────────────────────────────────
(async function boot() {
  initChart();
  buildSidebarCards();

  // Try to get snapshot first, then open streams
  await fetchDashboard();

  for (const mid of Object.keys(MACHINES_META)) {
    connectMachineStream(mid);
  }
  connectAlertStream();

  // Poll maintenance tickets every 15s
  pollMaintenance();
  setInterval(pollMaintenance, 15000);

  // Refresh alerts timestamps every 30s
  setInterval(() => {
    document.querySelectorAll('.alert-time').forEach(el => {
      const match = el.textContent.match(/^(.+?) · risk/);
      // timestamps are relative so we don't re-compute here (they'd need ISO stored)
    });
  }, 30000);

  console.log('[ForgeSight] Dashboard booted. Connecting to', API);
})();
