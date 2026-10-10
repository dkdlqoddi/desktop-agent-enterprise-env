const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// A module of src as CommonJS; its type imports go with the transpiler and React is the one in node_modules.
function load(file) {
  const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  });
  const module = { exports: {} };
  new Function('module', 'exports', 'require', outputText)(module, module.exports, require);
  return module.exports;
}
const { JobsFeed, NO_JOBS } = load('src/components/import-jobs.ts');
const { createStore } = load('src/lib/store.ts');

const none = { office: null, rtf: null, image: null, text: null, confluence: null, jira: null };
const office = (status, extra = {}) => ({
  id: 'o1',
  status,
  total: 2,
  processed: 0,
  batch: 1,
  batches: 1,
  results: [],
  ...extra,
});
const analysis = (status, processed, queue = []) => ({
  id: 'a1',
  status,
  total: 3,
  processed,
  queue,
  current: null,
  results: [],
});

function setup() {
  const store = createStore(NO_JOBS);
  const changes = [];
  const routines = [];
  const feed = new JobsFeed({
    store,
    request: async () => ({ seq: 1, jobs: { ...none, analysis: null } }),
    changed: () => changes.push(store.get()),
    routines: (list) => routines.push(list),
  });
  return { store, feed, changes, routines };
}

test("the first read's finished jobs are no news; one that finishes later reloads the documents", () => {
  const { store, feed, changes } = setup();
  feed.snapshot({ seq: 1, jobs: { ...none, office: office('completed'), analysis: null } });
  assert.equal(store.get().imports.office.status, 'completed');
  assert.equal(changes.length, 0);
  feed.event({ seq: 2, kind: 'rtf', job: { ...office('running'), id: 'r1' } });
  assert.equal(changes.length, 0);
  feed.event({ seq: 3, kind: 'rtf', job: { ...office('completed'), id: 'r1' } });
  assert.equal(changes.length, 1);
  assert.equal(store.get().imports.rtf.status, 'completed');
  // Told once: the same job read again is no news.
  feed.snapshot({
    seq: 4,
    jobs: { ...none, office: office('completed'), rtf: { ...office('completed'), id: 'r1' } },
  });
  assert.equal(changes.length, 1);
});

test('a read older than the one shown is dropped, and the same state draws nothing again', () => {
  const { store, feed } = setup();
  feed.snapshot({ seq: 5, jobs: { ...none, office: office('running', { processed: 1 }) } });
  const shown = store.get();
  feed.event({ seq: 4, kind: 'office', job: office('running', { processed: 0 }) });
  assert.equal(store.get(), shown);
  // A job whose results grew is a change; the same job again keeps the state as it was.
  feed.event({ seq: 6, kind: 'office', job: office('running', { processed: 2, results: [{}] }) });
  const next = store.get();
  assert.notEqual(next, shown);
  assert.equal(next.imports.office.processed, 2);
  feed.event({ seq: 7, kind: 'office', job: office('running', { processed: 2, results: [{}] }) });
  assert.equal(store.get(), next);
});

test('a batch of a folder done reloads the documents before the whole job ends', () => {
  const { feed, changes } = setup();
  feed.snapshot({ seq: 1, jobs: { ...none, office: office('running', { batches: 3 }) } });
  feed.event({ seq: 2, kind: 'office', job: office('running', { batches: 3, processed: 9 }) });
  assert.equal(changes.length, 0);
  feed.event({ seq: 3, kind: 'office', job: office('running', { batches: 3, batch: 2 }) });
  assert.equal(changes.length, 1);
});

test('the analysis reloads the documents as it moves on, and tells which documents wait', () => {
  const { store, feed, changes } = setup();
  feed.snapshot({ seq: 1, jobs: { ...none, analysis: null } });
  feed.event({ seq: 2, kind: 'analysis', job: analysis('running', 0, ['a', 'b', 'c']) });
  assert.equal(changes.length, 1);
  assert.deepEqual([...store.get().queued], ['a', 'b', 'c']);
  feed.event({ seq: 3, kind: 'analysis', job: analysis('running', 1, ['b', 'c']) });
  feed.event({ seq: 4, kind: 'analysis', job: analysis('completed', 3, []) });
  assert.equal(changes.length, 3);
  assert.deepEqual([...store.get().queued], []);
  assert.equal(store.get().analysis.status, 'completed');
});

test('the routines sent along go to their own answer, and a failed read changes nothing', async () => {
  const { store, feed, routines } = setup();
  feed.event({ seq: 1, kind: 'routines', routines: [{ id: 'r', running: true }] });
  assert.deepEqual(routines, [[{ id: 'r', running: true }]]);
  const failing = new JobsFeed({
    store,
    request: async () => {
      throw new Error('busy');
    },
    changed: () => assert.fail('no change'),
    routines: () => {},
  });
  await failing.refresh();
  assert.equal(store.get(), NO_JOBS);
});
