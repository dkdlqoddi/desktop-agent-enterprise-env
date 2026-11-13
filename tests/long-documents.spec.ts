import AxeBuilder from '@axe-core/playwright';
import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  chooseStorage,
  drawer,
  launch,
  openDashboard,
  results,
  searchBox,
  widget,
} from './helpers';

// Long documents as a Wiki holds them, written while the app is closed: chapters of headings, paragraphs, lists,
// tables and code, as Markdown or as an imported HTML page with the Markdown its text is searched and edited as, and
// their PageIndex table of contents (a section a chapter). `words` puts a word at the end of a chapter's first
// paragraph (chapter -1: the last), `links` a link whose address holds a word.
type Seeded = {
  id: string;
  title: string;
  size: number;
  html?: boolean;
  words?: [number, string][];
  links?: [number, string][];
};
const SEED = `
import json, random, sys
sys.path.insert(0, "backend")
from agent import pageindex
from agent.db import Database, index_text
from agent.html_document import page
from html import escape

WORDS = ("배포 일정 검토 회의 캐시 전략 설계 운영 장애 대응 지표 서버 클러스터 데이터 저장소 요청 응답 지연 시간 개선 "
         "결과 보고 정리 목표 범위 위험 계획 담당 확인 기록 분석 release cache cluster latency request response "
         "design review metric storage queue worker index search token").split()
stamp = "2026-10-01T00:00:00+00:00"

def words(rng, count):
    return " ".join(rng.choice(WORDS) for _ in range(count))

def chapters(rng, size):
    """(markdown, html) chapters until the Markdown holds about size characters."""
    md, html, length, number = [], [], 0, 0
    while length < size:
        number += 1
        title = f"제{number}장 {words(rng, 2)}"
        opening = words(rng, 80)
        text, body = [f"# {title}", opening + "."], [f"<h1>{escape(title)}</h1><p>{opening}.</p>"]
        for section in range(1, 4):
            heading = f"{number}.{section} {words(rng, 2)}"
            items = [words(rng, 8) for _ in range(5)]
            rows = [[words(rng, 1), str(rng.randint(1, 999)), words(rng, 5)] for _ in range(6)]
            code = [f"def step_{number}_{section}_{i}(value):" for i in range(3)]
            paragraph, closing = words(rng, 120) + ".", words(rng, 60) + "."
            text += [f"## {heading}", paragraph, "\\n".join(f"- {item}" for item in items),
                     "| 항목 | 값 | 설명 |\\n| --- | --- | --- |\\n" + "\\n".join(f"| {a} | {b} | {c} |" for a, b, c in rows),
                     "\`\`\`python\\n" + "\\n    return value\\n".join(code) + "\\n    return value\\n\`\`\`", closing]
            body.append(
                f"<h2>{escape(heading)}</h2><p>{paragraph}</p><ul>" + "".join(f"<li>{item}</li>" for item in items)
                + "</ul><table><tr><th>항목</th><th>값</th><th>설명</th></tr>"
                + "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in rows)
                + "</table><pre>" + escape("\\n    return value\\n".join(code)) + "\\n    return value</pre>"
                + f"<p>{closing}</p>")
        chapter = "\\n\\n".join(text)
        md.append(chapter)
        html.append("".join(body))
        length += len(chapter) + 2
    return md, html

with Database(sys.argv[1]).connect() as db:
    for spec in json.loads(sys.argv[2]):
        md, html = chapters(random.Random(spec["id"]), spec["size"])
        for chapter, word in spec.get("words", []):
            at = chapter - 1 if chapter > 0 else len(md) + chapter
            md[at] = md[at].replace(".\\n\\n## ", f" {word}.\\n\\n## ", 1)
            html[at] = html[at].replace(".</p>", f" {word}.</p>", 1)
        for chapter, word in spec.get("links", []):
            at = chapter - 1 if chapter > 0 else len(md) + chapter
            md[at] = md[at].replace(".\\n\\n## ", f" [참고](https://example.com/{word}).\\n\\n## ", 1)
        content = "\\n\\n".join(md)
        kind = "html" if spec.get("html") else "markdown"
        db.execute(
            "INSERT INTO documents(id,title,content,format,status,index_mode,created_at,updated_at) "
            "VALUES(?,?,?,?,'ready','pageindex',?,?)", (spec["id"], spec["title"], content, kind, stamp, stamp))
        if kind == "html":
            db.execute("INSERT INTO document_html(doc_id,html) VALUES(?,?)",
                       (spec["id"], page(spec["title"], "".join(html), "word")))
        nodes = pageindex.extractive(content, pageindex.build(content))
        pageindex.save(db, spec["id"], 1, nodes)
        index_text(db, [spec["id"]])
        print(spec["id"], len(content), len(nodes), flush=True)
`;

