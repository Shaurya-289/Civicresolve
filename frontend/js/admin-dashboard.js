import { api } from './admin-api.js';
import { root, esc, date, score, badge, renderShell, message } from './admin-ui.js';

const categories = ['road_damage', 'streetlight', 'drainage', 'water_leak', 'waste'];
const levels = ['Low', 'Medium', 'High', 'Critical'];
const statuses = ['Submitted', 'Under Review', 'Assigned', 'Resolved'];
const state = { page: 1, search: '', category: '', severity: '', priority_level: '', status: '', emergency_only: false, sort: 'priority' };
const options = (values, title) => `<option value="">${title}</option>${values.map(value => `<option value="${esc(value)}">${esc(value.replaceAll('_', ' '))}</option>`).join('')}`;

function row(item) {
  const path = `/portal/admin/complaints/${encodeURIComponent(item.id)}`;
  const pending = item.effective_priority_score == null;
  return `<tr class="complaint-row ${item.emergency_review_required ? 'urgent-row' : ''}" data-path="${path}" tabindex="0" aria-label="Open complaint ${esc(item.id)}">
    <td><a href="${path}" class="record-link">${esc(item.id.slice(0, 8))}</a></td><td><strong>${esc(item.title || 'Untitled complaint')}</strong><small>${esc(item.description_summary || '')}</small></td>
    <td>${esc((item.category || 'Pending').replaceAll('_', ' '))}</td><td>${esc(item.severity || 'Pending')}</td>
    <td>${pending ? badge('Pending evaluation', 'warning') : esc(score(item.effective_priority_score))}</td><td>${badge(item.effective_priority_level || 'Pending evaluation', pending ? 'warning' : '')}</td>
    <td>${item.emergency_review_required ? badge('Urgent review', 'danger') : '—'}</td><td>${badge(item.status)}</td><td>${esc(date(item.created_at))}</td><td><a href="${path}">View</a></td></tr>`;
}

export async function renderDashboard(navigate) {
  renderShell('Complaint dashboard', 'Live operational queue ordered by emergency review and priority.', `<section class="summary-grid" id="summary-grid"><p>Loading summary…</p></section>
    <section class="admin-card"><div class="section-title-row"><h2>Complaints</h2><span id="queue-count"></span></div>
      <form id="queue-filters" class="filter-grid" role="search"><label>Search<input name="search" placeholder="Title, description or location"></label><label>Category<select name="category">${options(categories, 'All categories')}</select></label><label>Severity<select name="severity">${options(['low','medium','high'], 'All severities')}</select></label><label>Priority level<select name="priority_level">${options(levels, 'All levels')}</select></label><label>Status<select name="status">${options(statuses, 'All statuses')}</select></label><label>Sort by<select name="sort"><option value="priority">Priority</option><option value="newest">Newest</option><option value="oldest">Oldest</option></select></label><label class="checkbox-line"><input type="checkbox" name="emergency_only"> Emergency only</label><button class="admin-button" type="submit">Apply filters</button><button class="admin-button secondary" type="reset">Clear</button></form>
      <div class="table-scroll"><table class="complaint-table"><thead><tr><th>ID</th><th>Complaint</th><th>Category</th><th>Severity</th><th>Priority</th><th>Level</th><th>Emergency</th><th>Status</th><th>Submitted</th><th></th></tr></thead><tbody id="queue-body"><tr><td colspan="10">Loading complaints…</td></tr></tbody></table></div>
      <div class="pager"><button id="previous-page" class="admin-button secondary" type="button">Previous</button><span id="page-indicator"></span><button id="next-page" class="admin-button secondary" type="button">Next</button></div></section>`);
  const form = root.querySelector('#queue-filters');
  for (const [key, value] of Object.entries(state)) if (form.elements[key]) form.elements[key].type === 'checkbox' ? form.elements[key].checked = value : form.elements[key].value = value;
  form.addEventListener('submit', event => { event.preventDefault(); for (const key of ['search','category','severity','priority_level','status','sort']) state[key] = form.elements[key].value.trim(); state.emergency_only = form.elements.emergency_only.checked; state.page = 1; loadQueue(); });
  form.addEventListener('reset', () => { Object.assign(state, { page: 1, search: '', category: '', severity: '', priority_level: '', status: '', emergency_only: false, sort: 'priority' }); setTimeout(loadQueue, 0); });
  root.querySelector('#previous-page').addEventListener('click', () => { state.page--; loadQueue(); });
  root.querySelector('#next-page').addEventListener('click', () => { state.page++; loadQueue(); });
  root.querySelector('#queue-body').addEventListener('click', event => { const tr = event.target.closest('tr[data-path]'); if (tr && !event.target.closest('a')) navigate(tr.dataset.path); });
  root.querySelector('#queue-body').addEventListener('keydown', event => { if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('tr[data-path]')) { event.preventDefault(); navigate(event.target.dataset.path); } });
  try { const summary = await api('/admin/summary'); const names = [['total','Total complaints'],['pending_review','Pending review'],['emergency_review','Emergency review'],['resolved','Resolved']]; root.querySelector('#summary-grid').innerHTML = names.map(([key,name]) => `<div class="stat-card"><strong>${esc(summary[key])}</strong><span>${name}</span></div>`).join(''); }
  catch (error) { root.querySelector('#summary-grid').textContent = 'Summary unavailable.'; message(error.message, 'error'); }
  await loadQueue();
}

async function loadQueue() {
  const params = new URLSearchParams({ page: String(state.page), page_size: '20', sort_by: state.sort === 'priority' ? 'priority' : 'submitted_at', order: state.sort === 'oldest' ? 'asc' : 'desc' });
  for (const key of ['search','category','severity','priority_level','status']) if (state[key]) params.set(key, state[key]);
  if (state.emergency_only) params.set('emergency_only', 'true');
  const body = root.querySelector('#queue-body'); if (!body) return; body.innerHTML = '<tr><td colspan="10">Loading complaints…</td></tr>';
  try {
    const data = await api(`/admin/complaints?${params}`);
    body.innerHTML = data.items.length ? data.items.map(row).join('') : '<tr><td colspan="10">No complaints match these filters.</td></tr>';
    root.querySelector('#queue-count').textContent = `${data.total} matching`;
    root.querySelector('#page-indicator').textContent = `Page ${data.page} of ${Math.max(1, data.total_pages)}`;
    root.querySelector('#previous-page').disabled = data.page <= 1;
    root.querySelector('#next-page').disabled = data.page >= data.total_pages;
  } catch (error) { body.innerHTML = '<tr><td colspan="10">Could not load complaints.</td></tr>'; message(error.message, 'error'); }
}
