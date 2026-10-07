const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { SiteStore, normalizeUrl } = require('../electron/sites.cjs');
const METHODS = require('../electron/login-methods.json').methods;

async function fixture(t, secure = true) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-sites-test-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const options = {
    file: path.join(root, 'sites.json'),
    legacyFiles: {
      confluence: path.join(root, 'confluence-login.json'),
      jira: path.join(root, 'jira-login.json'),
    },
    // Stand-in for safeStorage: reversible, but never the plain text.
    encrypt: (value) => Buffer.from('enc:' + [...value].reverse().join('')),
    decrypt: (value) => {
      const text = value.toString();
      if (!text.startsWith('enc:')) throw new Error('bad key');
      return [...text.slice(4)].reverse().join('');
    },
    canEncrypt: () => secure,
    methods: METHODS,
  };
  const store = new SiteStore(options);
  await store.load();
  return { root, options, store };
}

const jira = {
  kind: 'jira',
  name: 'JIRA',
  url: 'https://jira.test/jira/browse/TEAM-1?focusedCommentId=1#comment-1',
  login_id: 'tester',
  password: 'p@ss word',
  method: 'auto',
};

test('a saved site survives a restart with only an encrypted password on disk', async (t) => {
  const { options, store } = await fixture(t);
  assert.deepEqual(store.list().sites, []);
  assert.deepEqual(
    store.list().methods.map((method) => method.id),
    METHODS.map((method) => method.id),
  );
  const saved = await store.save(jira);
  assert.equal(saved.url, 'https://jira.test/jira');
  assert.equal(saved.has_password, true);
  assert.equal(saved.password_stored, true);
  assert.equal(saved.method_label, '자동 감지');
  assert.equal(JSON.stringify(saved).includes('p@ss word'), false);
  assert.equal(Object.hasOwn(saved, 'password'), false);
  assert.equal(Object.hasOwn(saved, 'encrypted_password'), false);
  const raw = await fs.readFile(options.file, 'utf8');
  assert.doesNotMatch(raw, /p@ss word/);
  assert.equal(JSON.parse(raw).sites[0].login_id, 'tester');
  if (process.platform !== 'win32') assert.equal((await fs.stat(options.file)).mode & 0o777, 0o600);

  const restarted = new SiteStore(options);
  const listed = await restarted.load();
  assert.deepEqual(listed.sites, [saved]);
  assert.deepEqual(restarted.plan(restarted.get(saved.id)).credentials, {
    id: 'tester',
    password: 'p@ss word',
  });
  // An empty password keeps the saved one; clearing drops it and the site uses the session.
  await restarted.save({ id: saved.id, name: '새 이름', password: '' });
  assert.equal(restarted.get(saved.id).password, 'p@ss word');
  const cleared = await restarted.save({ id: saved.id, clear_password: true });
  assert.equal(cleared.has_password, false);
  assert.equal(restarted.plan(restarted.get(saved.id)), null);
  assert.doesNotMatch(await fs.readFile(options.file, 'utf8'), /encrypted_password/);
  await restarted.remove(saved.id);
  assert.deepEqual((await new SiteStore(options).load()).sites, []);
  await assert.rejects(restarted.remove(saved.id), /찾을 수 없습니다/);
});

test('without OS encryption the password stays in memory only', async (t) => {
  const { options, store } = await fixture(t, false);
  const saved = await store.save(jira);
  assert.equal(saved.has_password, true);
  assert.equal(saved.password_stored, false);
  assert.equal(store.list().secure_storage, false);
  assert.doesNotMatch(await fs.readFile(options.file, 'utf8'), /p@ss word|encrypted/);
  const restarted = new SiteStore(options);
  const [site] = (await restarted.load()).sites;
  assert.equal(site.login_id, 'tester');
  assert.equal(site.has_password, false);
  assert.equal(restarted.plan(restarted.get(site.id)), null);
  // The site can still be edited; the password is entered again when needed.
  assert.equal((await restarted.save({ id: site.id, name: '다시' })).name, '다시');
});

