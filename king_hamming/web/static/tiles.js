// DP tile grids: one SVG per distributed DP root, coloured by tile state.
import {
  html, setHTML, field, fmtDuration, fmtInt, fmtTime, pct, shortId, tooltips,
} from './util.js';
import { button, isOperator, open } from './command.js';

function rootActions(root) {
  const run = { run_id: root.run_id };
  const terminal = ['complete', 'failed', 'cancelled'].includes(root.state);
  const buttons = [];
  if (!terminal) {
    buttons.push(root.state === 'paused' ? button('run.resume', run, 'Resume field')
      : button('run.pause', run, 'Pause field'));
    buttons.push(button('root.restart', run, 'Restart field…'));
    buttons.push(button('run.cancel', run, 'Cancel field…', 'danger'));
  } else {
    if (root.orphaned_children) buttons.push(button('root.cancel_leftovers', run, 'Cancel leftover tiles'));
    if (root.state !== 'complete' && root.attempt === root.attempts) buttons.push(button('root.restart', run, 'Restart field…'));
  }
  return buttons.length ? html`<div class="cmd-row">${buttons}</div>` : '';
}

export const TILE_STATES = {
  durable: 'Durable (2+ live copies)',
  complete: 'Complete, under-replicated',
  running: 'Running',
  paused: 'Paused',
  queued: 'Queued',
  ready: 'Ready, not yet queued',
  blocked: 'Blocked on dependencies',
  failed: 'Failed',
  cancelled: 'Cancelled',
  unscheduled: 'Never scheduled',
};

const CELL = 10;
let showFinished = false;

function grid(root, index) {
  const cells = root.cells.map((c, i) => {
    const x = c.c * CELL;
    const y = c.r * CELL;
    const label = c.s === 'running' && c.node
      ? html`<text class="tile-label" x="${x + CELL / 2}" y="${y + CELL / 2 + 1.4}">${c.node.slice(0, 2)}</text>` : '';
    return html`<rect class="tile t-${c.s}" x="${x + 0.5}" y="${y + 0.5}" width="${CELL - 1}" height="${CELL - 1}"
      rx="1.2" data-root="${index}" data-cell="${i}"></rect>${label}`;
  });
  const width = root.columns * CELL;
  const height = root.rows * CELL;
  return html`<svg class="tile-grid" viewBox="0 0 ${width} ${height}" role="img"
    aria-label="${field(root.p, root.r)} tile grid">${cells}</svg>`;
}

function rootCard(root, index) {
  const total = root.cells.length;
  const done = root.counts.durable + root.counts.complete;
  const chips = Object.keys(TILE_STATES)
    .filter((key) => root.counts[key])
    .map((key) => html`<span class="chip"><span class="swatch t-${key}"></span>${root.counts[key]} ${key}</span>`);
  const attempt = root.attempt ? `attempt ${root.attempt} of ${root.attempts}` : '';
  return html`<article class="root-card ${root.active ? '' : 'root-finished'}">
    <header class="root-head">
      <h3>${field(root.p, root.r)} <span class="state state-${root.state}">${root.state}</span></h3>
      <div class="root-sub">${attempt} · ${root.rows}×${root.columns} tiles of side ${root.tile_side} ·
        started ${fmtTime(root.created)}</div>
    </header>
    <div class="root-progress"><b>${pct(total ? done / total : 0)}</b> of ${fmtInt(total)} tiles complete
      ${root.median_tile_seconds ? html` · typical tile ${fmtDuration(root.median_tile_seconds)}` : ''}</div>
    <div class="chips">${chips}</div>
    ${root.boundary && root.boundary !== 'clear' ? html`<p class="hint">Frontier: tile ${root.boundary}</p>` : ''}
    ${root.orphaned_children ? html`<p class="alert">This root is ${root.state}, but ${root.orphaned_children}
      of its tiles are still running or queued.</p>` : ''}
    ${root.error ? html`<p class="error-text">${root.error}</p>` : ''}
    ${grid(root, index)}
    ${rootActions(root)}
  </article>`;
}

export function render(container, snapshot) {
  const roots = snapshot.roots;
  if (!roots) {
    setHTML(container, html`<p class="empty-note">Tile data is unavailable.</p>`);
    return;
  }
  const active = roots.filter((r) => r.active);
  const finished = roots.filter((r) => !r.active).reverse();
  const legend = Object.entries(TILE_STATES)
    .map(([key, label]) => html`<span class="legend-item"><span class="swatch t-${key}"></span>${label}</span>`);

  setHTML(container, html`
    <section class="panel">
      <div class="panel-head">
        <h2>Distributed DP roots</h2>
        <p class="hint">Rows and columns are tile coordinates; work sweeps along anti-diagonals.
          Running tiles show the first letters of their machine. Hover or tap a tile for details.</p>
      </div>
      <div class="legend">${legend}</div>
      ${active.length ? html`<div class="root-grid">${active.map((r) => rootCard(r, roots.indexOf(r)))}</div>`
        : html`<p class="empty-note">No DP root is active.</p>`}
      ${finished.length ? html`
        <details class="finished" ${showFinished ? 'open' : ''}>
          <summary>Finished in the last 24 hours (${finished.length})</summary>
          <div class="root-grid">${finished.map((r) => rootCard(r, roots.indexOf(r)))}</div>
        </details>` : ''}
    </section>`);

  container.addEventListener('click', (event) => {
    const target = event.target.closest('rect.tile');
    if (!target || !isOperator()) return;
    const c = roots[Number(target.dataset.root)].cells[Number(target.dataset.cell)];
    if (c && c.run && ['running', 'queued', 'paused'].includes(c.s)) {
      open(c.s === 'paused' ? 'run.resume' : 'run.pause', { run_id: c.run });
    }
  });

  const details = container.querySelector('details.finished');
  if (details) details.addEventListener('toggle', () => { showFinished = details.open; });

  tooltips(container, 'rect.tile', (target) => {
    const root = roots[Number(target.dataset.root)];
    const c = root && root.cells[Number(target.dataset.cell)];
    if (!c) return null;
    const lines = [html`<b>${field(root.p, root.r)} tile ${c.r},${c.c}</b> · ${TILE_STATES[c.s] || c.s}`];
    if (c.node) lines.push(html`machine ${c.node}`);
    if (c.s === 'running' && c.total) lines.push(html`${c.phase} · ${pct(c.done / c.total)}`);
    if (c.t0) lines.push(html`started ${fmtTime(c.t0)}${c.t1 ? html` · took ${fmtDuration(c.t1 - c.t0)}` : ''}`);
    if (c.run) lines.push(html`run ${shortId(c.run)} · attempt ${c.att || 1} · ${c.rep} live cop${c.rep === 1 ? 'y' : 'ies'}`);
    if (c.err) lines.push(html`<span class="error-text">${c.err}</span>`);
    if (isOperator() && ['running', 'queued', 'paused'].includes(c.s)) lines.push(html`<i>click to ${c.s === 'paused' ? 'resume' : 'pause'}</i>`);
    return html`${lines.map((line, i) => html`${i ? html`<br>` : ''}${line}`)}`;
  });
}
