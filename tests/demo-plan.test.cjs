const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {
  FILES,
  MARKER,
  MAX_CAPTION_BYTES,
  decodeText,
  looksEncrypted,
  readUserText,
  parseList,
  writeTemplate,
  prepareWiki,
  createRunFolder,
  findRecording,
} = require('../electron/demo-plan.cjs');

async function folder(t) {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'agent-demo-plan-')));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  return root;
}

const randomBytes = (size) =>
  Buffer.from(Array.from({ length: size }, (_, i) => (i * 73 + 41) % 256));

test('a list file is read as Notepad saves it: UTF-8 with or without BOM, UTF-16 or ANSI (cp949)', () => {
  const text = '제목 = 시연\r\nD:\\자료\\설계서.docx';
  assert.equal(decodeText(Buffer.from('\uFEFF' + text)), text);
  assert.equal(decodeText(Buffer.from(text)), text);
  assert.equal(
    decodeText(Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from(text, 'utf16le')])),
    text,
  );
  const big = Buffer.from(text, 'utf16le');
  big.swap16();
  assert.equal(decodeText(Buffer.concat([Buffer.from([0xfe, 0xff]), big])), text);
  // "한글" in cp949.
  assert.equal(decodeText(Buffer.from([0xc7, 0xd1, 0xb1, 0xdb])), '한글');
  assert.equal(looksEncrypted(text.repeat(20)), false);
  assert.equal(looksEncrypted(decodeText(randomBytes(4000))), true);
});

test('an encrypted list is read through Notepad, and its failure says what was tried', async (t) => {
  const root = await folder(t);
  const file = path.join(root, 'list.txt');
  await fs.writeFile(file, randomBytes(4000));
  const asked = [];
  const read = await readUserText(file, {
    what: '목록 파일',
    readProtected: async (name) => {
      asked.push(name);
      return 'D:\\자료';
    },
  });
  assert.equal(read, 'D:\\자료');
  assert.deepEqual(asked, [file]);
  await assert.rejects(
    readUserText(file, {
      what: '목록 파일',
      readProtected: async () => {
        throw new Error('메모장이 30초 안에 파일을 열지 못했습니다.');
      },
    }),
    /암호화되어 있어\(DRM\).*메모장으로도 읽지 못했습니다: 메모장이 30초/,
  );
  await assert.rejects(readUserText(file, { what: '목록 파일' }), /암호화되어 있어\(DRM\)/);
  await assert.rejects(
    readUserText(path.join(root, 'none.txt'), { what: '목록 파일' }),
    /목록 파일을 찾을 수 없습니다/,
  );
});

test('a list is at most 1 MB; the captions of a long demo may be larger', async (t) => {
  const root = await folder(t);
  const file = path.join(root, '원본 녹화.srt');
  await fs.writeFile(file, 'x'.repeat(1536 * 1024));
  await assert.rejects(
    readUserText(file, { what: '자막 파일' }),
    /자막 파일이 너무 큽니다\(1MB 이하\)/,
  );
  const text = await readUserText(file, { what: '자막 파일', limit: MAX_CAPTION_BYTES });
  assert.equal(text.length, 1536 * 1024);
});

test('the lines of a list are items with their captions, settings by name, and mistakes by line', () => {
  const base = path.resolve('/demo');
  const list = path.join(base, 'list.txt');
  const absolute = path.resolve('/자료/설계서.docx');
  const { settings, items, problems } = parseList(
    [
      '# 설명',
      '',
      `${absolute} | 설계 문서를 가져옵니다`,
      'https://confluence.example.com/pages/viewpage.action?pageId=1 | 위키 | 페이지',
      `"${absolute}"`,
      '제목 = 우리 팀 시연',
      'AI 분석 = 예',
      '하위 폴더=아니오',
      'Confluence   하위 페이지 = on',
      '빨리 감기 = 0',
      'wiki 폴더 = 시연 Wiki',
      '영상 폴더 = "out"',
      '끝나면 닫기 = 글쎄',
      '자료 폴더 = D:\\x',
      '상대 경로.docx',
    ].join('\r\n'),
    list,
  );
  assert.deepEqual(items, [
    { line: 3, source: absolute, caption: '설계 문서를 가져옵니다' },
    {
      line: 4,
      source: 'https://confluence.example.com/pages/viewpage.action?pageId=1',
      caption: '위키 | 페이지',
    },
    { line: 5, source: absolute, caption: '' },
  ]);
  assert.deepEqual(settings, {
    title: '우리 팀 시연',
    analyze: true,
    subfolders: false,
    subpages: true,
    fastForward: false,
    quit: true,
    wiki: path.join(base, '시연 Wiki'),
    output: path.join(base, 'out'),
  });
  assert.equal(problems.length, 3);
  assert.match(problems[0], /^13번째 줄: ‘끝나면 닫기’ 값은 예 또는 아니오/);
  assert.match(problems[1], /^14번째 줄을 읽지 못했습니다: ‘자료 폴더 = D:\\x’/);
  assert.match(problems[2], /^15번째 줄을 읽지 못했습니다/);
  assert.deepEqual(parseList('# 비어 있음\r\n', list).problems, [
    '가져올 파일·폴더 경로나 링크가 목록에 없습니다. 한 줄에 하나씩 적어 주세요.',
  ]);
  // The defaults: beside the list.
  const defaults = parseList(absolute, list).settings;
  assert.equal(defaults.wiki, path.join(base, '시연 Wiki'));
  assert.equal(defaults.output, path.join(base, '시연 영상'));
  assert.equal(defaults.analyze, false);
  assert.equal(defaults.fastForward, true);
});

