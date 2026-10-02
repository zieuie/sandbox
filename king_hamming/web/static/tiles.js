// DP tile grids. The overview shows one small grid per root; clicking a root
// opens its focus view (#tiles/RUN or #tiles/RUN/ROW,COL) with a large grid,
// progress and pace, and a details panel for the selected tile.
import {
  html, setHTML, field, fmtDuration, fmtInt, fmtTime, pct, shortId, tooltips,
} from './util.js';
import { button, isOperator } from './command.js';

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
const DONE = new Set(['durable', 'complete']);
const CELL = 10;
let showFinished = false;

function rootActions(root) {
  const run = { run_id: root.run_id };
  const terminal = ['complete', 'failed', 'cancelled'].includes(root.state);
  const buttons = [];
  if (!terminal) {
    buttons.push(root.state === 'paused' ? button('run.resume', run, 'Resume field')
      : button('run.pause', run, 'Pause field'));
    buttons.push(button('run.priority', run, 'Priority…'));
    buttons.push(button('root.restart', run, 'Restart field…'));
    buttons.push(button('run.cancel', run, 'Cancel field…', 'danger'));
  } else {
    if (root.orphaned_children) buttons.push(button('root.cancel_leftovers', run, 'Cancel leftover tiles'));
    if (root.state !== 'complete' && root.attempt === root.attempts) buttons.push(button('root.restart', run, 'Restart field…'));
  }
  return buttons.length ? html`<div class="cmd-row">${buttons}</div>` : '';
}

function tileLines(root, c) {
  const lines = [html`<b>${field(root.p, root.r)} tile ${c.r},${c.c}</b> · ${TILE_STATES[c.s] || c.s}`];
  if (c.node) lines.push(html`machine ${c.node}`);
  if (c.s === 'running' && c.total) lines.push(html`${c.phase} · ${pct(c.done / c.total)}`);
  if (c.t0) lines.push(html`started ${fmtTime(c.t0)}${c.t1 ? html` · took ${fmtDuration(c.t1 - c.t0)}` : ''}${c.gpu ? html` · <span class="gpu-tag">GPU</span>` : ''}`);
  if (c.run) lines.push(html`run ${shortId(c.run)} · attempt ${c.att || 1} · ${c.rep} live cop${c.rep === 1 ? 'y' : 'ies'}`);
  if (c.err) lines.push(html`<span class="error-text">${c.err}</span>`);
  return lines;
}

// Grid SVG. In focus mode it gets row/column numbers, longer machine labels and
// a selection outline; `margin` is the space for the numbers, in cell units.
function grid(root, index, { focus = false, selected = null } = {}) {
  const margin = focus ? 3 : 0;
  const offset = margin * CELL;
  const step = root.columns > 60 ? 10 : 5;
  const cells = root.cells.map((c, i) => {
    const x = offset + c.c * CELL;
    const y = offset + c.r * CELL;
    const label = c.s === 'running' && c.node
      ? html`<text class="tile-label" x="${x + CELL / 2}" y="${y + CELL / 2 + 1.4}">${c.node.slice(0, focus ? 3 : 2)}</text>` : '';
    const isSelected = selected && selected.r === c.r && selected.c === c.c;
    return html`<rect class="tile t-${c.s}${isSelected ? ' selected' : ''}" x="${x + 0.5}" y="${y + 0.5}"
      width="${CELL - 1}" height="${CELL - 1}" rx="1.2" data-root="${index}" data-cell="${i}"></rect>${label}`;
  });
  const numbers = [];
  if (focus) {
    for (let k = 0; k < Math.max(root.rows, root.columns); k += step) {
      if (k < root.columns) numbers.push(html`<text class="grid-number" x="${offset + k * CELL + CELL / 2}" y="${offset - 6}">${k}</text>`);
      if (k < root.rows) numbers.push(html`<text class="grid-number" x="${offset - 4}" y="${offset + k * CELL + CELL / 2 + 2}" text-anchor="end">${k}</text>`);
    }
  }
  const width = offset + root.columns * CELL;
  const height = offset + root.rows * CELL;
  return html`<svg class="tile-grid${focus ? ' focus' : ''}" viewBox="0 0 ${width} ${height}" role="img"
    aria-label="${field(root.p, root.r)} tile grid">${numbers}${cells}</svg>`;
}

function chips(root) {
  return Object.keys(TILE_STATES)
    .filter((key) => root.counts[key])
    .map((key) => html`<span class="chip"><span class="swatch t-${key}"></span>${fmtInt(root.counts[key])} ${key}</span>`);
}

