import AxeBuilder from '@axe-core/playwright';
import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  chooseStorage,
  connectLlm,
  drawer,
  goHome,
  launch,
  llmServer,
  openAdmin,
  openLibrary,
  results,
  searchBox,
  widget,
} from './helpers';

type Finding = { screen: string; rule: string; impact: string; help: string; targets: string[] };

// A 1×1 red PNG: imported, it becomes an HTML page drawn as paper in the document window.
const PICTURE =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==';

// The gate: axe's critical and serious findings fail it; moderate and minor ones are left to review.
const BLOCKING = ['critical', 'serious'];

async function audit(page: Page, screen: string, found: Finding[]) {
  // Nothing may still be loading, so the screen is measured as the user reads it.
  await expect(page.locator('.widget-loading, .loading-page')).toHaveCount(0);
  const { violations } = await new AxeBuilder({ page })
    // Electron has no second page to finish axe's run in (context.newPage), so it runs in one go in the window.
    .setLegacyMode(true)
    .analyze();
  for (const violation of violations)
    if (BLOCKING.includes(violation.impact ?? ''))
      found.push({
        screen,
        rule: violation.id,
        impact: violation.impact!,
        help: violation.help,
        targets: violation.nodes.slice(0, 6).map((node) => node.target.join(' ')),
      });
}

async function request<T>(page: Page, method: string, params: Record<string, unknown> = {}) {
  const response = await page.evaluate(
    ([name, values]) => window.privateAgent!.request(name, values),
    [method, params] as const,
  );
  expect(response.ok, `${method}: ${JSON.stringify(response)}`).toBe(true);
  return response.result as T;
}

const nav = (page: Page, name: string) =>
  page.getByRole('navigation', { name: '화면' }).getByRole('button', { name, exact: true });

// Every main screen once, each measured by axe: the dashboard and its editor, tasks, the library with a document open,
// the AI and activity pages, each admin section and a search with its answer.
async function visit(page: Page, theme: string, found: Finding[]) {
  const screen = (name: string) => `${theme} · ${name}`;
  await goHome(page);
  await expect(widget(page, 'documents').locator('.document-row')).toHaveCount(4);
  await audit(page, screen('홈'), found);
  await page.getByRole('button', { name: '위젯 편집' }).click();
  await expect(page.getByRole('region', { name: '추가할 수 있는 위젯' })).toBeVisible();
  await audit(page, screen('홈 · 위젯 편집'), found);
  await page.getByRole('button', { name: '편집 완료' }).click();

  await nav(page, '할 일').click();
  await expect(widget(page, 'tasks')).toContainText('주간 보고서 작성');
  await audit(page, screen('할 일'), found);

  await openLibrary(page, '문서');
  const rows = widget(page, 'documents').locator('.document-row');
  await expect(rows).toHaveCount(4);
  await audit(page, screen('지식 보관함 · 문서'), found);
  await rows.filter({ hasText: '캐시 도입 회의' }).click();
  await expect(drawer(page).getByRole('complementary', { name: 'AI 분석' })).toContainText(
    '요약입니다.',
  );
  await audit(page, screen('문서 보기'), found);
  await page.keyboard.press('Escape');
  await expect(drawer(page)).toHaveCount(0);
  // An imported page keeps its paper and ink in either theme: its own colors are made for white paper.
  await rows.filter({ hasText: '조직도' }).click();
  await expect(drawer(page).locator('[data-html-document]')).toContainText('1×1px');
  const paper = drawer(page).locator('.html-document-frame');
  await expect(paper).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(paper).toHaveCSS('color', 'rgb(37, 44, 64)');
  await audit(page, screen('가져온 문서 보기'), found);
  await page.keyboard.press('Escape');
  await expect(drawer(page)).toHaveCount(0);
  await openLibrary(page, '자료 가져오기');
  await audit(page, screen('지식 보관함 · 자료 가져오기'), found);
  await openLibrary(page, '지식 관계도');
  await expect(page.locator('.graph-2d .graph-node')).toHaveCount(4);
  await audit(page, screen('지식 보관함 · 지식 관계도'), found);

  for (const [view, tabs] of [
    ['AI 작업', ['프롬프트 실행', '예약 실행', '자동 작업']],
    ['활동 기록', ['실행 기록', '최근 대화']],
  ] as const) {
    await nav(page, view).click();
    for (const tab of tabs) {
      await page.getByRole('tab', { name: tab }).click();
      await audit(page, screen(`${view} · ${tab}`), found);
    }
  }

  for (const section of [
    'LLM 연결',
    '사이트 계정',
    '프롬프트 템플릿',
    '화면·검색·대시보드',
    'AI 도구 연동',
    'API 매뉴얼',
    '저장 위치·정보',
  ]) {
    await openAdmin(page, section);
    await expect(page.locator('.admin-panel > h2')).toHaveText(section);
    await audit(page, screen(`관리 · ${section}`), found);
  }

  await goHome(page);
  await searchBox(page).fill('캐시 도입');
  await searchBox(page).press('Enter');
  await expect(results(page).locator('.result-card').first()).toBeVisible();
  await expect(page.locator('.answer-column')).toContainText('캐시를 도입하기로 했습니다.');
  await audit(page, screen('검색'), found);
  await page.getByRole('button', { name: '검색 결과 닫기' }).click();
}