test('a list to fill in is written once, as Notepad keeps it, and has no item yet', async (t) => {
  const root = await folder(t);
  const list = path.join(root, '새 폴더', 'demo-list.txt');
  await writeTemplate(list);
  const bytes = await fs.readFile(list);
  assert.deepEqual([...bytes.subarray(0, 3)], [0xef, 0xbb, 0xbf]);
  assert.match(bytes.toString('utf8'), /# 제목 = Private Agent 시연\r\n/);
  assert.equal(bytes.toString('utf8').replace(/\r\n/g, '').includes('\n'), false);
  const { items, problems } = parseList(decodeText(bytes), list);
  assert.deepEqual(items, []);
  assert.equal(problems.length, 1);
  await assert.rejects(writeTemplate(list), { code: 'EEXIST' });
});

test('only a Wiki folder a demo made, or an empty one, is emptied; never the Wiki the app opens', async (t) => {
  const root = await folder(t);
  const wiki = path.join(root, '시연 Wiki');
  await prepareWiki(wiki);
  assert.match(await fs.readFile(path.join(wiki, MARKER), 'utf8'), /시연 녹화가 만든/);
  for (const name of ['wiki.db', 'wiki.db-wal', 'wiki.db-shm', '메모.txt'])
    await fs.writeFile(path.join(wiki, name), 'old');
  await prepareWiki(wiki);
  assert.deepEqual((await fs.readdir(wiki)).sort(), [MARKER, '메모.txt'].sort());

  const usual = path.join(root, '내 Wiki');
  await fs.mkdir(usual);
  await fs.writeFile(path.join(usual, 'wiki.db'), 'mine');
  await assert.rejects(prepareWiki(usual), /다른 파일이 있어 비우지 않았습니다/);
  await assert.rejects(prepareWiki(wiki, [wiki]), /평소 쓰는 Wiki와 같습니다/);
  assert.equal(await fs.readFile(path.join(usual, 'wiki.db'), 'utf8'), 'mine');
  // The same folder by another way to it (on Windows a junction, a mapped or subst drive, a short name): storage.json
  // keeps the Wiki's own path, the list may name it otherwise.
  await fs.writeFile(path.join(wiki, 'wiki.db'), 'used');
  const link = path.join(root, '바로 가기');
  await fs.symlink(wiki, link, 'junction');
  await assert.rejects(prepareWiki(link, [wiki]), /평소 쓰는 Wiki와 같습니다/);
  await assert.rejects(prepareWiki(wiki, [link]), /평소 쓰는 Wiki와 같습니다/);
  assert.equal(await fs.readFile(path.join(wiki, 'wiki.db'), 'utf8'), 'used');
  const empty = path.join(root, '빈 폴더');
  await fs.mkdir(empty);
  await prepareWiki(empty);
  assert.deepEqual(await fs.readdir(empty), [MARKER]);
});

test('each recording gets its own folder, and the newest one is found again for a new video', async (t) => {
  const root = await folder(t);
  const output = path.join(root, '시연 영상');
  const date = new Date(2026, 9, 5, 15, 30, 12);
  const first = await createRunFolder(output, date);
  const second = await createRunFolder(output, date);
  assert.equal(path.basename(first), '20261005-153012');
  assert.equal(path.basename(second), '20261005-153012-2');
  await fs.writeFile(path.join(first, FILES.raw), 'raw');
  const readList = async () => ({ output });
  // The second recording has no video of its own: the newest one with one is taken.
  assert.equal(await findRecording(output, { readList }), first);
  assert.equal(await findRecording(first, { readList }), first);
  const list = path.join(root, 'list.txt');
  await fs.writeFile(list, '');
  assert.equal(await findRecording(list, { readList }), first);
  await assert.rejects(
    findRecording(second, { readList: async () => ({ output: second }) }),
    /찾지 못했습니다/,
  );
  await assert.rejects(findRecording(path.join(root, 'none'), { readList }), /찾을 수 없습니다/);
});
