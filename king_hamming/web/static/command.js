// Command dialog: preview → review → confirm → run, with stale-state and
// password re-confirmation handling. Any element with data-command (and
// optional JSON data-params) opens it.
import { html, setHTML, api } from './util.js';
import { confirmPassword, session } from './account.js';

export const isOperator = () => (session() || {}).role === 'operator';

// Button markup for views; renders nothing for viewers.
export function button(name, params, text, variant = '') {
  if (!isOperator()) return '';
  return html`<button type="button" class="cmd ${variant}" data-command="${name}"
    data-params="${JSON.stringify(params || {})}">${text}</button>`;
}

function toast(message, tone = 'ok') {
  const element = document.createElement('div');
  element.className = `toast toast-${tone}`;
  element.textContent = message;
  document.body.appendChild(element);
  setTimeout(() => element.classList.add('toast-out'), 4000);
  setTimeout(() => element.remove(), 4600);
}

function body(preview, notice) {
  const changes = preview.changes.length ? html`<table class="mini changes">
      <thead><tr><th></th><th>Now</th><th>After</th></tr></thead>
      <tbody>${preview.changes.map((c) => html`<tr><td>${c.label}</td>
        <td class="mono">${c.before ?? '—'}</td><td class="mono">${c.after ?? '—'}</td></tr>`)}</tbody></table>` : '';
  return html`
    <h3>${preview.title}</h3>
    ${notice ? html`<p class="cmd-notice">${notice}</p>` : ''}
    <p>${preview.summary}</p>
    ${changes}
    ${preview.items.length ? html`<ul class="cmd-items">${preview.items.map((i) => html`<li>${i}</li>`)}</ul>` : ''}
    ${preview.warnings.length ? html`<ul class="cmd-warnings">${preview.warnings.map((w) => html`<li>${w}</li>`)}</ul>` : ''}
    ${preview.blockers.length ? html`<ul class="cmd-blockers">${preview.blockers.map((b) => html`<li>${b}</li>`)}</ul>` : ''}
    ${preview.confirm_text && !preview.blockers.length ? html`<label class="cmd-confirm"><span>Type
      <code>${preview.confirm_text}</code> to confirm</span><input name="confirm" autocomplete="off" spellcheck="false"></label>` : ''}
    ${preview.reauth && !preview.reauth_ok && !preview.blockers.length
      ? html`<p class="hint">You will be asked for your password.</p>` : ''}
    <p class="form-message" role="alert" hidden></p>
    <div class="dialog-buttons">
      <button type="button" class="secondary" value="cancel">${preview.blockers.length ? 'Close' : 'Cancel'}</button>
      ${preview.blockers.length ? '' : html`<button type="submit" value="run" class="${preview.confirm_text ? 'danger' : ''}">${preview.job ? 'Start job' : 'Run'}</button>`}
    </div>`;
}

export async function open(name, params = {}) {
  let preview;
  try {
    preview = await api('/api/command/preview', { method: 'POST', body: { name, params } });
  } catch (error) {
    toast(error.message, 'bad');
    return;
  }
  const dialog = document.createElement('dialog');
  dialog.className = 'command-dialog';
  document.body.appendChild(dialog);
  const close = () => { dialog.close(); dialog.remove(); };
  dialog.addEventListener('cancel', close);

  const show = (notice) => {
    setHTML(dialog, html`<form method="dialog" class="command-form">${body(preview, notice)}</form>`);
    const form = dialog.querySelector('form');
    const runButton = form.querySelector('button[value="run"]');
    const confirm = form.querySelector('input[name="confirm"]');
    const message = form.querySelector('.form-message');
    form.querySelector('button[value="cancel"]').addEventListener('click', close);
    if (confirm && runButton) {
      runButton.disabled = true;
      confirm.addEventListener('input', () => { runButton.disabled = confirm.value.trim() !== preview.confirm_text; });
      confirm.focus();
    } else {
      (runButton || form.querySelector('button[value="cancel"]')).focus();
    }
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!runButton) return;
      runButton.disabled = true;
      message.hidden = true;
      const attempt = async () => api('/api/command/run', {
        method: 'POST',
        body: { name, params, fingerprint: preview.fingerprint, confirm: confirm ? confirm.value : null },
      });
      try {
        if (preview.reauth && !preview.reauth_ok && !(await confirmPassword(`“${preview.title}” needs your password.`))) {
          runButton.disabled = false;
          return;
        }
        let result;
        try {
          result = await attempt();
        } catch (error) {
          if (error.body && error.body.reauth_required && await confirmPassword(`“${preview.title}” needs your password.`)) {
            result = await attempt();
          } else {
            throw error;
          }
        }
        close();
        toast(result.job ? `Started: ${result.title}` : `Done: ${result.title}`);
        window.dispatchEvent(new CustomEvent('kh:changed', { detail: result }));
        if (result.job) location.hash = '#activity';
      } catch (error) {
        if (error.status === 409 && error.body && error.body.preview) {
          preview = { ...error.body.preview, reauth_ok: preview.reauth_ok };
          show(error.message);
          return;
        }
        message.textContent = error.message;
        message.hidden = false;
        runButton.disabled = false;
      }
    });
  };
  show();
  dialog.showModal();
}

// A small dialog asking for one value before previewing a command.
export function ask({ title, text, fields, submit }) {
  const dialog = document.createElement('dialog');
  dialog.className = 'command-dialog';
  setHTML(dialog, html`<form method="dialog" class="command-form">
    <h3>${title}</h3>${text ? html`<p class="hint">${text}</p>` : ''}
    ${fields.map((f) => html`<label class="field-row">${f.label}
      <input name="${f.name}" value="${f.value ?? ''}" inputmode="${f.inputmode || 'numeric'}" autocomplete="off" required>
      ${f.hint ? html`<span class="hint">${f.hint}</span>` : ''}</label>`)}
    <div class="dialog-buttons">
      <button type="button" class="secondary" value="cancel">Cancel</button>
      <button type="submit" value="ok">Review</button>
    </div></form>`);
  document.body.appendChild(dialog);
  const close = () => { dialog.close(); dialog.remove(); };
  dialog.addEventListener('cancel', close);
  dialog.querySelector('button[value="cancel"]').addEventListener('click', close);
  dialog.querySelector('form').addEventListener('submit', (event) => {
    event.preventDefault();
    const values = Object.fromEntries(fields.map((f) => [f.name, dialog.querySelector(`input[name="${f.name}"]`).value.trim()]));
    close();
    submit(values);
  });
  dialog.showModal();
  dialog.querySelector('input').focus();
}

// One delegated handler for every [data-command] button on the page.
let installed = false;
export function install() {
  if (installed) return;
  installed = true;
  document.addEventListener('click', (event) => {
    const target = event.target.closest('[data-command]');
    if (!target) return;
    event.preventDefault();
    let params = {};
    try { params = JSON.parse(target.dataset.params || '{}'); } catch (error) { params = {}; }
    if (target.dataset.command === 'run.priority' && params.priority === undefined) {
      ask({
        title: 'Change priority',
        text: 'Higher numbers are leased first. Matching uses 100; DP uses 0.',
        fields: [{ name: 'priority', label: 'New priority', value: params.current ?? 0 }],
        submit: (values) => open('run.priority', { run_id: params.run_id, priority: values.priority }),
      });
      return;
    }
    open(target.dataset.command, params);
  });
}

export { toast };
