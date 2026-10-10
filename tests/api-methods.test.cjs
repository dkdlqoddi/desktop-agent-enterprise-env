const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createApiMethods, createPathCheck, serverOf } = require('../electron/api-methods.cjs');

const DOCS = [
  {
    id: 'a',
    title: '캐시 회의록',
    summary: 'API 캐시',
    excerpt: '',
    keywords: ['api', '캐시'],
    status: 'ready',
    index_mode: 'wiki',
    source: null,
  },
  {
    id: 'b',
    title: 'JIRA 티켓',
    summary: '',
    excerpt: '로그인 오류',
    keywords: [],
    status: 'error',
    index_mode: '',
    source: { kind: 'jira' },
  },
  {
    id: 'c',
    title: '보고서',
    summary: '',
    excerpt: '',
    keywords: [],
    status: 'indexing',
    index_mode: '',
    source: { kind: 'office' },
  },
  {
    id: 'd',
    title: '메모',
    summary: '',
    excerpt: '',
    keywords: [],
    status: 'pending',
    index_mode: '',
    source: { kind: 'office' },
  },
];

function setup(answers = {}) {
  const calls = [];
  const handle = async (method, params) => {
    calls.push({ method, params });
    if (method in answers)
      return typeof answers[method] === 'function' ? answers[method](params) : answers[method];
    if (method === 'wiki.list') return structuredClone(DOCS);
    return { method };
  };
  const linkKind = (url) =>
    url.includes('/browse/') ? 'jira' : url.includes('/pages/') ? 'confluence' : null;
  const methods = createApiMethods({
    handle,
    linkKind,
    extensions: {
      markdown: ['md', 'markdown'],
      text: ['txt'],
      office: ['docx', 'xlsx', 'pptx'],
      rtf: ['rtf'],
      image: ['png'],
    },
  });
  return { methods, calls };
}

test('starts the import a link or path calls for, as the import box does', async (t) => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'private-agent-import-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  for (const name of ['plan.docx', 'note.md', 'shot.png', 'data.bin'])
    await fs.writeFile(path.join(directory, name), 'x');
  const { methods, calls } = setup({
    'office.start': { id: 'job1', status: 'scanning' },
    'jira.start': { job: { id: 'job2' }, login_open: false },
    'files.import_text': { status: 'imported', doc_id: 'n1', title: 'note' },
  });
  const start = (params) => methods['import.start'](params, { access: 'full' });
  assert.deepEqual(await start({ source: path.join(directory, 'plan.docx') }), {
    kind: 'office',
    job: { id: 'job1', status: 'scanning' },
  });
  assert.deepEqual(calls.at(-1).params, {
    path: path.join(directory, 'plan.docx'),
    mode: 'file',
    recursive: false,
    analyze: false,
  });
  assert.deepEqual(
    (await start({ source: path.join(directory, 'note.md'), analyze: true })).document,
    {
      status: 'imported',
      doc_id: 'n1',
      title: 'note',
    },
  );
  await start({ source: path.join(directory, 'shot.png') });
  assert.equal(calls.at(-1).method, 'image.start');
  await start({ source: `"${directory}"`, folder_kind: 'text' });
  assert.deepEqual(calls.at(-1), {
    method: 'text.start',
    params: { path: directory, mode: 'directory', recursive: true, analyze: false },
  });
  assert.deepEqual(await start({ source: 'https://jira.example/browse/ABC-1' }), {
    kind: 'jira',
    job: { id: 'job2' },
  });
  assert.deepEqual(calls.at(-1).params, {
    url: 'https://jira.example/browse/ABC-1',
    scope: 'issue',
    keyword: '',
    user: '',
    role: 'any',
    analyze: false,
  });
  await start({ source: 'https://wiki.example/spaces/A/pages/1', recursive: true, analyze: true });
  assert.deepEqual(calls.at(-1).params, {
    url: 'https://wiki.example/spaces/A/pages/1',
    recursive: true,
    analyze: true,
  });
  // A link typed without its scheme, as the import box takes one; the backend opens it over https.
  assert.deepEqual(await start({ source: 'jira.example/browse/ABC-2' }), {
    kind: 'jira',
    job: { id: 'job2' },
  });
  assert.equal(calls.at(-1).params.url, 'jira.example/browse/ABC-2');
  // One that starts with a slash is a path (//server/share/…), whatever it looks like.
  await assert.rejects(start({ source: '//server/share/browse/ABC-3' }), /찾을 수 없습니다/);
  await assert.rejects(start({ source: 'https://example.com/' }), /가져올 수 없는 링크/);
  await assert.rejects(start({ source: path.join(directory, 'data.bin') }), /가져올 수 없는 파일/);
  await assert.rejects(start({ source: path.join(directory, 'missing.docx') }), /찾을 수 없습니다/);
  await assert.rejects(start({ source: 'relative/plan.docx' }), /전체 경로/);
  await assert.rejects(start({ source: directory, folder_kind: 'video' }), /folder_kind/);
  await assert.rejects(
    start({ source: path.join(directory, 'plan.docx'), analyze: 'yes' }),
    /analyze/,
  );
  await assert.rejects(start({}), /전체 경로/);
  // A path or link imported before updates its document; which one and whether edited text is replaced go along.
  await start({ source: path.join(directory, 'plan.docx'), doc_id: 'd1', replace: true });
  assert.deepEqual(calls.at(-1).params, {
    path: path.join(directory, 'plan.docx'),
    mode: 'file',
    recursive: false,
    analyze: false,
    doc_id: 'd1',
    replace: true,
  });
  await start({ source: 'https://jira.example/browse/ABC-1', doc_id: 'd2', replace: false });
  assert.equal(calls.at(-1).params.doc_id, 'd2');
  assert.equal('replace' in calls.at(-1).params, false);
  await start({ source: path.join(directory, 'note.md'), doc_id: '' });
  assert.deepEqual(calls.at(-1).params, { path: path.join(directory, 'note.md'), analyze: false });
  await assert.rejects(
    start({ source: path.join(directory, 'plan.docx'), replace: 'yes' }),
    /replace/,
  );
  await assert.rejects(start({ source: path.join(directory, 'plan.docx'), doc_id: 7 }), /doc_id/);
});

