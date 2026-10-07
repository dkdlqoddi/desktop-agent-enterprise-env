const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { replaceFile, readJsonFile, writeFileSafely } = require('../electron/files.cjs');

async function files(t) {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'agent-files-test-')));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const target = path.join(root, 'settings.json');
  await fs.writeFile(target, 'old');
  await fs.writeFile(target + '.tmp', 'new');
  return target;
}

const refusal = (code) => Object.assign(new Error(`${code}: rename`), { code });

test('a file another program holds for a moment is replaced once it is let go', async (t) => {
  const target = await files(t);
  const rename = fs.rename;
  let calls = 0;
  t.mock.method(fs, 'rename', (...args) =>
    ++calls < 3 ? Promise.reject(refusal('EPERM')) : rename(...args),
  );
  await replaceFile(target + '.tmp', target);
  assert.equal(calls, 3);
  assert.equal(await fs.readFile(target, 'utf8'), 'new');
});

test('other errors and a file held for good are reported', async (t) => {
  const target = await files(t);
  t.mock.method(fs, 'rename', () => Promise.reject(refusal('ENOENT')));
  await assert.rejects(replaceFile(target + '.tmp', target), { code: 'ENOENT' });
  assert.equal(fs.rename.mock.callCount(), 1);
  t.mock.restoreAll();
  t.mock.method(fs, 'rename', () => Promise.reject(refusal('EBUSY')));
  await assert.rejects(replaceFile(target + '.tmp', target, 3), { code: 'EBUSY' });
  assert.equal(fs.rename.mock.callCount(), 3);
  assert.equal(await fs.readFile(target, 'utf8'), 'old');
});

test('a settings file reads as its object, as null when missing, after a moment when held', async (t) => {
  const target = await files(t);
  assert.equal(await readJsonFile(target + '.missing'), null);
  await fs.writeFile(target, '{"port": 50123}');
  const readFile = fs.readFile;
  let calls = 0;
  t.mock.method(fs, 'readFile', (...args) =>
    ++calls < 3 ? Promise.reject(refusal('EBUSY')) : readFile(...args),
  );
  assert.deepEqual(await readJsonFile(target), { port: 50123 });
  assert.equal(calls, 3);
});

test('a damaged settings file is set aside, and one that cannot be read is an error', async (t) => {
  const target = await files(t);
  const directory = path.dirname(target);
  for (const damaged of ['{"cut": ', '', '[1, 2]', 'null']) {
    await fs.writeFile(target, damaged);
    assert.equal(await readJsonFile(target), null, JSON.stringify(damaged));
    await assert.rejects(fs.access(target));
  }
  const aside = (await fs.readdir(directory)).filter((name) =>
    name.startsWith('settings.json.corrupt-'),
  );
  assert.ok(aside.length >= 1);
  // Held for good, or not a file: reported, and the file stays as it is.
  await fs.writeFile(target, '{"kept": true}');
  t.mock.method(fs, 'readFile', () => Promise.reject(refusal('EBUSY')));
  await assert.rejects(readJsonFile(target, 2), { code: 'EBUSY' });
  t.mock.restoreAll();
  assert.equal(await fs.readFile(target, 'utf8'), '{"kept": true}');
  await fs.mkdir(target + '.d');
  await assert.rejects(readJsonFile(target + '.d'), { code: 'EISDIR' });
});

test('a settings file is written whole, or left as it was', async (t) => {
  const target = await files(t);
  await writeFileSafely(target, '{"new": true}');
  assert.equal(await fs.readFile(target, 'utf8'), '{"new": true}');
  await assert.rejects(fs.access(target + '.tmp'));
  if (process.platform !== 'win32') assert.equal((await fs.stat(target)).mode & 0o777, 0o600);
  t.mock.method(fs, 'rename', () => Promise.reject(refusal('ENOENT')));
  await assert.rejects(writeFileSafely(target, '{"newer": true}'), { code: 'ENOENT' });
  assert.equal(await fs.readFile(target, 'utf8'), '{"new": true}');
});
