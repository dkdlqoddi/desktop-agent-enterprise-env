const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { StorageManager } = require('../electron/storage.cjs');

async function fixture(t) {
  // The app keeps the folder's real path; a temp folder may be given in 8.3 short form
  // (C:\Users\RUNNER~1\… on GitHub's Windows runners).
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'agent-storage-test-')));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const config = path.join(root, 'config');
  await fs.mkdir(config);
  const starts = [];
  const options = {
    configDirectory: config,
    suggestedPath: path.join(root, 'wiki'),
    start: async (directory) => {
      starts.push(directory);
      await fs.writeFile(path.join(directory, 'wiki.db'), 'db', { flag: 'a' });
    },
    stop: async () => {},
    migrate: (a, b) => fs.copyFile(a, b, fs.constants.COPYFILE_EXCL),
  };
  return { root, config, starts, options, manager: new StorageManager(options) };
}

const refusal = (code) => Object.assign(new Error(`${code}: open`), { code });
const corrupt = async (config) =>
  (await fs.readdir(config)).filter((name) => name.startsWith('storage.json.corrupt-'));

test('first run creates no DB until selection and persists the selected directory', async (t) => {
  const { manager, config, starts, options } = await fixture(t);
  assert.equal((await manager.load()).initialized, false);
  await assert.rejects(fs.access(path.join(config, 'wiki.db')));
  const info = await manager.initialize(options.suggestedPath);
  assert.equal(info.initialized, true);
  assert.equal(starts[0], options.suggestedPath);
  const file = path.join(config, 'storage.json');
  assert.equal(JSON.parse(await fs.readFile(file)).directory, options.suggestedPath);
  // Written whole, through a file of its own that does not stay behind.
  assert.deepEqual(await fs.readdir(config), ['storage.json']);
  if (process.platform !== 'win32') assert.equal((await fs.stat(file)).mode & 0o777, 0o600);
  const next = new StorageManager(options);
  assert.equal((await next.load()).initialized, true);
  assert.equal(next.info().directory, options.suggestedPath);
});

test('a damaged storage.json offers the folder it still names, and is set aside only when replaced', async (t) => {
  const { manager, config, root, starts } = await fixture(t);
  const old = path.join(root, 'old-wiki');
  await fs.mkdir(old);
  await fs.writeFile(path.join(old, 'wiki.db'), 'old');
  // Cut short after the path, as a power loss may leave it.
  const text = JSON.stringify({ version: 1, directory: old }, null, 2);
  const damaged = text.slice(0, text.lastIndexOf('}'));
  await fs.writeFile(path.join(config, 'storage.json'), damaged);
  const info = await manager.load();
  assert.equal(info.initialized, false);
  assert.equal(info.suggested_path, old);
  assert.match(info.error, /storage\.json\)이 손상되었습니다\. .*이전에 쓰던 Wiki 폴더를 찾아/);
  assert.match(info.error, /storage\.json\.corrupt-<시각>/);
  assert.equal(starts.length, 0);
  // Nothing is moved or written until the user chooses.
  assert.equal(await fs.readFile(path.join(config, 'storage.json'), 'utf8'), damaged);
  assert.deepEqual(await corrupt(config), []);
  await manager.initialize(old);
  assert.deepEqual(starts, [old]);
  assert.equal(await fs.readFile(path.join(old, 'wiki.db'), 'utf8'), 'olddb');
  assert.equal(
    JSON.parse(await fs.readFile(path.join(config, 'storage.json'), 'utf8')).directory,
    old,
  );
  const aside = await corrupt(config);
  assert.equal(aside.length, 1);
  assert.equal(await fs.readFile(path.join(config, aside[0]), 'utf8'), damaged);
});

test('a storage.json cut inside its path offers no folder unasked, or the Wiki in the default folder', async (t) => {
  const { options, config, root, starts } = await fixture(t);
  const text = JSON.stringify({ version: 1, directory: path.join(root, 'old-wiki') }, null, 2);
  await fs.writeFile(
    path.join(config, 'storage.json'),
    text.slice(0, text.indexOf('old-wiki') + 3),
  );
  let info = await new StorageManager(options).load();
  assert.equal(info.suggested_path, '');
  assert.match(info.error, /알 수 없습니다\(남은 경로의 앞부분: .*old…\)/);
  // A folder that holds no Wiki, as a hand edit may leave: offered, and said to hold none.
  await fs.writeFile(
    path.join(config, 'storage.json'),
    JSON.stringify({ directory: path.join(root, 'empty') }).slice(0, -1),
  );
  info = await new StorageManager(options).load();
  assert.equal(info.suggested_path, path.join(root, 'empty'));
  assert.match(info.error, /Wiki\(wiki\.db\)를 찾지 못했습니다/);
  // No path at all, and a Wiki in the suggested folder: that one, said to be the default.
  await fs.writeFile(path.join(config, 'storage.json'), '\0'.repeat(64));
  await fs.mkdir(options.suggestedPath);
  await fs.writeFile(path.join(options.suggestedPath, 'wiki.db'), 'db');
  info = await new StorageManager(options).load();
  assert.equal(info.suggested_path, options.suggestedPath);
  assert.match(info.error, /기본 저장 위치에 있는 Wiki/);
  for (const invalid of [{ directory: 'relative' }, ['/wiki'], 'text']) {
    await fs.writeFile(path.join(config, 'storage.json'), JSON.stringify(invalid));
    assert.match((await new StorageManager(options).load()).error, /손상되었습니다/);
  }
  assert.equal(starts.length, 0);
});

