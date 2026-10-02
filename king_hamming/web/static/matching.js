// Matching: live runs, the fields waiting for matching, and every past run's
// phase-by-phase convergence (requests still unmatched after each phase).
import {
  html, setHTML, field, fmtInt, fmtCompact, fmtBytes, fmtDuration, fmtPoly, fmtTime, ago, pct, bar,
  tooltips, now, gpuName,
} from './util.js';
import { button } from './command.js';

let selected = null; // run_id of the expanded history row

const label = (f) => (f ? field(f[0], f[1]) : '?');

// Points [phase, unmatched] starting from "before phase 1" (everything unmatched).
function points(run) {
  const series = [[0, run.total]];
  run.phases.forEach(([phase, done]) => series.push([phase, run.total - done]));
  return series;
}

function axisLabel(value) {
  const units = [[1e9, 'B'], [1e6, 'M'], [1e3, 'k']];
  const unit = units.find(([size]) => value >= size);
  return unit ? `${value / unit[0]}${unit[1]}` : String(value);
}

// Near completion a rounded percentage hides what is left; show enough digits.
function matchedPct(done, total) {
  if (!total) return '—';
  const fraction = done / total;
  if (done >= total) return '100%';
  const digits = fraction < 0.99 ? 0 : Math.min(6, Math.ceil(-Math.log10(1 - fraction)));
  return `${(Math.floor(fraction * 100 * 10 ** digits) / 10 ** digits).toFixed(digits)}%`;
}

// Log-scale line of unmatched requests per phase. Zero (fully matched) sits on
// the baseline with its own marker, since log(0) is undefined.
function convergence(run, index) {
  if (!run.total) return '';
  const data = points(run);
  if (run.state === 'running' && run.done !== null && data.length) {
    const last = data[data.length - 1];
    const live = run.total - run.done;
    if (live < last[1]) data.push([last[0] + 1, live, true]);
  }
  const W = 560; const H = 210; const L = 52; const R = 16; const T = 14; const B = 34;
  const maxPhase = Math.max(1, ...data.map((d) => d[0]));
  const top = Math.ceil(Math.log10(Math.max(10, run.total)));
  const x = (phase) => L + (phase / maxPhase) * (W - L - R);
  const y = (value) => (value <= 0 ? H - B : T + (1 - Math.log10(Math.max(1, value)) / top) * (H - T - B - 12));
  const yTicks = [];
  const step = Math.max(1, Math.ceil(top / 5));
  for (let k = step; k <= top; k += step) yTicks.push(10 ** k);
  const xStep = Math.max(1, Math.ceil(maxPhase / 10));
  const xTicks = [];
  for (let p = 0; p <= maxPhase; p += xStep) xTicks.push(p);
  const solid = data.filter((d) => !d[2]);
  const path = solid.map((d, i) => `${i ? 'L' : 'M'}${x(d[0]).toFixed(1)},${y(d[1]).toFixed(1)}`).join(' ');
  const live = data.find((d) => d[2]);
  const end = solid[solid.length - 1];
  const tip = live || end;
  return html`<svg class="conv" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Requests still unmatched after each phase, ${label(run.field)}">
    ${yTicks.map((v) => html`<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"></line>
      <text class="axis" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${axisLabel(v)}</text>`)}
    <line class="grid zero" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"></line>
    <text class="axis" x="${L - 6}" y="${H - B + 4}" text-anchor="end">0</text>
    ${xTicks.map((p) => html`<text class="axis" x="${x(p)}" y="${H - B + 18}" text-anchor="middle">${p}</text>`)}
    <text class="axis" x="${(L + W - R) / 2}" y="${H - 2}" text-anchor="middle">phase</text>
    <path class="conv-line" d="${path}"></path>
    ${live ? html`<path class="conv-line live" d="M${x(end[0])},${y(end[1])} L${x(live[0])},${y(live[1])}"></path>` : ''}
    ${data.map((d, i) => html`<circle class="conv-dot ${d[1] <= 0 ? 'done' : ''} ${d[2] ? 'live' : ''}"
        cx="${x(d[0])}" cy="${y(d[1])}" r="4"></circle>
      <circle class="conv-hit" cx="${x(d[0])}" cy="${y(d[1])}" r="11" data-run="${index}" data-point="${i}"></circle>`)}
    ${tip ? html`<text class="conv-end" x="${x(tip[0]) > W - 90 ? x(tip[0]) - 8 : x(tip[0]) + 8}" y="${y(tip[1]) - 10}"
        text-anchor="${x(tip[0]) > W - 90 ? 'end' : 'start'}">${tip[1] <= 0 ? 'all matched'
          : `${fmtInt(tip[1])} left${live ? ' now' : ''}`}</text>` : ''}
  </svg>`;
}

