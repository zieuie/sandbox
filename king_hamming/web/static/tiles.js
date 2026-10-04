// DP tile grids. The overview shows one small grid per root; clicking a root
// opens its focus view (#tiles/RUN or #tiles/RUN/ROW,COL) with a large grid,
// progress and pace, and a details panel for the selected tile.
//
// A root arrives as a grid string (one state code per tile, row-major) plus the
// few tiles in flight; grids are drawn on canvases, so 200,000 tiles are a few
// elements, not 200,000. The focus view fetches its root's per-tile detail
// (/api/tiles) and the selected tile's record (/api/tile) on demand.
import {
  api, html, setHTML, field, fmtDuration, fmtInt, fmtTime, pct, shortId, showTooltip, hideTooltip,
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
// Mirrors STATE_CODES in snapshot.py.
const CODES = {
  d: 'durable', c: 'complete', r: 'running', p: 'paused', q: 'queued', y: 'ready',
  b: 'blocked', f: 'failed', x: 'cancelled', u: 'unscheduled',
};
const DONE = new Set(['durable', 'complete']);
const NUMBER_MARGIN = 28;    // CSS pixels for row/column numbers around the focus grid
let showFinished = false;

// Zoom for the focus grid, kept across the page's periodic redraws along with the scroll position.
const ZOOMS = [1, 2, 3, 4, 6, 8];
let zoom = 1;
let focusScroll = { run: null, left: 0, top: 0 };

// Hovering a tile that is drawn small shows a magnified neighbourhood next to the details.
const LOUPE_RADIUS = 5;      // tiles on each side of the pointer: an 11 × 11 view
const LOUPE_CELL = 20;       // pixels per tile in the magnifier
const LOUPE_BELOW = 14;      // only needed while tiles are drawn smaller than this many pixels
const LABEL_FROM = 14;       // machine names are drawn on running tiles at least this large

// Per-root detail fetched for the focus view, by run id; refetched when the snapshot changes.
const details = new Map();   // run_id -> { generated, data }
const tileRecords = new Map(); // `${run}/${r},${c}` -> { generated, record }

// ----- reading a root --------------------------------------------------------------

const derived = new WeakMap();
function info(root) {
  if (!derived.has(root)) {
    const live = new Map((root.live || []).map((cell) => [`${cell.r},${cell.c}`, cell]));
    let total = 0;
    for (const code of root.grid) if (code !== '-') total += 1;
    derived.set(root, { live, total });
  }
  return derived.get(root);
}

function stateAt(root, r, c) {
  if (r < 0 || c < 0 || r >= root.rows || c >= root.columns) return null;
  return CODES[root.grid[r * root.columns + c]] || null;
}

// What is known about one tile: its state, the in-flight record, and the fetched detail.
function cellAt(root, r, c) {
  const s = stateAt(root, r, c);
  if (!s) return null;
  const cell = { r, c, s, ...(info(root).live.get(`${r},${c}`) || {}) };
  const entry = details.get(root.run_id);
  if (entry) {
    const d = entry.data;
    const at = r * root.columns + c;
    if (d.node[at] >= 0) cell.node = d.machines[d.node[at]];
    if (d.start[at] >= 0) {
      cell.t0 = d.base + d.start[at];
      if (d.took[at] >= 0) cell.t1 = cell.t0 + d.took[at];
    }
    cell.rep = d.copies[at];
    cell.att = d.attempt[at];
    if (d.gpu[at] === '1') cell.gpu = 1;
  }
  return cell;
}

// ----- drawing ---------------------------------------------------------------------

function colours() {
  const style = getComputedStyle(document.documentElement);
  const result = {};
  Object.keys(TILE_STATES).forEach((key) => { result[key] = style.getPropertyValue(`--t-${key}`).trim(); });
  result.ink = style.getPropertyValue('--ink').trim() || '#222';
  result.muted = style.getPropertyValue('--muted').trim() || '#888';
  return result;
}

// Geometry of a drawn grid in CSS pixels: where tile (r, c) is and how big.
function geometry(canvas, root, focus) {
  const margin = focus ? NUMBER_MARGIN : 0;
  const width = canvas.clientWidth;
  const cell = (width - margin) / root.columns;
  return { margin, cell, width, height: margin + cell * root.rows };
}

function draw(canvas, root, { focus = false, selected = null } = {}) {
  const geo = geometry(canvas, root, focus);
  if (!(geo.cell > 0)) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(geo.width * ratio);
  canvas.height = Math.round(geo.height * ratio);
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, geo.width, geo.height);
  const palette = colours();
  const gap = geo.cell >= 4 ? Math.min(1, geo.cell * 0.1) : 0;
  const size = geo.cell - gap;
  // One pass per state keeps fillStyle changes to ten.
  const byState = new Map();
  for (let at = 0; at < root.grid.length; at += 1) {
    const state = CODES[root.grid[at]];
    if (!state) continue;
    if (!byState.has(state)) byState.set(state, []);
    byState.get(state).push(at);
  }
  byState.forEach((indices, state) => {
    context.fillStyle = palette[state];
    for (const at of indices) {
      const r = Math.floor(at / root.columns);
      const c = at - r * root.columns;
      context.fillRect(geo.margin + c * geo.cell + gap / 2, geo.margin + r * geo.cell + gap / 2, size, size);
    }
  });
  if (geo.cell >= LABEL_FROM) {
    context.fillStyle = '#fff';
    context.font = `600 ${Math.max(8, Math.floor(geo.cell * 0.4))}px system-ui, sans-serif`;
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    for (const cell of root.live || []) {
      if (cell.s !== 'running' || !cell.node) continue;
      context.fillText(cell.node.slice(0, 3), geo.margin + (cell.c + 0.5) * geo.cell, geo.margin + (cell.r + 0.5) * geo.cell);
    }
  }
  if (focus) {
    context.fillStyle = palette.muted;
    context.font = '11px system-ui, sans-serif';
    const step = [1, 2, 5, 10, 20, 50, 100].find((k) => k * geo.cell >= 24) || 200;
    context.textAlign = 'center';
    context.textBaseline = 'bottom';
    for (let k = 0; k < root.columns; k += step) context.fillText(String(k), geo.margin + (k + 0.5) * geo.cell, geo.margin - 4);
    context.textAlign = 'right';
    context.textBaseline = 'middle';
    for (let k = 0; k < root.rows; k += step) context.fillText(String(k), geo.margin - 4, geo.margin + (k + 0.5) * geo.cell);
  }
  if (selected) {
    context.strokeStyle = palette.ink;
    context.lineWidth = Math.max(1.5, geo.cell * 0.12);
    context.strokeRect(geo.margin + selected.c * geo.cell, geo.margin + selected.r * geo.cell, geo.cell, geo.cell);
  }
}

