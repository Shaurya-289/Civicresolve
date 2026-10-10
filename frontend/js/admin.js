import { api, login, logout, hasSession, clearSession, setSessionExpiredHandler } from './admin-api.js';
import { root, esc, renderShell, message, pending } from './admin-ui.js';
import { renderDashboard } from './admin-dashboard.js';
import { renderDetail } from './admin-detail.js';
import { renderResources } from './admin-resources.js';

const nav = document.getElementById('admin-nav');
const identity = document.getElementById('admin-identity');
let username = '';
let generation = 0;

function navigate(path, replace = false) {
  if (location.pathname !== path) { history[replace ? 'replaceState' : 'pushState']({}, '', path); window.scrollTo(0, 0); }
  render();
}

setSessionExpiredHandler(() => { username = ''; navigate('/portal/admin/login', true); });
document.addEventListener('click', event => {
  const link = event.target.closest('a[href^="/portal/admin/"]');
  if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) { event.preventDefault(); navigate(link.pathname); }
});
window.addEventListener('popstate', render);
document.getElementById('logout-button').addEventListener('click', async event => {
  pending(event.currentTarget, true, 'Signing out…');
  try { await logout(); navigate('/portal/admin/login', true); }
  catch (error) { message(error.message, 'error'); }
  finally { pending(event.currentTarget, false); }
});

function renderLogin() {
  nav.hidden = true;
  root.innerHTML = `<div class="login-wrap"><section class="login-card"><span class="eyebrow">ADMINISTRATOR ACCESS</span><h1>Sign in</h1><p>Manage reports, priorities and city resources.</p><p id="page-message" class="page-message" role="alert" hidden></p><form id="login-form"><label>Administrator ID / Username<input name="username" autocomplete="username" required maxlength="100"></label><label>Password<span class="password-row"><input name="password" type="password" autocomplete="current-password" required maxlength="200"><button type="button" id="password-toggle" class="text-button" aria-label="Show password">Show</button></span></label><button class="admin-button full" type="submit">Sign In</button></form><a class="back-link" href="/">← Back to Role Selection</a></section></div>`;
  const form = root.querySelector('#login-form');
  root.querySelector('#password-toggle').addEventListener('click', event => { const input = form.elements.password; const visible = input.type === 'text'; input.type = visible ? 'password' : 'text'; event.target.textContent = visible ? 'Show' : 'Hide'; event.target.setAttribute('aria-label', visible ? 'Show password' : 'Hide password'); });
  form.addEventListener('submit', async event => {
    event.preventDefault(); const button = form.querySelector('[type="submit"]'); pending(button, true, 'Signing in…'); message('');
    try { const admin = await login(form.elements.username.value, form.elements.password.value); username = admin.username; form.elements.password.value = ''; navigate('/portal/admin/dashboard', true); }
    catch (error) { message(error.message, 'error'); pending(button, false); }
  });
}

async function render() {
  const current = ++generation;
  const path = location.pathname;
  if (!hasSession()) { clearSession(); if (path !== '/portal/admin/login') history.replaceState({}, '', '/portal/admin/login'); renderLogin(); return; }
  if (path === '/portal/admin/login') { navigate('/portal/admin/dashboard', true); return; }
  nav.hidden = false;
  identity.textContent = username;
  for (const link of nav.querySelectorAll('[data-nav]')) link.setAttribute('aria-current', path.includes(link.dataset.nav) ? 'page' : 'false');
  try {
    if (path === '/portal/admin/resources') await renderResources();
    else if (/^\/portal\/admin\/complaints\/[0-9a-f-]{36}\/action$/i.test(path)) await renderDetail(path.split('/').at(-2), 'action');
    else if (/^\/portal\/admin\/complaints\/[0-9a-f-]{36}$/i.test(path)) await renderDetail(path.split('/').pop(), 'overview');
    else if (path === '/portal/admin/dashboard') await renderDashboard(navigate);
    else { navigate('/portal/admin/dashboard', true); return; }
  } catch (error) {
    if (current !== generation) return;
    renderShell('Unable to open this page', error.message, '<p><a href="/portal/admin/dashboard">Return to dashboard</a></p>');
  }
}

render();
