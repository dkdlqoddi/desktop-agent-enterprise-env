import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
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

    // The library filters by it and counts each group.
    await openLibrary(page, '문서');
    const filters = documents.getByRole('radiogroup', { name: 'AI 분석 여부' });
    await expect(filters.getByRole('radio')).toHaveText(['전체5', 'AI 분석 완료1', '분석 전4']);
    await filters.getByRole('radio', { name: /AI 분석 완료/ }).click();
    await expect(documents.locator('.document-row')).toHaveCount(1);
    await filters.getByRole('radio', { name: /분석 전/ }).click();
    await expect(documents.locator('.document-row strong')).toHaveText([
      '회고 메모',
      '배포 체크리스트',
      '실패할 메모',
      '설계 검토',
    ]);

    // Shift-click chooses the documents between two rows; the chosen ones are analyzed one by one.
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
    // Still in the 분석 전 filter: the failed document stays, the analyzed ones left it.
    await expect(documents.locator('.document-row strong')).toHaveText([
      '실패할 메모',
      '설계 검토',
    ]);
    await expect(row('실패할 메모').locator('.status-label')).toHaveText('분석 실패');
    await job.getByRole('button', { name: 'AI 분석 진행 상황 닫기' }).click();
    await expect(job).toHaveCount(0);

    // Taking back the analysis of two documents returns them to 분석 전.
    await filters.getByRole('radio', { name: /AI 분석 완료/ }).click();
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
    await expect(filters.getByRole('radio')).toHaveText(['전체5', 'AI 분석 완료1', '분석 전4']);

    // Deleting several documents asks once, in place, and moves them to the trash, from where they come back.
    await filters.getByRole('radio', { name: /전체/ }).click();
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