// The tile under the pointer, or null.
function hit(canvas, root, event, focus) {
  const geo = geometry(canvas, root, focus);
  const box = canvas.getBoundingClientRect();
  const x = event.clientX - box.left - geo.margin;
  const y = event.clientY - box.top - geo.margin;
  if (x < 0 || y < 0) return null;
  const c = Math.floor(x / geo.cell);
  const r = Math.floor(y / geo.cell);
  return stateAt(root, r, c) ? { r, c, cell: geo.cell } : null;
}

// Redraw every grid canvas in `container` now and whenever its width changes. One observer at a
// time: the page re-renders every poll, and an observer keeps its old canvases alive.
let gridObserver = null;
function attachGrids(container, roots, options) {
  const canvases = [...container.querySelectorAll('canvas.tile-grid')];
  const redraw = (canvas) => draw(canvas, roots[Number(canvas.dataset.root)], options(canvas));
  canvases.forEach(redraw);
  if (gridObserver) gridObserver.disconnect();
  gridObserver = new ResizeObserver((entries) => entries.forEach((entry) => {
    if (entry.target.isConnected) redraw(entry.target);
  }));
  canvases.forEach((canvas) => gridObserver.observe(canvas));
}

function canvasFor(root, index, focus = false) {
  // The width/height attributes give the canvas its aspect ratio until it is drawn.
  const margin = focus ? NUMBER_MARGIN : 0;
  return html`<canvas class="tile-grid${focus ? ' focus' : ''}" data-root="${index}"
    width="${root.columns * 10 + margin}" height="${root.rows * 10 + margin}" role="img"
    aria-label="${field(root.p, root.r)} tile grid"></canvas>`;
}

