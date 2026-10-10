export const root = document.getElementById('admin-root');
export const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
export const shown = value => value === null || value === undefined || value === '' ? 'Not available' : String(value);
export const date = value => value ? new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : 'Not available';
export const score = value => value === null || value === undefined ? 'Pending evaluation' : Number(value).toFixed(1);
export const label = value => String(value ?? 'Not available').replaceAll('_', ' ');
export const badge = (value, kind = '') => `<span class="admin-badge ${kind}">${esc(shown(value))}</span>`;
export function message(text, type = 'info') { const node = document.getElementById('page-message'); if (node) { node.textContent = text; node.className = `page-message ${type}`; node.hidden = !text; } }
export function pending(button, busy, text = 'Working…') { if (!button) return; if (busy) { button.dataset.originalText = button.textContent; button.textContent = text; button.disabled = true; button.classList.add('is-loading'); } else { button.textContent = button.dataset.originalText || button.textContent; button.disabled = false; button.classList.remove('is-loading'); } }
export function confirmed(text) { return window.confirm(text); }
export function renderShell(title, subtitle, content, back = '') {
  root.innerHTML = `${back ? `<a class="back-link" href="${esc(back)}">← Back</a>` : ''}<div class="page-heading"><div><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div></div><p id="page-message" class="page-message" role="status" hidden></p>${content}`;
}
export function safeList(items, empty = 'None recorded') { return Array.isArray(items) && items.length ? `<ul class="plain-list">${items.map(item => `<li>${esc(typeof item === 'string' ? item : JSON.stringify(item))}</li>`).join('')}</ul>` : `<p class="muted">${esc(empty)}</p>`; }
