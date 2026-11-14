const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const ts = require('typescript');

// src/components/app-control.ts, the screen's side of the local API's app control, run here in a DOM of jsdom: what an
// AI tool may read, click and type into at each access level. tests/app-control.test.cjs checks the main process's
// side, and tests/api.spec.ts and tests/user-only.spec.ts the two together in the app.
const { window } = new JSDOM('<!doctype html><html><head></head><body></body></html>');
// jsdom lays nothing out: every element counts as drawn and scrolling to one does nothing. The text an element shows
// is the text it holds, but for what is not visible, which Chromium's innerText leaves out as well.
window.Element.prototype.getClientRects = () => [{}];
window.Element.prototype.scrollIntoView = () => {};
Object.defineProperty(window.HTMLElement.prototype, 'innerText', {
  get() {
    const texts = window.document.createTreeWalker(this, window.NodeFilter.SHOW_TEXT);
    let shown = '';
    for (let node = texts.nextNode(); node; node = texts.nextNode())
      if (window.getComputedStyle(node.parentElement).visibility !== 'hidden') shown += node.data;
    return shown;
  },
});
// The rules of the app's stylesheet that leave what the user alone may use undrawn while the screen is read or
// captured, here as in the app (jsdom matches no pseudo-element, so those rules stay out).
const HIDING = [
  ...new JSDOM(
    `<style>${fs.readFileSync(path.join(__dirname, '..', 'src/styles/app.css'), 'utf8')}</style>`,
  ).window.document.styleSheets[0].cssRules,
]
  .filter(
    (rule) => rule.selectorText?.includes('data-screen-') && !rule.selectorText.includes('::'),
  )
  .map((rule) => rule.cssText);
window.document.head.insertAdjacentHTML('beforeend', `<style>${HIDING.join('\n')}</style>`);
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
const { clickControl, fillControl, maskCapture, readScreen, unmaskCapture } = load(
  'src/components/app-control.ts',
);

