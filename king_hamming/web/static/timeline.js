// Machine timeline: one row per machine, one bar per lease (merged when back to back).
import {
  html, setHTML, field, fmtDuration, fmtTime, pct, tooltips,
} from './util.js';

const WINDOWS = {
  '3h': 3 * 3600, '6h': 6 * 3600, '12h': 12 * 3600, '24h': 24 * 3600, '48h': 48 * 3600, '7d': 7 * 86400,
};
// Validated categorical palette (dataviz reference instance), stepped per theme.
const PALETTE_LIGHT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'];
const PALETTE_DARK = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'];

function darkTheme() {
  const theme = document.documentElement.dataset.theme;
  if (theme) return theme === 'dark';
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}
const OUTCOMES = {
  ok: 'Completed', running: 'Running', retry: 'Engine failure, retried', fail: 'Failed',
  expired: 'Lease expired', stop: 'Stopped deliberately', other: 'Other',
};
const KINDS = {
  tile: 'DP tile', root: 'DP coordinator', whole: 'whole DP', matching: 'matching coordinator',
  matching_partner: 'matching shard', other: 'job',
};
const LABEL_WIDTH = 92;
const TOP_FIELDS = 8;
const OTHER = '#9aa3b2';
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
  // Up to 24 h: per-lease detail and 15-minute CPU buckets. Beyond: the 7-day
  // overview (gaps under 10 minutes merged) with hourly CPU buckets.
  const long = span > 24 * 3600 && timeline.overview;
  const source = long ? timeline.overview : timeline;
  const segments = source.segments;
  const nodes = (snapshot.fleet && snapshot.fleet.nodes) || [];
  const byName = new Map(nodes.map((n) => [n.name, n]));
  const visible = segments.filter((s) => (s.t1 ?? end) > start);

  // Colour only the fields with the most leased time in this window (at most
  // eight, so no colour repeats); every other field is a neutral "other".
  const leasedByField = new Map();
  visible.forEach((s) => {
    if (!s.f) return;
    const seconds = Math.min(end, s.t1 ?? end) - Math.max(start, s.t0);
    leasedByField.set(s.f, (leasedByField.get(s.f) || 0) + seconds);
  });
  const top = [...leasedByField.entries()].sort((a, b) => b[1] - a[1]).slice(0, TOP_FIELDS).map(([f]) => f);
  const order = [];
  visible.slice().sort((a, b) => a.t0 - b.t0).forEach((s) => {
    if (s.f && top.includes(s.f) && !order.includes(s.f)) order.push(s.f);
  });
  const palette = darkTheme() ? PALETTE_DARK : PALETTE_LIGHT;
  const colour = new Map(order.map((f, i) => [f, palette[i]]));
  const others = leasedByField.size - order.length;

  const width = Math.max(560, Math.floor(container.parentElement.clientWidth || 1100) - 34);
  const plot = width - LABEL_WIDTH - 8;
  const x = (t) => LABEL_WIDTH + ((Math.max(start, Math.min(end, t)) - start) / span) * plot;

  let y = 24;
  const rows = source.nodes.map((row) => {
    const height = Math.max(22, row.lanes * LANE + ROW_PAD * 2);
    const top = y;
    y += height + 4;
    return { ...row, top, height };
  });
  const height = y + 4;

  const tickStep = span <= 6 * 3600 ? 1800 : span <= 12 * 3600 ? 3600 : span <= 24 * 3600 ? 3 * 3600
    : span <= 48 * 3600 ? 6 * 3600 : 86400;
  // Align ticks to local time (midnight for days), not to UTC.
  const offset = new Date(start * 1000).getTimezoneOffset() * 60;
  const ticks = [];
  for (let t = Math.ceil((start - offset) / tickStep) * tickStep + offset; t <= end; t += tickStep) {
    const date = new Date(t * 1000);
    const label = tickStep >= 86400
      ? date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
      : tickStep >= 6 * 3600 && date.getHours() === 0
        ? date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
        : date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    ticks.push(html`<line class="tick" x1="${x(t)}" x2="${x(t)}" y1="18" y2="${height}"></line>
      <text class="tick-label" x="${x(t)}" y="12">${label}</text>`);
  }

  const rowSvg = rows.map((row) => {
    const node = byName.get(row.name);
    const segs = visible.filter((s) => s.n === row.name);
    // Only count time that has lease records at all.
    const known = end - Math.max(start, timeline.history_start || start);
    const leased = known > 0
      ? unionSeconds(segs.map((s) => [Math.max(start, s.t0), Math.min(end, s.t1 ?? end)])) / known : 0;
    const util = long ? (source.utilization[row.name] || []) : ((node && node.utilization) || []);
    const bucket = long ? source.bucket_seconds : (snapshot.fleet ? snapshot.fleet.bucket_seconds : 900);
    const utilStart = (long ? source.computed_at : end) - util.length * bucket;
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
      const fill = (s.f && colour.get(s.f)) || OTHER;
      return html`<rect class="seg o-${s.o} k-${s.k}" x="${left.toFixed(1)}" y="${top}" width="${w.toFixed(1)}"
        height="${LANE - 2}" rx="1.5" fill="${fill}" data-i="${segments.indexOf(s)}"></rect>`;
    });
    return html`<rect class="tl-row" x="${LABEL_WIDTH}" y="${row.top}" width="${plot}" height="${row.height}" rx="4"></rect>
      ${area}
      <text class="tl-name" x="0" y="${row.top + row.height / 2 - 2}">${node ? node.hostname : row.name}</text>
      <text class="tl-sub" x="0" y="${row.top + row.height / 2 + 10}">leased ${pct(leased)}</text>
      ${bars}`;
  });

  // Before the oldest recorded lease there is no data at all: say so instead of
  // drawing it as idle.
  const historyStart = timeline.history_start;
  const noHistory = historyStart && historyStart > start ? html`
    <rect class="no-history" x="${LABEL_WIDTH}" y="18" width="${(x(historyStart) - LABEL_WIDTH).toFixed(1)}" height="${height - 18}"></rect>
    ${x(historyStart) - LABEL_WIDTH > 120 ? html`<text class="no-history-label" x="${(LABEL_WIDTH + x(historyStart)) / 2}" y="${height / 2}">no lease history before ${fmtTime(historyStart)}</text>` : ''}` : '';

  const legend = order.map((f) => html`<span class="legend-item"><span class="swatch" data-colour="${colour.get(f)}"></span>${fieldLabel(f)}</span>`)
    .concat(others > 0 ? [html`<span class="legend-item"><span class="swatch" data-colour="${OTHER}"></span>${others} other field${others === 1 ? '' : 's'}</span>`] : []);
  const windows = Object.keys(WINDOWS).map((name) => html`<a class="seg-btn ${name === windowName ? 'active' : ''}" href="#timeline/${name}">${name}</a>`);

  setHTML(container, html`
    <section class="panel">
      <div class="panel-head">
        <h2>Machine timeline</h2>
        <p class="hint">Each bar is a lease. Back-to-back tiles of one field on one machine are merged
          (in the 48 h and 7 d views, also across gaps under 10 minutes).
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
          ${noHistory}${ticks}${rowSvg}
          <line class="now-line" x1="${x(end)}" x2="${x(end)}" y1="18" y2="${height}"></line>
        </svg>
      </div>
    </section>`);

  // Legend swatches get their colour from data attributes (CSP forbids inline styles).
  container.querySelectorAll('.swatch[data-colour]').forEach((el) => { el.style.background = el.dataset.colour; });

  tooltips(container, 'rect.seg', (target) => {
    const s = segments[Number(target.dataset.i)];
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
