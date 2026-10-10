import { test, expect, type ElectronApplication } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  chooseStorage,
  connectLlm,
  drawer,
  launch,
  llmServer,
  results,
  searchBox,
  widget,
} from './helpers';

// About 19,000 characters: 1 플랫폼 설계 (1.1 배경, 1.2 캐시 전략, 1.3 운영) and 2 부록.
const paragraphs = (count: number, word: string) =>
  Array.from({ length: count }, (_, i) => `${word} 문단 ${i}: ${'내용 '.repeat(90).trim()}`).join(
    '\n\n',
  );
const LONG = [
  '# 플랫폼 설계',
  paragraphs(6, '개요'),
  '## 배경',
  paragraphs(14, '배경'),
  '## 캐시 전략',
  paragraphs(16, '전략'),
  'Redis 클러스터를 도입해 지연 시간을 30% 줄였습니다.',
  '## 운영',
  paragraphs(20, '운영'),
  '# 부록',
  paragraphs(10, '부록'),
].join('\n\n');

test('long documents: PageIndex analysis, AI-chosen sections and highlighted evidence', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-search-'));
  const llm = await llmServer(({ system, content }) =>
    system.includes('index the sections')
      ? {
          summaries: JSON.parse(content).sections.map((s: { id: string; title: string }) => ({
            id: s.id,
            summary: `${s.title} 섹션 요약`,
          })),
        }
      : system.includes('extract metadata')
        ? { summary: '플랫폼 설계 요약', keywords: ['platform', 'cache'], relations: [] }
        : system.includes('analyze a user')
          ? {
              intent: 'Redis 캐시 전략 찾기',
              terms: ['redis'],
              keywords: ['cache'],
              explanation: 'Redis 캐시 전략을 찾습니다.',
            }
          : system.includes('search agent')
            ? {
                thinking: '캐시 전략 섹션에 답이 있습니다.',
                results: [{ ref: 'D1', reason: 'Redis 도입 내용', sections: ['1.2'] }],
                next: null,
              }
            : { answer: 'Redis 클러스터를 도입했습니다. [1]', citations: [1] },
  );
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);

    // A long document written in the drawer is analyzed as a table of contents in bounded requests.
    await widget(page, 'documents').getByRole('button', { name: '새 문서' }).click();
    await drawer(page).getByLabel('문서 제목', { exact: true }).fill('플랫폼 설계서');
    await drawer(page).locator('textarea.document-editor').fill(LONG);
    await drawer(page)
      .getByLabel(/저장 후 LLM으로/)
      .check();
    await drawer(page).getByRole('button', { name: '문서 저장' }).click();
    const outline = drawer(page).getByRole('list', { name: '문서 목차 색인' });
    await expect(outline.getByRole('listitem')).toHaveCount(5);
    await expect(outline).toContainText('1.2캐시 전략');
    await expect(drawer(page).getByText(/12,000자가 넘는 문서는 목차 색인/)).toBeVisible();
    const summaries = llm.requests.filter((r) => r.system.includes('index the sections'));
    const extract = llm.requests.find((r) => r.system.includes('extract metadata'))!;
    expect(summaries.length).toBeGreaterThanOrEqual(2);
    expect(extract.content).toContain('목차와 섹션 요약');
    expect(extract.content).not.toContain(LONG);
    for (const request of llm.requests) expect(request.content.length).toBeLessThan(14_000);
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();
    await expect(widget(page, 'documents').locator('.index-badge')).toHaveText('목차 색인');

    // The search box: the AI search picks the document and its section from the outline, and the
    // answer reads that section only.
    await searchBox(page).fill('캐시는 어떻게 개선했어?');
    await searchBox(page).press('Enter');
    const card = results(page).locator('.result-card.selected');
    await expect(card).toContainText('Redis 도입 내용');
    await expect(card.getByLabel('AI가 고른 섹션')).toHaveText('플랫폼 설계 › 캐시 전략');
    await expect(card.locator('.result-snippets small').first()).toHaveText(
      '플랫폼 설계 › 캐시 전략',
    );
    await expect(card.locator('mark.search-hit').first()).toHaveText('Redis');
    const agent = JSON.parse(
      llm.requests.findLast((r) => r.system.includes('search agent'))!.content,
    );
    expect(agent.candidates[0].outline).toContain('1.2 캐시 전략 — 캐시 전략 섹션 요약');
    expect(agent.candidates[0].length).toBe(`${LONG.length.toLocaleString('en-US')}자`);
    await expect(results(page).locator('.answer')).toContainText('Redis 클러스터를 도입했습니다.');
    await results(page).locator('.citations summary').click();
    await expect(results(page).locator('.citation-sections')).toHaveText('1.2 캐시 전략');
    await expect(results(page).locator('.citations mark.search-hit').first()).toHaveText('Redis');
    const answer = JSON.parse(llm.requests.at(-1)!.content);
    expect(answer.documents[0].sections).toEqual(['1.2 캐시 전략']);
    expect(answer.documents[0].excerpt).toContain('Redis 클러스터');
    expect(answer.documents[0].excerpt).not.toContain('운영 문단');
    await page.screenshot({ path: 'test-results/pageindex-search.png', fullPage: true });

    // The document opened from the results marks the chosen section and the search words.
    await card.locator('.document-open').click();
    await expect(page.locator('.outline-list li.chosen')).toContainText('캐시 전략 섹션 요약');
    await expect(page.getByRole('group', { name: '검색어 위치 이동' })).toContainText('2곳');
    await page.screenshot({ path: 'test-results/pageindex-document.png', fullPage: true });
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

