import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  chatBox,
  chooseStorage,
  connectLlm,
  drawer,
  launch,
  llmServer,
  openAdmin,
  toolCalls,
} from './helpers';

// A page of another site whose ad only its label tells apart. The words and links are made up (example.test).
const PAGE = `<!doctype html><title>운동화 검색</title>
  <div class="card"><div class="badge">광고</div><a href="https://ad.example.test/x">특가 운동화 모음</a></div>
  <div class="card"><a href="https://shop.example.test/y">일반 운동화</a></div>`;
const DONE = '페이지를 열고 광고를 기록했습니다.';
const SLOW = '천천히 답해 줘';
const SLOWLY = '천천히 답했습니다.';
const DAY = 86_400_000;

const openChat = (page: Page) =>
  page
    .getByRole('navigation', { name: '화면' })
    .getByRole('button', { name: '채팅', exact: true })
    .click();

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

    // The chat is the first screen after a start or a reload, with examples while the app keeps nothing to offer.
    await page.reload();
    await expect(chatBox(page)).toBeVisible();
    await expect(
      page
        .getByRole('navigation', { name: '화면' })
        .getByRole('button', { name: '채팅', exact: true }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(page.getByText('무엇을 맡길까요?', { exact: true })).toBeVisible();
    await expect(page.getByRole('group', { name: '제안' })).toContainText(
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

    // Clearing the conversation asks first, with the button an AI tool may press only at the "full" access level. It
    // goes to the trash, from where 되돌리기 brings it back as it was.
    const clearChat = async () => {
      await page.getByRole('button', { name: '대화 비우기' }).click();
      const confirm = page.getByRole('group', { name: /대화를 모두 휴지통으로 옮길까요\?/ });
      await expect(confirm.locator('[data-destructive]')).toHaveCount(1);
      await confirm.locator('[data-destructive]').click();
      await expect(page.getByText('무엇을 맡길까요?', { exact: true })).toBeVisible();
      expect(await request<unknown[]>(page, 'agent.messages')).toEqual([]);
    };
    await clearChat();
    await page
      .getByRole('status')
      .filter({ hasText: '대화를 휴지통으로 옮겼습니다.' })
      .getByRole('button', { name: '되돌리기' })
      .click();
    await expect(log).toContainText(SLOWLY);
    await expect(record).toContainText('광고 1건');
    await clearChat();
    // The records stay, until the user deletes one: it waits in the trash with its screenshot.
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
    const question = page.getByRole('group', {
      name: /이 페이지의 기록과 스크린샷을 휴지통으로 옮길까요\?/,
    });
    await question.locator('[data-destructive]').click();
    await expect(page.getByText('기록을 휴지통으로 옮겼습니다.')).toBeVisible();
    await expect(page.getByText('기록이 없습니다', { exact: true })).toBeVisible();
    expect((await request<{ total: number }>(page, 'web.records')).total).toBe(0);
    expect((await stat(screenshot)).size).toBeGreaterThan(0);
    // Both wait in 관리 → 휴지통, which is the user's alone; deleted for good there, the record takes its screenshot.
    await openAdmin(page, '휴지통');
    await expect(page.locator('.trash-section')).toHaveAttribute('data-user-only', 'trash');
    const bin = page.getByRole('list', { name: '휴지통' });
    await expect(bin.getByRole('listitem')).toHaveCount(2);
    await expect(bin.getByRole('listitem').first()).toContainText('운동화 검색');
    await expect(bin.getByRole('listitem').first()).toContainText('웹 기록 · 광고 1');
    await expect(bin.getByRole('listitem').nth(1)).toContainText(`채팅 · 메시지 4개`);
    await bin.getByRole('button', { name: '운동화 검색 영구 삭제' }).click();
    await page
      .getByRole('group', { name: '영구 삭제할까요? 되돌릴 수 없습니다.' })
      .getByRole('button', { name: '영구 삭제', exact: true })
      .click();
    await expect(bin.getByRole('listitem')).toHaveCount(1);
    await expect(stat(screenshot)).rejects.toThrow();
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await new Promise<void>((resolve) => site.close(() => resolve()));
    await rm(directory, { recursive: true, force: true });
  }
});

