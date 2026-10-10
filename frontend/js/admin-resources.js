import { api } from './admin-api.js';
import { root, esc, shown, label, renderShell, message, pending, confirmed } from './admin-ui.js';

const currency = value => value == null ? 'Not set' : `INR ${Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
let resources = [];
let policy = null;

function card(item) {
  return `<article class="inventory-card"><div><span class="resource-kind">${esc(label(item.kind))}</span><h3>${esc(item.name)}</h3><p>${item.available_quantity == null ? 'Stock not recorded' : `${esc(item.available_quantity)} available`} · ${esc(currency(item.unit_cost))} per unit</p><small>${item.verified ? 'Verified' : 'Unverified'} · ${item.demo_data ? 'Synthetic demo data' : 'Operational data'}</small></div><button class="admin-button secondary edit-resource" type="button" data-id="${esc(item.id)}" aria-label="Edit ${esc(item.name)}">Edit</button></article>`;
}

function showResource(item = null) {
  const panel = root.querySelector('#resource-editor');
  const form = root.querySelector('#resource-form');
  form.reset();
  form.elements.id.value = item?.id || '';
  form.elements.name.value = item?.name || '';
  form.elements.kind.value = item?.kind || 'workforce';
  form.elements.available_quantity.value = item?.available_quantity ?? '';
  form.elements.unit_cost.value = item?.unit_cost ?? '';
  form.elements.verified.checked = !!item?.verified;
  form.elements.demo_data.checked = !!item?.demo_data;
  root.querySelector('#resource-editor-title').textContent = item ? `Edit ${item.name}` : 'Add resource';
  panel.hidden = false;
  panel.scrollIntoView({ block: 'nearest' });
  form.elements.name.focus();
}

function generatedId(name) {
  const slug = name.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 55) || 'resource';
  return `${slug}-${crypto.randomUUID().slice(0, 8)}`;
}

async function refresh() {
  [resources, policy] = await Promise.all([api('/admin/resources'), api('/admin/resources/budget/policy')]);
  root.querySelector('#inventory-list').innerHTML = resources.length ? resources.map(card).join('') : '<div class="empty-inventory"><strong>No resources yet</strong><p>Add a real catalog item, or load clearly labeled demo resources for testing.</p></div>';
  root.querySelector('#budget-value').textContent = currency(policy.max_amount);
  root.querySelector('#budget-label').textContent = policy.max_amount == null ? 'No budget ceiling configured' : policy.verified ? 'Verified configured ceiling' : 'Unverified configured ceiling';
  root.querySelector('#budget-note').textContent = 'Remaining balance is checked by the server during allocation; it is not shown here.';
  const form = root.querySelector('#budget-form');
  form.elements.max_amount.value = policy.max_amount ?? '';
  form.elements.verified.checked = policy.verified;
}

export async function renderResources() {
  renderShell('Resources', 'Manage the inventory available to complaint reviewers.', `
    <section class="admin-card"><div class="section-title-row"><div><h2>Inventory</h2><p class="muted">Quantities and costs come from this catalog. Verified items can be reserved after approval.</p></div><div class="button-row"><button id="load-demo" class="admin-button secondary" type="button">Load Demo Resources</button><button id="add-resource" class="admin-button" type="button">+ Add Resource</button></div></div><div id="inventory-list" class="inventory-cards">Loading inventory…</div></section>
    <section id="resource-editor" class="admin-card editor-card" hidden><div class="section-title-row"><h2 id="resource-editor-title">Add resource</h2><button id="cancel-resource" class="text-button" type="button">Close</button></div><form id="resource-form" class="stack-form"><input name="id" type="hidden"><div class="editor-grid"><label>Resource name<input name="name" required maxlength="150" placeholder="e.g. Road repair crew"></label><label>Type<select name="kind"><option value="workforce">Workforce</option><option value="equipment">Equipment</option><option value="materials">Materials</option></select></label><label>Available quantity<input name="available_quantity" type="number" min="0" step="1" placeholder="Not recorded"></label><label>Unit cost (INR)<input name="unit_cost" type="number" min="0" step="0.01" placeholder="Not recorded"></label></div><div class="button-row"><label class="checkbox-line"><input name="verified" type="checkbox"> Verified operational data</label><label class="checkbox-line"><input name="demo_data" type="checkbox"> Synthetic demo data</label></div><button class="admin-button" type="submit">Save resource</button></form></section>
    <section class="admin-card budget-card"><div><h2>Budget</h2><strong id="budget-value">Loading…</strong><p id="budget-label" class="muted"></p><p id="budget-note" class="muted"></p></div><button id="edit-budget" class="admin-button secondary" type="button">Edit budget</button></section>
    <section id="budget-editor" class="admin-card editor-card" hidden><div class="section-title-row"><h2>Edit budget ceiling</h2><button id="cancel-budget" class="text-button" type="button">Close</button></div><form id="budget-form" class="stack-form"><label>Configured ceiling (INR)<input name="max_amount" type="number" min="0" step="0.01" placeholder="Not set"></label><label class="checkbox-line"><input name="verified" type="checkbox"> This ceiling has been verified</label><p class="muted">This is a configured ceiling, not a live municipal balance.</p><button class="admin-button" type="submit">Save budget</button></form></section>`, '/portal/admin/dashboard');

  root.querySelector('#add-resource').addEventListener('click', () => showResource());
  root.querySelector('#cancel-resource').addEventListener('click', () => { root.querySelector('#resource-editor').hidden = true; });
  root.querySelector('#inventory-list').addEventListener('click', event => {
    const button = event.target.closest('.edit-resource');
    const item = resources.find(row => row.id === button?.dataset.id);
    if (item) showResource(item);
  });
  root.querySelector('#edit-budget').addEventListener('click', () => { root.querySelector('#budget-editor').hidden = false; root.querySelector('#budget-editor').scrollIntoView({ block: 'nearest' }); });
  root.querySelector('#cancel-budget').addEventListener('click', () => { root.querySelector('#budget-editor').hidden = true; });

  root.querySelector('#resource-form').addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget, button = form.querySelector('[type="submit"]');
    const quantity = form.elements.available_quantity.value.trim(), cost = form.elements.unit_cost.value.trim();
    if (quantity !== '' && (!/^\d+$/.test(quantity) || !Number.isSafeInteger(Number(quantity)))) { message('Quantity must be a whole nonnegative number.', 'error'); return; }
    if (cost !== '' && (!form.elements.unit_cost.validity.valid || !Number.isFinite(Number(cost)))) { message('Enter a valid nonnegative unit cost.', 'error'); return; }
    const id = form.elements.id.value || generatedId(form.elements.name.value.trim());
    const body = { id, name: form.elements.name.value.trim(), kind: form.elements.kind.value, available_quantity: quantity === '' ? null : Number(quantity), unit_cost: cost === '' ? null : Number(cost), verified: form.elements.verified.checked, demo_data: form.elements.demo_data.checked };
    pending(button, true, 'Saving…');
    try { await api(`/admin/resources/${encodeURIComponent(id)}`, { method: 'PUT', body }); await refresh(); root.querySelector('#resource-editor').hidden = true; message('Resource saved.', 'success'); }
    catch (error) { message(error.message, 'error'); } finally { pending(button, false); }
  });

  root.querySelector('#budget-form').addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget, button = form.querySelector('[type="submit"]'), raw = form.elements.max_amount.value.trim();
    if (raw !== '' && (!form.elements.max_amount.validity.valid || !Number.isFinite(Number(raw)))) { message('Enter a valid nonnegative budget.', 'error'); return; }
    if (!confirmed('Save this budget ceiling?')) return;
    pending(button, true, 'Saving…');
    try { await api('/admin/resources/budget/policy', { method: 'PUT', body: { max_amount: raw === '' ? null : Number(raw), currency: 'INR', verified: form.elements.verified.checked } }); await refresh(); root.querySelector('#budget-editor').hidden = true; message('Budget saved.', 'success'); }
    catch (error) { message(error.message, 'error'); } finally { pending(button, false); }
  });

  root.querySelector('#load-demo').addEventListener('click', async event => {
    if (!confirmed('Add missing synthetic demo resources? Existing inventory will not be changed.')) return;
    const button = event.currentTarget; pending(button, true, 'Loading…');
    try {
      const result = await api('/admin/resources/demo', { method: 'POST' });
      await refresh(); message(result.added ? `${result.added} synthetic demo resources added. They are unverified.` : 'Demo resources are already present. Existing inventory was left unchanged.', result.added ? 'success' : 'info');
    } catch (error) { message(error.message, 'error'); await refresh(); } finally { pending(button, false); }
  });

  try { await refresh(); } catch (error) { message(error.message, 'error'); root.querySelector('#inventory-list').textContent = 'Inventory could not be loaded.'; }
}
