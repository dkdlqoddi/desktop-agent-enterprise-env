import {
  _electron as electron,
  expect,
  type ElectronApplication,
  type Page,
} from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { createServer, type IncomingHttpHeaders } from 'node:http';
import path from 'node:path';

// PRIVATE_AGENT_E2E_SOURCE_BACKEND=1 runs the backend from source, backend/.venv's Python with backend/main.py, instead
// of the PyInstaller build in dist/backend: the tests then need no npm run build:python. The packaged exe
// (PRIVATE_AGENT_E2E_EXECUTABLE) always runs the backend built into it.
const sourceBackend =
  process.env.PRIVATE_AGENT_E2E_SOURCE_BACKEND === '1' && !process.env.PRIVATE_AGENT_E2E_EXECUTABLE;
const venvPython = path.resolve(
  'backend',
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);

// The backend the app under test runs, for a test that starts one of its own.
export const backendCommand = () =>
  sourceBackend
    ? { executable: venvPython, args: [path.resolve('backend', 'main.py')] }
    : {
        executable: path.resolve(
          'dist/backend',
          'private-agent-backend',
          `private-agent-backend${process.platform === 'win32' ? '.exe' : ''}`,
        ),
        args: [],
      };

// The packaged screens with the built-in backend (or the backend from source, as above), in a data folder of the
// test's own. The LLM settings start empty unless a test passes them. `args` go to the app, such as a demo's.
export const launch = (directory: string, env: Record<string, string> = {}, args: string[] = []) =>
  electron.launch({
    executablePath: process.env.PRIVATE_AGENT_E2E_EXECUTABLE,
    args: [
      ...(process.env.PRIVATE_AGENT_E2E_EXECUTABLE ? ['--no-sandbox'] : ['--no-sandbox', '.']),
      ...args,
    ],
    env: {
      ...process.env,
      // The test servers are on this PC; a proxy, set in the environment or in Windows,
      // must not stand between them and the app.
      NO_PROXY: '127.0.0.1,localhost',
      no_proxy: '127.0.0.1,localhost',
      PRIVATE_AGENT_USER_DATA: directory,
      PRIVATE_AGENT_USE_FROZEN: sourceBackend ? '' : '1',
      PRIVATE_AGENT_PYTHON: sourceBackend ? venvPython : '',
      PRIVATE_AGENT_DEV_URL: '',
      // The built-in backend reads only the PRIVATE_AGENT_ names of the LLM settings; one run from source reads the
      // plain names too (.env), but not where a PRIVATE_AGENT_ name is set, even empty.
      PRIVATE_AGENT_PROVIDER: '',
      PRIVATE_AGENT_API_FORMAT: '',
      PRIVATE_AGENT_BASE_URL: '',
      PRIVATE_AGENT_API_KEY: '',
      PRIVATE_AGENT_MODEL_NAME: '',
      PRIVATE_AGENT_LLM_HEADERS: '',
      PRIVATE_AGENT_TEMPERATURE: '',
      PRIVATE_AGENT_LLM_TIMEOUT: '',
      PRIVATE_AGENT_CA_BUNDLE: '',
      ...env,
    },
  });

export async function chooseStorage(page: Page, directory: string) {
  await page.getByLabel('Wiki 저장 폴더', { exact: true }).fill(directory);
  await page.getByRole('button', { name: '이 위치에 저장하고 시작' }).click();
  // This starts the built-in backend, which Electron gives 20 seconds; a cold start under
  // antivirus scanning takes longer than the 5 seconds an assertion waits by default.
  await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible({ timeout: 30_000 });
}

// The app's Python backend: the child of the Electron main process that runs the built-in backend, or Python with
// backend/main.py from source. On Windows Playwright starts Electron through a shell, so `app.process()` is cmd.exe,
// the main process's parent; the main process gives its own id.
export async function backendPid(app: ElectronApplication) {
  const parent = await app.evaluate(() => process.pid);
  const output =
    process.platform === 'win32'
      ? execFileSync(
          'powershell.exe',
          [
            '-NoProfile',
            '-Command',
            `(Get-CimInstance Win32_Process -Filter "ParentProcessId=${parent}" | Where-Object { $_.Name -like 'private-agent-backend*' -or $_.CommandLine -like '*backend*main.py*' }).ProcessId`,
          ],
          { encoding: 'utf8' },
        )
      : execFileSync(
          'pgrep',
          ['-P', String(parent), '-f', 'private-agent-backend|backend/main\\.py'],
          { encoding: 'utf8' },
        );
  const pids = output.split(/\s+/).filter(Boolean).map(Number);
  if (pids.length !== 1) throw new Error(`백엔드 프로세스를 찾지 못했습니다: ${output}`);
  return pids[0];
}

export const widget = (page: Page, id: string) => page.locator(`section[data-widget="${id}"]`);
export const searchBox = (page: Page) => page.getByLabel('검색 또는 질문');
export const results = (page: Page) => page.getByRole('region', { name: '검색 결과' });
export const drawer = (page: Page) => page.getByRole('dialog');

