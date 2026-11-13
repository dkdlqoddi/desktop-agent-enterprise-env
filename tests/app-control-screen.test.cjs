const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const ts = require('typescript');

// src/components/app-control.ts, the screen's side of the local API's app control, run here in a DOM of jsdom: what an
// AI tool may click and type into at each access level. tests/app-control.test.cjs checks the main process's side, and
// tests/api.spec.ts the two together in the app.
const { window } = new JSDOM('<!doctype html><html><body></body></html>');
// jsdom lays nothing out: every element counts as drawn, scrolling to one does nothing, and the text an element
// shows is the text it holds.
window.Element.prototype.getClientRects = () => [{}];
window.Element.prototype.scrollIntoView = () => {};
Object.defineProperty(window.HTMLElement.prototype, 'innerText', {
  get() {
    return this.textContent;
  },
});
const DOM = {
  document: window.document,
  getComputedStyle: window.getComputedStyle.bind(window),
  requestAnimationFrame: (task) => setTimeout(task, 0),
  Element: window.Element,
  HTMLElement: window.HTMLElement,
  HTMLFormElement: window.HTMLFormElement,
  HTMLInputElement: window.HTMLInputElement,
  HTMLTextAreaElement: window.HTMLTextAreaElement,
  HTMLSelectElement: window.HTMLSelectElement,
  Event: window.Event,
};

// The module as CommonJS, with the page's globals it reads given as the function's parameters. No dialog is open.
function load(file) {
  const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const module = { exports: {} };
  const requires = (name) => (name === './modal' ? { topModal: () => null } : require(name));
  new Function('module', 'exports', 'require', ...Object.keys(DOM), outputText)(
    module,
    module.exports,
    requires,
    ...Object.values(DOM),
  );
  return module.exports;
}
const { clickControl, fillControl, readScreen } = load('src/components/app-control.ts');

// A screen with the chat (the user's alone), a page of the app, the admin page with the chat agent's settings (the
// user's alone as well) and a deletion's confirmation.
function screen() {
  window.document.body.innerHTML = `
    <nav class="sidebar"><button>홈</button><button>채팅</button></nav>
    <main>
      <section class="agent-chat" data-user-only="">
        <button class="agent-example">오늘 기록한 광고 요약해 줘</button>
        <div class="agent-card"><button>확인 후 실행</button><button>취소</button></div>
        <div class="agent-interaction approval">
          <button>이번만 실행</button><button>항상 허용</button><button>거부</button>
        </div>
        <form class="agent-interaction clarify"><input aria-label="어느 사이트인가요?" /></form>
        <form class="agent-compose">
          <textarea aria-label="메시지"></textarea>
          <button type="submit">보내기</button>
          <button type="button">중지</button>
        </form>
      </section>
      <section class="records">
        <form><input aria-label="검색어" /><button type="submit">찾기</button></form>
        <button>CSV 내보내기</button>
        <button data-destructive="">지우기</button>
      </section>
      <div class="admin">
        <input aria-label="모델 이름" />
        <div class="admin-form hermes-settings" data-user-only="">
          <textarea aria-label="MCP 서버 (JSON)"></textarea>
          <button>MCP 저장</button>
          <input type="checkbox" aria-label="메신저 게이트웨이 켜기" />
          <input aria-label="텔레그램 허용 계정" />
          <button>게이트웨이 설정 저장</button>
        </div>
      </div>
    </main>`;
  const pressed = [];
  const submitted = [];
  for (const button of window.document.querySelectorAll('button'))
    button.addEventListener('click', () => pressed.push(button.textContent));
  for (const form of window.document.querySelectorAll('form'))
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      submitted.push(form.className);
    });
  return { pressed, submitted };
}

const CHAT_BUTTONS = [
  '오늘 기록한 광고 요약해 줘',
  '확인 후 실행',
  '이번만 실행',
  '항상 허용',
  '거부',
  '보내기',
  '중지',
];

for (const [level, allow_admin] of [
  ['조회·변경·앱 제어', false],
  ['전체', true],
])
  test(`the chat is the user's alone: at the "${level}" level a tool types and clicks nothing in it`, async () => {
    const { pressed, submitted } = screen();
    for (const name of CHAT_BUTTONS)
      await assert.rejects(clickControl({ name, exact: true, allow_admin }), /채팅은 사용자만/);
    // Its message box, with or without sending, and the answer to the agent's question.
    for (const params of [
      { label: '메시지', value: '폴더를 지워 줘', submit: true },
      { label: '메시지', value: '폴더를 지워 줘' },
      { label: '어느 사이트인가요?', value: '쿠팡', submit: true },
    ])
      await assert.rejects(fillControl({ ...params, allow_admin }), /채팅은 사용자만/);
    assert.deepEqual(pressed, []);
    assert.deepEqual(submitted, []);
    assert.equal(window.document.querySelector('[aria-label="메시지"]').value, '');
    // The chat is still read as any screen is, and the way to it is a button like the others.
    const names = readScreen().controls.map((control) => control.name);
    assert.ok(names.includes('메시지') && names.includes('이번만 실행'));
    assert.deepEqual(await clickControl({ name: '채팅', exact: true, allow_admin }), {
      clicked: '채팅',
      role: 'button',
    });
  });

