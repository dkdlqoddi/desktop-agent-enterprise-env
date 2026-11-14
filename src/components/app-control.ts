import { topModal } from './modal';

// The screen side of the local API's app control (electron/app-control.cjs sends the commands): what the window
// shows as text, and clicking or typing into its controls by their visible names, the way a person would.

export type ScreenControl = {
  role: string;
  name: string;
  disabled?: boolean;
  selected?: boolean;
  checked?: boolean;
  value?: string;
};
// `show`: a demo recording (electron/demo.cjs) lights up what it clicks or fills for a moment, since a video shows no
// pointer.
type Target = { allow_admin?: boolean; exact?: boolean; index?: number | null; show?: boolean };

const CLICKABLE: Record<string, string> = {
  button: 'button, [role="button"], summary',
  tab: '[role="tab"]',
  link: 'a[href], [role="link"]',
  checkbox: 'input[type="checkbox"]:not([role="switch"]), [role="checkbox"]',
  switch: '[role="switch"]',
  option: '[role="option"], [role="radio"], input[type="radio"]',
};
const FIELDS =
  'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="button"]):not([type="submit"]), textarea, select';
const TEXT_LIMIT = 20_000;
const CONTROL_LIMIT = 200;
// An input's value as the screen reading shows it; a document being written can be millions of characters.
const VALUE_LIMIT = 500;
// What the user alone may use (data-user-only: the areas named with guard below) is left out of what a tool reads off
// the screen and of a capture of the window: the stylesheet leaves it undrawn while <html> carries one of these
// (styles/app.css).
const USER_ONLY_AREA = '[data-user-only]';
const READING = 'data-screen-reading';
const CAPTURING = 'data-screen-capture';

const squash = (value: string) => value.replace(/\s+/g, ' ').trim();
const fold = (value: string) => squash(value).toLowerCase();
type Field = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
const isField = (element: Element): element is Field =>
  element instanceof HTMLInputElement ||
  element instanceof HTMLTextAreaElement ||
  element instanceof HTMLSelectElement;

// Whether the user is writing a document in the document panel, which a tool must not throw away.
export const editingDocument = () => !!document.querySelector('.document-form');

const WRITING =
  '사용자가 문서 창에서 글을 쓰고 있어 화면을 조작하지 않았습니다. 저장하거나 취소한 뒤 다시 시도해 주세요.';
const UNSAVED =
  '사용자가 화면에 입력한 내용을 아직 저장하지 않아 화면을 바꾸지 않았습니다. 사용자가 저장하거나 지운 뒤 다시 시도해 주세요.';

// What the user changed in the forms on the screen: for each field of a form they began to change, the value it held
// before, since the form was shown or last submitted. A tool's typing (no trusted event) is no work of the user's, nor
// is a question not yet sent: in the search box (role="search"), whose words stay when the screen changes, or in the
// greeting's question box (data-no-draft).
const DRAFT_FIELDS =
  'input:not([type="hidden"]):not([type="file"]):not([type="button"]):not([type="submit"]):not([type="reset"]):not([type="image"]), textarea, select';
const before = new Map<Field, string>();
const valueOf = (field: Field) =>
  field instanceof HTMLInputElement && (field.type === 'checkbox' || field.type === 'radio')
    ? String(field.checked)
    : field.value;

// The user is about to change something in a form (a key, a press, text arriving): its fields hold what they held
// before. Fields that left the screen are forgotten.
function remember(event: Event) {
  if (!event.isTrusted || !(event.target instanceof Element)) return;
  const form = event.target.closest('form');
  if (!form || form.matches('[role="search"], [data-no-draft]')) return;
  for (const field of before.keys()) if (!field.isConnected) before.delete(field);
  for (const field of form.querySelectorAll<Field>(DRAFT_FIELDS))
    if (!before.has(field)) before.set(field, valueOf(field));
}

// A submitted form holds nothing unsaved any more, nor one saved by a button that does not submit it (data-saves).
function forget(event: Event) {
  const form =
    event.target instanceof HTMLFormElement
      ? event.target
      : event.target instanceof Element && event.target.closest('[data-saves]')
        ? event.target.closest('form')
        : null;
  if (form) for (const field of form.querySelectorAll<Field>(DRAFT_FIELDS)) before.delete(field);
}