function phaseTable(run) {
  const data = points(run);
  return html`<details class="phase-table"><summary>Phase table</summary>
    <table class="mini"><thead><tr><th>Phase</th><th>Matched</th><th>Unmatched</th><th>Committed</th></tr></thead>
    <tbody>${data.map(([phase, unmatched], i) => html`<tr><td>${phase || 'start'}</td>
      <td>${matchedPct(run.total - unmatched, run.total)}</td><td>${fmtInt(unmatched)}</td>
      <td>${i ? fmtTime(run.phases[i - 1][2]) : fmtTime(run.started)}</td></tr>`)}</tbody></table></details>`;
}

const GPU_STAGES = [['field', 'field build (CPU)'], ['upload', 'upload'], ['greedy', 'greedy'],
  ['augment', 'augmenting'], ['output', 'payload write']];

// GPU runs finish in seconds and keep no phase checkpoints; show the stage timings instead.
function gpuSummary(run) {
  const gpu = run.gpu || {};
  const seconds = gpu.seconds || {};
  const stages = GPU_STAGES.filter(([key]) => typeof seconds[key] === 'number');
  return html`<div class="gpu-summary">
    <p>${gpu.device ? html`<span class="gpu-tag">${gpuName(gpu.device)}</span>` : html`<span class="gpu-tag">GPU</span>`}
      ${gpu.phases !== null && gpu.phases !== undefined ? html` · ${gpu.phases} augmenting phase${gpu.phases === 1 ? '' : 's'} after greedy` : ''}
      ${gpu.scans ? html` · ${fmtCompact(gpu.scans)} edge scans` : ''}</p>
    ${stages.length ? html`<table class="mini"><thead><tr><th>Stage</th><th>Seconds</th></tr></thead>
      <tbody>${stages.map(([key, name]) => html`<tr><td>${name}</td><td>${seconds[key].toFixed(2)}</td></tr>`)}</tbody></table>`
      : html`<p class="hint">${run.state === 'complete' ? 'Stage timings were not recorded.' : 'Single GPU run: no per-phase checkpoints; it finishes in seconds to minutes.'}</p>`}
  </div>`;
}

function engineText(run) {
  if (run.engine === 'gpu') return html`<span class="gpu-tag">GPU${run.gpu && run.gpu.index !== null && run.gpu.index !== undefined ? ` ${run.gpu.index}` : ''}</span>`;
  return `CPU · ${run.machines.length || run.workers} machine${(run.machines.length || run.workers) === 1 ? '' : 's'}`;
}

function resources(run) {
  if (!run.resources.length) return '';
  return html`<table class="mini"><thead><tr><th>Machine</th><th>Role</th><th>CPU time</th><th>Peak memory</th></tr></thead>
    <tbody>${run.resources.map((r) => html`<tr><td>${r.machine}</td>
      <td>${r.component === 'coordinator' ? 'coordinator' : r.component === 'solver' ? 'solver' : `shard ${r.shard}`}</td>
      <td>${fmtDuration(r.cpu_seconds)}</td><td>${fmtBytes(r.peak_rss)}</td></tr>`)}</tbody></table>`;
}

function liveCard(run, index, generatedAt) {
  const fraction = run.total ? run.done / run.total : 0;
  const lastPhase = run.phases.length ? run.phases[run.phases.length - 1][0] : 0;
  return html`<article class="card match-card">
    <header class="root-head">
      <h3>${label(run.field)} <span class="state state-${run.state}">${run.state}</span>
        <span class="health health-${run.health === 'responding' ? 'ok' : 'warn'}">${run.health}</span></h3>
      <div class="root-sub">polynomial <span class="mono">${fmtPoly(run.poly)}</span> ·
        ${run.engine === 'gpu' ? html`${engineText(run)} on ${run.machines.join(', ') || 'a GPU machine (waiting)'}`
          : html`${run.workers} machine${run.workers === 1 ? '' : 's'}: ${run.machines.join(', ') || 'waiting for partners'}`}</div>
    </header>
    <div class="match-progress">
      <div><b>${matchedPct(run.done, run.total)}</b> matched · ${fmtInt(run.total - run.done)} of ${fmtInt(run.total)} requests left</div>
      ${bar(fraction)}
      <div class="hint">${lastPhase} phase${lastPhase === 1 ? '' : 's'} committed ·
        ${run.started ? `running ${fmtDuration(generatedAt - run.started)}` : `queued ${ago(run.created)}`}
        ${run.last_progress_at ? ` · progress ${ago(run.last_progress_at)}` : ''}</div>
    </div>
    ${run.engine === 'gpu' ? gpuSummary(run) : html`<h4>Unmatched requests after each phase (log scale)</h4>
    ${convergence(run, index)}
    ${phaseTable(run)}`}
    ${resources(run)}
    <div class="cmd-row">${button('run.pause', { run_id: run.run_id }, 'Pause', 'small')}
      ${button('run.cancel', { run_id: run.run_id }, 'Cancel…', 'small')}</div>
  </article>`;
}

