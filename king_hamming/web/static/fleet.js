// Fleet: one card per machine with CPUs, memory, current work, 24 h utilisation and heat.
import {
  html, setHTML, field, fmtBytes, fmtDuration, fmtInt, pct, sparkline, bar, tooltips, now, gpuName, api, ago,
} from './util.js';
import { button, isOperator, toast } from './command.js';

// Disk usage in four parts, in bar order. "Other" is everything else king_hamming keeps.
const DISK_PARTS = [
  ['tiles', 'Tiles', 'disk-tiles'],
  ['other', 'Other king_hamming data', 'disk-other'],
  ['unrelated', 'Unrelated files', 'disk-unrelated'],
  ['free', 'Free', 'disk-free'],
];
let diskRecheck = null;

const diskPct = (part, size) => {
  if (!size) return '—';
  const fraction = (part || 0) / size;
  return `${(fraction * 100).toFixed(fraction > 0 && fraction < 0.1 ? 1 : 0)}%`;
};

function diskBar(disk, subject) {
  const size = disk.size || 1;
  let x = 0;
  const rects = DISK_PARTS.map(([key, , cls]) => {
    const width = Math.max(0, disk[key] || 0) / size * 100;
    const rect = html`<rect class="${cls}" x="${x.toFixed(3)}" y="0" width="${width.toFixed(3)}" height="12"></rect>`;
    x += width;
    return rect;
  });
  const label = DISK_PARTS.map(([key, name]) => `${name} ${fmtBytes(disk[key])}`).join(', ');
  return html`<svg class="diskbar" viewBox="0 0 100 12" preserveAspectRatio="none" role="img"
    aria-label="Disk: ${label}" data-disk="${subject}"><rect class="disk-free" x="0" y="0" width="100" height="12"></rect>${rects}</svg>`;
}

// Short labels for the card: the free figure is already in the line above the bar.
const DISK_SHORT = { tiles: 'Tiles', other: 'Other kh', unrelated: 'Unrelated' };
function diskFigures(disk) {
  return html`<div class="disk-figures">${DISK_PARTS.filter(([key]) => DISK_SHORT[key]).map(([key, , cls]) => html`<span><span class="swatch ${cls}"></span>${DISK_SHORT[key]} ${fmtBytes(disk[key])}</span>`)}</div>`;
}

function diskSection(node, fleetDisk, generatedAt) {
  if (!fleetDisk) return '';
  const disk = node.disk;
  if (!disk || !disk.size) {
    return html`<div class="disk"><div class="memline"><span>Disk</span>
      <span class="hint">${disk && disk.error ? `not measured: ${disk.error}` : 'not measured yet'}</span></div></div>`;
  }
  const stale = Boolean(disk.error) || generatedAt - disk.measured_at > 2 * fleetDisk.interval + 300;
  return html`<div class="disk ${stale ? 'disk-stale' : ''}">
    <div class="memline"><span>Disk · ${fmtBytes(disk.size)}</span>
      <span>${fmtBytes(disk.free)} free (${diskPct(disk.free, disk.size)})</span></div>
    ${diskBar(disk, node.name)}
    ${diskFigures(disk)}
    <div class="disk-note">${disk.error ? html`Last measurement failed (${disk.error}); showing data from ` : 'Measured '}${ago(disk.measured_at)}</div>
  </div>`;
}

function diskSummary(fleet, generatedAt) {
  const state = fleet.disk;
  if (!state) return '';
  const cluster = state.cluster;
  const measure = isOperator() ? html`<button type="button" class="cmd small" data-disk-measure ${state.running ? 'disabled' : ''}>${state.running ? 'Measuring…' : 'Measure disks now'}</button>` : '';
  if (!cluster || !cluster.total.size) {
    return html`<section class="disk-summary"><div class="row"><h4>Disk usage</h4>${measure}</div>
      <p class="hint">${state.running ? 'Measuring every machine now…' : 'Not measured yet. The dashboard measures each machine every '
        + `${Math.round(state.interval / 60)} minutes.`}</p></section>`;
  }
  const total = cluster.total;
  const reclaim = cluster.reclaimable;
  return html`<section class="disk-summary">
    <div class="row"><h4>Disk usage</h4>${measure}</div>
    ${diskBar(total, 'cluster')}
    <div class="disk-legend">${DISK_PARTS.map(([key, name, cls]) => html`<span class="legend-item"><span class="swatch ${cls}"></span>${name}
      <b>${fmtBytes(total[key])}</b> <span class="hint">${diskPct(total[key], total.size)}</span></span>`)}</div>
    <p class="hint">${total.machines} machines, ${fmtBytes(total.size)} in all, measured ${ago(state.measured_at)} and every
      ${Math.round(state.interval / 60)} minutes. Unrelated includes the space each disk keeps for root.</p>
    <p class="hint"><b>Could be reclaimed</b> (estimates; CAMPAIGN_NOTES items 22–24):
      tiles of finished fields ${fmtBytes(reclaim.finished_tiles)} ·
      ${reclaim.excess_copies === null ? '' : html`copies above ${cluster.target_copies} per tile ${fmtBytes(reclaim.excess_copies)}
      (average ${cluster.average_copies} copies) · `}run scratch ${fmtBytes(reclaim.scratch)}.
      The first two overlap.</p>
  </section>`;
}

