// Shared helpers: escaped HTML templates, number/time formatting, tooltips.

class Safe {
  constructor(text) { this.text = text; }
  toString() { return this.text; }
}

function render(value) {
  if (value === null || value === undefined || value === false) return '';
  if (value instanceof Safe) return value.text;
  if (Array.isArray(value)) return value.map(render).join('');
  return String(value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Tagged template: html`<b>${text}</b>` escapes text; nested html`` stays raw.
export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((value, index) => { out += render(value) + strings[index + 1]; });
  return new Safe(out);
}

export function setHTML(element, safe) {
  element.innerHTML = render(safe);
}

const SUPERSCRIPT = { 0: '⁰', 1: '¹', 2: '²', 3: '³', 4: '⁴', 5: '⁵', 6: '⁶', 7: '⁷', 8: '⁸', 9: '⁹' };
export const sup = (n) => String(n).split('').map((d) => SUPERSCRIPT[d] ?? d).join('');
export const field = (p, r) => `${p}${sup(r)}`;

export const fmtInt = (n) => (n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US'));

export function fmtCompact(n) {
  if (n === null || n === undefined) return '—';
  const units = [[1e15, 'P'], [1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'k']];
  for (const [size, suffix] of units) {
    if (n >= size) {
      const value = n / size;
      return `${value >= 100 ? value.toFixed(0) : value >= 10 ? value.toFixed(1) : value.toFixed(2)}${suffix}`;
    }
  }
  return String(n);
}

export function fmtBytes(n) {
  if (!n) return '0';
  const gib = n / 1024 ** 3;
  return gib >= 1 ? `${gib.toFixed(gib >= 10 ? 0 : 1)} GiB` : `${(n / 1024 ** 2).toFixed(0)} MiB`;
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  seconds = Math.max(0, seconds);
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) {
    const minutes = Math.round(seconds / 60);
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return m ? `${h}h ${m}m` : `${h}h`;
  }
  return `${(seconds / 86400).toFixed(1)}d`;
}

export const now = () => Date.now() / 1000;
export const ago = (timestamp) => (timestamp ? `${fmtDuration(now() - timestamp)} ago` : '—');
export const pct = (fraction) => `${Math.round((fraction || 0) * 100)}%`;
export const shortId = (id) => (id ? id.slice(0, 8) : '');

export function fmtTime(timestamp) {
  if (!timestamp) return '—';
  return new Date(timestamp * 1000).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

// Coefficients are listed from the constant term upward.
export function fmtPoly(coefficients) {
  if (!Array.isArray(coefficients)) return '—';
  const terms = [];
  for (let power = coefficients.length - 1; power >= 0; power -= 1) {
    const c = coefficients[power];
    if (!c) continue;
    const x = power === 0 ? '' : power === 1 ? 'x' : `x${sup(power)}`;
    terms.push(power === 0 ? String(c) : `${c === 1 ? '' : c}${x}`);
  }
  return terms.join(' + ') || '0';
}

// One floating tooltip shared by every view. contentFor(target) returns html`` or null.
const tip = document.createElement('div');
tip.className = 'tooltip';
tip.hidden = true;
document.body.appendChild(tip);

export function hideTooltip() { tip.hidden = true; }

function place(event) {
  const pad = 14;
  const { innerWidth, innerHeight } = window;
  const rect = tip.getBoundingClientRect();
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + rect.width > innerWidth - 8) x = Math.max(8, event.clientX - rect.width - pad);
  if (y + rect.height > innerHeight - 8) y = Math.max(8, event.clientY - rect.height - pad);
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
}

// Show the shared tooltip with `content` beside the pointer (for canvas grids, which hit-test
// themselves instead of using per-element targets).
export function showTooltip(event, content) {
  setHTML(tip, content);
  tip.hidden = false;
  place(event);
}

export function tooltips(container, selector, contentFor) {
  const show = (event) => {
    const target = event.target.closest(selector);
    if (!target || !container.contains(target)) { hideTooltip(); return; }
    const content = contentFor(target);
    if (!content) { hideTooltip(); return; }
    setHTML(tip, content);
    tip.hidden = false;
    place(event);
  };
  container.addEventListener('pointermove', show);
  container.addEventListener('pointerdown', show); // tap on touch screens
  container.addEventListener('pointerleave', hideTooltip);
}

// SVG sparkline of values in [0, 1].
export function sparkline(values, width = 96, height = 24) {
  if (!values || !values.length) return html``;
  const step = width / Math.max(1, values.length - 1);
  const points = values.map((v, i) => `${(i * step).toFixed(1)},${(height - v * height).toFixed(1)}`);
  const area = `M0,${height} L${points.join(' L')} L${width},${height} Z`;
  return html`<svg class="spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
    <path class="spark-area" d="${area}"></path>
    <polyline class="spark-line" points="${points.join(' ')}"></polyline>
  </svg>`;
}

export function bar(fraction, className = '') {
  const width = Math.max(0, Math.min(1, fraction || 0)) * 100;
  return html`<svg class="bar ${className}" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">
    <rect class="bar-track" x="0" y="0" width="100" height="6" rx="3"></rect>
    <rect class="bar-fill" x="0" y="0" width="${width.toFixed(2)}" height="6" rx="3"></rect>
  </svg>`;
}

// ----- API calls with the session's CSRF token ---------------------------

let csrfToken = null;
export function setCsrf(token) { csrfToken = token; }

export function toLogin() {
  location.assign(`/login?next=${encodeURIComponent(location.pathname + location.hash)}`);
}

// Front-end version this page was loaded with; a different one means a redeploy.
let loadedVersion = null;

// fetch JSON; POSTs carry the CSRF token. A 401 sends the browser to the login page.
export async function api(path, { method = 'GET', body } = {}) {
  const headers = {};
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
  }
  const response = await fetch(path, {
    method, headers, cache: 'no-store', redirect: 'manual',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  // The dashboard's API never redirects. A redirect means something in front of
  // it (Cloudflare Access, when its session expires) wants the browser itself:
  // reload, so the top-level page goes through that sign-in and comes back.
  if (response.type === 'opaqueredirect') {
    location.reload();
    throw new Error('signing in again');
  }
  // After a redeploy the server's front-end code differs from what this page runs:
  // reload once so open tabs pick up new views instead of rendering new data with old code.
  const version = response.headers.get('X-Dashboard-Version');
  if (version) {
    if (loadedVersion === null) loadedVersion = version;
    else if (version !== loadedVersion) {
      location.reload();
      throw new Error('dashboard updated; reloading');
    }
  }
  let value = null;
  try { value = await response.json(); } catch (error) { value = null; }
  if (response.status === 401 && path !== '/api/reauth' && path !== '/api/password') {
    toLogin();
    throw new Error('signed out');
  }
  if (!response.ok) {
    const error = new Error((value && value.error) || `HTTP ${response.status}`);
    error.status = response.status;
    error.body = value;
    throw error;
  }
  return value;
}

// Short GPU label: "NVIDIA GeForce RTX 3060 Laptop GPU" -> "RTX 3060 Laptop".
export function gpuName(name) {
  return String(name || 'GPU').replace(/^NVIDIA\s+/, '').replace(/^GeForce\s+/, '').replace(/\s+GPU$/, '') || 'GPU';
}
