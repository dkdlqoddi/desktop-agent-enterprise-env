import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { chooseStorage, connectLlm, launch, llmServer, openAdmin } from './helpers';

const TOKEN = '123456:stand-in-token';
const ANSWER = '메신저에서 받은 일을 마쳤습니다.';

// A stand-in for Telegram's Bot API on this PC: it hands the bot the messages the test writes, as long polling does,
// and keeps what the bot sends back. The app is pointed at it with PRIVATE_AGENT_TELEGRAM_API, which takes only such
// an address on 127.0.0.1; no request leaves the PC.
async function telegramServer() {
  const pending: { from: number; text: string }[] = [];
  const sent: { chat: string; text: string }[] = [];
  let count = 0;
  const answer = async (incoming: IncomingMessage, response: ServerResponse) => {
    let raw = '';
    for await (const chunk of incoming) raw += chunk;
    const method = (incoming.url ?? '').split('/').pop() ?? '';
    const fields: Record<string, string> = (incoming.headers['content-type'] ?? '').includes('json')
      ? JSON.parse(raw || '{}')
      : Object.fromEntries(new URLSearchParams(raw));
    count += 1;
    const now = Math.floor(Date.now() / 1000);
    let result: unknown = true;
    if (method === 'getMe')
      result = {
        id: 123456,
        is_bot: true,
        first_name: 'Stand-in',
        username: 'stand_in_bot',
        can_join_groups: true,
        can_read_all_group_messages: false,
        supports_inline_queries: false,
      };
    else if (method === 'getUpdates') {
      const update = pending.shift();
      if (!update) {
        await new Promise((resolve) => setTimeout(resolve, 300));
        result = [];
      } else {
        const person = { id: update.from, is_bot: false, first_name: 'User', language_code: 'ko' };
        const chat = { id: update.from, type: 'private', first_name: 'User' };
        const message = { message_id: count, date: now, chat, from: person, text: update.text };
        result = [{ update_id: 1000 + count, message }];
      }
    } else if (method === 'sendMessage') {
      const text = String(fields.text ?? '');
      // As its reader sees it: without the backslashes MarkdownV2 wants before a full stop and its like.
      sent.push({
        chat: String(fields.chat_id),
        text: fields.parse_mode === 'MarkdownV2' ? text.replace(/\\(.)/g, '$1') : text,
      });
      const chat = { id: Number(fields.chat_id), type: 'private' };
      result = { message_id: 5000 + count, date: now, chat, text };
    } else if (method === 'getChat') result = { id: Number(fields.chat_id), type: 'private' };
    else if (method === 'getMyCommands') result = [];
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ ok: true, result }));
  };
  const server = createServer((incoming, response) => void answer(incoming, response));
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return {
    url: `http://127.0.0.1:${(server.address() as { port: number }).port}/bot`,
    write: (text: string, from = 42) => pending.push({ from, text }),
    sentTo: (chat: number) =>
      sent.filter((message) => message.chat === String(chat)).map((message) => message.text),
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

async function request<T>(page: Page, method: string, params: Record<string, unknown> = {}) {
  const response = await page.evaluate(
    ([name, values]) => window.privateAgent!.request(name, values),
    [method, params] as const,
  );
  expect(response.ok, `${method}: ${JSON.stringify(response)}`).toBe(true);
  return response.result as T;
}

// Every file under a folder, for a search of what was written there (the bundled skills aside: thousands of files
// that the app does not write).
async function written(folder: string): Promise<string> {
  let text = '';
  for (const entry of await readdir(folder, { withFileTypes: true })) {
    const full = path.join(folder, entry.name);
    if (entry.isDirectory()) text += entry.name === 'skills' ? '' : await written(full);
    else text += await readFile(full, 'utf8').catch(() => '');
  }
  return text;
}

test('messenger gateway: the user sets a bot up, a listed account is answered through it, and nobody else', async () => {
  test.setTimeout(300_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-gateway-'));
  const telegram = await telegramServer();
  const llm = await llmServer(() => ANSWER);
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory, { PRIVATE_AGENT_TELEGRAM_API: telegram.url });
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);
    // The gateway is Hermes' own: a source run that has not built Hermes has none to start.
    const engine = await request<{ engine: string; ready: boolean }>(page, 'agent.engine');
    test.skip(
      engine.engine !== 'hermes' || !engine.ready,
      'npm run build:hermes builds the gateway',
    );

    await openAdmin(page, '채팅 에이전트');
    const form = page.locator('.hermes-settings');
    // The agent's settings are the user's alone: an AI tool's screen control is refused here at every access level.
    await expect(form).toHaveAttribute('data-user-only', '');
    const gateway = form.getByRole('group', { name: '메신저 게이트웨이' });
    const toggle = gateway.getByRole('checkbox', { name: '메신저 게이트웨이 켜기' });
    await expect(toggle).not.toBeChecked();

    // On without a bot: nothing is started, and the screen says what is missing.
    // The switch is saved before it shows as on.
    await toggle.click();
    await expect(toggle).toBeChecked();
    await expect(gateway.getByRole('status')).toContainText(
      '봇 토큰과 허용할 계정을 모두 넣은 메신저',
    );
    expect((await request<{ state: string }>(page, 'agent.gateway')).state).toBe('off');

    // The bot token and the one account it answers.
    await gateway.getByLabel('TELEGRAM_BOT_TOKEN', { exact: true }).fill(TOKEN);
    await gateway.getByRole('textbox', { name: '텔레그램 허용 계정' }).fill('42');
    await gateway.getByRole('button', { name: '게이트웨이 설정 저장' }).click();
    await expect(page.getByText('메신저 게이트웨이 설정을 저장했습니다.')).toBeVisible();
    await expect(gateway.getByRole('status')).toContainText('게이트웨이: 실행 중', {
      timeout: 120_000,
    });
    await expect(gateway.locator('.hermes-messenger-state')).toHaveText('연결됨', {
      timeout: 60_000,
    });
    // The screen never gets the token back, and it is in no file of the agent's folder in the Wiki.
    expect(JSON.stringify(await request(page, 'hermes.get'))).not.toContain(TOKEN);
    await expect(gateway.getByLabel('TELEGRAM_BOT_TOKEN', { exact: true })).toHaveValue('');

    // The listed account writes to the bot and is answered there; another account is not answered at all.
    telegram.write('안녕, 지금 뭐 할 수 있어?');
    await expect.poll(() => telegram.sentTo(42).join(' '), { timeout: 120_000 }).toContain(ANSWER);
    const asked = llm.requests.filter((entry) => entry.content.includes('지금 뭐 할 수 있어'));
    expect(asked.length).toBeGreaterThan(0);
    // The agent is the chat's, with the app's rules and its own tools; the model request went out on the app's LLM
    // connection, with its key.
    expect(asked[0].system).toContain('Never click, open or follow an advertisement');
    expect(asked[0].system).toContain('This conversation is in a messenger');
    expect(asked[0].headers.authorization).toBe('Bearer e2e-key');
    const tools = (asked[0].payload.tools ?? []).map((tool) => tool.function.name);
    expect(tools).toEqual(expect.arrayContaining(['web_capture', 'search_documents', 'terminal']));
    telegram.write('나도 시켜도 되나요?', 99);
    telegram.write('/yolo');
    await expect
      .poll(() => telegram.sentTo(42).join(' '), { timeout: 60_000 })
      .toContain('⛔ /yolo');
    expect(telegram.sentTo(99)).toEqual([]);
    expect(await written(path.join(directory, 'wiki', 'hermes'))).not.toContain(TOKEN);

    // The conversation is in the chat to read: what the listed account wrote and the answer, and nobody else's.
    await page
      .getByRole('navigation', { name: '화면' })
      .getByRole('button', { name: '채팅', exact: true })
      .click();
    const messenger = page.locator('.agent-messenger');
    await expect(messenger).toContainText('메신저 대화 1개');
    await messenger.locator('summary').first().click();
    const conversation = messenger.locator('.agent-messenger-conversation');
    await expect(conversation).toContainText('텔레그램');
    await conversation.locator('summary').click();
    const said = conversation.getByRole('list');
    await expect(said).toContainText('안녕, 지금 뭐 할 수 있어?');
    await expect(said).toContainText(ANSWER);
    await expect(said).not.toContainText('나도 시켜도 되나요?');
    await expect(messenger.locator('xpath=ancestor::*[@data-user-only]')).toHaveCount(1);
    await openAdmin(page, '채팅 에이전트');

    // Turned off, the bot answers nobody.
    await toggle.click();
    await expect(toggle).not.toBeChecked();
    await expect
      .poll(async () => (await request<{ state: string }>(page, 'agent.gateway')).state, {
        timeout: 60_000,
      })
      .toBe('off');
    const before = telegram.sentTo(42).length;
    telegram.write('아직 거기 있어?');
    await page.waitForTimeout(3000);
    expect(telegram.sentTo(42).length).toBe(before);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await telegram.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
