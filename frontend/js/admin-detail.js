import { api, requestKey } from './admin-api.js';
import { root, esc, shown, date, score, label, badge, safeList, renderShell, message, pending, confirmed } from './admin-ui.js';

const nextStatus = { Submitted: 'Under Review', 'Under Review': 'Assigned', Assigned: 'Resolved' };
const endpoint = id => `/admin/complaints/${encodeURIComponent(id)}`;
const page = id => `/portal/admin/complaints/${encodeURIComponent(id)}`;
const value = item => esc(shown(item));
const money = amount => amount == null ? 'Not set' : `INR ${Number(amount).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

function overview(data) {
  const priority = data.effective_priority_score ?? data.priority_score;
  const level = data.effective_priority_level ?? data.priority_level;
  const reasons = [...new Set([...(data.classification_review?.review_reasons || []), ...(data.review_reasons || [])])];
  renderShell(data.title || 'Untitled complaint', `Complaint ID ${data.id}`, `
    <div class="overview-highlights"><div class="highlight"><span>Priority score</span><strong>${esc(score(priority))}</strong><small>${priority == null ? 'Pending evaluation' : esc(shown(level))}</small></div><div class="highlight"><span>Workflow</span>${badge(data.status)}</div>${data.emergency_review_required ? '<div class="highlight alert"><span>Attention</span><strong>Emergency review</strong></div>' : ''}</div>
    ${priority == null ? '<div class="notice advisory">Priority evaluation is pending. Review the report before taking action.</div>' : ''}
    ${data.emergency_review_required ? `<div class="notice urgent"><strong>Urgent human review required</strong>${safeList(data.emergency_flags?.map(item => item.explanation), 'Possible emergency detected.')}</div>` : ''}
    <section class="admin-card overview-card"><h2>Complaint details</h2><p class="complaint-description preserve-lines">${value(data.description)}</p><dl class="facts"><dt>Reported location</dt><dd>${value(data.location)}</dd><dt>Area affected</dt><dd>${esc(label(data.affected_area))}</dd><dt>Duration</dt><dd>${data.days_existing == null ? 'Not available' : `${esc(data.days_existing)} days`}</dd><dt>Submitted</dt><dd>${esc(date(data.created_at))}</dd></dl></section>
    <section class="admin-card overview-card"><h2>ML classification</h2><dl class="facts"><dt>Category</dt><dd>${esc(label(data.category))}</dd><dt>Severity</dt><dd>${esc(label(data.severity))}</dd></dl>${reasons.length ? `<div class="review-note"><strong>Review suggested</strong>${safeList(reasons.map(label))}</div>` : '<p class="muted">Predicted category and severity should be checked against the report.</p>'}</section>
    <div class="step-actions"><a class="admin-button action-link" href="${page(data.id)}/action">Continue to Action &amp; Allocation →</a></div>`, '/portal/admin/dashboard');
}

function recommendationView(record, data) {
  if (!record) return '<p class="muted">No AI recommendation yet. Generate one when you are ready to review this complaint.</p>';
  const result = record.result || {};
  const population = result.gemini_population_recommendation;
  const resources = result.resource_recommendations;
  const review = data.recommendation_reviews?.find(item => item.recommendation_id === record.id);
  if (!population && !resources) return '<div class="notice urgent">AI analysis was unavailable. No recommendation was produced.</div>';
  const proposed = result.proposed_revised_evaluation?.priority_score;
  const suggested = [...(resources?.workforce || []), ...(resources?.equipment || []), ...(resources?.materials || [])];
  const accepted = data.population_decisions?.some(item => item.recommendation_id === record.id && item.decision === 'accept');
  const ignored = data.population_decisions?.some(item => item.recommendation_id === record.id && item.decision === 'reject');
  return `<div class="recommendation-summary"><div><span class="eyebrow">AI ADVICE · HUMAN REVIEW REQUIRED</span><h3>${value(resources?.department)}</h3><p>${value(resources?.explanation)}</p><p><strong>Suggested action</strong></p>${safeList(resources?.actions, 'No action suggested.')}</div><div><strong>Suggested resources</strong>${safeList(suggested.map(item => `${item.name}: ${item.quantity}`), 'No resources suggested.')}<p><strong>Suggested budget:</strong> ${resources?.budget_estimate?.amount == null ? 'Not available' : esc(money(resources.budget_estimate.amount))} ${resources?.budget_estimate?.basis ? `(${value(resources.budget_estimate.basis)})` : ''}</p></div></div>
    ${population?.proposed_score != null ? `<div class="suggestion"><div><strong>Suggested priority adjustment: ${esc(score(data.priority_score))} → ${proposed == null ? 'Pending' : esc(score(proposed))}</strong><p>${value(population.explanation)}</p><small>Population impact score: ${value(population.original_score)} → ${value(population.proposed_score)}. The original priority stays recorded.</small></div><div class="button-row"><button class="admin-button secondary population-choice" type="button" data-choice="accept" ${accepted || ignored || review?.decision === 'reject' ? 'disabled' : ''}>Accept</button><button class="admin-button secondary population-choice" type="button" data-choice="reject" ${accepted || ignored ? 'disabled' : ''}>Ignore</button><button class="admin-button secondary population-choice" type="button" data-choice="manual">Modify</button></div></div>` : ''}
    ${population?.proposed_score != null ? `<form id="population-form" class="stack-form decision-panel" hidden><h3 id="population-heading">Review adjustment</h3><input name="decision" type="hidden"><label id="manual-score-label" hidden>Population impact score (0–100)<input name="manual_score" type="number" min="0" max="100" step="1"></label><label>Reason for decision<textarea name="reason" required minlength="3" maxlength="1000" placeholder="Briefly explain your decision"></textarea></label><div id="population-preview" class="preview-box" hidden></div><div class="button-row"><button id="preview-button" class="admin-button secondary" type="button">Preview effect</button><button class="admin-button" type="submit">Confirm decision</button><button id="cancel-population" class="admin-button secondary" type="button">Cancel</button></div></form>` : ''}
    ${data.population_decisions?.length ? `<p class="muted">Latest priority decision: ${esc(label(data.population_decisions.at(-1).decision))} · ${esc(date(data.population_decisions.at(-1).created_at))}</p>` : ''}
    <details class="quiet-details"><summary>More about this AI recommendation</summary><p>Generated ${esc(date(record.created_at))}. Model: ${value(result.review_metadata?.model_id)}.</p>${safeList([...(result.review_metadata?.warnings || []), ...(resources?.limitations || [])], 'No additional limitations recorded. Verify before acting.')}<p>Timeframe: ${resources?.estimated_days == null ? 'Not available' : `${esc(resources.estimated_days)} days`}</p></details>
    <div class="review-actions">${review ? `<p class="muted">Recommendation ${esc(label(review.decision))}: ${esc(review.reason)}</p>` : `<form id="recommendation-review" class="inline-form"><label>Review note<input name="reason" required minlength="3" maxlength="1000" placeholder="Why are you using or dismissing this advice?"></label><button class="admin-button secondary" name="decision" value="accept_for_review">Mark reviewed</button><button class="admin-button secondary" name="decision" value="reject">Dismiss advice</button></form>`}</div>`;
}

function allocationRows(resources, previous = null) {
  if (!resources.length) return '<p class="muted">No inventory is configured. Add resources on the Resources page before allocating.</p>';
  return resources.map(item => {
    const known = Number.isSafeInteger(item.available_quantity) && item.available_quantity >= 0;
    const quantity = previous?.items?.find(row => row.resource_id === item.id)?.quantity || 0;
    return `<div class="resource-picker" data-resource-id="${esc(item.id)}"><div class="picker-label"><strong>${esc(item.name)}</strong><small>${esc(label(item.kind))} · ${known ? `${esc(item.available_quantity)} available` : 'Stock not recorded'} · ${item.unit_cost == null ? 'Cost not recorded' : `${esc(money(item.unit_cost))} / unit`} · ${item.verified ? 'Verified' : 'Unverified'}${item.demo_data ? ' · Demo data' : ''}</small></div><div class="picker-controls"><input class="quantity-slider" type="range" min="0" max="${known ? esc(item.available_quantity) : 0}" step="1" value="${esc(quantity)}" ${known && item.available_quantity > 0 ? '' : 'disabled'} aria-label="${esc(item.name)} quantity slider"><label><span class="sr-only">${esc(item.name)} quantity</span><input class="quantity-number" type="number" min="0" max="${known ? esc(item.available_quantity) : 0}" step="1" value="${esc(quantity)}" ${known ? '' : 'disabled'} aria-label="${esc(item.name)} quantity"></label></div>${known ? '' : '<small class="field-hint">Set available stock on the Resources page first.</small>'}</div>`;
  }).join('');
}

function history(data, audit) {
  const events = [
    ...(data.status_history || []).map(item => ({ at: item.created_at, text: `Status: ${item.from_status} → ${item.to_status}` })),
    ...(data.population_decisions || []).map(item => ({ at: item.created_at, text: `Priority adjustment ${label(item.decision)}` })),
    ...(data.resource_allocations || []).map(item => ({ at: item.created_at, text: `Allocation ${label(item.decision)}` })),
    ...(audit || []).filter(item => !['status_changed', 'resource_allocation', 'population_decision'].includes(item.action)).map(item => ({ at: item.created_at, text: label(item.action) })),
  ].sort((a, b) => new Date(b.at) - new Date(a.at));
  return events.length ? `<ol class="compact-history">${events.map(item => `<li><span>${esc(item.text)}</span><small>${esc(date(item.at))}</small></li>`).join('')}</ol>` : '<p class="muted">No actions recorded yet.</p>';
}

function actionView(data, resources, policy, audit) {
  const recommendation = data.recommendations?.at(-1);
  const relatedAllocations = data.resource_allocations?.filter(item => item.recommendation_id === (recommendation?.id || null)) || [];
  const pendingAllocation = relatedAllocations.filter(item => item.decision === 'pending_verification').at(-1);
  const retryReady = pendingAllocation && policy.verified && policy.max_amount != null && pendingAllocation.items?.length && pendingAllocation.items.every(row => {
    const item = resources.find(resource => resource.id === row.resource_id);
    return item?.verified && item.available_quantity != null && item.available_quantity >= row.quantity && item.unit_cost != null;
  });
  const allocationRecorded = relatedAllocations.some(item => item.decision === 'approved') || (pendingAllocation && !retryReady);
  const allocationRejected = data.resource_allocations?.some(item => item.recommendation_id === (recommendation?.id || null) && item.decision === 'rejected');
  const next = nextStatus[data.status];
  const budgetMax = policy.verified && Number.isFinite(policy.max_amount) && policy.max_amount > 0 ? policy.max_amount : null;
  renderShell('Action & Allocation', `${data.title || 'Untitled complaint'} · ${data.id}`, `
    <section class="admin-card compact-status"><span>Current workflow status</span>${badge(data.status)}<span>Priority ${esc(score(data.effective_priority_score ?? data.priority_score))} · ${value(data.effective_priority_level ?? data.priority_level)}</span></section>
    <section class="admin-card" id="recommendations"><div class="section-title-row"><div><h2>Gemini recommendations</h2><p class="muted">Generated only when requested. Check all suggestions before acting.</p></div><button id="generate-button" class="admin-button" type="button">${recommendation ? 'Generate new advice' : 'Generate AI Recommendations'}</button></div>${recommendationView(recommendation, data)}</section>
    <section class="admin-card"><div class="section-title-row"><div><h2>Resource allocation</h2><p class="muted">Choose quantities from configured inventory. Unverified items cannot be reserved until verified.</p></div><a href="/portal/admin/resources">Manage resources →</a></div>${allocationRecorded ? '<p class="notice advisory">An allocation is already approved or awaiting verification for this advice. Verify inventory and budget to retry a pending decision, or review the saved allocation.</p>' : retryReady ? '<p class="notice advisory">The previously pending allocation can now be reviewed and submitted again using current verified inventory.</p>' : ''}
      <form id="allocation-form" class="stack-form" novalidate><div class="allocation-list">${allocationRows(resources, retryReady ? pendingAllocation : null)}</div><div class="allocation-totals"><strong>Calculated item cost</strong><span id="cost-estimate">Select resources to see an estimate.</span></div>
      <div class="budget-entry"><label>Budget for this allocation (INR)<input name="approved_budget" type="number" min="0" ${budgetMax == null ? '' : `max="${esc(budgetMax)}"`} step="0.01" value="${retryReady && pendingAllocation.budget != null ? esc(pendingAllocation.budget) : ''}" placeholder="Enter amount"></label>${budgetMax == null ? '' : `<label>Budget slider<span class="muted">Verified ceiling: ${esc(money(budgetMax))}</span><input id="budget-slider" type="range" min="0" max="${esc(budgetMax)}" step="0.01" value="${retryReady && pendingAllocation.budget != null ? esc(pendingAllocation.budget) : 0}"></label>`}</div><p class="muted">${policy.max_amount == null ? 'No budget ceiling configured.' : `${policy.verified ? 'Verified' : 'Unverified'} configured ceiling: ${esc(money(policy.max_amount))}. The server checks available budget when you submit.`}</p>
      <label>Remarks (optional)<textarea name="remarks" maxlength="1000" placeholder="Add context for this decision"></textarea></label><div class="button-row"><button class="admin-button" name="decision" value="approve" ${allocationRecorded || !resources.length ? 'disabled' : ''}>Approve allocation</button><button class="admin-button secondary" name="decision" value="reject" ${allocationRecorded || allocationRejected ? 'disabled' : ''}>Reject allocation</button></div></form>
      ${data.resource_allocations?.length ? `<details class="quiet-details"><summary>Saved allocations (${data.resource_allocations.length})</summary>${safeList(data.resource_allocations.map(item => `${label(item.decision)} · ${date(item.created_at)} · ${money(item.budget)}`))}</details>` : ''}</section>
    <section class="admin-card"><h2>Update workflow status</h2><p>Current: ${badge(data.status)}</p>${next ? `<form id="status-form" class="stack-form"><p>Next step: <strong>${esc(next)}</strong></p><label>Remarks (optional)<textarea name="remarks" maxlength="1000"></textarea></label><button class="admin-button" type="submit">Move to ${esc(next)}</button></form>` : '<p class="muted">This complaint is resolved.</p>'}</section>
    <details class="admin-card quiet-details"><summary>Administrative history</summary>${history(data, audit)}</details>`, page(data.id));
  const back = root.querySelector('.back-link');
  if (back) back.textContent = '← Back to Complaint Overview';
  bindAction(data, resources, recommendation, allocationRecorded);
}

export async function renderDetail(id, view = 'overview') {
  if (view === 'overview') { overview(await api(endpoint(id))); return; }
  const [data, resources, policy, audit] = await Promise.all([
    api(endpoint(id)), api('/admin/resources'), api('/admin/resources/budget/policy'), api(`${endpoint(id)}/audit`),
  ]);
  actionView(data, resources, policy, audit);
}

function bindAction(data, resources, recommendation, allocationRecorded) {
  const id = data.id;
  let submitting = false;
  async function act(button, work, success) {
    if (submitting) return;
    submitting = true; pending(button, true); message('');
    try { const result = await work(); await renderDetail(id, 'action'); message(typeof success === 'function' ? success(result) : success, 'success'); }
    catch (error) { message(error.message, 'error'); pending(button, false); }
    finally { submitting = false; }
  }
  root.querySelector('#generate-button').addEventListener('click', event => act(event.currentTarget, () => api(`${endpoint(id)}/recommendations`, { method: 'POST' }), result => result.result?.gemini_population_recommendation || result.result?.resource_recommendations ? 'AI advice saved for review.' : 'AI analysis was unavailable.'));
  const review = root.querySelector('#recommendation-review');
  review?.addEventListener('submit', event => { event.preventDefault(); const button = event.submitter; const reason = review.elements.reason.value.trim(); if (reason.length < 3) return; if (!confirmed('Save this recommendation review?')) return; act(button, () => api(`${endpoint(id)}/recommendations/${recommendation.id}/decision`, { method: 'POST', body: { decision: button.value, reason } }), 'Recommendation review saved.'); });

  const population = root.querySelector('#population-form');
  if (population) {
    let previewBody = null; let key = requestKey();
    const preview = root.querySelector('#population-preview');
    const manualLabel = root.querySelector('#manual-score-label');
    const body = () => ({ decision: population.elements.decision.value, recommendation_id: recommendation.id, manual_score: population.elements.decision.value === 'manual' ? Number(population.elements.manual_score.value) : null, reason: population.elements.reason.value.trim() });
    root.querySelectorAll('.population-choice').forEach(button => button.addEventListener('click', () => {
      population.hidden = false; population.elements.decision.value = button.dataset.choice;
      root.querySelector('#population-heading').textContent = { accept: 'Accept suggested adjustment', reject: 'Ignore suggested adjustment', manual: 'Modify population impact' }[button.dataset.choice];
      manualLabel.hidden = button.dataset.choice !== 'manual'; previewBody = null; preview.hidden = true; key = requestKey(); population.scrollIntoView({ block: 'nearest' });
    }));
    root.querySelector('#cancel-population').addEventListener('click', () => { population.hidden = true; previewBody = null; });
    population.addEventListener('input', () => { previewBody = null; preview.hidden = true; key = requestKey(); });
    root.querySelector('#preview-button').addEventListener('click', async event => {
      const payload = body();
      if (payload.reason.length < 3 || (payload.decision === 'manual' && (!/^\d+$/.test(population.elements.manual_score.value) || payload.manual_score > 100))) { message('Enter a reason and a whole population score from 0 to 100 when modifying.', 'error'); return; }
      const button = event.currentTarget; pending(button, true);
      try { const result = await api(`${endpoint(id)}/population/preview`, { method: 'POST', body: payload }); previewBody = JSON.stringify(payload); preview.hidden = false; preview.textContent = result.approved_evaluation ? `Official priority would change from ${score(result.original_priority)} to ${score(result.approved_evaluation.score)}. Confirm to save.` : 'Official priority will not change. Confirm to record this decision.'; message('Preview calculated by the server.', 'success'); }
      catch (error) { message(error.message, 'error'); } finally { pending(button, false); }
    });
    population.addEventListener('submit', event => { event.preventDefault(); const payload = body(); if (previewBody !== JSON.stringify(payload)) { message('Preview this decision before confirming.', 'error'); return; } if (!confirmed('Save this priority adjustment decision?')) return; act(event.submitter, () => api(`${endpoint(id)}/population/decisions`, { method: 'POST', body: payload, key }), 'Priority decision saved.'); });
  }

  const allocation = root.querySelector('#allocation-form'); let allocationKey = requestKey();
  const budget = allocation.elements.approved_budget; const budgetSlider = root.querySelector('#budget-slider');
  function updateCost() {
    const selected = [...root.querySelectorAll('.resource-picker')].map(row => ({ item: resources.find(item => item.id === row.dataset.resourceId), quantity: Number(row.querySelector('.quantity-number').value) })).filter(row => row.quantity > 0);
    const display = root.querySelector('#cost-estimate');
    if (!selected.length) { display.textContent = 'Select resources to see an estimate.'; return; }
    if (selected.some(row => row.item?.unit_cost == null)) { display.textContent = 'Cost unavailable because some unit costs are missing.'; return; }
    const total = selected.reduce((sum, row) => sum + row.item.unit_cost * row.quantity, 0);
    display.textContent = `${money(total)} · ${selected.every(row => row.item.verified) ? 'verified unit costs' : 'estimated from unverified costs'}`;
  }
  root.querySelectorAll('.resource-picker').forEach(row => {
    const slider = row.querySelector('.quantity-slider'), number = row.querySelector('.quantity-number');
    slider.addEventListener('input', () => { number.value = slider.value; allocationKey = requestKey(); updateCost(); });
    number.addEventListener('input', () => { if (number.validity.valid && /^\d+$/.test(number.value)) slider.value = number.value; allocationKey = requestKey(); updateCost(); });
  });
  updateCost();
  budgetSlider?.addEventListener('input', () => { budget.value = budgetSlider.value; allocationKey = requestKey(); });
  budget.addEventListener('input', () => { if (budgetSlider && budget.validity.valid && budget.value !== '') budgetSlider.value = budget.value; allocationKey = requestKey(); });
  allocation.elements.remarks.addEventListener('input', () => { allocationKey = requestKey(); });
  allocation.addEventListener('submit', event => {
    event.preventDefault(); if (allocationRecorded || submitting) return;
    const button = event.submitter, decision = button.value, items = [];
    for (const row of decision === 'approve' ? root.querySelectorAll('.resource-picker') : []) {
      const input = row.querySelector('.quantity-number'), raw = input.value.trim(), item = resources.find(resource => resource.id === row.dataset.resourceId);
      if (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw)) || !item || (item.available_quantity == null && Number(raw) > 0) || Number(raw) > item.available_quantity) { message(`Enter a valid whole quantity within available stock for ${item?.name || 'each resource'}.`, 'error'); input.focus(); return; }
      if (Number(raw) > 0) items.push({ resource_id: item.id, quantity: Number(raw) });
    }
    const amount = budget.value.trim(), approvedBudget = amount === '' ? null : Number(amount);
    if (decision === 'approve' && amount !== '' && (!budget.validity.valid || !Number.isFinite(approvedBudget) || approvedBudget < 0)) { message('Enter a valid budget within the configured ceiling.', 'error'); budget.focus(); return; }
    if (decision === 'approve' && !items.length) { message('Choose at least one resource before approving.', 'error'); return; }
    if (decision === 'approve' && approvedBudget == null) { message('Enter a budget for this allocation.', 'error'); budget.focus(); return; }
    if (decision === 'approve' && items.every(row => resources.find(item => item.id === row.resource_id)?.unit_cost != null)) {
      const cost = items.reduce((sum, row) => sum + resources.find(item => item.id === row.resource_id).unit_cost * row.quantity, 0);
      if (approvedBudget + 0.00001 < cost) { message(`Budget must cover the calculated item cost of ${money(cost)}.`, 'error'); budget.focus(); return; }
    }
    if (!confirmed(`${decision === 'approve' ? 'Approve' : 'Reject'} this allocation?`)) return;
    const payload = { decision, recommendation_id: recommendation?.id || null, items: decision === 'reject' ? [] : items, approved_budget: decision === 'reject' ? null : approvedBudget, remarks: allocation.elements.remarks.value.trim() || null };
    act(button, () => api(`${endpoint(id)}/allocations`, { method: 'POST', body: payload, key: allocationKey }), result => result.decision === 'pending_verification' ? 'Saved as pending verification. No stock was reserved.' : 'Allocation decision saved.');
  });
  const status = root.querySelector('#status-form');
  status?.addEventListener('submit', event => { event.preventDefault(); if (!confirmed(`Move complaint to ${nextStatus[data.status]}?`)) return; act(event.submitter, () => api(`${endpoint(id)}/status`, { method: 'POST', body: { status: nextStatus[data.status], remarks: status.elements.remarks.value.trim() || null } }), 'Complaint status updated.'); });
}
