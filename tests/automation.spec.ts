import { test, expect, type ElectronApplication } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { addWidget, chooseStorage, goHome, launch, llmServer, openAdmin, widget } from './helpers';

test('automation: scheduled prompts run by themselves and the history widget shows every run', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-automation-'));
  const llm = await llmServer(({ content }) =>
    content.includes('실패') ? new Error('unavailable') : `## 정리\n\n${content}`,
  );
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory, {
      PRIVATE_AGENT_SCHEDULE_INTERVAL_MS: '1000',
      PRIVATE_AGENT_BASE_URL: llm.endpoint,
      PRIVATE_AGENT_API_KEY: 'e2e-key',
      PRIVATE_AGENT_MODEL_NAME: 'e2e-model',
    });
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await addWidget(page, 'Prompt 실행');
    await addWidget(page, '예약 실행');
    // The run history is one of the widgets added from the tray.
    await expect(widget(page, 'history')).toHaveCount(0);
    await addWidget(page, '실행 기록');
    const history = widget(page, 'history');
    await expect(history).toBeVisible();

    // Schedule a prompt from the prompt widget with the input typed there.
    const prompt = widget(page, 'prompt');
    await prompt.getByRole('combobox').selectOption({ label: '업무 메일 작성' });
    await prompt.getByLabel('전달할 내용').fill('주간 진행 상황');
    await prompt.getByRole('button', { name: '이 입력으로 예약' }).click();
    const editor = prompt.getByRole('form', { name: '새 예약' });
    await expect(editor.getByLabel('전달할 내용')).toHaveValue('주간 진행 상황');
    await editor.getByLabel('예약 이름').fill('주간 보고 초안');
    await editor.getByRole('radio', { name: '매주' }).click();
    await editor.getByLabel('요일').selectOption({ label: '금요일' });
    await editor.getByLabel('시각').fill('17:30');
    await expect(editor).toContainText('매주 금요일 오후 5:30');
    // A time box cleared with Backspace keeps the last time; it never breaks the screen.
    await editor.getByLabel('시각').fill('');
    await expect(editor).toContainText('매주 금요일 오후 5:30');
    await editor.getByRole('button', { name: '예약 저장' }).click();
    await expect(page.getByText(/예약을 저장했습니다/)).toBeVisible();

    // Pausing clears the next run, and "run now" runs it with the saved input.
    const schedules = widget(page, 'schedules');
    const row = schedules.locator('.schedule-row').filter({ hasText: '주간 보고 초안' });
    await expect(row).toContainText('매주 금요일 오후 5:30');
    const toggle = row.getByRole('switch', { name: '주간 보고 초안 예약 켜기' });
    await toggle.uncheck();
    await expect(row).toContainText('일시정지됨');
    await toggle.check();
    await expect(row).not.toContainText('일시정지됨');
    await row.getByRole('button', { name: '주간 보고 초안 지금 실행' }).click();
    await expect(page.getByText(/‘주간 보고 초안’을\(를\) 실행했습니다/)).toBeVisible();
    await expect(row).toContainText('성공');
    expect(llm.requests.at(-1)!.content).toContain('주간 진행 상황');

    // A due one-time schedule runs by itself, then shows as finished.
    const saved = await page.evaluate(
      async (runAt) => {
        const prompts = await window.privateAgent!.request('prompts.list');
        const meeting = (prompts.result as { id: string; title: string }[]).find(
          (p) => p.title === '회의록 정리',
        )!;
        return window.privateAgent!.request('schedules.save', {
          prompt_id: meeting.id,
          name: '오후 회의 요약',
          variables: { '회의 내용': '오후 회의 메모' },
          frequency: 'once',
          run_at: runAt,
        });
      },
      new Date(Date.now() + 1500).toISOString(),
    );
    expect(saved.ok).toBe(true);
    await expect(
      history.locator('.run-row').filter({ hasText: '예약 실행 · 오후 회의 요약' }),
    ).toBeVisible({ timeout: 20_000 });
    // Only Electron's scheduler may claim and execute occurrences.
    for (const method of ['schedules.due', 'schedules.execute'])
      expect((await page.evaluate((m) => window.privateAgent!.request(m), method)).ok).toBe(false);

    // Failed runs are recorded; filters narrow the list and the summary counts every run.
    const failed = await page.evaluate(async () => {
      const prompts = await window.privateAgent!.request('prompts.list');
      const mail = (prompts.result as { id: string; title: string }[]).find(
        (p) => p.title === '업무 메일 작성',
      )!;
      return window.privateAgent!.request('prompts.run', {
        prompt_id: mail.id,
        variables: { '전달할 내용': '실패 사례' },
      });
    });
    expect(failed.ok).toBe(false);
    await history.getByRole('radio', { name: '실패', exact: true }).click();
    await expect(history.locator('.run-row')).toHaveCount(1);
    await expect(history.locator('.run-row')).toContainText('HTTP 503');
    await history.getByRole('radio', { name: '전체', exact: true }).click();
    await expect(history.locator('.run-row')).toHaveCount(3);
    const tiles = history.locator('.stat-tile');
    await expect(tiles.nth(0)).toContainText('3');
    await expect(tiles.nth(1)).toContainText('67%');
    await expect(tiles.nth(3)).toContainText('1');
    // A group of day columns, each a labelled graphic a keyboard can reach.
    await expect(history.getByRole('group', { name: /일별 실행 수/ })).toBeVisible();
    await history.getByRole('button', { name: '표로 보기' }).click();
    await expect(history.locator('.chart-table tbody tr').first()).toHaveText(/오늘\s*2\s*1\s*1/);
    await page.screenshot({ path: 'test-results/run-history.png', fullPage: true });

    // A run opens in the drawer.
    await history.locator('.run-row').filter({ hasText: '오후 회의 요약' }).click();
    const detail = page.getByRole('dialog', { name: '회의록 정리' });
    await expect(detail).toContainText('오후 회의 메모');
    await detail.getByRole('button', { name: '응답 복사' }).click();
    await expect
      .poll(() => app!.evaluate(({ clipboard }) => clipboard.readText()))
      .toContain('오후 회의 메모');
    await detail.getByRole('button', { name: '닫기', exact: true }).click();

    // A notification click opens that run, and it can be deleted there.
    const failure = await page.evaluate(() =>
      window.privateAgent!.request('runs.list', { status: 'error' }),
    );
    await app.evaluate(
      ({ BrowserWindow }, id) =>
        BrowserWindow.getAllWindows()[0].webContents.send('schedule:event', {
          type: 'open',
          run_id: id,
        }),
      (failure.result as { id: string }[])[0].id,
    );
    const failedRun = page.getByRole('dialog', { name: '업무 메일 작성' });
    await expect(failedRun).toContainText('HTTP 503');
    await failedRun.getByRole('button', { name: '삭제', exact: true }).click();
    await failedRun.getByRole('button', { name: '삭제', exact: true }).click();
    await expect(page.getByText('실행 기록을 삭제했습니다.')).toBeVisible();
    await expect(history.locator('.run-row')).toHaveCount(2);

    await expect(
      schedules.locator('.schedule-row').filter({ hasText: '오후 회의 요약' }),
    ).toContainText('실행을 마쳤습니다');
    await schedules.getByRole('button', { name: '주간 보고 초안 삭제' }).click();
    await schedules.getByRole('button', { name: '삭제', exact: true }).click();
    await expect(schedules.locator('.schedule-row')).toHaveCount(1);

    // A template that gains an input asks first when a schedule has no value for it, and the schedule's run says
    // where to fill it in.
    await openAdmin(page, '프롬프트 템플릿');
    await page.getByRole('button', { name: '회의록 정리 수정' }).click();
    const template = page.getByRole('form', { name: '템플릿 수정' });
    // One form at a time: the other rows wait.
    await expect(page.getByRole('button', { name: '코드 리뷰 수정' })).toBeDisabled();
    await template
      .locator('textarea.code-input')
      .fill('회의를 정리해 주세요.\n\n{{회의 내용}}\n{{참석자}}');
    await template.getByRole('button', { name: '템플릿 저장' }).click();
    await expect(
      template.getByRole('group', { name: /예약 1개에 ‘참석자’ 값이 없어/ }),
    ).toBeVisible();
    await template.getByRole('button', { name: '템플릿 저장' }).click();
    await expect(page.getByText('템플릿을 저장했습니다.')).toBeVisible();
    await goHome(page);
    await schedules.getByRole('button', { name: '오후 회의 요약 지금 실행' }).click();
    await expect(
      page.getByText(
        '‘참석자’ 입력란이 비어 있습니다. 예약을 열어 입력 내용을 확인하고 저장해 주세요.',
      ),
    ).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