export async function openAdmin(page: Page, tab: string) {
  await page
    .getByRole('navigation', { name: '화면' })
    .getByRole('button', { name: '관리' })
    .click();
  await page.getByRole('tab', { name: tab }).click();
}

export async function goHome(page: Page) {
  await page.getByRole('navigation', { name: '화면' }).getByRole('button', { name: '홈' }).click();
}

export async function openLibrary(page: Page, tab: string) {
  await page
    .getByRole('navigation', { name: '화면' })
    .getByRole('button', { name: '지식 보관함', exact: true })
    .click();
  await page.getByRole('tab', { name: tab }).click();
}

// Adds a content to the dashboard in one of its formats (by the format's label), or in its first.
export async function addWidget(page: Page, title: string, format?: string) {
  await page.getByRole('button', { name: '위젯 편집' }).click();
  const content = page
    .getByRole('region', { name: '추가할 수 있는 위젯' })
    .getByRole('group', { name: title, exact: true });
  await (
    format
      ? content.getByRole('button', { name: `${title} · ${format} 추가`, exact: true })
      : content.getByRole('button').first()
  ).click();
  await page.getByRole('button', { name: '편집 완료' }).click();
}

// Saves the LLM settings the way the renderer does, for tests about other features.
export async function connectLlm(page: Page, endpoint: string) {
  const saved = await page.evaluate(
    (base_url) =>
      window.privateAgent!.request('settings.update', {
        provider: 'general',
        api_format: 'openai',
        base_url,
        api_key: 'e2e-key',
        model_name: 'e2e-model',
        headers: [{ name: 'X-Team', value: 'e2e' }],
      }),
    endpoint,
  );
  expect(saved.ok).toBe(true);
  await page.reload();
  await expect(page.getByRole('button', { name: 'e2e-model' })).toBeVisible();
}

export type LlmRequest = {
  headers: IncomingHttpHeaders;
  system: string;
  content: string;
  payload: {
    messages: { role: string; content: string }[];
    stream: boolean;
    model: string;
    max_tokens?: number;
  };
};

// An answer a stand-in LLM writes part by part, as an OpenAI-compatible server streams one to a request with stream:
// each part is a server-sent event of its own, sent once its promise settles (so a test can hold the rest back), and
// the answer ends with `finish` (length: the output limit cut it short). A request without stream gets the whole text
// at once, when every part has come.
export class StreamedReply {
  constructor(
    readonly parts: (string | Promise<string>)[],
    readonly finish: 'stop' | 'length' = 'stop',
  ) {}
}
export const streamed = (parts: (string | Promise<string>)[], finish?: 'stop' | 'length') =>
  new StreamedReply(parts, finish);

// A local stand-in for the LLM: `reply` answers each request by its system prompt, at once or
// when its promise settles; a StreamedReply streams to a request asked with stream.
export async function llmServer(
  reply: (
    request: LlmRequest,
  ) => string | object | StreamedReply | Promise<string | object | StreamedReply>,
) {
  const requests: LlmRequest[] = [];
  const server = createServer(async (request, response) => {
    let body = '';
    for await (const chunk of request) body += chunk;
    const payload = JSON.parse(body);
    const entry = {
      headers: request.headers,
      system: payload.messages[0].content as string,
      content: payload.messages.at(-1).content as string,
      payload,
    };
    requests.push(entry);
    const content = await reply(entry);
    if (content instanceof Error) {
      // The backend sends a request refused with 503 again, up to 4 times in all: Retry-After 0 spares the waits.
      response.writeHead(503, { 'Retry-After': '0' }).end();
      return;
    }
    if (content instanceof StreamedReply) {
      // A stopped answer's connection is closed by the backend while a part is held back: what follows is dropped.
      response.on('error', () => {});
      const gone = () => response.destroyed || request.socket.destroyed;
      if (!payload.stream) {
        const text = (await Promise.all(content.parts)).join('');
        if (gone()) return;
        response.writeHead(200, { 'Content-Type': 'application/json' });
        response.end(
          JSON.stringify({
            choices: [{ message: { content: text }, finish_reason: content.finish }],
          }),
        );
        return;
      }
      response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
      response.flushHeaders();
      const event = (data: object) => `data: ${JSON.stringify(data)}\n\n`;
      for (const part of content.parts) {
        const text = await part;
        if (gone()) return;
        response.write(event({ choices: [{ delta: { content: text }, finish_reason: null }] }));
      }
      if (gone()) return;
      response.end(
        event({ choices: [{ delta: {}, finish_reason: content.finish }] }) + 'data: [DONE]\n\n',
      );
      return;
    }
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(
      JSON.stringify({
        choices: [
          { message: { content: typeof content === 'string' ? content : JSON.stringify(content) } },
        ],
      }),
    );
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as { port: number };
  return {
    requests,
    endpoint: `http://127.0.0.1:${port}/v1/chat/completions`,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}