// Whether a form on the screen holds a change the user made and has not submitted. The document panel tells itself
// whether its writing is saved (data-dirty): a save that failed leaves it unsaved although the form was submitted.
export function changedForm() {
  if (document.querySelector('.document-form[data-dirty]')) return true;
  for (const [field, value] of before) {
    if (!field.isConnected) before.delete(field);
    else if (valueOf(field) !== value) return true;
  }
  return false;
}

// Reloading, closing the window or leaving the screen would throw away what the user writes: a tool may not.
export function refuseWhileWriting() {
  if (editingDocument()) throw new Error(WRITING);
  if (changedForm()) throw new Error(UNSAVED);
}

// Follows what the user changes in forms. The main process asks about it before it closes the window (electron/main.cjs)
// rather than this page holding its own unload: Electron answers a held unload itself, which an automation attached to
// the window (the E2E tests) takes for a dialog it cannot find.
export function watchDrafts() {
  const types = ['keydown', 'pointerdown', 'beforeinput', 'paste', 'drop'] as const;
  const ends = ['submit', 'reset', 'click'] as const;
  for (const type of types) document.addEventListener(type, remember, true);
  for (const type of ends) document.addEventListener(type, forget, true);
  return () => {
    for (const type of types) document.removeEventListener(type, remember, true);
    for (const type of ends) document.removeEventListener(type, forget, true);
  };
}

// Controls that change the screen (the sidebar, the top bar with the search box, the tabs of a screen): pressing them
// would leave the form the user is changing. Tabs inside a form only change what the form shows.
function leaves(element: Element) {
  const tabs = element.closest('[role="tablist"]');
  return !!element.closest('.sidebar, .topbar') || (!!tabs && !tabs.closest('form'));
}

// The local API's commands one after another, should the main process (which sends them one at a time) give up on
// one: a click cannot land while a fill waits to submit its form.
let turn: Promise<unknown> = Promise.resolve();
export function inTurn<T>(task: () => T | Promise<T>): Promise<T> {
  const run = turn.then(task);
  turn = run.catch(() => {});
  return run;
}

// What a tool may click or type into: with the panel open, only the panel and the toast, as for the user, whom the
// open panel keeps from the screen behind it.
function reachable(element: Element) {
  const panel = topModal();
  return !panel || panel.contains(element) || !!element.closest('.toast');
}

function visible(element: Element) {
  if (
    !(element instanceof HTMLElement) ||
    element.closest('[hidden], [inert], [aria-hidden="true"]')
  )
    return false;
  const style = getComputedStyle(element);
  return (
    style.visibility !== 'hidden' && style.display !== 'none' && element.getClientRects().length > 0
  );
}

function roleOf(element: Element) {
  for (const [role, selector] of Object.entries(CLICKABLE))
    if (element.matches(selector)) return role;
  return element instanceof HTMLSelectElement ? 'select' : 'input';
}

// A label's own words, without the input inside it or its help text.
function labelText(label: HTMLLabelElement) {
  const copy = label.cloneNode(true) as HTMLElement;
  copy.querySelectorAll('input, textarea, select, button, small').forEach((node) => node.remove());
  return copy.textContent ?? '';
}

function nameOf(element: Element) {
  const label = element.getAttribute('aria-label');
  if (label?.trim()) return squash(label);
  const by = element.getAttribute('aria-labelledby');
  if (by) {
    const text = by
      .split(/\s+/)
      .map((id) => document.getElementById(id)?.textContent ?? '')
      .join(' ');
    if (text.trim()) return squash(text);
  }
  if (isField(element)) {
    const labels = [...(element.labels ?? [])].map(labelText).join(' ');
    if (labels.trim()) return squash(labels);
    if ('placeholder' in element && element.placeholder) return squash(element.placeholder);
  }
  return squash(
    (element as HTMLElement).innerText ||
      element.textContent ||
      element.getAttribute('title') ||
      '',
  );
}

// A password or key never gives its value away, also while its eye button shows it; nor does a field marked
// data-secret, such as an LLM header value.
const secret = (element: Element) =>
  (element instanceof HTMLInputElement && element.type === 'password') ||
  !!element.closest('.secret-input, [data-secret]');

