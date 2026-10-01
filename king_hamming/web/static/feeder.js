// Feeder panel: process health, limits, backpressure, in-flight fields and recent passes.
import {
  html, setHTML, field, fmtBytes, fmtCompact, fmtDuration, fmtInt, fmtPoly, fmtTime, ago, bar, pct,
} from './util.js';

const SETTINGS = [
  ['target_dp_roots', 'Target active DP roots'],
  ['max_dp_roots', 'Hard cap on active DP roots'],
  ['target_ready_dp_tiles', 'Minimum ready-tile target'],
  ['max_dp_attempts', 'DP attempts per field'],
  ['max_visits', 'Max DP visits per field'],
  ['max_state_bytes', 'Max DP state'],
  ['dp_threads', 'DP threads'],
  ['tile_side', 'Tile side'],
  ['max_tile_bytes', 'Max tile memory'],
  ['max_ready_fields', 'Matching backlog limit'],
  ['matching_priority', 'Matching priority'],
  ['matching_workers', 'Full matching group'],
  ['matching_medium_workers', 'Medium matching group'],
  ['matching_small_workers', 'Small matching group'],
  ['matching_small_requests', 'Small group up to (requests)'],
  ['matching_medium_requests', 'Medium group up to (requests)'],
  ['matching_threads', 'Matching threads per machine'],
  ['max_matching_attempts', 'Matching attempts per polynomial'],
  ['matching_retry_seconds', 'Wait before matching retry'],
  ['max_matching_bytes', 'Max matching memory'],
  ['max_matching_edges', 'Max matching edges'],
  ['max_field_elements', 'Max field size (q)'],
  ['minimum_free_bytes', 'Leader disk watermark'],
];

function fmtSetting(key, value) {
  if (value === undefined || value === null) return '—';
  if (key.endsWith('_bytes')) return fmtBytes(value);
  if (key.endsWith('_seconds')) return fmtDuration(value);
  return value >= 100000 ? fmtCompact(value) : fmtInt(value);
}

function gauge(g) {
  const value = g.value ?? 0;
  const fraction = g.target ? value / g.target : 0;
  let tone = 'ok';
  if (g.floor) tone = value < g.target ? 'bad' : value < 2 * g.target ? 'warn' : 'ok';
  else if (g.limit) tone = value >= g.target ? 'warn' : 'ok';
  const shown = g.bytes ? `${fmtBytes(value)} free` : fmtInt(value);
  const target = g.bytes ? `watermark ${fmtBytes(g.target)}` : `of ${fmtInt(g.target)}`;
  return html`<div class="gauge tone-${tone}">
    <div class="gauge-label">${g.label}</div>
    <div class="gauge-value">${shown} <span>${target}</span></div>
    ${bar(g.floor ? Math.min(1, g.target / Math.max(1, value)) : Math.min(1, fraction), `tone-${tone}`)}
    <div class="gauge-note">${g.note}</div>
  </div>`;
}

function passSummary(result) {
  const parts = [];
  if (result.collected_dp) parts.push(`collected ${result.collected_dp} DP`);
  if (result.added_dp) parts.push(`added ${result.added_dp} root${result.added_dp === 1 ? '' : 's'}`);
  Object.entries(result.matching || {}).forEach(([key, n]) => {
    if (key !== 'inadmissible') parts.push(`${key.replace('_', ' ')} ${n}`);
  });
  return parts.length ? parts.join(' · ') : 'no changes';
}

function history(entries) {
  // Collapse runs of identical passes; newest first.
  const groups = [];
  entries.forEach((entry) => {
    const text = entry.kind === 'reconcile' ? passSummary(entry.result) : entry.message;
    const last = groups[groups.length - 1];
    if (last && last.kind === entry.kind && last.text === text) {
      last.count += 1;
      last.time = entry.time ?? last.time;
    } else {
      groups.push({ kind: entry.kind, text, count: 1, time: entry.time, first: entry.time });
    }
  });
  return groups.reverse().map((g) => html`<li class="pass ${g.kind === 'feeder_error' ? 'pass-error' : ''}">
    <span class="pass-time">${g.time ? ago(g.time) : 'earlier'}</span>
    <span class="pass-text">${g.kind === 'feeder_error' ? 'Error: ' : ''}${g.text}</span>
    ${g.count > 1 ? html`<span class="pass-count">×${g.count}</span>` : ''}
  </li>`);
}

