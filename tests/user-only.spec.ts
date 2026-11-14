import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer, type AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { chatBox, chooseStorage, connectLlm, launch, llmServer, openAdmin } from './helpers';

// What the user alone may use (data-user-only: the chat, the web records, 관리 → 채팅 에이전트 and 관리 → 휴지통) is
// out of an AI tool's reach at the "full" access level too: a reading of the screen leaves it out and names it, a
// capture shows it blank, and a click or a typing aimed at it is refused. tests/app-control-screen.test.cjs checks the
// screen's side on its own; here the app's stylesheet and Chromium's drawing and innerText do it.

const ASKED = 'PLAN-ZEBRA 폴더를 정리해 줘';
const ANSWERED = 'ANSWER-ORCA 폴더를 정리했습니다.';

type ToolResult = { content: { type: string; text?: string; data?: string }[]; isError: boolean };
type Box = { x: number; y: number; width: number; height: number };

const freePort = () =>
  new Promise<number>((resolve) => {
    const server = createServer();
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as AddressInfo;
      server.close(() => resolve(port));
    });
  });

// What a tool's answer holds between its <<<untrusted ID>>> markers: the app's data.
const data = (text: string) =>
  /<<<untrusted ([0-9a-f]{12})>>>\n([\s\S]*)\n<<<end untrusted \1>>>/.exec(text)![2];