function describe(element: Element): ScreenControl {
  const control: ScreenControl = { role: roleOf(element), name: nameOf(element) };
  if ((element as HTMLButtonElement).disabled || element.getAttribute('aria-disabled') === 'true')
    control.disabled = true;
  if (element.getAttribute('aria-selected') === 'true' || element.getAttribute('aria-current'))
    control.selected = true;
  if (element instanceof HTMLInputElement && element.type === 'checkbox')
    control.checked = element.checked;
  else if (isField(element) && !secret(element))
    control.value =
      element.value.length > VALUE_LIMIT
        ? `${element.value.slice(0, VALUE_LIMIT)}…`
        : element.value;
  return control;
}

// What the screen shows as text, with the controls a tool may use. What the user alone may use is left out, its text
// and its controls, and the text begins by naming those areas, so that a tool knows why it sees nothing of them
// (`user_only` names them too). The stylesheet hides them while <html> carries READING, which is set and removed
// within this one task: the screen is never drawn without them, and hiding by visibility, unlike display, keeps the
// layout and with it where the user scrolled to.
export function readScreen() {
  const user_only = areaNames(userOnlyAreas());
  const root = document.documentElement;
  root.setAttribute(READING, '');
  let shown: string;
  try {
    shown = document.body.innerText;
  } finally {
    root.removeAttribute(READING);
  }
  const text = shown
    .replace(/[ \t]+/g, ' ')
    .replace(/\n\s*\n\s*\n+/g, '\n\n')
    .trim();
  const controls = [
    ...document.querySelectorAll(`${Object.values(CLICKABLE).join(', ')}, ${FIELDS}`),
  ]
    .filter((element) => !element.closest(USER_ONLY_AREA) && visible(element))
    .slice(0, CONTROL_LIMIT)
    .map(describe)
    .filter((control) => control.name || control.role === 'input');
  const note = user_only.length
    ? `[사용자만 다루는 영역이라 읽지 않았습니다: ${user_only.join(', ')}]\n\n`
    : '';
  return {
    text: note + (text.length > TEXT_LIMIT ? `${text.slice(0, TEXT_LIMIT)}…` : text),
    controls,
    user_only,
  };
}

// The one element `wanted` names: exact names first, then names containing it; `index` picks among several. The
// controls of an area the user alone may use are none of a tool's to find, as the screen reading leaves them out: a
// name is never matched against theirs, and no message lists them.
function pick<T extends Element>(
  elements: T[],
  names: (element: T) => string[],
  wanted: string,
  target: Target,
) {
  const goal = fold(wanted);
  const usable = elements.filter((element) => !element.closest(USER_ONLY_AREA));
  const exact = usable.filter((element) => names(element).some((name) => fold(name) === goal));
  const found =
    target.exact || exact.length
      ? exact
      : usable.filter((element) => names(element).some((name) => fold(name).includes(goal)));
  if (!found.length) throw new Error(notFound(wanted));
  if (target.index !== null && target.index !== undefined) {
    if (!found[target.index])
      throw new Error(
        `‘${wanted}’에 맞는 항목은 ${found.length}개입니다. index는 0~${found.length - 1}로 입력해 주세요.`,
      );
    return found[target.index];
  }
  if (found.length > 1)
    throw new Error(
      `‘${wanted}’에 맞는 항목이 ${found.length}개입니다: ${found
        .slice(0, 8)
        .map((element) => names(element)[0])
        .join(', ')}. exact나 index로 하나를 골라 주세요.`,
    );
  return found[0];
}

const USER_ONLY =
  '채팅은 사용자만 다룰 수 있습니다. 채팅에 적은 글은 이 PC에서 터미널·파일·브라우저를 쓰는 에이전트가 실행하고, 명령 실행 확인과 에이전트의 설정(쓰는 도구, 메신저에서 일을 시킬 계정)은 사용자의 결정이므로 AI 도구는 허용 범위와 관계없이 채팅과 관리 → 채팅 에이전트에 입력하거나 그 버튼을 누를 수 없습니다.';
