// Login form: POST JSON credentials, then go to the requested page.
const form = document.querySelector('.login-form');
const error = document.querySelector('.login-error');
const button = form.querySelector('button');
const next = new URLSearchParams(location.search).get('next') || '/';

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  error.hidden = true;
  button.disabled = true;
  try {
    const response = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user: form.user.value, password: form.password.value, next }),
    });
    const body = await response.json();
    if (!response.ok) {
      error.textContent = response.status === 429
        ? `Too many attempts. Try again in ${Math.ceil(body.retry_after || 1)} s.`
        : body.error || 'Sign-in failed.';
      error.hidden = false;
      form.password.value = '';
      form.password.focus();
      return;
    }
    location.replace(body.next || '/');
  } catch (failure) {
    error.textContent = `Could not reach the dashboard: ${failure.message}`;
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
});
form.user.focus();