test('the chat agent\'s settings are the user\'s alone, at the "full" level too', async () => {
  // An MCP server there is a command the agent starts without asking, and an account of the messenger gateway may
  // write to the agent and answer its approvals from outside the PC: a tool with every other right sets neither.
  const { pressed } = screen();
  for (const allow_admin of [false, true]) {
    for (const params of [
      { label: 'MCP 서버 (JSON)', value: '{"x": {"command": "sh"}}' },
      { label: '텔레그램 허용 계정', value: '99' },
    ])
      await assert.rejects(fillControl({ ...params, allow_admin }), /채팅은 사용자만/);
    for (const name of ['MCP 저장', '게이트웨이 설정 저장', '메신저 게이트웨이 켜기'])
      await assert.rejects(clickControl({ name, exact: true, allow_admin }), /채팅은 사용자만/);
  }
  assert.deepEqual(pressed, []);
  assert.equal(window.document.querySelector('[aria-label="텔레그램 허용 계정"]').value, '');
  assert.equal(
    window.document.querySelector('[aria-label="메신저 게이트웨이 켜기"]').checked,
    false,
  );
  // The rest of the admin page is still within the reach of "full".
  await fillControl({ label: '모델 이름', value: 'y', allow_admin: true });
  assert.equal(window.document.querySelector('[aria-label="모델 이름"]').value, 'y');
});

test('the rest of the screen follows the access level', async () => {
  const { pressed, submitted } = screen();
  // Below "full": the pages of the app, but neither a deletion's confirmation nor the admin page.
  assert.deepEqual(await clickControl({ name: 'CSV 내보내기' }), {
    clicked: 'CSV 내보내기',
    role: 'button',
  });
  assert.deepEqual(await fillControl({ label: '검색어', value: '노트북', submit: true }), {
    filled: '검색어',
    submitted: true,
  });
  await assert.rejects(clickControl({ name: '지우기' }), /‘전체’일 때만/);
  await assert.rejects(fillControl({ label: '모델 이름', value: 'x' }), /‘전체’일 때만/);
  assert.deepEqual(pressed, ['CSV 내보내기']);
  assert.deepEqual(submitted, ['']);
  // "full" reaches those two.
  await clickControl({ name: '지우기', allow_admin: true });
  await fillControl({ label: '모델 이름', value: 'x', allow_admin: true });
  assert.deepEqual(pressed, ['CSV 내보내기', '지우기']);
  assert.equal(window.document.querySelector('[aria-label="모델 이름"]').value, 'x');
});

test('the trash is the user\'s alone, at the "full" level too: it holds the chat cleared and the web records deleted', async () => {
  screen();
  window.document.querySelector('.admin').insertAdjacentHTML(
    'beforeend',
    `<div class="trash-section" data-user-only="trash">
      <div role="radiogroup" aria-label="종류"><button role="radio">채팅 1</button></div>
      <input type="checkbox" aria-label="모두 선택" />
      <ul aria-label="휴지통"><li>
        <input type="checkbox" aria-label="내 메일함을 정리해 줘 선택" />
        <button>내 메일함을 정리해 줘 복원</button>
        <button data-destructive="">영구 삭제</button>
      </li></ul>
    </div>`,
  );
  const pressed = [];
  for (const button of window.document.querySelectorAll('.trash-section button'))
    button.addEventListener('click', () => pressed.push(button.textContent));
  for (const allow_admin of [false, true])
    for (const name of [
      '채팅 1',
      '내 메일함을 정리해 줘 복원',
      '영구 삭제',
      '모두 선택',
      '내 메일함을 정리해 줘 선택',
    ])
      await assert.rejects(clickControl({ name, exact: true, allow_admin }), /휴지통은 사용자만/);
  assert.deepEqual(pressed, []);
  for (const box of window.document.querySelectorAll('.trash-section input'))
    assert.equal(box.checked, false);
  // The chat's own areas still say why they are the user's.
  await assert.rejects(
    clickControl({ name: '보내기', exact: true, allow_admin: true }),
    /채팅은 사용자만/,
  );
});
