/**
 * CivicResolve Application Controller
 * Handles routing, form validation, complaint submission, tracking UI, and toast feedback.
 */

import { submitComplaint, getComplaint, generateIdempotencyKey } from './api.js';
import { retryKeyFor, clearRetryKey } from './retry.js';

// --- Category & Enum Formatting Dictionaries ---
const CATEGORY_NAMES = {
  road_damage: 'Road Damage & Potholes',
  streetlight: 'Streetlight & Electrical',
  drainage: 'Drainage & Sewerage',
  water_leak: 'Water Supply & Leakage',
  waste: 'Garbage & Sanitation',
};

const AREA_LABELS = {
  single_household_building: 'Single household / building',
  street_lane: 'Street / lane',
  neighborhood: 'Neighborhood',
  multiple_neighborhoods_major_area: 'Multiple neighborhoods / major area',
};

const TAG_LABELS = {
  residential_area: 'Residential Area',
  school: 'School / College',
  hospital: 'Hospital',
  main_road: 'Main Road',
  transport_hub: 'Transportation Hub',
};

// --- DOM Element References ---
const views = {
  home: document.getElementById('view-home'),
  report: document.getElementById('view-report'),
  result: document.getElementById('view-result'),
  track: document.getElementById('view-track'),
};

const navLinks = {
  home: document.getElementById('nav-home-link'),
  report: document.getElementById('nav-report-link'),
  track: document.getElementById('nav-track-link'),
};

const mobileMenuBtn = document.getElementById('nav-mobile-btn');
const mobileMenu = document.getElementById('nav-links-menu');

// Form Elements
const complaintForm = document.getElementById('complaint-form');
const inputTitle = document.getElementById('input-title');
const inputDescription = document.getElementById('input-description');
const inputLocation = document.getElementById('input-location');
const selectArea = document.getElementById('select-area');
const inputDays = document.getElementById('input-days');
const btnSubmitComplaint = document.getElementById('btn-submit-complaint');
const formAlert = document.getElementById('form-alert');
const formAlertText = document.getElementById('form-alert-text');
const titleCounter = document.getElementById('title-counter');
const descCounter = document.getElementById('desc-counter');

// Result Elements
const resultTrackingId = document.getElementById('result-tracking-id');
const resultTitleVal = document.getElementById('result-title-val');
const resultDateVal = document.getElementById('result-date-val');
const resultWorkflowStatus = document.getElementById('result-workflow-status');
const resultProcessingStatus = document.getElementById('result-processing-status');
const resultLocationVal = document.getElementById('result-location-val');
const btnCopyId = document.getElementById('btn-copy-id');
const copyBtnText = document.getElementById('copy-btn-text');
const btnResultTrack = document.getElementById('btn-result-track');
const btnResultNew = document.getElementById('btn-result-new');

// Track Elements
const trackForm = document.getElementById('track-form');
const inputTrackId = document.getElementById('input-track-id');
const btnTrackSubmit = document.getElementById('btn-track-submit');
const trackAlert = document.getElementById('track-alert');
const trackAlertText = document.getElementById('track-alert-text');
const trackResultContainer = document.getElementById('track-result-container');
const timelineProgressBar = document.getElementById('timeline-progress-bar');
const stepNodes = {
  Submitted: document.getElementById('step-submitted'),
  'Under Review': document.getElementById('step-review'),
  Assigned: document.getElementById('step-assigned'),
  Resolved: document.getElementById('step-resolved'),
};

// Track Details
const trackDetailTitle = document.getElementById('track-detail-title');
const trackDetailId = document.getElementById('track-detail-id');
const trackDetailStatusBadge = document.getElementById('track-detail-status-badge');
const trackDetailProcBadge = document.getElementById('track-detail-proc-badge');
const trackDetailDescription = document.getElementById('track-detail-description');
const trackDetailLocation = document.getElementById('track-detail-location');
const trackDetailCategory = document.getElementById('track-detail-category');
const trackDetailDate = document.getElementById('track-detail-date');
const trackDetailArea = document.getElementById('track-detail-area');
const trackDetailDays = document.getElementById('track-detail-days');
const trackDetailTags = document.getElementById('track-detail-tags');

// Toast
const toastContainer = document.getElementById('toast');
const toastMessage = document.getElementById('toast-message');

// State
let lastSubmittedId = null;
let lastTrackingReference = null;
let toastTimeout = null;
let submissionInFlight = false;
let trackRequestSequence = 0;

// --- Helper Functions ---

function showToast(message) {
  if (toastTimeout) {
    clearTimeout(toastTimeout);
  }
  toastMessage.textContent = message;
  toastContainer.classList.add('show');
  toastTimeout = setTimeout(() => {
    toastContainer.classList.remove('show');
  }, 2600);
}

