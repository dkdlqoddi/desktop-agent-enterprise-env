const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { ApiSettings } = require('../electron/api-settings.cjs');

// Stand-ins for Electron's safeStorage: "encryption" that is recognizable but not the plain token.
const encrypt = (value) => Buffer.from(`enc:${[...value].reverse().join('')}`);
const decrypt = (buffer) => {
  const text = buffer.toString();
  if (!text.startsWith('enc:')) throw new Error('bad key');
  return [...text.slice(4)].reverse().join('');
};

async function setup(t, canEncrypt = true) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'private-agent-api-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const file = path.join(directory, 'api.json');
  const make = () => new ApiSettings({ file, encrypt, decrypt, canEncrypt: () => canEncrypt });
  return { file, make };
}

test('starts off with the default port and access, and keeps one encrypted token', async (t) => {
  const { file, make } = await setup(t);
  const first = await make().load();
  assert.equal(first.enabled, false);
  assert.equal(first.port, 47615);
  // AI tools may only read until the user allows more.
  assert.equal(first.access, 'read');
  assert.equal(first.error, '');
  assert.match(first.token, /^pa_[A-Za-z0-9_-]{43}$/);
  assert.equal(first.token_stored, true);
  const record = JSON.parse(await fs.readFile(file, 'utf8'));
  assert.equal(record.token, undefined);
  assert.ok(!JSON.stringify(record).includes(first.token));
  assert.equal(decrypt(Buffer.from(record.encrypted_token, 'base64')), first.token);
  assert.equal((await make().load()).token, first.token);
});

test('saves only valid changes', async (t) => {
  const { make } = await setup(t);
  const settings = make();
  await settings.load();
  const changed = await settings.update({ enabled: true, port: 50123, access: 'read' });
  assert.deepEqual([changed.enabled, changed.port, changed.access], [true, 50123, 'read']);
  await assert.rejects(settings.update({ port: 80 }), /1024~65535/);
  await assert.rejects(settings.update({ port: '50000' }), /1024~65535/);
  await assert.rejects(settings.update({ access: 'root' }), /허용 범위/);
  await assert.rejects(settings.update({ enabled: 'yes' }), /켜기/);
  const again = await make().load();
  assert.deepEqual([again.enabled, again.port, again.access], [true, 50123, 'read']);
});

test('a new token replaces the old one', async (t) => {
  const { make } = await setup(t);
  const settings = make();
  const { token } = await settings.load();
  const rotated = await settings.rotate();
  assert.notEqual(rotated.token, token);
  assert.equal((await make().load()).token, rotated.token);
});

test('without the OS secure storage, the token lasts until the app closes', async (t) => {
  const { file, make } = await setup(t, false);
  const first = await make().load();
  assert.equal(first.token_stored, false);
  const record = JSON.parse(await fs.readFile(file, 'utf8'));
  assert.equal(record.encrypted_token, undefined);
  assert.notEqual((await make().load()).token, first.token);
});

test('a token the OS can no longer decrypt, or a damaged file, gives defaults and a new token', async (t) => {
  const { file, make } = await setup(t);
  await fs.writeFile(
    file,
    JSON.stringify({
      enabled: true,
      port: 3,
      access: 'everything',
      encrypted_token: Buffer.from('other').toString('base64'),
    }),
  );
  const loaded = await make().load();
  assert.equal(loaded.enabled, true);
  assert.equal(loaded.port, 47615);
  assert.equal(loaded.access, 'read');
  assert.match(loaded.token, /^pa_/);
  // Cut short by a power loss: set aside for recovery, then the defaults and a new token.
  await fs.writeFile(file, '{not json');
  const damaged = await make().load();
  assert.equal(damaged.enabled, false);
  assert.equal(damaged.error, '');
  const directory = path.dirname(file);
  const [aside] = (await fs.readdir(directory)).filter((name) =>
    name.startsWith('api.json.corrupt-'),
  );
  assert.equal(await fs.readFile(path.join(directory, aside), 'utf8'), '{not json');
  assert.equal((await make().load()).token, damaged.token);
});

test('earlier installs that never turned the server on get the read-only default', async (t) => {
  const { file, make } = await setup(t);
  const record = (values) =>
    fs.writeFile(
      file,
      JSON.stringify({
        port: 47615,
        encrypted_token: encrypt(`pa_${'a'.repeat(43)}`).toString('base64'),
        ...values,
      }),
    );
  // Version 1 stored the old default at the first start, chosen or not.
  await record({ version: 1, enabled: false, access: 'write' });
  assert.equal((await make().load()).access, 'read');
  // A level in use, or saved by this version, stays.
  await record({ version: 1, enabled: true, access: 'write' });
  assert.equal((await make().load()).access, 'write');
  await record({ version: 2, enabled: false, access: 'full' });
  assert.equal((await make().load()).access, 'full');
  const settings = make();
  await settings.load();
  await settings.update({ access: 'write' });
  assert.equal(JSON.parse(await fs.readFile(file, 'utf8')).version, 2);
  assert.equal((await make().load()).access, 'write');
});

test('a file that cannot be read keeps the server off and is never written over', async (t) => {
  const { file, make } = await setup(t);
  // Not readable at all (here a folder in its place).
  await fs.mkdir(file);
  const settings = make();
  const loaded = await settings.load();
  assert.equal(loaded.enabled, false);
  assert.match(loaded.error, /api\.json/);
  assert.match(loaded.token, /^pa_/);
  await assert.rejects(settings.update({ enabled: true }), /api\.json/);
  await assert.rejects(settings.rotate(), /api\.json/);
  assert.equal(settings.get().enabled, false);
  assert.ok((await fs.stat(file)).isDirectory());
});

test('a new token that cannot be saved is reported, and still replaces the old one now', async (t) => {
  const { make } = await setup(t);
  const settings = make();
  const { token } = await settings.load();
  t.mock.method(fs, 'open', () =>
    Promise.reject(Object.assign(new Error('EACCES: open'), { code: 'EACCES' })),
  );
  await assert.rejects(settings.rotate(), /새 토큰을 저장하지 못했습니다/);
  t.mock.restoreAll();
  assert.notEqual(settings.get().token, token);
  assert.equal(settings.get().token_stored, false);
  // The file still holds the old token: the user was told to issue a new one again.
  assert.equal((await make().load()).token, token);
});
