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
const DONE = '페이지를 열고 광고를 기록했습니다.';
const SLOW = '천천히 답해 줘';
const SLOWLY = '천천히 답했습니다.';

async function request<T>(page: Page, method: string, params: Record<string, unknown> = {}) {
  const response = await page.evaluate(
    ([name, values]) => window.privateAgent!.request(name, values),
    [method, params] as const,
  );
  expect(response.ok, `${method}: ${JSON.stringify(response)}`).toBe(true);
  return response.result as T;
}

test('agent chat: the agent opens a page and records its ad live, and the record is kept and exported', async () => {
  test.setTimeout(240_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-chat-'));
  const site = createServer((incoming, response) => {
    const found = incoming.url === '/search';
    response.writeHead(found ? 200 : 404, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(found ? PAGE : 'not found');
  });
  await new Promise<void>((resolve) => site.listen(0, '127.0.0.1', resolve));
  const address = `http://127.0.0.1:${(site.address() as { port: number }).port}/search`;
  // A dev run without the bundled Hermes falls back to one JSON plan of app actions.
  const actions = [
    { tool: 'open_site', args: { url: address } },
    { tool: 'capture_page', args: {} },
  ];
  // An answer the test holds back, to leave the chat and load it again while the turn runs.
  let release!: (text: string) => void;
  const held = new Promise<string>((resolve) => (release = resolve));
  // The bundled Hermes acts live with its tools: the model has it open the page, then record it, then answer. A
  // source run without the Hermes build asks for the plan as JSON (no tools in the request).
  const llm = await llmServer(async ({ content, payload }) => {
    if (content.includes(SLOW)) {
      const text = await held;
      return payload.tools?.length ? text : { message: text, actions: [] };
    }
    if (!payload.tools?.length) return { message: DONE, actions };
    const ran = payload.messages.filter((message) => message.role === 'tool').length;
    if (ran === 0) return toolCalls(['open_site', { url: address }]);
    if (ran === 1) return toolCalls(['capture_page', {}]);
    return DONE;
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
    // The agent runs the actions itself and answers once done; nothing waits for 확인 후 실행.
    await expect(log).toContainText(DONE, { timeout: 120_000 });
    const card = (title: string) => log.locator('.agent-card').filter({ hasText: title });
    const open = card(`${address} 열기`);
    const record = card('브라우저에 열린 페이지 기록');
    await expect(open).toContainText('완료');
    await expect(open).toContainText('브라우저 창에서 열었습니다.');
    await expect(record).toContainText('완료');
    await expect(record).toContainText('광고 1건');
    await expect(record.getByRole('list', { name: '기록한 광고' })).toContainText(
      '특가 운동화 모음',
    );
    // The actions ran without an approval card, and the browser was opened by the agent.
    await expect(log.getByRole('button', { name: '확인 후 실행' })).toHaveCount(0);
    expect((await request<{ open: boolean }>(page, 'web.status')).open).toBe(true);

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
    await expect(log).toContainText(DONE);

    // A turn goes on while another screen is shown: back on the chat it is still there, once, with 중지.
    const screens = page.getByRole('navigation', { name: '화면' });
    const pending = log.locator('.agent-message.pending');
    await chatBox(page).fill(SLOW);
    await chatBox(page).press('Enter');
    await expect(pending).toBeVisible();
    await screens.getByRole('button', { name: '웹 기록', exact: true }).click();
    await expect(page.getByRole('heading', { name: '웹 기록', level: 1 })).toBeVisible();
    await screens.getByRole('button', { name: '채팅', exact: true }).click();
    await expect(pending).toBeVisible();
    await expect(log.locator('.agent-message.user').filter({ hasText: SLOW })).toHaveCount(1);
    await expect(page.getByRole('button', { name: '중지' })).toBeVisible();
    await expect(chatBox(page)).toBeDisabled();
    // A chat loaded again while the turn runs finds it in the backend and follows it.
    await page.reload();
    await expect(chatBox(page)).toBeVisible();
    await expect(pending).toBeVisible();
    await expect(log.locator('.agent-message.user').filter({ hasText: SLOW })).toHaveCount(1);
    await expect(page.getByRole('button', { name: '중지' })).toBeVisible();
    // It ends while the user is elsewhere: a message says so, with the way back to the answer.
    await screens.getByRole('button', { name: '웹 기록', exact: true }).click();
    await expect(page.getByRole('heading', { name: '웹 기록', level: 1 })).toBeVisible();
    release(SLOWLY);
    const ended = page
      .getByRole('status')
      .filter({ hasText: '채팅 에이전트가 작업을 마쳤습니다.' });
    await ended.getByRole('button', { name: '채팅 열기' }).click();
    await expect(log).toContainText(SLOWLY);
    await expect(pending).toHaveCount(0);
    await expect(chatBox(page)).toBeEnabled();

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
