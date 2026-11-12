import { test, expect, type ElectronApplication } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { chooseStorage, goHome, launch, openAdmin, widget } from './helpers';

test('trash: a deleted task comes back by 되돌리기, a template with its schedule from the admin page, and a purge is for good', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-trash-'));
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    const request = async <T>(method: string, params: Record<string, unknown> = {}) => {
      const reply = await page.evaluate(
        ([name, values]) => window.privateAgent!.request(name, values),
        [method, params] as const,
      );
      expect(reply.ok, `${method}: ${JSON.stringify(reply)}`).toBe(true);
      return reply.result as T;
    };
    type Entries = { items: { id: string; kind: string; title: string }[]; total: number };

    // A task deleted from its form goes to the trash, and 되돌리기 on the message brings it back as it was.
    const tasks = widget(page, 'tasks');
    await tasks.getByLabel('새 할 일').fill('휴지통으로 갈 할 일');
    await tasks.getByLabel('새 할 일').press('Enter');
    await expect(tasks.locator('.task-row')).toHaveCount(1);
    await tasks.locator('.task-open').filter({ hasText: '휴지통으로 갈 할 일' }).click();
    await tasks.getByRole('button', { name: '삭제', exact: true }).click();
    const ask = tasks.getByRole('group', {
      name: '이 할 일을 휴지통으로 옮길까요? 30일 안에 복원할 수 있습니다.',
    });
    await ask.getByRole('button', { name: '삭제', exact: true }).click();
    await expect(tasks.locator('.task-row')).toHaveCount(0);
    const moved = page.getByRole('status').filter({ hasText: '할 일을 휴지통으로 옮겼습니다.' });
    await expect(moved).toBeVisible();
    expect((await request<Entries>('trash.list')).items.map((item) => item.kind)).toEqual(['task']);
    await moved.getByRole('button', { name: '되돌리기' }).click();
    await expect(tasks.locator('.task-row')).toHaveCount(1);
    await expect(tasks.locator('.task-row')).toContainText('휴지통으로 갈 할 일');
    expect((await request<Entries>('trash.list')).total).toBe(0);

    // A template goes with its schedule. Neither runs nor shows while it waits in the trash.
    await openAdmin(page, '프롬프트 템플릿');
    await page.getByRole('button', { name: '새 템플릿' }).click();
    const form = page.getByRole('form', { name: '새 템플릿' });
    await form.getByLabel('이름', { exact: true }).fill('주간 보고 초안');
    await form.locator('textarea.code-input').fill('이번 주에 한 일을 정리해 주세요.\n\n{{한 일}}');
    await form.getByRole('button', { name: '템플릿 저장' }).click();
    await expect(page.getByText('템플릿을 저장했습니다.')).toBeVisible();
    const template = (await request<{ id: string; title: string }[]>('prompts.list')).find(
      (item) => item.title === '주간 보고 초안',
    )!;
    await request('schedules.save', {
      prompt_id: template.id,
      name: '금요일 보고',
      variables: { '한 일': '배포 준비' },
      frequency: 'weekly',
      weekday: 4,
      run_time: '17:30',
    });
    await page.getByRole('button', { name: '주간 보고 초안 삭제' }).click();
    await page
      .getByRole('group', { name: /템플릿과 그 예약을 휴지통으로 옮길까요\?/ })
      .getByRole('button', { name: '삭제', exact: true })
      .click();
    await expect(page.getByText('템플릿을 휴지통으로 옮겼습니다.')).toBeVisible();
    await expect(page.getByRole('button', { name: '주간 보고 초안 삭제' })).toHaveCount(0);
    expect(await request<unknown[]>('schedules.list')).toEqual([]);
    // Deleted again right after the task came back, so that the trash holds two kinds.
    const [task] = await request<{ id: string }[]>('tasks.list');
    await request('tasks.delete', { id: task.id });

    // 관리 → 휴지통 lists what was deleted, the last first, and restores the template with its schedule.
    await page.getByRole('tab', { name: '휴지통' }).click();
    await expect(page.locator('.admin-panel > h2')).toHaveText('휴지통');
    const list = page.getByRole('list', { name: '휴지통' });
    await expect(list.getByRole('listitem')).toHaveCount(2);
    await expect(list.getByRole('listitem').first()).toContainText('휴지통으로 갈 할 일');
    await expect(list.getByRole('listitem').first()).toContainText('할 일 · 진행 중');
    await expect(list.getByRole('listitem').nth(1)).toContainText('주간 보고 초안');
    await expect(list.getByRole('listitem').nth(1)).toContainText(
      '프롬프트 템플릿 · 나만의 프롬프트 · 예약 1개 포함',
    );
    await page.screenshot({ path: 'test-results/admin-trash.png', fullPage: true });
    await list.getByRole('button', { name: '주간 보고 초안 복원' }).click();
    await expect(page.getByText('‘주간 보고 초안’ 프롬프트 템플릿을 복원했습니다.')).toBeVisible();
    await expect(list.getByRole('listitem')).toHaveCount(1);
    const schedules =
      await request<{ name: string; enabled: boolean; next_run_at: string }[]>('schedules.list');
    expect(schedules.map((item) => [item.name, item.enabled])).toEqual([['금요일 보고', true]]);
    // Its next run is the rule's next from now, not a time that passed.
    expect(Date.parse(schedules[0].next_run_at)).toBeGreaterThan(Date.now());
    await page.getByRole('tab', { name: '프롬프트 템플릿' }).click();
    await expect(page.getByRole('button', { name: '주간 보고 초안 삭제' })).toBeVisible();

    // Deleting for good asks first, with a button an AI tool presses only at the "full" level (data-destructive).
    await page.getByRole('tab', { name: '휴지통' }).click();
    await list.getByRole('button', { name: '휴지통으로 갈 할 일 영구 삭제' }).click();
    const purge = page.getByRole('group', { name: '영구 삭제할까요? 되돌릴 수 없습니다.' });
    await expect(purge.getByRole('button', { name: '영구 삭제', exact: true })).toHaveAttribute(
      'data-destructive',
      'true',
    );
    await purge.getByRole('button', { name: '영구 삭제', exact: true }).click();
    await expect(page.getByText('‘휴지통으로 갈 할 일’ 할 일을 영구 삭제했습니다.')).toBeVisible();
    await expect(page.getByText('휴지통이 비어 있습니다.')).toBeVisible();
    expect(await request<unknown[]>('tasks.list')).toEqual([]);

    // Emptying the trash takes everything in it at once.
    for (const title of ['하나', '둘']) {
      const made = await request<{ id: string }>('tasks.save', { title });
      await request('tasks.delete', { id: made.id });
    }
    await goHome(page);
    await openAdmin(page, '휴지통');
    await expect(list.getByRole('listitem')).toHaveCount(2);
    await page.getByRole('button', { name: '휴지통 비우기' }).click();
    const empty = page.getByRole('group', {
      name: '휴지통의 2개를 모두 영구 삭제할까요? 되돌릴 수 없습니다.',
    });
    await expect(empty.getByRole('button', { name: '휴지통 비우기' })).toHaveAttribute(
      'data-destructive',
      'true',
    );
    await empty.getByRole('button', { name: '휴지통 비우기' }).click();
    await expect(page.getByText('휴지통의 2개를 영구 삭제했습니다.')).toBeVisible();
    await expect(page.getByText('휴지통이 비어 있습니다.')).toBeVisible();
    expect((await request<Entries>('trash.list')).total).toBe(0);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