test('names the computer a path is on', () => {
  for (const [file, server] of [
    ['C:\\Users\\me\\plan.docx', ''],
    ['/home/me/plan.docx', ''],
    ['\\\\FileServer\\team\\plan.docx', 'fileserver'],
    ['//fileserver/team/plan.docx', 'fileserver'],
    ['\\\\?\\C:\\Users\\me\\plan.docx', ''],
    ['\\\\?\\UNC\\fileserver\\team\\plan.docx', 'fileserver'],
    ['\\\\.\\pipe\\private', null],
    ['\\\\?\\Volume{0000}\\plan.docx', null],
  ])
    assert.equal(serverOf(file), server, file);
});

test("reaches other computers' shared folders from AI tools only where the user already works", async () => {
  const lists = [];
  const check = createPathCheck({
    handle: async (method) => {
      lists.push(method);
      return [
        { id: 'a', source: null },
        { id: 'b', source: { kind: 'office', source_path: '\\\\fileserver\\team\\old.docx' } },
      ];
    },
    wikiDirectory: () => '\\\\nas\\me\\Private Agent',
  });
  const refused = async (method, params) =>
    assert.rejects(check(method, params, 'full'), (error) => {
      assert.equal(error.status, 403);
      assert.match(error.message, /\\\\attacker/);
      return true;
    });
  await refused('import.start', { source: '\\\\attacker\\share\\x.docx' });
  await refused('import.start', { source: '"//Attacker/share/x.docx"' });
  await refused('office.start', { path: '\\\\?\\UNC\\attacker\\share\\x.docx', mode: 'file' });
  await refused('files.import_text', { path: '\\\\attacker\\share\\x.md' });
  // The CA certificate of the LLM settings is read for every request, from wherever it is.
  await refused('settings.update', { ca_bundle: '"\\\\attacker\\share\\ca.pem"' });
  await refused('routines.save', {
    kind: 'source_refresh',
    config: { paths: ['C:\\docs', '\\\\attacker\\share'] },
  });
  // Read as the backend reads a routine's paths: the lines of a text, spaces and tabs around each, one pair of quotes.
  await refused('routines.save', {
    kind: 'source_refresh',
    config: { paths: 'C:\\docs\r\n \t"\\\\attacker\\share\\x" ' },
  });
  await assert.rejects(
    check('text.start', { path: '\\\\.\\pipe\\x', mode: 'file' }, 'full'),
    /장치 경로/,
  );
  // The Wiki folder's computer and one a document came from; this PC's own drives and links need no list.
  await check('import.start', { source: '\\\\FILESERVER\\team\\new.docx' }, 'full');
  await check('rtf.start', { path: '\\\\nas\\me\\docs', mode: 'directory' }, 'full');
  const looked = lists.length;
  await check('import.start', { source: 'C:\\Users\\me\\plan.docx' }, 'full');
  await check('import.start', { source: 'https://jira.example/browse/A-1' }, 'full');
  await check(
    'settings.update',
    { ca_bundle: 'C:\\certs\\private-ca.pem', model_name: 'm' },
    'full',
  );
  await check('wiki.search', { query: '\\\\attacker\\share' }, 'full');
  assert.equal(lists.length, looked);
  // Below "full" the method's own check refuses first, before anything is looked up.
  await check('import.start', { source: '\\\\attacker\\share\\x.docx' }, 'write');
  assert.equal(lists.length, looked);
});