test('invalid values are rejected without changing saved sites', async (t) => {
  const { store } = await fixture(t);
  const saved = await store.save(jira);
  for (const [params, message] of [
    [{ ...jira, kind: 'wiki' }, /종류/],
    [{ ...jira, url: 'ftp://jira.test/' }, /http/],
    [{ ...jira, url: 'https://user:pw@jira.test/' }, /http/],
    [{ ...jira, url: 'jira test/jira' }, /사이트 주소를 확인/],
    [{ ...jira, url: 'C:\\jira' }, /사이트 주소를 확인/],
    [{ ...jira, url: 'user@jira.test' }, /사이트 주소를 확인/],
    [{ ...jira, name: 'x'.repeat(81) }, /80자/],
    [{ ...jira, url: 'https://other.test', login_id: '' }, /로그인 ID/],
    [{ ...jira, url: 'https://other.test', login_id: 'a\nb' }, /로그인 ID/],
    [{ ...jira, url: 'https://other.test', login_id: 'x'.repeat(257) }, /로그인 ID/],
    [{ ...jira, url: 'https://other.test', password: 'line\nbreak' }, /비밀번호/],
    [{ ...jira, url: 'https://other.test', password: 'x'.repeat(1025) }, /비밀번호/],
    [{ ...jira, url: 'https://other.test', password: '' }, /비밀번호를 입력/],
    [{ ...jira, url: 'https://other.test', method: 'magic' }, /로그인 방식/],
    [
      { ...jira, url: 'https://other.test', method: 'custom', custom: { id: '#id' } },
      /사용자 지정/,
    ],
    [
      {
        ...jira,
        url: 'https://other.test',
        method: 'custom',
        custom: { before: Array(6).fill('#b'), id: '#i', password: '#p' },
      },
      /5개/,
    ],
    [
      {
        ...jira,
        url: 'https://other.test',
        method: 'custom',
        custom: { id: '#i\n', password: '#p' },
      },
      /줄바꿈/,
    ],
    // The same site twice, also through another link of it.
    [{ ...jira, url: 'https://jira.test/jira/projects/TEAM/issues/TEAM-9' }, /이미 있습니다/],
    [{ id: 'missing', ...jira }, /찾을 수 없습니다/],
  ])
    await assert.rejects(store.save(params), message, JSON.stringify(params));
  assert.deepEqual(store.list().sites, [saved]);
  // A session-only site needs neither an ID nor a password.
  const session = await store.save({
    kind: 'confluence',
    url: 'https://wiki.test/',
    method: 'session',
    name: '',
  });
  assert.equal(session.name, 'Confluence');
  assert.equal(session.method_label, '자동 입력 안 함 · 로그인 창 사용');
  assert.equal(store.plan(store.get(session.id)), null);
});

test('site addresses are the base path of JIRA and Confluence links', () => {
  for (const [value, expected] of [
    ['https://jira.test/jira/browse/TEAM-1?x=1#c', 'https://jira.test/jira'],
    ['https://JIRA.test:443/jira/projects/T/issues/T-9', 'https://jira.test/jira'],
    ['https://jira.test/browse/TEAM-1', 'https://jira.test'],
    ['https://jira.test/jira/secure/Dashboard.jspa', 'https://jira.test/jira'],
    ['https://wiki.test/wiki/spaces/S/pages/1/Title', 'https://wiki.test/wiki'],
    ['https://wiki.test/confluence/display/S/Page', 'https://wiki.test/confluence'],
    ['https://wiki.test/confluence/pages/viewpage.action?pageId=1', 'https://wiki.test/confluence'],
    [
      'http://127.0.0.1:8090/confluence/login.action?os_destination=x',
      'http://127.0.0.1:8090/confluence',
    ],
    ['https://wiki.test/x/AbC', 'https://wiki.test'],
    ['https://jira.test:8443/', 'https://jira.test:8443'],
    ['  https://jira.test/jira/  ', 'https://jira.test/jira'],
  ])
    assert.equal(normalizeUrl(value), expected, value);
});