function formatDate(isoString) {
  if (!isoString) return '—';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return '—';
    return new Intl.DateTimeFormat('en-US', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(d);
  } catch (_) {
    return '—';
  }
}

function setBadgeStatus(element, status) {
  element.className = 'badge';
  switch (status) {
    case 'Submitted':
      element.classList.add('badge-submitted');
      element.textContent = 'Submitted';
      break;
    case 'Under Review':
      element.classList.add('badge-review');
      element.textContent = 'Under Review';
      break;
    case 'Assigned':
      element.classList.add('badge-assigned');
      element.textContent = 'Assigned';
      break;
    case 'Resolved':
      element.classList.add('badge-resolved');
      element.textContent = 'Resolved';
      break;
    default:
      element.classList.add('badge-pending');
      element.textContent = status || 'Pending';
  }
}

function setBadgeProcessing(element, status, reviewFlagged = false) {
  element.className = 'badge';
  switch (status) {
    case 'completed':
      element.classList.add(reviewFlagged ? 'badge-review' : 'badge-completed');
      element.textContent = reviewFlagged ? 'Triage Complete — Review Flagged' : 'Triage Completed';
      break;
    case 'failed':
      element.classList.add('badge-review');
      element.textContent = 'Processing Failed — Review Needed';
      break;
    case 'incomplete':
      element.classList.add('badge-review');
      element.textContent = 'Pending Evaluation — Manual Review';
      break;
    case 'pending':
    default:
      element.classList.add('badge-pending');
      element.textContent = 'Pending Evaluation';
      break;
  }
}

// --- Routing & View Management ---

export function navigateTo(viewName, params = {}) {
  if (viewName === 'result' && !lastSubmittedId) viewName = 'home';
  // Update view visibility
  Object.keys(views).forEach((key) => {
    if (views[key]) {
      if (key === viewName) {
        views[key].classList.add('active');
      } else {
        views[key].classList.remove('active');
      }
    }
  });

  // Update navigation link active classes
  Object.keys(navLinks).forEach((key) => {
    if (navLinks[key]) {
      if (key === viewName || (viewName === 'result' && key === 'report')) {
        navLinks[key].classList.add('active');
        navLinks[key].setAttribute('aria-current', 'page');
      } else {
        navLinks[key].classList.remove('active');
        navLinks[key].removeAttribute('aria-current');
      }
    }
  });

  // Close mobile navigation menu if open
  if (mobileMenu && mobileMenu.classList.contains('mobile-open')) {
    mobileMenu.classList.remove('mobile-open');
    if (mobileMenuBtn) mobileMenuBtn.setAttribute('aria-expanded', 'false');
  }

  // Scroll to top
  window.scrollTo({ top: 0, behavior: 'smooth' });

  // Update URL hash without re-triggering hashchange unnecessarily
  let newHash = `#${viewName}`;
  if (viewName === 'track' && params.id) {
    newHash = `#track?id=${encodeURIComponent(params.id)}`;
  }
  if (window.location.hash !== newHash) {
    history.pushState(null, '', newHash);
  }

  // Action on entering views
  if (viewName === 'track' && params.id) {
    inputTrackId.value = params.id;
    performTrackLookup(params.id);
  }
}

function handleHashChange() {
  const pathView = window.location.pathname === '/report' ? 'report'
    : window.location.pathname === '/track' ? 'track' : 'home';
  const hash = window.location.hash.slice(1) || pathView;
  const [route, queryString] = hash.split('?');
  const params = {};
  if (queryString) {
    const urlParams = new URLSearchParams(queryString);
    for (const [k, v] of urlParams.entries()) {
      params[k] = v;
    }
  }

  if (views[route]) {
    navigateTo(route, params);
  } else {
    navigateTo('home');
  }
}

// --- Form Validation & Submission ---

function resetFormAlert() {
  formAlert.style.display = 'none';
  formAlertText.textContent = '';
  document.querySelectorAll('.form-control, .tag-checkbox.is-invalid').forEach(el => {
    el.classList.remove('is-invalid');
    el.removeAttribute('aria-invalid');
  });
}

function showFormError(message, invalidInput = null) {
  formAlertText.textContent = message;
  formAlert.style.display = 'flex';
  if (invalidInput) {
    invalidInput.classList.add('is-invalid');
    invalidInput.setAttribute('aria-invalid', 'true');
    invalidInput.focus();
  }
}