test('agent chat: a request offered from the app’s own data fills the box and is not sent', async () => {
  test.setTimeout(240_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-chat-'));
  const OFFERED = '기한이 지난 할 일 1개를 정리해 줘';
  const CHECKED = '할 일을 확인했습니다.';
  // The answer waits until the test lets it go, so that the task can be finished while the turn runs.
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  const llm = await llmServer(async ({ payload }) => {
    await held;
    return payload.tools?.length ? CHECKED : { message: CHECKED, actions: [] };
  });
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);
    const task = await request<{ id: string; updated_at: string }>(page, 'tasks.save', {
      title: '지난 보고서',
      due_at: new Date(Date.now() - 2 * DAY).toISOString(),
    });

    // A task past its due time: the chat offers to sort it out, and says why.
    await openChat(page);
    const offers = page.getByRole('group', { name: '제안' });
    const offer = offers.getByRole('button', { name: OFFERED });
    await expect(offer).toHaveAttribute('title', '기한 지남: 지난 보고서');
    await offer.click();
    // The words wait in the box for the user to read, change or send: nothing was sent.
    await expect(chatBox(page)).toHaveValue(OFFERED);
    await expect(chatBox(page)).toBeFocused();
    await expect(chatBox(page)).toBeEnabled();
    await expect(page.getByText('무엇을 맡길까요?', { exact: true })).toBeVisible();
    expect(await request<unknown[]>(page, 'agent.messages')).toEqual([]);
    expect(llm.requests.some(({ payload }) => JSON.stringify(payload).includes(OFFERED))).toBe(
      false,
    );

    // Sent, the offers leave while the turn runs and are read again once it ended: the task finished meanwhile is
    // offered no more, and the examples stand in.
    await chatBox(page).press('Enter');
    const log = page.getByRole('log', { name: '대화' });
    await expect(log.locator('.agent-message.pending')).toBeVisible();
    await expect(offers).toHaveCount(0);
    await request(page, 'tasks.save', {
      id: task.id,
      status: 'done',
      expected_updated_at: task.updated_at,
    });
    release();
    await expect(log).toContainText(CHECKED, { timeout: 120_000 });
    await expect(offers).toContainText('쿠팡에서 노트북 광고 기록해 줘');
    await expect(offers.getByRole('button', { name: OFFERED })).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    release();
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test('agent chat: the tasks and the document the agent reads are drawn on its cards', async () => {
  test.setTimeout(240_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-chat-'));
  const READ = '할 일과 회의록을 읽었습니다.';
  const MINUTES = '# 회의록\n\n다음 회의는 금요일입니다.';
  let documentId = '';
  // The bundled Hermes calls the app's tools itself, one after the other; a source run without it plans them as JSON.
  const llm = await llmServer(({ payload }) => {
    const read = { tool: 'read_document', args: { document_id: documentId } };
    if (!payload.tools?.length)
      return { message: READ, actions: [{ tool: 'list_tasks', args: { status: 'open' } }, read] };
    const ran = payload.messages.filter((message) => message.role === 'tool').length;
    if (ran === 0) return toolCalls(['list_tasks', { status: 'open' }]);
    if (ran === 1) return toolCalls(['read_document', read.args]);
    return READ;
  });
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);
    await request(page, 'tasks.save', {
      title: '분기 보고서',
      due_at: new Date(Date.now() + 3 * DAY).toISOString(),
    });
    documentId = (
      await request<{ id: string }>(page, 'wiki.save', { title: '회의록', content: MINUTES })
    ).id;

    await openChat(page);
    await chatBox(page).fill('진행 중인 할 일과 회의록을 보여 줘');
    await chatBox(page).press('Enter');
    const log = page.getByRole('log', { name: '대화' });
    await expect(log).toContainText(READ, { timeout: 120_000 });
    // Both only read, so they ran without a 확인 후 실행.
    await expect(log.getByRole('button', { name: '확인 후 실행' })).toHaveCount(0);
    const tasks = log.locator('.agent-card').filter({ hasText: '할 일 목록 (진행 중)' });
    await expect(tasks).toContainText('할 일 1개를 찾았습니다.');
    await expect(tasks.getByRole('list', { name: '할 일' })).toContainText('분기 보고서');
    await expect(tasks.getByRole('list', { name: '할 일' })).toContainText('진행 중');
    const document = log.locator('.agent-card').filter({ hasText: '문서 읽기: 회의록' });
    await expect(document).toContainText('완료');
    await expect(document).toContainText(`전체 ${MINUTES.length}자`);
    await document.getByRole('button', { name: '회의록 열기' }).click();
    await expect(drawer(page)).toContainText('다음 회의는 금요일입니다.');
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();
    await tasks.getByRole('button', { name: '할 일 보기' }).click();
    await expect(page.getByRole('heading', { name: '할 일', level: 1 })).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true });
  }
});
