// Results heatmap: primes down the side, exponents across, coloured by outcome.
import {
  html, setHTML, field, fmtInt, fmtCompact, fmtPoly, fmtTime, shortId,
} from './util.js';
import { button, isOperator } from './command.js';

export const STATUS = {
  matched: { label: 'Matched', glyph: '^' },
  obstructed: { label: 'Hall obstruction', glyph: '*' },
  matching: { label: 'Matching running', glyph: '' },
  checking: { label: 'Matched, certificate being checked', glyph: '' },
  awaiting_matching: { label: 'DP done, matching pending', glyph: '' },
  too_big: { label: 'DP done, too big to match', glyph: '' },
  dp_running: { label: 'DP running', glyph: '' },
  dp_failed: { label: 'DP failed', glyph: '!' },
  dp_cancelled: { label: 'DP cancelled', glyph: '–' },
  unknown: { label: 'Unknown', glyph: '?' },
};

let selected = null; // "p,r" of the open drawer, kept across refreshes and in the URL

export function render(container, snapshot, detail) {
  selected = detail;
  const results = snapshot.results;
  if (!results) {
    setHTML(container, html`<p class="empty-note">Results are unavailable.</p>`);
    return;
  }
  const byKey = new Map(results.fields.map((f) => [`${f.p},${f.r}`, f]));
  const counts = {};
  results.fields.forEach((f) => { counts[f.status] = (counts[f.status] || 0) + 1; });

  const legend = Object.entries(STATUS)
    .filter(([key]) => counts[key])
    .map(([key, s]) => html`<span class="legend-item"><span class="swatch st-${key}"></span>${s.label} <b>${counts[key]}</b></span>`);

  const header = results.exponents.map((r) => html`<th scope="col">r = ${r}</th>`);
  const body = results.primes.map((p) => html`<tr>
    <th scope="row">${p}</th>
    ${results.exponents.map((r) => {
      const f = byKey.get(`${p},${r}`);
      if (!f) {
        return isOperator()
          ? html`<td class="cell empty"><button type="button" class="submit-cell" data-command="field.submit"
              data-params="${JSON.stringify({ p, r })}" title="Submit ${field(p, r)}…" aria-label="Submit ${field(p, r)}">+</button></td>`
          : html`<td class="cell empty"></td>`;
      }
      const s = STATUS[f.status] || STATUS.unknown;
      const rows = f.metrics ? fmtCompact(f.metrics.rows) : '…';
      return html`<td class="cell st-${f.status}${selected === `${p},${r}` ? ' selected' : ''}">
        <button type="button" data-key="${p},${r}" title="${field(p, r)}: ${s.label}">
          <span class="cell-count">${rows}</span><span class="cell-glyph">${s.glyph}</span>
        </button></td>`;
    })}
  </tr>`);

  setHTML(container, html`
    <section class="panel">
      <div class="panel-head row">
        <h2>Results across retained campaigns</h2>
        ${button('field.submit.ask', {}, 'Submit a field…')}
      </div>
      <p class="hint">Each cell is the exact number of permutations (rows). Click a cell for details${isOperator() ? '; click an empty cell to submit that field' : ''}.</p>
      <div class="legend">${legend}</div>
      <div class="results-scroll">
        <table class="results"><thead><tr><th scope="col">p</th>${header}</tr></thead>
        <tbody>${body}</tbody></table>
      </div>
      <p class="hint">Deployments: ${results.deployments.map((d) => html`<span class="tag ${d.live ? 'tag-live' : ''}">${d.name}${d.live ? ' · live' : ''}</span> `)}</p>
    </section>
    <aside class="drawer" ${selected ? '' : 'hidden'}></aside>`);

  const drawer = container.querySelector('.drawer');
  const open = (key) => {
    selected = key;
    history.replaceState(null, '', `#results/${key}`);
    container.querySelectorAll('.cell.selected').forEach((c) => c.classList.remove('selected'));
    const button = container.querySelector(`button[data-key="${key}"]`);
    if (button) button.parentElement.classList.add('selected');
    renderDrawer(drawer, byKey.get(key), snapshot.campaign);
  };
  container.querySelector('tbody').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-key]');
    if (button) open(button.dataset.key);
  });
  if (selected && byKey.has(selected)) open(selected);
  else selected = null;
}

