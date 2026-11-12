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
const { AgentTurn, NO_TURN } = load('src/components/agent-turn.ts');
const { createStore } = load('src/lib/store.ts');

const message = (seq, role, content) => ({ seq, role, content, actions: [], engine: '' });
const SENT = { user: message(1, 'user', '광고 기록해 줘'), reply: message(2, 'assistant', '네.') };
const status = (extra = {}) => ({
  phase: 'thinking',
  text: '',
  draft: '',
  progress: [],
  interaction: null,
  can_stop: true,
  active: true,
  ...extra,
});
const approval = (id) => ({
  kind: 'approval',
  interaction_id: id,
  command: 'rm -r build',
  description: '',
  smart_denied: false,
});
const clarify = (id) => ({
  kind: 'clarify',
  interaction_id: id,
  questions: [{ qid: 'q1', question: '어느 사이트인가요?', choices: null }],
});

// A turn whose backend the test answers: agent.send settles when the test says, the other methods answer from
// `backend`, and the chat screen is shown or not as `screen.shown` says.
function setup() {
  const calls = [];
  const notices = [];
  const prompts = [];
  const screen = { shown: true };
  const backend = { status: status(), active: { request_id: '', text: '' }, fails: {} };
  let settle;
  const turn = new AgentTurn({
    store: createStore(NO_TURN),
    request: (method, params = {}) => {
      calls.push([method, params]);
      if (backend.fails[method]) return Promise.reject(new Error(backend.fails[method]));
      if (method === 'agent.send')
        return new Promise((resolve, reject) => (settle = { resolve, reject }));
      if (method === 'agent.status') return Promise.resolve(backend.status);
      if (method === 'agent.active') return Promise.resolve(backend.active);
      return Promise.resolve({ ok: true });
    },
    notify: (text, error) => notices.push([text, !!error]),
    prompt: (text, waits) => prompts.push([text, waits]),
    shown: () => screen.shown,
    // The test asks for each status itself.
    every: () => () => {},
  });
  const sent = (method) => calls.filter(([name]) => name === method).map(([, params]) => params);
  return { turn, calls, sent, notices, prompts, screen, backend, settle: () => settle };
}
const soon = () => new Promise((resolve) => setImmediate(resolve));

test('a message is sent, followed while the agent works, and joins the conversation as it ends', async () => {
  const { turn, sent, settle, backend, prompts } = setup();
  const done = turn.send('광고 기록해 줘', 'r1');
  assert.deepEqual(turn.store.get().sending, { id: 'r1', text: '광고 기록해 줘' });
  assert.deepEqual(sent('agent.send'), [{ text: '광고 기록해 줘', request_id: 'r1' }]);
  // A second message waits for the first: nothing is sent.
  await turn.send('또 하나', 'r2');
  assert.equal(sent('agent.send').length, 1);

  backend.status = status({
    draft: '쓰는 중',
    progress: [{ event: 'tool.started', text: '검색' }],
  });
  await turn.poll('r1');
  assert.equal(turn.store.get().status.draft, '쓰는 중');
  // A status of the request's last moment (the backend has saved the answer) does not end a turn whose own answer
  // is on its way: the answer does, with the messages it saved.
  backend.status = status({ phase: 'idle', active: false });
  await turn.poll('r1');
  assert.ok(turn.store.get().sending);

  settle().resolve(SENT);
  await done;
  const state = turn.store.get();
  assert.equal(state.sending, null);
  assert.equal(state.status, null);
  assert.deepEqual(state.ended, { n: 1, sent: SENT });
  // The user watched it end: nothing more is told.
  assert.deepEqual(prompts, []);
  // A status that answers late changes nothing.
  await turn.poll('r1');
  assert.equal(turn.store.get().status, null);
});

test('on another screen the user is told once that the agent waits, and that it has ended', async () => {
  const { turn, settle, backend, prompts, screen } = setup();
  const done = turn.send('빌드 폴더 지워 줘', 'r1');
  screen.shown = false;
  backend.status = status({ phase: 'waiting', interaction: approval('i1') });
  await turn.poll('r1');
  await turn.poll('r1');
  assert.equal(prompts.length, 1);
  assert.match(prompts[0][0], /확인을 기다리고/);
  assert.equal(prompts[0][1], true);
  // The question is still there when the chat is shown again, and another one is told anew.
  assert.equal(turn.store.get().status.interaction.interaction_id, 'i1');
  backend.status = status({ phase: 'waiting', interaction: clarify('i2') });
  await turn.poll('r1');
  assert.equal(prompts.length, 2);
  // Back on the chat nothing is told: the question is on the screen.
  screen.shown = true;
  backend.status = status({ phase: 'waiting', interaction: approval('i3') });
  await turn.poll('r1');
  assert.equal(prompts.length, 2);

  screen.shown = false;
  settle().resolve(SENT);
  await done;
  assert.deepEqual(prompts[2], ['채팅 에이전트가 작업을 마쳤습니다.', false]);
});

