import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { chatBox, chooseStorage, connectLlm, launch, llmServer, toolCalls } from './helpers';

// A page of another site whose ad only its label tells apart. The words and links are made up (example.test).
const PAGE = `<!doctype html><title>운동화 검색</title>
  <div class="card"><div class="badge">광고</div><a href="https://ad.example.test/x">특가 운동화 모음</a></div>
  <div class="card"><a href="https://shop.example.test/y">일반 운동화</a></div>`;
const PLANNED = '페이지를 열고 기록할 준비를 했습니다. 확인 후 실행을 눌러 주세요.';

async function request<T>(page: Page, method: string, params: Record<string, unknown> = {}) {
  const response = await page.evaluate(
    ([name, values]) => window.privateAgent!.request(name, values),
    [method, params] as const,
  );
  expect(response.ok, `${method}: ${JSON.stringify(response)}`).toBe(true);
  return response.result as T;
}

test('agent chat: proposed actions run only once approved, and the page they record is kept and exported', async () => {
  test.setTimeout(240_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-chat-'));
  const site = createServer((incoming, response) => {
    const found = incoming.url === '/search';
    response.writeHead(found ? 200 : 404, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(found ? PAGE : 'not found');
  });
  await new Promise<void>((resolve) => site.listen(0, '127.0.0.1', resolve));
  const address = `http://127.0.0.1:${(site.address() as { port: number }).port}/search`;
  const actions = [
    { tool: 'open_site', args: { url: address } },
    { tool: 'capture_page', args: {} },
    { tool: 'add_task', args: { title: '운동화 가격 비교' } },
  ];
  // The bundled Hermes plans with its prepare_actions tool and then says what it prepared; a source run without the
  // Hermes build asks for the plan as JSON.
  const llm = await llmServer(({ payload }) => {
    if (payload.messages.some((message) => message.role === 'tool')) return PLANNED;
    if (payload.tools?.some((tool) => tool.function.name === 'prepare_actions'))
      return toolCalls(['prepare_actions', { actions }]);
    return { message: PLANNED, actions };
  });
  let app: ElectronApplication | undefined;
  try {
    // The browser of the web records runs without a window here; the user's runs show it.
    app = await launch(directory, { PRIVATE_AGENT_WEB_HEADLESS: '1' });
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);

    // The chat is the first screen after a start or a reload, with examples while it is empty.
    await page.reload();
    await expect(chatBox(page)).toBeVisible();
    await expect(
      page
        .getByRole('navigation', { name: '화면' })
        .getByRole('button', { name: '채팅', exact: true }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(page.getByText('무엇을 맡길까요?', { exact: true })).toBeVisible();
    await expect(page.getByRole('group', { name: '예시' })).toContainText(
      '쿠팡에서 노트북 광고 기록해 줘',
    );
    const engine = (await request<{ engine: string; ready: boolean }>(page, 'agent.engine')).engine;
    await expect(page.locator('.agent-engine')).toContainText(
      engine === 'hermes' ? 'Hermes' : '기본 계획기',
    );

    await chatBox(page).fill(`이 페이지를 열어서 광고를 기록해 줘: ${address}`);
    await chatBox(page).press('Enter');
    const log = page.getByRole('log', { name: '대화' });
    await expect(log).toContainText(PLANNED, { timeout: 60_000 });
    const card = (title: string) => log.locator('.agent-card').filter({ hasText: title });
    const open = card(`${address} 열기`);
    const record = card('브라우저에 열린 페이지 기록');
    const task = card('할 일 추가: 운동화 가격 비교');
    for (const proposed of [open, record, task]) await expect(proposed).toContainText('승인 대기');
    await expect(open).toContainText('앱은 광고를 누르지 않습니다.');

    // Nothing ran before the user said so: no browser, no task.
    expect((await request<{ open: boolean }>(page, 'web.status')).open).toBe(false);
    const titles = async () =>
      (await request<{ title: string }[]>(page, 'tasks.list')).map((item) => item.title);
    expect(await titles()).not.toContain('운동화 가격 비교');

    // 취소 leaves the task out.
    await task.getByRole('button', { name: '취소' }).click();
    await expect(task).toContainText('취소함');
    await expect(task.getByRole('button', { name: '확인 후 실행' })).toHaveCount(0);
    expect(await titles()).not.toContain('운동화 가격 비교');

    // 확인 후 실행 opens the address in the browser, then records what the page shows without clicking it.
    await open.getByRole('button', { name: '확인 후 실행' }).click();
    await expect(open).toContainText('완료', { timeout: 90_000 });
    await expect(open).toContainText('브라우저 창에서 열었습니다.');
    expect((await request<{ open: boolean }>(page, 'web.status')).open).toBe(true);
    await record.getByRole('button', { name: '확인 후 실행' }).click();
    await expect(record).toContainText('완료', { timeout: 60_000 });
    await expect(record).toContainText('광고 1건');
    await expect(record.getByRole('list', { name: '기록한 광고' })).toContainText(
      '특가 운동화 모음',
    );

    // The record is in the web records, a page at a time, and goes to a CSV file in the Wiki folder.
    await record.getByRole('button', { name: '웹 기록에서 보기' }).click();
    await expect(page.getByRole('heading', { name: '웹 기록', level: 1 })).toBeVisible();
    await expect(page.getByText('기록 하나만 보는 중입니다.')).toBeVisible();
    const rows = page.getByRole('table').locator('tbody tr');
    await expect(rows.filter({ hasText: '특가 운동화 모음' })).toContainText('광고');
    await page.getByLabel('종류').selectOption('ad');
    await expect(rows).toHaveCount(1);
    await page.getByRole('button', { name: 'CSV 내보내기' }).click();
    const saved = page.getByText(/기록 1건을 CSV로 저장했습니다: /);
    await expect(saved).toBeVisible();
    const file = (await saved.textContent())!.split('저장했습니다: ')[1].trim();
    expect(path.dirname(file)).toBe(path.join(directory, 'wiki', 'exports'));
    const csv = await readFile(file, 'utf8');
    expect(csv.startsWith('﻿')).toBe(true);
    expect(csv).toContain('특가 운동화 모음');
    expect(csv).not.toContain('일반 운동화');
    const captures = page.getByRole('complementary', { name: '최근 기록한 페이지' });
    await expect(captures).toContainText('광고 1');

    // The conversation and what its actions did are kept: after a reload the chat shows them as they ended.
    await page.reload();
    await expect(chatBox(page)).toBeVisible();
    await expect(open).toContainText('완료');
    await expect(record).toContainText('광고 1건');
    await expect(task).toContainText('취소함');

    // Clearing the conversation asks first, with the button an AI tool may press only at the "full" access level.
    await page.getByRole('button', { name: '대화 비우기' }).click();
    const confirm = page.getByRole('group', { name: /대화를 모두 지울까요\?/ });
    await expect(confirm.locator('[data-destructive]')).toHaveCount(1);
    await confirm.locator('[data-destructive]').click();
    await expect(page.getByText('무엇을 맡길까요?', { exact: true })).toBeVisible();
    expect(await request<unknown[]>(page, 'agent.messages')).toEqual([]);
    // The records stay, until the user deletes one with its screenshot.
    const kept = await request<{ captures: { id: string; screenshot: string }[] }>(
      page,
      'web.captures',
    );
    expect(kept.captures).toHaveLength(1);
    const screenshot = path.join(directory, 'wiki', kept.captures[0].screenshot);
    expect((await stat(screenshot)).size).toBeGreaterThan(0);
    await page
      .getByRole('navigation', { name: '화면' })
      .getByRole('button', { name: '웹 기록', exact: true })
      .click();
    await page
      .getByRole('complementary', { name: '최근 기록한 페이지' })
      .getByRole('button', { name: /광고 1/ })
      .click();
    await page.getByRole('button', { name: '이 기록 지우기' }).click();
    const question = page.getByRole('group', { name: /이 페이지의 기록과 스크린샷을 지울까요\?/ });
    await question.locator('[data-destructive]').click();
    await expect(page.getByText('기록과 스크린샷을 지웠습니다.')).toBeVisible();
    await expect(page.getByText('기록이 없습니다', { exact: true })).toBeVisible();
    await expect(stat(screenshot)).rejects.toThrow();
    expect((await request<{ total: number }>(page, 'web.records')).total).toBe(0);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await new Promise<void>((resolve) => site.close(() => resolve()));
    await rm(directory, { recursive: true, force: true });
  }
});