// Starts the app on a new Wiki, closes it and writes `documents` into the Wiki: what the seed printed.
async function seed(directory: string, documents: Seeded[]) {
  const wiki = path.join(directory, 'wiki');
  const app = await launch(directory);
  await chooseStorage(await app.firstWindow(), wiki);
  await app.close();
  const python = path.resolve(
    'backend',
    '.venv',
    process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
  );
  const seeded = spawnSync(
    python,
    ['-c', SEED, path.join(wiki, 'wiki.db'), JSON.stringify(documents)],
    { encoding: 'utf8', env: { ...process.env, PYTHONPATH: '' }, maxBuffer: 1 << 24 },
  );
  if (seeded.status !== 0) throw new Error(seeded.stderr);
  return seeded.stdout.trim();
}

type Doc = Seeded & { word: string };
const DOCS: Doc[] = [
  { id: 'md2', title: '긴 문서 이백만', size: 2_000_000, word: '끝자락이백' },
  { id: 'md8', title: '긴 문서 팔백만', size: 8_000_000, word: '끝자락팔백' },
  { id: 'html2', title: '긴 HTML 문서', size: 2_000_000, html: true, word: '끝자락웹문서' },
].map((doc) => ({ ...doc, words: [[-1, doc.word]] }));

// The resident memory of the main window's renderer, in MB.
const rendererMemory = (app: ElectronApplication) =>
  app.evaluate(({ app, webContents }) => {
    const view = webContents.getAllWebContents().find((item) => item.getURL().startsWith('agent:'));
    const pid = view?.getOSProcessId();
    const metric = app.getAppMetrics().find((item) => item.pid === pid);
    return metric ? Math.round(metric.memory.workingSetSize / 1024) : 0;
  });

