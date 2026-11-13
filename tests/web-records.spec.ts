import { test, expect, type ElectronApplication } from '@playwright/test';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { chooseStorage, launch } from './helpers';

// A search page of another site whose ad only its label tells apart. The words and links are made up (example.test).
const PAGE = (word: string) => `<!doctype html><title>${word} 검색</title>
  <div class="card"><div class="badge">광고</div><a href="https://ad.example.test/${word}">특가 ${word} 모음</a></div>
  <div class="card"><a href="https://shop.example.test/${word}">일반 ${word}</a></div>`;

test('web records: several deleted together and brought back, a screenshot shown, and how long they are kept', async () => {
  test.setTimeout(180_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-records-'));
  const site = createServer((incoming, response) => {
    const word = (incoming.url ?? '').slice(1);
    const found = ['shoes', 'bags'].includes(word);
    response.writeHead(found ? 200 : 404, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(found ? PAGE(word) : 'not found');
  });
  await new Promise<void>((resolve) => site.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(site.address() as { port: number }).port}`;
  let app: ElectronApplication | undefined;
  try {
    // The browser of the web records runs without a window here; the user's runs show it.
    app = await launch(directory, { PRIVATE_AGENT_WEB_HEADLESS: '1' });
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
    // Two pages recorded as the user records one: opened in the browser, then 열린 페이지 기록.
    for (const word of ['shoes', 'bags']) {
      await request('web.open', { url: `${base}/${word}` });
      await request('web.capture_page', { source: 'manual' });
    }
    const kept = await request<{ captures: { screenshot: string }[] }>('web.captures');
    const shots = kept.captures.map((item) => path.join(directory, 'wiki', item.screenshot));
    expect(shots).toHaveLength(2);
    await page
      .getByRole('navigation', { name: '화면' })
      .getByRole('button', { name: '웹 기록', exact: true })
      .click();
    const captures = page.getByRole('complementary', { name: '최근 기록한 페이지' });
    await expect(captures.getByRole('listitem')).toHaveCount(2);

    // A page's screenshot, in a panel of its own.
    await captures.getByRole('button', { name: '기타 · bags 검색 스크린샷 보기' }).click();
    const viewer = page.getByRole('dialog', { name: '스크린샷' });
    await expect(
      viewer.getByRole('img', { name: '기타 · bags 검색 페이지의 스크린샷' }),
    ).toHaveAttribute('src', /^data:image\/jpeg;base64,/);
    await viewer.getByRole('button', { name: '닫기' }).click();
    await expect(viewer).toHaveCount(0);

    // Both chosen and deleted together after one question; 되돌리기 brings them back with their screenshots.
    await captures.getByRole('checkbox', { name: '모두 선택' }).check();
    await expect(captures.getByText('2개 선택')).toBeVisible();
    await captures.getByRole('button', { name: '지우기' }).click();
    const ask = captures.getByRole('group', {
      name: '선택한 기록 2개를 스크린샷과 함께 휴지통으로 옮길까요? 30일 안에 복원할 수 있습니다.',
    });
    await expect(ask.getByRole('button', { name: '지우기' })).toHaveAttribute(
      'data-destructive',
      'true',
    );
    await ask.getByRole('button', { name: '지우기' }).click();
    const moved = page.getByRole('status').filter({ hasText: '기록 2개를 휴지통으로 옮겼습니다.' });
    await expect(moved).toBeVisible();
    await expect(captures.getByText('아직 없습니다.')).toBeVisible();
    expect((await request<{ total: number }>('web.records')).total).toBe(0);
    for (const shot of shots) expect((await stat(shot)).size).toBeGreaterThan(0);
    await moved.getByRole('button', { name: '되돌리기' }).click();
    await expect(captures.getByRole('listitem')).toHaveCount(2);
    expect((await request<{ total: number }>('web.records')).total).toBeGreaterThan(0);

    // How long they are kept: until deleted, unless the user chooses; the choice stays with the Wiki.
    const keep = captures.getByLabel('보관 기간');
    await expect(keep).toHaveValue('0');
    await keep.selectOption('30');
    await expect(page.getByText(/웹 기록을 30일 동안 보관합니다/)).toBeVisible();
    expect(await request('web.retention')).toEqual({ days: 30 });
    await captures.getByRole('checkbox', { name: '기타 · shoes 검색 선택' }).check();
    await page.screenshot({ path: 'test-results/web-records.png', fullPage: true });
    await page.reload();
    await page
      .getByRole('navigation', { name: '화면' })
      .getByRole('button', { name: '웹 기록', exact: true })
      .click();
    await expect(captures.getByLabel('보관 기간')).toHaveValue('30');
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await new Promise<void>((resolve) => site.close(() => resolve()));
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