test('keywords search every document, whatever the next question is about, once an answer is written', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-keywords-'));
  // The answer about one document waits for the test.
  let release = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  const llm = await llmServer(async ({ system, content }) => {
    if (system.includes('extract metadata')) {
      const title = content.split('\n')[0].replace('제목: ', '');
      // An alias of a keyword the document does not have is left out.
      const aliases = { 캐시: ['cache'] };
      return { summary: `${title} 요약`, keywords: [title.split(' ')[0]], aliases, relations: [] };
    }
    if (!system.includes('Answer questions about')) return '연결 성공';
    await held;
    return { answer: '배포는 금요일입니다. [1]', citations: [1] };
  });
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);
    // Searches answer from the PC alone unless an answer is asked for.
    await page.evaluate(async () => {
      const request = window.privateAgent!.request;
      await request('preferences.save', { ai_answer: false });
      for (const [title, content] of [
        ['배포 계획', '배포는 금요일에 합니다.'],
        ['캐시 회의', '캐시를 도입하기로 했습니다.'],
      ]) {
        const saved = await request('wiki.save', { title, content });
        await request('wiki.analyze', { id: (saved.result as { id: string }).id });
      }
    });
    await page.reload();
    const rows = widget(page, 'documents').locator('.document-row');

    // A question about one document, its answer still being written.
    await rows.filter({ hasText: '배포 계획' }).click();
    await drawer(page).getByRole('button', { name: '이 문서에 질문' }).click();
    await searchBox(page).fill('배포는 언제야?');
    await searchBox(page).press('Enter');
    await expect(results(page).locator('.thinking')).toBeVisible();

    // A keyword waits for it: the window stays and says why.
    await rows.filter({ hasText: '캐시 회의' }).click();
    const keyword = drawer(page).getByRole('button', { name: '캐시', exact: true });
    await keyword.click();
    await expect(page.locator('.toast')).toContainText('AI가 앞의 질문에 답하고 있어');
    await expect(drawer(page).getByRole('heading', { name: '캐시 회의' })).toBeVisible();
    release();
    await expect(results(page).locator('.answer')).toContainText('배포는 금요일입니다.');

    // Then it searches every document, not the one the question box still points at.
    await keyword.click();
    await expect(drawer(page)).toHaveCount(0);
    await expect(results(page).getByRole('heading', { name: '캐시', exact: true })).toBeVisible();
    await expect(results(page).locator('.result-card')).toHaveCount(1);
    // A card's title is a heading, its button the one that opens the document.
    const title = results(page).getByRole('heading', { name: '캐시 회의', level: 3 });
    await expect(title.getByRole('button', { name: '캐시 회의' })).toBeVisible();
    await expect(
      page.getByRole('button', { name: '배포 계획 문서에 질문하기 끝내기' }),
    ).toBeVisible();
    expect(llm.requests.filter((r) => r.system.includes('Answer questions about'))).toHaveLength(1);

    // An English word finds the document by the alias of its Korean keyword, which the card names.
    await page.getByRole('button', { name: '배포 계획 문서에 질문하기 끝내기' }).click();
    await searchBox(page).fill('cache');
    await searchBox(page).press('Enter');
    await expect(results(page).locator('.result-card')).toHaveCount(1);
    await expect(results(page).locator('.result-card .tag.matched')).toHaveText('# 캐시 (cache)');
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