// The tiles around `centre`, drawn large; the window slides to stay inside the grid.
function loupe(root, centre, selected) {
  const size = LOUPE_RADIUS * 2 + 1;
  const top = Math.max(0, Math.min(centre.r - LOUPE_RADIUS, root.rows - size));
  const left = Math.max(0, Math.min(centre.c - LOUPE_RADIUS, root.columns - size));
  const rows = Math.min(size, root.rows);
  const columns = Math.min(size, root.columns);
  const live = info(root).live;
  const cells = [];
  for (let r = top; r < top + rows; r += 1) {
    for (let c = left; c < left + columns; c += 1) {
      const s = stateAt(root, r, c);
      if (!s) continue;
      const x = (c - left) * LOUPE_CELL;
      const y = (r - top) * LOUPE_CELL;
      const mark = r === centre.r && c === centre.c ? ' centre'
        : selected && selected.r === r && selected.c === c ? ' selected' : '';
      const running = live.get(`${r},${c}`);
      cells.push(html`<rect class="t-${s}${mark}" x="${x + 1}" y="${y + 1}" width="${LOUPE_CELL - 2}"
        height="${LOUPE_CELL - 2}" rx="2.5"></rect>${s === 'running' && running && running.node
        ? html`<text class="loupe-label" x="${x + LOUPE_CELL / 2}" y="${y + LOUPE_CELL / 2 + 3}">${running.node.slice(0, 3)}</text>` : ''}`);
    }
  }
  return html`<svg class="loupe" viewBox="0 0 ${columns * LOUPE_CELL} ${rows * LOUPE_CELL}"
    width="${columns * LOUPE_CELL}" height="${rows * LOUPE_CELL}" aria-hidden="true">${cells}</svg>`;
}

function tileLines(root, c) {
  const lines = [html`<b>${field(root.p, root.r)} tile ${c.r},${c.c}</b> · ${TILE_STATES[c.s] || c.s}`];
  if (c.node) lines.push(html`machine ${c.node}`);
  if (c.s === 'running' && c.total) lines.push(html`${c.phase} · ${pct(c.done / c.total)}`);
  if (c.t0) lines.push(html`started ${fmtTime(c.t0)}${c.t1 ? html` · took ${fmtDuration(c.t1 - c.t0)}` : ''}${c.gpu ? html` · <span class="gpu-tag">GPU</span>` : ''}`);
  if (c.run) lines.push(html`run ${shortId(c.run)}`);
  if (c.att || c.rep !== undefined) {
    lines.push(html`attempt ${c.att || 1}${c.rep !== undefined ? html` · ${c.rep} live cop${c.rep === 1 ? 'y' : 'ies'}` : ''}`);
  }
  if (c.err) lines.push(html`<span class="error-text">${c.err}</span>`);
  return lines;
}

// Details for a hovered tile: a magnifier when the grid is drawn too small to read, then the facts.
function tileTip(root, c, drawn, selected, footer = '') {
  const lines = tileLines(root, c).map((line, i) => html`${i ? html`<br>` : ''}${line}`);
  return html`${drawn < LOUPE_BELOW ? loupe(root, c, selected) : ''}<div>${lines}${footer}</div>`;
}

// ----- summaries -------------------------------------------------------------------

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

function chips(root) {
  return Object.keys(TILE_STATES)
    .filter((key) => root.counts[key])
    .map((key) => html`<span class="chip"><span class="swatch t-${key}"></span>${fmtInt(root.counts[key])} ${key}</span>`);
}