// Largest single-lease GPU memory advertised by a live node; null if no node reports GPUs.
function largestGpu(snapshot) {
  const all = (snapshot.fleet && snapshot.fleet.nodes) || [];
  if (!all.some((n) => Array.isArray(n.gpus))) return null;
  const nodes = all.filter((n) => n.state !== 'unavailable');
  let best = null;
  nodes.forEach((n) => (n.gpus || []).forEach((g) => {
    const usable = Math.max(0, g.total_bytes - 256 * 1024 * 1024);
    if (!best || usable > best.usable) best = { usable, name: gpuName(g.name), host: n.hostname };
  }));
  return best || { usable: 0 };
}

function waiting(snapshot) {
  const feeder = snapshot.feeder;
  if (!feeder) return '';
  const settings = feeder.settings;
  const gpu = largestGpu(snapshot);
  const gpuNote = (f) => {
    if (!f.gpu_bytes) return '';
    if (gpu === null) return ` · needs ${fmtBytes(f.gpu_bytes)} on a GPU (GPUs not reported yet)`;
    if (!gpu.usable) return ` · needs ${fmtBytes(f.gpu_bytes)} on a GPU; no GPU machine is online`;
    if (f.gpu_bytes <= gpu.usable) return ` · fits ${gpu.host}'s ${gpu.name} (${fmtBytes(f.gpu_bytes)})`;
    return ` · needs ${fmtBytes(f.gpu_bytes)} of GPU memory; largest GPU holds ${fmtBytes(gpu.usable)}`;
  };
  const rows = feeder.in_flight.filter((f) => f.dp_state === 'complete');
  if (!rows.length) return html`<p class="hint">Every completed DP has been matched or is matching now.</p>`;
  const why = (f) => {
    if (f.admission === 'admitted: single GPU') return `admitted · ${f.engine || 'single GPU'} (match_gpu)`;
    if (f.admission === 'field limit') return `q = ${fmtInt(f.q)} exceeds the CPU field limit of ${fmtInt(settings.max_field_elements)}${gpuNote(f)}`;
    if (f.admission === 'edge limit') return `${fmtCompact(f.edges)} edges exceed the limit of ${fmtCompact(settings.max_matching_edges)}`;
    if (f.admission === 'memory limit') return `needs more than ${fmtBytes(settings.max_matching_bytes)} per machine`;
    if (f.admission === 'waiting for nodes') return 'admitted; waiting for enough healthy machines';
    return f.admission || 'not yet considered';
  };
  return html`<div class="table-scroll"><table class="mini">
    <thead><tr><th>Field</th><th>Requests</th><th>Edges</th><th>Status</th></tr></thead>
    <tbody>${rows.map((f) => html`<tr><td><a href="#results/${f.field[0]},${f.field[1]}">${field(f.field[0], f.field[1])}</a></td>
      <td>${fmtCompact(f.requests)}</td><td>${fmtCompact(f.edges)}</td><td>${why(f)}</td></tr>`)}</tbody></table></div>
    <p class="hint">Fields that fit an advertised GPU are matched there (<span class="mono">match_gpu</span>);
      the CPU limits are feeder settings (Feeder tab). Matching memory grows with the field size,
      so raising them can exceed what one machine holds.</p>`;
}

