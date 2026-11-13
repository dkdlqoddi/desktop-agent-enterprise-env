const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// src/lib/long-text.ts as CommonJS: it imports nothing, and its types go with the transpiler.
function load(file) {
  const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const module = { exports: {} };
  new Function('module', 'exports', 'require', outputText)(module, module.exports, require);
  return module.exports;
}
const {
  EDIT_MAX_CHARS,
  countWords,
  editParts,
  headings,
  layout,
  partNames,
  partStarts,
  piece,
  pieceAt,
  sectionHeading,
  stepWord,
  wordNumber,
} = load('src/lib/long-text.ts');

// A chapter of Markdown about `size` characters long: a heading, paragraphs, a list and a table.
function chapter(number, size = 900) {
  const words = '배포 일정 검토 회의 캐시 전략 release cache design review'.split(' ');
  const paragraph = (seed) =>
    Array.from({ length: 30 }, (_, i) => words[(seed + i * 7) % words.length]).join(' ') + '.';
  const blocks = [`## 장 ${number}`];
  while (blocks.join('\n\n').length < size)
    blocks.push(paragraph(blocks.length), '- 하나\n- 둘', '| 칸 | 값 |\n| --- | --- |\n| a | 1 |');
  return blocks.join('\n\n');
}
const book = (chapters, size) =>
  Array.from({ length: chapters }, (_, i) => chapter(i, size)).join('\n\n');

test('a text up to the whole size is one piece, and a longer one pieces that are the text', () => {
  const short = book(3);
  assert.deepEqual(layout(short, short.length), {
    starts: [0],
    ends: [short.length],
    shared: '',
  });
  const text = book(60);
  const plan = layout(text, 1000, 5000);
  assert.ok(plan.starts.length > 5, `${plan.starts.length} pieces`);
  assert.equal(plan.starts[0], 0);
  assert.equal(plan.ends.at(-1), text.length);
  plan.starts.slice(1).forEach((start, i) => {
    // The line break between two pieces is in neither, after a blank line, and before a line at the margin.
    assert.equal(plan.ends[i] + 1, start);
    assert.equal(text[plan.ends[i]], '\n');
    assert.equal(text[plan.ends[i] - 1], '\n');
    assert.match(text[start], /\S/);
    assert.ok(plan.ends[i] - plan.starts[i] >= 5000 - 1);
  });
  assert.equal(
    plan.starts.map((_, i) => piece(text, plan, i)).join('\n'),
    text,
    'the pieces together are the text',
  );
});

test('a piece never ends inside fenced code, and link definitions go with every piece', () => {
  const code = ['```', 'first', '', 'second line at the margin', '', 'third', '```'].join('\n');
  const text = [
    'a'.repeat(60),
    '',
    code,
    '',
    '[site]: https://example.com',
    '',
    'b'.repeat(60),
    '',
    'c'.repeat(60),
  ].join('\n');
  const plan = layout(text, 10, 20);
  for (const end of plan.ends.slice(0, -1)) {
    const before = text.slice(0, end);
    const fences = before.split('\n').filter((line) => line.startsWith('```')).length;
    assert.equal(fences % 2, 0, `cut at ${end} inside code`);
  }
  assert.equal(plan.shared, '\n\n[site]: https://example.com\n');
  assert.ok(piece(text, plan, plan.starts.length - 1).endsWith(plan.shared));
  // One piece needs no definitions of its own.
  assert.equal(layout(text, text.length).shared, '');
});

test('the piece of an offset', () => {
  const starts = [0, 10, 25, 40];
  assert.equal(pieceAt(starts, 0), 0);
  assert.equal(pieceAt(starts, 9), 0);
  assert.equal(pieceAt(starts, 10), 1);
  assert.equal(pieceAt(starts, 39), 2);
  assert.equal(pieceAt(starts, 400), 3);
  assert.equal(pieceAt([0], 5), 0);
});

test('the search words of each piece are counted in what Markdown shows', () => {
  // As the window's highlighter writes it: a space of a search word stands for any white space.
  const pattern = /캐시\s+전략|redis/giu;
  const text = [
    'Redis와 **캐시 전략**을 정했습니다.',
    '',
    '[문서](https://example.com/redis "redis") ![redis 그림](redis.png) <https://example.com/redis>',
    '',
    '[redis]: https://example.com/redis',
    '',
    '`redis` 코드와 각주[^1]',
    '',
    '[^1]: redis 각주',
    '',
    '캐시',
    '전략은 다음 문단입니다.',
  ].join('\n');
  // Not counted: the address and title of a link, a picture, a link definition.
  assert.deepEqual(countWords(text, layout(text), pattern), [6]);
  const first = text.split('\n')[0];
  const plan = { starts: [0, first.length + 2], ends: [first.length, text.length], shared: '' };
  assert.deepEqual(countWords(text, plan, pattern), [2, 4]);
});

