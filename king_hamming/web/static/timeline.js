// Machine timeline: one row per machine, one bar per lease (merged when back to back).
import {
  html, setHTML, field, fmtDuration, fmtTime, pct, tooltips,
} from './util.js';

const WINDOWS = { '3h': 3 * 3600, '6h': 6 * 3600, '12h': 12 * 3600, '24h': 24 * 3600 };
const PALETTE = ['#3f78e0', '#e07a3f', '#2aa3b0', '#a35bd6', '#c9a227', '#d64f8a',
  '#5fae4f', '#8a6d3b', '#4b5fc4', '#c2513b'];
const OUTCOMES = {
  ok: 'Completed', running: 'Running', retry: 'Engine failure, retried', fail: 'Failed',
  expired: 'Lease expired', stop: 'Stopped deliberately', other: 'Other',
};
const KINDS = {
  tile: 'DP tile', root: 'DP coordinator', whole: 'whole DP', matching: 'matching coordinator',
  matching_partner: 'matching shard', other: 'job',
};
const LABEL_WIDTH = 92;
const LANE = 9;
const ROW_PAD = 5;

let windowName = '24h';
let resizeHooked = false;

function fieldLabel(key) {
  if (!key) return '—';
  const [p, r] = key.split(',');
  return field(p, r);
}

function unionSeconds(intervals) {
  let total = 0;
  let end = -Infinity;
  intervals.sort((a, b) => a[0] - b[0]).forEach(([low, high]) => {
    if (low > end) { total += high - low; end = high; } else if (high > end) { total += high - end; end = high; }
  });
  return total;
}

