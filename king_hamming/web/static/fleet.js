// Fleet: one card per machine with CPUs, memory, current work and 24 h utilisation.
import {
  html, setHTML, field, fmtBytes, fmtDuration, fmtInt, pct, sparkline, bar, tooltips, now,
} from './util.js';
import { button } from './command.js';

function actions(item) {
  if (item.role !== 'lease') return '';
  const run = { run_id: item.run_id };
  const buttons = [button('run.pause', run, 'Pause', 'small')];
  if (item.kind !== 'dp_tile') buttons.push(button('run.cancel', run, 'Cancel', 'small'));
  buttons.push(button('run.priority', run, 'Priority…', 'small'));
  return html`<div class="cmd-row">${buttons}</div>`;
}

const HEALTH_CLASS = {
  responding: 'ok', starting: 'ok', stopping: 'warn', 'no-progress-warning': 'warn',
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
      ${elapsed !== null ? html` · ${fmtDuration(elapsed)}` : ''}</div>
    ${item.role !== 'partner' && item.total ? bar(fraction, `w${index % 6}`) : ''}
    ${actions(item)}
  </li>`;
}

function card(node, generatedAt) {
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
    ${node.work.length
      ? html`<ul class="work-list">${node.work.map((item, i) => workEntry(item, i, generatedAt))}</ul>`
      : html`<p class="idle">Idle: ${node.idle_reason || 'unknown reason'}</p>`}
    <div class="util">
      <div class="util-head"><span>CPU use, 24 h</span><span>avg ${pct(average)} · leased ${pct(node.busy_fraction)}</span></div>
      ${sparkline(node.utilization)}
    </div>
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

  setHTML(container, html`
    <section class="panel">
      <div class="panel-head row">
        <h2>Fleet</h2>
        ${fleet.dispatch === 'running' ? button('dispatch.stop', {}, 'Stop dispatch…')
          : button('dispatch.resume', {}, 'Resume dispatch')}
      </div>
      <div>
        <p class="hint">${healthy}/${nodes.length} machines healthy · ${busy}/${allocatable} CPUs allocated now ·
          average CPU use over 24 h ${pct(average)}. Allocation is reserved capacity; the graph is measured use.</p>
      </div>
      <div class="legend">
        <span class="legend-item"><span class="cpu cpu-busy w0"></span>Allocated</span>
        <span class="legend-item"><span class="cpu cpu-free"></span>Free</span>
        <span class="legend-item"><span class="cpu cpu-reserved"></span>Not schedulable (leader core)</span>
      </div>
      <div class="node-grid">${nodes.map((n) => card(n, snapshot.generated_at || now()))}</div>
    </section>`);

  const byName = new Map(nodes.map((n) => [n.name, n]));
  tooltips(container, '.cpu[data-node]', (target) => {
    const node = byName.get(target.dataset.node);
    const cpu = node && node.cpus[Number(target.dataset.cpu)];
    if (!cpu) return null;
    const item = cpu.work !== null && cpu.work !== undefined ? node.work[cpu.work] : null;
    return html`<b>CPU ${cpu.id}</b> on ${node.hostname}<br>${item ? workLabel(item)
      : cpu.state === 'free' ? 'free' : 'not schedulable'}`;
  });
}
