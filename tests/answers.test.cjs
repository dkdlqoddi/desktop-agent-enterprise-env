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
const { AnswerStream, KEEP_MS, newRequestId, pending } = load('src/components/answers.ts');
const { createStore } = load('src/lib/store.ts');

// A stream whose assistant.cancel the test answers, at a clock it sets.
function stream({ cancelled = true, fails = false } = {}) {
  const clock = { now: 1_000 };
  const cancels = [];
  const answers = new AnswerStream(
    createStore({}),
    async (requestId) => {
      cancels.push(requestId);
      if (fails) throw new Error('백엔드를 다시 시작하고 있습니다.');
      return { ok: true, cancelled };
    },
    () => clock.now,
  );
  return { answers, clock, cancels };
}
const delta = (request_id, text, extra = {}) => ({
  type: 'delta',
  request_id,
  conversation_id: 'c1',
  text,
  ...extra,
});
const result = (extra = {}) => ({
  conversation_id: 'c1',
  messages: [
    { seq: 1, role: 'user', content: '질문', citations: [], created_at: '' },
    { seq: 2, role: 'assistant', content: '답변입니다.', citations: [], created_at: '' },
  ],
  search: null,
  ...extra,
});

test('the parts of an answer add up, a reset starts the text over, and its end says how it ended', () => {
  const { answers } = stream();
  answers.start('r1', '질문', null);
  assert.equal(answers.get('r1').status, 'streaming');
  assert.equal(answers.get('r1').conversationId, null);
  answers.event(delta('r1', '첫 '));
  answers.event(delta('r1', '부분'));
  // A new conversation's ID comes with its first event.
  assert.equal(answers.get('r1').conversationId, 'c1');
  assert.equal(answers.get('r1').text, '첫 부분');
  answers.event(delta('r1', '다시 쓴 답', { reset: true }));
  assert.equal(answers.get('r1').text, '다시 쓴 답');
  answers.event({
    type: 'done',
    request_id: 'r1',
    conversation_id: 'c1',
    status: 'completed',
    truncated: true,
  });
  assert.equal(answers.get('r1').status, 'completed');
  assert.equal(answers.get('r1').truncated, true);
  assert.equal(pending(answers.get('r1')), true);
  answers.finish('r1', result({ truncated: true }));
  assert.equal(pending(answers.get('r1')), false);
  assert.equal(answers.latest('c1').requestId, 'r1');
});

test("another request's events, an AI tool's, change nothing", () => {
  const { answers } = stream();
  answers.start('r1', '질문', 'c1');
  const before = answers.store.get();
  answers.event(delta('tool', '남의 답'));
  assert.equal(answers.store.get(), before);
});

test('a request that answers without its end event is ended by its answer, stopped or not', () => {
  const { answers } = stream();
  answers.start('r1', '질문', null);
  answers.finish('r1', result({ cancelled: true }));
  assert.equal(answers.get('r1').status, 'cancelled');
  answers.start('r2', '질문', 'c1');
  answers.finish('r2', result());
  assert.equal(answers.get('r2').status, 'completed');
});

test('a failed request leaves nothing, and a deleted conversation takes its answers along', () => {
  const { answers } = stream();
  answers.start('r1', '질문', 'c1');
  answers.fail('r1');
  assert.equal(answers.get('r1'), null);
  answers.start('r2', '질문', 'c1');
  answers.start('r3', '다른 질문', 'c2');
  answers.forget('c1');
  assert.deepEqual(Object.keys(answers.store.get()), ['r3']);
});

test('the newest answer of a conversation is the one shown when the conversation opens', () => {
  const { answers, clock } = stream();
  answers.start('r1', '첫 질문', 'c1');
  answers.finish('r1', result());
  clock.now += 10;
  answers.start('r2', '이어서', 'c1');
  assert.equal(answers.latest('c1').requestId, 'r2');
  assert.equal(answers.latest('c9'), null);
});

test('answers that ended long ago go when a new question starts; those still being written stay', () => {
  const { answers, clock } = stream();
  answers.start('old', '오래된 질문', 'c1');
  answers.finish('old', result());
  answers.start('slow', '느린 질문', 'c2');
  clock.now += KEEP_MS + 1;
  answers.start('new', '새 질문', null);
  assert.deepEqual(Object.keys(answers.store.get()).sort(), ['new', 'slow']);
});

test('중지 asks the backend once, and an answer that had already ended is left as it is', async () => {
  const { answers, cancels } = stream();
  answers.start('r1', '질문', null);
  const stopping = answers.stop('r1');
  assert.equal(answers.get('r1').stopping, true);
  // Pressed again while the first press is on its way: asked once.
  await answers.stop('r1');
  await stopping;
  assert.deepEqual(cancels, ['r1']);
  // Its end clears it.
  answers.event({
    type: 'done',
    request_id: 'r1',
    conversation_id: 'c1',
    status: 'cancelled',
    truncated: false,
  });
  assert.equal(answers.get('r1').stopping, false);
  assert.equal(answers.get('r1').status, 'cancelled');
  answers.finish('r1', result({ cancelled: true }));
  await answers.stop('r1');
  assert.deepEqual(cancels, ['r1']);

  const ended = stream({ cancelled: false });
  ended.answers.start('r2', '질문', null);
  await ended.answers.stop('r2');
  assert.equal(ended.answers.get('r2').stopping, false);
});

test('a stop that fails is told, and the button can be pressed again', async () => {
  const { answers } = stream({ fails: true });
  answers.start('r1', '질문', null);
  await assert.rejects(answers.stop('r1'), /다시 시작/);
  assert.equal(answers.get('r1').stopping, false);
});

test('request IDs are the backend form, one of a kind', () => {
  const ids = new Set(Array.from({ length: 50 }, newRequestId));
  assert.equal(ids.size, 50);
  for (const id of ids) assert.match(id, /^[A-Za-z0-9._:-]{1,80}$/);
});
