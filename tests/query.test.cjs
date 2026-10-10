const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// src/lib/query.ts as CommonJS: it imports types only, which the transpiler drops.
function load(file) {
  const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const module = { exports: {} };
  new Function('module', 'exports', 'require', outputText)(module, module.exports, require);
  return module.exports;
}
const { QueryCache, TAGS, keepEqual } = load('src/lib/query.ts');

// A backend whose answers the test gives one by one: each request waits in `pending` until answered.
function backend() {
  const pending = [];
  const send = (method, params) =>
    new Promise((resolve, reject) => pending.push({ method, params, resolve, reject }));
  return { pending, cache: new QueryCache(send) };
}
const flush = () => new Promise((resolve) => setImmediate(resolve));

test('an answer is one per method and parameters, whatever order the keys come in', () => {
  const { cache } = backend();
  const a = cache.entry('runs.list', { prompt_id: 'p', limit: 5 });
  const b = cache.entry('runs.list', { limit: 5, prompt_id: 'p', before: undefined });
  assert.equal(a, b);
  assert.notEqual(a, cache.entry('runs.list', { prompt_id: 'q', limit: 5 }));
  assert.deepEqual(a.tags, TAGS['runs.list']);
});

test('the first component to show an answer loads it; others share it until none shows it', async () => {
  const { cache, pending } = backend();
  const entry = cache.entry('wiki.graph');
  const seen = [];
  const off = cache.subscribe(entry, () => seen.push(entry.state));
  const other = cache.subscribe(entry, () => {});
  assert.equal(pending.length, 1);
  pending[0].resolve({ nodes: [] });
  await flush();
  assert.deepEqual(entry.state, { data: { nodes: [] }, error: null });
  assert.equal(seen.length, 1);
  off();
  other();
  // Shown again after nobody showed it: it loads afresh, with the last data meanwhile.
  cache.subscribe(entry, () => {});
  assert.equal(pending.length, 2);
  assert.deepEqual(entry.state.data, { nodes: [] });
});

test('a component that only reads what the app loads asks for nothing', async () => {
  const { cache, pending } = backend();
  const entry = cache.entry('stats');
  let told = 0;
  cache.subscribe(entry, () => told++, false);
  assert.equal(pending.length, 0);
  // The app loads it (loadLibrary): the reader hears the answer.
  void cache.load(entry);
  pending[0].resolve({ documents: 2 });
  await flush();
  assert.equal(told, 1);
  assert.deepEqual(entry.state.data, { documents: 2 });
});

test('an earlier request answering after a later one is dropped', async () => {
  const { cache, pending } = backend();
  const entry = cache.entry('wiki.list');
  const first = cache.fetch(entry);
  const second = cache.fetch(entry);
  pending[1].resolve(['new']);
  await second;
  pending[0].resolve(['old']);
  assert.deepEqual(await first, ['old']);
  assert.deepEqual(entry.state.data, ['new']);
});

test('an answer that did not change keeps the objects shown, and draws nothing again', async () => {
  const { cache, pending } = backend();
  const entry = cache.entry('wiki.list');
  let told = 0;
  cache.subscribe(entry, () => told++);
  const first = [
    { id: 'a', title: 'A', keywords: ['x'] },
    { id: 'b', title: 'B', keywords: [] },
  ];
  pending[0].resolve(first);
  await flush();
  assert.equal(told, 1);
  void cache.fetch(entry);
  pending[1].resolve(JSON.parse(JSON.stringify(first)));
  await flush();
  assert.equal(told, 1);
  assert.equal(entry.state.data, first);
  // One item changed and moved to the top: the list is new, the unchanged item the very object it was.
  void cache.fetch(entry);
  pending[2].resolve([
    { id: 'b', title: 'B 고침', keywords: [] },
    { id: 'a', title: 'A', keywords: ['x'] },
  ]);
  await flush();
  assert.equal(told, 2);
  assert.notEqual(entry.state.data, first);
  assert.equal(entry.state.data[1], first[0]);
  assert.equal(entry.state.data[0].keywords, first[1].keywords);
});

test('keepEqual keeps equal parts of anything JSON gives', () => {
  const previous = { counts: { open: 1 }, list: [1, 2], name: '오늘' };
  assert.equal(keepEqual(previous, { counts: { open: 1 }, list: [1, 2], name: '오늘' }), previous);
  const next = keepEqual(previous, { counts: { open: 1 }, list: [1, 2, 3], name: '오늘' });
  assert.notEqual(next, previous);
  assert.equal(next.counts, previous.counts);
  assert.deepEqual(next.list, [1, 2, 3]);
  assert.deepEqual(keepEqual(previous, { counts: { open: 1 } }), { counts: { open: 1 } });
  assert.equal(keepEqual(null, 'x'), 'x');
  assert.equal(keepEqual([1], null), null);
});

test('invalidating a tag reloads the shown answers that carry it, and only those', async () => {
  const { cache, pending } = backend();
  const graph = cache.entry('wiki.graph');
  const runs = cache.entry('runs.stats', { days: 14 });
  const trash = cache.entry('wiki.trash');
  cache.subscribe(graph, () => {});
  cache.subscribe(runs, () => {});
  pending.splice(0).forEach((request) => request.resolve({}));
  await flush();
  const heard = [];
  cache.watch((tags) => heard.push(tags && [...tags]));
  const done = cache.invalidate(['documents']);
  // The graph is shown and reloads; the trash is not shown and loads when it is; the runs do not depend on documents.
  assert.deepEqual(
    pending.map((request) => request.method),
    ['wiki.graph'],
  );
  pending[0].resolve({ nodes: [1] });
  await done;
  assert.deepEqual(graph.state.data, { nodes: [1] });
  assert.equal(trash.state.data, undefined);
  assert.deepEqual(heard, [['documents']]);
  // Everything shown reloads for null (an AI tool changed something).
  void cache.invalidate(null);
  assert.deepEqual(
    pending
      .slice(1)
      .map((request) => request.method)
      .sort(),
    ['runs.stats', 'wiki.graph'],
  );
  assert.deepEqual(heard.at(-1), null);
});

test('a failed request keeps the last data, and the invalidation fails as it did', async () => {
  const { cache, pending } = backend();
  const entry = cache.entry('stats');
  cache.subscribe(entry, () => {});
  pending[0].resolve({ documents: 1 });
  await flush();
  const done = cache.invalidate(['prompts']);
  pending[1].reject(new Error('백엔드가 바쁩니다.'));
  await assert.rejects(done, /바쁩니다/);
  assert.deepEqual(entry.state.data, { documents: 1 });
  assert.equal(entry.state.error.message, '백엔드가 바쁩니다.');
});

test('data that came another way is shown as the latest answer', async () => {
  const { cache, pending } = backend();
  const entry = cache.entry('routines.list');
  cache.subscribe(entry, () => {});
  cache.set('routines.list', {}, [{ id: 'r', running: true }]);
  // The request asked before the data came is older: its answer is dropped.
  pending[0].resolve([]);
  await flush();
  assert.deepEqual(entry.state.data, [{ id: 'r', running: true }]);
});