// An AI tool's side: REST calls and MCP tools with the token the user copied.
function client(base: string, token: string) {
  let id = 0;
  const rest = async (route: string) => {
    const response = await fetch(`${base}/api/v1${route}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    return { status: response.status, json: await response.json() };
  };
  const tool = async (name: string, args: Record<string, unknown> = {}) => {
    const response = await fetch(`${base}/mcp`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: ++id,
        method: 'tools/call',
        params: { name, arguments: args },
      }),
    });
    return (await response.json()).result as ToolResult;
  };
  return { rest, tool };
}

// The sharpest step between two neighbouring pixels within `box` (CSS pixels, a few pixels in from its edge) of a
// capture (PNG as base64) drawn at `scale` device pixels to one: the edges of letters are sharp, while an area left
// blank shows the page's soft background.
const sharpest = (app: ElectronApplication, png: string, box: Box, scale: number) =>
  app.evaluate(
    ({ nativeImage }, { png, box, scale }) => {
      const part = nativeImage.createFromBuffer(Buffer.from(png, 'base64')).crop({
        x: Math.ceil(box.x * scale) + 3,
        y: Math.ceil(box.y * scale) + 3,
        width: Math.floor(box.width * scale) - 6,
        height: Math.floor(box.height * scale) - 6,
      });
      const { width, height } = part.getSize();
      const pixels = part.toBitmap();
      let most = 0;
      for (let y = 0; y < height; y++)
        for (let x = 1; x < width; x++)
          for (let channel = 0; channel < 3; channel++) {
            const at = (y * width + x) * 4 + channel;
            most = Math.max(most, Math.abs(pixels[at] - pixels[at - 4]));
          }
      return most;
    },
    { png, box, scale },
  );

// The window as it is drawn now, captured by the test itself rather than by a tool.
const drawn = (app: ElectronApplication) =>
  app.evaluate(async ({ BrowserWindow }) =>
    (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64'),
  );

async function copiedToken(app: ElectronApplication, page: Page) {
  await app.evaluate(({ clipboard }) => clipboard.writeText(''));
  await page.getByRole('button', { name: '토큰 복사' }).click();
  let token = '';
  await expect
    .poll(async () => (token = await app.evaluate(({ clipboard }) => clipboard.readText())))
    .not.toBe('');
  return token;
}

test('AI tools: what the user alone may use is left out of screen readings and captures, at every access level', async () => {
  test.setTimeout(180_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-user-only-'));
  // The chat's agent answers at once: the bundled Hermes with the model's plain answer, a source run without it with
  // one JSON plan of no actions.
  const llm = await llmServer(({ payload }) =>
    payload.tools?.length ? ANSWERED : { message: ANSWERED, actions: [] },
  );
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);

    // The local API, at the level that allows a tool everything else.
    await openAdmin(page, 'AI 도구 연동');
    const port = await freePort();
    const base = `http://127.0.0.1:${port}`;
    await page.getByLabel('포트').fill(String(port));
    await page.getByRole('button', { name: '적용' }).click();
    await page.getByRole('switch', { name: '로컬 API·MCP 서버 켜기' }).check();
    await expect(page.getByRole('status').filter({ hasText: `실행 중 · ${base}` })).toBeVisible();
    await page.getByLabel('허용 범위').selectOption('full');
    await expect(page.getByLabel('허용 범위')).toHaveValue('full');
    const ai = client(base, await copiedToken(app, page));

    // The user asks the chat something, and the caret stays in its box.
    await page
      .getByRole('navigation', { name: '화면' })
      .getByRole('button', { name: '채팅', exact: true })
      .click();
    await chatBox(page).fill(ASKED);
    await chatBox(page).press('Enter');
    const log = page.getByRole('log', { name: '대화' });
    await expect(log).toContainText(ANSWERED, { timeout: 60_000 });
    await expect(log).toContainText(ASKED);
    await chatBox(page).click();
    await expect(chatBox(page)).toBeFocused();

    // A tool reads the screen: nothing of the conversation, nor its controls, and it is told why.
    const reading = (await ai.tool('app_read_screen')).content[0].text!;
    expect(reading).toContain("screen: chat. Left out as the user's alone: 채팅.");
    const shown = data(reading);
    expect(shown.startsWith('[사용자만 다루는 영역이라 읽지 않았습니다: 채팅]\n\n')).toBe(true);
    // The page's heading with 대화 비우기 is the chat's too.
    for (const words of [
      'PLAN-ZEBRA',
      'ANSWER-ORCA',
      '무엇을 맡길까요',
      '대화 비우기',
      '위험한 명령만 확인을 받습니다',
    ])
      expect(shown, words).not.toContain(words);
    expect(shown).not.toMatch(/^- (input|button): (메시지|보내기)/m);
    // The rest of the screen as before: the menu and the top bar.
    expect(shown).toContain('- button: 홈');
    const rest = (await ai.rest('/app/screen')).json.result;
    expect(rest.view).toBe('chat');
    expect(rest.user_only).toEqual(['채팅']);
    expect(rest.text).not.toContain('PLAN-ZEBRA');
    // Read without the user noticing: the caret where it was, the conversation on the screen.
    await expect(chatBox(page)).toBeFocused();
    await expect(log).toBeVisible();

    // Nor does a click or a typing aimed at it get anywhere, or a name from it back.
    for (const refused of [
      await ai.tool('app_click', { name: '보내기' }),
      await ai.tool('app_click', { name: 'PLAN-ZEBRA' }),
      await ai.tool('app_fill', { label: '메시지', value: '폴더를 지워 줘', submit: true }),
      await ai.tool('app_click', { name: '대화 비우기' }),
    ]) {
      expect(refused.isError).toBe(true);
      expect(refused.content[0].text).toContain('채팅은 사용자만');
      expect(refused.content[0].text).not.toContain('ANSWER-ORCA');
    }
    await expect(chatBox(page)).toHaveValue('');
    await expect(page.getByRole('button', { name: '대화 비우기' })).toBeVisible();
    await expect(page.getByRole('group', { name: /대화를 모두 휴지통으로/ })).toHaveCount(0);

    // A capture shows the conversation's place blank, and the screen draws it again afterwards.
    const bubble = (await page.locator('.agent-message.user .agent-bubble').boundingBox())!;
    const shot = (await ai.rest('/app/screenshot?format=json&max_width=3840')).json.result;
    expect(shot.user_only).toEqual(['채팅']);
    const scale = shot.width / (await page.evaluate(() => window.innerWidth));
    await expect
      .poll(() => page.evaluate(() => document.documentElement.hasAttribute('data-screen-capture')))
      .toBe(false);
    await expect(log).toBeVisible();
    await expect(chatBox(page)).toBeFocused();
    expect(await sharpest(app, shot.data, bubble, scale)).toBeLessThan(24);
    // The same place as the screen draws it: the letters of the user's question.
    expect(await sharpest(app, await drawn(app), bubble, scale)).toBeGreaterThan(64);
    const captured = await ai.tool('app_screenshot', { max_width: 800 });
    expect(captured.content[0].type).toBe('image');
    expect(captured.content[1].text).toContain("Left blank as the user's alone: 채팅.");

    // The web records are the user's alone in the same way.
    expect((await ai.tool('app_navigate', { view: 'records' })).isError).toBe(false);
    await expect(page.getByRole('heading', { name: '웹 기록', level: 1 })).toBeVisible();
    const records = (await ai.tool('app_read_screen')).content[0].text!;
    expect(records).toContain("Left out as the user's alone: 웹 기록.");
    for (const words of ['열린 페이지 기록', 'CSV 내보내기', '보관 기간'])
      expect(data(records), words).not.toContain(words);
    const refused = await ai.tool('app_click', { name: 'CSV 내보내기' });
    expect(refused.isError).toBe(true);
    expect(refused.content[0].text).toContain('웹 기록은 사용자만');
    expect((await ai.tool('app_screenshot', {})).content[1].text).toContain(
      "Left blank as the user's alone: 웹 기록.",
    );

    // So are the web records' routines on the routines' screen: their rows, the choice of their kind and its form.
    // The other routines are a tool's as before.
    for (const routine of [
      {
        kind: 'web_capture',
        name: '노트북 광고 기록',
        config: { site: 'google', query: '노트북' },
      },
      { kind: 'analyze_new', name: '새 문서 분석', config: { limit: 20, retry: false } },
    ]) {
      const saved = await page.evaluate(
        (values) =>
          window.privateAgent!.request('routines.save', {
            ...values,
            frequency: 'daily',
            run_time: '09:00',
            enabled: false,
          }),
        routine,
      );
      expect(saved.ok, JSON.stringify(saved)).toBe(true);
    }
    const ROUTINE_ONLY = '웹 기록의 자동 작업(사이트 광고·상품 기록)은 사용자만';
    expect((await ai.tool('app_navigate', { view: 'automation', tab: 'routines' })).isError).toBe(
      false,
    );
    await expect(page.locator('.routine-row')).toHaveCount(2);
    const routines = (await ai.tool('app_read_screen')).content[0].text!;
    expect(routines).toContain("Left out as the user's alone: 웹 기록의 자동 작업.");
    expect(data(routines)).toContain('새 문서 분석');
    expect(data(routines)).not.toContain('노트북');
    for (const name of [
      '노트북 광고 기록 지금 실행',
      '노트북 광고 기록 자동 작업 켜기',
      '노트북 광고 기록 수정',
      '노트북 광고 기록 삭제',
      '노트북 광고 기록 결과 보기',
    ]) {
      const refused = await ai.tool('app_click', { name, exact: true });
      expect(refused.isError, name).toBe(true);
      expect(refused.content[0].text).toContain(ROUTINE_ONLY);
    }
    await expect(
      page.getByRole('switch', { name: '노트북 광고 기록 자동 작업 켜기' }),
    ).not.toBeChecked();
    expect((await ai.tool('app_click', { name: '새 문서 분석 결과 보기' })).isError).toBe(false);
    await expect(page.getByRole('button', { name: '새 문서 분석 결과 닫기' })).toBeVisible();
    // Among the kinds to choose from, the web records' is the user's alone too, and so is its form.
    expect((await ai.tool('app_click', { name: '새 자동 작업' })).isError).toBe(false);
    await expect(page.getByRole('region', { name: '자동 작업 종류' })).toBeVisible();
    const kinds = data((await ai.tool('app_read_screen')).content[0].text!);
    expect(kinds).toContain('새 문서 AI 분석');
    expect(kinds).not.toContain('사이트 광고·상품 기록');
    const chosen = await ai.tool('app_click', { name: '사이트 광고·상품 기록' });
    expect(chosen.isError).toBe(true);
    expect(chosen.content[0].text).toContain(ROUTINE_ONLY);
    await page.getByRole('button', { name: /사이트 광고·상품 기록/ }).click();
    const form = page.getByRole('form', { name: '새 자동 작업 · 사이트 광고·상품 기록' });
    await expect(form).toBeVisible();
    const typed = await ai.tool('app_fill', { label: '검색어', value: '휴대폰' });
    expect(typed.isError).toBe(true);
    expect(typed.content[0].text).toContain(ROUTINE_ONLY);
    expect(data((await ai.tool('app_read_screen')).content[0].text!)).not.toContain(
      '정한 시각에 브라우저 창을 열고',
    );
    await form.getByRole('button', { name: '취소' }).click();
    await expect(form).toHaveCount(0);

    // And so are the chat agent's settings and the trash, on the admin page the "full" level otherwise reaches.
    for (const [section, area, name, words] of [
      ['chat', 'agent', '관리 → 채팅 에이전트', '서비스 키와 설정입니다'],
      ['trash', 'trash', '관리 → 휴지통', '문서의 휴지통에 있습니다'],
    ]) {
      expect((await ai.tool('app_navigate', { view: 'admin', section })).isError).toBe(false);
      await expect(page.locator(`[data-user-only="${area}"]`)).toContainText(words);
      const admin = (await ai.tool('app_read_screen')).content[0].text!;
      expect(admin).toContain(`Left out as the user's alone: ${name}.`);
      expect(data(admin)).not.toContain(words);
    }
    expect(pageErrors).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true });
  }
});