test('lists documents through the backend: its filters and pages, nothing filtered here', async () => {
  // The backend's pages of the list: the first, and the one its cursor continues with.
  const pages = {
    first: { documents: DOCS.slice(0, 2), next_cursor: 'c2', total: 4 },
    c2: { documents: DOCS.slice(2), next_cursor: null, total: 4 },
  };
  const { methods, calls } = setup({
    'wiki.list': (params) => structuredClone(pages[params.cursor ?? 'first']),
  });
  const query = methods['documents.query'];
  const first = await query({});
  assert.deepEqual(calls.at(-1), { method: 'wiki.list', params: { limit: 50 } });
  assert.deepEqual([first.total, first.limit, first.next_cursor], [4, 50, 'c2']);
  assert.deepEqual(
    first.documents.map((doc) => [doc.id, doc.analysis]),
    [
      ['a', 'analyzed'],
      ['b', 'failed'],
    ],
  );
  const second = await query({ limit: 2, cursor: first.next_cursor });
  assert.deepEqual(calls.at(-1).params, { limit: 2, cursor: 'c2' });
  assert.deepEqual(
    second.documents.map((doc) => doc.analysis),
    ['running', 'not_analyzed'],
  );
  assert.equal(second.next_cursor, null);
  // Every filter goes to the backend, which answers the page: the text and keyword trimmed, the empty ones left out.
  await query({
    query: ' 캐시 ',
    keyword: '데이터베이스',
    source: 'office',
    analysis: 'not_analyzed',
    updated_from: '2026-10-01T00:00:00+09:00',
    updated_to: '2026-10-08T00:00:00+09:00',
    limit: 5,
    cursor: 'c2',
  });
  assert.deepEqual(calls.at(-1).params, {
    limit: 5,
    query: '캐시',
    keyword: '데이터베이스',
    source: 'office',
    analysis: 'not_analyzed',
    updated_from: '2026-10-01T00:00:00+09:00',
    updated_to: '2026-10-08T00:00:00+09:00',
    cursor: 'c2',
  });
  await query({ query: '  ', keyword: '', source: '', analysis: null, offset: 0 });
  assert.deepEqual(calls.at(-1).params, { limit: 50 });
  assert.equal(calls.filter((call) => call.method === 'wiki.list' && !call.params.limit).length, 0);
  await assert.rejects(query({ limit: 0 }), /limit/);
  await assert.rejects(query({ limit: 501 }), /limit/);
  await assert.rejects(query({ source: 'dropbox' }), /source/);
  await assert.rejects(query({ analysis: 'done' }), /analysis/);
  await assert.rejects(query({ query: 'x'.repeat(201) }), /검색어/);
  // An offset would answer the first page again and again: the caller is told to use the cursor.
  await assert.rejects(query({ offset: 50 }), /next_cursor를 cursor로/);
  await assert.rejects(query({ offset: '50' }), /next_cursor를 cursor로/);
});

test('leaves a document HTML page out unless asked', async () => {
  const { methods } = setup({
    'wiki.get': () => ({ id: 'a', html: '<p>big</p>', status: 'ready', index_mode: '' }),
  });
  assert.equal((await methods['documents.get']({ id: 'a' })).html, undefined);
  assert.equal(
    (await methods['documents.get']({ id: 'a', include_html: true })).html,
    '<p>big</p>',
  );
});

test('filters tasks by status', async () => {
  const tasks = [
    { id: 1, status: 'open' },
    { id: 2, status: 'done' },
  ];
  const { methods } = setup({ 'tasks.list': tasks });
  assert.equal((await methods['tasks.query']({})).length, 2);
  assert.deepEqual(await methods['tasks.query']({ status: 'done' }), [tasks[1]]);
  await assert.rejects(methods['tasks.query']({ status: 'late' }), /status/);
});