function historyRow(run, index) {
  const duration = run.finished && run.started ? run.finished - run.started : null;
  const peak = Math.max(0, ...run.resources.map((r) => r.peak_rss));
  const open = selected === run.run_id;
  const outcome = run.outcome === 'matched' ? html`<span class="text-ok">matched</span>`
    : run.outcome === 'obstructed' ? html`<span class="state-obstructed">Hall obstruction · ${fmtInt(run.total - run.done)} short</span>`
      : html`<span class="state state-${run.state}">${run.state}</span>`;
  return html`<tr class="history-row ${open ? 'open' : ''}" data-run-id="${run.run_id}">
      <td><button type="button" class="link-button" aria-expanded="${open}">${open ? '▾' : '▸'} ${label(run.field)}</button></td>
      <td>${outcome}</td><td>${fmtCompact(run.total)}</td><td>${run.engine === 'gpu' && run.gpu && run.gpu.phases !== null && run.gpu.phases !== undefined ? run.gpu.phases : run.phases.length || '—'}</td>
      <td>${duration ? fmtDuration(duration) : '—'}</td><td>${engineText(run)}</td>
      <td>${peak ? fmtBytes(peak) : '—'}</td><td>${fmtTime(run.finished)}</td></tr>
    ${open ? html`<tr class="history-detail"><td colspan="8">
      <div class="root-sub">polynomial <span class="mono">${fmtPoly(run.poly)}</span> · machines ${run.machines.join(', ')}</div>
      ${run.error ? html`<p class="error-text">${run.error}</p>` : ''}
      ${run.engine === 'gpu' ? gpuSummary(run) : html`${convergence(run, index)}${phaseTable(run)}`}${resources(run)}</td></tr>` : ''}`;
}

export function render(container, snapshot, detail) {
  const matching = snapshot.matching;
  if (!matching) {
    setHTML(container, html`<p class="empty-note">Matching data is unavailable.</p>`);
    return;
  }
  if (detail) selected = detail;
  const runs = matching.runs;
  const live = runs.filter((r) => ['running', 'stopping', 'queued', 'waiting', 'paused'].includes(r.state));
  const past = runs.filter((r) => !live.includes(r));
  const matched = past.filter((r) => r.outcome === 'matched').length;
  const obstructed = past.filter((r) => r.outcome === 'obstructed').length;

  setHTML(container, html`
    <section class="panel">
      <div class="panel-head">
        <h2>Bipartite matching</h2>
        <p class="hint">Each matching run pairs every request with a field element for one primitive polynomial,
          in phases. CPU runs checkpoint every phase boundary; single-GPU runs finish in seconds and simply
          rerun if interrupted. A run ends with a full matching or a certified Hall obstruction.</p>
      </div>
      <h3 class="section-title">Now</h3>
      ${live.length ? html`<div class="match-grid">${live.map((r) => liveCard(r, runs.indexOf(r), snapshot.generated_at || now()))}</div>`
        : html`<p class="all-clear neutral">No matching is running.</p>`}
      <h3 class="section-title">Waiting for matching</h3>
      ${waiting(snapshot)}
      <h3 class="section-title">History <span class="hint">(${past.length} runs: ${matched} matched${obstructed ? `, ${obstructed} obstructed` : ''})</span></h3>
      <div class="table-scroll card"><table class="mini history">
        <thead><tr><th>Field</th><th>Outcome</th><th>Requests</th><th>Phases</th><th>Took</th>
          <th>Engine</th><th>Peak memory</th><th>Finished</th></tr></thead>
        <tbody>${past.map((r) => historyRow(r, runs.indexOf(r)))}</tbody></table></div>
    </section>`);

  container.querySelector('tbody') && container.querySelectorAll('tr.history-row').forEach((row) => {
    row.addEventListener('click', () => {
      selected = selected === row.dataset.runId ? null : row.dataset.runId;
      history.replaceState(null, '', selected ? `#matching/${selected}` : '#matching');
      render(container, snapshot, selected);
    });
  });

  tooltips(container, 'circle.conv-hit', (target) => {
    const run = runs[Number(target.dataset.run)];
    if (!run) return null;
    const data = points(run);
    const live = run.state === 'running' && Number(target.dataset.point) >= data.length;
    const point = live ? [null, run.total - run.done] : data[Number(target.dataset.point)];
    if (!point) return null;
    const unmatched = point[1];
    return html`<b>${label(run.field)}</b> · ${point[0] === null ? 'now (uncommitted)' : point[0] === 0 ? 'start' : `after phase ${point[0]}`}<br>
      ${fmtInt(unmatched)} unmatched · ${matchedPct(run.total - unmatched, run.total)} matched`;
  });
}
