import { test, expect, type ElectronApplication } from '@playwright/test';
import {
  access,
  chmod,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rm,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import {
  addWidget,
  backendCommand,
  backendPid,
  chooseStorage,
  connectLlm,
  drawer,
  goHome,
  launch,
  llmServer,
  openAdmin,
  results,
  searchBox,
  widget,
} from './helpers';

test('desktop: admin settings, prompt widget, one search box with answers, drawer, graph, layout and persistence', async () => {
  // The app keeps the folder's real path; a temp folder may be given in 8.3 short form.
  const directory = await realpath(await mkdtemp(path.join(tmpdir(), 'private-agent-test-')));
  const wikiDirectory = path.join(directory, 'my-wiki');
  const llm = await llmServer(({ system, content }) =>
    system.includes('extract metadata')
      ? {
          summary: 'Python API 캐시 도입 기록입니다.',
          keywords: ['python', 'api', '캐시'],
          relations: [
            { source: 'python', target: 'api', label: '구현' },
            { source: '캐시', target: 'api', label: '성능 개선' },
          ],
        }
      : system.includes('analyze a user')
        ? {
            terms: ['캐시'],
            keywords: ['api'],
            explanation: 'API 성능 개선과 관련된 기록을 찾습니다.',
          }
        : system.includes('search agent')
          ? {
              thinking: '캐시 도입 기록이 질문과 맞습니다.',
              results: [{ ref: 'D1', reason: 'API 캐시 도입 결정', sections: [] }],
              next: null,
            }
          : system.includes('Answer questions about')
            ? { answer: '개발팀이 금요일까지 API 캐시를 도입합니다. [1]', citations: [1] }
            : content.includes('연결 테스트')
              ? '연결 성공'
              : '## 회의 요약\n\nPython API에 캐시를 도입하기로 결정했습니다.\n\n- 담당: 개발팀\n- 기한: 금요일',
  );
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    let page = await app.firstWindow();
    await expect(page.getByRole('button', { name: '이 위치에 저장하고 시작' })).toBeVisible();
    await page.screenshot({ path: 'test-results/storage-setup.png', fullPage: true });
    await expect(access(path.join(directory, 'wiki.db'))).rejects.toThrow();
    await chooseStorage(page, wikiDirectory);
    await addWidget(page, '가져오기');
    await addWidget(page, 'Prompt 실행');
    await addWidget(page, '예약 실행');
    await addWidget(page, '최근 대화');
    await addWidget(page, '지식 관계도');
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    // One page holds everything; the prompt widget offers the four seeded templates.
    await expect(page.getByRole('heading', { name: '오늘의 브리핑' })).toBeVisible();
    const prompt = widget(page, 'prompt');
    await expect(prompt.getByRole('combobox').locator('option')).toHaveCount(4);
    await page.screenshot({ path: 'test-results/dashboard.png', fullPage: true });

    // Every setting lives on the admin page; the LLM status in the top bar leads to its LLM tab.
    await page.getByRole('button', { name: 'LLM 연결 필요' }).click();
    await expect(page.getByRole('tab', { name: 'LLM 연결' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const endpoint = page.getByLabel(/API 엔드포인트/);
    await expect(endpoint).toHaveValue('https://api.openai.com/v1/chat/completions');
    await page.getByRole('radio', { name: 'Anthropic' }).click();
    await expect(endpoint).toHaveValue('https://api.anthropic.com/v1/messages');
    await page.getByRole('radio', { name: 'OpenAI 호환' }).click();
    await endpoint.fill(llm.endpoint);
    await page.getByLabel(/모델 이름/).fill('e2e-model');
    await page.getByPlaceholder('필요 없는 서버면 비워 둡니다').fill('e2e-secret-key');
    for (const [name, value] of [
      ['api-key', '{api_key}'],
      ['X-Request-Id', '{uuid}'],
    ]) {
      await page.getByRole('button', { name: '헤더 추가' }).click();
      await page
        .getByLabel(/^헤더 \d+ 이름$/)
        .last()
        .fill(name);
      await page
        .getByLabel(/^헤더 \d+ 값$/)
        .last()
        .fill(value);
    }
    await page.getByRole('button', { name: '저장하고 사용' }).click();
    await expect(page.getByText('LLM 연결 설정을 저장했습니다.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'e2e-model' })).toBeVisible();
    await page.screenshot({ path: 'test-results/admin-llm.png', fullPage: true });

    // Prompt widget: Shift+Enter breaks the line, the Enter committing IME composition is ignored,
    // Enter runs. The request count checked below proves neither of the first two ran it.
    await goHome(page);
    await prompt.getByRole('combobox').selectOption({ label: '회의록 정리' });
    const meeting = prompt.getByLabel('회의 내용');
    await meeting.fill('Python API 캐시를 도입합니다.');
    await meeting.press('Shift+Enter');
    await page.keyboard.insertText('담당 개발팀, 금요일까지.');
    await expect(meeting).toHaveValue('Python API 캐시를 도입합니다.\n담당 개발팀, 금요일까지.');
    await meeting.dispatchEvent('keydown', { key: 'Enter', isComposing: true });
    await meeting.press('Enter');
    await expect(prompt.getByRole('heading', { name: '회의 요약' })).toBeVisible();
    expect(llm.requests.filter((r) => r.content.includes('캐시를 도입합니다'))).toHaveLength(1);
    await prompt.getByRole('button', { name: '응답 복사' }).click();
    await expect
      .poll(() => app!.evaluate(({ clipboard }) => clipboard.readText()))
      .toContain('Python API에 캐시');
    await prompt.getByRole('button', { name: 'Wiki에 저장' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Wiki 문서로 저장' })).toBeVisible();

    // The documents widget lists it; the drawer opens it and runs the AI analysis.
    const documents = widget(page, 'documents');
    await expect(documents.locator('.document-row')).toHaveCount(1);
    await documents.locator('.document-row').click();
    await drawer(page).getByRole('button', { name: 'AI 분석하기' }).click();
    await expect(drawer(page).getByText('AI 분석 완료', { exact: true })).toBeVisible();
    await expect(drawer(page).locator('.relation-list')).toContainText('성능 개선');
    await page.screenshot({ path: 'test-results/document-drawer.png', fullPage: true });
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();
    await expect(drawer(page)).toHaveCount(0);

    // One search box: the local search answers at once, then the AI picks and cites documents.
    await searchBox(page).fill('캐시 도입은 어떻게 했어?');
    await searchBox(page).press('Enter');
    await expect(
      results(page).locator('.result-card mark.search-hit', { hasText: '캐시' }).first(),
    ).toBeVisible();
    await expect(results(page).getByText('AI가 고른 문서 1개')).toBeVisible();
    await expect(results(page).locator('.result-card.selected')).toContainText(
      'API 캐시 도입 결정',
    );
    await expect(results(page).locator('.answer')).toContainText('API 캐시를 도입합니다.');
    await results(page).locator('.trace summary').click();
    await expect(results(page).getByRole('list', { name: '검색 과정' })).toContainText('AI 검색');
    await results(page).locator('.citations summary').click();
    await expect(
      results(page).locator('.citations blockquote mark.search-hit', { hasText: '캐시' }),
    ).toHaveCount(1);
    await page.screenshot({ path: 'test-results/search-answer.png', fullPage: true });
    // A result opens in the drawer, which moves between the search words.
    await results(page).locator('.result-card .document-open').first().click();
    const words = page.getByRole('group', { name: '검색어 위치 이동' });
    await expect(words).toContainText('검색어 캐시');
    await words.getByRole('button', { name: '다음 검색어 위치' }).click();
    await expect(page.locator('.document-content mark.search-hit.current')).toHaveCount(1);
    await expect(words).toContainText('1/');
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();
    // A follow-up continues the same conversation until the chip is removed.
    await searchBox(page).fill('담당은 누구야?');
    await searchBox(page).press('Enter');
    await expect(results(page).locator('.answer')).toHaveCount(2);
    const answers = llm.requests.filter((r) => r.system.includes('Answer questions about'));
    expect(
      JSON.parse(answers[1].content).history.map((m: { content: string }) => m.content),
    ).toEqual(['캐시 도입은 어떻게 했어?', '개발팀이 금요일까지 API 캐시를 도입합니다. [1]']);
    await page.getByRole('button', { name: '대화 이어가기 끝내기' }).click();
    await expect(page.getByRole('button', { name: '대화 이어가기 끝내기' })).toHaveCount(0);
    await expect(widget(page, 'conversations').locator('.conversation')).toHaveCount(1);
    // With automatic answers off, the box searches locally until an answer is asked for.
    await openAdmin(page, '검색·대시보드');
    await page.getByRole('switch', { name: /검색할 때 AI 답변 만들기/ }).uncheck();
    await goHome(page);
    const before = llm.requests.length;
    await searchBox(page).fill('PYTHON');
    await searchBox(page).press('Enter');
    await expect(results(page).locator('.result-card')).toHaveCount(1);
    await expect(results(page).getByRole('button', { name: 'AI 답변 받기' })).toBeVisible();
    expect(llm.requests.length).toBe(before);
    await results(page).getByRole('button', { name: '검색 결과 닫기' }).click();
    await expect(results(page)).toHaveCount(0);

    // The knowledge graph widget: a keyword lists its relations and searches its documents.
    const knowledge = widget(page, 'knowledge');
    await expect(knowledge.getByRole('group', { name: '2D 지식 그래프' })).toBeVisible();
    await expect(knowledge.locator('.graph-node')).toHaveCount(3);
    await knowledge.getByRole('button', { name: 'python 키워드 · 문서 1개' }).click();
    const list = knowledge.getByRole('complementary', { name: '지식 목록' });
    await expect(list).toContainText('python → 구현 → api');
    await list.getByRole('button', { name: '관련 문서 검색' }).click();
    await expect(results(page).locator('.result-card')).toHaveCount(1);
    // Documents are drawn, and laid out, only when asked for; the keywords keep their places.
    await expect(knowledge.locator('.graph-doc')).toHaveCount(0);
    const spot = () =>
      knowledge
        .getByRole('button', { name: 'python 키워드 · 문서 1개' })
        .evaluate((node) => node.getAttribute('transform'));
    const placed = await spot();
    await knowledge.getByLabel('문서 함께 보기').check();
    await expect(knowledge.locator('.graph-doc')).toHaveCount(1);
    await knowledge.getByLabel('문서 함께 보기').uncheck();
    await expect(knowledge.locator('.graph-doc')).toHaveCount(0);
    expect(await spot()).toBe(placed);
    // The arrow keys move through the view choices, as through every radio group and tab list.
    await knowledge.getByRole('radio', { name: '2D 그래프' }).press('ArrowRight');
    await expect(knowledge.getByRole('radio', { name: '3D 그래프' })).toBeFocused();
    await expect(knowledge.getByRole('radio', { name: '3D 그래프' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    await expect(knowledge.getByRole('img', { name: /3D 지식 그래프/ })).toBeVisible();
    await page.screenshot({ path: 'test-results/knowledge-widget.png', fullPage: true });

    // Every LLM request since then carries the custom headers, the key only where {api_key} put
    // it and a new ID each time; the analysis sees the question and the Wiki's keywords, the AI
    // search the passages.
    expect(llm.requests.length).toBeGreaterThan(0);
    for (const request of llm.requests) {
      expect(request.headers['api-key']).toBe('e2e-secret-key');
      expect(request.headers.authorization).toBeUndefined();
      expect(request.headers['x-request-id']).toMatch(/^[0-9a-f-]{36}$/);
      // The search box's answers stream as the LLM writes them; every other request takes its answer whole.
      expect(request.payload.stream).toBe(request.system.includes('Answer questions about'));
      expect(request.payload.model).toBe('e2e-model');
    }
    const ids = new Set(llm.requests.map((request) => request.headers['x-request-id']));
    expect(ids.size).toBe(llm.requests.length);
    // Keywords sharing a word with the question come first, then the most frequent ones.
    const analysis = llm.requests.find((r) => r.system.includes('analyze a user'))!;
    expect(JSON.parse(analysis.content)).toEqual({
      question: '캐시 도입은 어떻게 했어?',
      wiki_keywords: ['캐시', 'api', 'python'],
    });
    const agent = JSON.parse(llm.requests.find((r) => r.system.includes('search agent'))!.content);
    expect(agent.candidates[0].snippets.join(' ')).toContain('캐시');

    // Layout editing: remove, add, move and resize widgets; the page keeps the arrangement.
    await page.getByRole('button', { name: '위젯 편집' }).click();
    await page.getByRole('button', { name: '알림 빼기' }).click();
    await expect(widget(page, 'reminders')).toHaveCount(0);
    await page.getByLabel('문서 크기').selectOption('xl');
    await page.getByRole('button', { name: '지식 관계도 앞으로' }).click();
    await page
      .getByRole('region', { name: '추가할 수 있는 위젯' })
      .getByRole('button', { name: '실행 기록 · 기록 추가' })
      .click();
    await page.screenshot({ path: 'test-results/dashboard-editing.png', fullPage: true });
    await page.getByRole('button', { name: '편집 완료' }).click();
    const order = () =>
      page
        .locator('section[data-widget]')
        .evaluateAll((items) =>
          items.map(
            (item) =>
              `${item.getAttribute('data-widget')}:${item.className.match(/span-(\w+)/)![1]}`,
          ),
        );
    const arranged = await order();
    expect(arranged).toContain('documents:xl');
    expect(arranged).not.toContain('reminders:s');
    expect(arranged.at(-1)).toBe('history:xl');
    expect(arranged.indexOf('knowledge:xl')).toBe(arranged.indexOf('conversations:s') - 1);

    const saved = await readFile(path.join(directory, 'settings.json'), 'utf8');
    expect(saved).not.toContain('e2e-secret-key');
    expect(pageErrors).toEqual([]);
    await access(path.join(wikiDirectory, 'wiki.db'));
    expect(JSON.parse(await readFile(path.join(directory, 'storage.json'), 'utf8')).directory).toBe(
      wikiDirectory,
    );
    await app.close();

    app = await launch(directory);
    page = await app.firstWindow();
    await expect(widget(page, 'documents').locator('.document-row')).toHaveCount(1);
    await expect(widget(page, 'documents').locator('.document-row')).toContainText('AI 분석 완료');
    expect(await order()).toEqual(arranged);
    await expect(page.getByRole('button', { name: '이 위치에 저장하고 시작' })).toHaveCount(0);
    const info = await page.evaluate(() => window.privateAgent?.request('settings.get'));
    expect(JSON.stringify(info)).not.toContain('e2e-secret-key');
    const llmSettings = info?.result as {
      provider: string;
      headers: object[];
      secure_storage: boolean;
    };
    expect(llmSettings.provider).toBe('general');
    // The screens read the headers' names and whether each has a value, never the values; the values survive a
    // restart, encrypted where the OS keeps secrets, else in the file as before.
    expect(llmSettings.headers).toContainEqual({
      name: 'api-key',
      has_value: true,
      uses_api_key: true,
    });
    if (llmSettings.secure_storage) expect(saved).not.toContain('{api_key}');
    const forbidden = await page.evaluate(() => window.privateAgent?.request('arbitrary.command'));
    expect(forbidden?.ok).toBe(false);

    // A text file goes through the same import box as links and Office files, then edits and
    // deletes in the drawer without another window.
    const importPath = path.join(directory, '가져오기.md');
    await writeFile(importPath, '# 오프라인 기록\n\nAPI 없이 저장하고 검색할 수 있습니다.', 'utf8');
    await app.evaluate(({ dialog }, file) => {
      dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] });
    }, importPath);
    const importer = widget(page, 'import');
    await importer.getByRole('button', { name: '파일 선택' }).click();
    await expect(importer.getByLabel('가져올 링크 또는 경로')).toHaveValue(importPath);
    await expect(importer).toContainText('Markdown 파일');
    await importer.getByRole('button', { name: 'Wiki로 가져오기' }).click();
    await expect(page.getByText('‘가져오기’: Markdown 문서를 Wiki에 저장했습니다.')).toBeVisible();
    await widget(page, 'documents')
      .locator('.document-row')
      .filter({ hasText: '가져오기' })
      .click();
    await expect(drawer(page).getByRole('heading', { name: '오프라인 기록' })).toBeVisible();
    await drawer(page).getByRole('button', { name: '수정', exact: true }).click();
    await drawer(page).getByLabel('문서 제목', { exact: true }).fill('수정된 오프라인 기록');
    await drawer(page).getByRole('button', { name: '문서 저장' }).click();
    await expect(
      drawer(page).getByRole('heading', { name: '수정된 오프라인 기록', level: 2 }),
    ).toBeVisible();
    await drawer(page).getByRole('button', { name: '삭제', exact: true }).click();
    // The confirming button is solid red, so its label has to stay white to be read.
    await expect(drawer(page).getByRole('button', { name: '삭제', exact: true })).toHaveCSS(
      'color',
      'rgb(255, 255, 255)',
    );
    await drawer(page).getByRole('button', { name: '삭제', exact: true }).click();
    await expect(drawer(page)).toHaveCount(0);
    await expect(widget(page, 'documents').locator('.document-row')).toHaveCount(1);

    // Templates are managed on the admin page and survive editing, then delete without reseeding.
    await openAdmin(page, '프롬프트 템플릿');
    await page.getByRole('button', { name: '새 템플릿' }).click();
    await page.getByLabel('이름', { exact: true }).fill('내 테스트 템플릿');
    await page.getByLabel('분류', { exact: true }).fill('테스트');
    await page
      .getByPlaceholder('다음 업무 내용을 주간 보고서로 정리해 주세요.\n\n{{업무 내용}}')
      .fill('검토해 주세요: {{내용}}');
    await page.getByRole('button', { name: '템플릿 저장' }).click();
    await expect(page.locator('.template-row')).toHaveCount(5);
    await page.getByRole('button', { name: '내 테스트 템플릿 수정' }).click();
    await page.getByLabel('이름', { exact: true }).fill('수정된 테스트 템플릿');
    await page.getByRole('button', { name: '템플릿 저장' }).click();
    await expect(
      page.locator('.template-row').filter({ hasText: '수정된 테스트 템플릿' }),
    ).toBeVisible();
    await page.getByRole('button', { name: '수정된 테스트 템플릿 삭제' }).click();
    await page.getByRole('button', { name: '삭제', exact: true }).click();
    await expect(page.locator('.template-row')).toHaveCount(4);
    await goHome(page);
    await expect(widget(page, 'prompt').getByRole('combobox').locator('option')).toHaveCount(4);

    // What to send when something goes wrong: the app, this PC and the end of both logs, on the clipboard.
    await openAdmin(page, '저장 위치·정보');
    await app.evaluate(({ clipboard }) => clipboard.writeText(''));
    await page.getByRole('button', { name: '진단 정보 복사' }).click();
    let report = '';
    await expect
      .poll(async () => (report = await app!.evaluate(({ clipboard }) => clipboard.readText())))
      .toContain('Private Agent 진단 정보');
    expect(report).toContain('백엔드 실행 중');
    expect(report).toContain('=== backend.log (마지막 부분) ===');
    expect(report).toContain('backend started');

    // A backend that ends unexpectedly is started again, and the app goes on without being restarted.
    process.kill(await backendPid(app), 'SIGKILL');
    await expect(
      page.getByText('예기치 않게 종료되어 다시 시작했습니다', { exact: false }),
    ).toBeVisible({
      timeout: 60_000,
    });
    const stats = await page.evaluate(() => window.privateAgent!.request('stats'));
    expect(stats.ok).toBe(true);
    await app.evaluate(({ clipboard }) => clipboard.writeText(''));
    await page.getByRole('button', { name: '진단 정보 복사' }).click();
    await expect
      .poll(async () => app!.evaluate(({ clipboard }) => clipboard.readText()))
      .toContain('백엔드가 예기치 않게 종료되었습니다');
  } finally {
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

test('a damaged storage.json offers its Wiki again; fields edit by mouse; a crashed screen loads again', async () => {
  const directory = await realpath(await mkdtemp(path.join(tmpdir(), 'private-agent-recovery-')));
  const wikiDirectory = path.join(directory, 'my-wiki');
  const file = path.join(directory, 'storage.json');
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    let page = await app.firstWindow();
    await chooseStorage(page, wikiDirectory);
    await app.close();
    // Cut short, as a power loss may leave it: the folder it still names is offered, and the file is kept.
    const text = await readFile(file, 'utf8');
    const damaged = text.slice(0, text.lastIndexOf('}'));
    await writeFile(file, damaged);
    app = await launch(directory);
    page = await app.firstWindow();
    await expect(
      page.getByText('Wiki 저장 위치 설정 파일(storage.json)이 손상되었습니다.'),
    ).toBeVisible();
    await expect(page.getByLabel('Wiki 저장 폴더', { exact: true })).toHaveValue(wikiDirectory);
    await page.getByRole('button', { name: '이 위치에 저장하고 시작' }).click();
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible({ timeout: 30_000 });
    expect(JSON.parse(await readFile(file, 'utf8')).directory).toBe(wikiDirectory);
    const aside = (await readdir(directory)).filter((name) =>
      name.startsWith('storage.json.corrupt-'),
    );
    expect(aside).toHaveLength(1);
    expect(await readFile(path.join(directory, aside[0]), 'utf8')).toBe(damaged);

    // A backend started again gets the LLM settings the app holds before any request reaches it, though settings.json
    // cannot be read at that moment (held by antivirus; made unreadable here), and saving them still works.
    await connectLlm(page, 'http://127.0.0.1:9/v1');
    const settings = path.join(directory, 'settings.json');
    await chmod(settings, 0);
    const answers = page.evaluate(async () => {
      const seen: { ok: boolean; provider?: string; load_error?: string }[] = [];
      for (const until = Date.now() + 60_000; Date.now() < until;) {
        const reply = await window.privateAgent!.request('settings.get');
        const result = reply.result as { provider: string; load_error: string } | undefined;
        seen.push({ ok: reply.ok, provider: result?.provider, load_error: result?.load_error });
        // Answered again after failing while the backend was gone: the restart is over.
        if (reply.ok && seen.some((item) => !item.ok)) break;
        await new Promise((resolve) => setTimeout(resolve, 20));
      }
      return seen;
    });
    process.kill(await backendPid(app), 'SIGKILL');
    const seen = await answers;
    await chmod(settings, 0o600);
    expect(seen.some((item) => !item.ok)).toBe(true);
    expect(seen.filter((item) => item.ok)).toEqual(
      seen.filter((item) => item.ok).map(() => ({ ok: true, provider: 'general', load_error: '' })),
    );
    const saved = await page.evaluate(() =>
      window.privateAgent!.request('settings.update', { model_name: 'e2e-model' }),
    );
    expect(saved.ok).toBe(true);

    // A field's edit menu by mouse: the menu the app builds, caught instead of shown.
    await app.evaluate(({ Menu }) => {
      const shown: string[][] = [];
      Object.assign(globalThis, { shownMenus: shown });
      Menu.buildFromTemplate = ((template: { label?: string }[]) => {
        shown.push(template.map((item) => item.label ?? '-'));
        return { popup() {} };
      }) as unknown as typeof Menu.buildFromTemplate;
    });
    await searchBox(page).click({ button: 'right' });
    await expect
      .poll(() =>
        app!.evaluate(() => (globalThis as unknown as { shownMenus: string[][] }).shownMenus),
      )
      .toEqual([['잘라내기', '복사', '붙여넣기', '-', '모두 선택']]);

    // A screen that crashed is loaded again, and the app's log says why.
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].webContents.forcefullyCrashRenderer(),
    );
    const log = path.join(directory, 'logs', 'app.log');
    await expect
      .poll(() => readFile(log, 'utf8').catch(() => ''), { timeout: 30_000 })
      .toContain('앱 화면을 다시 불러왔습니다.');
    expect(await readFile(log, 'utf8')).toMatch(/앱 화면 프로세스가 종료되었습니다 \(\w+/);
    await expect
      .poll(() =>
        app!.evaluate(({ BrowserWindow }) =>
          BrowserWindow.getAllWindows()[0].webContents.executeJavaScript(
            'document.querySelectorAll("section[data-widget]").length',
          ),
        ),
      )
      .toBeGreaterThan(0);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

test('a Markdown file is saved at once, and its AI analysis follows in the background', async () => {
  const directory = await realpath(await mkdtemp(path.join(tmpdir(), 'private-agent-markdown-')));
  let release = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  const llm = await llmServer(async ({ system }) => {
    if (!system.includes('extract metadata')) return '연결 성공';
    await held;
    return { summary: '회의 기록입니다.', keywords: ['회의', '캐시'], relations: [] };
  });
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    await chooseStorage(page, path.join(directory, 'wiki'));
    await connectLlm(page, llm.endpoint);
    await addWidget(page, '가져오기');
    const note = path.join(directory, '회의 기록.md');
    await writeFile(note, '# 회의 기록\n\n다음 주까지 API 캐시를 도입합니다.', 'utf8');
    const importer = widget(page, 'import');
    await importer.getByLabel('가져올 링크 또는 경로').fill(note);
    await importer.getByLabel(/가져온 뒤 LLM으로 요약·키워드 분석/).check();
    await importer.getByRole('button', { name: 'Wiki로 가져오기' }).click();
    // Answered while the LLM still holds its answer; the document list shows the analysis going on, then done.
    await expect(page.locator('.toast')).toContainText(
      '‘회의 기록’: Markdown 문서를 Wiki에 저장했습니다. AI 분석은 이어서 진행합니다.',
    );
    const row = widget(page, 'documents').locator('.document-row').filter({ hasText: '회의 기록' });
    await expect(row).toContainText('분석 중');
    release();
    await expect(row).toContainText('AI 분석 완료');
  } finally {
    release();
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

test('first-run storage migration retains existing Wiki and leaves the original database', async () => {
  const directory = await realpath(await mkdtemp(path.join(tmpdir(), 'private-agent-migration-')));
  const destination = path.join(directory, 'selected-wiki');
  let app: ElectronApplication | undefined;
  try {
    const backend = backendCommand();
    const seed = spawnSync(backend.executable, backend.args, {
      input:
        JSON.stringify({
          id: 'seed',
          method: 'wiki.save',
          params: { title: '이전 버전의 기록', content: '저장 위치를 옮겨도 보존되는 문서' },
        }) + '\n',
      encoding: 'utf8',
      timeout: 20000,
      env: { ...process.env, PRIVATE_AGENT_DATA_DIR: directory, PYTHONPATH: '', PYTHONUTF8: '1' },
    });
    expect(seed.status).toBe(0);
    expect(JSON.parse(seed.stdout).result.title).toBe('이전 버전의 기록');
    app = await launch(directory);
    const page = await app.firstWindow();
    await expect(page.getByText(/이전 데이터가 있습니다/)).toBeVisible();
    await chooseStorage(page, destination);
    await expect(widget(page, 'documents')).toContainText('이전 버전의 기록');
    await access(path.join(directory, 'wiki.db'));
    await access(path.join(destination, 'wiki.db'));
    expect(JSON.parse(await readFile(path.join(directory, 'storage.json'), 'utf8')).directory).toBe(
      destination,
    );
    // The history widget is one of the widgets that can be added later.
    await addWidget(page, '실행 기록');
    await expect(widget(page, 'history')).toContainText('아직 실행 기록이 없어요');
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

// A Word document written back once into its file: the document, its import, and the copy of the file the write made
// in the Wiki folder's 원본 백업 folder, as writeback.py records it.
const WRITTEN_BACK = `
import hashlib, pathlib, sqlite3, sys
sys.path.insert(0, "backend")
from agent.html_document import index_markdown, page
wiki, source = pathlib.Path(sys.argv[1]), sys.argv[2]
db = sqlite3.connect(wiki)
html = page("보고서", "<h1>일정</h1><p>3월 15일까지 마칩니다.</p>", "word")
content = index_markdown(html)
db.execute("INSERT INTO documents(id,title,content,format,created_at,updated_at) VALUES ('word','보고서',?,'html',"
           "'2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00')", (content,))
db.execute("INSERT INTO document_html VALUES ('word', ?)", (html,))
row = {"doc_id": "word", "source_path": source, "application": "Word",
       "source_hash": hashlib.sha256(b"word").hexdigest(), "markdown": content,
       "imported_at": "2026-10-01T00:00:00+00:00"}
db.execute(f"INSERT INTO office_sources({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
folder = wiki.parent / "원본 백업"
folder.mkdir(exist_ok=True)
copy = folder / "20261001-090000 보고서.docx"
copy.write_bytes(b"office bytes before the write")
data = copy.read_bytes()
db.execute("INSERT INTO writeback_backups(id,source_path,doc_id,name,checksum,size,kind,state,created_at) "
           "VALUES ('b1',?,'word',?,?,?,'write','written','2026-10-01T00:00:00+00:00')",
           (source, copy.name, hashlib.sha256(data).hexdigest(), len(data)))
db.commit()
`;

test('LLM limits saved and read back; Wiki backups restored after a restart, a restore taken back; an Office file put back from its copy', async () => {
  test.setTimeout(180_000);
  const directory = await realpath(await mkdtemp(path.join(tmpdir(), 'private-agent-backups-')));
  const wikiDirectory = path.join(directory, 'wiki');
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    let page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, wikiDirectory);
    const request = async <T>(method: string, params: Record<string, unknown> = {}) => {
      const reply = await page.evaluate(
        ([name, values]) => window.privateAgent!.request(name, values),
        [method, params] as const,
      );
      expect(reply.ok, `${method}: ${JSON.stringify(reply)}`).toBe(true);
      return reply.result as T;
    };
    type Backups = { pending_restore: { file: string } | null };

    // The LLM's limits: the backend's defaults, a value out of range refused at its field, new values saved and
    // read back.
    await openAdmin(page, 'LLM 연결');
    await page.getByLabel(/API 엔드포인트/).fill('http://127.0.0.1:9/v1/chat/completions');
    await page.getByLabel(/모델 이름/).fill('e2e-model');
    const maxTokens = page.getByLabel('최대 출력 토큰', { exact: true });
    const contextWindow = page.getByLabel('컨텍스트 크기 (토큰)', { exact: true });
    const concurrency = page.getByLabel('동시 요청 수', { exact: true });
    await page.locator('details.advanced summary').click();
    await expect(maxTokens).toHaveValue('0');
    await expect(contextWindow).toHaveValue('64000');
    await expect(concurrency).toHaveValue('3');
    await expect(maxTokens).toHaveAccessibleDescription(/0이면 자동/);
    // The headers' values are said to be encrypted only where the OS keeps them so; elsewhere the settings file has
    // them as they are, and the screen says that instead.
    const { secure_storage: secure } = await request<{ secure_storage: boolean }>('settings.get');
    await expect(page.locator('.llm-headers > small')).toContainText(
      secure
        ? '운영체제 보안 저장소로 암호화해 저장'
        : '설정 파일(settings.json)에 그대로 저장합니다',
    );
    await maxTokens.fill('100');
    await page.getByRole('button', { name: '저장하고 사용' }).click();
    await expect(maxTokens).toHaveJSProperty(
      'validationMessage',
      '최대 출력 토큰은 0(자동) 또는 256~1,000,000으로 입력해 주세요.',
    );
    expect(await request('settings.get')).toMatchObject({ max_tokens: 0, model_name: '' });
    await maxTokens.fill('4096');
    await contextWindow.fill('128000');
    await concurrency.fill('9');
    await expect(concurrency).toHaveJSProperty(
      'validationMessage',
      '동시 요청 수는 1~8로 입력해 주세요.',
    );
    await concurrency.fill('2');
    await page.getByRole('button', { name: '저장하고 사용' }).click();
    await expect(page.getByText('LLM 연결 설정을 저장했습니다.')).toBeVisible();
    const limits = { max_tokens: 4096, context_window: 128000, concurrency: 2 };
    expect(await request('settings.get')).toMatchObject(limits);
    expect(JSON.parse(await readFile(path.join(directory, 'settings.json'), 'utf8'))).toMatchObject(
      limits,
    );
    await page.reload();
    await openAdmin(page, 'LLM 연결');
    await page.locator('details.advanced summary').click();
    await expect(maxTokens).toHaveValue('4096');
    await expect(contextWindow).toHaveValue('128000');
    await expect(concurrency).toHaveValue('2');
    await page.screenshot({ path: 'test-results/admin-llm-limits.png', fullPage: true });

    // A backup made now is listed with its kind, time and size; a document saved after it is not in it.
    await request('wiki.save', { title: '백업 전 문서', content: '백업에 들어가는 문서입니다.' });
    await openAdmin(page, '저장 위치·정보');
    await page.getByRole('button', { name: '지금 백업' }).click();
    await expect(page.getByText(/^Wiki를 백업했습니다: wiki-manual-/)).toBeVisible();
    const list = () => page.getByRole('list', { name: '백업 목록' });
    const manual = () => list().getByRole('listitem').filter({ hasText: '직접' });
    await expect(manual()).toHaveCount(1);
    await expect(manual()).toContainText(/wiki-manual-\d{8}-\d{6}\.db · [\d,]+KB/);
    await request('wiki.save', { title: '백업 뒤 문서', content: '복원하면 사라지는 문서입니다.' });

    // Restoring asks first, saying the app restores the backup when it starts again; the confirming button is marked
    // destructive, for AI tools below the full access level.
    await manual()
      .getByRole('button', { name: /백업 복원$/ })
      .click();
    const question = page.getByRole('group', { name: /앱을 다시 시작할 때 복원하며/ });
    await expect(question.getByRole('button', { name: '복원', exact: true })).toHaveAttribute(
      'data-destructive',
      'true',
    );
    await question.getByRole('button', { name: '복원', exact: true }).click();
    const pending = page.locator('.restore-pending');
    await expect(pending).toContainText('앱을 다시 시작하면 Wiki를');
    await expect(pending.getByRole('button', { name: '지금 다시 시작' })).toBeFocused();
    await expect(manual()).toContainText('복원 예약됨');
    await page.screenshot({ path: 'test-results/admin-backups.png', fullPage: true });
    expect((await request<Backups>('wiki.backups')).pending_restore?.file).toMatch(/^wiki-manual-/);

    // Taken back, nothing waits for the next start.
    await pending.getByRole('button', { name: '복원 취소' }).click();
    await expect(pending).toHaveCount(0);
    expect((await request<Backups>('wiki.backups')).pending_restore).toBeNull();

    // Asked again, then 지금 다시 시작: the app quits to start again as the same program. The new start is caught
    // here, and the test starts the app itself.
    await manual()
      .getByRole('button', { name: /백업 복원$/ })
      .click();
    await question.getByRole('button', { name: '복원', exact: true }).click();
    await expect(pending).toBeVisible();
    await app.evaluate(({ app: electron }) => {
      const caught = globalThis as unknown as { relaunched: unknown[]; quitAsked: boolean };
      caught.relaunched = [];
      caught.quitAsked = false;
      electron.relaunch = (options?: unknown) => void caught.relaunched.push(options ?? null);
      const quit = electron.quit.bind(electron);
      electron.quit = () => {
        caught.quitAsked = true;
        electron.quit = quit;
      };
    });
    await pending.getByRole('button', { name: '지금 다시 시작' }).click();
    await expect
      .poll(() => app!.evaluate(() => (globalThis as unknown as { quitAsked: boolean }).quitAsked))
      .toBe(true);
    const relaunched = await app.evaluate(
      () => (globalThis as unknown as { relaunched: { execPath: string }[] }).relaunched,
    );
    expect(relaunched).toHaveLength(1);
    expect(relaunched[0].execPath).toBe(await app.evaluate(() => process.execPath));
    await app.close();

    // The next start restores the Wiki before anything opens it, and the backups say how it went.
    app = await launch(directory);
    page = await app.firstWindow();
    page.on('pageerror', (error) => errors.push(error.message));
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible({ timeout: 30_000 });
    const titles = (await request<{ title: string }[]>('wiki.list')).map((doc) => doc.title);
    expect(titles).toContain('백업 전 문서');
    expect(titles).not.toContain('백업 뒤 문서');
    await openAdmin(page, '저장 위치·정보');
    await expect(page.locator('.restore-result')).toContainText('되돌렸습니다');
    await expect(page.locator('.restore-pending')).toHaveCount(0);
    await expect(list().getByRole('listitem').filter({ hasText: '복원 전' })).toHaveCount(1);

    // An Office document written back keeps copies of its file: one goes back in the file's place, asking first, and
    // the file as it was is kept as a copy too.
    const file = path.join(directory, '보고서.docx');
    await writeFile(file, 'office bytes after the write');
    const python = path.resolve(
      'backend',
      '.venv',
      process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
    );
    const seeded = spawnSync(
      python,
      ['-c', WRITTEN_BACK, path.join(wikiDirectory, 'wiki.db'), file],
      { encoding: 'utf8', env: { ...process.env, PYTHONPATH: '' } },
    );
    expect(seeded.stderr).toBe('');
    await page.reload();
    await widget(page, 'documents').locator('.document-row').filter({ hasText: '보고서' }).click();
    const source = drawer(page).locator('.document-source');
    await source.locator('.writeback-backups summary').click();
    const copies = source.getByRole('list', { name: '원본 백업 목록' });
    await expect(copies.getByRole('listitem')).toHaveCount(1);
    await expect(copies).toContainText('반영 전');
    await page.screenshot({ path: 'test-results/document-source-backups.png' });
    await copies.getByRole('button', { name: /원본 백업에서 복원$/ }).click();
    const restoreAsk = source.getByRole('group', {
      name: /원본 파일\(보고서\.docx\)을 .+ 바꿀까요/,
    });
    const restore = restoreAsk.getByRole('button', { name: '원본 백업에서 복원', exact: true });
    await expect(restore).toHaveAttribute('data-destructive', 'true');
    await restore.click();
    await expect(page.getByText(/원본 파일 자리에 복원했습니다/)).toBeVisible();
    expect(await readFile(file, 'utf8')).toBe('office bytes before the write');
    await expect(copies.getByRole('listitem')).toHaveCount(2);
    await expect(copies.getByRole('listitem').filter({ hasText: '복원 전' })).toHaveCount(1);
    await expect(
      copies.getByRole('listitem').filter({ hasText: '지금 원본 파일과 같음' }),
    ).toContainText('반영 전');
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