function diskTooltip(title, disk, cluster) {
  const rows = DISK_PARTS.map(([key, name, cls]) => html`<tr><td><span class="swatch ${cls}"></span>${name}</td>
    <td class="num">${fmtBytes(disk[key])}</td><td class="num">${diskPct(disk[key], disk.size)}</td></tr>`);
  const groups = disk.tiles_by_group;
  const parts = disk.other_parts;
  return html`<b>${title}</b>
    <table class="mini disk-tip"><tbody>${rows}</tbody></table>
    ${groups ? html`<div>Tiles: finished fields ${fmtBytes(groups.finished)} · unfinished ${fmtBytes(groups.unfinished)}
      · failed attempts ${fmtBytes(groups.failed)}</div>` : ''}
    ${disk.top_fields && disk.top_fields.length ? html`<div>Largest: ${disk.top_fields.map(([p, r, size]) => `${field(p, r)} ${fmtBytes(size)}`).join(' · ')}</div>` : ''}
    ${cluster && cluster.average_copies ? html`<div>Each tile is stored on ${cluster.average_copies} machines on average (target ${cluster.target_copies}).</div>` : ''}
    ${parts ? html`<div>Other king_hamming: blobs ${fmtBytes(parts.blobs)} · run scratch ${fmtBytes(parts.scratch)}
      · earlier deployments ${fmtBytes(parts.deployments)}${parts.repository ? html` · repository ${fmtBytes(parts.repository)}` : ''}</div>` : ''}
    ${disk.reserved ? html`<div class="hint">Unrelated includes ${fmtBytes(disk.reserved)} the filesystem keeps for root.</div>` : ''}`;
}

function actions(item) {
  if (item.role !== 'lease') return '';
  const run = { run_id: item.run_id };
  const buttons = [button('run.pause', run, 'Pause', 'small')];
  if (item.kind !== 'dp_tile') buttons.push(button('run.cancel', run, 'Cancel', 'small'));
  buttons.push(button('run.priority', run, 'Priority…', 'small'));
  return html`<div class="cmd-row">${buttons}</div>`;
}

const HEALTH_CLASS = {
  responding: 'ok', starting: 'ok', 'waiting-for-gpu': 'ok', verifying: 'ok', stopping: 'warn', 'no-progress-warning': 'warn',
  stalled: 'bad', 'heartbeat-missing': 'bad',
};

const fieldName = (item) => (item.field ? field(item.field[0], item.field[1]) : item.program || item.kind);

// What a health badge describes: the tile, the matching job, or the whole DP.
function subject(item) {
  if (item.kind === 'dp_tile') return 'tile';
  if (item.kind === 'matching') return 'matching';
  if (item.kind === 'dp_distributed') return 'root';
  return 'job';
}

function workLabel(item) {
  const name = fieldName(item);
  if (item.kind === 'matching') return `${name} matching${item.role === 'partner' ? ' (partner)' : ''}`;
  return `${name} ${item.label}`;
}

