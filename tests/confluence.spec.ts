import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { confluenceServer } from './fixtures/confluence.cjs';
import {
  addWidget,
  chooseStorage,
  drawer,
  goHome,
  launch,
  openAdmin,
  openLibrary,
  results,
  searchBox,
  widget,
} from './helpers';

type Sites = {
  sites: {
    login_id: string;
    has_password: boolean;
    method: string;
    detected_method: string | null;
    url: string;
  }[];
  secure_storage: boolean;
};
const sitesOf = async (page: Page) =>
  ((await page.evaluate(() => window.privateAgent!.request('sites.list', {}))) as { result: Sites })
    .result;

test('Confluence: site account with detected login method, import box, subpages, source Markdown and persistence', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'confluence-desktop-'));
  const wikiDirectory = path.join(directory, 'wiki');
  const server = await confluenceServer();
  server.options.sso = true;
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    let page = await app.firstWindow();
    await chooseStorage(page, wikiDirectory);
    await addWidget(page, '가져오기');
    const importer = widget(page, 'import');
    const input = importer.getByLabel('가져올 링크 또는 경로');

    // Only page links import; the box says which account a link would sign in with.
    await input.fill(server.origin + '/confluence/dashboard.action');
    await expect(importer.locator('.import-kind')).toContainText('지원하지 않는 링크');
    await expect(importer.getByRole('button', { name: 'Wiki로 가져오기' })).toHaveCount(0);
    await input.fill(server.url);
    await expect(importer.locator('.import-kind')).toHaveText('Confluence 페이지');
    await expect(importer).toContainText('등록된 계정이 없습니다');

    // The account is registered on the admin page, prefilled from the link.
    await importer.getByRole('button', { name: '계정 등록' }).click();
    const form = page.getByRole('form', { name: '사이트 추가' });
    await expect(form.getByLabel('주소')).toHaveValue(server.url);
    await expect(form.getByRole('radio', { name: 'Confluence' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    await form.getByLabel('로그인 ID').fill('tester');
    await form.getByLabel('비밀번호', { exact: true }).fill('correct horse');
    await expect(form.getByLabel('로그인 방식')).toHaveValue('auto');
    await form.getByRole('button', { name: '사이트 저장' }).click();
    const site = page.locator('.site').filter({ hasText: 'Confluence' });
    // A page link is saved as the site's address.
    await expect(site.locator('.site-url')).toHaveText(server.site);
    await expect(site).toContainText('자동 감지 · 처음 로그인할 때 여러 방식을 시도합니다');
    expect(JSON.stringify(await sitesOf(page))).not.toContain('correct horse');

    // The first login looks for each method's login screen and keeps the one that worked.
    await site.getByRole('button', { name: '로그인 확인' }).click();
    await expect(site).toContainText('로그인했습니다 · 일반 로그인 폼 · 자동 탐색', {
      timeout: 60_000,
    });
    await expect(site).toContainText('자동 감지됨 · 일반 로그인 폼 · 자동 탐색');
    expect(server.logins).toEqual([{ id: 'tester', password: 'correct horse' }]);
    expect((await sitesOf(page)).sites[0].detected_method).toBe('generic');
    await page.screenshot({ path: 'test-results/admin-sites.png', fullPage: true });

    // The import opens the page with the session the login check left in the site's profile: nothing is typed.
    await goHome(page);
    await input.fill(server.url);
    await expect(importer.locator('.site-line')).toContainText(
      'Confluence · tester · 자동 감지됨 · 일반 로그인 폼 · 자동 탐색',
    );
    await importer.getByRole('button', { name: 'Wiki로 가져오기' }).click();
    const job = importer.locator('.job');
    await expect(job).toContainText('가져오기가 끝났습니다', { timeout: 60_000 });
    await expect(job).toContainText('저장 1');
    await expect(job).toContainText('범위 이 페이지만');
    await expect(job).toContainText('로그인 Confluence · 저장된 로그인 세션 사용');
    expect(server.logins).toHaveLength(1);
    await page.screenshot({ path: 'test-results/confluence-import.png', fullPage: true });

    // Subpages: the documents widget refreshes when the background job ends.
    await importer.getByLabel('하위 페이지까지 모두 가져오기').check();
    await importer.getByRole('button', { name: 'Wiki로 가져오기' }).click();
    await expect(widget(page, 'documents').locator('.document-row')).toHaveCount(4, {
      timeout: 30_000,
    });
    await expect(job).toContainText('범위 하위 페이지 포함');
    await expect(job).toContainText('중복 1');

    // A cleared result stays cleared when the import box shows again on another screen.
    await importer.getByRole('button', { name: 'Confluence 결과 지우기' }).click();
    await expect(job).toHaveCount(0);
    await openLibrary(page, '자료 가져오기');
    await expect(input).toBeVisible();
    await expect(job).toHaveCount(0);
    await goHome(page);
    await expect(input).toBeVisible();
    await expect(job).toHaveCount(0);

    // The visible login window, opened from the admin page, shows the site signed in with the session the login
    // check left in the profile: it types the saved login only where that session is gone.
    await openAdmin(page, '사이트 계정');
    const requests = server.requests.length;
    await site.getByRole('button', { name: '로그인 창 열기' }).click();
    await expect(site.getByRole('status')).toContainText('로그인 창이 열려 있습니다');
    await expect
      .poll(() => server.requests.slice(requests).some((path) => path.startsWith('/confluence')), {
        timeout: 30_000,
      })
      .toBe(true);
    expect(server.logins).toHaveLength(1);
    await site.getByRole('button', { name: '로그인 창 닫기' }).click();
    await expect(site.getByRole('button', { name: '로그인 창 열기' })).toBeVisible();
    expect(await readdir(wikiDirectory)).not.toContain('confluence');

    await goHome(page);
    await widget(page, 'documents').locator('.document-row').first().click();
    await expect(drawer(page).getByText('가져온 Confluence 페이지', { exact: true })).toBeVisible();
    await drawer(page).getByRole('button', { name: '원문 Markdown' }).click();
    const source = drawer(page).getByLabel('변환 Markdown 소스');
    await expect(source).toHaveValue(/\|.*\|/);
    await expect(source).toHaveValue(/```java\nint answer = \d;\n```/);
    const download = drawer(page).getByRole('link', { name: '.md 다운로드' });
    await expect(download).toHaveAttribute('download', /\.md$/);
    await expect(download).toHaveAttribute('href', /^data:text\/markdown/);
    const exported = path.join(directory, 'exported.md');
    await app.evaluate(({ session }, filename) => {
      session.defaultSession.once('will-download', (_event, item) => item.setSavePath(filename));
    }, exported);
    await download.click();
    await expect
      .poll(() => readFile(exported, 'utf8').catch(() => ''))
      .toBe(await source.inputValue());
    // Internal browser-import RPC is deliberately unavailable to renderer callers.
    const denied = await page.evaluate(() => window.privateAgent!.request('confluence.import', {}));
    expect(denied.ok).toBe(false);
    await app.close();

    app = await launch(directory);
    page = await app.firstWindow();
    await expect(widget(page, 'documents').locator('.document-row')).toHaveCount(4);
    await searchBox(page).fill('접힌 세부 내용');
    await searchBox(page).press('Enter');
    await expect(results(page).locator('.result-card')).toHaveCount(4);

    // The account comes back after a restart with its detected method; the password only with
    // OS secure storage.
    const state = await sitesOf(page);
    expect(state.sites[0].login_id).toBe('tester');
    expect(state.sites[0].detected_method).toBe('generic');
    expect(state.sites[0].has_password).toBe(state.secure_storage);
    if (state.secure_storage) {
      await widget(page, 'import').getByLabel('가져올 링크 또는 경로').fill(server.url);
      await widget(page, 'import').getByRole('button', { name: 'Wiki로 가져오기' }).click();
      await expect(widget(page, 'import').locator('.job')).toContainText('가져오기가 끝났습니다', {
        timeout: 60_000,
      });
      expect(server.logins.at(-1)).toEqual({ id: 'tester', password: 'correct horse' });
    } else {
      await openAdmin(page, '사이트 계정');
      await expect(page.getByText('OS 보안 저장소를 쓸 수 없어', { exact: false })).toBeVisible();
      await expect(page.locator('.site')).toContainText('비밀번호 없음');
    }
  } finally {
    await app?.close();
    await server.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
