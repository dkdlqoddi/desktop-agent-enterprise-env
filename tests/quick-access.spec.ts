import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { QuickAccess } from '../src/lib/api/types';
import { chatBox, chooseStorage, drawer, goHome, launch, openAdmin, widget } from './helpers';

// Settles once the app's process has ended, however it was closed: the backend is stopped first, which takes a moment.
function ended(app: ElectronApplication, ms = 60_000) {
  const child = app.process();
  return new Promise<void>((resolve, reject) => {
    if (child.exitCode !== null || child.signalCode !== null) return resolve();
    const timer = setTimeout(
      () => reject(new Error(`앱이 ${ms / 1000}초 안에 끝나지 않았습니다.`)),
      ms,
    );
    child.once('exit', () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

const quickState = async (page: Page) => {
  const response = await page.evaluate(() => window.privateAgent!.request('quick.get'));
  expect(response.ok).toBe(true);
  return response.result as QuickAccess;
};

// The card on the admin page (관리 → 화면·검색·대시보드).
const card = (page: Page) => page.getByRole('group', { name: '빠른 호출' });
const shortcutBox = (page: Page) => card(page).getByLabel('채팅 바로 가기 키');
const keepRunning = (page: Page) =>
  card(page).getByRole('switch', { name: '창을 닫아도 계속 실행' });
const registrationFailed = (page: Page) =>
  card(page).getByText('이 키를 등록하지 못했습니다', { exact: false });
const shortcutOff = (page: Page) => card(page).getByText('꺼져 있습니다', { exact: false });

const held = (app: ElectronApplication, keys: string[]) =>
  app.evaluate(
    ({ globalShortcut }, list) => list.map((key) => globalShortcut.isRegistered(key)),
    keys,
  );

// What the shortcut and the tray's 채팅 열기 send the window (electron/quick-access.cjs): the shortcut itself is the
// system's, which Playwright's keyboard does not reach.
const chatFocus = (app: ElectronApplication) =>
  app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].webContents.send('chat:focus'),
  );

test('quick access: the chat from anywhere, its settings kept, and the app closing as before', async () => {
  test.setTimeout(240_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-quick-'));
  const file = path.join(directory, 'quick-access.json');
  const saved = async () => JSON.parse(await readFile(file, 'utf8'));
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    let page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));

    // The card, with the defaults: no shortcut, the tray icon on, the app closing with its window. A fresh start holds
    // no key of the system's at all; the presets are offered, the first one first.
    await openAdmin(page, '화면·검색·대시보드');
    await expect(shortcutBox(page)).toHaveValue('');
    await expect(shortcutOff(page)).toBeVisible();
    await expect(card(page).getByRole('switch', { name: '트레이 아이콘' })).toBeChecked();
    await expect(keepRunning(page)).not.toBeChecked();
    const fresh = await quickState(page);
    expect(fresh.registered).toBe(false);
    const presets = fresh.presets.map((preset) => preset.shortcut);
    expect(presets[0]).toBe('CommandOrControl+Shift+Space');
    expect(await held(app, presets)).toEqual(presets.map(() => false));
    await expect(shortcutBox(page).locator('option').first()).toHaveAttribute(
      'value',
      'CommandOrControl+Shift+Space',
    );
    await expect(registrationFailed(page)).toHaveCount(0);

    // Chosen, the first preset is held where this system lets the app have it, and the screen says what the main
    // process holds.
    await shortcutBox(page).selectOption('CommandOrControl+Shift+Space');
    await expect
      .poll(async () => (await quickState(page)).shortcut)
      .toBe('CommandOrControl+Shift+Space');
    await expect(shortcutBox(page)).toBeEnabled();
    await expect(shortcutOff(page)).toHaveCount(0);
    const [registers] = await held(app, ['CommandOrControl+Shift+Space']);
    expect((await quickState(page)).registered).toBe(registers);
    await expect(registrationFailed(page)).toHaveCount(registers ? 0 : 1);
    if (registers) {
      // A shortcut another program holds is not registered, and the card says so; 다시 시도 takes it once it is free.
      const taken = 'CommandOrControl+Alt+A';
      expect(
        await app.evaluate(
          ({ globalShortcut }, key) => globalShortcut.register(key, () => {}),
          taken,
        ),
      ).toBe(true);
      await shortcutBox(page).selectOption(taken);
      await expect
        .poll(() => quickState(page))
        .toMatchObject({ shortcut: taken, registered: false });
      // The card has the answer once the box can be used again.
      await expect(shortcutBox(page)).toBeEnabled();
      await expect(registrationFailed(page)).toBeVisible();
      // The one chosen before went back to the system.
      expect(await held(app, ['CommandOrControl+Shift+Space'])).toEqual([false]);
      await app.evaluate(({ globalShortcut }, key) => globalShortcut.unregister(key), taken);
      await card(page).getByRole('button', { name: '다시 시도' }).click();
      await expect.poll(async () => (await quickState(page)).registered).toBe(true);
      await expect(shortcutBox(page)).toBeEnabled();
      await expect(registrationFailed(page)).toHaveCount(0);
    }

    // Another preset is saved, held in place of the last one, and still chosen after a reload.
    await shortcutBox(page).selectOption('Alt+Space');
    await expect.poll(saved).toEqual({ shortcut: 'Alt+Space', tray: true, keep_running: false });
    await expect.poll(async () => (await quickState(page)).shortcut).toBe('Alt+Space');
    const [altSpace, last] = await held(app, ['Alt+Space', 'CommandOrControl+Alt+A']);
    expect(last).toBe(false);
    // Held where this system lets the app have it, and said as it is.
    expect((await quickState(page)).registered).toBe(altSpace);
    await page.reload();
    await expect(chatBox(page)).toBeVisible();
    await openAdmin(page, '화면·검색·대시보드');
    await expect(shortcutBox(page)).toHaveValue('Alt+Space');

    // The shortcut's and the tray's request shows the chat, with the caret in its message box; nothing is typed.
    await chatFocus(app);
    await expect(chatBox(page)).toBeFocused();
    await expect(chatBox(page)).toHaveValue('');
    // A document open to read closes for the chat.
    const document = await page.evaluate(() =>
      window.privateAgent!.request('wiki.save', {
        title: '빠른 호출 문서',
        content: '읽기만 하는 문서입니다.',
      }),
    );
    expect(document.ok).toBe(true);
    await page.reload();
    await expect(chatBox(page)).toBeVisible();
    await goHome(page);
    await widget(page, 'documents').locator('.document-row').click();
    await expect(drawer(page)).toBeVisible();
    await chatFocus(app);
    await expect(drawer(page)).toHaveCount(0);
    await expect(chatBox(page)).toBeFocused();
    // One being written stays in front of it, with a word; once it is closed, the chat is there.
    await page.getByRole('button', { name: '새 문서 작성', exact: true }).click();
    await expect(drawer(page).getByLabel('문서 제목', { exact: true })).toBeVisible();
    await chatFocus(app);
    await expect(
      page.getByText('작성 중인 문서가 있어 문서 창을 그대로 두었습니다', { exact: false }),
    ).toBeVisible();
    await expect(drawer(page).getByLabel('문서 제목', { exact: true })).toBeVisible();
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();
    await expect(drawer(page)).toHaveCount(0);
    await expect(chatBox(page)).toBeVisible();

    // With the option off, closing the window ends the app as it always has.
    expect(errors).toEqual([]);
    let end = ended(app);
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
    await end;

    // Started again, with the settings kept. Kept running in the tray, closing the window hides it and the app goes
    // on, backend and all; the tray's 종료 (app.quit) ends it.
    app = await launch(directory);
    page = await app.firstWindow();
    await expect(chatBox(page)).toBeVisible({ timeout: 30_000 });
    const state = await quickState(page);
    expect(state.shortcut).toBe('Alt+Space');
    await openAdmin(page, '화면·검색·대시보드');
    if (state.tray_available) {
      await keepRunning(page).click();
      await expect(keepRunning(page)).toBeChecked();
      await expect.poll(saved).toEqual({ shortcut: 'Alt+Space', tray: true, keep_running: true });
      await expect.poll(async () => (await quickState(page)).keep_running).toBe(true);
      await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
      await expect
        .poll(() =>
          app!.evaluate(({ BrowserWindow }) =>
            BrowserWindow.getAllWindows().map((window) => window.isVisible()),
          ),
        )
        .toEqual([false]);
      expect((await page.evaluate(() => window.privateAgent!.request('health'))).ok).toBe(true);
      end = ended(app);
      await app.evaluate(({ app: electronApp }) => electronApp.quit());
      await end;
    } else {
      // No tray on this system: its two switches are off limits, and closing the window ends the app.
      await expect(card(page).getByText('이 환경에서는 트레이를 쓸 수 없습니다')).toBeVisible();
      await expect(keepRunning(page)).toBeDisabled();
      end = ended(app);
      await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
      await end;
    }
  } finally {
    await app?.close().catch(() => {});
    await rm(directory, { recursive: true, force: true });
  }
});
