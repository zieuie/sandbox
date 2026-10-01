// Problems feed: what needs attention now, then recent events grouped by kind.
import { html, setHTML, fmtTime, ago, fmtDuration } from './util.js';
import { button } from './command.js';

const SEVERITIES = ['critical', 'warning', 'info'];
const STORAGE_FILTER = 'kh.problems.filter';
const STORAGE_SEEN = 'kh.problems.seen';

function load(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : JSON.parse(value);
  } catch (error) { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (error) { /* storage unavailable */ }
}

let shown = load(STORAGE_FILTER, { critical: true, warning: true, info: false });
let seenBefore = null; // last visit, read once per page load

function when(entry) {
  if (entry.time) return html`<span title="${entry.after ? `first seen between ${fmtTime(entry.after)} and ${fmtTime(entry.time)}` : fmtTime(entry.time)}">${ago(entry.time)}</span>`;
  return entry.from_log ? html`<span title="Logged before the dashboard started watching; the log has no timestamps">before watching</span>` : '';
}

function activeCard(entry) {
  return html`<li class="problem sev-${entry.severity}">
    <div class="problem-main">
      <span class="sev-dot"></span>
      <div><div class="problem-title">${entry.title}</div>
        ${entry.detail ? html`<div class="problem-detail">${entry.detail}</div>` : ''}</div>
    </div>
    <div class="problem-side">${entry.time ? html`since ${when(entry)}` : ''}
      ${entry.action ? button(entry.action.command, entry.action.params, entry.action.label, 'small') : ''}
      ${entry.link ? html`<a href="${entry.link}">View →</a>` : ''}</div>
  </li>`;
}

function group(entries) {
  const rank = { critical: 0, warning: 1, info: 2 };
  const severity = entries.reduce((best, e) => (rank[e.severity] < rank[best] ? e.severity : best), 'info');
  const timed = entries.filter((e) => e.time).sort((a, b) => b.time - a.time);
  const latest = timed[0] || entries[entries.length - 1];
  const oldest = timed[timed.length - 1];
  return { key: latest.group, severity, entries: timed.concat(entries.filter((e) => !e.time)), latest, oldest,
    lastTime: latest.time || 0 };
}

function groupRow(g) {
  const isNew = seenBefore !== null && g.lastTime > seenBefore;
  const range = g.latest.time
    ? (g.oldest && g.oldest !== g.latest ? `${ago(g.latest.time)} · first ${ago(g.oldest.time)}` : ago(g.latest.time))
    : 'before watching';
  const items = g.entries.slice(0, 100).map((e) => html`<li>
    <span class="event-time">${e.time ? fmtTime(e.time) : '—'}</span>
    <span>${e.title}${e.detail ? html` <span class="hint">— ${e.detail}</span>` : ''}</span></li>`);
  return html`<li class="event sev-${g.severity}">
    <details>
      <summary>
        <span class="sev-dot"></span>
        <span class="event-title">${g.latest.title}${isNew ? html` <span class="new-tag">new</span>` : ''}</span>
        ${g.entries.length > 1 ? html`<span class="pass-count">×${g.entries.length}</span>` : ''}
        <span class="event-when">${range}</span>
        ${g.latest.link ? html`<a class="event-link" href="${g.latest.link}">View →</a>` : ''}
      </summary>
      ${g.latest.detail ? html`<p class="problem-detail">${g.latest.detail}</p>` : ''}
      <ul class="event-items">${items}</ul>
      ${g.entries.length > 100 ? html`<p class="hint">${g.entries.length - 100} more not shown</p>` : ''}
    </details>
  </li>`;
}

export function render(container, snapshot) {
  const problems = snapshot.problems;
  if (!problems) {
    setHTML(container, html`<p class="empty-note">Problems data is unavailable.</p>`);
    return;
  }
  // First visit ever: nothing is "new" yet.
  if (seenBefore === null) seenBefore = load(STORAGE_SEEN, snapshot.generated_at);
  save(STORAGE_SEEN, snapshot.generated_at);

  const groups = new Map();
  problems.events.forEach((e) => {
    if (!groups.has(e.group)) groups.set(e.group, []);
    groups.get(e.group).push(e);
  });
  const all = [...groups.values()].map(group).sort((a, b) => b.lastTime - a.lastTime);
  const visible = all.filter((g) => shown[g.severity]);
  const active = problems.active.filter((e) => shown[e.severity] || e.severity !== 'info');
  const counts = {};
  all.forEach((g) => { counts[g.severity] = (counts[g.severity] || 0) + 1; });

  const toggles = SEVERITIES.map((s) => html`<label class="toggle sev-${s}">
    <input type="checkbox" data-sev="${s}" ${shown[s] ? 'checked' : ''}><span class="sev-dot"></span>${s}
    <b>${counts[s] || 0}</b></label>`);

  setHTML(container, html`
    <section class="panel">
      <div class="panel-head">
        <h2>Problems</h2>
        <p class="hint">Current conditions come from the live state. Events cover the last
          ${fmtDuration(problems.window_seconds)} of run history; log entries are timed from when the
          dashboard first saw them${problems.log_observed_since ? html` (watching since ${fmtTime(problems.log_observed_since)})` : ''}.</p>
      </div>
      <h3 class="section-title">Needs attention now</h3>
      ${active.length ? html`<ul class="problems">${active.map(activeCard)}</ul>`
        : html`<p class="all-clear">✓ Nothing needs attention right now.</p>`}
      <div class="toolbar"><h3 class="section-title">Recent events</h3><div class="toggles">${toggles}</div></div>
      ${visible.length ? html`<ul class="events">${visible.map(groupRow)}</ul>`
        : html`<p class="hint">No events at the selected severities.</p>`}
    </section>`);

  container.querySelectorAll('input[data-sev]').forEach((input) => {
    input.addEventListener('change', () => {
      shown = { ...shown, [input.dataset.sev]: input.checked };
      save(STORAGE_FILTER, shown);
      render(container, snapshot);
    });
  });
}

// Count for the tab badge: active critical and warning conditions.
export function badge(snapshot) {
  const problems = snapshot && snapshot.problems;
  if (!problems) return null;
  const { critical = 0, warning = 0 } = problems.counts;
  if (!critical && !warning) return null;
  return { count: critical + warning, severity: critical ? 'critical' : 'warning' };
}