// Time left from the typical tile, following the wave: tiles on one
// anti-diagonal (row + column) can run together, and each diagonal waits for
// the one before. So the time left is about
//   typical tile × Σ over unfinished diagonals of ⌈unfinished tiles there ÷ machines⌉.
// This stays sensible when the wave is narrow (start, end) or wide (middle).
// It assumes the field gets every healthy machine and excludes reconstruction.
function typicalEstimate(root, machines, roots = []) {
  const typical = typicalTile(root, roots);
  if (!typical || ['complete', 'failed', 'cancelled'].includes(root.state)) return null;
  const median = typical.seconds;
  const perDiagonal = new Map();
  root.cells.forEach((c) => {
    if (!DONE.has(c.s)) perDiagonal.set(c.r + c.c, (perDiagonal.get(c.r + c.c) || 0) + 1);
  });
  if (!perDiagonal.size) return null;
  const width = Math.max(1, machines);
  let steps = 0;
  perDiagonal.forEach((count) => { steps += Math.ceil(count / width); });
  const remaining = [...perDiagonal.values()].reduce((a, b) => a + b, 0);
  return { seconds: steps * median, remaining, diagonals: perDiagonal.size, machines: width, typical };
}

// This attempt's typical tile, or (when every finished tile so far was reused
// from an earlier attempt) the most recent earlier attempt's of the same field.
function typicalTile(root, roots) {
  if (root.median_tile_seconds) return { seconds: root.median_tile_seconds, from: null };
  const earlier = roots
    .filter((other) => other !== root && other.p === root.p && other.r === root.r && other.median_tile_seconds)
    .sort((a, b) => b.created - a.created)[0];
  return earlier ? { seconds: earlier.median_tile_seconds, from: earlier.attempt } : null;
}

function estimateText(estimate) {
  return `about ${fmtDuration(estimate.seconds)} left`;
}

function estimateTitle(root, estimate) {
  return `${fmtInt(estimate.remaining)} tiles left on ${fmtInt(estimate.diagonals)} diagonals × `
    + `${fmtDuration(estimate.typical.seconds)} typical tile`
    + `${estimate.typical.from ? ` (from attempt ${estimate.typical.from})` : ''}, with ${estimate.machines} machines; `
    + 'longer if other fields share them; excludes reconstruction';
}

// Healthy compute machines; if none are up right now, assume they all return.
function healthyMachines(snapshot) {
  const nodes = ((snapshot.fleet && snapshot.fleet.nodes) || []).filter((n) => n.compute_enabled !== false);
  return nodes.filter((n) => n.state !== 'unavailable').length || nodes.length;
}

function rootCard(root, index, machines, roots) {
  const estimate = typicalEstimate(root, machines, roots);
  const total = root.cells.length;
  const done = root.counts.durable + root.counts.complete;
  const attempt = root.attempt ? `attempt ${root.attempt} of ${root.attempts}` : '';
  return html`<article class="root-card ${root.active ? '' : 'root-finished'}">
    <header class="root-head">
      <h3><a class="root-link" href="#tiles/${root.run_id}">${field(root.p, root.r)}</a>
        <span class="state state-${root.state}">${root.state}</span></h3>
      <div class="root-sub">${attempt} · ${root.rows}×${root.columns} tiles of side ${root.tile_side} ·
        started ${fmtTime(root.created)}</div>
    </header>
    <div class="root-progress"><b>${pct(total ? done / total : 0)}</b> of ${fmtInt(total)} tiles complete
      ${estimate ? html` · typical tile ${fmtDuration(estimate.typical.seconds)}${estimate.typical.from ? ` (attempt ${estimate.typical.from})` : ''}` : ''}
      ${estimate ? html` · <span title="${estimateTitle(root, estimate)}">${estimateText(estimate)}</span>` : ''}
      ${root.gpu_tiles ? html` · <span class="gpu-tag" title="Tiles computed or computing on a GPU with kh_gpu_dp_tile (byte-identical to the CPU kernel)">${fmtInt(root.gpu_tiles)} tile${root.gpu_tiles === 1 ? '' : 's'} on GPU</span>` : ''}</div>
    <div class="chips">${chips(root)}</div>
    ${root.boundary && root.boundary !== 'clear' ? html`<p class="hint">Frontier: tile ${root.boundary}</p>` : ''}
    ${root.orphaned_children ? html`<p class="alert">This root is ${root.state}, but ${root.orphaned_children}
      of its tiles are still running or queued.</p>` : ''}
    ${root.error ? html`<p class="error-text">${root.error}</p>` : ''}
    <a class="grid-link" href="#tiles/${root.run_id}" title="Open ${field(root.p, root.r)}">${grid(root, index)}</a>
    <div class="card-foot">${rootActions(root)}<a class="cmd small open-link" href="#tiles/${root.run_id}">Open ↗</a></div>
  </article>`;
}

