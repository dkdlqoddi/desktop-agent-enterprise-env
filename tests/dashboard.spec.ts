import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { addWidget, chooseStorage, goHome, launch, widget } from './helpers';

// The widgets as placed: id, format, width and height.
const placed = (page: Page) =>
  page.locator('section[data-widget]').evaluateAll((items) =>
    items.map(
      (item) =>
        `${item.getAttribute('data-widget')}:${item.getAttribute('data-view')}:${
          item.className.match(/span-(\w+)/)![1]
        }${
          item.className
            .match(/ h-(\w+)/)?.[0]
            .trim()
            .replace('h-', ':h-') ?? ''
        }`,
    ),
  );

test('dashboard: templates, one content in many formats, widths, heights, undo and an earlier layout', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-dashboard-'));
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    let page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));

    // Tasks with deadlines this week, one overdue, one finished today, one paused, one reminder to come; two
    // documents, one of them imported in another way than written.
    await page.evaluate(async () => {
      const request = window.privateAgent!.request;
      const at = (days: number, hours: number) => {
        const day = new Date();
        day.setDate(day.getDate() + days);
        day.setHours(hours, 0, 0, 0);
        return day.toISOString();
      };
      const task = (title: string, extra: Record<string, unknown> = {}) =>
        request('tasks.save', {
          id: '',
          title,
          notes: '',
          assignee: '',
          status: 'open',
          due_at: null,
          remind_at: null,
          repeat: 'none',
          source_id: null,
          ...extra,
        });
      await task('주간 보고서 작성', { due_at: at(2, 18), assignee: '홍길동' });
      await task('회의록 정리', { due_at: at(-2, 18) });
      await task('예산안 검토', { due_at: at(3, 10), remind_at: at(1, 9) });
      await task('발표 자료 다듬기', { status: 'done' });
      await task('외부 일정 확인', { status: 'paused' });
      await request('wiki.save', {
        title: '캐시 도입 회의',
        content: '# 캐시 도입 회의\n\nPython API에 캐시를 도입하기로 결정했습니다.',
      });
      await request('wiki.save', { title: '분기 계획', content: '분기 목표와 일정 초안' });
    });
    await page.reload();
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible();
    const caption = page.locator('.dashboard-caption');
    await expect(caption).toContainText('기본 템플릿 · 위젯 6개');
    expect(await placed(page)).toEqual([
      'welcome:banner:xl',
      'shortcuts:tiles:xl',
      'briefing:full:l',
      'reminders:list:s',
      'tasks:list:m',
      'documents:list:m',
    ]);

    // A template replaces the layout at once; 되돌리기 brings the one before back.
    await page.getByRole('button', { name: '위젯 편집' }).click();
    const templates = page.getByRole('region', { name: '대시보드 템플릿' });
    await expect(templates.getByRole('button')).toHaveCount(6);
    await expect(templates.getByRole('button', { name: /^기본/ })).toHaveAttribute(
      'aria-current',
      'true',
    );
    await page.screenshot({ path: 'test-results/dashboard-editor.png', fullPage: true });
    await templates.getByRole('button', { name: /^오늘 집중/ }).click();
    await expect(page.locator('.editor-status')).toContainText('‘오늘 집중’ 템플릿으로 바꿨습니다');
    expect(await placed(page)).toEqual([
      'welcome:strip:xl',
      'briefing:stats:l',
      'briefing-2:progress:s',
      'tasks:checklist:m',
      'reminders:agenda:m',
      'tasks-2:week:xl',
    ]);
    await page.getByRole('button', { name: '되돌리기' }).click();
    await expect(caption).toContainText('기본 템플릿');
    await expect(page.getByRole('button', { name: '되돌리기' })).toBeDisabled();

    // Every template draws without an error, each widget labelled by its own heading.
    for (const [title, count] of [
      ['오늘 집중', 6],
      ['지식 관리', 7],
      ['AI 자동화', 6],
      ['한눈에 보기', 9],
      ['간결하게', 3],
      ['기본', 6],
    ] as const) {
      await templates.getByRole('button', { name: new RegExp(`^${title}`) }).click();
      await expect(caption).toContainText(`${title} 템플릿 · 위젯 ${count}개`);
      await page.getByRole('button', { name: '편집 완료' }).click();
      await expect(page.locator('section[data-widget]')).toHaveCount(count);
      await expect(page.locator('.widget-loading')).toHaveCount(0);
      for (const section of await page.locator('section[data-widget]').all()) {
        const label = await section.getAttribute('aria-labelledby');
        await expect(page.locator(`[id="${label}"]`)).toHaveCount(1);
      }
      await page.screenshot({
        path: `test-results/dashboard-template-${title}.png`,
        fullPage: true,
      });
      await page.getByRole('button', { name: '위젯 편집' }).click();
    }
    expect(errors).toEqual([]);

    // The same content in several formats at once, each placed after the others with a numbered id.
    await page.getByRole('button', { name: '편집 완료' }).click();
    await addWidget(page, '할 일', '보드');
    await addWidget(page, '할 일', '주간');
    await addWidget(page, '할 일', '차트');
    await addWidget(page, '할 일', '목록');
    await expect(page.locator('section[data-widget^="tasks"]')).toHaveCount(5);
    const board = widget(page, 'tasks-2');
    await expect(board.getByRole('heading', { name: /할 일 보드/ })).toBeVisible();
    await expect(board.locator('.board-column.open .board-card')).toHaveCount(3);
    await expect(board.locator('.board-column.paused')).toContainText('외부 일정 확인');
    await expect(board.locator('.board-column.done')).toContainText('발표 자료 다듬기');
    const week = widget(page, 'tasks-3');
    await expect(week.locator('.week-late')).toContainText('회의록 정리');
    await expect(week.locator('.week-strip > li')).toHaveCount(7);
    await expect(week.locator('.week-strip')).toContainText('주간 보고서 작성');
    await expect(widget(page, 'tasks-4').locator('.bar-list li')).toHaveCount(3);
    await expect(widget(page, 'tasks-4').locator('.column-chart')).toContainText('오늘 1개 완료');
    // A change in one format shows in the others: the board finishes a task the list then shows done.
    await page.screenshot({ path: 'test-results/dashboard-formats.png', fullPage: true });
    await board.getByRole('button', { name: '예산안 검토 완료' }).click();
    await expect(board.locator('.board-column.done')).toContainText('예산안 검토');
    await expect(widget(page, 'tasks')).not.toContainText('예산안 검토');

    // In the edit bar a widget changes its format, width and height in place; two of the same are numbered.
    await page.getByRole('button', { name: '위젯 편집' }).click();
    await expect(page.getByRole('button', { name: '할 일 2 빼기' })).toBeVisible();
    await page.getByRole('button', { name: '할 일 2 빼기' }).click();
    await page.getByLabel('문서 형식').selectOption('cards');
    await page.getByLabel('최근 문서 크기').selectOption('xl');
    await page.getByLabel('최근 문서 높이').selectOption('s');
    await page.getByLabel('알림 형식').selectOption('stats');
    await page.getByRole('button', { name: '편집 완료' }).click();
    const cards = widget(page, 'documents');
    await expect(cards).toHaveAttribute('data-view', 'cards');
    await expect(cards.locator('.doc-card')).toHaveCount(2);
    await expect.poll(async () => (await cards.boundingBox())!.height).toBe(300);
    await expect(widget(page, 'reminders').locator('.metrics')).toContainText('다음 알림');
    const arranged = await placed(page);
    expect(arranged).toContain('documents:cards:xl:h-s');
    expect(arranged.filter((item) => item.startsWith('tasks'))).toEqual([
      'tasks:list:m',
      'tasks-2:board:xl',
      'tasks-3:week:xl',
      'tasks-4:chart:s',
    ]);

    // Narrow windows never scroll sideways, editing or not.
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.getByRole('button', { name: '위젯 편집' }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({ path: 'test-results/dashboard-editing-narrow.png', fullPage: true });
    await page.getByRole('button', { name: '편집 완료' }).click();
    await page.setViewportSize({ width: 1440, height: 900 });
    expect(errors).toEqual([]);
    await app.close();

    // The layout is kept with the Wiki.
    app = await launch(directory);
    page = await app.firstWindow();
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible({ timeout: 30_000 });
    expect(await placed(page)).toEqual(arranged);
    await expect(page.locator('.dashboard-caption')).toContainText('직접 구성');

    // A layout saved before formats gets the greeting and the shortcuts back on top.
    await page.evaluate(() =>
      window.privateAgent!.request('preferences.save', {
        widgets: [
          { id: 'tasks', size: 'l' },
          { id: 'documents', size: 's' },
        ],
      }),
    );
    await page.reload();
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible();
    expect(await placed(page)).toEqual([
      'welcome:banner:xl',
      'shortcuts:tiles:xl',
      'tasks:list:l',
      'documents:list:s',
    ]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

// Preferences written straight into the Wiki, as an earlier version or a damaged record left them.
const STORE = `
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("INSERT INTO metadata VALUES ('preferences', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
           (sys.argv[2],))
db.commit()
`;

test('dashboard: a part that fails to draw, layouts of other versions, the widget limit and a refused save', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-dashboard-'));
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    const nav = page.getByRole('navigation', { name: '화면' });
    const request = (method: string, params: Record<string, unknown> = {}) =>
      page.evaluate(([method, params]) => window.privateAgent!.request(method, params), [
        method,
        params,
      ] as const);
    const saved = async () =>
      ((await request('preferences.get')) as { result: { widgets: unknown } }).result.widgets;

    // A widget that fails to draw shows why in its place, with 다시 시도; the rest of the dashboard stays.
    // (The greeting's date is the one long weekday the screens write.)
    await page.evaluate(() => {
      const format = Object.getOwnPropertyDescriptor(Intl.DateTimeFormat.prototype, 'format')!;
      const test = window as unknown as { broken: boolean };
      test.broken = true;
      Object.defineProperty(Intl.DateTimeFormat.prototype, 'format', {
        configurable: true,
        get(this: Intl.DateTimeFormat) {
          if (test.broken && this.resolvedOptions().weekday === 'long')
            throw new Error('시험용 오류');
          return format.get!.call(this);
        },
      });
    });
    await nav.getByRole('button', { name: '할 일', exact: true }).click();
    await goHome(page);
    const failed = widget(page, 'welcome').getByRole('alert');
    await expect(failed).toContainText('이 위젯을 표시하지 못했습니다.');
    await expect(failed).toContainText('시험용 오류');
    await expect(widget(page, 'tasks').getByRole('heading', { name: '할 일' })).toBeVisible();
    await page.evaluate(() => {
      (window as unknown as { broken: boolean }).broken = false;
    });
    await failed.getByRole('button', { name: '다시 시도' }).click();
    await expect(widget(page, 'welcome').locator('.today-pill')).toBeVisible();
    await expect(widget(page, 'welcome').getByRole('alert')).toHaveCount(0);

    // Outside every such part the whole screen says so in Korean and draws the app again in place: no form, no
    // address to load, both of which the window refuses. (The profile button writes the user's initial.)
    await page.evaluate(() => {
      const upper = String.prototype.toUpperCase;
      const test = window as unknown as { upper: boolean };
      test.upper = true;
      String.prototype.toUpperCase = function (this: string) {
        if (test.upper) throw new Error('화면 전체 시험 오류');
        return upper.call(this);
      };
    });
    await nav.getByRole('button', { name: '할 일', exact: true }).click();
    await expect(page.getByRole('heading', { name: '화면을 표시하지 못했습니다' })).toBeVisible();
    await expect(page.locator('form')).toHaveCount(0);
    await page.evaluate(() => {
      (window as unknown as { upper: boolean }).upper = false;
    });
    await page.getByRole('button', { name: '다시 시도' }).click();
    await expect(nav).toBeVisible({ timeout: 15_000 });
    await goHome(page);
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible();

    // A layout saved before formats that names the greeting itself keeps one of it, and saves again.
    await request('preferences.save', {
      widgets: [
        { id: 'welcome', size: 'xl' },
        { id: 'tasks', size: 'm' },
      ],
    });
    await page.reload();
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible();
    expect(await placed(page)).toEqual(['shortcuts:tiles:xl', 'welcome:banner:xl', 'tasks:list:m']);
    await page.getByRole('button', { name: '위젯 편집' }).click();
    await page.getByLabel('할 일 크기').selectOption('l');
    await page.getByRole('button', { name: '편집 완료' }).click();
    await expect.poll(saved).toEqual([
      { id: 'shortcuts', type: 'shortcuts', view: 'tiles', size: 'xl' },
      { id: 'welcome', type: 'welcome', view: 'banner', size: 'xl' },
      { id: 'tasks', type: 'tasks', view: 'list', size: 'l' },
    ]);

    // Names every object answers (`constructor`), left by an earlier version, are left out instead of ending the app.
    const python = path.resolve(
      'backend',
      '.venv',
      process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
    );
    const stored = spawnSync(
      python,
      [
        '-c',
        STORE,
        path.join(directory, 'wiki', 'wiki.db'),
        JSON.stringify({
          widgets: [
            { id: 'constructor', type: 'constructor', size: 'm' },
            { id: 'tasks', type: 'tasks', view: 'constructor', size: 'm' },
            { id: 'documents', type: 'documents', view: 'list', size: 'm' },
          ],
        }),
      ],
      { encoding: 'utf8', env: { ...process.env, PYTHONPATH: '' } },
    );
    expect(stored.stderr).toBe('');
    await page.reload();
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible();
    expect(await placed(page)).toEqual(['documents:list:m']);

    // Widgets and formats of a newer version stay saved through an edit, where they were; ↓ keeps the keyboard on the
    // moved widget.
    await request('preferences.save', {
      widgets: [
        { id: 'tasks', type: 'tasks', view: 'list', size: 'm' },
        { id: 'radar', type: 'radar', view: 'sweep', size: 'm' },
        { id: 'tasks-2', type: 'tasks', view: 'gantt', size: 's' },
        { id: 'documents', type: 'documents', view: 'list', size: 'm' },
      ],
    });
    await page.reload();
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible();
    expect(await placed(page)).toEqual(['tasks:list:m', 'tasks-2:gantt:s', 'documents:list:m']);
    await page.getByRole('button', { name: '위젯 편집' }).click();
    const down = widget(page, 'tasks').locator('button[data-move="down"]');
    await down.focus();
    await page.keyboard.press('Enter');
    await expect
      .poll(() => placed(page))
      .toEqual(['tasks-2:gantt:s', 'tasks:list:m', 'documents:list:m']);
    await expect(down).toBeFocused();
    await expect.poll(saved).toEqual([
      { id: 'tasks-2', type: 'tasks', view: 'gantt', size: 's' },
      { id: 'tasks', type: 'tasks', view: 'list', size: 'm' },
      { id: 'radar', type: 'radar', view: 'sweep', size: 'm' },
      { id: 'documents', type: 'documents', view: 'list', size: 'm' },
    ]);
    // At the end, the other button takes the keyboard.
    await page.keyboard.press('Enter');
    await expect(widget(page, 'tasks').locator('button[data-move="up"]')).toBeFocused();
    await expect.poll(saved).toEqual([
      { id: 'tasks-2', type: 'tasks', view: 'gantt', size: 's' },
      { id: 'documents', type: 'documents', view: 'list', size: 'm' },
      { id: 'tasks', type: 'tasks', view: 'list', size: 'm' },
      { id: 'radar', type: 'radar', view: 'sweep', size: 'm' },
    ]);
    await page.getByRole('button', { name: '편집 완료' }).click();

    // As many widgets as the Wiki keeps, those of a newer version counted: 추가 waits and says why.
    await request('preferences.save', {
      widgets: [
        ...Array.from({ length: 39 }, (_, i) => ({
          id: i ? `tasks-${i + 1}` : 'tasks',
          type: 'tasks',
          view: 'chart',
          size: 's',
        })),
        { id: 'radar', type: 'radar', view: 'sweep', size: 'm' },
      ],
    });
    await page.reload();
    await page.getByRole('button', { name: '위젯 편집' }).click();
    const tray = page.getByRole('region', { name: '추가할 수 있는 위젯' });
    await expect(tray).toContainText('위젯은 40개까지 놓을 수 있습니다');
    await expect(tray.getByRole('button', { name: '할 일 · 목록 추가' })).toBeDisabled();
    await page.getByRole('button', { name: '할 일 현황 39 빼기' }).click();
    await expect(tray.getByRole('button', { name: '할 일 · 목록 추가' })).toBeEnabled();
    expect(errors).toEqual([]);

    // A change the Wiki refused does not stay on screen: the saved layout comes back, with the reason.
    await app.evaluate(({ BrowserWindow, ipcMain }) => {
      const handlers = (ipcMain as unknown as { _invokeHandlers: Map<string, Function> })
        ._invokeHandlers;
      const original = handlers.get('agent:request')!;
      BrowserWindow.getAllWindows()[0].webContents.ipc.handle(
        'agent:request',
        (event, method: string, params: unknown) =>
          method === 'preferences.save'
            ? { ok: false, error: '배치를 저장하지 못했습니다(시험).' }
            : original(event, method, params),
      );
    });
    await page.getByLabel('할 일 현황 1 크기').selectOption('xl');
    await expect(page.locator('.toast.error')).toContainText('배치를 저장하지 못했습니다(시험).');
    await expect(widget(page, 'tasks')).toHaveClass(/span-s/);
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].webContents.ipc.removeHandler('agent:request'),
    );
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