// A failed or cancelled DP can be retried when the live campaign's feeder knows
// the field; the command's preview explains anything that blocks it.
function retryButton(f, campaign) {
  if (!isOperator() || !['dp_failed', 'dp_cancelled'].includes(f.status)) return '';
  if (!f.dp_attempts.some((a) => a.deployment === campaign)) {
    return html`<p class="hint">Only attempts from old deployments exist; use Submit a field… to start it in
      ${campaign}.</p>`;
  }
  return html`<div class="cmd-row drawer-actions">${button('feeder.retry', { p: f.p, r: f.r }, 'Retry DP…')}
    <span class="hint">A new attempt reuses every finished tile with two live copies.</span></div>`;
}

function renderDrawer(drawer, f, campaign) {
  const s = STATUS[f.status] || STATUS.unknown;
  const m = f.metrics;
  const metrics = m ? html`
    <dl class="facts">
      <dt>Rows</dt><dd>${fmtInt(m.rows)}</dd>
      <dt>q</dt><dd>${fmtInt(m.q)}</dd>
      <dt>θ</dt><dd>${fmtInt(m.theta)}</dd>
      <dt>F</dt><dd>${fmtInt(m.f)}</dd>
      <dt>Requests</dt><dd>${fmtInt(m.requests)}</dd>
      <dt>Edges</dt><dd>${fmtInt(m.edges)}</dd>
      ${f.admission ? html`<dt>Admission</dt><dd>${f.admission}</dd>` : ''}
    </dl>` : html`<p class="hint">No completed DP yet.</p>`;

  const dpRows = f.dp_attempts.map((a) => html`<tr>
    <td>${a.deployment}</td><td class="mono">${shortId(a.run_id)}</td>
    <td><span class="state state-${a.state.split(' ')[0]}">${a.state}</span></td>
    <td>${fmtTime(a.created)}</td><td>${fmtTime(a.finished)}</td>
  </tr>${a.error ? html`<tr class="error-row"><td colspan="5">${a.error}</td></tr>` : ''}`);

  const matchRows = f.matching_attempts.map((a) => html`<tr>
    <td>${a.deployment}</td><td class="mono">${fmtPoly(a.poly)}</td>
    <td><span class="state state-${a.state.split(' ')[0]}">${a.state}</span></td>
    <td>${a.outcome || '—'}</td><td>${fmtTime(a.finished)}</td>
  </tr>${a.error ? html`<tr class="error-row"><td colspan="5">${a.error}</td></tr>` : ''}`);

  setHTML(drawer, html`
    <div class="drawer-head">
      <h3>${field(f.p, f.r)} <span class="badge st-${f.status}">${s.label}</span></h3>
      <button type="button" class="close" aria-label="Close">×</button>
    </div>
    ${retryButton(f, campaign)}
    ${metrics}
    ${f.notes.length ? html`<ul class="notes">${f.notes.map((n) => html`<li>${n}</li>`)}</ul>` : ''}
    <h4>DP attempts</h4>
    ${dpRows.length ? html`<table class="mini"><thead><tr><th>Deployment</th><th>Run</th><th>State</th><th>Created</th><th>Finished</th></tr></thead><tbody>${dpRows}</tbody></table>` : html`<p class="hint">None recorded.</p>`}
    <h4>Matching attempts</h4>
    ${matchRows.length ? html`<table class="mini"><thead><tr><th>Deployment</th><th>Polynomial</th><th>State</th><th>Outcome</th><th>Finished</th></tr></thead><tbody>${matchRows}</tbody></table>` : html`<p class="hint">None yet.</p>`}
  `);
  drawer.hidden = false;
  drawer.querySelector('.close').addEventListener('click', () => {
    selected = null;
    history.replaceState(null, '', '#results');
    drawer.hidden = true;
    document.querySelectorAll('.cell.selected').forEach((c) => c.classList.remove('selected'));
  });
}