function fieldRow(f) {
  const progress = f.dp_progress;
  const dp = f.dp_given_up
    ? html`<span class="state-failed">gave up</span> after ${f.dp_attempts} attempts`
    : html`<span class="state state-${f.dp_state}">${f.dp_state}</span>${f.dp_attempts > 1 ? html` · attempt ${f.dp_attempts}` : ''}`;
  const matching = f.matching_attempts
    ? html`${f.matching_state} · ${f.matching_attempts} attempt${f.matching_attempts === 1 ? '' : 's'}${f.poly ? html`<div class="mono">${fmtPoly(f.poly)}</div>` : ''}`
    : '—';
  return html`<tr class="${f.dp_given_up ? 'row-bad' : ''}">
    <td><a href="#results/${f.field[0]},${f.field[1]}">${field(f.field[0], f.field[1])}</a></td>
    <td>${dp}${progress ? html`<div class="mini-progress">${bar(progress.total ? progress.done / progress.total : 0)}
      <span>${fmtInt(progress.done)}/${fmtInt(progress.total)} tiles</span></div>` : ''}</td>
    <td>${f.requests ? fmtCompact(f.requests) : '—'}</td>
    <td>${matching}</td>
    <td>${f.admission || '—'}${f.engine ? html`<div class="hint">${f.engine}</div>` : ''}
      ${f.notes.map((n) => html`<div class="error-text">${n}</div>`)}</td>
  </tr>`;
}

export function render(container, snapshot) {
  const feeder = snapshot.feeder;
  if (!feeder) {
    setHTML(container, html`<p class="empty-note">This deployment has no pipeline.json, so there is no feeder to show.</p>`);
    return;
  }
  const p = feeder.process;
  const processCard = !p.recorded ? html`<p class="hint">No feeder_process.json recorded.</p>`
    : html`<div class="kv"><span>Process</span><b class="${p.alive ? 'text-ok' : 'text-bad'}">${p.alive ? `running (pid ${p.pid})` : `not running (pid ${p.pid} gone)`}</b></div>
      ${p.started ? html`<div class="kv"><span>Up for</span><b>${fmtDuration(snapshot.generated_at - p.started)}</b></div>` : ''}
      <div class="kv"><span>Pass interval</span><b>${fmtDuration(p.interval)}</b></div>`;
  const settings = feeder.settings;
  const upcoming = feeder.upcoming.length
    ? html`<ol class="upcoming">${feeder.upcoming.map((u) => html`<li>${field(u.field[0], u.field[1])} <span class="hint">q = ${fmtCompact(u.q)}</span></li>`)}</ol>`
    : html`<p class="hint">No new field fits the current limits (max ${fmtCompact(settings.max_visits)} visits,
        ${fmtBytes(settings.max_state_bytes)} state). Once the active roots finish, the feeder will report
        “frontier exhausted”.</p>`;

  setHTML(container, html`
    <section class="panel">
      <div class="panel-head">
        <h2>Feeder</h2>
        <p class="hint">The feeder collects finished DP, submits matching, and adds new DP roots every pass.
          Pass history is stamped with the time the dashboard first saw it${feeder.observing_since ? html` (watching since ${fmtTime(feeder.observing_since)})` : ''}.</p>
      </div>
      <div class="feeder-top">
        <div class="card">
          <h3>Status</h3>
          <div class="kv"><span>State</span><b>${feeder.state}</b></div>
          <div class="kv"><span>Last pass</span><b class="${feeder.stale ? 'text-bad' : ''}">${ago(feeder.last_reconcile)}</b></div>
          <div class="kv"><span>Policy</span><b>${feeder.policy}</b></div>
          ${processCard}
        </div>
        <div class="gauges">${feeder.gauges.map(gauge)}</div>
      </div>
      <div class="feeder-grid">
        <div class="card">
          <h3>Fields in flight <span class="hint">(${feeder.in_flight.length}; ${feeder.fields_complete} fully matched)</span></h3>
          <div class="table-scroll"><table class="mini fields">
            <thead><tr><th>Field</th><th>DP</th><th>Requests</th><th>Matching</th><th>Admission</th></tr></thead>
            <tbody>${feeder.in_flight.map(fieldRow)}</tbody></table></div>
        </div>
        <div class="side">
          <div class="card"><h3>Next fields</h3>${upcoming}</div>
          <div class="card"><h3>Recent passes</h3><ul class="passes">${history(feeder.history)}</ul></div>
        </div>
      </div>
      <details class="card settings">
        <summary><h3>Limits and settings</h3></summary>
        <dl class="facts">${SETTINGS.filter(([key]) => key in settings).map(([key, label]) =>
          html`<dt>${label}</dt><dd>${fmtSetting(key, settings[key])}</dd>`)}</dl>
      </details>
    </section>`);
}