// The trash's screen (admin/trash.tsx) holds what the chat and the web records put there, which is the user's alone.
const USER_ONLY_TRASH =
  '관리 → 휴지통은 사용자만 다룰 수 있습니다. 비운 채팅과 지운 웹 기록도 있는 곳이라, AI 도구는 허용 범위와 관계없이 이 화면에 입력하거나 그 버튼을 누를 수 없습니다. 휴지통은 call_api의 trash.list·trash.restore·trash.purge로 다루세요.';
// The web records (web-records.tsx), which no method of the local API reaches either.
const USER_ONLY_RECORDS =
  '웹 기록은 사용자만 다룰 수 있습니다. 사이트에서 본 광고·상품과 사용자가 누른 광고의 기록이라, AI 도구는 허용 범위와 관계없이 이 화면에 입력하거나 그 버튼을 누를 수 없습니다.';
// The web records' automatic routines on the routines' screen (widgets/routines.tsx), which no method of the local API
// shows, makes or runs either (electron/routines.cjs).
const USER_ONLY_WEB_ROUTINE =
  '웹 기록의 자동 작업(사이트 광고·상품 기록)은 사용자만 다룰 수 있습니다. 웹 기록처럼, AI 도구는 허용 범위와 관계없이 그 작업을 보거나 만들거나 실행하거나 바꿀 수 없습니다.';
// An area of a later screen that no entry below names is the user's all the same.
const USER_ONLY_OTHER =
  '이 영역은 사용자만 다룰 수 있어, AI 도구는 허용 범위와 관계없이 여기에 입력하거나 그 버튼을 누를 수 없습니다.';

// The values of data-user-only, one for each kind of area the user alone may use.
export type UserOnly = 'chat' | 'agent' | 'trash' | 'records' | 'web-routine';
// The areas the user alone may use, by the value of their data-user-only: what an AI tool is told such an area is
// called, when the screen reading and a capture leave it out, and why it may not type or press there.
type UserOnlyArea = { name: string; refusal: string };
const AREAS: [UserOnly, UserOnlyArea][] = [
  ['chat', { name: '채팅', refusal: USER_ONLY }],
  ['agent', { name: '관리 → 채팅 에이전트', refusal: USER_ONLY }],
  ['trash', { name: '관리 → 휴지통', refusal: USER_ONLY_TRASH }],
  ['records', { name: '웹 기록', refusal: USER_ONLY_RECORDS }],
  ['web-routine', { name: '웹 기록의 자동 작업', refusal: USER_ONLY_WEB_ROUTINE }],
];
const USER_ONLY_AREAS = new Map<string, UserOnlyArea>(AREAS);
const userOnlyArea = (area: Element): UserOnlyArea =>
  USER_ONLY_AREAS.get(area.getAttribute('data-user-only') ?? '') ?? {
    name: squash(area.getAttribute('aria-label') ?? '') || '사용자만 다루는 영역',
    refusal: USER_ONLY_OTHER,
  };

// The areas the user alone may use that the screen holds now, each once (not one inside another) and in the page's
// order. Left undrawn while the window is captured, they are on the screen all the same: their boxes are asked, not
// their drawing.
function userOnlyAreas() {
  return [...document.querySelectorAll(USER_ONLY_AREA)].filter(
    (area) => !area.parentElement?.closest(USER_ONLY_AREA) && area.getClientRects().length > 0,
  );
}
const areaNames = (areas: Element[]) => [...new Set(areas.map((area) => userOnlyArea(area).name))];

// Nothing a tool may use goes by `wanted`. With an area the user alone may use on the screen, the answer is why that
// area is out of reach, whether or not it holds such a control, so that it tells nothing of what is in there.
function notFound(wanted: string) {
  const areas = userOnlyAreas();
  if (!areas.length)
    return `‘${wanted}’에 맞는 항목이 화면에 없습니다. 화면 읽기(app.read_screen)로 이름을 확인해 주세요.`;
  const reasons = new Set(areas.map((area) => userOnlyArea(area).refusal));
  return `${[...reasons].join(' ')} 그 밖의 화면에는 ‘${wanted}’에 맞는 항목이 없습니다.`;
}

