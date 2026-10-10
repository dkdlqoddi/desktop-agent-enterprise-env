const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {
  Diagnostics,
  MAX_BYTES,
  STDERR_BYTES,
  TAIL_BYTES,
  anonymize,
  buildInfo,
  redact,
} = require('../electron/diagnostics.cjs');

async function logs(t, options = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-diagnostics-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const directory = path.join(root, 'logs');
  return {
    root,
    directory,
    diagnostics: new Diagnostics({
      directory,
      now: () => new Date(0),
      build: null,
      level: '',
      terminal: false,
      ...options,
    }),
  };
}

test('records what failed in the app log, and starts a new file past its size', async (t) => {
  const { directory, diagnostics } = await logs(t);
  await diagnostics.record('요청 wiki.search이(가) 960초 안에 끝나지 않았습니다.\n');
  const file = path.join(directory, 'app.log');
  assert.equal(
    await fs.readFile(file, 'utf8'),
    '1970-01-01T00:00:00.000Z 요청 wiki.search이(가) 960초 안에 끝나지 않았습니다.\n',
  );
  await fs.writeFile(file, 'x'.repeat(MAX_BYTES + 1));
  await diagnostics.record('다음 기록');
  assert.equal((await fs.stat(file + '.1')).size, MAX_BYTES + 1);
  assert.match(await fs.readFile(file, 'utf8'), /^1970-01-01T00:00:00.000Z 다음 기록\n$/);
});

test('keeps the end of the backend error output, and writes it down only when the backend ends', async (t) => {
  const { directory, diagnostics } = await logs(t);
  diagnostics.capture('x'.repeat(STDERR_BYTES));
  diagnostics.capture('Traceback (most recent call last):\nagent.llm.UserError: 설정 값\n');
  await assert.rejects(fs.access(path.join(directory, 'app.log')));
  await diagnostics.backendExited({ code: 1, signal: null });
  const logged = await fs.readFile(path.join(directory, 'app.log'), 'utf8');
  assert.match(logged, /백엔드가 예기치 않게 종료되었습니다 \(종료 코드 1\)/);
  assert.match(logged, /agent\.llm\.UserError: 설정 값$/m);
  assert.ok(logged.length < STDERR_BYTES + 200);
  // A later exit has nothing left over from this one.
  await diagnostics.backendExited({ code: null, signal: 'SIGKILL' });
  assert.match(
    await fs.readFile(path.join(directory, 'app.log'), 'utf8'),
    /\(종료 코드 없음, 신호 SIGKILL\)\.\n$/,
  );
});

test('a report holds the facts and the end of both logs, from whole lines', async (t) => {
  const { directory, diagnostics } = await logs(t);
  assert.match(
    await diagnostics.report(['앱 0.1.0']),
    /앱 0\.1\.0\n로그 폴더: .*\n\n=== app\.log \(마지막 부분\) ===\n\(기록 없음\)/,
  );
  await fs.mkdir(directory, { recursive: true });
  const lines = Array.from({ length: 2000 }, (_, i) => `줄 ${i} ${'가'.repeat(10)}`).join('\n');
  await fs.writeFile(path.join(directory, 'backend.log'), lines + '\n');
  await diagnostics.record('앱 기록');
  const report = await diagnostics.report(['앱 0.1.0', '백엔드 실행 중']);
  const backend = report.split('=== backend.log (마지막 부분) ===\n')[1];
  assert.ok(Buffer.byteLength(backend) <= TAIL_BYTES);
  assert.match(backend, /^줄 \d+ 가{10}\n/);
  assert.match(backend, /줄 1999 가{10}\n$/);
  assert.match(report, /=== app\.log \(마지막 부분\) ===\n1970-01-01T00:00:00.000Z 앱 기록\n/);
});

