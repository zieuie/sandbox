// Activity: process jobs with live output, the last rollout, and the audit log.
import { html, setHTML, api, ago, fmtTime, fmtDuration, now } from './util.js';
import { button, isOperator } from './command.js';

let pollTimer = null;
let lastContainer = null;

function jobCard(job) {
  const running = job.status === 'running';
  const duration = (job.finished || now()) - job.started;
  return html`<li class="job job-${job.status}">
    <details ${running ? 'open' : ''}>
      <summary>
        <span class="job-status">${job.status}</span>
        <span class="job-title">${job.title}</span>
        <span class="hint">by ${job.user} · ${ago(job.started)} · ${fmtDuration(duration)}${job.exit_code !== null && job.exit_code !== undefined ? ` · exit ${job.exit_code}` : ''}</span>
      </summary>
      ${job.tail !== undefined ? html`<pre class="job-log">${job.tail || '(no output yet)'}</pre>` : html`<p class="hint">Output is shown for the five most recent jobs.</p>`}
    </details>
  </li>`;
}

function auditRow(entry) {
  const what = entry.action === 'command'
    ? (entry.title || entry.command)
    : { login: 'Signed in', logout: 'Signed out', reauth: 'Confirmed password', password: 'Changed password',
      'add-user': 'Account created', 'remove-user': 'Account removed', 'set-role': 'Role changed',
      'revoke-sessions': 'Sessions revoked' }[entry.action] || entry.action;
  return html`<tr class="audit-${entry.outcome}">
    <td>${fmtTime(entry.time)}</td><td>${entry.user || '—'}</td><td>${what}</td>
    <td>${entry.outcome}${entry.error ? html`<div class="error-text">${entry.error}</div>` : ''}</td>
    <td class="mono">${entry.address || ''}</td></tr>`;
}

async function draw(container) {
  let data;
  let audit;
  try {
    [data, audit] = await Promise.all([api('/api/jobs'), api('/api/audit')]);
  } catch (error) {
    setHTML(container, html`<p class="empty-note">Activity is unavailable: ${error.message}</p>`);
    return;
  }
  if (!container.isConnected) return;
  const running = data.jobs.find((job) => job.status === 'running');
  const git = data.git || {};
  const rollout = data.rollout;
  const operator = isOperator();
  setHTML(container, html`
    <section class="panel">
      <div class="panel-head">
        <h2>Activity</h2>
        <p class="hint">Process jobs run detached from the dashboard and keep going if it restarts.
          Only one job runs at a time.</p>
      </div>
      ${operator ? html`<div class="card">
        <h3>Processes</h3>
        <div class="cmd-row">
          ${button('process.ensure_feeder', {}, 'Start feeder')}
          ${button('process.restart_feeder', {}, 'Restart feeder')}
          ${button('dispatch.stop', {}, 'Stop dispatch')}
          ${button('dispatch.resume', {}, 'Resume dispatch')}
          ${button('process.upgrade_workers', {}, 'Upgrade workers…', 'danger')}
          ${button('process.upgrade_leader', {}, 'Upgrade leader…', 'danger')}
        </div>
        <p class="hint">Upgrades deploy your working tree at <b>${git.head || '?'}</b>
          (${git.subject || 'unknown commit'})${git.dirty && git.dirty.length ? html` with <b>${git.dirty.length}</b> uncommitted change(s)` : ''}.
          They need every run idle: stop dispatch first and wait.</p>
        ${rollout ? html`<p class="hint">Last rollout: stage <b>${rollout.stage}</b>, started ${fmtTime(rollout.started)}.
          ${rollout.recovery ? html`<br>Recovery: ${rollout.recovery}` : ''}</p>` : ''}
      </div>` : ''}
      <div class="card">
        <h3>Jobs ${running ? html`<span class="hint">· updating every 3 s</span>` : ''}</h3>
        ${data.jobs.length ? html`<ul class="jobs">${data.jobs.map(jobCard)}</ul>` : html`<p class="hint">No jobs yet.</p>`}
      </div>
      <div class="card">
        <h3>Audit log</h3>
        <div class="table-scroll"><table class="mini audit">
          <thead><tr><th>When</th><th>User</th><th>What</th><th>Outcome</th><th>From</th></tr></thead>
          <tbody>${audit.entries.slice().reverse().map(auditRow)}</tbody></table></div>
      </div>
    </section>`);
  container.querySelectorAll('.job-log').forEach((pre) => { pre.scrollTop = pre.scrollHeight; });
  clearTimeout(pollTimer);
  if (running) {
    pollTimer = setTimeout(() => {
      if (lastContainer === container && container.isConnected && !document.hidden) draw(container);
    }, 3000);
  }
}

export function render(container) {
  lastContainer = container;
  if (!container.firstChild) setHTML(container, html`<p class="empty-note">Loading…</p>`);
  draw(container);
}