test('a hand-edited storage.json saved with a byte order mark is read as it is', async (t) => {
  const { options, config, root, starts } = await fixture(t);
  const wiki = path.join(root, 'moved');
  await fs.mkdir(wiki);
  await fs.writeFile(path.join(wiki, 'wiki.db'), 'db');
  await fs.writeFile(
    path.join(config, 'storage.json'),
    '\uFEFF' + JSON.stringify({ version: 1, directory: wiki }),
  );
  assert.equal((await new StorageManager(options).load()).initialized, true);
  assert.deepEqual(starts, [wiki]);
});

test('a storage.json held for a moment is read after it, and one held for good is never replaced', async (t) => {
  const { options, config, root, starts } = await fixture(t);
  const wiki = path.join(root, 'held');
  await fs.mkdir(wiki);
  await fs.writeFile(path.join(wiki, 'wiki.db'), 'db');
  const saved = JSON.stringify({ version: 1, directory: wiki });
  await fs.writeFile(path.join(config, 'storage.json'), saved);
  const readFile = fs.readFile;
  let calls = 0;
  t.mock.method(fs, 'readFile', (...args) =>
    ++calls < 3 ? Promise.reject(refusal('EBUSY')) : readFile(...args),
  );
  assert.equal((await new StorageManager(options).load()).initialized, true);
  assert.deepEqual(starts, [wiki]);
  t.mock.restoreAll();
  t.mock.method(fs, 'readFile', () => Promise.reject(refusal('EBUSY')));
  const held = new StorageManager(options);
  const info = await held.load();
  t.mock.restoreAll();
  assert.equal(info.initialized, false);
  assert.match(info.error, /storage\.json\)을 읽지 못했습니다\(EBUSY\)/);
  const other = path.join(root, 'other');
  await assert.rejects(held.initialize(other), /이번 실행에서는 저장 위치를 고를 수 없습니다/);
  assert.equal(await fs.readFile(path.join(config, 'storage.json'), 'utf8'), saved);
  await assert.rejects(fs.access(other));
  assert.deepEqual(starts, [wiki]);
});

test('a saved folder that does not answer in time is reported without waiting for it', async (t) => {
  const { options, config, root, starts } = await fixture(t);
  const share = path.join(root, 'disconnected-share');
  await fs.writeFile(path.join(config, 'storage.json'), JSON.stringify({ directory: share }));
  const stat = fs.stat;
  t.mock.method(fs, 'stat', (file, ...rest) =>
    String(file).startsWith(share) ? new Promise(() => {}) : stat(file, ...rest),
  );
  const info = await new StorageManager({ ...options, checkLimit: 50 }).load();
  assert.equal(info.initialized, false);
  assert.equal(info.suggested_path, share);
  assert.match(info.error, /저장된 Wiki 폴더가 0초 안에 응답하지 않습니다/);
  assert.equal(starts.length, 0);
});

test('legacy data is copied without removing source; existing target is never overwritten', async (t) => {
  const { manager, config, options, root } = await fixture(t);
  await fs.writeFile(path.join(config, 'wiki.db'), 'legacy');
  await manager.load();
  assert.equal(manager.info().has_legacy_data, true);
  const occupied = path.join(root, 'occupied');
  await fs.mkdir(occupied);
  await fs.writeFile(path.join(occupied, 'wiki.db'), 'other');
  await assert.rejects(manager.initialize(occupied), /이미/);
  assert.equal(await fs.readFile(path.join(occupied, 'wiki.db'), 'utf8'), 'other');
  await manager.initialize(options.suggestedPath);
  assert.equal(await fs.readFile(path.join(config, 'wiki.db'), 'utf8'), 'legacy');
  assert.equal(await fs.readFile(path.join(options.suggestedPath, 'wiki.db'), 'utf8'), 'legacydb');
});

test('missing configured volume returns recovery UI and never silently creates an empty wiki', async (t) => {
  const { manager, config, root, starts } = await fixture(t);
  const missing = path.join(root, 'disconnected-drive');
  await fs.writeFile(path.join(config, 'storage.json'), JSON.stringify({ directory: missing }));
  const info = await manager.load();
  assert.equal(info.initialized, false);
  assert.match(info.error, /찾을 수 없습니다/);
  assert.equal(starts.length, 0);
  await assert.rejects(fs.access(missing));
});

test('failed initialization never persists configuration and relative paths are rejected', async (t) => {
  const { options, config } = await fixture(t);
  const manager = new StorageManager({
    ...options,
    start: async () => {
      throw new Error('backend unavailable');
    },
  });
  await manager.load();
  await assert.rejects(manager.initialize('relative/wiki'), /전체 경로/);
  await assert.rejects(manager.initialize(options.suggestedPath), /backend unavailable/);
  assert.equal(manager.info().initialized, false);
  await assert.rejects(fs.access(path.join(config, 'storage.json')));
});

test('a demo opens its own Wiki for one run: storage.json is neither read nor written, no legacy copy', async (t) => {
  const { options, config, starts, root } = await fixture(t);
  const usual = path.join(root, 'usual');
  await fs.writeFile(path.join(config, 'storage.json'), JSON.stringify({ directory: usual }));
  await fs.writeFile(path.join(config, 'wiki.db'), 'legacy');
  const demo = path.join(root, 'demo-wiki');
  const manager = new StorageManager({ ...options, suggestedPath: demo, remember: false });
  const info = await manager.load();
  assert.equal(info.initialized, false);
  assert.equal(info.has_legacy_data, false);
  assert.equal(info.suggested_path, demo);
  assert.equal(info.error, '');
  assert.equal(starts.length, 0);
  await manager.initialize(demo);
  assert.deepEqual(starts, [demo]);
  assert.equal(manager.info().directory, demo);
  assert.equal(
    JSON.parse(await fs.readFile(path.join(config, 'storage.json'), 'utf8')).directory,
    usual,
  );
  assert.equal(await fs.readFile(path.join(demo, 'wiki.db'), 'utf8'), 'db');
});