// A screen with the chat (the user's alone), a page of the app, the web records and the admin page with the chat
// agent's settings (the user's alone as well), and a deletion's confirmation.
function screen() {
  window.document.body.innerHTML = `
    <nav class="sidebar"><button>홈</button><button>채팅</button><button>웹 기록</button></nav>
    <main>
      <section class="agent-chat" data-user-only="chat">
        <ol class="agent-messages">
          <li class="agent-message user"><p class="agent-text">PLAN-ZEBRA 폴더를 정리해 줘</p></li>
          <li class="agent-message assistant"><p class="agent-text">ANSWER-ORCA 정리했습니다</p></li>
        </ol>
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
      <section class="library">
        <h2>지식 보관함</h2>
        <form><input aria-label="검색어" /><button type="submit">찾기</button></form>
        <button>목록 내보내기</button>
        <button data-destructive="">지우기</button>
      </section>
      <div class="workspace-page records-page" data-user-only="records">
        <button>열린 페이지 기록</button><button>CSV 내보내기</button>
        <label>사이트 <select><option value="">전체</option><option value="coupang">쿠팡</option></select></label>
        <label>기록 검색 <input type="search" placeholder="제목·검색어로 찾기" /></label>
        <ul aria-label="최근 기록한 페이지"><li>
          <input type="checkbox" aria-label="쿠팡 노트북 선택" />
          <button>쿠팡 노트북 스크린샷 보기</button>
        </li></ul>
        <button data-destructive="">선택한 기록 지우기</button>
      </div>
      <div class="admin">
        <input aria-label="모델 이름" />
        <div class="admin-form hermes-settings" data-user-only="agent">
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
    // A reading of the screen holds none of its controls, and the way to it is a button like the others.
    const names = readScreen().controls.map((control) => control.name);
    assert.ok(
      names.includes('채팅') && !names.includes('메시지') && !names.includes('이번만 실행'),
    );
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
  assert.deepEqual(await clickControl({ name: '목록 내보내기' }), {
    clicked: '목록 내보내기',
    role: 'button',
  });
  assert.deepEqual(await fillControl({ label: '검색어', value: '노트북', submit: true }), {
    filled: '검색어',
    submitted: true,
  });
  await assert.rejects(clickControl({ name: '지우기' }), /‘전체’일 때만/);
  await assert.rejects(fillControl({ label: '모델 이름', value: 'x' }), /‘전체’일 때만/);
  assert.deepEqual(pressed, ['목록 내보내기']);
  assert.deepEqual(submitted, ['']);
  // "full" reaches those two.
  await clickControl({ name: '지우기', allow_admin: true });
  await fillControl({ label: '모델 이름', value: 'x', allow_admin: true });
  assert.deepEqual(pressed, ['목록 내보내기', '지우기']);
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

test('the web records are the user\'s alone, at the "full" level too', async () => {
  // What the sites showed the user and the ads they pressed: no method of the local API reaches them, and no click or
  // typing of a tool does.
  screen();
  const pressed = [];
  for (const button of window.document.querySelectorAll('.records-page button'))
    button.addEventListener('click', () => pressed.push(button.textContent));
  for (const allow_admin of [false, true]) {
    for (const name of [
      '열린 페이지 기록',
      'CSV 내보내기',
      '쿠팡 노트북 스크린샷 보기',
      '쿠팡 노트북 선택',
      '선택한 기록 지우기',
    ])
      await assert.rejects(clickControl({ name, exact: true, allow_admin }), /웹 기록은 사용자만/);
    for (const params of [
      { label: '기록 검색', value: '노트북' },
      { label: '제목·검색어로 찾기', value: '노트북', submit: true },
      { label: '사이트', value: '쿠팡' },
    ])
      await assert.rejects(fillControl({ ...params, allow_admin }), /웹 기록은 사용자만/);
  }
  assert.deepEqual(pressed, []);
  for (const field of window.document.querySelectorAll('.records-page input, .records-page select'))
    assert.equal(
      field.type === 'checkbox' ? field.checked : field.value,
      field.type === 'checkbox' ? false : '',
    );
  // The way there is a button like the others.
  assert.deepEqual(await clickControl({ name: '웹 기록', exact: true }), {
    clicked: '웹 기록',
    role: 'button',
  });
});

const LEFT_OUT = ['채팅', '웹 기록', '관리 → 채팅 에이전트'];

test('the screen reading leaves out what the user alone may use, and names those areas', () => {
  screen();
  const reading = readScreen();
  // Nothing of the chat, the web records or the chat agent's settings: neither their words nor their controls.
  for (const words of [
    'PLAN-ZEBRA',
    'ANSWER-ORCA',
    '오늘 기록한 광고',
    '이번만 실행',
    '열린 페이지 기록',
    '쿠팡',
    'MCP 저장',
    '게이트웨이',
  ])
    assert.ok(!reading.text.includes(words), words);
  assert.deepEqual(
    reading.controls.map((control) => control.name),
    ['홈', '채팅', '웹 기록', '검색어', '찾기', '목록 내보내기', '지우기', '모델 이름'],
  );
  // The text says first which areas it left out, as the result does, in the page's order.
  assert.deepEqual(reading.user_only, LEFT_OUT);
  assert.ok(
    reading.text.startsWith(
      `[사용자만 다루는 영역이라 읽지 않았습니다: ${LEFT_OUT.join(', ')}]\n\n`,
    ),
    reading.text,
  );
  assert.ok(reading.text.includes('지식 보관함') && reading.text.includes('목록 내보내기'));
  // They are hidden for the reading's own task alone.
  assert.equal(window.document.documentElement.hasAttribute('data-screen-reading'), false);
  assert.equal(
    window.getComputedStyle(window.document.querySelector('.agent-text')).visibility,
    'visible',
  );
  // A screen without such an area is read as it is.
  for (const area of window.document.querySelectorAll('[data-user-only]')) area.remove();
  const plain = readScreen();
  assert.deepEqual(plain.user_only, []);
  assert.ok(plain.text.startsWith('홈'), plain.text);
});

test('a click or a typing aimed into such an area is refused, naming nothing that is in there', async () => {
  const { pressed } = screen();
  // Two of the chat's buttons and none outside it have 실행 in their names: the answer says why the areas on the
  // screen are out of reach, and names neither. Nor does a name found nowhere answer otherwise, so that a refusal
  // tells nothing of what those areas hold.
  for (const target of [
    { name: '실행' },
    { name: '실행', index: 1 },
    { name: '실행', role: 'button' },
    { name: '이번만 실행', exact: true },
    { name: '어디에도 없는 단추' },
  ]) {
    const error = await clickControl(target).then(
      () => assert.fail(`clicked ${target.name}`),
      (failure) => failure,
    );
    assert.match(error.message, /^채팅은 사용자만.* 웹 기록은 사용자만.* 그 밖의 화면에는/);
    assert.doesNotMatch(error.message, /확인 후 실행|오늘 기록한|항상 허용/);
  }
  const typed = await fillControl({ label: '사이트', value: '쿠팡' }).catch((failure) => failure);
  assert.match(typed.message, /웹 기록은 사용자만/);
  assert.doesNotMatch(typed.message, /어느 사이트인가요/);
  // A name both in such an area and outside it is the one outside, the only one a tool sees.
  assert.deepEqual(await clickControl({ name: '기록' }), { clicked: '웹 기록', role: 'button' });
  assert.deepEqual(await fillControl({ label: '검색', value: '노트북' }), {
    filled: '검색어',
    submitted: false,
  });
  assert.equal(window.document.querySelector('.records-page input[type="search"]').value, '');
  assert.deepEqual(pressed, ['웹 기록']);
  // With none of those areas on the screen, a name found nowhere is just that.
  for (const area of window.document.querySelectorAll('[data-user-only]')) area.remove();
  await assert.rejects(
    clickControl({ name: '실행' }),
    /^Error: ‘실행’에 맞는 항목이 화면에 없습니다/,
  );
});

test('a capture leaves such areas undrawn, also one that comes meanwhile, until it is over', async () => {
  screen();
  window.document
    .querySelector('.library')
    .insertAdjacentHTML('beforeend', '<input type="password" aria-label="비밀번호" />');
  const opacity = (selector) =>
    window.getComputedStyle(window.document.querySelector(selector)).opacity;
  const root = window.document.documentElement;
  assert.deepEqual(await maskCapture(), { secrets: 1, user_only: LEFT_OUT, painted: true });
  assert.equal(root.hasAttribute('data-screen-capture'), true);
  for (const selector of ['.agent-chat', '.agent-text', '.records-page button', '.hermes-settings'])
    assert.equal(opacity(selector), '0', selector);
  assert.equal(opacity('.library button'), '1');
  // The trash opened meanwhile: it is not drawn for the capture either.
  window.document
    .querySelector('.admin')
    .insertAdjacentHTML(
      'beforeend',
      '<div class="trash-section" data-user-only="trash"><button>복원</button></div>',
    );
  assert.equal(opacity('.trash-section button'), '0');
  assert.deepEqual(unmaskCapture(), { shown: true });
  assert.equal(root.hasAttribute('data-screen-capture'), false);
  for (const selector of ['.agent-text', '.records-page button', '.trash-section button'])
    assert.equal(opacity(selector), '1', selector);
  // A screen with nothing to leave out is captured at once, and kept so until the capture is over all the same.
  for (const area of window.document.querySelectorAll('[data-user-only], [type="password"]'))
    area.remove();
  assert.deepEqual(await maskCapture(), { secrets: 0, user_only: [], painted: true });
  assert.equal(root.hasAttribute('data-screen-capture'), true);
  unmaskCapture();
  assert.equal(root.hasAttribute('data-screen-capture'), false);
});
