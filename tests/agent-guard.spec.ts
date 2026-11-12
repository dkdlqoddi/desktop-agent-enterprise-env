import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  chatBox,
  chooseStorage,
  connectLlm,
  launch,
  llmServer,
  openAdmin,
  toolCalls,
} from './helpers';

// What Hermes' own safety check asks the model about a flagged command begins with these words; the stand-in model
// leaves the decision to the user.
const GUARDIAN = 'security reviewer';
const COMMAND = 'rm -rf ./stale-output';
const DELETED = '출력 폴더를 지웠습니다.';
const REFUSED = '지우지 못했습니다.';

async function request<T>(page: Page, method: string, params: Record<string, unknown> = {}) {
  const response = await page.evaluate(
    ([name, values]) => window.privateAgent!.request(name, values),
    [method, params] as const,
  );
  expect(response.ok, `${method}: ${JSON.stringify(response)}`).toBe(true);
  return response.result as T;
}

type Shown = {
  allowed: { command: string }[];
  protected: { credentials: boolean; folders: string[] };
  blocked: string[];
};

test('agent chat: a command is asked about with how far each answer reaches, and the settings take it back', async () => {
  test.setTimeout(420_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-guard-'));
  const output = path.join(directory, 'wiki', 'hermes', 'stale-output');
  const stale = async () => {
    await mkdir(output, { recursive: true });
    await writeFile(path.join(output, 'old.log'), 'x');
  };
  const llm = await llmServer(({ system, content, payload }) => {
    if (system.includes(GUARDIAN)) return 'ESCALATE';
    if (!payload.tools?.length) return { message: '도구 없이 답합니다.', actions: [] };
    const results = payload.messages.filter((message) => message.role === 'tool');
    const asked = String(
      payload.messages.filter((message) => message.role === 'user').at(-1)?.content ?? content,
    )
      .split('New message from the user:')
      .at(-1)!;
    if (asked.includes('어디에')) {
      if (!results.length)
        return toolCalls([
          'clarify',
          { questions: [{ question: '어디에 저장할까요?', choices: ['문서', '바탕 화면'] }] },
        ]);
      return String(results.at(-1)!.content).includes('내 폴더')
        ? '내 폴더에 저장하겠습니다.'
        : '답을 받지 못했습니다.';
    }
    if (!results.length) return toolCalls(['terminal', { command: COMMAND }]);
    return String(results.at(-1)!.content).includes('BLOCKED') ? REFUSED : DELETED;
  });
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory, { PRIVATE_AGENT_WEB_HEADLESS: '1' });
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);
    await page.reload();
    await expect(chatBox(page)).toBeVisible();
    const engine = (await request<{ engine: string }>(page, 'agent.engine')).engine;
    // A source run without the bundled agent has no terminal and asks about no command.
    test.skip(engine !== 'hermes', 'npm run build:hermes builds the agent this test runs');

    const screens = page.getByRole('navigation', { name: '화면' });
    const toChat = () => screens.getByRole('button', { name: '채팅', exact: true }).click();
    const log = page.getByRole('log', { name: '대화' });
    const prompt = page.getByRole('group', { name: '명령 실행 확인' });
    const answers = log.locator('.agent-message.assistant:not(.pending)');
    const send = async (words: string) => {
      await chatBox(page).fill(words);
      await chatBox(page).press('Enter');
    };

    // The agent stops at a flagged command and says how far each answer reaches.
    await stale();
    await send('출력 폴더 지워 줘');
    await expect(prompt).toBeVisible({ timeout: 120_000 });
    await expect(prompt.locator('.agent-command')).toContainText(COMMAND);
    for (const name of ['이번만 실행', '이 대화에서 허용', '항상 허용', '거부'])
      await expect(prompt.getByRole('button', { name })).toBeVisible();
    await expect(prompt).toContainText('글자까지 같은 이 명령만 앞으로 묻지 않습니다.');
    await expect(prompt).toContainText('대화를 비우거나 앱을 닫을 때까지');
    expect((await stat(output)).isDirectory()).toBe(true);

    // 항상 허용: the command runs, and what the turn did is kept under its answer.
    await prompt.getByRole('button', { name: '항상 허용' }).click();
    await expect(answers.last()).toContainText(DELETED, { timeout: 120_000 });
    await expect(stat(output)).rejects.toThrow();
    const activity = answers.last().locator('.agent-activity');
    await expect(activity.locator('summary')).toContainText('한 일 2건');
    await activity.locator('summary').click();
    const steps = activity.getByRole('list', { name: '이 답을 만들며 한 일' }).locator('li');
    await expect(steps.nth(0)).toContainText('도구');
    await expect(steps.nth(1)).toContainText(COMMAND);
    await expect(steps.nth(1)).toContainText('항상 허용');

    // The command is kept with the app's settings on this PC, not in the Wiki folder.
    expect(
      (await request<Shown>(page, 'hermes.get')).allowed.map((entry) => entry.command),
    ).toEqual([COMMAND]);
    const settings = JSON.parse(await readFile(path.join(directory, 'hermes.json'), 'utf8'));
    expect(settings.allowed[0].command).toBe(COMMAND);
    const config = await readFile(path.join(directory, 'wiki', 'hermes', 'config.yaml'), 'utf8');
    expect(JSON.parse(config).command_allowlist).toEqual([]);

    // The same command runs without a question from then on.
    await stale();
    await send('출력 폴더 또 지워 줘');
    await expect(answers).toHaveCount(2, { timeout: 120_000 });
    await expect(answers.last()).toContainText(DELETED);
    await expect(answers.last().locator('.agent-activity summary')).toContainText('한 일 1건');
    await expect(stat(output)).rejects.toThrow();
    expect(
      JSON.parse(await readFile(path.join(directory, 'wiki', 'hermes', 'config.yaml'), 'utf8'))
        .command_allowlist,
    ).toEqual([COMMAND]);

    // The settings list it, and take it back.
    await openAdmin(page, '채팅 에이전트');
    const allowed = page.getByRole('list', { name: '항상 허용한 명령' });
    await expect(allowed.locator('li')).toHaveCount(1);
    await expect(allowed).toContainText(COMMAND);
    await expect(page.locator('.hermes-settings[data-user-only]')).toHaveCount(1);
    await allowed.getByRole('button', { name: '허용 취소' }).click();
    await expect(page.getByText('항상 허용한 명령이 없습니다.')).toBeVisible();
    expect((await request<Shown>(page, 'hermes.get')).allowed).toEqual([]);

    // Asked again; allowed for this conversation, the same kind of command runs unasked until it is forgotten.
    await toChat();
    await stale();
    await send('출력 폴더 다시 지워 줘');
    await expect(prompt).toBeVisible({ timeout: 120_000 });
    await prompt.getByRole('button', { name: '이 대화에서 허용' }).click();
    await expect(answers).toHaveCount(3, { timeout: 120_000 });
    await expect(answers.last()).toContainText(DELETED);
    const granted = (await request<{ session: string[] }>(page, 'agent.grants')).session;
    expect(granted.length).toBeGreaterThan(0);
    await stale();
    await send('출력 폴더 한 번 더 지워 줘');
    await expect(answers).toHaveCount(4, { timeout: 120_000 });
    await expect(answers.last()).toContainText(DELETED);
    await openAdmin(page, '채팅 에이전트');
    await expect(page.getByRole('list', { name: '이 대화에서 허용한 명령 종류' })).toContainText(
      granted[0],
    );
    const history = page.locator('.hermes-history');
    await expect(history.locator('summary')).toContainText('최근에 확인을 받은 명령 2건');
    await history.locator('summary').click();
    const asked = page.getByRole('list', { name: '최근에 확인을 받은 명령' }).locator('li');
    await expect(asked.nth(0)).toContainText('이 대화에서 허용');
    await expect(asked.nth(1)).toContainText('항상 허용');
    await page.getByRole('button', { name: '모두 다시 묻기' }).click();
    await expect(page.getByRole('list', { name: '이 대화에서 허용한 명령 종류' })).toHaveCount(0);
    expect((await request<{ session: string[] }>(page, 'agent.grants')).session).toEqual([]);

    // A pattern the user refuses outright stops the command before any question.
    const patterns = page.getByRole('textbox', { name: '실행하지 않을 명령' });
    await patterns.fill('*stale-output*\n\n  *never-this*  ');
    await page.getByRole('button', { name: '실행하지 않을 명령 저장' }).click();
    await expect(page.getByText('실행하지 않을 명령을 저장했습니다.')).toBeVisible();
    await expect(patterns).toHaveValue('*stale-output*\n*never-this*');
    expect((await request<Shown>(page, 'hermes.get')).blocked).toEqual([
      '*stale-output*',
      '*never-this*',
    ]);
    await toChat();
    await stale();
    await send('출력 폴더 마지막으로 지워 줘');
    await expect(answers).toHaveCount(5, { timeout: 120_000 });
    await expect(answers.last()).toContainText(REFUSED);
    await expect(prompt).toHaveCount(0);
    expect((await stat(output)).isDirectory()).toBe(true);

    // A question with choices also takes an answer in the user's own words.
    await send('어디에 저장할지 물어봐 줘');
    const question = page.getByRole('form', { name: '추가 질문' });
    await expect(question).toBeVisible({ timeout: 120_000 });
    // The choice the agent recommends is marked in the app's language.
    await expect(question.getByRole('button', { name: '문서 (추천)', exact: true })).toBeVisible();
    const choice = question.getByRole('button', { name: '바탕 화면', exact: true });
    await choice.click();
    await expect(choice).toHaveAttribute('aria-pressed', 'true');
    await question.getByRole('textbox', { name: '어디에 저장할까요? (직접 입력)' }).fill('내 폴더');
    await expect(choice).toHaveAttribute('aria-pressed', 'false');
    await question.getByRole('button', { name: '보내기' }).click();
    await expect(answers.last()).toContainText('내 폴더에 저장하겠습니다.', { timeout: 120_000 });

    // A folder is protected and freed on the settings screen; a path that is not a full one is refused.
    await openAdmin(page, '채팅 에이전트');
    const vault = path.join(directory, 'vault');
    await mkdir(vault);
    const zone = page.getByRole('group', { name: '보호 구역' });
    const folder = page.getByRole('textbox', { name: '보호할 폴더', exact: true });
    await folder.fill('relative/folder');
    await zone.getByRole('button', { name: '추가', exact: true }).click();
    await expect(page.getByText(/보호할 폴더는 전체 경로로 적어 주세요/)).toBeVisible();
    await folder.fill(vault);
    await zone.getByRole('button', { name: '추가', exact: true }).click();
    const folders = page.getByRole('list', { name: '보호하는 폴더' });
    await expect(folders).toContainText(vault);
    expect((await request<Shown>(page, 'hermes.get')).protected).toEqual({
      credentials: true,
      folders: [vault],
    });
    await folders.getByRole('button', { name: '보호 풀기' }).click();
    await expect(folders).toHaveCount(0);
    const credentials = page.getByRole('checkbox', { name: /자격 증명이 있는 곳 보호/ });
    await expect(credentials).toBeChecked();
    // The switch follows the saved setting, so it is clicked rather than set.
    await credentials.click();
    await expect(credentials).not.toBeChecked();
    await expect(page.getByText('자격 증명이 있는 곳의 보호를 껐습니다.')).toBeVisible();
    expect((await request<Shown>(page, 'hermes.get')).protected.credentials).toBe(false);

    // What the agent remembers is the user's to read and change: an entry is added, corrected and deleted.
    const about = '사용자에 대해 기억하는 것';
    await page
      .getByRole('textbox', { name: `${about}에 추가` })
      .fill('답은 짧게 한다\n\n오전에 일한다');
    const memory = page.locator('.hermes-memory').filter({ hasText: about });
    await memory.getByRole('button', { name: '추가' }).click();
    const remembered = page.getByRole('list', { name: about }).locator('li');
    await expect(remembered).toHaveCount(2);
    await expect(memory).toContainText(/\d+ \/ 1,375자/);
    await remembered.nth(0).getByRole('button', { name: '고치기' }).click();
    await page
      .getByRole('textbox', { name: `${about} 항목 고치기` })
      .fill('답은 두 문장 안에 한다');
    await remembered.nth(0).getByRole('button', { name: '저장' }).click();
    await expect(remembered.nth(0)).toContainText('답은 두 문장 안에 한다');
    await remembered.nth(1).getByRole('button', { name: '지우기' }).click();
    const forgetting = page.getByRole('group', { name: /이 기억을 지울까요\?/ });
    await expect(forgetting.locator('[data-destructive]')).toHaveCount(1);
    await forgetting.locator('[data-destructive]').click();
    await expect(remembered).toHaveCount(1);
    expect(
      await readFile(path.join(directory, 'wiki', 'hermes', 'memories', 'USER.md'), 'utf8'),
    ).toBe('답은 두 문장 안에 한다');
    // The records the agent kept of the turns above are deleted whole, after a question.
    const records = page.locator('.hermes-memory').filter({ hasText: '작업 기록' });
    await expect(records).toContainText(/파일 \d+개/);
    await records.getByRole('button', { name: '작업 기록 지우기' }).click();
    await page
      .getByRole('group', { name: /에이전트의 작업 기록을 모두 지울까요\?/ })
      .locator('[data-destructive]')
      .click();
    await expect(page.getByText('에이전트의 작업 기록을 지웠습니다.')).toBeVisible();
    await expect(records).toContainText('없음');
    await expect(stat(path.join(directory, 'wiki', 'hermes', 'state.db'))).rejects.toThrow();
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true });
  }
});
