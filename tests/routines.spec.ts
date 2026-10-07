import { test, expect, type ElectronApplication } from '@playwright/test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { jiraServer } from './fixtures/jira.cjs';
import {
  addWidget,
  chooseStorage,
  drawer,
  goHome,
  launch,
  llmServer,
  openLibrary,
  widget,
} from './helpers';

test('routines: pick a ready-made job, let it run by itself and now, see what changed, and manage it', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-routines-'));
  const folder = path.join(directory, '업무 폴더');
  await mkdir(folder);
  await writeFile(path.join(folder, '회의.md'), '# 회의\n\n결정 A');
  await writeFile(path.join(folder, '설계.md'), '# 설계\n\n초안');
  const server = await jiraServer();
  const original = { ...server.issues['TEAM-1'] };
  const llm = await llmServer(({ system }) =>
    system.includes('what changed')
      ? '### [TEAM-1] 로그인 화면 오류\n- 설명이 바뀌었습니다'
      : '정리',
  );
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory, {
      PRIVATE_AGENT_SCHEDULE_INTERVAL_MS: '1000',
      PRIVATE_AGENT_PROVIDER: 'general',
      PRIVATE_AGENT_BASE_URL: llm.endpoint,
      PRIVATE_AGENT_API_KEY: 'e2e-key',
      PRIVATE_AGENT_MODEL_NAME: 'e2e-model',
    });
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    const account = await page.evaluate(
      (url) =>
        window.privateAgent!.request('sites.save', {
          kind: 'jira',
          name: '팀 JIRA',
          url,
          login_id: 'tester',
          password: 'correct horse',
          method: 'auto',
        }),
      server.url,
    );
    expect(account.ok).toBe(true);

    // AI 작업 → 자동 작업: with none set up yet, the catalog of ready-made jobs shows.
    await page
      .getByRole('navigation', { name: '화면' })
      .getByRole('button', { name: 'AI 작업' })
      .click();
    await page.getByRole('tab', { name: '자동 작업' }).click();
    const box = widget(page, 'routines');
    const catalog = box.getByRole('region', { name: '자동 작업 종류' });
    await expect(catalog.locator('.recipe-card')).toHaveCount(7);
    await expect(catalog.getByRole('button', { name: /업무 보고 초안/ })).toContainText('LLM 필요');
    await page.screenshot({ path: 'test-results/routines-catalog.png', fullPage: true });
    await catalog.getByRole('button', { name: /JIRA 티켓 변경 확인/ }).click();
    const form = box.getByRole('form', { name: '새 자동 작업 · JIRA 티켓 변경 확인' });
    await form.getByLabel('JIRA 티켓 링크 (한 줄에 하나)').fill(server.url);
    // The account the link signs in with, as the import box shows it.
    await expect(form.locator('.site-line')).toContainText('팀 JIRA · tester');
    await form.getByLabel('반복', { exact: true }).selectOption({ label: '15분마다' });
    await expect(form.locator('.schedule-summary')).toContainText('15분마다 실행합니다');
    await page.screenshot({ path: 'test-results/routines-form.png', fullPage: true });
    await form.getByRole('button', { name: '만들기' }).click();
    await expect(page.getByText(/‘JIRA 티켓 변경 확인’ 자동 작업을 저장했습니다/)).toBeVisible();

    // A new routine runs by itself at once: the ticket as it is now is the baseline.
    const row = box.locator('.routine-row').filter({ hasText: 'JIRA 티켓 변경 확인' });
    await expect(row).toContainText('티켓 1개를 기준으로 가져왔습니다', { timeout: 60_000 });
    await expect(row).toContainText('15분마다');
    expect(server.logins).toEqual([{ id: 'tester', password: 'correct horse' }]);

    // A notification clicked while the routine's form is open leaves what is being written; the routine's results
    // show once the form closes.
    const listed = await page.evaluate(() => window.privateAgent!.request('routines.list'));
    const watchId = (listed.result as { id: string }[])[0].id;
    await row.getByRole('button', { name: 'JIRA 티켓 변경 확인 수정' }).click();
    const edit = box.getByRole('form', { name: 'JIRA 티켓 변경 확인 수정' });
    await edit.getByLabel('이름', { exact: true }).fill('고치는 중');
    await app.evaluate(
      ({ BrowserWindow }, id) =>
        BrowserWindow.getAllWindows()[0].webContents.send('routine:event', {
          type: 'open',
          routine_id: id,
        }),
      watchId,
    );
    await expect(page.getByText(/작성 중인 자동 작업은 그대로 두었습니다/)).toBeVisible();
    await expect(edit.getByLabel('이름', { exact: true })).toHaveValue('고치는 중');
    await edit.getByRole('button', { name: '취소' }).click();
    await expect(row.locator('.routine-results')).toBeVisible();

    // The ticket changes; the next run tells what changed, with the LLM's summary. While it lasts, its import shows
    // in the import box as the routine's. The main process first tells the screens of the first run's end (it looks
    // every 3 seconds), which has them look at the imports again: after that, the import shows by ▶ alone.
    await page.waitForTimeout(4000);
    server.issues['TEAM-1'].text = '로그인 버튼 오류가 고쳐졌습니다';
    server.options.slow = true;
    await row.getByRole('button', { name: 'JIRA 티켓 변경 확인 지금 실행' }).click();
    await openLibrary(page, '자료 가져오기');
    await expect(widget(page, 'import').locator('.job')).toContainText(
      '자동 작업 ‘JIRA 티켓 변경 확인’',
    );
    server.options.slow = false;
    await page
      .getByRole('navigation', { name: '화면' })
      .getByRole('button', { name: 'AI 작업' })
      .click();
    await page.getByRole('tab', { name: '자동 작업' }).click();
    await expect(row).toContainText('바뀐 티켓 1개 · 티켓 1개 확인', { timeout: 60_000 });
    await row.getByRole('button', { name: 'JIRA 티켓 변경 확인 결과 보기' }).click();
    await expect(row.locator('.run-badge')).toHaveText('새 소식');
    const results = row.locator('.routine-results');
    await expect(results.locator('.routine-items > li')).toHaveCount(1);
    await expect(results.locator('.item-status')).toHaveText('바뀜');
    await expect(results.locator('.routine-text')).toContainText('설명이 바뀌었습니다');
    await results.getByText(/바뀐 줄/).click();
    await expect(results.locator('.change-lines li.added').first()).toContainText(
      '로그인 버튼 오류가 고쳐졌습니다',
    );
    await expect(results.locator('.change-lines li.removed').first()).toContainText(
      '로그인 버튼 오류',
    );
    await expect(results.locator('.routine-history li')).toHaveCount(2);
    await page.screenshot({ path: 'test-results/routines.png', fullPage: true });
    // The ticket's Wiki document has the new text.
    await results.getByRole('button', { name: '[TEAM-1] 로그인 화면 오류 문서 열기' }).click();
    await expect(drawer(page)).toContainText('로그인 버튼 오류가 고쳐졌습니다');
    await page.keyboard.press('Escape');
    await expect(drawer(page)).toHaveCount(0);

    // A routine that brings this PC's files up to date, at a time of day: it waits for its time or for ▶.
    await box.getByRole('button', { name: '새 자동 작업' }).click();
    await catalog.getByRole('button', { name: /원본 최신 내용으로 업데이트/ }).click();
    const refresh = box.getByRole('form', { name: '새 자동 작업 · 원본 최신 내용으로 업데이트' });
    await refresh.getByLabel('파일·폴더 경로 (한 줄에 하나)').fill(folder);
    await refresh.getByLabel('반복', { exact: true }).selectOption({ label: '매일 정한 시각에' });
    await refresh.getByLabel('시각').fill('08:30');
    await refresh.getByRole('button', { name: '만들기' }).click();
    const files = box.locator('.routine-row').filter({ hasText: '원본 최신 내용으로 업데이트' });
    await expect(files).toContainText('매일 오전 8:30');
    await expect(files).toContainText('아직 실행하지 않았습니다');
    await files.getByRole('button', { name: '원본 최신 내용으로 업데이트 지금 실행' }).click();
    await expect(files).toContainText('새 문서 2 · 그대로 0', { timeout: 60_000 });
    await writeFile(path.join(folder, '회의.md'), '# 회의\n\n결정 B');
    await files.getByRole('button', { name: '원본 최신 내용으로 업데이트 지금 실행' }).click();
    await expect(files).toContainText('업데이트 1 · 그대로 1', { timeout: 60_000 });
    await expect(files.locator('.routine-items > li')).toHaveCount(1);
    await expect(files.locator('.routine-items')).toContainText('회의.md');

    // Turned off, it shows so; deleting asks first.
    await files
      .getByRole('switch', { name: '원본 최신 내용으로 업데이트 자동 작업 켜기' })
      .uncheck();
    await expect(files).toContainText('꺼져 있음');
    await row.getByRole('button', { name: 'JIRA 티켓 변경 확인 삭제' }).click();
    await row.getByRole('button', { name: '삭제', exact: true }).click();
    await expect(page.getByText('자동 작업을 삭제했습니다.')).toBeVisible();
    await expect(box.locator('.routine-row')).toHaveCount(1);

    // Only the main process may take due runs and their logins.
    for (const method of ['routines.due', 'routines.execute', 'routines.events', 'routines.needs'])
      expect((await page.evaluate((m) => window.privateAgent!.request(m), method)).ok).toBe(false);

    // The dashboard shows the latest results.
    await goHome(page);
    await addWidget(page, '자동 작업', '최근 결과');
    const feed = widget(page, 'routines');
    await expect(feed.locator('.routine-feed li')).toHaveCount(2);
    await expect(feed).toContainText('업데이트 1 · 그대로 1');
    expect(errors).toEqual([]);
  } finally {
    Object.assign(server.issues['TEAM-1'], original);
    server.options.slow = false;
    await app?.close();
    await llm.close();
    await server.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