test('the next and previous search word go across pieces and around', () => {
  const counts = [2, 0, 1, 0, 3];
  assert.deepEqual(stepWord(counts, null, 1), { piece: 0, index: 0 });
  assert.deepEqual(stepWord(counts, null, -1), { piece: 4, index: 2 });
  assert.deepEqual(stepWord(counts, { piece: 0, index: 0 }, 1), { piece: 0, index: 1 });
  assert.deepEqual(stepWord(counts, { piece: 0, index: 1 }, 1), { piece: 2, index: 0 });
  assert.deepEqual(stepWord(counts, { piece: 2, index: 0 }, -1), { piece: 0, index: 1 });
  assert.deepEqual(stepWord(counts, { piece: 4, index: 2 }, 1), { piece: 0, index: 0 });
  assert.deepEqual(stepWord(counts, { piece: 0, index: 0 }, -1), { piece: 4, index: 2 });
  // From a piece whose words turned out to be none: on to the next one with any.
  assert.deepEqual(stepWord([0, 0, 1], { piece: 1, index: -1 }, 1), { piece: 2, index: 0 });
  assert.deepEqual(stepWord([2, 0, 0], { piece: 1, index: 0 }, -1), { piece: 0, index: 1 });
  // One word: around to itself.
  assert.deepEqual(stepWord([0, 1], { piece: 1, index: 0 }, 1), { piece: 1, index: 0 });
  assert.equal(stepWord([0, 0], null, 1), null);
  assert.equal(stepWord([], null, 1), null);
  assert.equal(wordNumber(counts, { piece: 4, index: 1 }), 4);
  assert.equal(wordNumber(counts, { piece: 0, index: 0 }), 0);
});

test('headings are read as the table of contents reads them', () => {
  const text = [
    '# 개요',
    '',
    '```',
    '# not a heading',
    '```',
    '',
    '    # indented four spaces: code',
    '   ## **캐시** [전략](https://example.com) \\#1 ##',
    '#no space',
    '##',
    '## **',
    '###### 여섯 #없음',
    '####### seven',
    '~~~~',
    '## inside tildes',
    '~~~',
    '## still inside',
    '~~~~~',
    '## 맺음말 ###   ',
    '',
    `## ${'긴 제목 '.repeat(40)}`,
  ].join('\r\n');
  const found = headings(text);
  assert.deepEqual(
    found.map((item) => item.title),
    [
      '개요',
      '캐시 전략 #1',
      '여섯 #없음',
      '맺음말',
      // At most 120 characters, cut where they end.
      '긴 제목 '.repeat(40).trim().slice(0, 120),
    ],
  );
  for (const item of found) assert.match(text.slice(item.offset), /^ {0,3}#/);
  // The table of contents' position finds its heading; a title elsewhere is found by its title.
  assert.equal(sectionHeading(found, { heading: 1, title: '캐시 전략 #1' }), found[1]);
  assert.equal(sectionHeading(found, { heading: 0, title: '맺음말' }), found[3]);
  assert.equal(sectionHeading(found, { heading: 9, title: '없는 제목' }), undefined);
  assert.equal(sectionHeading(found, { heading: null, title: '개요' }), found[0]);
});

const sum = (lengths) => lengths.reduce((total, length) => total + length, 0);

test('a long text is edited in parts that together are the text, cut where it is drawn in pieces', () => {
  assert.equal(editParts(book(10)), null);
  const text = book(300);
  const lengths = editParts(text);
  assert.ok(lengths.length > 3, `${lengths.length} parts`);
  assert.equal(sum(lengths), text.length);
  partStarts(lengths)
    .slice(1)
    .forEach((start) => {
      assert.equal(text.slice(start - 2, start), '\n\n');
      assert.match(text[start], /\S/);
    });
  for (const length of lengths) assert.ok(length <= EDIT_MAX_CHARS);
  assert.deepEqual(partStarts([3, 4, 5]), [0, 3, 7]);
});

test('a part with no blank line to end at ends at a line break, or between two whole characters', () => {
  const lines = Array.from({ length: 4000 }, (_, i) => `줄 ${i} ${'가'.repeat(60)}`).join('\n');
  const lengths = editParts(lines);
  assert.equal(sum(lengths), lines.length);
  for (const length of lengths) assert.ok(length <= EDIT_MAX_CHARS);
  for (const start of partStarts(lengths).slice(1)) assert.equal(lines[start - 1], '\n');
  const emoji = '😀'.repeat(150_000);
  const cut = editParts(emoji);
  assert.ok(cut.length >= 3);
  assert.equal(sum(cut), emoji.length);
  for (const start of partStarts(cut)) {
    const code = emoji.charCodeAt(start);
    assert.ok(code < 0xdc00 || code > 0xdfff, `part at ${start} begins inside a character`);
  }
});

test('the parts are named by their headings', () => {
  const parts = ['# 하나\n\n가나다\n\n', '## 둘\n\n라마\n\n', '본문\n\n', '## 셋\n\n바사'];
  const text = parts.join('');
  const lengths = parts.map((part) => part.length);
  assert.deepEqual(partNames(text, lengths), [
    '1/4 · 하나',
    '2/4 · 둘',
    '3/4 · 둘 (이어서)',
    '4/4 · 셋',
  ]);
  assert.deepEqual(partNames(text, [lengths[0] + lengths[1], lengths[2] + lengths[3]]), [
    '1/2 · 하나 ~ 둘',
    '2/2 · 둘 (이어서) ~ 셋',
  ]);
  assert.deepEqual(partNames('  첫 줄\n둘째 줄', [10]), ['1/1 · 첫 줄']);
  assert.deepEqual(partNames('본문\n\n   \n', [4, 5]), ['1/2 · 본문', '2/2 · 빈 부분']);
  const title = '아주 긴 제목 '.repeat(10).trim();
  assert.equal(
    partNames(`# ${title}`, [title.length + 2])[0],
    `1/1 · ${[...title].slice(0, 39).join('')}…`,
  );
});
