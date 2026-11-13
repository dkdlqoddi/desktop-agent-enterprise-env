const { test } = require('node:test');
const assert = require('node:assert/strict');
const { trashValues } = require('../electron/trash.cjs');

// What the backend gets for a request of the trash (electron/trash.cjs): the main process makes it from the request's
// own values it takes, and adds what decides which entries the caller meets. tests/api.spec.ts checks the two together
// in the app.

test('a page of the trash, of one kind or all, goes on as asked and nothing else of the request does', () => {
  const asked = { kind: 'run', limit: 100, cursor: 'c1', without_recipes: [], allow_files: true };
  assert.deepEqual(trashValues('trash.list', asked), { kind: 'run', limit: 100, cursor: 'c1' });
  assert.deepEqual(trashValues('trash.list', {}), {});
  // The screens restore all of it, and a routine that reads this PC's files with it.
  assert.deepEqual(trashValues('trash.restore', { ids: ['e1'], allow_files: false }), {
    ids: ['e1'],
    allow_files: true,
  });
  assert.deepEqual(trashValues('trash.purge', { all: true, without_recipes: ['x'] }), {
    ids: undefined,
    all: true,
  });
});

test("an AI tool's request never meets the web records' routines, whatever it asks", () => {
  for (const access of ['read', 'write', 'full']) {
    const listed = trashValues('trash.list', { limit: 10, without_recipes: [] }, access);
    assert.deepEqual(listed, { limit: 10, without_recipes: ['web_capture'] });
    const purged = trashValues('trash.purge', { all: true, without_recipes: [] }, access);
    assert.deepEqual(purged.without_recipes, ['web_capture']);
    const restored = trashValues('trash.restore', { ids: ['e1'], allow_files: true }, access);
    assert.deepEqual(restored.without_recipes, ['web_capture']);
    // A routine that reads this PC's files comes back only at the level that may have them read.
    assert.equal(restored.allow_files, access === 'full');
  }
});