// Pace and sweep position from the cells' own start/finish times.
function pace(root, now) {
  const finished = root.cells.filter((c) => DONE.has(c.s) && c.t1 && c.t0 && c.t1 - c.t0 >= 1);
  const lastHour = finished.filter((c) => c.t1 > now - 3600).length;
  const remaining = root.cells.filter((c) => !DONE.has(c.s)).length;
  const done = new Set(root.cells.filter((c) => DONE.has(c.s)).map((c) => c.r + c.c));
  // Swept through diagonal k: every tile with row + column <= k is done.
  const byDiagonal = new Map();
  root.cells.forEach((c) => {
    const k = c.r + c.c;
    byDiagonal.set(k, (byDiagonal.get(k) ?? true) && DONE.has(c.s));
  });
  let swept = -1;
  while (byDiagonal.get(swept + 1)) swept += 1;
  const running = root.cells.filter((c) => c.s === 'running').map((c) => c.r + c.c);
  return {
    lastHour, remaining, swept, diagonals: root.rows + root.columns - 1,
    front: running.length ? [Math.min(...running), Math.max(...running)] : null,
    eta: lastHour ? (remaining / lastHour) * 3600 : null, any: done.size > 0,
  };
}

function focusView(outer, snapshot, roots, root, selected) {
  // A fresh element per redraw, so its listeners never pile up.
  const container = document.createElement('div');
  outer.replaceChildren(container);
  const index = roots.indexOf(root);
  const total = root.cells.length;
  const done = root.counts.durable + root.counts.complete;
  const stats = pace(root, snapshot.generated_at);
  const typical = typicalEstimate(root, healthyMachines(snapshot), roots);
  const cell = selected && root.cells.find((c) => c.r === selected.r && c.c === selected.c);
  const detail = cell ? html`
      <div class="card tile-detail">
        <h3>Tile ${cell.r},${cell.c}</h3>
        <p>${tileLines(root, cell).map((line, i) => html`${i ? html`<br>` : ''}${line}`)}</p>
        ${cell.run && ['running', 'queued', 'paused'].includes(cell.s) ? html`<div class="cmd-row">
          ${cell.s === 'paused' ? button('run.resume', { run_id: cell.run }, 'Resume tile', 'small')
            : button('run.pause', { run_id: cell.run }, 'Pause tile', 'small')}
          ${button('run.priority', { run_id: cell.run }, 'Priority…', 'small')}</div>` : ''}
      </div>`
    : html`<div class="card tile-detail"><p class="hint">Click a tile to see its details${isOperator() ? ' and actions' : ''}.</p></div>`;

  setHTML(container, html`
    <section class="panel">
      <div class="focus-head">
        <a class="back-link" href="#tiles">← All DP roots</a>
        <h2>${field(root.p, root.r)} <span class="state state-${root.state}">${root.state}</span></h2>
        <div class="root-sub">${root.attempt ? `attempt ${root.attempt} of ${root.attempts} · ` : ''}${root.rows}×${root.columns}
          tiles of side ${root.tile_side} · started ${fmtTime(root.created)}</div>
        ${rootActions(root)}
      </div>
      ${root.orphaned_children ? html`<p class="alert">This root is ${root.state}, but ${root.orphaned_children}
        of its tiles are still running or queued.</p>` : ''}
      ${root.error ? html`<p class="error-text">${root.error}</p>` : ''}
      <div class="focus-body">
        <div class="focus-grid">${grid(root, index, { focus: true, selected })}</div>
        <aside class="focus-side">
          <div class="card">
            <div class="big-number">${pct(total ? done / total : 0)}</div>
            <div class="hint">${fmtInt(done)} of ${fmtInt(total)} tiles complete</div>
            ${bar2(total ? done / total : 0)}
            <dl class="facts">
              <dt>Last hour</dt><dd>${fmtInt(stats.lastHour)} tiles</dd>
              <dt>At that pace</dt><dd>${stats.eta !== null ? html`about ${fmtDuration(stats.eta)} left` : '—'}</dd>
              <dt>Typical tile</dt><dd>${typical ? html`${fmtDuration(typical.typical.seconds)}${typical.typical.from
                ? html` <span class="hint">(attempt ${typical.typical.from})</span>` : ''}` : '—'}</dd>
              <dt>From typical tile</dt><dd>${typical ? html`<span title="${estimateTitle(root, typical)}">${estimateText(typical)}</span>
                <div class="hint">${fmtInt(typical.remaining)} tiles on ${fmtInt(typical.diagonals)} diagonals,
                  ${typical.machines} machines</div>` : '—'}</dd>
              <dt>Swept through</dt><dd>${stats.swept >= 0 ? `diagonal ${stats.swept} of ${stats.diagonals - 1}` : 'not started'}</dd>
              <dt>Running on</dt><dd>${stats.front ? `diagonals ${stats.front[0]}–${stats.front[1]}` : '—'}</dd>
              ${root.boundary && root.boundary !== 'clear' ? html`<dt>Frontier</dt><dd>tile ${root.boundary}</dd>` : ''}
            </dl>
            <p class="hint">Tiles depend on earlier rows and columns, so work moves as a wave along
              anti-diagonals (row + column). The pace is usually slower at the start and end of the
              wave, when fewer tiles are ready at once. Both estimates leave out the final
              reconstruction step.</p>
          </div>
          <div class="chips">${chips(root)}</div>
          ${detail}
        </aside>
      </div>
    </section>`);

  container.addEventListener('click', (event) => {
    const target = event.target.closest('rect.tile');
    if (!target) return;
    const c = root.cells[Number(target.dataset.cell)];
    const same = selected && selected.r === c.r && selected.c === c.c;
    history.replaceState(null, '', same ? `#tiles/${root.run_id}` : `#tiles/${root.run_id}/${c.r},${c.c}`);
    focusView(outer, snapshot, roots, root, same ? null : { r: c.r, c: c.c });
  });
  tooltips(container, 'rect.tile', (target) => {
    const c = root.cells[Number(target.dataset.cell)];
    return c ? html`${tileLines(root, c).map((line, i) => html`${i ? html`<br>` : ''}${line}`)}` : null;
  });
}

