import test from 'node:test';
import assert from 'node:assert/strict';

import { submitComplaint, getComplaint } from '../frontend/js/api.js';

const ID = '3fa85f64-5717-4562-b3fc-2c963f66afa6';
const TOKEN = 'a'.repeat(64);
const REFERENCE = `${ID}:${TOKEN}`;
const payload = {
  title: null, description: 'Streetlight is out.', location: 'Mansarovar Jaipur',
  affected_area: 'street_lane', days_existing: 2, location_tags: ['residential_area'],
};
const detail = {
  id: ID, tracking_token: TOKEN, title: null, description: payload.description, location: payload.location,
  affected_area: payload.affected_area, days_existing: payload.days_existing,
  location_tags: payload.location_tags, created_at: '2026-10-10T00:00:00+00:00',
  status: 'Submitted', processing_status: 'completed',
};
const originalFetch = globalThis.fetch;

test.afterEach(() => { globalThis.fetch = originalFetch; });

test('submission sends the exact payload and stable caller-provided retry key', async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return new Response(JSON.stringify(detail), { status: 201 });
  };
  const result = await submitComplaint(payload, 'retry-key-123');
  assert.equal(result.id, ID);
  assert.equal(requests[0].url, '/complaints');
  assert.equal(requests[0].options.headers['Idempotency-Key'], 'retry-key-123');
  assert.deepEqual(JSON.parse(requests[0].options.body), payload);
  assert.ok(requests[0].options.signal instanceof AbortSignal);
});

test('an ambiguous timeout explains safe retry and does not leak internals', async () => {
  globalThis.fetch = async () => { throw Object.assign(new Error('private details'), { name: 'AbortError' }); };
  await assert.rejects(submitComplaint(payload, 'retry-key-123'), /may have been saved.*same details/i);
});

test('network failure and server error do not expose internal response text', async () => {
  globalThis.fetch = async () => { throw new Error('internal socket path'); };
  await assert.rejects(submitComplaint(payload, 'retry-key-123'), /Network connection error/);
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'private stack trace' }), { status: 500 });
  await assert.rejects(submitComplaint(payload, 'retry-key-123'), error => !error.message.includes('private stack trace'));
});

test('malformed success cannot create a fake confirmation', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ status: 'Submitted' }), { status: 201 });
  await assert.rejects(submitComplaint(payload, 'retry-key-123'), /incomplete confirmation/);
});

test('lookup validates IDs and handles missing records', async () => {
  await assert.rejects(getComplaint('bad-id'), /Invalid tracking reference/);
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'not found' }), { status: 404 });
  await assert.rejects(getComplaint(REFERENCE), /credential invalid/);
});

test('lookup accepts canonical backend UUID for uppercase input', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ ...detail, status: 'Assigned' }), { status: 200 });
  const result = await getComplaint(`${ID.toUpperCase()}:${TOKEN}`);
  assert.equal(result.status, 'Assigned');
});

test('lookup timeout and malformed response are reported', async () => {
  globalThis.fetch = async () => { throw Object.assign(new Error('slow'), { name: 'AbortError' }); };
  await assert.rejects(getComplaint(REFERENCE), /Lookup timed out/);
  globalThis.fetch = async () => new Response('{}', { status: 200 });
  await assert.rejects(getComplaint(REFERENCE), /incomplete complaint record/);
});
