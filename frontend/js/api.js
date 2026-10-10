/**
 * CivicResolve API Client
 * Clean boundary for interacting with the FastAPI complaint backend.
 */

const API_BASE = '/complaints';
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function fetchWithTimeout(url, options, timeoutMs, timeoutMessage) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (err) {
    if (err && err.name === 'AbortError') throw new Error(timeoutMessage);
    throw new Error('Network connection error. Please check your connection and try again.');
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Generate a standard UUID v4 string for the Idempotency-Key header.
 * Meets backend criteria: min_length=8, max_length=128, pattern=^[A-Za-z0-9._:-]+$
 */
export function generateIdempotencyKey() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    return 'idemp-' + Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  }
  throw new Error('Secure browser randomness is unavailable. Please use a modern browser to submit a complaint.');
}

/**
 * Submit a citizen complaint to POST /complaints
 * @param {Object} data - Complaint payload
 * @param {string} idempotencyKey - Unique key for safe retries
 * @returns {Promise<Object>} Created or retrieved complaint detail
 */
export async function submitComplaint(data, idempotencyKey) {
  const key = idempotencyKey || generateIdempotencyKey();

  const response = await fetchWithTimeout(API_BASE, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': key,
      },
      body: JSON.stringify(data),
    }, 30000, 'Submission timed out. Your complaint may have been saved. Please retry with the same details.');

  if (response.status === 201 || response.status === 200) {
    let result;
    try {
      result = await response.json();
    } catch (_) {
      throw new Error('The server returned an unreadable confirmation. Please retry with the same details.');
    }
    if (!result || !UUID_PATTERN.test(result.id || '') || !/^[a-f0-9]{64}$/.test(result.tracking_token || '')
      || !result.status || !result.processing_status
      || typeof result.location !== 'string' || typeof result.created_at !== 'string'
      || !(result.title === null || typeof result.title === 'string')) {
      throw new Error('The server returned an incomplete confirmation. Please retry with the same details.');
    }
    return result;
  }

  let errorData;
  try {
    errorData = await response.json();
  } catch (_) {
    errorData = null;
  }

  if (response.status === 409) {
    throw new Error(
      (errorData && errorData.detail) ||
      'This submission key was already used for a different complaint. Please try again with a new submission.'
    );
  }

  if (response.status === 422) {
    let msg = 'Validation error: ';
    if (errorData && Array.isArray(errorData.detail)) {
      msg += errorData.detail.map(d => `${d.loc ? d.loc.join('.') : 'field'}: ${d.msg}`).join(', ');
    } else if (errorData && typeof errorData.detail === 'string') {
      msg += errorData.detail;
    } else {
      msg += 'Please verify that all required fields are filled correctly.';
    }
    throw new Error(msg);
  }

  if (response.status === 503) {
    throw new Error('The complaint storage service is temporarily unavailable. Please try again in a few moments.');
  }

  throw new Error('The complaint service could not confirm your submission. Please retry with the same details.');
}

/**
 * Retrieve a restricted tracking status with a copied ID:token capability.
 * @param {string} reference - UUID:token string
 * @returns {Promise<Object>} Complaint detail
 */
export async function getComplaint(reference) {
  const trimmed = (reference || '').trim();
  if (!trimmed) {
    throw new Error('Please enter your copied tracking reference.');
  }
  const [trimmedId, token, extra] = trimmed.split(':');
  if (!UUID_PATTERN.test(trimmedId || '') || !/^[a-f0-9]{64}$/.test(token || '') || extra !== undefined) {
    throw new Error('Invalid tracking reference. Copy the full reference from your submission confirmation.');
  }

  const response = await fetchWithTimeout(`${API_BASE}/${encodeURIComponent(trimmedId)}/track`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ token }),
    }, 15000, 'Lookup timed out. Please try again.');

  if (response.status === 200) {
    try {
      const result = await response.json();
      if (result && typeof result.id === 'string' && result.id.toLowerCase() === trimmedId.toLowerCase()
        && typeof result.status === 'string' && typeof result.processing_status === 'string'
        && typeof result.created_at === 'string') return result;
    } catch (_) {
      // Malformed 200 responses must not render invented details.
    }
    throw new Error('The server returned an incomplete complaint record. Please try again.');
  }

  if (response.status === 404) {
    throw new Error('Tracking reference not found or credential invalid.');
  }

  if (response.status === 429) throw new Error('Too many tracking attempts. Please wait and try again.');

  if (response.status === 422) {
    throw new Error('Invalid tracking reference. Please check the copied value.');
  }

  if (response.status === 503) {
    throw new Error('Complaint lookup service is temporarily unavailable. Please try again shortly.');
  }

  throw new Error('Complaint lookup is temporarily unavailable. Please try again.');
}
