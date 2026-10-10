const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { BackendProcess, ENDED } = require('../electron/backend-process.cjs');

// A stand-in for the Python backend: JSON lines in, JSON lines out. SILENT=1 never answers; FAIL_AT_START=1 ends at
// once, as a backend missing a DLL does.
const FAKE = `
const readline = require('node:readline');
const reply = (message) => process.stdout.write(JSON.stringify(message) + '\\n');
if (process.env.FAIL_AT_START === '1') {
  process.stderr.write('ImportError: DLL load failed while importing _sqlite3\\n');
  process.exit(1);
}
readline.createInterface({ input: process.stdin }).on('line', (line) => {
  if (process.env.SILENT === '1') return;
  const { id, method, params } = JSON.parse(line);
  if (method === 'health') reply({ id, result: { ok: true } });
  else if (method === 'echo') reply({ id, result: params });
  else if (method === 'fail') reply({ id, error: '설정 값이 올바르지 않습니다.' });
  else if (method === 'noise') {
    process.stdout.write('null\\nnot json\\n{"id": null}\\n');
    reply({ id, result: 'after noise' });
  } else if (method === 'stream') {
    // An answer's parts as the backend sends them, lines without an id, before the answer itself; an event with an id,
    // or one without a name, is no event.
    const { request_id } = params;
    reply({ event: 'assistant.delta', params: { request_id, conversation_id: 'c1', text: '캐시를' } });
    reply({ event: 'assistant.delta', params: { request_id, conversation_id: 'c1', text: ' 도입' } });
    reply({ id: 'someone-else', event: 'assistant.delta', params: {} });
    reply({ event: 7 });
    reply({ event: 'assistant.done', params: { request_id, conversation_id: 'c1', status: 'completed' } });
    reply({ id, result: { conversation_id: 'c1' } });
  } else if (method === 'crash') {
    process.stderr.write('Traceback (most recent call last):\\nImportError: DLL load failed\\n');
    setTimeout(() => process.exit(3), 20);
  }
});
`;

async function setup(t, { env = {}, executable, spawn, packaged = true, limits, onEvent } = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-backend-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const script = path.join(root, 'backend.cjs');
  await fs.writeFile(script, FAKE);
  const records = [];
  const diagnostics = {
    stderr: '',
    record: async (text) => void records.push(text),
    capture(chunk) {
      this.stderr += chunk;
    },
  };
  const exits = [];
  const backend = new BackendProcess({
    executable: executable ?? process.execPath,
    args: executable ? [] : [script],
    options: {
      env: { ...process.env, ...env },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
      detached: process.platform !== 'win32',
    },
    diagnostics,
    packaged,
    limits,
    exited: (outcome) => {
      exits.push(outcome);
      return '다시 시작하는 중입니다.';
    },
    ...(spawn ? { spawn } : {}),
    ...(onEvent ? { onEvent } : {}),
  });
  t.after(() => backend.stop());
  return { backend, records, diagnostics, exits };
}

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('a backend that answers starts, answers its requests, and leaves no time-out in the log', async (t) => {
  const { backend, records } = await setup(t);
  await backend.started({ limit: 300, message: '시작하지 못했습니다.' });
  assert.equal(backend.running, true);
  assert.deepEqual(await backend.request('echo', { a: 1 }), { a: 1 });
  await assert.rejects(backend.request('fail'), /설정 값이 올바르지 않습니다/);
  // Lines that are no answer of a request waiting are passed over.
  assert.equal(await backend.request('noise'), 'after noise');
  // The start's time limit has passed since: it ended with the start.
  await pause(400);
  assert.deepEqual(records, []);
});

test("the backend's own events reach their listener in order, before the answer they belong to", async (t) => {
  const events = [];
  const { backend, records } = await setup(t, {
    onEvent: (name, params) => {
      events.push([name, params]);
      // A listener that fails costs neither the other events nor the answer.
      if (params.text === ' 도입') throw new Error('창이 닫혔습니다');
    },
  });
  await backend.started();
  const answer = await backend.request('stream', { request_id: 'r1' });
  assert.deepEqual(answer, { conversation_id: 'c1' });
  assert.deepEqual(events, [
    ['assistant.delta', { request_id: 'r1', conversation_id: 'c1', text: '캐시를' }],
    ['assistant.delta', { request_id: 'r1', conversation_id: 'c1', text: ' 도입' }],
    ['assistant.done', { request_id: 'r1', conversation_id: 'c1', status: 'completed' }],
  ]);
  assert.deepEqual(records, [
    '백엔드 이벤트 assistant.delta을(를) 전하지 못했습니다: 창이 닫혔습니다',
  ]);
  // Without a listener, events are passed over.
  const { backend: quiet } = await setup(t);
  await quiet.started();
  assert.deepEqual(await quiet.request('stream', { request_id: 'r2' }), { conversation_id: 'c1' });
});

