import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtemp, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { jiraServer } from './fixtures/jira.cjs';
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

const sitesOf = async (page: Page) =>
  (
    (await page.evaluate(() => window.privateAgent!.request('sites.list', {}))) as {
      result: {
        sites: { id: string; kind: string; url: string; detected_method: string | null }[];
      };
    }
  ).result.sites;

test('JIRA: per-site account, ticket link check, scopes, Python Playwright import, detected method, persistence and cancel', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'jira-desktop-'));
  const wikiDirectory = path.join(directory, 'wiki');
  const server = await jiraServer();
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    let page = await app.firstWindow();
    await chooseStorage(page, wikiDirectory);
    await addWidget(page, '가져오기');

    // The JIRA account is registered by its address; a pasted ticket link keeps only the site.
    await openAdmin(page, '사이트 계정');
    await page.getByRole('button', { name: '사이트 추가' }).click();
    const form = page.getByRole('form', { name: '사이트 추가' });
    await expect(form.getByRole('radio', { name: 'JIRA' })).toHaveAttribute('aria-checked', 'true');
    await form.getByLabel('주소').fill(server.url);
    await form.getByLabel('이름').fill('팀 JIRA');
    await form.getByLabel('로그인 ID').fill('tester');
    await form.getByLabel('비밀번호', { exact: true }).fill('correct horse');
    await form.getByRole('button', { name: '사이트 저장' }).click();
    // Follows `page` across app restarts.
    const site = () => page.locator('.site').filter({ hasText: '팀 JIRA' });
    await expect(site().locator('.site-url')).toHaveText(server.site);
    expect(await sitesOf(page)).toEqual([
      expect.objectContaining({ kind: 'jira', url: server.site, detected_method: null }),
    ]);

    await goHome(page);
    const importer = widget(page, 'import');
    const input = importer.getByLabel('가져올 링크 또는 경로');
    // Only ticket links import.
    await input.fill(`${server.origin}/jira/issues/?jql=project%3DTEAM`);
    await expect(importer.locator('.import-kind')).toContainText('지원하지 않는 링크');
    await input.fill(server.url);
    await expect(importer.locator('.import-kind')).toHaveText('JIRA 티켓 · TEAM-1');
    await expect(importer.locator('.site-line')).toContainText('팀 JIRA · tester · 자동 감지');
    await expect(importer.getByRole('radio', { name: '이 티켓만' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(server.requests).toEqual([]);
    await importer.getByRole('button', { name: 'Wiki로 가져오기' }).click();
    const job = importer.locator('.job');
    await expect(job).toContainText('가져오기가 끝났습니다', { timeout: 60_000 });
    await expect(job).toContainText('저장 1');
    await expect(job).toContainText('범위 이 티켓만');
    // The backend's browser found the SSO Login link, signed in, and the method was kept.
    await expect(job).toContainText('로그인 팀 JIRA · SSO 링크 · 표준 로그인 폼 (자동 감지)');
    expect(server.logins).toEqual([{ id: 'tester', password: 'correct horse' }]);
    await expect.poll(async () => (await sitesOf(page))[0].detected_method).toBe('sso-link');
    await expect(importer.locator('.site-line')).toContainText(
      '자동 감지됨 · SSO 링크 · 표준 로그인 폼',
    );

    await importer.getByRole('radio', { name: '키워드 검색' }).click();
    await importer.getByLabel('검색 키워드').fill('로그인');
    await page.screenshot({ path: 'test-results/jira-keyword-search.png', fullPage: true });
    await importer.getByRole('button', { name: 'Wiki로 가져오기' }).click();
    await expect(job).toContainText('범위 키워드 “로그인”');
    await expect(job).toContainText('가져오기가 끝났습니다', { timeout: 60_000 });
    await expect(job).toContainText('저장 1');
    await expect(job).toContainText('중복 1');
    await expect(
      job.getByText('검색 조건(JQL): project = "TEAM" AND text ~ "로그인" ORDER BY key ASC'),
    ).toBeVisible();

    // The user search starts from the account's login ID.
    await importer.getByRole('radio', { name: '사용자 검색' }).click();
    await expect(importer.getByLabel('사용자', { exact: true })).toHaveValue('tester');
    await importer.getByLabel('검색 대상').selectOption('assignee');
    await importer.getByRole('button', { name: 'Wiki로 가져오기' }).click();
    await expect(widget(page, 'documents').locator('.document-row')).toHaveCount(3, {
      timeout: 60_000,
    });
    await expect(job).toContainText('범위 담당자 tester');
    await expect(job).toContainText('저장 1');
    await page.screenshot({ path: 'test-results/jira-import.png', fullPage: true });
    expect(await readdir(wikiDirectory)).not.toContain('jira');
    expect(server.requests.some((url) => url.includes('/secure/attachment/'))).toBe(false);

    await widget(page, 'documents').locator('.document-row').first().click();
    await expect(drawer(page).getByText(/가져온 JIRA 티켓 · TEAM-/)).toBeVisible();
    await drawer(page).getByRole('button', { name: '원문 Markdown' }).click();
    const source = drawer(page).getByLabel('변환 Markdown 소스');
    await expect(source).toHaveValue(/```java\nint answer = 1;\n```/);
    await expect(source).toHaveValue(/### 인수 조건\n\n로그인 후 목록이 보여야 한다/);
    await expect(source).toHaveValue(/마지막 댓글 검색어/);
    await expect(source).not.toHaveValue(/secret/);
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();
    // Internal backend methods and the raw login calls are not available to renderer callers.
    for (const method of [
      'jira.import',
      'jira.shutdown',
      'jira.check',
      'jira.login',
      'backend.shutdown',
    ]) {
      const denied = await page.evaluate((name) => window.privateAgent!.request(name, {}), method);
      expect(denied.ok).toBe(false);
    }
    await app.close();

    app = await launch(directory);
    page = await app.firstWindow();
    await expect(widget(page, 'documents').locator('.document-row')).toHaveCount(3);
    await searchBox(page).fill('마지막 댓글 검색어');
    await searchBox(page).press('Enter');
    await expect(results(page).locator('.result-card')).toHaveCount(3);

    // A wrong password is refused at once: the login form showed again, no other method fits the page, and its
    // fields are not sent again.
    await openAdmin(page, '사이트 계정');
    await expect(site()).toContainText('자동 감지됨 · SSO 링크 · 표준 로그인 폼');
    await site().getByRole('button', { name: '팀 JIRA 수정' }).click();
    const edit = page.getByRole('form', { name: '팀 JIRA 수정' });
    await edit.getByLabel('비밀번호', { exact: true }).fill('wrong');
    await edit.getByRole('button', { name: '사이트 저장' }).click();
    const logins = server.logins.length;
    await site().getByRole('button', { name: '로그인 확인' }).click();
    await expect(site().getByRole('status')).toContainText(
      '로그인하지 못했습니다. ID·비밀번호를 확인해 주세요. (시도한 방식: SSO 링크 · 표준 로그인 폼: 로그인 화면이 다시 표시됨)',
      { timeout: 60_000 },
    );
    expect(server.logins.slice(logins)).toEqual([{ id: 'tester', password: 'wrong' }]);
    await expect(site()).toContainText('로그인 실패');
    await page.screenshot({ path: 'test-results/admin-sites-failed.png', fullPage: true });

    // With the right password again, a slow import can be cancelled.
    await site().getByRole('button', { name: '팀 JIRA 수정' }).click();
    await edit.getByLabel('비밀번호', { exact: true }).fill('correct horse');
    await edit.getByRole('button', { name: '사이트 저장' }).click();
    await goHome(page);
    server.options.slow = true;
    await widget(page, 'import').getByLabel('가져올 링크 또는 경로').fill(server.url);
    await widget(page, 'import').getByRole('button', { name: 'Wiki로 가져오기' }).click();
    await widget(page, 'import').getByRole('button', { name: 'JIRA 가져오기 중지' }).click();
    await expect(widget(page, 'import').locator('.job')).toContainText('중지했습니다', {
      timeout: 30_000,
    });

    // A cleared result stays cleared when the import box shows again on another screen.
    await widget(page, 'import').getByRole('button', { name: 'JIRA 결과 지우기' }).click();
    await expect(widget(page, 'import').locator('.job')).toHaveCount(0);
    await openLibrary(page, '자료 가져오기');
    await expect(widget(page, 'import').getByLabel('가져올 링크 또는 경로')).toBeVisible();
    await expect(widget(page, 'import').locator('.job')).toHaveCount(0);
    await goHome(page);
    await expect(widget(page, 'import').getByLabel('가져올 링크 또는 경로')).toBeVisible();
    await expect(widget(page, 'import').locator('.job')).toHaveCount(0);
  } finally {
    await app?.close();
    await server.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

test('JIRA: a login check is stopped midway, also after another section was shown', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'jira-check-stop-'));
  const server = await jiraServer();
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    await chooseStorage(page, path.join(directory, 'wiki'));
    await openAdmin(page, '사이트 계정');
    await page.getByRole('button', { name: '사이트 추가' }).click();
    const form = page.getByRole('form', { name: '사이트 추가' });
    await form.getByLabel('주소').fill(server.site);
    await form.getByLabel('이름').fill('팀 JIRA');
    await form.getByLabel('로그인 ID').fill('tester');
    await form.getByLabel('비밀번호', { exact: true }).fill('correct horse');
    await form.getByRole('button', { name: '사이트 저장' }).click();
    const site = page.locator('.site').filter({ hasText: '팀 JIRA' });
    const check = site.getByRole('button', { name: '로그인 확인', exact: true });
    const stop = site.getByRole('button', { name: '중지', exact: true });
    const toast = page.locator('.toast');

    // The SSO takes the login but never returns to JIRA: the check waits until the user stops it.
    server.options.pending = true;
    await check.click();
    await expect(site.getByRole('button', { name: '로그인 확인 중…' })).toBeDisabled();
    await expect.poll(() => server.logins.length, { timeout: 60_000 }).toBe(1);
    await page.screenshot({ path: 'test-results/admin-sites-check-stop.png', fullPage: true });
    await stop.click();
    await expect(toast).toContainText('로그인 확인을 중지했습니다.');
    await expect(stop).toHaveCount(0);
    await expect(check).toBeEnabled();
    // Nothing was found out about the login, so nothing is recorded.
    await expect(site).toContainText('아직 로그인하지 않음');
    await expect(site.getByRole('status')).toHaveCount(0);
    expect(await sitesOf(page)).toEqual([
      expect.objectContaining({ verified_at: null, last_error: '' }),
    ]);
    await toast.getByRole('button', { name: '알림 닫기' }).click();

    // A check keeps running while another section is shown; back on the accounts it can be stopped.
    await check.click();
    await expect.poll(() => server.logins.length, { timeout: 60_000 }).toBe(2);
    await openAdmin(page, 'LLM 연결');
    await openAdmin(page, '사이트 계정');
    await expect(site.getByRole('button', { name: '로그인 확인 중…' })).toBeDisabled();
    await expect(site.getByRole('button', { name: '팀 JIRA 수정' })).toBeDisabled();
    // Nor can an AI tool delete the account while it signs in.
    const [{ id }] = await sitesOf(page);
    expect(
      await page.evaluate(
        (siteId) => window.privateAgent!.request('sites.delete', { id: siteId }),
        id,
      ),
    ).toEqual({
      ok: false,
      error: '이 계정의 로그인 확인이 끝난 뒤 삭제해 주세요. 확인을 중지해도 됩니다.',
    });
    await stop.click();
    await expect(toast).toContainText('로그인 확인을 중지했습니다.');
    await expect(check).toBeEnabled();
    await expect(stop).toHaveCount(0);

    // The importer is free again: the next check signs in.
    server.options.pending = false;
    await check.click();
    await expect(site.getByRole('status')).toContainText('로그인했습니다', { timeout: 60_000 });
    expect(server.logins).toHaveLength(3);

    // Moved to another address, the account keeps its password only typed again: the form says so first.
    await site.getByRole('button', { name: '팀 JIRA 수정' }).click();
    const edit = page.getByRole('form', { name: '팀 JIRA 수정' });
    await edit.getByLabel('주소').fill(server.site.replace('127.0.0.1', 'localhost'));
    await expect(edit.getByRole('status')).toContainText('저장된 비밀번호를 지웁니다');
    await expect(edit.getByLabel('비밀번호', { exact: true })).toHaveAttribute('required', '');
    await edit.getByLabel('주소').fill(`${server.site}/browse/TEAM-1`);
    await expect(edit.getByRole('status')).toHaveCount(0);
    await edit.getByRole('button', { name: '취소' }).click();

    // Deleted, the account takes its browser profile along: no session of it stays behind.
    const profiles = path.join(directory, 'jira-profiles');
    expect(await readdir(profiles)).toHaveLength(1);
    await site.getByRole('button', { name: '팀 JIRA 삭제' }).click();
    await site
      .getByRole('group', { name: '이 사이트 계정을 삭제할까요?' })
      .getByRole('button', { name: '삭제' })
      .click();
    await expect(site).toHaveCount(0);
    await expect.poll(() => readdir(profiles)).toEqual([]);
  } finally {
    await app?.close();
    await server.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