test('a link belongs to the same-kind site with the longest address, and only to a site with one', async (t) => {
  const { store } = await fixture(t);
  const base = { login_id: 'a', password: 'b', method: 'auto' };
  const root = await store.save({ ...base, kind: 'jira', url: 'https://jira.test' });
  const nested = await store.save({
    ...base,
    kind: 'jira',
    url: 'https://jira.test/jira/browse/X-1',
  });
  const wiki = await store.save({ ...base, kind: 'confluence', url: 'https://jira.test/jira' });
  const match = (url, kind = 'jira') => store.match(url, kind)?.id ?? null;
  assert.equal(match('https://jira.test/jira/browse/TEAM-1'), nested.id);
  assert.equal(match('https://jira.test/jira'), nested.id);
  assert.equal(match('https://jira.test/jira2/browse/TEAM-1'), root.id);
  assert.equal(match('https://jira.test/browse/TEAM-1'), root.id);
  assert.equal(
    match('https://jira.test/jira/pages/viewpage.action?pageId=1', 'confluence'),
    wiki.id,
  );
  assert.equal(match('https://jira.test:8443/browse/TEAM-1'), null);
  assert.equal(match('https://jira.test.evil.test/browse/TEAM-1'), null);
  assert.equal(match('not a url'), null);
  // Every account names its site: a link to another host gets no password, whatever its path looks like.
  await assert.rejects(store.save({ ...base, kind: 'jira', url: '' }), /사이트 주소를 입력/);
  await assert.rejects(store.save({ ...base, kind: 'jira', url: '  ' }), /사이트 주소를 입력/);
  assert.equal(match('https://other.test/browse/TEAM-1'), null);
  assert.equal(match('https://other.test/display/S/P', 'confluence'), null);
});

test('logins of earlier versions become accounts that sign in only once given an address', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-sites-legacy-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const encrypted = (value) =>
    Buffer.from('enc:' + [...value].reverse().join('')).toString('base64');
  const legacy = {
    confluence: path.join(root, 'confluence-login.json'),
    jira: path.join(root, 'jira-login.json'),
  };
  await fs.writeFile(
    legacy.confluence,
    JSON.stringify({
      version: 1,
      login_id: 'wiki-user',
      encrypted_password: encrypted('wiki-pass'),
    }),
  );
  // Written under another OS account: the ID survives, the password is entered again.
  await fs.writeFile(
    legacy.jira,
    JSON.stringify({ version: 1, login_id: 'jira-user', encrypted_password: 'eA==' }),
  );
  const options = {
    file: path.join(root, 'sites.json'),
    legacyFiles: legacy,
    encrypt: (value) => Buffer.from('enc:' + [...value].reverse().join('')),
    decrypt: (value) => {
      const text = value.toString();
      if (!text.startsWith('enc:')) throw new Error('bad key');
      return [...text.slice(4)].reverse().join('');
    },
    canEncrypt: () => true,
    methods: METHODS,
  };
  const store = new SiteStore(options);
  const { sites } = await store.load();
  assert.deepEqual(
    sites.map(({ kind, name, url, login_id, has_password, method }) => ({
      kind,
      name,
      url,
      login_id,
      has_password,
      method,
    })),
    [
      {
        kind: 'jira',
        name: '기본 JIRA 계정',
        url: '',
        login_id: 'jira-user',
        has_password: false,
        method: 'auto',
      },
      {
        kind: 'confluence',
        name: '기본 Confluence 계정',
        url: '',
        login_id: 'wiki-user',
        has_password: true,
        method: 'auto',
      },
    ],
  );
  await assert.rejects(fs.access(legacy.confluence));
  await assert.rejects(fs.access(legacy.jira));
  // Loaded from sites.json on the next start, not migrated again.
  assert.deepEqual((await new SiteStore(options).load()).sites, sites);
  // Without an address they sign in nowhere: their password would be typed into any site a link named.
  const link = 'https://attacker.test/display/S/P';
  assert.equal(store.match(link, 'confluence'), null);
  assert.equal(store.match('https://wiki.test/display/S/P', 'confluence'), null);
  const moved = sites.find((site) => site.kind === 'confluence');
  await assert.rejects(store.save({ id: moved.id, name: '새 이름' }), /사이트 주소를 입력/);
  // Its password was typed for no address: given one, the account signs in once the password comes with it.
  const addressed = await store.save({ id: moved.id, url: 'https://wiki.test/display/S/P' });
  assert.equal(addressed.url, 'https://wiki.test');
  assert.equal(addressed.has_password, false);
  assert.equal(store.plan(store.match('https://wiki.test/display/S/P', 'confluence')), null);
  await store.save({ id: moved.id, password: 'wiki-pass' });
  assert.deepEqual(
    store.plan(store.match('https://wiki.test/display/S/P', 'confluence')).credentials,
    { id: 'wiki-user', password: 'wiki-pass' },
  );
  assert.equal(store.match(link, 'confluence'), null);
});

