// App shell: fetch the snapshot, poll while visible, render the selected tab.
import {
  html, setHTML, fmtDuration, ago, now, hideTooltip, api, setCsrf,
} from './util.js';
import * as account from './account.js';
import * as command from './command.js';
import * as activity from './activity.js';
import * as results from './results.js';
import * as fleet from './fleet.js';
import * as tiles from './tiles.js';
import * as matching from './matching.js';
import * as timeline from './timeline.js';
import * as feeder from './feeder.js';
import * as problems from './problems.js';

const VIEWS = {
  results: { title: 'Results', module: results },
  fleet: { title: 'Fleet', module: fleet },
  tiles: { title: 'DP tiles', module: tiles },
  matching: { title: 'Matching', module: matching },
  timeline: { title: 'Timeline', module: timeline },
  feeder: { title: 'Feeder', module: feeder },
  problems: { title: 'Problems', module: problems },
  activity: { title: 'Activity', module: activity },
};
const POLL_SECONDS = 30;

const main = document.querySelector('main');
const nav = document.querySelector('nav.tabs');
const status = document.querySelector('.status');
const banner = document.querySelector('.banner');
const refreshButton = document.querySelector('button.refresh');
const updated = document.querySelector('.updated');

let snapshot = null;
let fetchError = null;
let loading = false;
let timer = null;

// The hash is "#view" or "#view/detail", e.g. #results/2,29.
function currentView() {
  const name = location.hash.slice(1).split('/')[0];
  return VIEWS[name] ? name : 'results';
}

function currentDetail() {
  const [, ...rest] = location.hash.slice(1).split('/');
  return rest.length ? decodeURIComponent(rest.join('/')) : null;
}

async function load(force = false) {
  if (loading) return;
  loading = true;
  refreshButton.disabled = true;
  refreshButton.classList.add('spinning');
  try {
    snapshot = await api(force ? '/api/refresh' : '/api/snapshot', { method: force ? 'POST' : 'GET' });
    fetchError = null;
  } catch (error) {
    if (error.message !== 'signing in again' && !error.message.startsWith('dashboard updated')) fetchError = error.message;
  } finally {
    loading = false;
    refreshButton.disabled = false;
    refreshButton.classList.remove('spinning');
  }
  render();
  schedule();
}

function schedule() {
  clearTimeout(timer);
  timer = setTimeout(() => (document.hidden ? schedule() : load()), POLL_SECONDS * 1000);
}

function renderStatus() {
  const s = snapshot && snapshot.status;
  if (!s) { setHTML(status, html``); return; }
  const feeder = s.feeder;
  const dispatchClass = s.dispatch === 'running' ? 'ok' : 'warn';
  const nodesClass = s.nodes_healthy === s.nodes_total ? 'ok' : 'bad';
  setHTML(status, html`
    <span class="pill pill-${dispatchClass}">Dispatch ${s.dispatch}</span>
    ${feeder ? html`<span class="pill">Feeder ${feeder.state} · ${ago(feeder.last_reconcile)}</span>` : ''}
    <span class="pill pill-${nodesClass}">${s.nodes_healthy}/${s.nodes_total} machines</span>
    <span class="pill">${s.runs.running} running · ${s.runs.queued} queued · ${s.runs.waiting} waiting</span>`);
}

function renderBanner() {
  const messages = [];
  if (fetchError) messages.push(html`<li>Could not reach the dashboard server: ${fetchError}</li>`);
  if (snapshot) {
    snapshot.warnings.forEach((w) => messages.push(html`<li><b>${w.section}:</b> ${w.message}</li>`));
    if (snapshot.stale.length) messages.push(html`<li>Showing older data for: ${snapshot.stale.join(', ')}</li>`);
  }
  banner.hidden = messages.length === 0;
  setHTML(banner, html`<ul>${messages}</ul>`);
}

function renderUpdated() {
  if (!snapshot) { updated.textContent = loading ? 'Loading…' : ''; return; }
  updated.textContent = `Data from ${fmtDuration(now() - snapshot.generated_at)} ago`;
  updated.title = `Built in ${snapshot.build_seconds}s`;
}

function renderNav(name) {
  const count = problems.badge(snapshot);
  setHTML(nav, html`${Object.entries(VIEWS).map(([key, v]) => html`<a href="#${key}" data-view="${key}"
    class="${key === name ? 'active' : ''}">${v.title}${key === 'problems' && count
      ? html`<span class="nav-badge sev-${count.severity}">${count.count}</span>` : ''}</a>`)}`);
}

function render() {
  const name = currentView();
  renderNav(name);
  document.title = `${VIEWS[name].title} · King Hamming`;
  renderStatus();
  renderBanner();
  renderUpdated();
  hideTooltip();
  if (!snapshot) {
    setHTML(main, html`<p class="empty-note">${fetchError ? 'No data yet.' : 'Loading…'}</p>`);
    return;
  }
  // A fresh container per render, so view event listeners never accumulate.
  const view = document.createElement('div');
  view.className = `view view-${name}`;
  main.replaceChildren(view);
  VIEWS[name].module.render(view, snapshot, currentDetail());
}

window.addEventListener('hashchange', render);
window.addEventListener('kh:changed', () => load());
command.install();
refreshButton.addEventListener('click', () => load(true));
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && snapshot && now() - snapshot.generated_at > POLL_SECONDS) load();
});
setInterval(renderUpdated, 1000);
render();
api('/api/session').then((session) => {
  setCsrf(session.csrf);
  account.mount(document.querySelector('.account'), session);
  load();
}).catch((error) => {
  if (error.message !== 'signed out' && error.message !== 'signing in again') { fetchError = error.message; render(); }
});