test('a backend whose program is missing says so, logs the error, and fails its requests at once', async (t) => {
  const missing = path.join(os.tmpdir(), 'no-such-folder', 'private-agent-backend.exe');
  const { backend, records, exits } = await setup(t, { executable: missing });
  await assert.rejects(backend.started({ limit: 5000 }), (error) => {
    assert.match(error.message, /실행 파일을 찾지 못했습니다\(.*private-agent-backend\.exe\)/);
    assert.match(error.message, /백신이 검역\(격리\)했을 수 있으니/);
    return true;
  });
  assert.equal(backend.running, false);
  await assert.rejects(backend.request('echo'), /실행 파일을 찾지 못했습니다/);
  assert.equal(records.length, 1);
  assert.match(records[0], /백엔드 프로세스를 시작하지 못했습니다: .*ENOENT/);
  assert.match(records[0], /코드 ENOENT, errno -?\d+, 실행 파일 .*private-agent-backend\.exe/);
  // No exit is reported for a process that never ran.
  assert.deepEqual(exits, []);
});

test('a program blocked before a process exists is reported the same way', async (t) => {
  const { backend, records } = await setup(t, {
    executable: 'C:\\blocked\\private-agent-backend.exe',
    spawn: () => {
      throw Object.assign(new Error('spawn UNKNOWN'), { code: 'UNKNOWN', errno: -4094 });
    },
  });
  await assert.rejects(
    backend.started(),
    /실행하지 못했습니다\(UNKNOWN: .*\).*백신이나 보안 프로그램이 막았을/,
  );
  assert.equal(backend.running, false);
  assert.equal(backend.pid, undefined);
  assert.match(records[0], /코드 UNKNOWN, errno -4094/);
  await backend.stop();
});

test('in development the message points to the Python setup', async (t) => {
  const { backend } = await setup(t, { executable: '/no/python', packaged: false });
  await assert.rejects(backend.started(), /npm run setup:python/);
});

test('a backend that never answers fails its start after the time limit, and the log says so', async (t) => {
  const { backend, records } = await setup(t, { env: { SILENT: '1' } });
  await assert.rejects(backend.started({ limit: 200, message: '백엔드를 시작하지 못했습니다.' }), {
    message: '백엔드를 시작하지 못했습니다.',
  });
  assert.deepEqual(records, ['백엔드가 0초 안에 응답하지 않았습니다.']);
});

test('a backend that ends while it starts says so, with its exit code', async (t) => {
  const { backend, diagnostics, exits } = await setup(t, { env: { FAIL_AT_START: '1' } });
  await assert.rejects(backend.started(), {
    message: 'Python 백엔드가 시작하는 중에 종료되었습니다(종료 코드 1). 앱을 다시 실행해 주세요.',
  });
  assert.deepEqual(exits, [{ code: 1, signal: null }]);
  assert.match(diagnostics.stderr, /DLL load failed/);
});

test('a backend that ends fails its waiting requests with the message the app gives, and keeps its error output', async (t) => {
  const { backend, diagnostics, exits } = await setup(t);
  await backend.started();
  const waiting = backend.request('crash');
  await assert.rejects(waiting, { message: '다시 시작하는 중입니다.' });
  assert.deepEqual(exits, [{ code: 3, signal: null }]);
  assert.match(diagnostics.stderr, /ImportError: DLL load failed/);
  assert.equal(backend.running, false);
  await assert.rejects(backend.request('echo'), { message: ENDED });
});

test('a request that runs out of time fails and is logged; stopping fails the rest and ends the process', async (t) => {
  const { backend, records } = await setup(t, { limits: { slow: 100 } });
  await backend.started();
  await backend.request('echo', {}); // Answered, so the process reads its input.
  await assert.rejects(backend.request('slow'), /응답 제한 시간을 초과했습니다/);
  assert.deepEqual(records, ['요청 slow이(가) 0.1초 안에 끝나지 않았습니다.']);
  const waiting = assert.rejects(backend.request('never answered'), {
    message: '앱을 닫는 중입니다.',
  });
  await backend.stop('앱을 닫는 중입니다.');
  await waiting;
  assert.equal(backend.running, false);
});
