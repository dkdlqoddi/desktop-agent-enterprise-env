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
  for (const name of ['EXA_API_KEY', 'TELEGRAM_BOT_TOKEN', 'GITHUB_TOKEN', 'SLACK_WEBHOOK_URL'])
    assert.equal(refusedName(name), '');
});

test('names that would change who a bot answers or make the gateway listen are refused', () => {
  // The accounts listed per messenger are the only way in to the agent: no key may let everyone in, whole chats or
  // roles, or other bots, nor start Hermes' API server or a webhook listener.
  for (const name of [
    'GATEWAY_ALLOW_ALL_USERS',
    'GATEWAY_ALLOWED_USERS',
    'TELEGRAM_ALLOW_ALL_USERS',
    'TELEGRAM_ALLOWED_USERS',
    'TELEGRAM_GROUP_ALLOWED_USERS',
    'TELEGRAM_GROUP_ALLOWED_CHATS',
    'DISCORD_ALLOWED_ROLES',
    'DISCORD_ALLOWED_CHANNELS',
    'DISCORD_ALLOW_BOTS',
    'SLACK_ALLOWED_USERS',
    'SLACK_HOME_CHANNEL',
    'TELEGRAM_HOME_CHANNEL',
    'TELEGRAM_WEBHOOK_URL',
    'API_SERVER_KEY',
    'API_SERVER_ENABLED',
    'WEBHOOK_ENABLED',
  ])
    assert.match(refusedName(name), /허용 계정/, name);
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

test("the chat is run with the folder, the tools and the service keys; the bot tokens are the gateway's", async (t) => {
  const { directory, make } = await setup(t);
  const settings = make();
  await settings.load();
  // Nothing set: the agent's own folder, computer use, no MCP server, pages read without a service, no gateway.
  assert.deepEqual(settings.agent(), {
    workspace: '',
    computer_use: true,
    mcp_servers: {},
    search_backend: 'ddgs',
    extract_backend: 'ddgs',
    env: {},
    gateway: { enabled: false, users: {}, env: {} },
  });
  // The messenger gateway's bot tokens are its process's alone, apart from the keys of the chat's worker, and they
  // are sent only while the gateway is on.
  await settings.update({
    workspace: directory,
    computer_use: false,
    users: { telegram: '123', slack: 'U1, U2' },
    mcp: JSON.stringify({ files: { command: 'npx' } }),
    keys: {
      FIRECRAWL_API_KEY: 'fc-secret',
      MY_SKILL_TOKEN: 'skill-secret',
      TELEGRAM_BOT_TOKEN: 'tg-secret',
      DISCORD_BOT_TOKEN: 'dc-secret',
      SLACK_BOT_TOKEN: 'xoxb-secret',
      SLACK_APP_TOKEN: 'xapp-secret',
    },
  });
  const chat = {
    workspace: directory,
    computer_use: false,
    mcp_servers: { files: { command: 'npx' } },
    search_backend: 'firecrawl',
    extract_backend: 'firecrawl',
    env: { FIRECRAWL_API_KEY: 'fc-secret', MY_SKILL_TOKEN: 'skill-secret' },
  };
  const off = settings.agent();
  assert.deepEqual(off, { ...chat, gateway: { enabled: false, users: {}, env: {} } });
  assert.ok(!/tg-secret|dc-secret|xoxb-secret|xapp-secret/.test(JSON.stringify(off)));
  await settings.update({ gateway: true });
  assert.deepEqual(settings.agent(), {
    ...chat,
    gateway: {
      enabled: true,
      users: { telegram: '123', discord: '', slack: 'U1,U2' },
      env: {
        TELEGRAM_BOT_TOKEN: 'tg-secret',
        DISCORD_BOT_TOKEN: 'dc-secret',
        SLACK_BOT_TOKEN: 'xoxb-secret',
        SLACK_APP_TOKEN: 'xapp-secret',
      },
    },
  });
  // The screen learns only that a token is set.
  const shown = settings.get().keys.find((key) => key.name === 'TELEGRAM_BOT_TOKEN');
  assert.deepEqual(shown, {
    name: 'TELEGRAM_BOT_TOKEN',
    label: '텔레그램 봇 토큰',
    group: 'gateway',
    has_value: true,
  });
  assert.ok(!/tg-secret|dc-secret|xoxb-secret|xapp-secret/.test(JSON.stringify(settings.get())));
});

test('a web service key names the service that searches and the one that reads pages', async (t) => {
  // Hermes takes its web services from its settings, not from the keys it finds: a key stored here does nothing until
  // its service is named. Without a key the search is the one that needs none, and no service reads pages.
  const cases = [
    [{}, 'ddgs', 'ddgs'],
    [{ EXA_API_KEY: 'k' }, 'exa', 'exa'],
    [{ TAVILY_API_KEY: 'k' }, 'tavily', 'tavily'],
    [{ PARALLEL_API_KEY: 'k' }, 'parallel', 'parallel'],
    [{ FIRECRAWL_API_KEY: 'k' }, 'firecrawl', 'firecrawl'],
    // Brave only searches: pages stay unread by a service.
    [{ BRAVE_SEARCH_API_KEY: 'k' }, 'brave-free', 'ddgs'],
    // Several keys: one service each, in a fixed order.
    [{ BRAVE_SEARCH_API_KEY: 'k', EXA_API_KEY: 'k' }, 'exa', 'exa'],
    [{ EXA_API_KEY: 'k', FIRECRAWL_API_KEY: 'k' }, 'exa', 'firecrawl'],
    [{ TAVILY_API_KEY: 'k', EXA_API_KEY: 'k', FIRECRAWL_API_KEY: 'k' }, 'tavily', 'firecrawl'],
    // A key of another kind names no web service.
    [{ FAL_KEY: 'k', MY_SKILL_TOKEN: 'k' }, 'ddgs', 'ddgs'],
  ];
  for (const [keys, search, extract] of cases) {
    const { make } = await setup(t);
    const settings = make();
    await settings.load();
    if (Object.keys(keys).length) await settings.update({ keys });
    const sent = settings.agent();
    assert.deepEqual(
      [sent.search_backend, sent.extract_backend],
      [search, extract],
      JSON.stringify(keys),
    );
  }
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