async function handleComplaintSubmit(e) {
  e.preventDefault();
  if (submissionInFlight) return;
  resetFormAlert();

  const titleVal = inputTitle.value.trim();
  const descVal = inputDescription.value.trim();
  const locVal = inputLocation.value.trim();
  const areaVal = selectArea.value;
  const daysText = inputDays.value.trim();
  const daysVal = Number(daysText);

  // Collect checked location tags
  const checkedTags = Array.from(
    document.querySelectorAll('input[name="location_tags"]:checked')
  ).map(cb => cb.value);

  // Client Validation
  if (titleVal.length > 200) {
    showFormError('Title exceeds the maximum limit of 200 characters.', inputTitle);
    return;
  }
  if (!descVal) {
    showFormError('Please enter a description of the infrastructure problem.', inputDescription);
    return;
  }
  if (descVal.length > 2000) {
    showFormError('Description exceeds the maximum limit of 2,000 characters.', inputDescription);
    return;
  }
  if (!locVal) {
    showFormError('Please provide the location or nearby landmark.', inputLocation);
    return;
  }
  if (locVal.length > 200) {
    showFormError('Location exceeds 200 characters. Please provide a concise address or landmark.', inputLocation);
    return;
  }
  if (!Object.hasOwn(AREA_LABELS, areaVal)) {
    showFormError('Please select a valid affected area.', selectArea);
    return;
  }
  if (!/^(0|[1-9]\d*)$/.test(daysText) || !Number.isSafeInteger(daysVal) || daysVal > 3650) {
    showFormError('Please enter whole days from 0 to 3650.', inputDays);
    return;
  }
  if (checkedTags.length === 0 || checkedTags.some(tag => !Object.hasOwn(TAG_LABELS, tag))) {
    showFormError('Please select at least one location context tag.', document.getElementById('tag-residential'));
    return;
  }

  // Construct request payload matching backend schema strictly
  const payload = {
    title: titleVal.length > 0 ? titleVal : null,
    description: descVal,
    location: locVal,
    affected_area: areaVal,
    days_existing: daysVal,
    location_tags: checkedTags,
  };

  // Set loading state
  submissionInFlight = true;
  btnSubmitComplaint.disabled = true;
  btnSubmitComplaint.classList.add('btn-loading');

  try {
    const idempotencyKey = await retryKeyFor(payload, generateIdempotencyKey);
    const result = await submitComplaint(payload, idempotencyKey);
    clearRetryKey();
    lastSubmittedId = result.id;
    lastTrackingReference = `${result.id}:${result.tracking_token}`;

    // Populate Confirmation Result View
    resultTrackingId.textContent = result.id;
    resultTitleVal.textContent = result.title || 'Untitled Complaint';
    resultDateVal.textContent = formatDate(result.created_at);
    setBadgeStatus(resultWorkflowStatus, result.status);
    setBadgeProcessing(resultProcessingStatus, result.processing_status,
      result.classification_review?.needs_review === true || result.emergency_review_required === true);
    resultLocationVal.textContent = result.location;

    // Reset Form Fields
    complaintForm.reset();
    titleCounter.textContent = '0/200';
    descCounter.textContent = '0/2000';
    // Re-check default tag
    const defaultTag = document.getElementById('tag-residential');
    if (defaultTag) defaultTag.checked = true;

    // Navigate to Result View
    navigateTo('result');
  } catch (err) {
    showFormError(err.message || 'Submission failed. Please try again.');
  } finally {
    submissionInFlight = false;
    btnSubmitComplaint.disabled = false;
    btnSubmitComplaint.classList.remove('btn-loading');
  }
}

// --- Tracking Lookup Logic ---

async function performTrackLookup(complaintId) {
  const requestSequence = ++trackRequestSequence;
  const id = (complaintId || inputTrackId.value || '').trim();

  // Clear previous alerts and results
  trackAlert.style.display = 'none';
  trackResultContainer.classList.remove('active');

  if (!id) {
    trackAlertText.textContent = 'Please enter a valid Complaint ID.';
    trackAlert.style.display = 'flex';
    inputTrackId.focus();
    return;
  }

  // Set loading state
  btnTrackSubmit.disabled = true;
  btnTrackSubmit.classList.add('btn-loading');

  try {
    const data = await getComplaint(id);
    if (requestSequence !== trackRequestSequence) return;

    // Populate Details
    trackDetailTitle.textContent = data.title || 'Untitled Complaint';
    trackDetailId.textContent = data.id;
    trackDetailDescription.textContent = 'Complaint details are private. This page shows official tracking status only.';
    trackDetailLocation.textContent = 'Private';
    trackDetailDate.textContent = formatDate(data.created_at);
    trackDetailArea.textContent = 'Private';
    trackDetailDays.textContent = 'Private';

    // Category
    if (data.category && CATEGORY_NAMES[data.category]) {
      trackDetailCategory.textContent = CATEGORY_NAMES[data.category];
    } else if (data.category) {
      trackDetailCategory.textContent = data.category.replace(/_/g, ' ');
    } else {
      trackDetailCategory.textContent = 'Pending Evaluation';
    }
    if (data.category) {
      trackDetailCategory.textContent += data.classification_review?.needs_review === true
        ? ' — review flagged' : ' — provisional';
    }

    // Context Tags
    trackDetailTags.textContent = 'Private';

    // Status Badges
    setBadgeStatus(trackDetailStatusBadge, data.status);
    setBadgeProcessing(trackDetailProcBadge, data.processing_status,
      data.classification_review?.needs_review === true || data.emergency_review_required === true);

    // Update 4-Stage Progress Timeline
    updateTimeline(data.status);

    // Show result container
    trackResultContainer.classList.add('active');
  } catch (err) {
    if (requestSequence !== trackRequestSequence) return;
    trackAlertText.textContent = err.message || 'Unable to find complaint.';
    trackAlert.style.display = 'flex';
  } finally {
    if (requestSequence === trackRequestSequence) {
      btnTrackSubmit.disabled = false;
      btnTrackSubmit.classList.remove('btn-loading');
    }
  }
}

