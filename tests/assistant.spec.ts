import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  addWidget,
  chooseStorage,
  connectLlm,
  drawer,
  launch,
  llmServer,
  results,
  searchBox,
  streamed,
  widget,
} from './helpers';

test('personal assistant widgets: offline tasks, reviewed extraction, document questions, reminders and persistence', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'personal-assistant-'));
  const evidence = '홍길동은 2026년 10월 1일 오후 6시까지 보고서를 작성한다.';
  const llm = await llmServer(({ system }) =>
    system.includes('Extract actionable tasks')
      ? {
          tasks: [
            {
              title: '보고서 작성',
              assignee: '홍길동',
              notes: '',
              due_at: '2026-10-01T18:00:00+09:00',
              remind_at: null,
              repeat: 'none',
              evidence,
            },
          ],
        }
      : system.includes('analyze a user')
        ? { terms: ['보고서'], keywords: [], explanation: '보고서 관련 자료' }
        : system.includes('search agent')
          ? { results: [{ ref: 'D1', reason: '보고서 담당 기록', sections: [] }], next: null }
          : system.includes('Answer questions about')
            ? { answer: '홍길동이 보고서를 작성합니다. [1]', citations: [1] }
            : '## 오늘의 우선 업무\n\n- 저장된 보고서 작성 업무를 확인하세요.',
  );
  let app: ElectronApplication | undefined;
  // Exercise the portable/offline fallback deterministically; native display is covered by main-process tests.
  const start = async () => {
    app = await launch(directory);
    await app.evaluate(({ Notification }) => {
      Notification.isSupported = () => false;
    });
    return app.firstWindow();
  };
  const tasksOf = (page: Page) => widget(page, 'tasks');
  try {
    let page = await start();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await addWidget(page, '최근 대화');
    await expect(page.getByRole('heading', { name: '오늘의 브리핑' })).toBeVisible();
    await expect(widget(page, 'reminders')).toContainText('시스템 알림을 표시할 수 없습니다');
    expect(llm.requests).toHaveLength(0);

    // A task is added in one line, then its deadline and reminder are set in place.
    let tasks = tasksOf(page);
    await tasks.getByLabel('새 할 일').fill('오프라인 마감 확인');
    await tasks.getByLabel('새 할 일').press('Enter');
    // The box keeps its focus while the task saves: what is typed right after stays in it.
    await page.keyboard.type('다음 할 일');
    await expect(tasks.getByLabel('새 할 일')).toHaveValue('다음 할 일');
    await expect(tasks.getByLabel('새 할 일')).toBeFocused();
    await expect(tasks.locator('.task-row')).toHaveCount(1);
    await tasks.getByLabel('새 할 일').fill('');
    await tasks.locator('.task-open').filter({ hasText: '오프라인 마감 확인' }).click();
    await tasks.getByLabel('마감 시간', { exact: true }).fill('2020-01-01T10:00');
    await tasks.getByLabel('알림 시간', { exact: true }).fill('2020-01-01T09:00');
    await tasks.getByRole('button', { name: '할 일 저장' }).click();
    await expect(tasks.locator('.task-form')).toHaveCount(0);
    expect(llm.requests).toHaveLength(0);
    const reminders = widget(page, 'reminders');
    await expect(reminders.locator('.reminder-row')).toContainText('오프라인 마감 확인');
    await reminders.getByRole('button', { name: '10분 뒤', exact: true }).click();
    await expect(reminders.locator('.reminder-row')).toHaveCount(0);
    await expect(widget(page, 'briefing').locator('.overdue-count')).toHaveText('1');

    const seeded = await page.evaluate(
      (evidence) =>
        window.privateAgent!.request('wiki.save', { title: '프로젝트 회의록', content: evidence }),
      evidence,
    );
    expect(seeded.ok).toBe(true);
    await connectLlm(page, llm.endpoint);
    tasks = tasksOf(page);

    // AI extraction happens inside the widget; nothing is saved until the reviewed items are.
    await tasks.getByRole('button', { name: '할 일 추출' }).click();
    await tasks.getByLabel('추출할 문서').selectOption({ label: '프로젝트 회의록' });
    await tasks.getByRole('button', { name: 'AI로 추출' }).click();
    await expect(tasks.getByLabel('할 일 제목', { exact: true })).toHaveValue('보고서 작성');
    const beforeReview = await page.evaluate(() => window.privateAgent!.request('tasks.list'));
    expect(beforeReview.result).toHaveLength(1);
    await tasks.getByLabel('할 일 제목', { exact: true }).fill('검토한 보고서 작성');
    await tasks.getByRole('button', { name: '선택한 1개 저장' }).click();
    await expect(tasks.locator('.task-row')).toHaveCount(2);
    await expect(
      tasks.locator('.task-row').filter({ hasText: '검토한 보고서 작성' }),
    ).toContainText('홍길동');
    await tasks.getByRole('button', { name: '검토한 보고서 작성 완료', exact: true }).click();
    await tasks.getByLabel('할 일 상태').selectOption('done');
    await expect(tasks.locator('.task-row')).toHaveCount(1);
    await expect(tasks.locator('.task-row')).toContainText('검토한 보고서 작성');

    // A question about one document: the drawer points the search box at it.
    await widget(page, 'documents').locator('.document-row').click();
    await drawer(page).getByRole('button', { name: '이 문서에 질문' }).click();
    const chip = page.getByRole('button', { name: '프로젝트 회의록 문서에 질문하기 끝내기' });
    await expect(chip).toBeVisible();
    await searchBox(page).fill('보고서 담당자는 누구야?');
    await searchBox(page).press('Enter');
    await expect(results(page).locator('.answer')).toContainText('홍길동이 보고서를 작성합니다.');
    await expect(results(page)).toContainText('질문한 문서');
    // Picked documents are answered without a search: only the answer request was made.
    expect(llm.requests.filter((r) => r.system.includes('analyze a user'))).toHaveLength(0);
    await results(page).locator('.citations summary').click();
    await expect(results(page).locator('.citations blockquote')).toContainText(evidence);
    await results(page).getByRole('button', { name: '현재 문서 열기' }).click();
    await expect(drawer(page)).toContainText(evidence);
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();
    // Without the document chip the follow-up searches the whole Wiki, in the same conversation.
    await chip.click();
    await searchBox(page).fill('그 사람이 해야 할 일은?');
    await searchBox(page).press('Enter');
    await expect(results(page).locator('.answer')).toHaveCount(2);
    const answer = results(page).locator('.answer').last();
    await expect(answer.locator('.trace summary')).toContainText(
      '질문 분석 → 키워드 검색 → AI 검색',
    );
    await answer.locator('.citations summary').click();
    await expect(answer.locator('.citations mark.search-hit').first()).toHaveText('보고서');
    const chatPayload = llm.requests.filter((r) => r.system.includes('Answer questions about'));
    expect(JSON.parse(chatPayload[1].content).history).toHaveLength(2);
    await page.screenshot({ path: 'test-results/assistant-answer.png', fullPage: true });
    await widget(page, 'briefing').getByRole('button', { name: 'AI 브리핑 생성' }).click();
    await expect(page.getByRole('heading', { name: '오늘의 우선 업무' })).toBeVisible();
    await page.screenshot({ path: 'test-results/assistant-briefing.png', fullPage: true });
    expect(errors).toEqual([]);

    await app!.close();
    page = await start();
    page.on('pageerror', (error) => errors.push(error.message));
    // A saved conversation opens again from the recent conversations widget.
    const conversations = widget(page, 'conversations');
    await conversations
      .locator('.conversation')
      .filter({ hasText: '보고서 담당자는 누구야?' })
      .click();
    await expect(results(page).locator('.answer')).toHaveCount(2);
    await expect(results(page).locator('.question')).toHaveCount(1);
    tasks = tasksOf(page);
    await tasks.getByLabel('할 일 상태').selectOption('all');
    await expect(tasks.locator('.task-row')).toHaveCount(2);
    await tasks.locator('.task-open').filter({ hasText: '오프라인 마감 확인' }).click();
    await tasks.getByRole('button', { name: '삭제', exact: true }).click();
    await tasks.getByRole('button', { name: '삭제', exact: true }).click();
    await expect(tasks.locator('.task-row')).toHaveCount(1);
    await conversations.getByRole('button', { name: '보고서 담당자는 누구야? 대화 삭제' }).click();
    await conversations.getByRole('button', { name: '삭제', exact: true }).click();
    await expect(conversations.locator('.conversation')).toHaveCount(0);
    await expect(results(page)).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

// A promise the test settles when it lets an answer go on.
function gate() {
  let open = () => {};
  const opened = new Promise<void>((resolve) => (open = resolve));
  return { open, opened };
}

test('streamed answers: the text as it is written, 답변 중지, the output limit, another conversation meanwhile and a refusal', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-stream-'));
  const steps = gate();
  const held = gate();
  const other = gate();
  const busy = gate();
  // Every question asks about one document, so each makes the answer request alone; the answers stream in parts, some
  // held back until the test lets them go on.
  const llm = await llmServer(({ system, content }) => {
    if (!system.includes('Answer questions about')) return '연결 성공';
    const { question } = JSON.parse(content) as { question: string };
    if (question.includes('단계'))
      return streamed([
        '{"answer": "첫 문단입니다.',
        steps.opened.then(() => '\\n\\n둘째 문단입니다. [1]", '),
        '"citations": [1]}',
      ]);
    if (question.includes('멈출'))
      return streamed(['{"answer": "멈출 답변의 앞부분', held.opened.then(() => ' 뒷부분"}')]);
    if (question.includes('긴')) return streamed(['{"answer": "긴 답변의 앞부분'], 'length');
    if (question.includes('다른'))
      return streamed([
        '{"answer": "다른 대화의 앞부분',
        other.opened.then(() => '과 뒷부분입니다."}'),
      ]);
    if (question.includes('바쁜'))
      return busy.opened.then(() => ({ answer: '바쁜 대화의 답변입니다.', citations: [] }));
    return streamed(['{"answer": "이어서 ', '답했습니다."}']);
  });
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    const seeded = await page.evaluate(() =>
      window.privateAgent!.request('wiki.save', {
        title: '배포 회의록',
        content: '금요일에 배포하기로 했습니다.',
      }),
    );
    expect(seeded.ok).toBe(true);
    const docId = (seeded.result as { id: string }).id;
    await connectLlm(page, llm.endpoint);
    await addWidget(page, '최근 대화');
    const conversations = widget(page, 'conversations').locator('.conversation');
    await widget(page, 'documents').locator('.document-row').click();
    await drawer(page).getByRole('button', { name: '이 문서에 질문' }).click();
    const ask = async (question: string) => {
      await searchBox(page).fill(question);
      await searchBox(page).press('Enter');
    };
    const live = results(page).locator('.answer.live');
    const answers = results(page).locator('.answer');
    const column = results(page).locator('.answer-column');

    // The text shows as the LLM writes it, with 답변 중지, and the saved answer takes its place with its sources.
    await ask('단계별 답변은?');
    await expect(live).toContainText('첫 문단입니다.');
    await expect(live).not.toContainText('둘째 문단');
    await expect(results(page).getByRole('button', { name: '답변 중지' })).toBeEnabled();
    await expect(column).toHaveAttribute('aria-busy', 'true');
    // The new conversation is listed while its answer is written.
    await expect(conversations.first()).toContainText('답변 중');
    await page.screenshot({ path: 'test-results/answer-streaming.png' });
    steps.open();
    await expect(live).toHaveCount(0);
    await expect(answers).toHaveCount(1);
    await expect(answers).toContainText('둘째 문단입니다.');
    await expect(answers.locator('.citations summary')).toContainText('배포 회의록');
    await expect(column).not.toHaveAttribute('aria-busy');
    expect(llm.requests.at(-1)!.payload.stream).toBe(true);
    await expect(conversations).toHaveCount(1);
    await expect(conversations).not.toContainText('답변 중');

    // 답변 중지 keeps what came, marked as stopped.
    await ask('멈출 수 있는 답변은?');
    await expect(live).toContainText('멈출 답변의 앞부분');
    await results(page).getByRole('button', { name: '답변 중지' }).click();
    const stopped = results(page).locator('.answer.stopped');
    await expect(live).toHaveCount(0);
    await expect(stopped).toHaveCount(1);
    await expect(stopped).toContainText('멈출 답변의 앞부분');
    await expect(stopped).not.toContainText('뒷부분');
    await expect(stopped.locator('.answer-note')).toHaveText('답변을 중지했습니다.');
    await expect(answers).toHaveCount(2);

    // An answer the output limit cut short says so, and leads to the setting that lifts it.
    await ask('긴 답변을 써 줘');
    await expect(answers).toHaveCount(3);
    await expect(live).toHaveCount(0);
    const cut = answers.last();
    await expect(cut).toContainText('긴 답변의 앞부분');
    await expect(cut.locator('.answer-note.truncated')).toContainText('최대 출력 길이');
    await expect(cut.getByRole('button', { name: 'LLM 설정 열기' })).toBeVisible();

    // Another conversation while an answer is written: the answer goes on, and shows again on coming back to it.
    await page.getByRole('button', { name: '대화 이어가기 끝내기' }).click();
    await ask('다른 대화를 시작해요');
    await expect(live).toContainText('다른 대화의 앞부분');
    await conversations.filter({ hasText: '단계별 답변은?' }).click();
    await expect(results(page).getByRole('heading', { name: '단계별 답변은?' })).toBeVisible();
    await expect(live).toHaveCount(0);
    await expect(answers).toHaveCount(3);
    const writing = conversations.filter({ hasText: '다른 대화를 시작해요' });
    await expect(writing).toContainText('답변 중');
    await writing.click();
    await expect(live).toContainText('다른 대화의 앞부분');
    other.open();
    await expect(live).toHaveCount(0);
    await expect(answers).toContainText('다른 대화의 앞부분과 뒷부분입니다.');
    await expect(conversations).toHaveCount(2);
    await expect(conversations.filter({ hasText: '다른 대화를 시작해요' })).not.toContainText(
      '답변 중',
    );

    // A question in a conversation still answering another (here an AI tool's, which the screen did not ask) is
    // refused by the backend, which the box says by the question; it can be sent again once the other answer is in.
    const listed = await page.evaluate(() =>
      window.privateAgent!.request('assistant.conversations'),
    );
    const otherId = (listed.result as { id: string; title: string }[]).find(
      (item) => item.title === '다른 대화를 시작해요',
    )!.id;
    const background = page.evaluate(
      ([id, doc]) =>
        window.privateAgent!.request('assistant.ask', {
          question: '바쁜 질문',
          conversation_id: id,
          document_ids: [doc],
        }),
      [otherId, docId] as const,
    );
    await expect.poll(() => llm.requests.some((r) => r.content.includes('바쁜 질문'))).toBe(true);
    await ask('이어서 물어볼게요');
    const refusal = results(page).getByRole('alert');
    await expect(refusal).toContainText('이 대화의 이전 질문에 답하는 중입니다');
    await expect(results(page).locator('.question').last()).toHaveText('이어서 물어볼게요');
    busy.open();
    expect((await background).ok).toBe(true);
    await refusal.getByRole('button', { name: '다시 보내기' }).click();
    await expect(answers.last()).toContainText('이어서 답했습니다.');
    await expect(results(page).getByRole('alert')).toHaveCount(0);
    await expect(answers).toHaveCount(3);
    expect(errors).toEqual([]);
  } finally {
    for (const waiting of [steps, held, other, busy]) waiting.open();
    await app?.close();
    await llm.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