// A thin progress bar without importing bar() styles that use --wc.
function bar2(fraction) {
  const width = Math.max(0, Math.min(1, fraction)) * 100;
  return html`<svg class="bar" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">
    <rect class="bar-track" x="0" y="0" width="100" height="6" rx="3"></rect>
    <rect class="bar-fill done-fill" x="0" y="0" width="${width.toFixed(2)}" height="6" rx="3"></rect></svg>`;
}

export function render(container, snapshot, detail) {
  const roots = snapshot.roots;
  if (!roots) {
    setHTML(container, html`<p class="empty-note">Tile data is unavailable.</p>`);
    return;
  }
  if (detail) {
    const [runId, position] = detail.split('/');
    const root = roots.find((r) => r.run_id === runId);
    if (!root) {
      setHTML(container, html`<section class="panel"><a class="back-link" href="#tiles">← All DP roots</a>
        <p class="empty-note">That DP root is no longer shown (only active roots and those finished in the
        last 24 hours are).</p></section>`);
      return;
    }
    const [r, c] = (position || '').split(',').map(Number);
    focusView(container, snapshot, roots, root, position && Number.isInteger(r) && Number.isInteger(c) ? { r, c } : null);
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
          Click a root to open it full size with its pace and per-tile details.</p>
      </div>
      <div class="legend">${legend}</div>
      ${active.length ? html`<div class="root-grid">${active.map((r) => rootCard(r, roots.indexOf(r), healthyMachines(snapshot), roots))}</div>`
        : html`<p class="empty-note">No DP root is active.</p>`}
      ${finished.length ? html`
        <details class="finished" ${showFinished ? 'open' : ''}>
          <summary>Finished in the last 24 hours (${finished.length})</summary>
          <div class="root-grid">${finished.map((r) => rootCard(r, roots.indexOf(r), healthyMachines(snapshot), roots))}</div>
        </details>` : ''}
    </section>`);

  const details = container.querySelector('details.finished');
  if (details) details.addEventListener('toggle', () => { showFinished = details.open; });

  // Clicking a tile in the overview opens the root with that tile selected.
  container.addEventListener('click', (event) => {
    const target = event.target.closest('rect.tile');
    if (!target) return;
    event.preventDefault();
    const root = roots[Number(target.dataset.root)];
    const c = root.cells[Number(target.dataset.cell)];
    location.hash = `#tiles/${root.run_id}/${c.r},${c.c}`;
  });

  tooltips(container, 'rect.tile', (target) => {
    const root = roots[Number(target.dataset.root)];
    const c = root && root.cells[Number(target.dataset.cell)];
    if (!c) return null;
    return html`${tileLines(root, c).map((line, i) => html`${i ? html`<br>` : ''}${line}`)}<br><i>click to open</i>`;
  });
}