test('a site moved to another origin or kind keeps its password only when it is typed again', async (t) => {
  const { options, store } = await fixture(t);
  const site = await store.save(jira);
  const password = () => store.get(site.id).password;
  // Another path of the same origin keeps it.
  await store.save({ id: site.id, url: 'https://jira.test/jira2' });
  assert.equal(password(), 'p@ss word');
  // Another origin, e.g. an AI tool's or a typo's: the stored password is not used there, and the site signs in
  // nowhere until the password is typed again.
  for (const url of [
    'https://other.test/jira',
    'http://jira.test/jira',
    'https://jira.test:8443/jira',
    // Without its scheme it may open over http: a move too.
    'jira.test/jira',
  ]) {
    await store.save({ id: site.id, url: 'https://jira.test/jira' });
    await store.save({ id: site.id, password: 'p@ss word' });
    const moved = await store.save({ id: site.id, url });
    assert.deepEqual([moved.has_password, moved.password_stored], [false, false], url);
    assert.equal(store.plan(store.get(site.id)), null);
  }
  assert.doesNotMatch(await fs.readFile(options.file, 'utf8'), /encrypted_password/);
  // Typed again with the move, it is the one kept.
  const typed = await store.save({ id: site.id, url: 'https://jira3.test/', password: 'new pass' });
  assert.equal(typed.has_password, true);
  assert.deepEqual(store.plan(store.get(site.id)).credentials, {
    id: 'tester',
    password: 'new pass',
  });
  // Another kind at the same address too.
  assert.equal((await store.save({ id: site.id, kind: 'confluence' })).has_password, false);
});

test('a change that cannot be written to sites.json changes nothing', async (t) => {
  const { options, store } = await fixture(t);
  const site = await store.save(jira);
  const write = store.write;
  store.write = async () => {
    throw new Error('ENOSPC: no space left on device');
  };
  const listed = JSON.stringify(store.list());
  await assert.rejects(store.save({ ...jira, url: 'https://new.test/' }), /ENOSPC/);
  await assert.rejects(
    store.save({ id: site.id, name: '새 이름', url: 'https://moved.test/' }),
    /ENOSPC/,
  );
  await assert.rejects(store.record(site.id, { ok: false, error: '실패' }), /ENOSPC/);
  await assert.rejects(store.remove(site.id), /ENOSPC/);
  assert.equal(JSON.stringify(store.list()), listed);
  assert.equal(store.get(site.id).password, 'p@ss word');
  // Once it can be written, the same changes go through.
  store.write = write;
  assert.equal((await store.save({ ...jira, url: 'https://new.test/' })).url, 'https://new.test');
  assert.equal((await store.save({ id: site.id, name: '새 이름' })).name, '새 이름');
  assert.equal((await new SiteStore(options).load()).sites.length, 2);
});

test('logins of earlier versions that cannot be moved stay where they are', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-sites-unmoved-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const legacy = path.join(root, 'jira-login.json');
  await fs.writeFile(legacy, JSON.stringify({ version: 1, login_id: 'jira-user' }));
  const store = new SiteStore({
    file: path.join(root, 'sites.json'),
    legacyFiles: { jira: legacy },
    encrypt: (value) => Buffer.from(value),
    decrypt: (value) => value.toString(),
    canEncrypt: () => true,
    methods: METHODS,
  });
  store.write = async () => {
    throw new Error('EACCES: permission denied');
  };
  // The app starts; the accounts cannot be changed this session, and the next start tries again.
  const listed = await store.load();
  assert.deepEqual(listed.sites, []);
  assert.match(listed.error, /이전 버전의 로그인 정보/);
  await assert.rejects(store.save(jira), /이전 버전의 로그인 정보/);
  await fs.access(legacy);
  await assert.rejects(fs.access(path.join(root, 'sites.json')));
});

test('a damaged sites.json is set aside, and one that cannot be read is never written over', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-sites-damaged-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const options = {
    file: path.join(root, 'sites.json'),
    encrypt: (value) => Buffer.from('enc:' + [...value].reverse().join('')),
    decrypt: (value) => [...value.toString().slice(4)].reverse().join(''),
    canEncrypt: () => true,
    methods: METHODS,
  };
  // Cut short by a power loss: kept beside the new file for recovery, and the store starts empty.
  await fs.writeFile(options.file, '{"version":1,"sites":[{"id"');
  const store = new SiteStore(options);
  const loaded = await store.load();
  assert.deepEqual([loaded.sites, loaded.error], [[], '']);
  const [aside] = (await fs.readdir(root)).filter((name) => name.startsWith('sites.json.corrupt-'));
  assert.equal(await fs.readFile(path.join(root, aside), 'utf8'), '{"version":1,"sites":[{"id"');
  await store.save(jira);
  assert.equal((await new SiteStore(options).load()).sites.length, 1);
  assert.equal(await fs.readFile(path.join(root, aside), 'utf8'), '{"version":1,"sites":[{"id"');

  // Not readable at all (here a folder in its place): nothing is saved over it this session.
  await fs.rm(options.file);
  await fs.mkdir(options.file);
  const blocked = new SiteStore(options);
  const listed = await blocked.load();
  assert.deepEqual(listed.sites, []);
  assert.match(listed.error, /sites\.json/);
  await assert.rejects(blocked.save(jira), /sites\.json/);
  assert.equal(await blocked.record('missing', { ok: true }).catch(() => 'refused'), 'refused');
  assert.ok((await fs.stat(options.file)).isDirectory());
});