// The chat belongs to the user alone (data-user-only="chat"): what is typed there is carried out by an agent with a
// terminal, files and a browser on this PC, and the prompt that asks before a dangerous command is the user's own
// decision. No access level reaches it, "full" neither, nor does a demo recording: the words of a document a tool has
// read could otherwise have the tool run a command here and approve it itself. The agent's own settings (admin → 채팅
// 에이전트, data-user-only="agent") are the user's in the same way: an MCP server there is a command the agent starts
// without asking, and the accounts of the messenger gateway may write to the agent and answer its approvals from
// outside the PC. So are the trash (data-user-only="trash"), which holds the chat cleared and the web records deleted,
// and the web records themselves (data-user-only="records") with their automatic routines on the routines' screen
// (data-user-only="web-routine"), which the local API's methods do not reach either. None
// of them is read or captured for a tool either (readScreen, maskCapture), nor does a tool learn their controls'
// names: pick never matches them, and this refuses one should it be reached all the same.
// The admin page and passwords decide what the API itself may do, and the import box and a document's update from its
// file read this PC's files (marked data-requires-full); only the "full" access level reaches them. So does the
// button that carries out a deletion or an emptying (data-destructive): the words of a document may ask for it.
function guard(element: Element, target: Target) {
  const own = element.closest(USER_ONLY_AREA);
  if (own) throw new Error(userOnlyArea(own).refusal);
  if (target.allow_admin) return;
  if (element.closest('[data-destructive]'))
    throw new Error(
      '삭제·비우기처럼 되돌리기 어려운 동작의 확인 버튼은 API 허용 범위가 ‘전체’일 때만 누를 수 있습니다. 문서는 call_api의 wiki.delete로, 할 일·대화·실행 기록·프롬프트·예약·자동 작업은 각각의 delete 메서드로 휴지통에 보낼 수 있습니다.',
    );
  if (element.closest('.admin, [data-requires-full]') || secret(element))
    throw new Error(
      '관리 화면, 가져오기 상자, 파일의 원본에서 업데이트와 비밀번호 입력란은 API 허용 범위가 ‘전체’일 때만 다룰 수 있습니다. JIRA·Confluence 링크는 import_source로 가져올 수 있습니다.',
    );
}

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function spotlight(element: Element, name: string, ms: number) {
  element.classList.add(name);
  setTimeout(() => element.classList.remove(name), ms);
}

export async function clickControl(params: { name: string; role?: string } & Target) {
  if (editingDocument()) throw new Error(WRITING);
  const selector = params.role ? CLICKABLE[params.role] : Object.values(CLICKABLE).join(', ');
  if (!selector) throw new Error('role을 확인해 주세요.');
  const element = pick(
    [...document.querySelectorAll(selector)].filter(
      (element) => visible(element) && reachable(element),
    ),
    (item) => [nameOf(item)],
    params.name,
    params,
  );
  guard(element, params);
  if ((element as HTMLButtonElement).disabled || element.getAttribute('aria-disabled') === 'true')
    throw new Error(`‘${nameOf(element)}’ 항목은 지금 누를 수 없습니다.`);
  if (leaves(element) && changedForm()) throw new Error(UNSAVED);
  element.scrollIntoView({ block: 'center' });
  if (params.show) {
    spotlight(element, 'demo-press', 900);
    await pause(450);
    // The screen may have changed meanwhile.
    guard(element, params);
  }
  (element as HTMLElement).click();
  return { clicked: nameOf(element), role: roleOf(element) };
}

function setValue(element: Field, value: string) {
  if (element instanceof HTMLSelectElement) {
    const option = [...element.options].find(
      (item) => item.value === value || squash(item.text) === squash(value),
    );
    if (!option)
      throw new Error(
        `‘${value}’ 선택지가 없습니다: ${[...element.options].map((item) => squash(item.text)).join(', ')}`,
      );
    value = option.value;
  }
  // React keeps its own copy of an input's value; the prototype's setter makes it see the change.
  const prototype =
    element instanceof HTMLSelectElement
      ? HTMLSelectElement.prototype
      : element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value')?.set?.call(element, value);
  element.dispatchEvent(new Event('input', { bubbles: true }));
  element.dispatchEvent(new Event('change', { bubbles: true }));
}

