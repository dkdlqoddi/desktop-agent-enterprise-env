import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { createServer, type AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  chooseStorage,
  connectLlm,
  drawer,
  launch,
  llmServer,
  openAdmin,
  results,
} from './helpers';

const freePort = () =>
  new Promise<number>((resolve) => {
    const server = createServer();
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as AddressInfo;
      server.close(() => resolve(port));
    });
  });

type ToolResult = { content: { type: string; text?: string; data?: string }[]; isError: boolean };

// What a tool's answer holds between its <<<untrusted ID>>> markers: the app's data.
const data = (text: string) =>
  /<<<untrusted ([0-9a-f]{12})>>>\n([\s\S]*)\n<<<end untrusted \1>>>/.exec(text)![2];

// An AI tool's side of the connection: REST calls and MCP messages with the token from the admin page.
function client(base: string, token: () => string) {
  let id = 0;
  const rest = async (method: string, route: string, body?: unknown) => {
    const response = await fetch(`${base}/api/v1${route}`, {
      method,
      headers: {
        Authorization: `Bearer ${token()}`,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: response.status, json: await response.json() };
  };
  const mcp = async (method: string, params: Record<string, unknown> = {}) => {
    const response = await fetch(`${base}/mcp`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token()}`,
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
      },
      body: JSON.stringify({ jsonrpc: '2.0', id: ++id, method, params }),
    });
    return response.json();
  };
  const tool = async (name: string, args: Record<string, unknown> = {}) =>
    (await mcp('tools/call', { name, arguments: args })).result as ToolResult;
  return { rest, mcp, tool };
}

// What a copy button put on the clipboard; the main process writes it after the screen's request arrives.
async function copied(app: ElectronApplication, page: Page, button: string) {
  await app.evaluate(({ clipboard }) => clipboard.writeText(''));
  await page.getByRole('button', { name: button }).first().click();
  let text = '';
  await expect
    .poll(async () => (text = await app.evaluate(({ clipboard }) => clipboard.readText())))
    .not.toBe('');
  return text;
}

test('AI tools: local API and MCP server with the admin page, manual, data, app control and access levels', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-api-'));
  const llm = await llmServer(({ system }) =>
    system.includes('analyze a user')
      ? {
          intent: '캐시 도입 결정 찾기',
          terms: ['캐시'],
          keywords: [],
          explanation: '캐시를 찾습니다.',
        }
      : system.includes('search agent')
        ? {
            thinking: '회의록이 맞습니다.',
            results: [{ ref: 'D1', reason: '캐시 결정', sections: [] }],
            next: null,
          }
        : { answer: '캐시 서버를 도입하기로 했습니다. [1]', citations: [1] },
  );
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    let page = await app.firstWindow();
    await chooseStorage(page, path.join(directory, 'wiki'));
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));

    // Nothing listens until the user turns the server on, on a port of their choice.
    await openAdmin(page, 'AI 도구 연동');
    const toggle = page.getByRole('switch', { name: '로컬 API·MCP 서버 켜기' });
    await expect(toggle).not.toBeChecked();
    await expect(page.getByText('꺼져 있습니다.', { exact: false })).toBeVisible();
    const port = await freePort();
    const base = `http://127.0.0.1:${port}`;
    await page.getByLabel('포트').fill(String(port));
    await page.getByRole('button', { name: '적용' }).click();
    await expect(page.getByText(`MCP 주소 ${base}/mcp`)).toBeVisible();
    await expect(fetch(`${base}/api/v1/health`)).rejects.toThrow();
    await toggle.check();
    await expect(page.getByRole('status').filter({ hasText: `실행 중 · ${base}` })).toBeVisible();
    // AI tools may only read until the user allows more.
    await expect(page.getByLabel('허용 범위')).toHaveValue('read');
    await page.getByLabel('허용 범위').selectOption('write');
    await expect(page.getByLabel('허용 범위')).toHaveValue('write');

    // The token reaches the clipboard, never the screen; the snippets show a placeholder.
    let token = await copied(app, page, '토큰 복사');
    expect(token).toMatch(/^pa_[A-Za-z0-9_-]{43}$/);
    await expect(page.locator('body')).not.toContainText(token);
    await expect(page.locator('.api-snippet').first()).toContainText('<PRIVATE_AGENT_TOKEN>');
    expect(await copied(app, page, '토큰 넣어 복사')).toBe(
      `claude mcp add --transport http --scope user private-agent ${base}/mcp --header "Authorization: Bearer ${token}"`,
    );
    for (const [name, text] of [
      ['Codex', `[mcp_servers.private-agent]\nurl = "${base}/mcp"`],
      ['OpenCode', '"type": "remote"'],
      ['Antigravity', `"serverUrl": "${base}/mcp"`],
    ]) {
      await page.getByRole('radio', { name }).click();
      expect(await copied(app, page, '토큰 넣어 복사')).toContain(text);
    }
    await page.getByRole('radio', { name: '기타 (stdio)' }).click();
    const stdio = JSON.parse(await copied(app, page, '토큰 넣어 복사'));
    const bridge = stdio.mcpServers['private-agent'].args[0];
    expect(await readFile(bridge, 'utf8')).toContain('Private Agent MCP over stdio');

    const ai = client(base, () => token);
    expect((await fetch(`${base}/api/v1/health`)).status).toBe(200);
    expect((await fetch(`${base}/api/v1/stats`)).status).toBe(401);

    // Data through REST shows on the screens without reloading them.
    await page
      .getByRole('navigation', { name: '화면' })
      .getByRole('button', { name: '지식 보관함', exact: true })
      .click();
    const created = await ai.rest('POST', '/documents', {
      title: 'API로 만든 회의록',
      content: '# 회의\n\n캐시 서버를 도입하기로 했습니다.',
    });
    expect(created.status).toBe(200);
    const id = created.json.result.id as string;
    await expect(page.locator('section[data-widget="documents"]')).toContainText(
      'API로 만든 회의록',
    );
    const listed = await ai.rest('GET', '/documents?analysis=not_analyzed&limit=5');
    expect(listed.json.result.documents.map((doc: { id: string }) => doc.id)).toEqual([id]);
    const searched = await ai.rest('GET', `/search?query=${encodeURIComponent('캐시')}`);
    expect(searched.json.result.documents[0].id).toBe(id);
    const rpc = await ai.rest('POST', '/rpc', { method: 'stats' });
    expect(rpc.json.result.documents).toBe(1);

    // MCP: search, read, then show the user what was found.
    const init = await ai.mcp('initialize', {
      protocolVersion: '2025-06-18',
      capabilities: {},
      clientInfo: { name: 'e2e', version: '1' },
    });
    expect(init.result.serverInfo.name).toBe('private-agent');
    const found = await ai.tool('search_documents', { query: '캐시' });
    expect(found.content[0].text).toContain(`API로 만든 회의록 — id: ${id}`);
    const read = await ai.tool('get_document', { id });
    expect(read.content[0].text).toContain('캐시 서버를 도입하기로 했습니다.');
    const resources = await ai.mcp('resources/list');
    expect(resources.result.resources.map((item: { uri: string }) => item.uri)).toContain(
      `private-agent://documents/${id}`,
    );

    await ai.tool('app_navigate', { view: 'tasks' });
    await expect(page.getByRole('heading', { name: '할 일', exact: true })).toBeVisible();
    expect((await ai.tool('app_open_document', { id, terms: ['캐시'] })).isError).toBe(false);
    await expect(drawer(page).getByRole('heading', { name: 'API로 만든 회의록' })).toBeVisible();
    const missing = await ai.tool('app_open_document', { id: 'no-such-document' });
    expect(missing.isError).toBe(true);
    const screen = await ai.tool('app_read_screen');
    expect(screen.content[0].text).toContain('screen: tasks + document panel');
    expect(screen.content[0].text).toContain('button: 닫기');
    const shot = await ai.tool('app_screenshot', { max_width: 800 });
    expect(shot.content[0].type).toBe('image');
    expect(Buffer.from(shot.content[0].data!, 'base64').subarray(1, 4).toString()).toBe('PNG');
    expect((await ai.tool('app_click', { name: '닫기' })).isError).toBe(false);
    await expect(drawer(page)).toHaveCount(0);
    const filled = await ai.tool('app_fill', {
      label: '검색 또는 질문',
      value: '캐시',
      submit: true,
    });
    expect(filled.isError).toBe(false);
    await expect(results(page)).toContainText('API로 만든 회의록');
    const window = JSON.parse(
      (await ai.tool('app_window', { action: 'set_zoom', zoom: 1.1 })).content[0].text!,
    );
    expect(window.zoom).toBeCloseTo(1.1);
    await ai.tool('app_window', { action: 'set_zoom', zoom: 1 });
    const status = JSON.parse(data((await ai.tool('get_app_status')).content[0].text!));
    expect(status.screen.view).toBe('home');
    expect(status.stats.documents).toBe(1);

    // What the user types is never thrown away: no leaving the screen, searching or reloading meanwhile.
    await ai.tool('app_navigate', { view: 'library', tab: 'import' });
    const box = page.getByLabel('가져올 링크 또는 경로');
    await box.fill('https://jira.example/browse/ABC-1');
    for (const refused of [
      await ai.tool('app_navigate', { view: 'home' }),
      await ai.tool('app_search', { query: '캐시' }),
      await ai.tool('app_click', { name: '홈', exact: true }),
    ]) {
      expect(refused.isError).toBe(true);
      expect(refused.content[0].text).toContain('저장하지 않아');
    }
    const reload = await ai.rest('POST', '/app/reload');
    expect(reload.status).toBe(400);
    expect(reload.json.error.message).toContain('저장하지 않아');
    // Nor by closing the window once: it stays, saying why.
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
    await expect(page.getByText('창을 닫지 않았습니다', { exact: false })).toBeVisible();
    await expect(box).toHaveValue('https://jira.example/browse/ABC-1');
    // The messages stack, and the success of the last copy above may still be showing: the notice is closed by its
    // own button.
    const kept = page.getByRole('alert').filter({ hasText: '창을 닫지 않았습니다' });
    await kept.getByRole('button', { name: '알림 닫기' }).click();
    await expect(kept).toHaveCount(0);
    await box.fill('');
    expect((await ai.tool('app_navigate', { view: 'home' })).isError).toBe(false);

    // The admin page and its inputs stay out of reach below the "full" level.
    const admin = await ai.tool('app_navigate', { view: 'admin', section: 'llm' });
    expect(admin.content[0].text).toContain('"view": "admin"');
    const blocked = await ai.tool('app_fill', { label: 'API Key', value: 'token-from-an-ai-tool' });
    expect(blocked.isError).toBe(true);
    expect(blocked.content[0].text).toContain('전체');
    expect((await ai.rest('PUT', '/settings', { model_name: 'x' })).status).toBe(403);
    // A token the user types stays out of the screen reading, also while its eye button shows it,
    // and so do the values of the custom headers.
    await page.getByPlaceholder('필요 없는 서버면 비워 둡니다').fill('secret-key-typed-by-user');
    // Sent to the section already shown, the page keeps what is typed in it.
    await page.getByRole('button', { name: 'LLM 연결 필요' }).click();
    await expect(page.getByPlaceholder('필요 없는 서버면 비워 둡니다')).toHaveValue(
      'secret-key-typed-by-user',
    );
    await page.getByRole('button', { name: 'API Key 표시' }).click();
    let reading = (await ai.tool('app_read_screen')).content[0].text!;
    expect(reading).toContain('API Key');
    expect(reading).not.toContain('secret-key-typed-by-user');
    // A capture draws it as dots, and as it was afterwards.
    const dots = () =>
      page
        .getByPlaceholder('필요 없는 서버면 비워 둡니다')
        .evaluate((input) => getComputedStyle(input).getPropertyValue('-webkit-text-security'));
    expect(await dots()).toBe('none');
    const masked = await ai.tool('app_screenshot', { max_width: 800 });
    expect(masked.isError).toBe(false);
    expect(masked.content[0].type).toBe('image');
    await expect.poll(dots).toBe('none');
    await page.getByPlaceholder('필요 없는 서버면 비워 둡니다').fill('');
    await page.getByRole('button', { name: '헤더 추가' }).click();
    await page.getByLabel('헤더 1 이름').fill('X-Secret');
    await page.getByLabel('헤더 1 값').fill('secret-header-typed-by-user');
    reading = (await ai.tool('app_read_screen')).content[0].text!;
    expect(reading).toContain('헤더 1 값');
    expect(reading).not.toContain('secret-header-typed-by-user');
    await page.getByRole('button', { name: '헤더 1 빼기' }).click();

    // Files on this PC are read with the user's own rights: below "full" they are refused through the API and
    // through the app's own import box alike.
    const imported = await ai.tool('import_source', { source: path.join(directory, 'plan.docx') });
    expect(imported.isError).toBe(true);
    expect(imported.content[0].text).toContain('파일 가져오기');
    await ai.tool('app_navigate', { view: 'library', tab: 'import' });
    const typed = await ai.tool('app_fill', { label: '가져올 링크 또는 경로', value: directory });
    expect(typed.isError).toBe(true);
    expect(typed.content[0].text).toContain('전체');
    await expect(page.getByLabel('가져올 링크 또는 경로')).toHaveValue('');

    // A slow tool with the LLM: answered in time here, otherwise handed over as a job.
    await connectLlm(page, llm.endpoint);
    page = await app.firstWindow();
    // AI tools learn which headers the LLM requests carry, never their values.
    const llmSettings = (await ai.rest('GET', '/settings')).json.result;
    expect(llmSettings.headers).toEqual([{ name: 'X-Team' }]);
    expect(JSON.stringify(llmSettings)).not.toContain('e2e-key');
    const answer = await ai.tool('ask_wiki', { question: '캐시는 어떻게 하기로 했나요?' });
    expect(answer.content[0].text).toContain('캐시 서버를 도입하기로 했습니다. [1]');
    expect(answer.content[0].text).toContain(`[1] API로 만든 회의록 — id: ${id}`);

    // Read-only access: changes are refused and the change tools disappear.
    await openAdmin(page, 'AI 도구 연동');
    await page.getByLabel('허용 범위').selectOption('read');
    await expect(page.getByLabel('허용 범위')).toHaveValue('read');
    const refused = await ai.rest('POST', '/documents', { title: 'x', content: 'y' });
    expect(refused.status).toBe(403);
    expect(refused.json.error.code).toBe('forbidden');
    const tools = (await ai.mcp('tools/list')).result.tools.map(
      (tool: { name: string }) => tool.name,
    );
    expect(tools).toContain('search_documents');
    expect(tools).not.toContain('save_document');
    expect(tools).not.toContain('app_click');
    // The latest requests, refusals included, without their contents.
    const recent = page.getByRole('list', { name: '최근 요청' });
    await expect(recent.getByRole('listitem').first()).toContainText('tools/list');
    await expect(recent.getByRole('listitem').nth(1)).toContainText('실패wiki.save');
    await expect(recent).not.toContainText('API로 만든 회의록');

    // The manual: the admin page, the endpoint in JSON and Markdown, the web page and OpenAPI.
    await page.getByRole('tab', { name: 'API 매뉴얼' }).click();
    await expect(page.getByRole('tabpanel')).toContainText(`${base}/api/v1`);
    await page.getByLabel('API 찾기').fill('screenshot');
    await expect(page.getByText('/api/v1/app/screenshot', { exact: true })).toBeVisible();
    await expect(page.locator('.api-route')).toHaveCount(2);
    await expect(page.getByText('app_screenshot', { exact: true })).toBeVisible();
    await page.getByLabel('API 찾기').fill('');
    await page.locator('.api-group summary').first().click();
    await page.screenshot({ path: 'test-results/api-manual.png', fullPage: true });
    // Without the token the manual leaves out what is the user's own: the level they chose, the relay in their profile.
    const relay = (doc: {
      result: { mcp: { transports: { name: string; endpoint: string }[] } };
    }) => doc.result.mcp.transports.find((item) => item.name === 'stdio')!.endpoint;
    const manual = await (await fetch(`${base}/api/v1/manual`)).json();
    expect(manual.result.access).toBe(null);
    expect(relay(manual)).not.toBe(bridge);
    const personal = await (
      await fetch(`${base}/api/v1/manual`, { headers: { Authorization: `Bearer ${token}` } })
    ).json();
    expect(personal.result.access).toBe('read');
    expect(relay(personal)).toBe(bridge);
    const markdown = await (await fetch(`${base}/api/v1/manual?format=markdown`)).text();
    expect(markdown).toContain('# Private Agent 로컬 API 매뉴얼');
    expect(markdown).not.toContain(token);
    expect(await (await fetch(`${base}/docs`)).text()).toContain('Private Agent API 매뉴얼');
    expect((await (await fetch(`${base}/openapi.json`)).json()).openapi).toBe('3.1.0');

    // A new token locks out the old one.
    await page.getByRole('tab', { name: 'AI 도구 연동' }).click();
    await page.getByRole('button', { name: '새로 발급' }).click();
    await page
      .getByRole('group', { name: /새 토큰을 발급할까요/ })
      .getByRole('button', { name: '새로 발급' })
      .click();
    await expect(page.getByRole('group', { name: /새 토큰을 발급할까요/ })).toHaveCount(0);
    expect((await ai.rest('GET', '/stats')).status).toBe(401);
    token = await copied(app, page, '토큰 복사');
    expect((await ai.rest('GET', '/stats')).status).toBe(200);
    expect(pageErrors).toEqual([]);
    await page.screenshot({ path: 'test-results/api-settings.png', fullPage: true });

    // The server comes back after a restart with the same port and access level.
    await app.close();
    app = await launch(directory);
    page = await app.firstWindow();
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible();
    await expect.poll(async () => (await fetch(`${base}/api/v1/health`)).status).toBe(200);
    await openAdmin(page, 'AI 도구 연동');
    await expect(page.getByLabel('허용 범위')).toHaveValue('read');
    await page.getByRole('switch', { name: '로컬 API·MCP 서버 켜기' }).uncheck();
    await expect(page.getByText('꺼져 있습니다.', { exact: false })).toBeVisible();
    await expect(fetch(`${base}/api/v1/health`)).rejects.toThrow();
  } finally {
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