// Unfinished tiles per anti-diagonal (row + column), from the grid string.
function unfinishedByDiagonal(root) {
  const perDiagonal = new Map();
  for (let at = 0; at < root.grid.length; at += 1) {
    const state = CODES[root.grid[at]];
    if (!state || DONE.has(state)) continue;
    const r = Math.floor(at / root.columns);
    const k = r + (at - r * root.columns);
    perDiagonal.set(k, (perDiagonal.get(k) || 0) + 1);
  }
  return perDiagonal;
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
  const perDiagonal = unfinishedByDiagonal(root);
  if (!perDiagonal.size) return null;
  const width = Math.max(1, machines);
  let steps = 0;
  perDiagonal.forEach((count) => { steps += Math.ceil(count / width); });
  const remaining = [...perDiagonal.values()].reduce((a, b) => a + b, 0);
  return { seconds: steps * typical.seconds, remaining, diagonals: perDiagonal.size, machines: width, typical };
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

// Tiles that finished more than once are work done twice (a lost or cleared copy, then a recompute).
function recomputedNote(root) {
  if (!root.recomputed) return '';
  const done = root.counts.durable + root.counts.complete;
  return html`<p class="hint" title="Complete tile runs the grid no longer points at: a tile whose copy was lost or cleared and then computed again.">${fmtInt(root.recomputed)} tile${root.recomputed === 1 ? '' : 's'} finished more than once${done ? html` (${pct(root.recomputed / (done + root.recomputed))} of all finished work)` : ''}</p>`;
}

function rootCard(root, index, machines, roots) {
  const estimate = typicalEstimate(root, machines, roots);
  const total = info(root).total;
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
    ${recomputedNote(root)}
    ${root.boundary && root.boundary !== 'clear' ? html`<p class="hint">Frontier: tile ${root.boundary}</p>` : ''}
    ${root.orphaned_children ? html`<p class="alert">This root is ${root.state}, but ${root.orphaned_children}
      of its tiles are still running or queued.</p>` : ''}
    ${root.error ? html`<p class="error-text">${root.error}</p>` : ''}
    <a class="grid-link" href="#tiles/${root.run_id}" aria-label="Open ${field(root.p, root.r)}">${canvasFor(root, index)}</a>
    <div class="card-foot">${rootActions(root)}<a class="cmd small open-link" href="#tiles/${root.run_id}">Open ↗</a></div>
  </article>`;
}

// Pace and sweep position from the grid and the server's count of tiles finished in the last hour.
function pace(root) {
  const unfinished = unfinishedByDiagonal(root);
  const remaining = [...unfinished.values()].reduce((a, b) => a + b, 0);
  // Swept through diagonal k: every tile with row + column <= k is done.
  let swept = -1;
  const diagonals = root.rows + root.columns - 1;
  while (swept + 1 < diagonals && !unfinished.has(swept + 1)) swept += 1;
  const running = (root.live || []).filter((c) => c.s === 'running').map((c) => c.r + c.c);
  const lastHour = root.finished_last_hour || 0;
  return {
    lastHour, remaining, swept, diagonals,
    front: running.length ? [Math.min(...running), Math.max(...running)] : null,
    eta: lastHour ? (remaining / lastHour) * 3600 : null,
  };
}

// A thin progress bar without importing bar() styles that use --wc.
function bar2(fraction) {
  const width = Math.max(0, Math.min(1, fraction)) * 100;
  return html`<svg class="bar" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">
    <rect class="bar-track" x="0" y="0" width="100" height="6" rx="3"></rect>
    <rect class="bar-fill done-fill" x="0" y="0" width="${width.toFixed(2)}" height="6" rx="3"></rect></svg>`;
}

// ----- focus view ------------------------------------------------------------------

function detailPanel(root, selected, generated) {
  if (!selected) {
    return html`<p class="hint">Click a tile to see its details${isOperator() ? ' and actions' : ''}.</p>`;
  }
  const entry = tileRecords.get(`${root.run_id}/${selected.r},${selected.c}`);
  const cell = entry && entry.generated === generated ? entry.record : cellAt(root, selected.r, selected.c);
  if (!cell) return html`<p class="hint">Tile ${selected.r},${selected.c} is not in this grid.</p>`;
  return html`<h3>Tile ${cell.r},${cell.c}</h3>
    <p>${tileLines(root, cell).map((line, i) => html`${i ? html`<br>` : ''}${line}`)}</p>
    ${cell.run && ['running', 'queued', 'paused'].includes(cell.s) ? html`<div class="cmd-row">
      ${cell.s === 'paused' ? button('run.resume', { run_id: cell.run }, 'Resume tile', 'small')
        : button('run.pause', { run_id: cell.run }, 'Pause tile', 'small')}
      ${button('run.priority', { run_id: cell.run }, 'Priority…', 'small')}</div>` : ''}`;
}

function focusView(outer, snapshot, roots, root, selected) {
  // A fresh element per redraw, so its listeners never pile up.
  const container = document.createElement('div');
  outer.replaceChildren(container);
  const index = roots.indexOf(root);
  const total = info(root).total;
  const done = root.counts.durable + root.counts.complete;
  const stats = pace(root);
  const typical = typicalEstimate(root, healthyMachines(snapshot), roots);
  const generated = snapshot.generated_at;
  const zoomBar = html`<div class="zoom-bar" role="group" aria-label="Zoom">
    <button type="button" class="cmd small" data-zoom="out" ${zoom === ZOOMS[0] ? 'disabled' : ''} title="Zoom out">−</button>
    <span class="zoom-level">${zoom}×</span>
    <button type="button" class="cmd small" data-zoom="in" ${zoom === ZOOMS[ZOOMS.length - 1] ? 'disabled' : ''} title="Zoom in">+</button>
    <button type="button" class="cmd small" data-zoom="fit" ${zoom === 1 ? 'disabled' : ''} title="Fit the whole grid">Fit</button>
    <span class="hint">Hover a tile for a magnified view; zoom to click a single tile.</span>
  </div>`;

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
        <div class="focus-grid${zoom > 1 ? ' zoomed' : ''}">${zoomBar}<div class="focus-scroll">${canvasFor(root, index, true)}</div></div>
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
          ${recomputedNote(root)}
          <div class="card tile-detail">${detailPanel(root, selected, generated)}</div>
        </aside>
      </div>
    </section>`);

  // Zoom lives in CSS (a variable on the grid) because the page's content-security policy has no inline styles.
  const scroller = container.querySelector('.focus-scroll');
  const canvas = scroller.querySelector('canvas.tile-grid');
  canvas.style.setProperty('--zoom', String(zoom));
  attachGrids(container, roots, () => ({ focus: true, selected }));
  if (focusScroll.run === root.run_id) {
    scroller.scrollLeft = focusScroll.left;
    scroller.scrollTop = focusScroll.top;
  }
  scroller.addEventListener('scroll', () => {
    focusScroll = { run: root.run_id, left: scroller.scrollLeft, top: scroller.scrollTop };
  }, { passive: true });

  // Per-tile detail for tooltips, and the selected tile's full record, once per snapshot.
  const entry = details.get(root.run_id);
  if (!entry || entry.generated !== generated) {
    api(`/api/tiles?run=${encodeURIComponent(root.run_id)}`).then((data) => {
      details.set(root.run_id, { generated, data });
    }).catch(() => {});
  }
  if (selected) {
    const key = `${root.run_id}/${selected.r},${selected.c}`;
    const known = tileRecords.get(key);
    if (!known || known.generated !== generated) {
      api(`/api/tile?run=${encodeURIComponent(root.run_id)}&r=${selected.r}&c=${selected.c}`).then((record) => {
        tileRecords.set(key, { generated, record });
        const panel = container.querySelector('.tile-detail');
        if (panel && container.isConnected) setHTML(panel, detailPanel(root, selected, generated));
      }).catch(() => {});
    }
  }

  container.addEventListener('click', (event) => {
    const zoomButton = event.target.closest('button[data-zoom]');
    if (zoomButton) {
      const before = zoom;
      const at = ZOOMS.indexOf(zoom);
      zoom = zoomButton.dataset.zoom === 'fit' ? 1
        : ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, at + (zoomButton.dataset.zoom === 'in' ? 1 : -1)))];
      // Keep the middle of what is on screen in the middle after the grid changes size.
      const ratio = zoom / before;
      const centreX = scroller.scrollLeft + scroller.clientWidth / 2;
      const centreY = scroller.scrollTop + scroller.clientHeight / 2;
      focusScroll = {
        run: root.run_id,
        left: Math.max(0, centreX * ratio - scroller.clientWidth / 2),
        top: Math.max(0, centreY * ratio - scroller.clientHeight / 2),
      };
      focusView(outer, snapshot, roots, root, selected);
      return;
    }
    if (event.target !== canvas) return;
    const found = hit(canvas, root, event, true);
    if (!found) return;
    const same = selected && selected.r === found.r && selected.c === found.c;
    history.replaceState(null, '', same ? `#tiles/${root.run_id}` : `#tiles/${root.run_id}/${found.r},${found.c}`);
    focusView(outer, snapshot, roots, root, same ? null : { r: found.r, c: found.c });
  });
  canvas.addEventListener('pointermove', (event) => {
    const found = hit(canvas, root, event, true);
    if (!found) { hideTooltip(); return; }
    showTooltip(event, tileTip(root, cellAt(root, found.r, found.c), found.cell, selected));
  });
  canvas.addEventListener('pointerleave', hideTooltip);
}

