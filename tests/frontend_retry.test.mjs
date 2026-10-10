import test from 'node:test';
import assert from 'node:assert/strict';

import { retryKeyFor, clearRetryKey } from '../frontend/js/retry.js';

function memoryStorage() {
  const values = new Map();
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
    values,
  };
}

test('exact retry reuses the key, including after module reload, without storing complaint text', async () => {
  const storage = memoryStorage();
  const payload = { description: 'Private complaint wording', location: 'Test Lane 4' };
  let generated = 0;
  const generate = () => `test-key-${++generated}`;
  const first = await retryKeyFor(payload, generate, storage);
  assert.equal(await retryKeyFor(payload, generate, storage), first);

  const saved = [...storage.values.values()].join('');
  assert.ok(saved.includes('test-key-1'));
  assert.ok(!saved.includes('Private complaint wording'));
  assert.ok(!saved.includes('Test Lane 4'));

  const reloaded = await import('../frontend/js/retry.js?test-reload=1');
  assert.equal(await reloaded.retryKeyFor(payload, generate, storage), first);
  assert.equal(generated, 1);
  reloaded.clearRetryKey(storage);
});

test('a changed payload receives a new key, and success clears the stored key', async () => {
  const storage = memoryStorage();
  clearRetryKey(storage);
  let generated = 0;
  const generate = () => `test-key-${++generated}`;
  const first = await retryKeyFor({ description: 'A' }, generate, storage);
  const second = await retryKeyFor({ description: 'B' }, generate, storage);
  assert.notEqual(first, second);
  clearRetryKey(storage);
  assert.equal(storage.values.size, 0);
  assert.notEqual(await retryKeyFor({ description: 'B' }, generate, storage), second);
  clearRetryKey(storage);
});

test('storage failure still allows in-memory retry', async () => {
  const broken = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() {} };
  clearRetryKey(broken);
  let generated = 0;
  const generate = () => `test-key-${++generated}`;
  assert.equal(await retryKeyFor({ description: 'C' }, generate, broken), 'test-key-1');
  assert.equal(await retryKeyFor({ description: 'C' }, generate, broken), 'test-key-1');
  clearRetryKey(broken);
});
