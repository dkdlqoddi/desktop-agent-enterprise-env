const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { TRASH_KINDS } = require('../electron/api-catalog.cjs');
const { trashValues } = require('../electron/trash.cjs');

// What the backend gets for a request of the trash (electron/trash.cjs): the main process makes it from the request's
// own values it takes, and adds what decides which entries the caller meets. backend/tests/test_trash.py checks how the
// backend keeps to it, and tests/api.spec.ts the two together in the app.

test('a page of the trash, of one kind or all, goes on as asked and nothing else of the request does', () => {
  const asked = {
    kind: 'run',
    limit: 100,
    cursor: 'c1',
    only_kinds: ['chat'],
    without_recipes: [],
    allow_files: true,
  };
  assert.deepEqual(trashValues('trash.list', asked), { kind: 'run', limit: 100, cursor: 'c1' });
  assert.deepEqual(trashValues('trash.list', {}), {});
  // The screens meet all of it, and restore a routine that reads this PC's files with it.
  assert.deepEqual(trashValues('trash.restore', { ids: ['e1'], allow_files: false }), {
    ids: ['e1'],
    allow_files: true,
  });
  assert.deepEqual(trashValues('trash.purge', { all: true, only_kinds: ['task'] }), {
    ids: undefined,
    all: true,
  });
});

test("an AI tool's request meets neither what the chat and the web records put in the trash nor their routines", () => {
  for (const access of ['read', 'write', 'full'])
    for (const method of ['trash.list', 'trash.restore', 'trash.purge']) {
      // Whatever the tool asks for: another kind, its own restrictions, or none.
      for (const params of [
        { ids: ['e1'], kind: 'chat', limit: 10 },
        { ids: ['e1'], all: true, only_kinds: ['chat', 'web_capture'], without_recipes: [] },
      ]) {
        const values = trashValues(method, params, access);
        assert.deepEqual(values.only_kinds, TRASH_KINDS, `${access} ${method}`);
        assert.deepEqual(values.without_recipes, ['web_capture'], `${access} ${method}`);
        // A routine that reads this PC's files comes back only at the level that may have them read.
        if (method === 'trash.restore') assert.equal(values.allow_files, access === 'full');
      }
    }
  // The kinds a tool meets are those the API deletes into the trash, never the user's own.
  assert.deepEqual(TRASH_KINDS, ['conversation', 'run', 'task', 'prompt', 'schedule', 'routine']);
  for (const kind of ['chat', 'web_capture']) assert.ok(!TRASH_KINDS.includes(kind));
});

test("the backend's kinds of entry are those the tools know, and the user's own", () => {
  // A kind the trash takes later is a tool's only once it is named in TRASH_KINDS; this keeps the list in step with
  // backend/agent/trash.py, so a new kind is a decision rather than an oversight either way.
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'backend', 'agent', 'trash.py'),
    'utf8',
  );
  const table = source.slice(
    source.indexOf('KINDS = {'),
    source.indexOf('\n}\n', source.indexOf('KINDS = {')),
  );
  const kinds = [...table.matchAll(/^ {4}"([a-z_]+)": Kind\(/gm)].map((match) => match[1]);
  assert.deepEqual(kinds, [...TRASH_KINDS, 'chat', 'web_capture']);
});