test('the decision and the answers go to the question shown, which leaves the screen at once', async () => {
  const { turn, sent, backend, notices } = setup();
  void turn.send('빌드 폴더 지워 줘', 'r1');
  // Nothing is asked: nothing is sent.
  await turn.decide('once');
  assert.deepEqual(sent('agent.approve_command'), []);

  backend.status = status({ phase: 'waiting', interaction: approval('i1') });
  await turn.poll('r1');
  await turn.answer({ q1: '쿠팡' });
  assert.deepEqual(sent('agent.answer_clarify'), []);
  await turn.decide('deny');
  assert.deepEqual(sent('agent.approve_command'), [
    { request_id: 'r1', interaction_id: 'i1', decision: 'deny' },
  ]);
  assert.equal(turn.store.get().status.interaction, null);

  backend.status = status({ phase: 'waiting', interaction: clarify('i2') });
  await turn.poll('r1');
  await turn.answer({ q1: '쿠팡' });
  assert.deepEqual(sent('agent.answer_clarify'), [
    { request_id: 'r1', interaction_id: 'i2', answers: { q1: '쿠팡' } },
  ]);
  // A refusal (the question ran out meanwhile) is told to the user.
  backend.status = status({ phase: 'waiting', interaction: approval('i3') });
  await turn.poll('r1');
  backend.fails['agent.approve_command'] = '응답할 작업을 찾을 수 없습니다.';
  await turn.decide('once');
  assert.deepEqual(notices, [['응답할 작업을 찾을 수 없습니다.', true]]);

  await turn.stop();
  assert.deepEqual(sent('agent.stop'), [{ request_id: 'r1' }]);
});

test('a request that failed gives its words back once and tells why', async () => {
  const { turn, settle, notices } = setup();
  const done = turn.send('광고 기록해 줘', 'r1');
  settle().reject(new Error('LLM이 연결되지 않았습니다.'));
  await done;
  const state = turn.store.get();
  assert.equal(state.sending, null);
  assert.equal(state.ended.n, 0);
  assert.deepEqual(notices, [['LLM이 연결되지 않았습니다.', true]]);
  assert.equal(turn.takeFailed(), '광고 기록해 줘');
  assert.equal(turn.takeFailed(), null);
  // The next message starts afresh.
  void turn.send('다시', 'r2');
  assert.equal(turn.store.get().sending.id, 'r2');
});

test('a request that ended without its answer while the turn goes on is followed to its end', async () => {
  const { turn, settle, backend, notices } = setup();
  const done = turn.send('오래 걸리는 일', 'r1');
  backend.active = { request_id: 'r1', text: '오래 걸리는 일' };
  settle().reject(new Error('요청 시간이 초과되었습니다.'));
  await done;
  // Still running in the backend: the turn stays, and nothing is told as failed.
  assert.equal(turn.store.get().sending.id, 'r1');
  assert.deepEqual(notices, []);
  assert.equal(turn.takeFailed(), null);
  backend.status = status({ phase: 'waiting', interaction: approval('i1') });
  await turn.poll('r1');
  assert.equal(turn.store.get().status.interaction.interaction_id, 'i1');
  // It ends when the backend carries it out no more; the conversation is then read again (sent: null).
  backend.status = status({ phase: 'idle', active: false });
  await turn.poll('r1');
  assert.equal(turn.store.get().sending, null);
  assert.deepEqual(turn.store.get().ended, { n: 1, sent: null });
});

test('a chat loaded while a turn runs finds it, and one loaded when none does finds nothing', async () => {
  const { turn, backend, sent } = setup();
  await turn.resume();
  assert.equal(turn.store.get().sending, null);

  backend.active = { request_id: 'r9', text: '광고 기록해 줘' };
  await turn.resume();
  assert.deepEqual(turn.store.get().sending, { id: 'r9', text: '광고 기록해 줘' });
  // Shown again while it is followed: nothing is asked or started twice.
  const asked = sent('agent.active').length;
  await turn.resume();
  assert.equal(sent('agent.active').length, asked);

  backend.status = status({ phase: 'waiting', interaction: clarify('i1') });
  await turn.poll('r9');
  assert.equal(turn.store.get().status.interaction.kind, 'clarify');
  backend.status = status({ phase: 'idle', active: false });
  await turn.poll('r9');
  assert.deepEqual(turn.store.get().ended, { n: 1, sent: null });
  assert.equal(turn.store.get().sending, null);

  // A backend that cannot say (it is starting again) leaves the chat as it is.
  backend.fails['agent.active'] = '백엔드를 다시 시작하고 있습니다.';
  await turn.resume();
  assert.equal(turn.store.get().sending, null);
});

test('a status that does not come is only a draft missed', async () => {
  const { turn, backend, settle } = setup();
  const done = turn.send('광고 기록해 줘', 'r1');
  backend.fails['agent.status'] = '진행 중인 요청이 많습니다.';
  await turn.poll('r1');
  assert.equal(turn.store.get().status, null);
  assert.ok(turn.store.get().sending);
  delete backend.fails['agent.status'];
  await turn.poll('r1');
  assert.equal(turn.store.get().status.phase, 'thinking');
  settle().resolve(SENT);
  await done;
  await soon();
});