function workEntry(item, index, generatedAt) {
  const fraction = item.total ? item.done / item.total : 0;
  const elapsed = item.started ? generatedAt - item.started : null;
  const progress = item.role === 'partner' ? '' : item.total
    ? `${pct(fraction)} of ${fmtInt(item.total)} ${item.units}` : '';
  return html`<li class="work w${index % 6}">
    <div class="work-head">
      <span class="work-dot"></span>
      <span class="work-label">${workLabel(item)}</span>
      ${item.orphaned ? html`<span class="health health-bad" title="The DP calculation this tile belongs to is ${item.root_state}">${fieldName(item)} root: ${item.root_state}</span>` : ''}
      <span class="health health-${HEALTH_CLASS[item.health] || 'warn'}" title="Health of this ${subject(item)} process">${subject(item)}: ${item.health}</span>
    </div>
    <div class="work-meta">${item.phase || ''}${progress ? html` · ${progress}` : ''}
      · ${item.cpus.length} CPU${item.cpus.length === 1 ? '' : 's'}
      ${item.memory ? html` · ${fmtBytes(item.memory)}` : ''}
      ${item.gpu !== null && item.gpu !== undefined ? html` · <span class="gpu-tag" title="Fenced GPU lease">GPU ${item.gpu}</span>`
        : item.accelerated ? html` · <span class="gpu-tag" title="This tile is computing on the host GPU">GPU</span>` : ''}
      ${elapsed !== null ? html` · ${fmtDuration(elapsed)}` : ''}</div>
    ${item.role !== 'partner' && item.total ? bar(fraction, `w${index % 6}`) : ''}
    ${actions(item)}
  </li>`;
}

function gpuLine(node) {
  if (node.gpus === null || node.gpus === undefined) {
    return html`<div class="memline gpuline"><span>GPU</span><span class="hint" title="This leader does not record GPUs yet">not reported</span></div>`;
  }
  const devices = node.gpus;
  if (!devices.length) return html`<div class="memline gpuline"><span>GPU</span><span>none usable</span></div>`;
  const busy = node.work.some((item) => (item.gpu !== null && item.gpu !== undefined) || item.accelerated);
  return html`<div class="memline gpuline"><span>GPU${busy ? html` <span class="gpu-tag">busy</span>` : ''}</span>
    <span title="${devices.map((d) => d.name).join(', ')}">${devices.map((d) => `${gpuName(d.name)} · ${fmtBytes(d.total_bytes)}`).join(', ')}</span></div>`;
}

// Real GPU utilisation sampled by the agent (nvidia-smi); absent until agents and leader report it.
function gpuUtil(node) {
  if (!node.gpu_utilization) return '';
  const series = node.gpu_utilization;
  const average = series.length ? series.reduce((a, b) => a + b, 0) / series.length : 0;
  const now = node.gpu_now;
  return html`<div class="util gpu-util">
    <div class="util-head"><span>GPU use, 24 h</span><span>${now ? html`now ${now.util}% · ${fmtBytes(now.memory_used_bytes)} · ` : ''}avg ${pct(average)}</span></div>
    ${sparkline(series)}
  </div>`;
}

// ----- heat: live gauges and a 24 h history (agents report temperatures since 2026-10-06) -----
const HEAT_KINDS = [['cpu', 'CPU'], ['gpu', 'GPU'], ['nvme', 'NVMe']];
const HEAT_LOW = 20;    // °C at the bottom of the chart and of each gauge
const HEAT_HIGH = 105;  // °C at the top

function heatLevel(value, limits) {
  if (value === null || value === undefined || !limits) return 'none';
  if (value >= limits.hot) return 'hot';
  if (value >= limits.warm) return 'warm';
  return 'cool';
}

function heatGauge(kind, name, value, limits) {
  if (value === null || value === undefined) return '';
  const level = heatLevel(value, limits);
  const fill = Math.max(0, Math.min(1, (value - HEAT_LOW) / (HEAT_HIGH - HEAT_LOW))) * 100;
  return html`<span class="heat-chip heat-${level}" title="${name}: ${value.toFixed(0)} °C now (warm from ${limits.warm}, hot from ${limits.hot})">
    <span class="heat-name">${name}</span>
    <svg class="heat-tube" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">
      <rect class="heat-track" x="0" y="0" width="100" height="6" rx="3"></rect>
      <rect class="heat-fill" x="0" y="0" width="${fill.toFixed(1)}" height="6" rx="3"></rect>
    </svg>
    <b>${value.toFixed(0)}°</b></span>`;
}

// One line per sensor; a bucket without a reading breaks the line.
function heatLines(series, width, height) {
  const step = width / Math.max(1, series.length - 1);
  const y = (value) => (height - (Math.max(HEAT_LOW, Math.min(HEAT_HIGH, value)) - HEAT_LOW) / (HEAT_HIGH - HEAT_LOW) * height).toFixed(1);
  const segments = [];
  let current = [];
  series.forEach((value, i) => {
    if (value === null || value === undefined) {
      if (current.length) segments.push(current);
      current = [];
    } else {
      current.push(`${(i * step).toFixed(1)},${y(value)}`);
    }
  });
  if (current.length) segments.push(current);
  return segments;
}