test('secrets quoted by what is logged are redacted, and the report leaves the user name out', async (t) => {
  const { directory, diagnostics } = await logs(t);
  await diagnostics.record(
    'LLM 요청 실패: https://llm.example/v1?api_key=sk-abcdef123456 {"Authorization":"Bearer eyJhbGciOi.payload","token":"pa_1234567890abcdef","password":"p@ss word"}',
  );
  diagnostics.capture('agent.llm.UserError: x-api-key: secret-value-1\n');
  await diagnostics.backendExited({ code: 1, signal: null });
  const logged = await fs.readFile(path.join(directory, 'app.log'), 'utf8');
  for (const secret of [
    'sk-abcdef123456',
    'eyJhbGciOi.payload',
    'pa_1234567890abcdef',
    'p@ss word',
    'secret-value-1',
  ])
    assert.doesNotMatch(logged, new RegExp(secret.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), secret);
  assert.match(logged, /llm\.example\/v1\?api_key=\[REDACTED\]/);
  await fs.writeFile(path.join(directory, 'backend.log'), 'older log: Bearer abcdef.ghijkl\n');
  const report = await diagnostics.report([
    '앱 0.1.0 · C:\\Users\\gildong\\AppData',
    '/home/gildong/.config',
  ]);
  assert.doesNotMatch(report, /abcdef\.ghijkl|gildong/);
  assert.match(report, /C:\\Users\\<user>\\AppData/);
  assert.match(report, /\/home\/<user>\/\.config/);
  // Ordinary words stay: only a value after the name of a secret goes.
  assert.equal(
    redact('the token count is 12; x-api-key: abc, next'),
    'the token count is 12; x-api-key: [REDACTED], next',
  );
  // A bearer token after its header name, and every cookie of a cookie header, go whole.
  assert.equal(
    redact('Authorization: Bearer abcdef.ghijkl\nnext'),
    'Authorization: [REDACTED]\nnext',
  );
  assert.equal(redact('cookie: sid=abc123; theme=dark'), 'cookie: [REDACTED]');
  assert.equal(anonymize('D:\\data\\wiki.db'), 'D:\\data\\wiki.db');
});

test('a log that cannot be written never stops the app', async (t) => {
  const { directory } = await logs(t);
  await fs.mkdir(path.dirname(directory), { recursive: true });
  await fs.writeFile(directory, 'a file where the folder should be');
  const diagnostics = new Diagnostics({ directory });
  await diagnostics.record('기록');
  assert.match(await diagnostics.report([]), /\(기록 없음\)/);
});

// scripts/package.mjs writes electron/build-info.json into the package; a run from source has none.
test('a packaged build names its commit and date at the top of the report', async (t) => {
  const { root, diagnostics } = await logs(t, {
    build: { commit: '0123456789abcdef0123456789abcdef01234567', date: '2026-10-10T01:02:03Z' },
  });
  assert.match(
    await diagnostics.report(['앱 0.1.0']),
    /^Private Agent 진단 정보 · .*\n빌드 0123456789ab · 2026-10-10T01:02:03Z\n앱 0\.1\.0\n/,
  );
  const file = path.join(root, 'build-info.json');
  await fs.writeFile(file, JSON.stringify({ commit: 'abcdef1234567-dirty', date: '2026-10-10' }));
  assert.deepEqual(buildInfo(file), { commit: 'abcdef1234567-dirty', date: '2026-10-10' });
  const dirty = new Diagnostics({ directory: root, build: buildInfo(file), terminal: false });
  assert.match(await dirty.report([]), /\n빌드 abcdef123456-dirty · 2026-10-10\n/);
  // A file that is missing, damaged or names no commit gives no line.
  assert.equal(buildInfo(path.join(root, 'missing.json')), null);
  await fs.writeFile(file, '{"commit": "<script>"}');
  assert.equal(buildInfo(file), null);
  await fs.writeFile(file, 'not json');
  assert.equal(buildInfo(file), null);
  const { diagnostics: source } = await logs(t);
  assert.doesNotMatch(await source.report([]), /빌드/);
});

test('debug entries are kept only at PRIVATE_AGENT_LOG_LEVEL=debug, and the terminal can get a copy of each', async (t) => {
  const quiet = await logs(t);
  await quiet.diagnostics.debug('요청 wiki.search 12ms');
  await assert.rejects(fs.access(path.join(quiet.directory, 'app.log')));
  const written = [];
  const loud = await logs(t, {
    level: ' DEBUG ',
    terminal: true,
    output: { write: (text) => written.push(text) },
  });
  await loud.diagnostics.debug('요청 wiki.search 12ms');
  await loud.diagnostics.record('백엔드 실패: token=abc123');
  assert.equal(
    await fs.readFile(path.join(loud.directory, 'app.log'), 'utf8'),
    '1970-01-01T00:00:00.000Z 요청 wiki.search 12ms\n1970-01-01T00:00:00.000Z 백엔드 실패: token=[REDACTED]\n',
  );
  assert.equal(written.join(''), await fs.readFile(path.join(loud.directory, 'app.log'), 'utf8'));
});
