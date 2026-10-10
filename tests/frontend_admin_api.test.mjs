import test from 'node:test';
import assert from 'node:assert/strict';
import { api, login, logout, hasSession, clearSession, setSessionExpiredHandler } from '../frontend/js/admin-api.js';

test('login validates with me and sends bearer only in Authorization header', async () => {
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith('/login')) return { ok: true, status: 200, json: async () => ({ access_token: 'private-token', expires_at: new Date(Date.now() + 60_000).toISOString() }) };
    if (path.endsWith('/me')) return { ok: true, status: 200, json: async () => ({ username: 'reviewer' }) };
    return { ok: true, status: 204 };
  };
  assert.equal((await login('reviewer', 'test-password')).username, 'reviewer');
  assert.equal(hasSession(), true);
  assert.equal(calls[0].options.headers.Authorization, undefined);
  assert.equal(calls[1].options.headers.Authorization, 'Bearer private-token');
  assert.equal(calls[1].path.includes('private-token'), false);
  await logout();
  assert.equal(hasSession(), false);
  assert.equal(calls[2].options.headers.Authorization, 'Bearer private-token');
});

test('401 clears session and triggers the route guard', async () => {
  let expired = 0;
  setSessionExpiredHandler(() => expired++);
  globalThis.fetch = async path => path.endsWith('/login')
    ? { ok: true, status: 200, json: async () => ({ access_token: 'temporary', expires_at: new Date(Date.now() + 60_000).toISOString() }) }
    : path.endsWith('/me')
      ? { ok: true, status: 200, json: async () => ({ username: 'reviewer' }) }
      : { ok: false, status: 401, json: async () => ({ detail: 'Session expired' }) };
  await login('reviewer', 'test-password');
  await assert.rejects(api('/admin/complaints'), /Session expired/);
  assert.equal(hasSession(), false);
  assert.equal(expired, 1);
  clearSession();
});