test("user-defined steps are checked, and earlier versions' methods load as steps", async (t) => {
  const { options, store } = await fixture(t);
  const site = await store.save(jira);
  const id = { action: 'fill', value: 'id', target: '#user' };
  const password = { action: 'fill', value: 'password', target: '#pass' };
  const submit = { action: 'submit', target: '#go' };
  for (const [steps, message] of [
    [[...Array(15).fill(id), password, submit], /16개/],
    [[...Array(15).fill(id), password], /16개/],
    [[id, submit, password, submit], /마지막에 하나만/],
    [[{ action: 'type', target: '#user' }, password], /1번째 단계: 동작/],
    [[id, { action: 'click', target: ' ' }, password], /2번째 단계: 입력하거나 누를 위치/],
    [[{ action: 'fill', target: '#user' }, password], /1번째 단계: 입력할 값/],
    [
      [id, { action: 'fill', value: 'text', target: '#d', text: '' }, password],
      /2번째 단계: 입력할 글자/,
    ],
    [[id, { action: 'fill', value: 'text', target: '#d', text: 'a\nb' }, password], /줄바꿈/],
    [
      [id, { action: 'fill', value: 'text', target: '#d', text: 'x'.repeat(201) }, password],
      /200자/,
    ],
    [[id, { action: 'click', target: '#a\n' }, password], /2번째 단계의 위치: 줄바꿈/],
    [[id, { action: 'click', target: '#a', unless: 'x'.repeat(501) }, password], /건너뛸 조건/],
    [[id, submit], /로그인 ID와 비밀번호/],
    [
      [{ action: 'fill', value: 'text', target: '#user', text: 'tester' }, password],
      /로그인 ID와 비밀번호/,
    ],
    [[], /로그인 ID와 비밀번호/],
    ['steps', /16개/],
  ])
    await assert.rejects(
      store.save({ id: site.id, method: 'custom', custom: { steps } }),
      message,
      JSON.stringify(steps),
    );
  assert.equal(store.get(site.id).method, 'auto');

  // A sites.json of an earlier version: its user-defined method and the LLM's fields become steps.
  const legacy = {
    version: 1,
    sites: [
      {
        ...store.get(site.id),
        password: undefined,
        method: 'custom',
        custom: { before: ['#cookie'], id: '#account', next: '', password: '#pw', submit: '' },
        detected_method: null,
        learned: { before: [], id: '#a', next: '#b', password: '#c', submit: '#d' },
      },
    ],
  };
  await fs.writeFile(options.file, JSON.stringify(legacy));
  const restarted = new SiteStore(options);
  const [loaded] = (await restarted.load()).sites;
  assert.deepEqual(loaded.custom.steps, [
    { action: 'click', target: '#cookie', optional: true, unless: '#account' },
    { action: 'fill', target: '#account', value: 'id' },
    { action: 'fill', target: '#pw', value: 'password' },
    { action: 'submit', target: '' },
  ]);
  assert.deepEqual(
    loaded.learned.steps.map((step) => [step.action, step.target]),
    [
      ['fill', '#a'],
      ['click', '#b'],
      ['fill', '#c'],
      ['submit', '#d'],
    ],
  );
  assert.deepEqual(restarted.plan(restarted.get(site.id)).methods[0].detect, [
    '#cookie',
    '#account',
  ]);
  // An earlier version's method without its fields, or a damaged one, is none.
  legacy.sites[0].custom = { id: '#account', password: '' };
  await fs.writeFile(options.file, JSON.stringify(legacy));
  const again = new SiteStore(options);
  const [empty] = (await again.load()).sites;
  assert.deepEqual(empty.custom, { steps: [] });
  assert.equal(again.plan(again.get(site.id)), null);
});