test('accessibility: the main screens have no critical or serious axe findings, in either theme', async () => {
  test.setTimeout(300_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-a11y-'));
  // A stand-in LLM, so the documents have keywords and relations and a search has an answer with its sources.
  const llm = await llmServer(({ system, content }) => {
    if (system.includes('extract metadata')) {
      const title = content.split('\n')[0].replace('제목: ', '');
      return {
        summary: `${title} 요약입니다.`,
        keywords: ['cache', 'release', title.includes('회의') ? 'meeting' : 'design'],
        relations: [{ source: 'cache', target: 'release', label: '배포에 영향' }],
      };
    }
    if (system.includes('analyze a user'))
      return { intent: '캐시 결정 찾기', terms: ['캐시'], keywords: ['cache'], explanation: '' };
    if (system.includes('search agent'))
      return {
        thinking: '회의록에 결정이 있습니다.',
        results: [{ ref: 'D1', reason: '캐시 도입 결정', sections: [] }],
        next: null,
      };
    return { answer: '캐시를 도입하기로 했습니다. [1]', citations: [1] };
  });
  let app: ElectronApplication | undefined;
  const found: Finding[] = [];
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    // Without motion, nothing is measured halfway through its fade-in.
    await page.emulateMedia({ reducedMotion: 'reduce' });

    // The first screen in Windows' light and dark modes: without a setting the theme follows the system.
    await expect(page.getByLabel('Wiki 저장 폴더', { exact: true })).toBeVisible();
    await audit(page, '밝게 · 저장 위치 선택', found);
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' });
    await expect(page.locator('body')).toHaveCSS('color', 'rgb(229, 232, 240)');
    await audit(page, '시스템 어둡게 · 저장 위치 선택', found);
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'light' });
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);

    // Documents with an analysis, tasks with deadlines, a reminder and a prompt run, so every screen shows content.
    const ids: string[] = [];
    for (const [title, content] of [
      ['캐시 도입 회의', '# 캐시 도입 회의\n\nPython API에 캐시를 도입하기로 결정했습니다.'],
      ['배포 체크리스트', '- 캐시 설정 확인\n- 배포 공지'],
      ['설계 검토', '설계 검토 메모입니다.'],
    ])
      ids.push((await request<{ id: string }>(page, 'wiki.save', { title, content })).id);
    for (const id of ids) await request(page, 'wiki.analyze', { id });
    const at = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString();
    for (const [title, due, remind] of [
      ['주간 보고서 작성', at(2), null],
      ['회의록 정리', at(-1), null],
      ['예산안 검토', at(3), at(1)],
    ])
      await request(page, 'tasks.save', {
        id: '',
        title,
        notes: '',
        assignee: '',
        status: 'open',
        due_at: due,
        remind_at: remind,
        repeat: 'none',
        source_id: null,
      });
    const picture = path.join(directory, '조직도.png');
    await writeFile(picture, Buffer.from(PICTURE, 'base64'));
    await request(page, 'image.start', {
      path: picture,
      mode: 'file',
      recursive: false,
      analyze: false,
    });
    await expect
      .poll(
        async () =>
          (await request<{ title: string }[]>(page, 'wiki.list')).some((d) => d.title === '조직도'),
        { timeout: 30_000 },
      )
      .toBe(true);
    const prompts = await request<{ id: string; title: string }[]>(page, 'prompts.list');
    await request(page, 'prompts.run', {
      prompt_id: prompts.find((prompt) => prompt.title === '업무 메일 작성')!.id,
      variables: { '전달할 내용': '주간 진행 상황' },
    });
    await page.reload();
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible();

    await visit(page, '밝게', found);

    // The graph's nodes are one Tab stop: the arrow keys move between them by where they are drawn, Home and End go to
    // the first and the last, Enter opens a node's connections, and Tab leaves the graph.
    await openLibrary(page, '지식 관계도');
    const focusedNode = () =>
      page.evaluate(() => document.activeElement?.getAttribute('data-node') ?? null);
    const stop = page.locator('.graph-2d [data-node][tabindex="0"]');
    await expect(page.locator('.graph-2d [data-node]')).toHaveCount(4);
    await expect(stop).toHaveCount(1);
    await stop.focus();
    const first = await focusedNode();
    await page.keyboard.press('End');
    expect(await focusedNode()).not.toBe(first);
    await page.keyboard.press('Home');
    expect(await focusedNode()).toBe(first);
    const reached = new Set<string | null>();
    for (const arrow of ['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp']) {
      await page.locator(`.graph-2d [data-node="${first}"]`).focus();
      await page.keyboard.press(arrow);
      reached.add(await focusedNode());
    }
    reached.delete(first);
    expect(reached.size).toBeGreaterThan(0);
    expect([...reached].every((node) => node?.startsWith('k:'))).toBe(true);
    await page.locator(`.graph-2d [data-node="${first}"]`).focus();
    await page.keyboard.press('Enter');
    await expect(page.locator(`.graph-2d [data-node="${first}"]`)).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(page.locator('.explorer-detail h3')).toHaveText(first!.slice(2));
    await expect(stop).toHaveAttribute('data-node', first!);
    await page.keyboard.press('Tab');
    expect(await focusedNode()).toBeNull();

    // The dark theme chosen in the admin page is on <html> again after a reload, before the screen draws.
    await openAdmin(page, '화면·검색·대시보드');
    const themes = page.getByRole('radiogroup', { name: '화면 테마' });
    await expect(themes.getByRole('radio', { name: '시스템 설정' })).toBeChecked();
    await themes.getByRole('radio', { name: '어둡게' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible();
    await visit(page, '어둡게', found);

    // The zoom keys and Ctrl+wheel reach the main process, which zooms the window and keeps the factor for the next
    // start. They are sent as Windows sends them: Playwright's keyboard reaches the page only, not the main process.
    const zoom = () =>
      app!.evaluate(({ BrowserWindow }) =>
        BrowserWindow.getAllWindows()[0].webContents.getZoomFactor(),
      );
    type Input = Parameters<Electron.WebContents['sendInputEvent']>[0];
    const send = (events: Input[]) =>
      app!.evaluate(({ BrowserWindow }, list) => {
        const contents = BrowserWindow.getAllWindows()[0].webContents;
        for (const event of list) contents.sendInputEvent(event);
      }, events);
    const key = (keyCode: string) =>
      send([
        { type: 'keyDown', keyCode, modifiers: ['control'] },
        { type: 'keyUp', keyCode, modifiers: ['control'] },
      ]);
    await key('=');
    await expect.poll(zoom).toBeCloseTo(1.1);
    await key('-');
    await key('-');
    await expect.poll(zoom).toBeCloseTo(0.9);
    await key('0');
    await expect.poll(zoom).toBeCloseTo(1);
    await send([
      { type: 'mouseWheel', x: 600, y: 400, deltaY: 120, wheelTicksY: 1, modifiers: ['control'] },
    ]);
    await expect.poll(zoom).toBeGreaterThan(1);
    // At 200% the narrow layout takes over, with the menu at the bottom; it is measured too.
    for (let i = 0; i < 8; i++) await key('=');
    await expect.poll(zoom).toBeCloseTo(2);
    await expect
      .poll(async () => JSON.parse(await readFile(path.join(directory, 'window.json'), 'utf8')))
      .toEqual({ zoom: 2 });
    await audit(page, '어둡게 · 홈 200%', found);
    await key('0');
    await expect.poll(zoom).toBeCloseTo(1);

    expect(errors).toEqual([]);
    expect(found).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true });
  }
});
