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
  // Nothing set: the agent's own folder, computer use, no MCP server, pages read without a service, no gateway;
  // nothing runs unasked, and the places credentials are kept are protected.
  const guard = {
    allowed: [],
    protected: { app: [], credentials: true, folders: [] },
    blocked: [],
    turn_limits: { request_seconds: 120, iterations: 24, turn_minutes: 0, answer_minutes: 5 },
  };
  assert.deepEqual(settings.agent(), {
    workspace: '',
    computer_use: true,
    mcp_servers: {},
    search_backend: 'ddgs',
    extract_backend: 'ddgs',
    ...guard,
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
    ...guard,
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

test('a command is allowed for good only by the answer to a question, and taken back on the screen', async (t) => {
  const { file, make } = await setup(t);
  const settings = make();
  await settings.load();
  assert.deepEqual(settings.get().allowed, []);
  assert.equal(await settings.allow('  git push --force origin main  '), true);
  assert.equal(await settings.allow('git push --force origin main'), true);
  // Not a single line of text: nothing is kept.
  assert.equal(await settings.allow('rm -rf a\nrm -rf b'), false);
  assert.equal(await settings.allow(''), false);
  assert.equal(await settings.allow(7), false);
  const shown = settings.get().allowed;
  assert.deepEqual(
    shown.map((entry) => entry.command),
    ['git push --force origin main'],
  );
  assert.match(shown[0].added_at, /^\d{4}-\d{2}-\d{2}T/);
  // The backend is handed the commands; the screens cannot add one through the settings they save.
  assert.deepEqual(settings.agent().allowed, ['git push --force origin main']);
  await settings.update({ allowed: [{ command: 'rm -rf /' }], computer_use: false });
  assert.deepEqual(settings.agent().allowed, ['git push --force origin main']);
  // A new start reads the list back, and a command is taken back by its text.
  const again = make();
  await again.load();
  assert.deepEqual(again.agent().allowed, ['git push --force origin main']);
  await assert.rejects(again.update({ disallow: 'all' }), /허용을 취소할 명령/);
  await again.update({ disallow: ['git push --force origin main', 'never allowed'] });
  assert.deepEqual(again.get().allowed, []);
  assert.deepEqual(JSON.parse(await fs.readFile(file, 'utf8')).allowed, []);
});

test('the list of allowed commands is read strictly from the file', async (t) => {
  const { file, make } = await setup(t);
  await fs.writeFile(
    file,
    JSON.stringify({
      allowed: [
        { command: 'npm publish', added_at: '2026-01-02T03:04:05.000Z' },
        { command: 'npm publish' },
        { command: 'two\nlines' },
        { command: 'x'.repeat(1001) },
        'npm unpublish',
        null,
      ],
    }),
  );
  const settings = make();
  await settings.load();
  assert.deepEqual(settings.get().allowed, [
    { command: 'npm publish', added_at: '2026-01-02T03:04:05.000Z' },
  ]);
});

test('protected folders and refused commands are saved, and what the agent works in stays open', async (t) => {
  const { directory, file } = await setup(t);
  const wiki = path.join(directory, 'wiki');
  const work = path.join(directory, 'projects', 'site');
  await fs.mkdir(wiki);
  await fs.mkdir(work, { recursive: true });
  const make = () =>
    new HermesSettings({
      file,
      encrypt,
      decrypt,
      canEncrypt: () => true,
      zones: () => [path.join(directory, 'app-settings')],
      reachable: () => [wiki],
    });
  const settings = make();
  await settings.load();
  // The places credentials are kept are protected until the user turns that off; the app's own folder always is.
  assert.deepEqual(settings.get().protected, { credentials: true, folders: [] });
  assert.deepEqual(settings.agent().protected, {
    app: [path.join(directory, 'app-settings')],
    credentials: true,
    folders: [],
  });
  const vault = path.join(directory, 'vault');
  const shown = await settings.update({
    protected: { credentials: false, folders: [vault, ` ${vault} `] },
    blocked: [' git push --force* ', '', '*shutdown*', 'git push --force*'],
  });
  assert.deepEqual(shown.protected, { credentials: false, folders: [vault] });
  assert.deepEqual(shown.blocked, ['git push --force*', '*shutdown*']);
  assert.deepEqual(settings.agent().protected.folders, [vault]);
  assert.deepEqual(settings.agent().blocked, ['git push --force*', '*shutdown*']);

  // Not a full path, the top of a drive, or a pattern that would refuse every command: refused, nothing changes.
  for (const refused of [
    { protected: { folders: ['relative/folder'] } },
    { protected: { folders: [path.parse(directory).root] } },
    { protected: { folders: 'vault' } },
    { protected: { credentials: 'no' } },
    { protected: [] },
    { blocked: ['*'] },
    { blocked: ['* ?'] },
    { blocked: 'rm' },
    { blocked: ['x'.repeat(201)] },
  ])
    await assert.rejects(settings.update(refused), (error) => error.name === 'Error');
  assert.deepEqual(settings.get().protected, { credentials: false, folders: [vault] });

  // A folder holding the Wiki folder or the agent's work folder cannot be protected, nor a work folder be chosen
  // inside a protected one.
  await assert.rejects(
    settings.update({ protected: { folders: [directory] } }),
    /함께 쓸 수 없습니다/,
  );
  await assert.rejects(settings.update({ protected: { folders: [wiki] } }), /함께 쓸 수 없습니다/);
  await settings.update({ workspace: work });
  await assert.rejects(
    settings.update({ protected: { folders: [path.join(directory, 'projects')] } }),
    /함께 쓸 수 없습니다/,
  );
  await settings.update({ workspace: '' });
  await settings.update({ protected: { folders: [path.join(directory, 'projects')] } });
  await assert.rejects(settings.update({ workspace: work }), /함께 쓸 수 없습니다/);

  // A new start reads it all back.
  const again = make();
  await again.load();
  assert.deepEqual(again.get().protected, {
    credentials: false,
    folders: [path.join(directory, 'projects')],
  });
  assert.deepEqual(again.get().blocked, ['git push --force*', '*shutdown*']);
});

test("a chat turn's limits start as they were before, are saved within their bounds and reach the backend", async (t) => {
  const { file, make } = await setup(t);
  const settings = make();
  await settings.load();
  const before = { request_seconds: 120, iterations: 24, turn_minutes: 0, answer_minutes: 5 };
  assert.deepEqual(settings.get().turn_limits, before);
  assert.deepEqual(settings.agent().turn_limits, before);

  // What was sent changes; what was not stays.
  const shown = await settings.update({ turn_limits: { iterations: 60, turn_minutes: 30 } });
  const changed = { ...before, iterations: 60, turn_minutes: 30 };
  assert.deepEqual(shown.turn_limits, changed);
  await settings.update({ turn_limits: { request_seconds: 300, answer_minutes: 30 } });
  const most = { ...changed, request_seconds: 300, answer_minutes: 30 };
  assert.deepEqual(settings.agent().turn_limits, most);
  // Back to automatic for the whole turn.
  await settings.update({ turn_limits: { turn_minutes: 0 } });
  assert.equal(settings.get().turn_limits.turn_minutes, 0);

  // Out of bounds, not a whole number, or not a set of limits at all: refused with what is allowed, nothing changes.
  for (const [refused, words] of [
    [{ request_seconds: 29 }, /30~300초/],
    [{ request_seconds: 301 }, /30~300초/],
    [{ iterations: 3 }, /4~200번/],
    [{ iterations: 24.5 }, /4~200번/],
    [{ turn_minutes: 1 }, /2~60분/],
    [{ turn_minutes: 61 }, /2~60분/],
    [{ answer_minutes: 0 }, /1~30분/],
    [{ answer_minutes: '5' }, /1~30분/],
  ])
    await assert.rejects(settings.update({ turn_limits: refused }), words);
  for (const refused of [[], 'fast', null])
    await assert.rejects(settings.update({ turn_limits: refused }), /요청 한도/);
  assert.deepEqual(settings.get().turn_limits, { ...most, turn_minutes: 0 });

  // A new start reads them back; a value the file holds out of bounds goes back to its default.
  const again = make();
  await again.load();
  assert.deepEqual(again.get().turn_limits, { ...most, turn_minutes: 0 });
  const record = JSON.parse(await fs.readFile(file, 'utf8'));
  record.turn_limits = { request_seconds: 5000, iterations: 'many', turn_minutes: 45 };
  await fs.writeFile(file, JSON.stringify(record));
  const damaged = make();
  await damaged.load();
  assert.deepEqual(damaged.get().turn_limits, { ...before, turn_minutes: 45 });
});