test('fields the LLM found are kept for an auto site, and a failed login keeps its analysis', async (t) => {
  const { options, store } = await fixture(t);
  const site = await store.save(jira);
  const analysis = {
    category: 'credentials',
    label: 'ID·비밀번호',
    cause: '비밀번호가 거부되었습니다.',
    evidence: ['안내 문구', 7],
    actions: ['비밀번호를 고치세요.'],
    retry: false,
    wait: 0,
    attempt: '',
  };
  let recorded = await store.record(site.id, {
    ok: false,
    error: '로그인하지 못했습니다.',
    diagnosis: analysis,
  });
  assert.deepEqual(recorded.last_diagnosis, {
    category: 'credentials',
    label: 'ID·비밀번호',
    cause: '비밀번호가 거부되었습니다.',
    evidence: ['안내 문구'],
    actions: ['비밀번호를 고치세요.'],
    attempt: '',
  });
  const learned = {
    before: ['#open-login'],
    id: '#account',
    next: '',
    password: '#passcode',
    submit: '/html/body/span',
  };
  recorded = await store.record(site.id, { ok: true, method: 'learned', detected: true, learned });
  assert.equal(recorded.detected_method, 'learned');
  assert.equal(recorded.detected_label, 'AI가 찾은 로그인 화면');
  // Kept as the steps of a user-defined method, which the screens show and the user may copy.
  const steps = [
    { action: 'click', target: '#open-login', optional: true, unless: '#account' },
    { action: 'fill', value: 'id', target: '#account' },
    { action: 'fill', value: 'password', target: '#passcode' },
    { action: 'submit', target: 'xpath=/html/body/span' },
  ];
  assert.deepEqual(recorded.learned, { steps });
  assert.equal(recorded.last_diagnosis, null);
  // Tried on the next login, also after a restart, as a user-defined method of those fields: before the rest, after
  // the SSO link and AD SSO methods that are tried first.
  const restarted = new SiteStore(options);
  await restarted.load();
  const plan = restarted.plan(restarted.get(site.id));
  const priority = METHODS.filter((method) => method.priority).map((method) => method.id);
  assert.deepEqual(
    plan.methods.map((method) => method.id),
    [...priority, 'learned', ...METHODS.filter((m) => !m.priority).map((m) => m.id)],
  );
  assert.deepEqual(plan.methods[priority.length], {
    id: 'learned',
    label: 'AI가 찾은 로그인 화면',
    detect: ['#open-login', '#account'],
    steps,
  });
  // Signing in with them again keeps them; fields that are no method change nothing; a catalog method
  // that fits instead replaces them.
  recorded = await store.record(site.id, { ok: true, method: 'learned', detected: true });
  assert.equal(recorded.detected_method, 'learned');
  recorded = await store.record(site.id, {
    ok: true,
    method: 'learned',
    detected: true,
    learned: { id: '#account' },
  });
  assert.deepEqual(recorded.learned, { steps });
  recorded = await store.record(site.id, { ok: true, method: 'generic', detected: true });
  assert.deepEqual([recorded.detected_method, recorded.learned], ['generic', null]);
  // Only why an analysis could not be made; a site moved to another address starts over.
  recorded = await store.record(site.id, {
    ok: false,
    error: '실패',
    diagnosis: { error: 'LLM 오류' },
  });
  assert.deepEqual(recorded.last_diagnosis, { error: 'LLM 오류' });
  await store.record(site.id, { ok: true, method: 'learned', detected: true, learned });
  const moved = await store.save({ id: site.id, url: 'https://jira2.test/' });
  assert.deepEqual(
    [moved.detected_method, moved.learned, moved.last_diagnosis],
    [null, null, null],
  );
  // A site whose method was chosen keeps it.
  await store.save({ id: site.id, method: 'atlassian' });
  recorded = await store.record(site.id, { ok: true, method: 'learned', detected: true, learned });
  assert.deepEqual([recorded.detected_method, recorded.learned], [null, null]);
});

test('saves are serialized', async (t) => {
  const { options, store } = await fixture(t);
  const saved = await Promise.all(
    Array.from({ length: 8 }, (_, i) =>
      store.save({ ...jira, url: `https://jira${i}.test/`, name: `JIRA ${i}` }),
    ),
  );
  assert.equal(new Set(saved.map((site) => site.id)).size, 8);
  const restarted = new SiteStore(options);
  assert.equal((await restarted.load()).sites.length, 8);
});