function heatChart(history, limits, width = 96, height = 40) {
  // One dashed guide at the CPU's warm limit (85 °C); the gauges carry the rest.
  const guide = (height - (limits.cpu.warm - HEAT_LOW) / (HEAT_HIGH - HEAT_LOW) * height).toFixed(1);
  const guides = html`<line class="heat-guide" x1="0" x2="${width}" y1="${guide}" y2="${guide}"></line>`;
  const lines = HEAT_KINDS.flatMap(([kind]) => heatLines(history[kind] || [], width, height).map((points) =>
    points.length === 1
      ? html`<circle class="heat-${kind}-dot" cx="${points[0].split(',')[0]}" cy="${points[0].split(',')[1]}" r="0.9"></circle>`
      : html`<polyline class="heat-line heat-${kind}-line" points="${points.join(' ')}"></polyline>`));
  return html`<svg class="spark heat-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
    ${guides}${lines}</svg>`;
}

function heatSection(node, limits) {
  const heat = node.heat;
  if (!heat || !limits) return '';
  const latest = heat.now || {};
  const peaks = HEAT_KINDS.map(([kind, name]) => {
    const values = (heat.history[kind] || []).filter((value) => value !== null && value !== undefined);
    return values.length ? `${name} ${Math.max(...values).toFixed(0)}°` : null;
  }).filter(Boolean);
  const gauges = HEAT_KINDS.map(([kind, name]) => heatGauge(kind, name, latest[`${kind}_c`], limits[kind]));
  return html`<div class="util heat">
    <div class="util-head"><span>Heat, 24 h</span><span>${peaks.length ? `peak ${peaks.join(' · ')}` : ''}</span></div>
    <div class="heat-gauges">${gauges}</div>
    ${heatChart(heat.history, limits)}
    <div class="heat-legend">${HEAT_KINDS.filter(([kind]) => (heat.history[kind] || []).some((v) => v !== null))
      .map(([kind, name]) => html`<span><span class="heat-key heat-${kind}-key"></span>${name}</span>`)}
      <span class="hint">${HEAT_LOW}–${HEAT_HIGH} °C, dashed ${limits.cpu.warm} °C</span></div>
  </div>`;
}

// The hottest reading in the fleet now, relative to its own limits.
function hottest(nodes, limits) {
  let best = null;
  nodes.forEach((node) => {
    const latest = (node.heat && node.heat.now) || {};
    HEAT_KINDS.forEach(([kind, name]) => {
      const value = latest[`${kind}_c`];
      if (value === null || value === undefined || !limits || !limits[kind]) return;
      const share = value / limits[kind].hot;
      if (!best || share > best.share) best = { share, text: `${node.hostname} ${name} ${value.toFixed(0)} °C`, level: heatLevel(value, limits[kind]) };
    });
  });
  return best;
}

function card(node, generatedAt, fleetDisk, heatLimits) {
  const alive = node.state !== 'unavailable';
  const average = node.utilization.length
    ? node.utilization.reduce((a, b) => a + b, 0) / node.utilization.length : 0;
  const cpus = node.cpus.map((cpu) => html`<span class="cpu cpu-${cpu.state}${cpu.work !== null && cpu.work !== undefined ? ` w${cpu.work % 6}` : ''}"
      data-node="${node.name}" data-cpu="${cpu.id}"></span>`);
  return html`<article class="node-card ${alive ? '' : 'node-down'}">
    <header class="node-head">
      <div>
        <h3><span class="dot ${alive ? (node.work.length ? 'dot-busy' : 'dot-idle') : 'dot-down'}"></span>${node.hostname}</h3>
        <div class="node-sub">.${node.host.split('.').pop()} · ${node.name}</div>
      </div>
      <div class="node-beat" title="Last heartbeat">${alive ? `♥ ${fmtDuration(node.heartbeat_age)}` : 'offline'}</div>
    </header>
    <div class="cpus" aria-label="CPU allocation">${cpus}</div>
    <div class="memline"><span>Memory reserved</span>
      <span>${fmtBytes(node.reserved_memory_bytes)} / ${fmtBytes(node.memory_bytes)}</span></div>
    ${bar(node.memory_bytes ? node.reserved_memory_bytes / node.memory_bytes : 0, 'mem')}
    ${gpuLine(node)}
    ${diskSection(node, fleetDisk, generatedAt)}
    ${node.work.length
      ? html`<ul class="work-list">${node.work.map((item, i) => workEntry(item, i, generatedAt))}</ul>`
      : html`<p class="idle">Idle: ${node.idle_reason || 'unknown reason'}</p>`}
    <div class="util">
      <div class="util-head"><span>CPU use, 24 h</span><span>avg ${pct(average)} · leased ${pct(node.busy_fraction)}</span></div>
      ${sparkline(node.utilization)}
    </div>
    ${gpuUtil(node)}
    ${heatSection(node, heatLimits)}
    <footer class="node-foot">${node.cpus.length} logical / ${node.physical_cores || '?'} cores ·
      runtime ${node.runtime_version || '?'} · storage ${node.storage_validation || '?'}</footer>
  </article>`;
}

