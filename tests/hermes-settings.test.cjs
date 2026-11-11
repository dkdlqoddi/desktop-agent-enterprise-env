const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { HermesSettings, refusedName, mcpServers } = require('../electron/hermes-settings.cjs');

// Stand-ins for Electron's safeStorage: "encryption" that is recognizable but not the plain text.
const encrypt = (value) => Buffer.from(`enc:${[...value].reverse().join('')}`);
const decrypt = (buffer) => {
  const text = buffer.toString();
  if (!text.startsWith('enc:')) throw new Error('bad key');
  return [...text.slice(4)].reverse().join('');
};

async function setup(t, canEncrypt = true) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'private-agent-hermes-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const file = path.join(directory, 'hermes.json');
  const make = () => new HermesSettings({ file, encrypt, decrypt, canEncrypt: () => canEncrypt });
  return { directory, file, make };
}

test('keys are kept encrypted and the screen only learns which are set', async (t) => {
  const { file, make } = await setup(t);
  const settings = make();
  await settings.load();
  const shown = await settings.update({
    keys: { EXA_API_KEY: ' exa-secret ', MY_SKILL_TOKEN: 'skill-secret' },
  });
  assert.deepEqual(
    shown.keys.filter((key) => key.has_value).map((key) => key.name),
    ['EXA_API_KEY', 'MY_SKILL_TOKEN'],
  );
  assert.equal(shown.keys.find((key) => key.name === 'MY_SKILL_TOKEN').group, 'other');
  assert.ok(!JSON.stringify(shown).includes('secret'));
  const written = await fs.readFile(file, 'utf8');
  assert.ok(!written.includes('exa-secret') && !written.includes('skill-secret'));
  // The backend gets the values themselves, trimmed; a new start reads them back.
  assert.equal(settings.backend().env.EXA_API_KEY, 'exa-secret');
  const again = make();
  await again.load();
  assert.deepEqual(again.backend().env, {
    EXA_API_KEY: 'exa-secret',
    MY_SKILL_TOKEN: 'skill-secret',
  });
  // An empty value clears a key; one not sent stays.
  await again.update({ keys: { MY_SKILL_TOKEN: '' } });
  assert.deepEqual(again.backend().env, { EXA_API_KEY: 'exa-secret' });
});

test('names that steer the runtime or pick a model provider are refused', () => {
  for (const name of [
    'PATH',
    'HOME',
    'PYTHONPATH',
    'HERMES_HOME',
    'OPENAI_API_KEY',
    'ANTHROPIC_API_KEY',
    'OPENROUTER_API_KEY',
  ])
    assert.notEqual(refusedName(name), '', name);
  assert.match(refusedName('OPENAI_API_KEY'), /LLM 연결/);
  assert.match(refusedName('exa_key'), /대문자/);
  for (const name of ['EXA_API_KEY', 'TELEGRAM_BOT_TOKEN', 'GITHUB_TOKEN'])
    assert.equal(refusedName(name), '');
});

test('refused changes leave the saved settings as they were', async (t) => {
  const { directory, make } = await setup(t);
  const settings = make();
  await settings.load();
  await settings.update({ keys: { EXA_API_KEY: 'kept' }, workspace: directory });
  await assert.rejects(settings.update({ keys: { OPENAI_API_KEY: 'x' } }), /LLM 연결/);
  await assert.rejects(settings.update({ workspace: 'relative/folder' }), /전체 경로/);
  await assert.rejects(
    settings.update({ workspace: path.join(directory, 'missing') }),
    /찾을 수 없습니다/,
  );
  await assert.rejects(settings.update({ gateway: 'yes' }), /켜기/);
  await assert.rejects(settings.update({ users: { telegram: 'bad id!' } }), /계정 ID/);
  await assert.rejects(settings.update({ mcp: '{"x": {}}' }), /command나 연결할 url/);
  assert.deepEqual(settings.backend().env, { EXA_API_KEY: 'kept' });
  assert.equal(settings.backend().workspace, directory);
});

test('gateway accounts, MCP servers and computer use reach the backend as Hermes takes them', async (t) => {
  const { make } = await setup(t);
  const settings = make();
  const loaded = await settings.load();
  // Computer use is on, the gateway off until accounts and a token are set.
  assert.equal(loaded.computer_use, true);
  assert.equal(loaded.gateway, false);
  const mcp = JSON.stringify({
    files: { command: 'npx', args: ['-y', '@example/files'], env: { TOKEN: 'mcp-secret' } },
  });
  const shown = await settings.update({
    gateway: true,
    users: { telegram: '123, 456 123', slack: 'U01ABC' },
    mcp,
    computer_use: false,
  });
  assert.deepEqual(shown.users, { telegram: '123,456', discord: '', slack: 'U01ABC' });
  assert.deepEqual(shown.mcp_servers, ['files']);
  assert.equal(shown.has_mcp, true);
  assert.ok(!JSON.stringify(shown).includes('mcp-secret'));
  const sent = settings.backend();
  assert.equal(sent.gateway, true);
  assert.equal(sent.computer_use, false);
  assert.equal(sent.mcp_servers.files.env.TOKEN, 'mcp-secret');
  assert.deepEqual(mcpServers(''), {});
  assert.throws(() => mcpServers('[1]'), /이름별 객체/);
  assert.throws(() => mcpServers('{"x": {"url": "ftp://example.test"}}'), /command나 연결할 url/);
});

test('without secure storage values last for this run only', async (t) => {
  const { file, make } = await setup(t, false);
  const settings = make();
  await settings.load();
  const shown = await settings.update({ keys: { EXA_API_KEY: 'only-in-memory' } });
  assert.equal(shown.secure_storage, false);
  assert.equal(shown.stored, false);
  assert.ok(!(await fs.readFile(file, 'utf8')).includes('only-in-memory'));
  assert.equal(settings.backend().env.EXA_API_KEY, 'only-in-memory');
  const next = make();
  await next.load();
  assert.deepEqual(next.backend().env, {});
});

test('a damaged file gives the defaults, and one that cannot be read is never written over', async (t) => {
  const { directory, file, make } = await setup(t);
  await fs.writeFile(file, '{ not json');
  const damaged = await make().load();
  assert.equal(damaged.error, '');
  assert.equal(damaged.gateway, false);
  // The damaged file is set aside beside it, as every settings file of the app is.
  const aside = (await fs.readdir(directory)).find((name) => name !== 'hermes.json');
  assert.equal(await fs.readFile(path.join(directory, aside), 'utf8'), '{ not json');

  await fs.rm(file, { force: true });
  await fs.mkdir(file);
  const unreadable = make();
  assert.match((await unreadable.load()).error, /hermes\.json/);
  await assert.rejects(unreadable.update({ gateway: true }), /hermes\.json/);
  assert.ok((await fs.stat(file)).isDirectory());
});
