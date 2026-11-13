const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// A module of src as CommonJS; its type imports go with the transpiler.
function load(file) {
  const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const module = { exports: {} };
  new Function('module', 'exports', 'require', outputText)(module, module.exports, require);
  return module.exports;
}
const { afterFailedRestore, endOfPreparing } = load('src/components/restore-outcome.ts');

const A = 'wiki-manual-20261009-090000.db';
const B = 'wiki-20261008-090000.db';
const backups = ({ preparing = null, pending = null } = {}) => ({
  folder: '/wiki/backups',
  keep: 7,
  backups: [],
  pending_restore: pending && { file: pending, requested_at: '2026-10-09T00:00:00+00:00' },
  preparing_restore: preparing && { file: preparing, started_at: '2026-10-09T00:00:00+00:00' },
  last_restore: null,
});

test('a request that failed while its copy is still being made is told as the copy going on', () => {
  assert.equal(afterFailedRestore(backups({ preparing: A }), A), 'preparing');
  // Another copy under way (asked for by an AI tool, say) is not this request's.
  assert.equal(afterFailedRestore(backups({ preparing: B }), A), 'failed');
});

test('a request that failed after its copy was finished is told as scheduled', () => {
  assert.equal(afterFailedRestore(backups({ pending: A }), A), 'scheduled');
  // The restore of another backup that waits does not make this one scheduled.
  assert.equal(afterFailedRestore(backups({ pending: B }), A), 'failed');
});

test('a request that failed with nothing under way or waiting is a failure, also when wiki.backups was not read', () => {
  assert.equal(afterFailedRestore(backups(), A), 'failed');
  assert.equal(afterFailedRestore(null, A), 'failed');
});

test('the end of a copy the screen saw is told once it has ended, as scheduled or as failed', () => {
  // None seen before, or still being made: nothing to tell.
  assert.equal(endOfPreparing('', backups({ pending: A })), null);
  assert.equal(endOfPreparing('', backups()), null);
  assert.equal(endOfPreparing(A, backups({ preparing: A })), null);
  assert.equal(endOfPreparing(A, backups({ preparing: A, pending: B })), null);
  // Ended with its restore waiting, or without one (the backend failed it, or ended).
  assert.equal(endOfPreparing(A, backups({ pending: A })), 'scheduled');
  assert.equal(endOfPreparing(A, backups()), 'failed');
  assert.equal(endOfPreparing(A, backups({ pending: B })), 'failed');
  // Another copy took its place: the first one is over all the same.
  assert.equal(endOfPreparing(A, backups({ preparing: B, pending: A })), 'scheduled');
  assert.equal(endOfPreparing(A, backups({ preparing: B })), 'failed');
});
