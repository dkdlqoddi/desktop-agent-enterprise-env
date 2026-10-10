import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  chooseStorage,
  connectLlm,
  drawer,
  goHome,
  launch,
  llmServer,
  openLibrary,
  widget,
} from './helpers';

async function request<T>(page: Page, method: string, params: Record<string, unknown>) {
  const response = await page.evaluate(
    ([name, values]) => window.privateAgent!.request(name, values),
    [method, params] as const,
  );
  expect(response.ok).toBe(true);
  return response.result as T;
}

test('documents: AI-analyzed documents stand apart; several are analyzed, cleared and deleted at once', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-documents-'));
  // The first chosen document waits for the test, so the list can be seen mid-run.
  let release = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  const llm = await llmServer(async ({ system, content }) => {
    if (!system.includes('extract metadata')) return '연결 성공';
    const title = content.split('\n')[0].replace('제목: ', '');
    if (title === '회고 메모') await held;
    if (title === '실패할 메모') return new Error('unavailable');
    return { summary: `${title} 요약입니다.`, keywords: ['회의', title], relations: [] };
  });
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);
    // Listed newest first: 회고 메모, 배포 체크리스트, 실패할 메모, 설계 검토, 주간 회의록.
    const ids: Record<string, string> = {};
    for (const title of ['주간 회의록', '설계 검토', '실패할 메모', '배포 체크리스트', '회고 메모'])
      ids[title] = (
        await request<{ id: string }>(page, 'wiki.save', {
          title,
          content: `${title}의 본문입니다.`,
        })
      ).id;
    await request(page, 'wiki.analyze', { id: ids['주간 회의록'] });
    await page.reload();

    // On the dashboard the analyzed document is labelled apart from the ones left as imported.
    const documents = widget(page, 'documents');
    const row = (title: string) =>
      documents.locator('.document-list li').filter({ hasText: title });
    await expect(row('주간 회의록').locator('.status-label')).toHaveText('AI 분석 완료');
    await expect(row('주간 회의록')).toContainText('주간 회의록 요약입니다.');
    await expect(row('설계 검토').locator('.status-label')).toHaveText('분석 전');
    await expect(documents.getByRole('checkbox')).toHaveCount(0);

    // The library filters by it on the backend, which counts what each choice keeps.
    await openLibrary(page, '문서');
    const analysis = documents.getByRole('combobox', { name: 'AI 분석' });
    const count = documents.locator('.document-filter small');
    await expect(count).toHaveText('5개');
    await analysis.selectOption({ label: 'AI 분석 완료' });
    await expect(documents.locator('.document-row')).toHaveCount(1);
    await expect(count).toHaveText('1개');
    await analysis.selectOption({ label: '분석 전' });
    await expect(documents.locator('.document-row strong')).toHaveText([
      '회고 메모',
      '배포 체크리스트',
      '실패할 메모',
      '설계 검토',
    ]);
    await expect(count).toHaveText('4개');

    // Shift-click chooses the documents between two rows; the chosen ones stay chosen in another filter that shows
    // them, and are analyzed one by one.
    await documents.getByRole('checkbox', { name: '회고 메모 선택' }).check();
    await documents
      .getByRole('checkbox', { name: '실패할 메모 선택' })
      .click({ modifiers: ['Shift'] });
    const toolbar = documents.getByRole('group', { name: '선택한 문서 작업' });
    await expect(toolbar).toContainText('3개 선택');
    await expect(toolbar.getByRole('checkbox', { name: '모두 선택' })).toHaveJSProperty(
      'indeterminate',
      true,
    );
    await expect(toolbar.getByRole('button', { name: '분석 결과 지우기' })).toHaveCount(0);
    await page.screenshot({ path: 'test-results/documents-selected.png', fullPage: true });
    await analysis.selectOption({ label: '전체' });
    await expect(documents.locator('.document-row')).toHaveCount(5);
    await expect(toolbar).toContainText('3개 선택');
    await toolbar.getByRole('button', { name: 'AI 분석', exact: true }).click();
    const job = documents.locator('.analysis-job');
    await expect(job.getByRole('status')).toContainText('선택한 문서를 차례로 분석하는 중');
    await expect(job).toContainText('0 / 3');
    await expect(row('회고 메모').locator('.status-label')).toHaveText('분석 중');
    await expect(row('배포 체크리스트').locator('.status-label')).toHaveText('분석 대기');
    await expect(row('실패할 메모').locator('.status-label')).toHaveText('분석 대기');
    await expect(row('설계 검토').locator('.status-label')).toHaveText('분석 전');
    await expect(toolbar).not.toContainText('개 선택');
    await page.screenshot({ path: 'test-results/documents-analyzing.png', fullPage: true });
    // A document open while the run reaches it shows its analysis once done. Ending while its text is edited, the
    // analysis waits with a word, so the editor keeps what was written, and shows once the editing ends.
    await documents.locator('.document-row').filter({ hasText: '회고 메모' }).click();
    const insight = drawer(page).getByRole('complementary', { name: 'AI 분석' });
    await expect(insight.locator('.status-label')).toHaveText('분석 중');
    await drawer(page).getByRole('button', { name: '수정', exact: true }).click();
    const editor = drawer(page).locator('textarea.document-editor');
    await editor.fill('회고 메모의 본문입니다. 고치는 중');
    release();
    await expect(page.locator('.toast')).toContainText(
      '편집하는 동안 이 문서의 AI 분석이 끝났습니다.',
    );
    await expect(editor).toHaveValue('회고 메모의 본문입니다. 고치는 중');
    await drawer(page).getByRole('button', { name: '취소', exact: true }).click();
    await drawer(page).getByRole('button', { name: '버리고 닫기' }).click();
    await expect(insight.locator('.status-label')).toHaveText('AI 분석 완료');
    await expect(insight).toContainText('회고 메모 요약입니다.');
    await expect(drawer(page).getByRole('button', { name: '다시 분석하기' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(job.getByRole('status')).toContainText(
      '끝났습니다 · 분석하지 못한 문서가 있습니다',
    );
    await expect(job).toContainText('3 / 3');
    await expect(job.locator('.job-counts')).toHaveText('완료 2실패 1');
    await job.getByText('분석하지 못한 문서 1건').click();
    await expect(job.locator('.job-results li')).toContainText('실패할 메모');
    await expect(job.locator('.job-results li')).toContainText('HTTP 503');
    // Before the analysis is the document it did not reach; the failed one waits apart, to be tried again.
    await analysis.selectOption({ label: '분석 전' });
    await expect(documents.locator('.document-row strong')).toHaveText(['설계 검토']);
    await analysis.selectOption({ label: '분석 실패' });
    await expect(documents.locator('.document-row strong')).toHaveText(['실패할 메모']);
    await expect(row('실패할 메모').locator('.status-label')).toHaveText('분석 실패');
    await job.getByRole('button', { name: 'AI 분석 진행 상황 닫기' }).click();
    await expect(job).toHaveCount(0);

    // Taking back the analysis of two documents returns them to 분석 전.
    await analysis.selectOption({ label: 'AI 분석 완료' });
    await expect(documents.locator('.document-row strong')).toHaveText([
      '회고 메모',
      '배포 체크리스트',
      '주간 회의록',
    ]);
    await toolbar.getByRole('checkbox', { name: '모두 선택' }).check();
    await expect(toolbar).toContainText('3개 선택');
    await documents.getByRole('checkbox', { name: '주간 회의록 선택' }).uncheck();
    await toolbar.getByRole('button', { name: '분석 결과 지우기' }).click();
    await toolbar
      .getByRole('group', { name: /선택한 문서 2개의 AI 분석 결과/ })
      .getByRole('button', { name: '분석 결과 지우기' })
      .click();
    await expect(page.getByText('문서 2개의 AI 분석 결과를 지웠습니다.')).toBeVisible();
    await expect(documents.locator('.document-row strong')).toHaveText(['주간 회의록']);
    await expect(count).toHaveText('1개');
    await analysis.selectOption({ label: '분석 전' });
    await expect(count).toHaveText('3개');

    // Deleting several documents asks once, in place, and moves them to the trash, from where they come back.
    await analysis.selectOption({ label: '전체' });
    await documents.getByRole('checkbox', { name: '실패할 메모 선택' }).check();
    await documents.getByRole('checkbox', { name: '설계 검토 선택' }).check();
    await toolbar.getByRole('button', { name: '삭제', exact: true }).click();
    await toolbar
      .getByRole('group', {
        name: '선택한 문서 2개를 휴지통으로 옮길까요? 30일 안에 복원할 수 있습니다.',
      })
      .getByRole('button', { name: '삭제', exact: true })
      .click();
    await expect(page.getByText('문서 2개를 휴지통으로 옮겼습니다.')).toBeVisible();
    await expect(documents.locator('.document-row strong')).toHaveText([
      '회고 메모',
      '배포 체크리스트',
      '주간 회의록',
    ]);
    await documents.getByRole('button', { name: '휴지통 2' }).click();
    const trash = documents.getByRole('list', { name: '휴지통' });
    await expect(trash.locator('li strong')).toHaveText(['설계 검토', '실패할 메모']);
    await trash
      .locator('li')
      .filter({ hasText: '설계 검토' })
      .getByRole('button', { name: '복원' })
      .click();
    await expect(page.getByText('‘설계 검토’ 문서를 복원했습니다.')).toBeVisible();
    await expect(trash.locator('li strong')).toHaveText(['실패할 메모']);
    await documents.getByRole('button', { name: '휴지통 비우기' }).click();
    await documents
      .getByRole('group', { name: '휴지통의 문서 1개를 영구 삭제할까요? 되돌릴 수 없습니다.' })
      .getByRole('button', { name: '휴지통 비우기' })
      .click();
    await expect(page.getByText('문서 1개를 영구 삭제했습니다.')).toBeVisible();
    await expect(documents.getByText('휴지통이 비어 있습니다.')).toBeVisible();
    // The restored document is back where it was: restoring changes nothing about it.
    await documents.getByRole('button', { name: '휴지통', exact: true }).click();
    await expect(documents.locator('.document-row strong')).toHaveText([
      '회고 메모',
      '배포 체크리스트',
      '설계 검토',
      '주간 회의록',
    ]);
    await expect(
      page
        .getByRole('navigation', { name: '화면' })
        .getByRole('button', { name: '지식 보관함', exact: true }),
    ).toContainText('4');
    await page.screenshot({ path: 'test-results/documents-library.png', fullPage: true });

    // The document window says the same, and offers the analysis again or for the first time.
    await documents.locator('.document-row').filter({ hasText: '주간 회의록' }).click();
    await expect(drawer(page).getByRole('complementary', { name: 'AI 분석' })).toContainText(
      'AI 분석 완료',
    );
    await expect(drawer(page).getByRole('button', { name: '다시 분석하기' })).toBeVisible();
    await page.keyboard.press('Escape');
    await documents.locator('.document-row').filter({ hasText: '회고 메모' }).click();
    await expect(drawer(page).getByRole('complementary', { name: 'AI 분석' })).toContainText(
      '분석 전',
    );
    await expect(drawer(page).getByRole('button', { name: 'AI 분석하기' })).toBeVisible();
    await page.keyboard.press('Escape');
    // One extraction for the first document, then one for each chosen document: the failing one's request, refused
    // with HTTP 503, is sent 4 times in all.
    expect(llm.requests.filter((r) => r.system.includes('extract metadata'))).toHaveLength(7);
    await goHome(page);
    await expect(documents.getByRole('checkbox')).toHaveCount(0);
    await documents.screenshot({ path: 'test-results/documents-home.png' });

    // A narrow window wraps the filters and the actions instead of scrolling sideways.
    await page.setViewportSize({ width: 390, height: 844 });
    await openLibrary(page, '문서');
    await documents.getByRole('checkbox', { name: '주간 회의록 선택' }).check();
    await expect(toolbar.getByRole('button', { name: '분석 결과 지우기' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({ path: 'test-results/documents-narrow.png', fullPage: true });
    expect(errors).toEqual([]);
  } finally {
    release();
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

test('documents: the library comes in pages filtered by the backend, drawn a window at a time, and acts on a choice across pages', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-pages-'));
  const llm = await llmServer(({ system }) =>
    system.includes('extract metadata')
      ? {
          summary: '데이터 저장소를 정리한 메모입니다.',
          keywords: ['database', 'storage'],
          aliases: { database: ['데이터베이스', 'DB'] },
          relations: [{ source: 'database', target: 'storage', label: '저장' }],
        }
      : '연결 성공',
  );
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);
    // A Markdown file imported first, then 120 notes: 메모 001 … 메모 120, the newest last; 메모 120 is analyzed.
    const folder = path.join(directory, '회의 자료');
    await mkdir(folder);
    await writeFile(
      path.join(folder, '분기 회의록.md'),
      '# 분기 회의록\n\n분기 목표를 정했습니다.',
    );
    await request(page, 'files.import_text', {
      path: path.join(folder, '분기 회의록.md'),
      analyze: false,
    });
    const ids = await page.evaluate(async () => {
      const made: string[] = [];
      for (let i = 1; i <= 120; i++) {
        const name = `메모 ${String(i).padStart(3, '0')}`;
        const response = await window.privateAgent!.request('wiki.save', {
          title: name,
          content: `${name}의 본문입니다.`,
        });
        if (!response.ok) throw new Error(response.error);
        made.push((response.result as { id: string }).id);
      }
      return made;
    });
    await request(page, 'wiki.analyze', { id: ids[119] });
    await page.reload();

    await openLibrary(page, '문서');
    const documents = widget(page, 'documents');
    const list = documents.getByRole('list', { name: '문서 목록' });
    const rows = list.locator('li[data-index]');
    const count = documents.locator('.document-filter small');
    const toEnd = () => list.evaluate((box) => box.scrollTo(0, box.scrollHeight));
    // The counts are the backend's: every document, though the list holds the first page of 100.
    await expect(documents.locator('.widget-head .count')).toHaveText('121');
    await expect(count).toHaveText('121개');
    await expect(rows.first()).toContainText('메모 120');
    await expect(list.locator('.list-more')).toContainText('100 / 121');
    // Only the rows near what the list shows are drawn.
    expect(await rows.count()).toBeLessThan(40);
    // Scrolled to its end, the list brings the next page: the oldest document is the last.
    await toEnd();
    await expect(list.locator('.list-more')).toHaveCount(0);
    await toEnd();
    await expect(rows.last()).toContainText('분기 회의록');
    await expect(rows.last()).toHaveAttribute('aria-setsize', '121');
    expect(await rows.count()).toBeLessThan(40);
    await page.screenshot({ path: 'test-results/documents-pages.png', fullPage: true });

    // The keyboard: one row in the Tab order, the arrows, Home and End move across the pages loaded.
    await list.evaluate((box) => box.scrollTo(0, 0));
    await expect(rows.first()).toContainText('메모 120');
    await rows.first().locator('.document-row').focus();
    await expect(list.locator('[tabindex="0"]')).toHaveCount(2);
    await page.keyboard.press('ArrowDown');
    await expect(page.locator(':focus')).toContainText('메모 119');
    await page.keyboard.press('End');
    await expect(page.locator(':focus')).toContainText('분기 회의록');
    await page.keyboard.press('ArrowLeft');
    await expect(page.locator(':focus')).toHaveAttribute('aria-label', '분기 회의록 선택');
    await page.keyboard.press('Home');
    await expect(page.locator(':focus')).toHaveAttribute('aria-label', '메모 120 선택');

    // A choice spans the pages: one on the first, one on the second, deleted at once and brought back.
    await documents.getByRole('checkbox', { name: '메모 120 선택' }).check();
    await toEnd();
    await documents.getByRole('checkbox', { name: '메모 001 선택' }).check();
    const toolbar = documents.getByRole('group', { name: '선택한 문서 작업' });
    await expect(toolbar).toContainText('2개 선택');
    await toolbar.getByRole('button', { name: '삭제', exact: true }).click();
    await toolbar
      .getByRole('group', {
        name: '선택한 문서 2개를 휴지통으로 옮길까요? 30일 안에 복원할 수 있습니다.',
      })
      .getByRole('button', { name: '삭제', exact: true })
      .click();
    const toast = page.locator('.toast').filter({ hasText: '문서 2개를 휴지통으로 옮겼습니다.' });
    await expect(toast).toBeVisible();
    await expect(count).toHaveText('119개');
    await expect(documents.getByRole('checkbox', { name: '메모 001 선택' })).toHaveCount(0);
    await toast.getByRole('button', { name: '되돌리기' }).click();
    await expect(count).toHaveText('121개');
    await list.evaluate((box) => box.scrollTo(0, 0));
    await expect(rows.first()).toContainText('메모 120');

    // 모두 선택 takes the documents loaded, then every one the filters keep.
    await page.reload();
    await openLibrary(page, '문서');
    await expect(count).toHaveText('121개');
    await toolbar.getByRole('checkbox', { name: '모두 선택' }).check();
    await expect(toolbar).toContainText('100개 선택');
    await toolbar.getByRole('button', { name: '조건에 맞는 문서 121개 모두 선택' }).click();
    await expect(toolbar).toContainText('121개 선택');
    await toolbar.getByRole('button', { name: '선택 해제' }).click();

    // The filters are the backend's: where documents came from, their analysis, when they changed and a text.
    await documents.getByRole('combobox', { name: '출처' }).selectOption({ label: 'Markdown' });
    await expect(count).toHaveText('1개');
    await expect(rows).toHaveText([/분기 회의록/]);
    await documents.getByRole('button', { name: '조건 지우기' }).click();
    await documents
      .getByRole('combobox', { name: 'AI 분석' })
      .selectOption({ label: 'AI 분석 완료' });
    await expect(rows).toHaveText([/메모 120/]);
    await documents.getByRole('combobox', { name: 'AI 분석' }).selectOption({ label: '전체' });
    const period = documents.getByRole('combobox', { name: '수정일' });
    await period.selectOption({ label: '직접 지정' });
    await documents.getByLabel('수정일 시작').fill('2099-01-01');
    await expect(count).toHaveText('0개');
    await expect(documents).toContainText('조건에 맞는 문서가 없습니다.');
    await period.selectOption({ label: '오늘' });
    await expect(count).toHaveText('121개');
    const filter = page.getByLabel('보관함 문서 필터');
    await filter.fill('메모 05');
    await expect(count).toHaveText('10개');
    await expect(rows).toHaveCount(10);
    await filter.fill('회의 자료');
    await expect(rows).toHaveText([/분기 회의록/]);
    await filter.fill('데이터베이스');
    await expect(rows).toHaveText([/메모 120/]);
    await documents.getByRole('button', { name: '조건 지우기' }).click();
    await expect(count).toHaveText('121개');

    // The knowledge graph finds a keyword by the other name the analysis gave it, and shows the keyword.
    await page.getByRole('tab', { name: '지식 관계도' }).click();
    const panel = page.getByRole('complementary', { name: '지식 목록' });
    await panel.getByLabel('키워드 찾기').fill('데이터베이스');
    await expect(panel.locator('.panel-list > button')).toHaveText([/# database · 데이터베이스/]);
    await panel.locator('.panel-list > button').click();
    await expect(panel.locator('.keyword-aliases')).toHaveText('다른 이름: DB, 데이터베이스');
    await panel.getByRole('tab', { name: /발견한 관계/ }).click();
    await panel.getByLabel('관계 찾기').fill('db');
    await expect(panel.locator('.panel-list > button')).toHaveText([/database → 저장 → storage/]);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

// A 1×1 red PNG.
const PICTURE =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==';

test('documents: an edit that meets a change made elsewhere is compared and saved as chosen; a downloaded page runs no script', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-conflict-'));
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    type Saved = { id: string; title: string; content: string; revision: number };
    const plan = '# 배포 계획\n\n- 월요일 준비\n- 화요일 배포\n- 금요일 회고';
    const { id } = await request<Saved>(page, 'wiki.save', { title: '배포 계획', content: plan });
    // Another window or an AI tool saves the document while its editor is open here.
    const elsewhere = async (content: string) => {
      const { revision } = await request<Saved>(page, 'wiki.get', { id });
      await request(page, 'wiki.save', {
        id,
        title: '배포 계획',
        content,
        expected_revision: revision,
      });
    };
    await page.reload();
    await openLibrary(page, '문서');
    await widget(page, 'documents')
      .locator('.document-row')
      .filter({ hasText: '배포 계획' })
      .click();
    const panel = drawer(page);
    const editor = panel.locator('textarea.document-editor');
    const view = panel.locator('.document-content');
    const lines = (kind: 'added' | 'removed') => panel.locator(`.change-lines li.${kind}`);
    const toast = (text: string) => page.locator('.toast').filter({ hasText: text });

    // The save is refused: the window compares what changed elsewhere, what the user changed and what saving over the
    // document would do, and keeps the writing meanwhile.
    await panel.getByRole('button', { name: '수정', exact: true }).click();
    await editor.fill(`${plan}\n- 다음 주 점검`);
    await elsewhere('# 배포 계획\n\n- 월요일 준비\n- 목요일 배포\n- 금요일 회고');
    await panel.getByRole('button', { name: '문서 저장' }).click();
    await expect(panel.getByRole('heading', { name: '수정 충돌' })).toBeVisible();
    await expect(
      panel.getByRole('alert').filter({ hasText: '다른 곳에서 문서가 바뀌어' }),
    ).toBeVisible();
    await expect(lines('removed')).toHaveText([/화요일 배포/]);
    await expect(lines('added')).toHaveText([/목요일 배포/]);
    await panel.getByRole('radio', { name: '내가 바꾼 내용' }).click();
    await expect(lines('removed')).toHaveCount(0);
    await expect(lines('added')).toHaveText([/다음 주 점검/]);
    await panel.getByRole('radio', { name: '내가 바꾼 내용' }).press('ArrowRight');
    await expect(panel.getByRole('radio', { name: '덮어쓰면 바뀌는 내용' })).toBeChecked();
    await expect(lines('removed')).toHaveText([/목요일 배포/]);
    await expect(lines('added')).toHaveText([/화요일 배포/, /다음 주 점검/]);
    await page.screenshot({ path: 'test-results/document-conflict.png' });
    const ask = panel.getByRole('group', { name: /저장하지 않은 내용이 있습니다/ });
    await page.keyboard.press('Escape');
    await expect(ask).toBeVisible();
    await ask.getByRole('button', { name: '계속 쓰기' }).click();

    // 내 수정 유지하고 다시 열기: the writing goes on from the document as it is now, what changed there beside it.
    await panel.getByRole('button', { name: '내 수정 유지하고 다시 열기' }).click();
    await expect(panel.getByRole('heading', { name: '문서 수정' })).toBeVisible();
    await expect(editor).toHaveValue(`${plan}\n- 다음 주 점검`);
    await panel.getByText('다른 곳에서 바뀐 내용 보기').click();
    await expect(lines('added')).toHaveText([/목요일 배포/]);
    await editor.fill('# 배포 계획\n\n- 월요일 준비\n- 목요일 배포\n- 금요일 회고\n- 다음 주 점검');
    await panel.getByRole('button', { name: '문서 저장' }).click();
    await expect(view).toContainText('목요일 배포');
    await expect(view).toContainText('다음 주 점검');
    await expect(toast('문서를 저장했습니다.')).toBeVisible();

    // 새 문서로 저장: the writing becomes a document of its own, which the window links to.
    await panel.getByRole('button', { name: '수정', exact: true }).click();
    await editor.fill('# 배포 계획\n\n내가 쓴 메모');
    await elsewhere('# 배포 계획\n\n다른 곳에서 쓴 메모');
    await panel.getByRole('button', { name: '문서 저장' }).click();
    await panel.getByRole('button', { name: '새 문서로 저장' }).click();
    await expect(view).toContainText('다른 곳에서 쓴 메모');
    // The toast says so too; the link stays in the window.
    const copied = panel.locator('.banner').filter({ hasText: '배포 계획 (충돌 사본)' });
    await expect(copied).toHaveAttribute('role', 'status');
    await expect(toast('배포 계획 (충돌 사본)')).toBeVisible();

    // 현재 문서 덮어쓰기, asked first: the writing replaces what changed elsewhere.
    await panel.getByRole('button', { name: '수정', exact: true }).click();
    await editor.fill('# 배포 계획\n\n최종 메모');
    await elsewhere('# 배포 계획\n\n또 다른 메모');
    await panel.getByRole('button', { name: '문서 저장' }).click();
    await panel.getByRole('button', { name: '현재 문서 덮어쓰기' }).click();
    await panel
      .getByRole('group', { name: /다른 곳에서 바뀐 내용은 사라지고 내 수정으로 저장됩니다/ })
      .getByRole('button', { name: '현재 문서 덮어쓰기' })
      .click();
    await expect(view).toContainText('최종 메모');
    await expect(toast('현재 문서를 내 수정으로 덮어썼습니다.')).toBeVisible();
    expect((await request<Saved>(page, 'wiki.get', { id })).content).toBe(
      '# 배포 계획\n\n최종 메모',
    );
    await copied.getByRole('button', { name: '사본 열기' }).click();
    await expect(panel.getByRole('heading', { name: '배포 계획 (충돌 사본)' })).toBeVisible();
    await expect(view).toContainText('내가 쓴 메모');
    await page.keyboard.press('Escape');
    await expect(panel).toHaveCount(0);

    // An imported page downloads with its own policy first: opened from the disk, no script of it runs, its embedded
    // picture still shows.
    const picture = path.join(directory, '조직도.png');
    await writeFile(picture, Buffer.from(PICTURE, 'base64'));
    await request(page, 'image.start', { path: picture, mode: 'file', recursive: false });
    await expect
      .poll(
        async () => (await request<{ status: string } | null>(page, 'image.status', {}))?.status,
      )
      .toBe('completed');
    await page.reload();
    await openLibrary(page, '문서');
    await widget(page, 'documents').locator('.document-row').filter({ hasText: '조직도' }).click();
    await page.evaluate(() => {
      const made = URL.createObjectURL.bind(URL);
      const blobs: Blob[] = [];
      Object.assign(window, { blobs });
      URL.createObjectURL = (object: Blob | MediaSource) => {
        if (object instanceof Blob) blobs.push(object);
        return made(object);
      };
    });
    await panel.getByRole('button', { name: '원문 HTML' }).click();
    await expect(panel.getByLabel('HTML 원문')).toHaveValue(/^<!doctype html>/);
    const downloaded = await page.evaluate(() =>
      (window as unknown as { blobs: Blob[] }).blobs.at(-1)!.text(),
    );
    expect(downloaded).toMatch(
      /^<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline';[^"]*"><html/,
    );
    const file = path.join(directory, '조직도.html');
    await writeFile(
      file,
      downloaded.replace('</body>', '<script>document.title = "실행됨"</script></body>'),
    );
    const opened = await app.evaluate(async ({ BrowserWindow }, local) => {
      const window = new BrowserWindow({ show: false });
      try {
        await window.loadFile(local);
        return {
          title: window.webContents.getTitle(),
          picture: await window.webContents.executeJavaScript(
            'document.querySelector("img").naturalWidth',
          ),
        };
      } finally {
        window.destroy();
      }
    }, file);
    expect(opened).toEqual({ title: '조직도', picture: 1 });
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