export function render(container, snapshot) {
  const fleet = snapshot.fleet;
  if (!fleet) {
    setHTML(container, html`<p class="empty-note">Fleet data is unavailable.</p>`);
    return;
  }
  const nodes = fleet.nodes;
  const healthy = nodes.filter((n) => n.state !== 'unavailable').length;
  const allocatable = nodes.reduce((sum, n) => sum + n.cpus.filter((c) => c.state !== 'reserved').length, 0);
  const busy = nodes.reduce((sum, n) => sum + n.cpus.filter((c) => c.state === 'busy').length, 0);
  const average = nodes.length ? nodes.reduce((sum, n) =>
    sum + n.utilization.reduce((a, b) => a + b, 0) / Math.max(1, n.utilization.length), 0) / nodes.length : 0;
  const hot = hottest(nodes, fleet.heat_limits);

  setHTML(container, html`
    <section class="panel">
      <div class="panel-head row">
        <h2>Fleet</h2>
        ${fleet.dispatch === 'running' ? button('dispatch.stop', {}, 'Stop dispatch…')
          : button('dispatch.resume', {}, 'Resume dispatch')}
      </div>
      <div>
        <p class="hint">${healthy}/${nodes.length} machines healthy · ${busy}/${allocatable} CPUs allocated now ·
          average CPU use over 24 h ${pct(average)}${hot ? html` · hottest now: <span class="heat-text heat-${hot.level}">${hot.text}</span>` : ''}.
          Allocation is reserved capacity; the graph is measured use.</p>
      </div>
      ${diskSummary(fleet, snapshot.generated_at || now())}
      <div class="legend">
        <span class="legend-item"><span class="cpu cpu-busy w0"></span>Allocated</span>
        <span class="legend-item"><span class="cpu cpu-free"></span>Free</span>
        <span class="legend-item"><span class="cpu cpu-reserved"></span>Not schedulable (leader core)</span>
      </div>
      <div class="node-grid">${nodes.map((n) => card(n, snapshot.generated_at || now(), fleet.disk, fleet.heat_limits))}</div>
    </section>`);

  clearTimeout(diskRecheck);
  if (fleet.disk && fleet.disk.running) {
    diskRecheck = setTimeout(() => window.dispatchEvent(new Event('kh:changed')), 5000);
  }
  container.addEventListener('click', async (event) => {
    const target = event.target.closest('[data-disk-measure]');
    if (!target) return;
    target.disabled = true;
    try {
      const answer = await api('/api/disk/measure', { method: 'POST', body: {} });
      toast(answer.started ? 'Measuring every machine…' : answer.reason);
      setTimeout(() => window.dispatchEvent(new Event('kh:changed')), 2000);
    } catch (error) {
      toast(error.message, 'bad');
      target.disabled = false;
    }
  });
  const byName = new Map(nodes.map((n) => [n.name, n]));
  // One handler for every tooltip here: a second tooltips() call would hide this one's tooltips.
  tooltips(container, '.cpu[data-node], .diskbar[data-disk]', (target) => {
    if (target.dataset.disk !== undefined) {
      if (target.dataset.disk === 'cluster') {
        return fleet.disk.cluster ? diskTooltip('All machines', fleet.disk.cluster.total, fleet.disk.cluster) : null;
      }
      const machine = byName.get(target.dataset.disk);
      return machine && machine.disk && machine.disk.size
        ? diskTooltip(`${machine.hostname} disk`, machine.disk, fleet.disk.cluster) : null;
    }
    const node = byName.get(target.dataset.node);
    const cpu = node && node.cpus[Number(target.dataset.cpu)];
    if (!cpu) return null;
    const item = cpu.work !== null && cpu.work !== undefined ? node.work[cpu.work] : null;
    return html`<b>CPU ${cpu.id}</b> on ${node.hostname}<br>${item ? workLabel(item)
      : cpu.state === 'free' ? 'free' : 'not schedulable'}`;
  });
}