// ----- overview --------------------------------------------------------------------

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
  const machines = healthyMachines(snapshot);

  setHTML(container, html`
    <section class="panel">
      <div class="panel-head">
        <h2>Distributed DP roots</h2>
        <p class="hint">Rows and columns are tile coordinates; work sweeps along anti-diagonals.
          Click a root to open it full size with its pace and per-tile details.</p>
      </div>
      <div class="legend">${legend}</div>
      ${active.length ? html`<div class="root-grid">${active.map((r) => rootCard(r, roots.indexOf(r), machines, roots))}</div>`
        : html`<p class="empty-note">No DP root is active.</p>`}
      ${finished.length ? html`
        <details class="finished" ${showFinished ? 'open' : ''}>
          <summary>Finished in the last 24 hours (${finished.length})</summary>
          ${showFinished ? html`<div class="root-grid">${finished.map((r) => rootCard(r, roots.indexOf(r), machines, roots))}</div>` : ''}
        </details>` : ''}
    </section>`);

  const finishedSection = container.querySelector('details.finished');
  if (finishedSection) {
    // Finished roots are drawn only when opened.
    finishedSection.addEventListener('toggle', () => {
      if (showFinished === finishedSection.open) return;
      showFinished = finishedSection.open;
      render(container, snapshot, detail);
    });
  }
  attachGrids(container, roots, () => ({}));

  container.querySelectorAll('canvas.tile-grid').forEach((canvas) => {
    const root = roots[Number(canvas.dataset.root)];
    canvas.addEventListener('pointermove', (event) => {
      const found = hit(canvas, root, event, false);
      if (!found) { hideTooltip(); return; }
      showTooltip(event, tileTip(root, cellAt(root, found.r, found.c), found.cell, null, html`<br><i>click to open</i>`));
    });
    canvas.addEventListener('pointerleave', hideTooltip);
    // Clicking a tile in the overview opens the root with that tile selected.
    canvas.addEventListener('click', (event) => {
      const found = hit(canvas, root, event, false);
      if (!found) return;
      event.preventDefault();
      location.hash = `#tiles/${root.run_id}/${found.r},${found.c}`;
    });
  });
}