function updateTimeline(status) {
  const stages = ['Submitted', 'Under Review', 'Assigned', 'Resolved'];
  const currentIndex = stages.indexOf(status);

  // Calculate percentage width for connecting bar
  // 0% at Submitted (0/3), 33.3% at Under Review (1/3), 66.6% at Assigned (2/3), 100% at Resolved (3/3)
  const percent = currentIndex >= 0 ? (currentIndex / (stages.length - 1)) * 100 : 0;
  timelineProgressBar.style.width = `${percent}%`;

  stages.forEach((stage, idx) => {
    const node = stepNodes[stage];
    if (!node) return;

    node.classList.remove('completed', 'current');
    if (idx < currentIndex) {
      node.classList.add('completed');
    } else if (idx === currentIndex) {
      node.classList.add('current');
    }
  });
}

// --- Copy ID Handler ---

async function copyTrackingId() {
  const text = lastTrackingReference;
  if (!text) return;

  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      // Fallback
      const textArea = document.createElement('textarea');
      textArea.value = text;
      textArea.style.position = 'fixed';
      textArea.style.left = '-9999px';
      document.body.appendChild(textArea);
      textArea.select();
      if (!document.execCommand('copy')) throw new Error('Copy command was not accepted');
      document.body.removeChild(textArea);
    }

    // Visual Feedback
    btnCopyId.classList.add('copied');
    copyBtnText.textContent = 'Copied!';
    showToast('Private tracking reference copied. Keep it safe.');

    setTimeout(() => {
      btnCopyId.classList.remove('copied');
      copyBtnText.textContent = 'Copy Reference';
    }, 2200);
  } catch (err) {
    showToast('Failed to copy ID to clipboard.');
  }
}

// --- Event Listeners Initialization ---

document.addEventListener('DOMContentLoaded', () => {
  // Navigation events
  window.addEventListener('hashchange', handleHashChange);

  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener('click', (e) => {
      const target = link.getAttribute('href').slice(1);
      if (views[target]) {
        e.preventDefault();
        navigateTo(target);
      }
    });
  });

  // Mobile menu toggle
  if (mobileMenuBtn && mobileMenu) {
    mobileMenuBtn.addEventListener('click', () => {
      const isOpen = mobileMenu.classList.toggle('mobile-open');
      mobileMenuBtn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });
  }

  // Hero action buttons
  document.getElementById('hero-report-btn')?.addEventListener('click', () => navigateTo('report'));
  document.getElementById('hero-track-btn')?.addEventListener('click', () => navigateTo('track'));

  // Character counters
  inputTitle?.addEventListener('input', () => {
    titleCounter.textContent = `${inputTitle.value.length}/200`;
  });
  inputDescription?.addEventListener('input', () => {
    descCounter.textContent = `${inputDescription.value.length}/2000`;
  });

  // Form submission
  complaintForm?.addEventListener('submit', handleComplaintSubmit);

  // Copy ID button
  btnCopyId?.addEventListener('click', copyTrackingId);

  // Result actions
  btnResultTrack?.addEventListener('click', () => {
    if (lastTrackingReference) {
      navigateTo('track');
      inputTrackId.value = lastTrackingReference;
      performTrackLookup(lastTrackingReference);
    } else {
      navigateTo('track');
    }
  });

  btnResultNew?.addEventListener('click', () => {
    navigateTo('report');
  });

  // Track search submission
  trackForm?.addEventListener('submit', (e) => {
    e.preventDefault();
    performTrackLookup();
  });

  // Initial Route Resolution
  handleHashChange();
});