export function render(container, snapshot, detail) {
  const timeline = snapshot.timeline;
  if (!timeline) {
    setHTML(container, html`<p class="empty-note">Timeline data is unavailable.</p>`);
    return;
  }
  if (detail && WINDOWS[detail]) windowName = detail;
  const span = WINDOWS[windowName];
  const end = timeline.end;
  const start = end - span;
  const nodes = (snapshot.fleet && snapshot.fleet.nodes) || [];
  const byName = new Map(nodes.map((n) => [n.name, n]));
  const visible = timeline.segments.filter((s) => (s.t1 ?? end) > start);

  // Stable colours: fields in order of first appearance in this window.
  const order = [];
  visible.slice().sort((a, b) => a.t0 - b.t0).forEach((s) => {
    if (s.f && !order.includes(s.f)) order.push(s.f);
  });
  const colour = new Map(order.map((f, i) => [f, PALETTE[i % PALETTE.length]]));

  const width = Math.max(560, Math.floor(container.parentElement.clientWidth || 1100) - 34);
  const plot = width - LABEL_WIDTH - 8;
  const x = (t) => LABEL_WIDTH + ((Math.max(start, Math.min(end, t)) - start) / span) * plot;

  let y = 24;
  const rows = timeline.nodes.map((row) => {
    const height = Math.max(22, row.lanes * LANE + ROW_PAD * 2);
    const top = y;
    y += height + 4;
    return { ...row, top, height };
  });
  const height = y + 4;

  const tickStep = span <= 6 * 3600 ? 1800 : span <= 12 * 3600 ? 3600 : 3 * 3600;
  const ticks = [];
  for (let t = Math.ceil(start / tickStep) * tickStep; t <= end; t += tickStep) {
    const label = new Date(t * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    ticks.push(html`<line class="tick" x1="${x(t)}" x2="${x(t)}" y1="18" y2="${height}"></line>
      <text class="tick-label" x="${x(t)}" y="12">${label}</text>`);
  }

  const rowSvg = rows.map((row) => {
    const node = byName.get(row.name);
    const segs = visible.filter((s) => s.n === row.name);
    const leased = unionSeconds(segs.map((s) => [Math.max(start, s.t0), Math.min(end, s.t1 ?? end)])) / span;
    const util = (node && node.utilization) || [];
    const bucket = snapshot.fleet ? snapshot.fleet.bucket_seconds : 900;
    const utilStart = end - util.length * bucket;
    let area = '';
    if (util.length) {
      const points = [];
      util.forEach((v, i) => {
        const t0 = utilStart + i * bucket;
        if (t0 + bucket < start) return;
        points.push(`${x(t0).toFixed(1)},${(row.top + row.height - v * row.height).toFixed(1)}`,
          `${x(t0 + bucket).toFixed(1)},${(row.top + row.height - v * row.height).toFixed(1)}`);
      });
      if (points.length) {
        area = html`<polygon class="tl-util" points="${x(start)},${row.top + row.height} ${points.join(' ')} ${x(end)},${row.top + row.height}"></polygon>`;
      }
    }
    const bars = segs.map((s) => {
      const left = x(s.t0);
      const w = Math.max(1.5, x(s.t1 ?? end) - left);
      const top = row.top + ROW_PAD + s.lane * LANE;
      const fill = s.f ? colour.get(s.f) : '#8892a6';
      return html`<rect class="seg o-${s.o} k-${s.k}" x="${left.toFixed(1)}" y="${top}" width="${w.toFixed(1)}"
        height="${LANE - 2}" rx="1.5" fill="${fill}" data-i="${timeline.segments.indexOf(s)}"></rect>`;
    });
    return html`<rect class="tl-row" x="${LABEL_WIDTH}" y="${row.top}" width="${plot}" height="${row.height}" rx="4"></rect>
      ${area}
      <text class="tl-name" x="0" y="${row.top + row.height / 2 - 2}">${node ? node.hostname : row.name}</text>
      <text class="tl-sub" x="0" y="${row.top + row.height / 2 + 10}">leased ${pct(leased)}</text>
      ${bars}`;
  });

  const legend = order.map((f) => html`<span class="legend-item"><span class="swatch" data-colour="${colour.get(f)}"></span>${fieldLabel(f)}</span>`);
  const windows = Object.keys(WINDOWS).map((name) => html`<a class="seg-btn ${name === windowName ? 'active' : ''}" href="#timeline/${name}">${name}</a>`);

  setHTML(container, html`
    <section class="panel">
      <div class="panel-head">
        <h2>Machine timeline</h2>
        <p class="hint">Each bar is a lease. Back-to-back tiles of one field on one machine are merged.
          Gaps are idle time; the shaded area behind each row is measured CPU use.
          Outlined bars ended badly. Hover or tap a bar for details.</p>
      </div>
      <div class="toolbar"><div class="seg-group">${windows}</div>
        <div class="legend">${legend}
          <span class="legend-item"><span class="swatch outline-fail"></span>failed / expired</span>
          <span class="legend-item"><span class="swatch outline-retry"></span>retried</span>
          <span class="legend-item"><span class="swatch outline-stop"></span>stopped</span>
        </div></div>
      <div class="timeline-scroll">
        <svg class="timeline" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
          ${ticks}${rowSvg}
          <line class="now-line" x1="${x(end)}" x2="${x(end)}" y1="18" y2="${height}"></line>
        </svg>
      </div>
    </section>`);

  // Legend swatches get their colour from data attributes (CSP forbids inline styles).
  container.querySelectorAll('.swatch[data-colour]').forEach((el) => { el.style.background = el.dataset.colour; });

  tooltips(container, 'rect.seg', (target) => {
    const s = timeline.segments[Number(target.dataset.i)];
    if (!s) return null;
    const node = byName.get(s.n);
    const finish = s.t1 ?? end;
    const what = s.c > 1 ? `${s.c} ${KINDS[s.k] || s.k}s, ${s.a} → ${s.b}` : `${KINDS[s.k] || s.k} ${s.a || ''}`;
    return html`<b>${fieldLabel(s.f)}</b> on ${node ? node.hostname : s.n}<br>${what}<br>
      ${fmtTime(s.t0)} → ${s.t1 ? fmtTime(s.t1) : 'now'} · ${fmtDuration(finish - s.t0)}<br>${OUTCOMES[s.o] || s.o}`;
  });

  if (!resizeHooked) {
    resizeHooked = true;
    let timer = null;
    window.addEventListener('resize', () => {
      clearTimeout(timer);
      timer = setTimeout(() => { if (location.hash.startsWith('#timeline')) window.dispatchEvent(new Event('hashchange')); }, 200);
    });
  }
}
