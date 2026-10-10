/** Keep a safe retry key across a tab refresh without storing complaint text. */
const STORAGE_KEY = 'civic_pending_submission_v1';
let pending = null;

function browserStorage() {
  try { return globalThis.sessionStorage; } catch (_) { return null; }
}

async function fingerprint(signature) {
  try {
    if (!globalThis.crypto?.subtle) return null;
    const bytes = new TextEncoder().encode(signature);
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  } catch (_) {
    return null;
  }
}

export async function retryKeyFor(payload, generateKey, storage = browserStorage()) {
  const signature = JSON.stringify(payload);
  if (pending?.signature === signature) return pending.key;

  const hash = await fingerprint(signature);
  let key = null;
  if (hash) {
    try {
      const saved = JSON.parse(storage.getItem(STORAGE_KEY) || 'null');
      if (saved?.hash === hash && /^[A-Za-z0-9._:-]{8,128}$/.test(saved.key)) key = saved.key;
    } catch (_) {
      // Session storage can be disabled; in-memory retry still works.
    }
  }
  key ||= generateKey();
  pending = { signature, key };
  if (hash) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify({ hash, key }));
    } catch (_) {
      // Keep the in-memory key when storage is unavailable.
    }
  }
  return key;
}

export function clearRetryKey(storage = browserStorage()) {
  pending = null;
  try {
    storage.removeItem(STORAGE_KEY);
  } catch (_) {
    // No action required if storage is unavailable.
  }
}