export async function fillControl(
  params: { label: string; value?: string; submit?: boolean } & Target,
) {
  if (editingDocument()) throw new Error(WRITING);
  const fields = [...document.querySelectorAll<Field>(FIELDS)].filter(
    (element) => visible(element) && reachable(element),
  );
  const element = pick(
    fields,
    (item) =>
      [
        nameOf(item),
        item.getAttribute('aria-label'),
        'placeholder' in item ? item.placeholder : '',
      ].filter((name): name is string => !!name),
    params.label,
    params,
  );
  guard(element, params);
  if (element.disabled || ('readOnly' in element && element.readOnly))
    throw new Error(`‘${nameOf(element)}’ 입력란은 지금 바꿀 수 없습니다.`);
  // Searching from the top bar leaves the screen.
  if (params.submit && leaves(element) && changedForm()) throw new Error(UNSAVED);
  element.focus();
  if (params.show) spotlight(element, 'demo-focus', 1600);
  setValue(element, params.value ?? '');
  if (params.submit) {
    // Let React render the new value before the form reads it.
    await new Promise((resolve) => setTimeout(resolve, 50));
    const form = element.closest('form');
    if (!form) throw new Error(`‘${nameOf(element)}’ 입력란에는 제출할 양식이 없습니다.`);
    // Checked again as it is sent: the value may have made the form one that reads files on this PC
    // (data-requires-full), and the user may have begun writing meanwhile.
    if (editingDocument()) throw new Error(WRITING);
    if (!form.isConnected || !reachable(form))
      throw new Error(`‘${nameOf(element)}’ 입력란의 양식이 화면에서 사라졌습니다.`);
    guard(form, params);
    form.requestSubmit();
  }
  return { filled: nameOf(element), submitted: !!params.submit };
}

// While the window is captured (electron/app-control.cjs): passwords, keys and header values are drawn as dots, also
// those an eye button shows, and what the user alone may use is not drawn at all (CAPTURING on <html>,
// styles/app.css), an area that comes onto the screen meanwhile neither. All is drawn as before once captured, or
// after a few seconds should that word never come. The attribute is set also when the screen holds none of it now, so
// the main process lifts it after every capture.
const DOTS = '-webkit-text-security';
let masked: { element: HTMLElement; value: string; priority: string }[] = [];
let unmaskTimer: ReturnType<typeof setTimeout> | undefined;

export async function maskCapture() {
  unmaskCapture();
  masked = [...document.querySelectorAll<HTMLElement>('input, textarea')]
    .filter(secret)
    .map((element) => ({
      element,
      value: element.style.getPropertyValue(DOTS),
      priority: element.style.getPropertyPriority(DOTS),
    }));
  const user_only = areaNames(userOnlyAreas());
  document.documentElement.setAttribute(CAPTURING, '');
  for (const { element } of masked) element.style.setProperty(DOTS, 'disc', 'important');
  unmaskTimer = setTimeout(unmaskCapture, 5000);
  // Answered once a frame has been drawn so: the second frame's callback comes after it. So also when the screen holds
  // nothing to leave out now, as the window still shows its last frame until it draws one, which may be from before
  // (the chat a tool has just moved away from). A window that draws nothing (covered, throttled) never gets there.
  const painted = await Promise.race([
    new Promise<boolean>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve(true))),
    ),
    pause(1000).then(() => false),
  ]);
  return { secrets: masked.length, user_only, painted };
}

export function unmaskCapture() {
  clearTimeout(unmaskTimer);
  document.documentElement.removeAttribute(CAPTURING);
  for (const { element, value, priority } of masked)
    element.style.setProperty(DOTS, value, priority);
  masked = [];
  return { shown: true };
}

// Scrolls the open document to `to` (0 the top, 1 the end) smoothly, as a demo shows an imported page.
export function scrollPanel(to: number) {
  const area = topModal()?.querySelector<HTMLElement>('.document-content');
  if (!area) return { scrolled: false };
  const top = (area.scrollHeight - area.clientHeight) * Math.min(1, Math.max(0, to));
  area.scrollTo({ top, behavior: 'smooth' });
  return { scrolled: true };
}