// Runs `step` while the renderer's memory is sampled twice a second: its time and the largest memory seen.
async function measured<T>(app: ElectronApplication, step: () => Promise<T>) {
  let peak = await rendererMemory(app);
  let sampling = true;
  const sampler = (async () => {
    while (sampling) {
      peak = Math.max(peak, await rendererMemory(app).catch(() => 0));
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  })();
  const started = Date.now();
  try {
    const value = await step();
    return { ms: Date.now() - started, peak, value };
  } finally {
    sampling = false;
    await sampler;
    peak = Math.max(peak, await rendererMemory(app).catch(() => 0));
  }
}

// What a step clicks: the nth element `selector` matches, or the one whose text is `text`.
type Find = { selector: string; index?: number; text?: string };
// What a step waits for: an element `selector` matches whose text holds `text`, present or in the document's view.
type Until = { selector: string; text: string; view: boolean };

// In the page (whose policy runs no code made of strings): finds an element in the page and in the HTML pages it shows
// (shadow roots), as `until` says, and whether it is in the document's scrolled view.
function lookUp(until: Until) {
  const all = [
    ...document.querySelectorAll(until.selector),
    ...[...document.querySelectorAll('[data-html-document]')].flatMap((host) => [
      ...(host.shadowRoot?.querySelectorAll(until.selector) ?? []),
    ]),
  ];
  const found = all.find((item) => (item.textContent ?? '').includes(until.text));
  if (!found || !until.view) return !!found;
  const view = document.querySelector('.document-content');
  if (!view) return false;
  const box = view.getBoundingClientRect();
  const at = found.getBoundingClientRect();
  return at.bottom > box.top && at.top < box.bottom;
}

// Clicks the element `find` names and waits until `until` holds, checked every frame: the time from the click until the
// frame after that is drawn, in milliseconds, by the page's clock.
async function clickUntil(page: Page, find: Find, until: Until, timeout: number) {
  type Clock = { clicked: number };
  await page.evaluate((find) => {
    const candidates = [...document.querySelectorAll<HTMLElement>(find.selector)];
    const element =
      find.text === undefined
        ? candidates.at(find.index ?? 0)
        : candidates.find((item) => item.textContent?.trim() === find.text);
    if (!element) throw new Error(`not found: ${JSON.stringify(find)}`);
    (window as unknown as Clock).clicked = performance.now();
    element.click();
  }, find);
  await page.waitForFunction(lookUp, until, { timeout, polling: 'raf' });
  return page.evaluate(async () => {
    // A frame's callbacks come before its layout: the second one comes once the first frame is drawn.
    const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
    await frame();
    await frame();
    return Math.round(performance.now() - (window as unknown as Clock).clicked);
  });
}

// The critical and serious findings of axe on the window, which tests/a11y.spec.ts keeps out of the app's screens.
async function blocking(page: Page) {
  const { violations } = await new AxeBuilder({ page }).setLegacyMode(true).analyze();
  return violations
    .filter((violation) => ['critical', 'serious'].includes(violation.impact ?? ''))
    .map((violation) => violation.id);
}

// Waits until `until` holds.
const waitUntil = (page: Page, until: Until, timeout: number) =>
  page.waitForFunction(lookUp, until, { timeout });

test('long documents: a search word or a section far down shows at once, and the text around it as it is read', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'long-documents-'));
  const title = '긴 보고서';
  const word = '표식단어';
  let app: ElectronApplication | undefined;
  try {
    // About 1.2 million characters, 30 pieces: the word in the first chapter, the middle one and the last, and in
    // the address of a link, which the window does not show.
    await seed(directory, [
      {
        id: 'long',
        title,
        size: 1_200_000,
        words: [
          [1, word],
          [130, word],
          [-1, word],
        ],
        links: [[60, word]],
      },
    ]);
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await openDashboard(page);
    await searchBox(page).fill(word);
    await searchBox(page).press('Enter');
    await results(page)
      .locator('.result-card')
      .filter({ hasText: title })
      .locator('.document-open')
      .click();
    const view = drawer(page).locator('.document-content');
    const parts = view.locator('.long-document-part');
    const waiting = view.locator('.long-document-part[data-pending]');
    const nav = page.getByRole('group', { name: '검색어 위치 이동' });
    const drawnParts = async () => (await parts.count()) - (await waiting.count());

    // The words are counted in the text before it is drawn, where the window opens only its beginning.
    await expect(nav).toContainText('3곳');
    const total = await parts.count();
    expect(total).toBeGreaterThan(25);
    expect(await drawnParts()).toBeLessThanOrEqual(2);
    await expect(view.getByRole('status')).toHaveText(/긴 문서는 읽는 자리 근처만 표시합니다/);
    await expect(waiting.last()).toHaveText(
      `긴 문서의 ${total}/${total}번째 부분입니다. 이 자리에 오면 표시합니다.`,
    );
    // The window with its empty places reads as the rest of the app does.
    expect(await blocking(page)).toEqual([]);

    // ↓ shows the first word, then the one in the middle, drawn for it; ↑ goes back, and around to the last.
    const current = view.locator('mark.search-hit.current');
    const step = async (name: string, shown: string) => {
      await nav.getByRole('button', { name }).click();
      await expect(nav).toContainText(shown);
      await expect(current).toHaveCount(1);
      await expect(current).toHaveText(word);
      await expect(current).toBeInViewport();
    };
    await step('다음 검색어 위치', '1/3곳');
    await step('다음 검색어 위치', '2/3곳');
    await step('이전 검색어 위치', '1/3곳');
    await step('이전 검색어 위치', '3/3곳');
    await step('다음 검색어 위치', '1/3곳');
    expect(await drawnParts()).toBeLessThan(12);

    // The last section of the table of contents: its heading at once.
    const outline = drawer(page).getByRole('list', { name: '문서 목차 색인' });
    const last = outline.getByRole('button').last();
    const chapter = await last.evaluate((button) => button.childNodes[1]?.textContent ?? '');
    expect(chapter).toMatch(/^제\d+장 /);
    await last.click();
    const heading = view.getByRole('heading', { name: chapter, exact: true });
    await expect(heading).toBeInViewport();
    // Going up, the piece above is drawn where it was an empty box, and what the reader sees stays where it is.
    const before = await heading.evaluate((element) => {
      const scroll = element.closest('.document-content')!;
      const part = element.closest('.long-document-part')!;
      scroll.scrollTop +=
        part.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 100;
      return element.getBoundingClientRect().top;
    });
    await expect
      .poll(() =>
        heading.evaluate(
          (element) =>
            !element
              .closest('.long-document-part')!
              .previousElementSibling!.hasAttribute('data-pending'),
        ),
      )
      .toBe(true);
    const after = await heading.evaluate((element) => element.getBoundingClientRect().top);
    expect(Math.abs(after - before)).toBeLessThan(2);

    // Read from the beginning to the end, the text is drawn only around the reader: about a million characters.
    let most = 0;
    for (let index = 0; index < total; index++) {
      await parts.nth(index).evaluate((element) => element.scrollIntoView({ block: 'start' }));
      await expect(parts.nth(index)).not.toHaveAttribute('data-pending');
      most = Math.max(most, await drawnParts());
    }
    expect(most).toBeLessThanOrEqual(27);
    expect(await drawnParts()).toBeLessThan(total);

    // 전체 표시 draws all of it, and the note goes.
    await view.getByRole('button', { name: '전체 표시' }).click();
    await expect(waiting).toHaveCount(0, { timeout: 60_000 });
    await expect(view.locator('.long-document-more')).toHaveCount(0);
    await expect(view.locator('h1')).toHaveCount(Number(chapter.match(/\d+/)![0]));
    await expect(view.locator('mark.search-hit')).toHaveCount(3);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

test('long documents: a long text is edited a part at a time and saved whole, as a change made elsewhere allows', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'long-documents-'));
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    type Saved = { id: string; content: string; revision: number };
    const request = async <T>(method: string, params: Record<string, unknown>) => {
      const response = await page.evaluate(
        ([name, values]) => window.privateAgent!.request(name, values),
        [method, params] as const,
      );
      expect(response.ok).toBe(true);
      return response.result as T;
    };
    // About 200,000 characters in five parts: 400 sections, each with a line of its own.
    const text = Array.from(
      { length: 400 },
      (_, i) => `## 절 ${i}\n\n줄 ${i}: ${'본문 '.repeat(160).trim()}`,
    ).join('\n\n');
    const { id } = await request<Saved>('wiki.save', { title: '긴 계획', content: text });
    // Another window or an AI tool saves the document while its editor is open here.
    const elsewhere = async (change: (content: string) => string) => {
      const { content, revision } = await request<Saved>('wiki.get', { id });
      await request('wiki.save', {
        id,
        title: '긴 계획',
        content: change(content),
        expected_revision: revision,
      });
    };
    await page.reload();
    await openDashboard(page);
    await widget(page, 'documents').locator('.document-row').filter({ hasText: '긴 계획' }).click();
    const panel = drawer(page);
    const view = panel.locator('.document-content');
    const editor = panel.locator('textarea.document-editor');
    const pick = panel.getByLabel('고칠 부분');
    const toast = (words: string) => page.locator('.toast').filter({ hasText: words });

    // Read in the middle, then 수정: the editor holds the part being read, not the whole text.
    await view.evaluate((element) => element.scrollTo(0, element.scrollHeight / 2));
    // The first heading of the first drawn piece in the view, once one is drawn there.
    const reading = () =>
      view.evaluate((element) => {
        const { top, bottom } = element.getBoundingClientRect();
        const part = [...element.querySelectorAll('.long-document-part:not([data-pending])')].find(
          (item) => {
            const box = item.getBoundingClientRect();
            return box.bottom > top && box.top < bottom;
          },
        );
        return part?.querySelector('h2')?.textContent ?? '';
      });
    await expect.poll(reading).toMatch(/^절 \d+$/);
    const read = await reading();
    expect(read).not.toBe('절 0');
    await panel.getByRole('button', { name: '수정', exact: true }).click();
    const parts = await pick.locator('option').count();
    expect(parts).toBe(5);
    await expect(editor).toHaveValue(new RegExp(`## ${read}\\n`));
    const shown = await editor.inputValue();
    expect(shown.length).toBeLessThan(100_000);
    // The size of a document holds for the whole text, whatever part is written.
    await expect(editor).toHaveAttribute(
      'maxlength',
      String(8_000_000 - text.length + shown.length),
    );
    await expect(panel.getByText(`${text.length.toLocaleString()} / 8,000,000자`)).toBeVisible();
    expect(await blocking(page)).toEqual([]);

    // Changes in three parts: the first, the start of the second (the keyboard goes there with 다음 부분) and the last.
    await pick.selectOption({ index: 0 });
    await editor.fill((await editor.inputValue()).replace('줄 0:', '고친 줄 0:'));
    await panel.getByRole('button', { name: '다음 부분' }).click();
    await expect(pick).toHaveValue('1');
    await expect(editor).toBeFocused();
    const second = (await editor.inputValue()).slice(0, 40);
    await page.keyboard.insertText('앞에 쓴 글\n\n');
    await pick.selectOption({ index: parts - 1 });
    await editor.fill((await editor.inputValue()).replace('줄 399:', '고친 줄 399:'));
    // Each part keeps what was written in it.
    await pick.selectOption({ index: 0 });
    await expect(editor).toHaveValue(/^## 절 0\n\n고친 줄 0:/);
    await pick.selectOption({ index: 1 });
    await expect(editor).toHaveValue(new RegExp(`^앞에 쓴 글\\n\\n`));

    // A change made elsewhere on another line meanwhile: the save merges the two, the whole text with it.
    await elsewhere((content) => content.replace('줄 200:', '다른 곳의 줄 200:'));
    await panel.getByRole('button', { name: '문서 저장' }).click();
    await expect(toast('다른 곳에서 바뀐 줄과 내 수정을 합쳐 저장했습니다.')).toBeVisible();
    const merged = text
      .replace('줄 0:', '고친 줄 0:')
      .replace(second, `앞에 쓴 글\n\n${second}`)
      .replace('줄 399:', '고친 줄 399:')
      .replace('줄 200:', '다른 곳의 줄 200:');
    expect((await request<Saved>('wiki.get', { id })).content).toBe(merged);
    await expect(view).toContainText('고친 줄 0:');

    // The same line changed on both sides: compared as before, and 내 수정 유지하고 다시 열기 goes on in the part that
    // was shown, the writing kept.
    await panel.getByRole('button', { name: '수정', exact: true }).click();
    await pick.selectOption({ index: 3 });
    const line = /줄 (\d+):/.exec(await editor.inputValue())![1];
    await editor.fill((await editor.inputValue()).replace(`줄 ${line}:`, `내 줄 ${line}:`));
    await elsewhere((content) => content.replace(`줄 ${line}:`, `남의 줄 ${line}:`));
    await panel.getByRole('button', { name: '문서 저장' }).click();
    await expect(panel.getByRole('heading', { name: '수정 충돌' })).toBeVisible();
    await panel.getByRole('button', { name: '내 수정 유지하고 다시 열기' }).click();
    await expect(pick).toHaveValue('3');
    await expect(editor).toHaveValue(new RegExp(`내 줄 ${line}:`));
    await panel.getByRole('button', { name: '문서 저장' }).click();
    await expect(toast('문서를 저장했습니다.')).toBeVisible();
    const saved = (await request<Saved>('wiki.get', { id })).content;
    expect(saved).toBe(merged.replace(`줄 ${line}:`, `내 줄 ${line}:`));
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

test('long documents: measured (PRIVATE_AGENT_PERF=1)', async () => {
  test.skip(
    process.env.PRIVATE_AGENT_PERF !== '1',
    'a measurement: set PRIVATE_AGENT_PERF=1 to run it',
  );
  test.setTimeout(90 * 60_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'long-documents-'));
  const only = (process.env.PRIVATE_AGENT_PERF_DOCS ?? '').split(',').filter(Boolean);
  const docs = only.length ? DOCS.filter((doc) => only.includes(doc.id)) : DOCS;
  // The steps to take (open, search, outline, edit), all unless PRIVATE_AGENT_PERF_STEPS names some.
  const steps = (process.env.PRIVATE_AGENT_PERF_STEPS ?? 'open,search,outline,edit').split(',');
  let app: ElectronApplication | undefined;
  const report: string[] = [];
  const note = (line: string) => {
    report.push(line);
    console.log(line);
  };
  try {
    note(`seeded: ${(await seed(directory, docs)).replace(/\n/g, '; ')}`);

    // Each step in an app of its own, so that what one drew does not count for the next.
    const fresh = async () => {
      await app?.close();
      app = await launch(directory);
      const page = await app.firstWindow();
      await openDashboard(page, 60_000);
      return { app, page };
    };
    const openRow = (page: Page, doc: Doc) =>
      widget(page, 'documents').locator('.document-row').filter({ hasText: doc.title }).click();
    const minute = 60_000;
    for (const doc of docs) {
      // The first chapter's heading, in view once the document shows its beginning.
      const start: Until = { selector: 'h1', text: '제1장', view: true };
      // Opened from the list: the beginning of the text.
      if (steps.includes('open')) {
        const { app, page } = await fresh();
        const opened = await measured(app, async () => {
          await openRow(page, doc);
          await waitUntil(page, start, 10 * minute);
        });
        note(`${doc.id} open from list: ${opened.ms} ms, renderer ${opened.peak} MB`);
      }
      // Opened from a search hit near the end: the count of the word shown, then its place.
      if (steps.includes('search')) {
        const { app, page } = await fresh();
        await searchBox(page).fill(doc.word);
        await searchBox(page).press('Enter');
        const card = results(page).locator('.result-card').filter({ hasText: doc.title });
        await card.waitFor({ timeout: minute });
        const index = await results(page)
          .locator('.result-card')
          .evaluateAll(
            (cards, title) => cards.findIndex((card) => card.textContent?.includes(title)),
            doc.title,
          );
        const counted = await measured(app, () =>
          clickUntil(
            page,
            { selector: '.result-card .document-open', index },
            { selector: '.match-nav', text: '1곳', view: false },
            30 * minute,
          ),
        );
        const shown = await measured(app, () =>
          clickUntil(
            page,
            { selector: '.match-nav button[aria-label="다음 검색어 위치"]' },
            { selector: 'mark.search-hit.current', text: doc.word, view: true },
            30 * minute,
          ),
        );
        note(
          `${doc.id} search hit: counted ${counted.value} ms (renderer ${counted.peak} MB), ` +
            `shown ${shown.value} ms more (renderer ${shown.peak} MB)`,
        );
      }
      // The last section of the table of contents.
      if (steps.includes('outline')) {
        const { app, page } = await fresh();
        await openRow(page, doc);
        const outline = drawer(page).getByRole('list', { name: '문서 목차 색인' });
        await outline.waitFor({ timeout: 10 * minute });
        await waitUntil(page, start, 10 * minute);
        const sections = await outline
          .locator('button:not([disabled])')
          .evaluateAll((buttons) => buttons.map((button) => button.textContent ?? ''));
        const last = sections.length - 1;
        const title = await outline
          .locator('button:not([disabled])')
          .nth(last)
          .evaluate((button) => button.childNodes[1]?.textContent ?? '');
        const jumped = await measured(app, () =>
          clickUntil(
            page,
            { selector: '.outline-list button:not([disabled])', index: last },
            { selector: 'h1, h2, h3, h4, h5, h6', text: title, view: true },
            30 * minute,
          ),
        );
        note(
          `${doc.id} last of ${sections.length} sections (${title}): ${jumped.value} ms, renderer ${jumped.peak} MB`,
        );
      }
      // The editor, and a character typed at the end of what it holds (each one drawn before the next).
      if (steps.includes('edit')) {
        const { app, page } = await fresh();
        await openRow(page, doc);
        await waitUntil(page, start, 10 * minute);
        const edited = await measured(app, () =>
          clickUntil(
            page,
            { selector: 'dialog button', text: '수정' },
            { selector: 'textarea.document-editor', text: '', view: false },
            10 * minute,
          ),
        );
        const typed = await page.evaluate(async () => {
          const box = document.querySelector<HTMLTextAreaElement>('textarea.document-editor')!;
          box.focus();
          box.setSelectionRange(box.value.length, box.value.length);
          const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
          const started = performance.now();
          for (let count = 0; count < 10; count++) {
            document.execCommand('insertText', false, '가');
            await frame();
          }
          await frame();
          return Math.round((performance.now() - started) / 10);
        });
        note(
          `${doc.id} editor: ${edited.value} ms, renderer ${edited.peak} MB; a character typed ${typed} ms`,
        );
      }
    }
  } finally {
    console.log(`\n${report.join('\n')}`);
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
