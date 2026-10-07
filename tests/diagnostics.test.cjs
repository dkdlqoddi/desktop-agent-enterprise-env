const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { Diagnostics, MAX_BYTES, STDERR_BYTES, TAIL_BYTES } = require('../electron/diagnostics.cjs');

async function logs(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-diagnostics-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const directory = path.join(root, 'logs');
  return { directory, diagnostics: new Diagnostics({ directory, now: () => new Date(0) }) };
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

test('a log that cannot be written never stops the app', async (t) => {
  const { directory } = await logs(t);
  await fs.mkdir(path.dirname(directory), { recursive: true });
  await fs.writeFile(directory, 'a file where the folder should be');
  const diagnostics = new Diagnostics({ directory });
  await diagnostics.record('기록');
  assert.match(await diagnostics.report([]), /\(기록 없음\)/);
});
