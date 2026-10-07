import { test, expect, type ElectronApplication } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { jiraServer } from './fixtures/jira.cjs';
import {
  addWidget,
  chooseStorage,
  connectLlm,
  goHome,
  launch,
  llmServer,
  openAdmin,
  widget,
} from './helpers';

type Element = { n: number; kind: string; text?: string; label?: string; name?: string };
const ANALYSIS = {
  category: 'credentials',
  cause: '통합 로그인이 ID 또는 비밀번호를 거부했습니다.',
  evidence: ['화면에 ‘비밀번호가 올바르지 않습니다’ 안내가 있습니다.'],
  actions: ['관리 → 사이트 계정에서 비밀번호를 고친 뒤 로그인 확인을 누르세요.'],
  retry: false,
  wait: 0,
};

// The LLM's stand-in: it finds a login screen's fields by their labels as a person would,
// and answers the analysis of a failure with ANALYSIS.
function reply({ system, content }: { system: string; content: string }) {
  if (system.startsWith('You tell the user')) return ANALYSIS;
  const elements: Element[] = JSON.parse(content).page.elements;
  const find = (kind: string, word: string) =>
    elements.find(
      (element) =>
        element.kind === kind &&
        [element.text, element.label, element.name].join(' ').includes(word),
    )?.n ?? null;
  const password = elements.find((element) => element.kind === 'password')?.n ?? null;
  return {
    screen: 'login',
    id: find('text', '계정'),
    password,
    next: password ? null : find('button', '다음'),
    submit: password ? find('button', '확인') : null,
    reason: '로그인 화면입니다.',
  };
}

test('the LLM signs in where no login method fits, and explains a failed login and import', async () => {
  // Each login waits for the login methods' screens (30 seconds) before the LLM reads the page.
  test.setTimeout(360_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'web-assist-'));
  const server = await jiraServer();
  server.options.steps = true;
  const llm = await llmServer(reply);
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);
    await addWidget(page, '가져오기');

    await openAdmin(page, '사이트 계정');
    const option = page.getByRole('switch', { name: /로그인·접속 실패 시 AI 분석/ });
    await expect(option).toBeChecked();
    await page.getByRole('button', { name: '사이트 추가' }).click();
    const form = page.getByRole('form', { name: '사이트 추가' });
    await form.getByLabel('주소').fill(server.url);
    await form.getByLabel('이름').fill('팀 JIRA');
    await form.getByLabel('로그인 ID').fill('tester');
    await form.getByLabel('비밀번호', { exact: true }).fill('correct horse');
    await form.getByRole('button', { name: '사이트 저장' }).click();
    const site = page.locator('.site').filter({ hasText: '팀 JIRA' });

    // No login method fits the two-step screen: the LLM picks its fields, and the site keeps them.
    await site.getByRole('button', { name: '로그인 확인' }).click();
    await expect(site.getByRole('status')).toContainText('로그인했습니다 · AI가 찾은 로그인 화면', {
      timeout: 120_000,
    });
    await expect(site).toContainText('자동 감지됨 · AI가 찾은 로그인 화면');
    expect(server.logins).toEqual([{ id: 'tester', password: 'correct horse' }]);

    // A refused password: the check comes with the LLM's analysis.
    await site.getByRole('button', { name: '팀 JIRA 수정' }).click();
    const edit = page.getByRole('form', { name: '팀 JIRA 수정' });
    await edit.getByLabel('비밀번호', { exact: true }).fill('wrong');
    await edit.getByRole('button', { name: '사이트 저장' }).click();
    await site.getByRole('button', { name: '로그인 확인' }).click();
    await expect(site.getByRole('status')).toContainText('로그인하지 못했습니다', {
      timeout: 120_000,
    });
    const analysis = site.getByRole('note', { name: 'AI 원인 분석' });
    await expect(analysis).toContainText('ID·비밀번호');
    await expect(analysis).toContainText(ANALYSIS.cause);
    await expect(analysis).toContainText(ANALYSIS.actions[0]);
    await analysis.getByText('근거 1개').click();
    await expect(analysis).toContainText(ANALYSIS.evidence[0]);
    await page.screenshot({ path: 'test-results/web-assist-check.png', fullPage: true });

    // An import that cannot sign in shows the analysis with its error.
    await goHome(page);
    const importer = widget(page, 'import');
    await importer.getByLabel('가져올 링크 또는 경로').fill(server.url);
    await importer.getByRole('button', { name: 'Wiki로 가져오기' }).click();
    const job = importer.locator('.job');
    await expect(job).toContainText('가져오지 못했습니다', { timeout: 120_000 });
    await expect(job.getByRole('note', { name: 'AI 원인 분석' })).toContainText(ANALYSIS.cause);
    await page.screenshot({ path: 'test-results/web-assist-import.png', fullPage: true });
    // Neither the password nor the ID reached the LLM.
    expect(JSON.stringify(llm.requests.map(({ content }) => content))).not.toMatch(
      /correct horse|wrong|tester/,
    );

    // With the option off the LLM is not asked, and the site keeps only the failure.
    await openAdmin(page, '사이트 계정');
    await expect(site.getByRole('note', { name: 'AI 원인 분석' })).toContainText(ANALYSIS.cause);
    await option.click();
    await expect(option).not.toBeChecked();
    const asked = llm.requests.length;
    await site.getByRole('button', { name: '로그인 확인' }).click();
    await expect(site.getByRole('status')).toContainText('로그인하지 못했습니다', {
      timeout: 120_000,
    });
    await expect(site.getByRole('note', { name: 'AI 원인 분석' })).toHaveCount(0);
    expect(llm.requests.length).toBe(asked);
  } finally {
    await app?.close();
    await server.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
