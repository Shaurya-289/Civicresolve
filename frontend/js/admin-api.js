// Bearer credentials exist only in this module's memory and vanish on refresh.
let token = null;
let expiresAt = null;
let onSessionExpired = () => {};

export function setSessionExpiredHandler(handler) { onSessionExpired = handler; }
export function hasSession() { return !!token && (!expiresAt || Date.now() < Date.parse(expiresAt)); }
export function clearSession() { token = null; expiresAt = null; }
export function setSession(data) { token = data.access_token; expiresAt = data.expires_at; }
export function requestKey() { return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`; }

function errorMessage(detail, status) {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail.message === 'string') return detail.message;
  if (detail && typeof detail.code === 'string') {
    const messages = { missing_api_key: 'AI recommendations are unavailable until the server is configured.', quota_exhausted: 'The AI service has reached its quota. Try again later.', provider_timeout: 'The AI service timed out. Try again later.' };
    return messages[detail.code] || 'The AI service could not complete this request.';
  }
  if (status === 401) return 'Your administrator session has ended. Please sign in again.';
  if (status === 403) return 'You do not have permission for this action.';
  if (status === 404) return 'The requested record was not found.';
  if (status >= 500) return 'The server could not complete this request. Please try again.';
  return 'The request could not be completed. Check the details and try again.';
}

export async function api(path, { method = 'GET', body, key, publicRequest = false } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (key) headers['Idempotency-Key'] = key;
  if (!publicRequest) {
    if (!hasSession()) { clearSession(); onSessionExpired(); throw new Error('Please sign in to continue.'); }
    headers.Authorization = `Bearer ${token}`;
  }
  let response;
  try { response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store' }); }
  catch { throw new Error('Could not connect to the server. Check your connection and try again.'); }
  if (response.status === 204) return null;
  let data = null;
  try { data = await response.json(); } catch { /* A non-JSON failure still gets a useful message. */ }
  if (!response.ok) {
    if (response.status === 401 && !publicRequest) { clearSession(); onSessionExpired(); }
    const detail = data?.detail;
    if (Array.isArray(detail)) throw new Error(detail.map(item => item.msg || 'Invalid input').join('; '));
    throw new Error(errorMessage(detail, response.status));
  }
  return data;
}

export async function login(username, password) {
  const credentials = await api('/admin/auth/login', { method: 'POST', body: { username, password }, publicRequest: true });
  setSession(credentials);
  try { return await api('/admin/auth/me'); }
  catch (error) { clearSession(); throw error; }
}

export async function logout() {
  try { if (hasSession()) await api('/admin/auth/logout', { method: 'POST' }); }
  finally { clearSession(); }
}
