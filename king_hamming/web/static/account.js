// Account menu (sign out, change password) and the password re-confirmation
// dialog that risky commands will require.
import { html, setHTML, api, setCsrf, now } from './util.js';

let current = null;

export function session() { return current; }

export function mount(element, info) {
  current = info;
  setHTML(element, html`
    <details class="account-menu">
      <summary>${info.user} <span class="role role-${info.role}">${info.role}</span></summary>
      <div class="account-panel">
        <form class="password-form">
          <h4>Change password</h4>
          <label>Current password <input name="current" type="password" autocomplete="current-password" required></label>
          <label>New password <input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
          <label>Repeat new password <input name="repeat" type="password" autocomplete="new-password" required></label>
          <p class="form-message" role="alert" hidden></p>
          <button type="submit">Change password</button>
          <p class="hint">Changing it signs you out everywhere.</p>
        </form>
        <button type="button" class="sign-out">Sign out</button>
      </div>
    </details>`);

  element.querySelector('.sign-out').addEventListener('click', async () => {
    try { await api('/api/logout', { method: 'POST', body: {} }); } finally { location.assign('/login'); }
  });

  const form = element.querySelector('.password-form');
  const message = form.querySelector('.form-message');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    message.hidden = false;
    if (form.new.value !== form.repeat.value) {
      message.textContent = 'The new passwords do not match.';
      return;
    }
    try {
      await api('/api/password', { method: 'POST', body: { current: form.current.value, new: form.new.value } });
      location.assign('/login');
    } catch (error) {
      message.textContent = error.message;
    }
  });
}

// Resolve true once the user has entered their password within the last
// 10 minutes (asking now if needed), false if they cancel.
export function confirmPassword(reason) {
  if (current && current.reauth_until > now() + 5) return Promise.resolve(true);
  return new Promise((resolve) => {
    const dialog = document.createElement('dialog');
    dialog.className = 'reauth-dialog';
    setHTML(dialog, html`<form method="dialog" class="reauth-form">
      <h3>Confirm it's you</h3>
      <p class="hint">${reason || 'This action needs your password again.'}</p>
      <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
      <p class="form-message" role="alert" hidden></p>
      <div class="dialog-buttons">
        <button type="button" value="cancel" class="secondary">Cancel</button>
        <button type="submit" value="ok">Confirm</button>
      </div>
    </form>`);
    document.body.appendChild(dialog);
    const form = dialog.querySelector('form');
    const message = dialog.querySelector('.form-message');
    const finish = (value) => { dialog.close(); dialog.remove(); resolve(value); };
    dialog.querySelector('button[value="cancel"]').addEventListener('click', () => finish(false));
    dialog.addEventListener('cancel', () => finish(false));
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        const info = await api('/api/reauth', { method: 'POST', body: { password: form.password.value } });
        current = info;
        setCsrf(info.csrf);
        finish(true);
      } catch (error) {
        message.textContent = error.status === 429 ? 'Too many attempts; wait a moment.' : error.message;
        message.hidden = false;
        form.password.value = '';
      }
    });
    dialog.showModal();
    form.password.focus();
  });
}
