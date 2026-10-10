// The local API that AI tools (Claude Code, Codex, OpenCode, Antigravity, …) use: every method it offers, the access
// it needs, and its HTTP route. The REST router, the manuals, the OpenAPI document and the MCP tools are all made from
// these tables, so they always describe the same API.

const API_PREFIX = '/api/v1';
const DEFAULT_PORT = 47615;

// What the user lets AI tools do, from the admin page. Each level includes the ones before it.
const ACCESS_LEVELS = [
  {
    id: 'read',
    label: '조회만',
    description: '검색과 문서·할 일·대화·실행 기록 조회만 허용합니다. 아무것도 바꾸지 않습니다.',
  },
  {
    id: 'write',
    label: '조회·변경·앱 제어',
    description:
      '문서·할 일·프롬프트·예약·자동 작업을 만들고 고치고 지우며(문서 삭제는 휴지통으로, 30일 안에 복원 가능), 등록한 사이트의 JIRA·Confluence 링크 가져오기·AI 분석·질문과 앱 화면 제어를 허용합니다. 이 PC의 파일·폴더 가져오기(이를 읽는 자동 작업 포함), 등록하지 않은 사이트의 링크, 휴지통 비우기, LLM 연결·로그인 AI 분석 설정과 사이트 계정은 쓸 수 없습니다.',
  },
  {
    id: 'full',
    label: '전체',
    description:
      '이 PC의 파일·폴더 가져오기(가져온 파일의 본문을 AI 도구가 읽게 됨), 등록하지 않은 사이트의 링크, 휴지통 비우기(영구 삭제), LLM 연결·로그인 AI 분석 설정, 사이트 계정과 로그인 창, 관리 화면에서의 클릭·입력과 삭제 확인까지 모두 허용합니다.',
  },
];
const DEFAULT_ACCESS = 'read';

const SCOPES = {
  read: { label: '조회', levels: ['read', 'write', 'full'] },
  write: { label: '변경', levels: ['write', 'full'] },
  control: { label: '앱 제어', levels: ['write', 'full'] },
  // Importing this PC's files: the app reads them with the user's own rights.
  files: { label: '파일 가져오기', levels: ['full'] },
  admin: { label: '설정', levels: ['full'] },
  // Emptying the trash: nothing brings a purged document back.
  destructive: { label: '영구 삭제', levels: ['full'] },
};

const allows = (access, scope) => !!SCOPES[scope]?.levels.includes(access);

// A refusal of the API itself (who may call what), as opposed to the app's own answer to a request.
class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    Object.assign(this, { status, code });
  }
}

const forbidden = (access, scope) =>
  new ApiError(
    403,
    'forbidden',
    `허용 범위 ‘${ACCESS_LEVELS.find((level) => level.id === access)?.label ?? access}’에서는 쓸 수 없는 기능입니다(${SCOPES[scope]?.label ?? scope}). Private Agent의 관리 → AI 도구 연동에서 허용 범위를 바꿀 수 있습니다.`,
  );

// Whether the LLM has analyzed a document, as the document list shows it (src/components/analysis.tsx).
function analysisState(doc) {
  if (doc.status === 'indexing') return 'running';
  if (doc.index_mode) return 'analyzed';
  return doc.status === 'error' ? 'failed' : 'not_analyzed';
}

// Screens, tabs and admin sections the app can be sent to, as in src/components/workspace.tsx and admin.tsx.
const VIEWS = ['home', 'tasks', 'library', 'automation', 'activity', 'admin'];
const TABS = {
  tasks: ['reminders'],
  library: ['documents', 'import', 'knowledge'],
  automation: ['prompt', 'schedules', 'routines'],
  activity: ['history', 'conversations'],
};
const ADMIN_SECTIONS = ['llm', 'sites', 'templates', 'preferences', 'api', 'manual', 'about'];
const WINDOW_ACTIONS = [
  'show',
  'minimize',
  'maximize',
  'restore',
  'fullscreen',
  'set_bounds',
  'set_zoom',
];
const IMPORT_KINDS = ['office', 'rtf', 'image', 'text', 'confluence', 'jira'];
const SOURCE_KINDS = ['manual', 'rtf', 'office', 'image', 'text', 'markdown', 'confluence', 'jira'];

const method = (scope, summary, options = {}) => ({ scope, summary, ...options });

// Every method AI tools may call, by the name POST /api/v1/rpc and the MCP tool call_api take. `slow` methods wait
// for the LLM or a browser and may take minutes. The screens' own dialogs (file and folder pickers, the Wiki
// location) and the API settings are left out: only the user changes those.
const METHODS = {
  health: method('read', '연결 확인'),
  stats: method('read', '문서·키워드·관계·프롬프트 수'),
  'storage.get': method('read', 'Wiki 저장 위치'),

  'wiki.search': method('read', '문서 검색 (mode=prompt는 LLM이 고름)', { slow: true }),
  'documents.query': method(
    'read',
    '문서 목록: 검색어·출처·AI 분석 여부·키워드로 거르고 나누어 받기',
  ),
  'documents.get': method('read', '문서 하나: 본문(Markdown)·출처·키워드·관계·목차'),
  'wiki.list': method('read', '전체 문서 목록 (본문 앞 200자)'),
  'wiki.get': method('read', '문서 하나 (HTML 문서는 HTML 페이지 포함)'),
  'wiki.source_markdown': method('read', '가져온 문서의 Markdown 원문'),
  'wiki.source_html': method('read', '가져온 파일의 HTML 원문'),
  'wiki.source_status': method(
    'read',
    '가져온 파일이 지금도 문서가 가져간 그대로인지 (바뀜·없어짐)',
  ),
  'writeback.start': method(
    'files',
    '가져온 뒤 고친 문서 내용을 원본(이 PC의 Office 파일)에 반영 시작 (백그라운드)',
  ),
  'writeback.status': method('read', '원본 반영 진행 상황과 결과'),
  'writeback.cancel': method('write', '원본 반영 중지 (파일을 저장하기 전까지)'),
  'writeback.dismiss': method('write', '끝난 원본 반영 작업 지우기'),
  'writeback.backups': method(
    'files',
    '원본 파일의 백업 목록 (반영·복원하기 전의 파일, 파일마다 최근 5개)과 강제 종료로 끊긴 반영이 남긴 상태',
  ),
  'writeback.restore_backup': method(
    'files',
    '그 문서의 원본 백업 하나를 원본 파일 자리에 복원 (지금 파일은 먼저 백업)',
  ),
  'wiki.save': method('write', '문서 만들기·고치기 (본문이 바뀌면 AI 분석 결과를 지움)'),
  'wiki.compare': method(
    'read',
    'expected_revision이 맞지 않아 저장이 거절된 수정 비교: 편집을 시작한 본문(base)·고친 본문(content)·지금 문서',
  ),
  'wiki.delete': method('write', '문서를 휴지통으로 (30일 보관, wiki.restore로 복원)'),
  'wiki.delete_many': method('write', '여러 문서를 휴지통으로 (모두 또는 전혀)'),
  'wiki.trash': method('read', '휴지통의 문서 목록'),
  'wiki.restore': method('write', '휴지통의 문서 복원'),
  'wiki.purge': method('destructive', '휴지통의 문서 영구 삭제 (ids, 또는 all: true로 비우기)'),
  'wiki.analyze': method('write', '문서 하나를 LLM으로 분석 (요약·키워드·관계·목차)', {
    slow: true,
  }),
  'wiki.clear_analysis': method('write', '여러 문서의 AI 분석 결과 지우기'),
  'wiki.graph': method('read', '지식 관계도: 키워드·관계·문서'),
  'analysis.start': method('write', '여러 문서 AI 분석 시작 (백그라운드)'),
  'analysis.status': method('read', '여러 문서 AI 분석 진행 상황'),
  'analysis.cancel': method('write', '여러 문서 AI 분석 중지'),
  'analysis.dismiss': method('write', '끝난 AI 분석 작업 지우기'),

  'assistant.ask': method('write', '문서를 근거로 LLM이 답변 (대화로 저장)', { slow: true }),
  'assistant.conversations': method('read', '최근 대화 50개'),
  'assistant.history': method('read', '대화의 메시지와 근거'),
  'assistant.delete': method('write', '대화 삭제'),
  'briefing.get': method('read', '오늘의 브리핑 자료 (할 일·알림·최근 문서)'),
  'briefing.generate': method('read', 'LLM이 쓰는 오늘의 브리핑', { slow: true }),

  'tasks.query': method('read', '할 일 목록 (상태로 거르기)'),
  'tasks.list': method('read', '전체 할 일 목록'),
  'tasks.save': method('write', '할 일 만들기·고치기'),
  'tasks.save_many': method('write', '할 일 여러 개 저장 (최대 20개)'),
  'tasks.delete': method('write', '할 일 삭제'),
  'tasks.extract': method('read', '글이나 문서에서 할 일 후보 추출 (저장하지 않음)', {
    slow: true,
  }),
  'tasks.snooze': method('write', '할 일 알림 미루기'),
  'reminders.due': method('read', '지금 알릴 할 일'),
  'reminders.ack': method('write', '알림 확인 처리'),
  'reminders.status': method('read', '시스템 알림 지원 여부'),

  'prompts.list': method('read', '프롬프트 템플릿 목록'),
  'prompts.save': method('write', '프롬프트 템플릿 만들기·고치기'),
  'prompts.delete': method('write', '프롬프트 템플릿 삭제'),
  'prompts.run': method('write', '프롬프트 실행 (실행 기록에 저장)', { slow: true }),
  'runs.list': method('read', '실행 기록 목록'),
  'runs.get': method('read', '실행 기록 하나 (입력·출력)'),
  'runs.delete': method('write', '실행 기록 삭제'),
  'runs.stats': method('read', '기간별 실행 통계'),
  'runs.active': method('read', '지금 실행 중인 프롬프트'),

  'schedules.list': method('read', '예약 실행 목록'),
  'schedules.save': method('write', '예약 만들기·고치기'),
  'schedules.toggle': method('write', '예약 켜기·끄기'),
  'schedules.delete': method('write', '예약 삭제'),
  'schedules.run': method('write', '예약을 지금 한 번 실행', { slow: true }),

  'routines.catalog': method(
    'read',
    '자동 작업 종류: 종류마다 입력 항목·기본 주기·필요한 계정과 LLM',
  ),
  'routines.list': method('read', '자동 작업 목록: 설정·다음 실행·마지막 결과·만든 문서'),
  'routines.get': method('read', '자동 작업 하나'),
  'routines.runs': method(
    'read',
    '자동 작업의 실행 기록과 찾은 내용 (id가 없으면 모든 자동 작업의 최근 실행)',
  ),
  'routines.save': method(
    'write',
    '자동 작업 만들기·고치기 (이 PC의 파일을 읽는 작업은 전체 허용 범위에서만)',
  ),
  'routines.toggle': method('write', '자동 작업 켜기·끄기'),
  'routines.delete': method('write', '자동 작업 삭제'),
  'routines.run': method(
    'write',
    '자동 작업을 지금 한 번 실행 (백그라운드, routines.runs로 결과 확인)',
  ),
  'routines.cancel': method('write', '실행 중이거나 실행을 기다리는 자동 작업 중지'),

  'import.start': method(
    'write',
    '링크나 파일·폴더 경로를 알맞은 가져오기로 시작. 이미 가져온 경로·링크는 그 문서를 업데이트 (파일·폴더 경로는 전체 허용 범위에서만)',
  ),
  'import.status': method('read', '모든 가져오기 작업과 AI 분석 작업의 진행 상황'),
  'import.capabilities': method('read', 'Office·RTF·이미지·텍스트 가져오기를 쓸 수 있는지'),
  'import.cancel': method('write', '가져오기 작업 중지'),
  'import.dismiss': method('write', '끝난 가져오기 작업 지우기'),
  'files.import_text': method(
    'files',
    'UTF-8 Markdown 파일 가져오기 (이미 가져온 경로는 문서를 업데이트)',
  ),
  ...Object.fromEntries(
    ['office', 'rtf', 'image', 'text'].flatMap((kind) => [
      [`${kind}.capabilities`, method('read', `${kind} 가져오기를 쓸 수 있는지`)],
      [`${kind}.start`, method('files', `${kind} 파일·폴더 가져오기 시작`)],
      [`${kind}.status`, method('read', `${kind} 가져오기 진행 상황`)],
      [`${kind}.cancel`, method('write', `${kind} 가져오기 중지`)],
      [`${kind}.dismiss`, method('write', `끝난 ${kind} 가져오기 지우기`)],
    ]),
  ),
  ...Object.fromEntries(
    ['confluence', 'jira'].flatMap((kind) => [
      [`${kind}.start`, method('write', `${kind} 링크 가져오기 시작`)],
      [`${kind}.status`, method('read', `${kind} 가져오기 진행 상황`)],
      [`${kind}.cancel`, method('write', `${kind} 가져오기 중지`)],
      [`${kind}.dismiss`, method('write', `끝난 ${kind} 가져오기 지우기`)],
    ]),
  ),

  'sites.list': method('read', '사이트 계정 목록 (비밀번호 제외)'),
  'sites.match': method('read', '링크가 쓸 사이트 계정'),
  'sites.status': method('read', '열린 로그인 창과 진행 중인 로그인 확인'),
  'sites.save': method('admin', '사이트 계정 만들기·고치기'),
  'sites.delete': method('admin', '사이트 계정 삭제'),
  'sites.check': method('admin', '저장된 계정으로 로그인 확인', { slow: true }),
  'sites.check_cancel': method('admin', '진행 중인 로그인 확인 중지'),
  'sites.login': method('admin', '로그인 창 열기'),
  'sites.login_close': method('admin', '로그인 창 닫기'),

  'settings.get': method('read', 'LLM 연결 설정 (API Key와 헤더 값 제외)'),
  'settings.update': method('admin', 'LLM 연결 설정 바꾸기'),
  'settings.test': method('read', '사용 중인 LLM 연결 테스트', { slow: true }),
  'preferences.get': method('read', '검색·대시보드 설정'),
  'preferences.save': method(
    'write',
    '검색·대시보드 설정 바꾸기 (로그인 AI 분석 web_assist는 전체 허용 범위에서만)',
  ),

  'app.status': method('read', '앱·창·현재 화면·저장소·LLM 상태'),
  'app.window': method('control', '창 보이기·최소화·최대화·크기·확대'),
  'app.navigate': method('control', '화면·탭·관리 항목으로 이동'),
  'app.search': method('control', '앱 검색창에서 검색'),
  'app.open_document': method('control', '문서 창에서 문서 열기'),
  'app.open_run': method('control', '문서 창에서 실행 기록 열기'),
  'app.new_document': method('control', '새 문서 편집 창 열기 (저장은 사용자가)'),
  'app.close_reader': method('control', '문서 창 닫기'),
  'app.read_screen': method('control', '지금 화면의 글과 누를 수 있는 항목'),
  'app.screenshot': method('control', '앱 창 화면 캡처 (PNG)'),
  'app.click': method('control', '화면의 버튼·탭·링크를 이름으로 누르기'),
  'app.fill': method('control', '화면의 입력란을 이름으로 채우기'),
  'app.notify': method('control', '앱 안내 또는 시스템 알림 보이기'),
  'app.reload': method('control', '앱 화면 새로고침'),
  'clipboard.write': method('control', '클립보드에 글 복사'),
};

const methodScope = (name) => METHODS[name]?.scope ?? null;

// Values of a method that need more than the method itself. Whether the LLM reads the sites' login
// pages and retries the sign-in (web_assist) is decided as the LLM connection is; the dashboard and the AI answers of
// the same method stay with "write".
const FIELD_SCOPES = { 'preferences.save': { web_assist: 'admin' } };

// The catalog entry of a method an AI tool calls, or the refusal: an unknown method, or one (or one of its values) the
// access level the user chose does not allow.
function authorize(name, access, params = {}) {
  const entry = typeof name === 'string' && Object.hasOwn(METHODS, name) ? METHODS[name] : null;
  if (!entry)
    throw new ApiError(
      404,
      'not_found',
      `없는 메서드입니다: ${name}. GET ${API_PREFIX}/manual에서 메서드를 확인해 주세요.`,
    );
  if (!allows(access, entry.scope)) throw forbidden(access, entry.scope);
  for (const [field, scope] of Object.entries(FIELD_SCOPES[name] ?? {}))
    if (
      params &&
      typeof params === 'object' &&
      Object.hasOwn(params, field) &&
      !allows(access, scope)
    )
      throw forbidden(access, scope);
  return entry;
}

// --- REST routes --------------------------------------------------------------------------------------------------

const string = (description, options = {}) => ({ type: 'string', description, ...options });
const integer = (description, options = {}) => ({ type: 'integer', description, ...options });
const number = (description, options = {}) => ({ type: 'number', description, ...options });
const boolean = (description, options = {}) => ({ type: 'boolean', description, ...options });
const array = (description, items = { type: 'string' }, options = {}) => ({
  type: 'array',
  items,
  description,
  ...options,
});
const object = (description, options = {}) => ({ type: 'object', description, ...options });

const ID = string('ID', { required: true });
const IDS = (max) =>
  array(
    `문서 ID 목록 (1~${max.toLocaleString('en-US')}개)`,
    { type: 'string' },
    { required: true },
  );
const TIMEZONE = integer(
  '현지 시간대: JavaScript getTimezoneOffset() 값(분). 한국은 -540. 비우면 이 PC의 시간대',
);
const DOCUMENT_BODY = {
  title: string('제목 (최대 200자)', { required: true }),
  content: string('본문 Markdown (최대 80,000자)', { required: true }),
};
const TASK_FIELDS = {
  title: string('할 일 (최대 200자)'),
  notes: string('메모 (최대 8,000자)'),
  assignee: string('담당자'),
  status: string('상태', { enum: ['open', 'done', 'paused'] }),
  due_at: string('마감: 시간대가 있는 ISO 8601 (예: 2026-10-02T18:00:00+09:00), 없으면 null'),
  remind_at: string('알림 시각: 시간대가 있는 ISO 8601, 없으면 null'),
  repeat: string('알림 반복 (반복하려면 remind_at 필요)', { enum: ['none', 'daily', 'weekly'] }),
  source_id: string('출처 문서 ID'),
};
const PROMPT_FIELDS = {
  title: string('제목 (최대 120자)', { required: true }),
  template: string('프롬프트. {{변수}} 자리는 실행할 때 채웁니다 (최대 40,000자)', {
    required: true,
  }),
  description: string('설명'),
  category: string('분류'),
  system_prompt: string('시스템 프롬프트'),
};
const SCHEDULE_FIELDS = {
  prompt_id: string('실행할 프롬프트 ID', { required: true }),
  frequency: string('반복 주기', {
    required: true,
    enum: ['once', 'hourly', 'daily', 'weekdays', 'weekly', 'monthly'],
  }),
  name: string('예약 이름 (비우면 프롬프트 제목)'),
  variables: object('프롬프트 변수 값 {"변수": "값"}'),
  run_time: string('실행 시각 HH:MM (hourly는 분만 사용)'),
  weekday: integer('weekly의 요일: 0=월요일 … 6=일요일'),
  month_day: integer('monthly의 날짜 1~31 (없는 날은 그 달의 마지막 날)'),
  run_at: string('once의 실행 일시: 시간대가 있는 ISO 8601'),
  enabled: boolean('켜기'),
  notify: boolean('끝나면 알림'),
};
const ROUTINE_FIELDS = {
  kind: string('자동 작업 종류 (routines.catalog의 kind). 만들 때만 씁니다', {
    enum: [
      'confluence_watch',
      'jira_watch',
      'jira_filter',
      'jira_project',
      'source_refresh',
      'analyze_new',
      'work_report',
    ],
  }),
  name: string('이름 (최대 80자, 비우면 종류의 이름)'),
  config: object(
    '종류의 입력 항목 {key: 값} (routines.catalog의 fields). 목록 항목(urls·paths)은 문자열 배열',
  ),
  frequency: string('반복: interval(분 간격) · daily · weekdays · weekly · monthly', {
    enum: ['interval', 'daily', 'weekdays', 'weekly', 'monthly'],
  }),
  interval: integer('interval의 간격(분)', { enum: [15, 30, 60, 120, 180, 360, 720, 1440] }),
  run_time: string('daily·weekdays·weekly·monthly의 시각 HH:MM'),
  weekday: integer('weekly의 요일: 0=월요일 … 6=일요일'),
  month_day: integer('monthly의 날짜 1~31 (없는 날은 그 달의 마지막 날)'),
  enabled: boolean('켜기'),
  notify: boolean('찾은 것이 있거나 실패하면 알림'),
};
const SITE_FIELDS = {
  kind: string('사이트 종류', {
    enum: ['jira', 'confluence'],
  }),
  name: string('이름'),
  url: string(
    '사이트 주소. 다른 프로토콜·호스트·포트나 종류로 바꾸면 같은 요청에 password가 없을 때 저장된 비밀번호를 지움',
  ),
  login_id: string('로그인 ID'),
  password: string('비밀번호 (저장만 하고 다시 보여 주지 않음)'),
  method: string(
    '로그인 방식 ID (auto, session, custom 또는 sites.list의 methods). 고른 방식을 먼저, 실패하면 SSO 링크·AD SSO(priority) 방식부터 나머지를 차례로 시도',
  ),
  custom: object(
    '사용자 지정 방식 {steps: [{action, target, value, text, optional, unless}]} (16단계까지). action: fill(텍스트 입력: value id·password·text, text는 직접 입력할 글자 200자까지) · click(버튼 클릭) · submit(로그인 버튼, 마지막에 하나, target이 없으면 Enter. 빠지면 Enter로 붙임). target: XPath(/html/…) 또는 CSS 선택자. click과 text 입력만 optional(있을 때만)·unless(이 요소가 보이면 건너뜀). 로그인 ID와 비밀번호 입력 단계가 있어야 함. 예전 형식 {before, id, next, password, submit}도 받음',
  ),
};

const route = (verb, path, name, summary, params = {}, extra = {}) => ({
  verb,
  path,
  method: name,
  summary,
  params,
  ...extra,
});

const GROUPS = [
  {
    id: 'system',
    title: '시스템',
    description: '연결 확인과 매뉴얼, 그리고 모든 기능을 메서드 이름으로 부르는 범용 호출입니다.',
    routes: [
      route(
        'GET',
        '/health',
        'health',
        '연결 확인. 토큰 없이 부를 수 있습니다.',
        {},
        { public: true },
      ),
      route(
        'GET',
        '/manual',
        null,
        'API 매뉴얼. 토큰 없이 부를 수 있습니다.',
        { format: string('응답 형식', { enum: ['json', 'markdown'], default: 'json' }) },
        { public: true, special: 'manual' },
      ),
      route('GET', '/stats', 'stats', '문서·키워드·관계·프롬프트 수'),
      route('GET', '/storage', 'storage.get', 'Wiki 저장 위치'),
      route(
        'POST',
        '/rpc',
        null,
        '모든 기능을 메서드 이름으로 호출합니다. 결과는 각 메서드의 결과입니다.',
        {
          method: string('메서드 이름 (아래 ‘전체 메서드’ 참고)', { required: true }),
          params: object('메서드에 넘길 값'),
        },
        { special: 'rpc' },
      ),
    ],
  },
  {
    id: 'search',
    title: '검색',
    description:
      '검색창과 같은 검색입니다. auto는 PC 안의 단어 검색, prompt는 LLM이 질문을 분석해 문서와 섹션을 고르는 AI 검색(최대 4번 LLM 요청)입니다.',
    routes: [
      route('GET', '/search', 'wiki.search', '문서 검색', {
        query: string('검색어 또는 질문 (최대 2,000자). "따옴표"는 그대로 찾을 문구', {
          required: true,
        }),
        mode: string(
          'auto: 검색창과 같은 단어 검색 · word: 모든 단어 포함 · keyword: 추출 키워드(쉼표로 구분) · prompt: AI 검색(느림)',
          { enum: ['auto', 'word', 'keyword', 'prompt'], default: 'auto' },
        ),
      }),
      route('POST', '/search', 'wiki.search', '문서 검색 (긴 질문은 본문으로)', {
        query: string('검색어 또는 질문 (최대 2,000자)', { required: true }),
        mode: string('검색 방식', {
          enum: ['auto', 'word', 'keyword', 'prompt'],
          default: 'auto',
        }),
      }),
    ],
  },
  {
    id: 'documents',
    title: '문서',
    description:
      'Wiki 문서는 Markdown 본문(content)을 가지며, 가져온 파일은 HTML 페이지도 함께 가집니다. 검색과 LLM은 본문을 읽습니다.',
    routes: [
      route('GET', '/documents', 'documents.query', '문서 목록 (거르기·나누어 받기)', {
        query: string('제목·요약·본문 앞부분·키워드에 들어 있는 글'),
        source: string('출처 (manual = 직접 쓴 문서)', { enum: SOURCE_KINDS }),
        analysis: string('AI 분석 여부', {
          enum: ['analyzed', 'not_analyzed', 'failed', 'running'],
        }),
        keyword: string('추출 키워드 (정확히 일치)'),
        limit: integer('최대 개수 1~500', { default: 50, minimum: 1, maximum: 500 }),
        offset: integer('건너뛸 개수', { default: 0, minimum: 0 }),
      }),
      route('POST', '/documents', 'wiki.save', '새 Markdown 문서 저장', DOCUMENT_BODY),
      route('GET', '/documents/{id}', 'documents.get', '문서 하나', {
        id: ID,
        include_html: boolean('HTML 문서의 HTML 페이지도 받기 (클 수 있음)', { default: false }),
      }),
      route(
        'PUT',
        '/documents/{id}',
        'wiki.save',
        '문서 고치기. 제목과 본문을 모두 보냅니다. 본문이 바뀌면 AI 분석 결과를 지웁니다.',
        {
          id: ID,
          ...DOCUMENT_BODY,
          expected_revision: integer(
            '읽은 문서의 revision. 그 뒤에 다른 곳에서 문서가 바뀌었으면 저장하지 않고 거절합니다',
          ),
        },
      ),
      route('DELETE', '/documents/{id}', 'wiki.delete', '문서를 휴지통으로 (30일 보관)', {
        id: ID,
      }),
      route(
        'POST',
        '/documents/delete',
        'wiki.delete_many',
        '여러 문서를 휴지통으로 (모두 또는 전혀)',
        { ids: IDS(10000) },
      ),
      route('GET', '/documents/trash', 'wiki.trash', '휴지통의 문서 목록 (최근 삭제 순)'),
      route('POST', '/documents/restore', 'wiki.restore', '휴지통의 문서 복원', {
        ids: IDS(10000),
      }),
      route(
        'POST',
        '/documents/purge',
        'wiki.purge',
        '휴지통의 문서 영구 삭제: ids, 또는 all: true로 모두 (허용 범위 ‘전체’)',
        {
          ids: array('문서 ID 목록 (1~10,000개); all이 true면 생략', { type: 'string' }),
          all: boolean('휴지통을 모두 비우기', { default: false }),
        },
      ),
      route(
        'POST',
        '/documents/clear-analysis',
        'wiki.clear_analysis',
        '여러 문서의 AI 분석 결과(요약·키워드·관계·목차) 지우기',
        { ids: IDS(10000) },
      ),
      route(
        'POST',
        '/documents/{id}/analyze',
        'wiki.analyze',
        '문서 하나를 LLM으로 바로 분석 (끝날 때까지 기다림)',
        { id: ID },
      ),
      route(
        'GET',
        '/documents/{id}/source-markdown',
        'wiki.source_markdown',
        '가져온 문서의 Markdown 원문',
        { id: ID },
      ),
      route('GET', '/documents/{id}/source-html', 'wiki.source_html', '가져온 파일의 HTML 원문', {
        id: ID,
      }),
      route(
        'GET',
        '/documents/{id}/source-status',
        'wiki.source_status',
        '가져온 파일이 지금도 문서가 가져간 그대로인지: same·changed·missing·unreadable (페이지·티켓은 unknown)',
        { id: ID },
      ),
      route(
        'POST',
        '/documents/{id}/write-back',
        'writeback.start',
        '가져온 뒤 고친 내용을 원본 파일에 반영: 바뀐 줄만 원본의 서식을 살려 쓰고 저장 (Word·Excel 셀·PowerPoint·Visio 도형 글, 원본 백업 후 숨은 Office로). 백그라운드로 실행하고 GET /write-back으로 확인',
        { id: ID },
      ),
      route('GET', '/write-back', 'writeback.status', '원본 반영 진행 상황과 결과', {
        id: string('작업 ID (없으면 마지막 작업)'),
      }),
      route(
        'POST',
        '/write-back/cancel',
        'writeback.cancel',
        '원본 반영 중지 (파일을 저장하기 전까지)',
      ),
      route(
        'GET',
        '/documents/{doc_id}/backups',
        'writeback.backups',
        '원본 파일의 백업 목록: 원본에 반영하거나 복원하기 전의 파일을 파일마다 최근 5개 (최근 것부터). interrupted는 앱이 강제로 종료되어 끊긴 반영이 남긴 파일 상태(intact·partial·unknown)',
        { doc_id: ID },
      ),
      route(
        'POST',
        '/documents/{doc_id}/backups/restore',
        'writeback.restore_backup',
        '원본 백업 하나를 원본 파일 자리에 복원 (그 문서의 파일을 백업한 것만, 지금 파일은 먼저 백업). 문서는 그대로이니 다시 읽으려면 원본에서 업데이트하세요',
        {
          doc_id: ID,
          backup: string('복원할 백업의 id (GET /documents/{doc_id}/backups)', { required: true }),
        },
      ),
      route(
        'POST',
        '/analysis',
        'analysis.start',
        '여러 문서를 차례로 AI 분석 (백그라운드, GET /analysis로 확인)',
        { ids: IDS(1000) },
      ),
      route('GET', '/analysis', 'analysis.status', 'AI 분석 작업 진행 상황'),
      route(
        'POST',
        '/analysis/cancel',
        'analysis.cancel',
        'AI 분석 작업 중지 (분석 중인 문서 다음)',
      ),
      route('POST', '/analysis/dismiss', 'analysis.dismiss', '끝난 AI 분석 작업 지우기', {
        id: string('작업 ID', { required: true }),
      }),
      route('GET', '/graph', 'wiki.graph', '지식 관계도: 키워드(최대 1,000개)·관계·문서'),
    ],
  },
  {
    id: 'assistant',
    title: '질문·대화·브리핑',
    description:
      '질문은 검색창의 AI 답변과 같습니다: AI 검색으로 고른 문서의 발췌를 근거로 LLM이 답하고, 대화로 저장합니다.',
    routes: [
      route('POST', '/ask', 'assistant.ask', '문서를 근거로 질문하기', {
        question: string('질문 (최대 2,000자)', { required: true }),
        conversation_id: string('이어서 물을 대화 ID'),
        document_ids: array('이 문서들만 근거로 쓰기 (최대 6개)'),
      }),
      route('GET', '/conversations', 'assistant.conversations', '최근 대화 50개'),
      route('GET', '/conversations/{id}', 'assistant.history', '대화의 메시지·근거·검색 과정', {
        id: ID,
      }),
      route('DELETE', '/conversations/{id}', 'assistant.delete', '대화 삭제', { id: ID }),
      route('GET', '/briefing', 'briefing.get', '오늘의 브리핑 자료', {
        timezone_offset: TIMEZONE,
      }),
      route('POST', '/briefing', 'briefing.generate', 'LLM이 쓰는 오늘의 브리핑', {
        timezone_offset: TIMEZONE,
      }),
    ],
  },
  {
    id: 'tasks',
    title: '할 일·알림',
    description: '날짜는 시간대가 있는 ISO 8601로 주고받습니다. 저장은 UTC로 합니다.',
    routes: [
      route('GET', '/tasks', 'tasks.query', '할 일 목록', {
        status: string('상태', { enum: ['open', 'done', 'paused', 'all'], default: 'all' }),
      }),
      route('POST', '/tasks', 'tasks.save', '할 일 만들기', {
        ...TASK_FIELDS,
        title: string('할 일 (최대 200자)', { required: true }),
      }),
      route('PUT', '/tasks/{id}', 'tasks.save', '할 일 고치기 (보낸 항목만 바뀜)', {
        id: ID,
        ...TASK_FIELDS,
        expected_updated_at: string('이 시각 이후 바뀌었으면 거절 (동시 수정 방지)'),
      }),
      route('DELETE', '/tasks/{id}', 'tasks.delete', '할 일 삭제', { id: ID }),
      route('POST', '/tasks/batch', 'tasks.save_many', '할 일 여러 개를 한 번에 저장', {
        tasks: array('할 일 (1~20개)', { type: 'object' }, { required: true }),
      }),
      route(
        'POST',
        '/tasks/extract',
        'tasks.extract',
        '글이나 문서에서 할 일 후보 추출 (저장하지 않음)',
        {
          content: string('추출할 글 (최대 32,000자). source_id를 주면 생략'),
          source_id: string('추출할 문서 ID'),
          timezone_offset: TIMEZONE,
        },
      ),
      route('POST', '/tasks/{id}/snooze', 'tasks.snooze', '알림 미루기', {
        id: ID,
        minutes: integer('미룰 시간(분)', { enum: [10, 60, 1440], default: 10 }),
      }),
      route('GET', '/reminders', 'reminders.due', '지금 알릴 할 일'),
      route('POST', '/reminders/{id}/ack', 'reminders.ack', '알림 확인 (반복 알림은 다음 회차로)', {
        id: ID,
        remind_at: string('확인할 알림 시각 (할 일의 remind_at)', { required: true }),
      }),
      route('GET', '/reminders/status', 'reminders.status', '시스템 알림 지원 여부'),
    ],
  },
  {
    id: 'prompts',
    title: '프롬프트·실행 기록',
    description: '프롬프트 템플릿의 {{변수}}를 채워 LLM에 보내고, 모든 실행을 기록합니다.',
    routes: [
      route('GET', '/prompts', 'prompts.list', '프롬프트 템플릿 목록'),
      route('POST', '/prompts', 'prompts.save', '프롬프트 템플릿 만들기', PROMPT_FIELDS),
      route('PUT', '/prompts/{id}', 'prompts.save', '프롬프트 템플릿 고치기 (보낸 항목만 바뀜)', {
        id: ID,
        ...PROMPT_FIELDS,
      }),
      route('DELETE', '/prompts/{id}', 'prompts.delete', '프롬프트 템플릿 삭제', { id: ID }),
      route('POST', '/prompts/{prompt_id}/run', 'prompts.run', '프롬프트 실행', {
        prompt_id: string('프롬프트 ID', { required: true }),
        variables: object('{{변수}} 값 {"변수": "값"}'),
      }),
      route('GET', '/runs', 'runs.list', '실행 기록 목록 (최근 순)', {
        limit: integer('최대 개수 1~200', { default: 50, minimum: 1, maximum: 200 }),
        prompt_id: string('프롬프트 ID'),
        schedule_id: string('예약 ID'),
        status: string('결과', { enum: ['success', 'error'] }),
        kind: string('실행 방식', { enum: ['manual', 'scheduled'] }),
        query: string('제목·입력·출력에 들어 있는 글'),
        before: string('이 시각(created_at)보다 이전 기록만'),
      }),
      route('GET', '/runs/stats', 'runs.stats', '기간별 실행 통계', {
        days: integer('기간(일)', { enum: [7, 14, 30], default: 14 }),
        prompt_id: string('프롬프트 ID'),
        timezone_offset: TIMEZONE,
      }),
      route('GET', '/runs/active', 'runs.active', '지금 실행 중인 프롬프트'),
      route('GET', '/runs/{id}', 'runs.get', '실행 기록 하나 (입력·출력)', { id: ID }),
      route('DELETE', '/runs/{id}', 'runs.delete', '실행 기록 삭제', { id: ID }),
    ],
  },
  {
    id: 'schedules',
    title: '예약 실행',
    description:
      '앱이 켜져 있는 동안 정해진 시각에 프롬프트를 실행합니다. 켜 둘 수 있는 예약은 50개입니다.',
    routes: [
      route('GET', '/schedules', 'schedules.list', '예약 목록'),
      route('POST', '/schedules', 'schedules.save', '예약 만들기', SCHEDULE_FIELDS),
      route('PUT', '/schedules/{id}', 'schedules.save', '예약 고치기 (모든 항목을 보냅니다)', {
        id: ID,
        ...SCHEDULE_FIELDS,
      }),
      route('DELETE', '/schedules/{id}', 'schedules.delete', '예약 삭제', { id: ID }),
      route('POST', '/schedules/{id}/toggle', 'schedules.toggle', '예약 켜기·끄기', {
        id: ID,
        enabled: boolean('켜기', { required: true }),
      }),
      route('POST', '/schedules/{id}/run', 'schedules.run', '예약을 지금 한 번 실행', { id: ID }),
    ],
  },
  {
    id: 'routines',
    title: '자동 작업',
    description:
      '미리 만들어 둔 작업을 골라 정한 주기마다 실행합니다: Confluence 페이지·JIRA 티켓 변경 확인, JIRA 검색 결과 알림, JIRA 프로젝트 진행 현황, 원본 최신 내용으로 업데이트(파일·폴더), 새 문서 AI 분석, 업무 보고 초안. 앱이 켜져 있는 동안 한 번에 하나씩 실행하며, JIRA·Confluence에는 사이트 계정으로 로그인합니다. 찾은 내용은 실행 기록과 Wiki 문서에 남습니다.',
    routes: [
      route('GET', '/routines/catalog', 'routines.catalog', '자동 작업 종류와 입력 항목'),
      route('GET', '/routines', 'routines.list', '자동 작업 목록'),
      route('POST', '/routines', 'routines.save', '자동 작업 만들기', {
        ...ROUTINE_FIELDS,
        kind: { ...ROUTINE_FIELDS.kind, required: true },
      }),
      route('GET', '/routines/runs', 'routines.runs', '모든 자동 작업의 최근 실행', {
        limit: integer('최대 개수 1~30', { default: 10, minimum: 1, maximum: 30 }),
      }),
      route('GET', '/routines/{id}', 'routines.get', '자동 작업 하나', { id: ID }),
      route('PUT', '/routines/{id}', 'routines.save', '자동 작업 고치기 (보낸 항목만 바뀜)', {
        id: ID,
        ...ROUTINE_FIELDS,
      }),
      route('DELETE', '/routines/{id}', 'routines.delete', '자동 작업 삭제', { id: ID }),
      route('POST', '/routines/{id}/toggle', 'routines.toggle', '자동 작업 켜기·끄기', {
        id: ID,
        enabled: boolean('켜기', { required: true }),
      }),
      route(
        'POST',
        '/routines/{id}/run',
        'routines.run',
        '지금 한 번 실행 (백그라운드). 결과는 GET /routines/{id}/runs',
        { id: ID },
      ),
      route(
        'POST',
        '/routines/{id}/cancel',
        'routines.cancel',
        '실행 중이거나 기다리는 실행 중지',
        {
          id: ID,
        },
      ),
      route('GET', '/routines/{id}/runs', 'routines.runs', '자동 작업의 실행 기록과 찾은 내용', {
        id: ID,
        limit: integer('최대 개수 1~30', { default: 10, minimum: 1, maximum: 30 }),
      }),
    ],
  },
  {
    id: 'imports',
    title: '자료 가져오기',
    description:
      '가져오기 상자와 같습니다: JIRA·Confluence 링크, Office·.rtf·이미지·.txt·.md 파일 또는 폴더의 전체 경로를 주면 알맞은 가져오기를 백그라운드로 시작합니다. 종류마다 한 번에 하나씩 진행합니다. 이미 가져온 경로·링크를 다시 가져오면 새 문서를 만들지 않고 그 문서를 원본의 바뀐 내용으로 업데이트합니다(같은 내용이면 건너뜀, 가져온 뒤 본문을 고친 문서는 replace일 때만).',
    routes: [
      route('POST', '/imports', 'import.start', '가져오기 시작', {
        source: string('JIRA 티켓·Confluence 페이지 링크, 또는 파일·폴더의 전체 경로', {
          required: true,
        }),
        analyze: boolean('가져온 뒤 LLM으로 AI 분석', { default: false }),
        recursive: boolean('폴더: 하위 폴더 포함(기본 켬) · Confluence: 하위 페이지 포함(기본 끔)'),
        folder_kind: string('폴더에서 가져올 파일 종류', {
          enum: ['office', 'rtf', 'image', 'text'],
          default: 'office',
        }),
        jira_scope: string('JIRA: 이 티켓만, 같은 프로젝트의 키워드 검색, 사용자 검색', {
          enum: ['issue', 'keyword', 'user'],
          default: 'issue',
        }),
        keyword: string('JIRA keyword 검색어'),
        user: string('JIRA user 검색 대상 (이름 또는 ID)'),
        role: string('JIRA user 검색에서 볼 역할', {
          enum: ['any', 'assignee', 'reporter'],
          default: 'any',
        }),
        doc_id: string(
          '업데이트할 문서 ID. 같은 경로·링크를 여러 번 가져온 문서가 있을 때 고릅니다 (비우면 마지막으로 가져온 문서)',
        ),
        replace: boolean('가져온 뒤 본문을 고친 문서도 원본의 내용으로 바꾸기', { default: false }),
      }),
      route('GET', '/imports', 'import.status', '모든 가져오기와 AI 분석 작업의 진행 상황'),
      route(
        'GET',
        '/imports/capabilities',
        'import.capabilities',
        'Office·RTF·이미지·텍스트 가져오기를 쓸 수 있는지',
      ),
      route('POST', '/imports/{kind}/cancel', 'import.cancel', '가져오기 중지', {
        kind: string('가져오기 종류', { required: true, enum: IMPORT_KINDS }),
      }),
      route('POST', '/imports/{kind}/dismiss', 'import.dismiss', '끝난 가져오기 작업 지우기', {
        kind: string('가져오기 종류', { required: true, enum: IMPORT_KINDS }),
        id: string('작업 ID', { required: true }),
      }),
    ],
  },
  {
    id: 'sites',
    title: '사이트 계정',
    description:
      'JIRA·Confluence 주소마다 로그인 계정과 방식을 저장합니다. 비밀번호는 OS 보안 저장소로 암호화하며 API로 돌려주지 않습니다.',
    routes: [
      route('GET', '/sites', 'sites.list', '사이트 계정과 로그인 방식 목록'),
      route('GET', '/sites/match', 'sites.match', '링크의 종류와 그 링크가 쓸 계정', {
        url: string('JIRA·Confluence 링크', { required: true }),
        kind: string(
          '이 종류의 계정으로 찾기 (JIRA 사이트·프로젝트 주소처럼 티켓 링크가 아닐 때)',
          {
            enum: ['jira', 'confluence'],
          },
        ),
      }),
      route('GET', '/sites/status', 'sites.status', '열린 로그인 창과 진행 중인 로그인 확인'),
      route('POST', '/sites', 'sites.save', '사이트 계정 만들기', SITE_FIELDS),
      route('PUT', '/sites/{id}', 'sites.save', '사이트 계정 고치기 (보낸 항목만 바뀜)', {
        id: ID,
        ...SITE_FIELDS,
        clear_password: boolean('저장된 비밀번호 지우기'),
      }),
      route('DELETE', '/sites/{id}', 'sites.delete', '사이트 계정 삭제', { id: ID }),
      route('POST', '/sites/{id}/check', 'sites.check', '저장된 계정으로 백그라운드 로그인 확인', {
        id: ID,
      }),
      route(
        'POST',
        '/sites/{id}/check/cancel',
        'sites.check_cancel',
        '진행 중인 로그인 확인 중지: 끝날 때까지 기다려 중지했는지(stopped) 알려 줌',
        { id: ID },
      ),
      route('POST', '/sites/{id}/login', 'sites.login', '사용자가 직접 로그인할 창 열기', {
        id: ID,
      }),
      route('POST', '/sites/{id}/login/close', 'sites.login_close', '로그인 창 닫기', { id: ID }),
    ],
  },
  {
    id: 'settings',
    title: '설정',
    description:
      'OpenAI·Anthropic 형식과 사용자 지정 헤더로 LLM에 연결합니다. API Key는 바꿀 수만 있고 돌려받을 수 없으며, 헤더는 이름만 돌려줍니다.',
    routes: [
      route('GET', '/settings', 'settings.get', 'LLM 연결 설정'),
      route('PUT', '/settings', 'settings.update', 'LLM 연결 설정 바꾸기 (보낸 항목만)', {
        provider: string('LLM 연결 종류 (general)', {
          enum: ['general'],
        }),
        api_format: string('요청 형식 (openai는 OpenAI 호환 서버 포함)', {
          enum: ['openai', 'anthropic'],
        }),
        base_url: string(
          'API 엔드포인트 전체 URL. 다른 서버로 바꾸면 같은 요청에 api_key가 없을 때 저장된 API Key를 지움',
        ),
        api_key: string('API Key (비우면 그대로)'),
        clear_api_key: boolean('저장된 API Key 지우기'),
        model_name: string('모델 이름'),
        headers: array(
          'custom header [{name, value}] 전체 목록. 값의 {api_key}는 API Key로, {uuid}는 요청마다 새 ID로 바뀜. value 없이 name만 보내면 저장된 값을 유지하며, base_url을 다른 서버로 바꿀 때는 모든 값을 함께 보내야 함',
          {
            type: 'object',
            properties: { name: { type: 'string' }, value: { type: 'string' } },
          },
        ),
        temperature: number('Temperature 0~2 (Anthropic 형식에는 보내지 않음)'),
        timeout: integer('응답 제한 시간(초) 5~300, 기본 300'),
        ca_bundle: string('CA 인증서(PEM) 경로: 사설·자체 서명 인증서를 쓰는 서버일 때'),
      }),
      route('POST', '/settings/test', 'settings.test', '사용 중인 LLM 연결 테스트'),
      route('GET', '/preferences', 'preferences.get', '검색·대시보드 설정'),
      route('PUT', '/preferences', 'preferences.save', '검색·대시보드 설정 바꾸기', {
        ai_answer: boolean('검색할 때 AI 답변 만들기'),
        web_assist: boolean(
          '로그인·접속 실패 시 AI 분석: LLM이 로그인 화면을 찾아 다시 시도하거나 다시 접속하고 원인을 알려 줌. 바꾸기는 전체 허용 범위에서만',
        ),
        widgets: array(
          '대시보드 위젯 배치 [{id, type, view, size: s|m|l|xl, height: auto|s|m|l}]: type은 내용(tasks·documents 등), view는 형식(list·stats·chart 등), id는 배치 안에서 하나. null이면 기본 템플릿',
          { type: 'object' },
          { nullable: true },
        ),
      }),
    ],
  },
  {
    id: 'app',
    title: '앱 제어',
    description:
      '사용자가 보고 있는 데스크톱 앱을 다룹니다. 화면을 바꾸는 요청은 사용자의 작업을 방해하지 않도록 필요할 때만 쓰세요.',
    routes: [
      route('GET', '/app', 'app.status', '앱·창·현재 화면·저장소·LLM 상태'),
      route('POST', '/app/window', 'app.window', '창 다루기', {
        action: string('동작', { required: true, enum: WINDOW_ACTIONS }),
        width: integer('set_bounds: 너비 (최소 1060)'),
        height: integer('set_bounds: 높이 (최소 720)'),
        x: integer('set_bounds: 화면 왼쪽에서의 위치'),
        y: integer('set_bounds: 화면 위쪽에서의 위치'),
        zoom: number('set_zoom: 확대 비율 0.5~2'),
      }),
      route('POST', '/app/navigate', 'app.navigate', '화면 이동', {
        view: string('화면', { required: true, enum: VIEWS }),
        tab: string(
          '탭 — library: documents·import·knowledge · automation: prompt·schedules·routines · activity: history·conversations · tasks: reminders',
        ),
        section: string('view=admin의 관리 항목', { enum: ADMIN_SECTIONS }),
      }),
      route('POST', '/app/search', 'app.search', '앱 검색창에서 검색 (AI 답변 설정을 따름)', {
        query: string('검색어 또는 질문', { required: true }),
      }),
      route('POST', '/app/open-document', 'app.open_document', '문서 창에서 문서 열기', {
        id: ID,
        terms: array('본문에서 표시할 검색어'),
      }),
      route('POST', '/app/open-run', 'app.open_run', '문서 창에서 실행 기록 열기', { id: ID }),
      route(
        'POST',
        '/app/new-document',
        'app.new_document',
        '내용을 채운 새 문서 편집 창 열기 (저장은 사용자가 누름)',
        { title: string('제목'), content: string('본문 Markdown') },
      ),
      route('POST', '/app/close-reader', 'app.close_reader', '문서 창 닫기'),
      route('GET', '/app/screen', 'app.read_screen', '지금 화면의 글과 누를 수 있는 항목'),
      route(
        'GET',
        '/app/screenshot',
        'app.screenshot',
        '앱 창 캡처. 기본은 PNG 이미지, format=json이면 base64',
        {
          max_width: integer('최대 너비(px) 320~3840', {
            default: 1280,
            minimum: 320,
            maximum: 3840,
          }),
          format: string('응답 형식', { enum: ['png', 'json'], default: 'png' }),
        },
        { binary: true },
      ),
      route('POST', '/app/click', 'app.click', '버튼·탭·링크·스위치를 이름으로 누르기', {
        name: string('보이는 이름 또는 aria-label', { required: true }),
        role: string('항목 종류', {
          enum: ['button', 'tab', 'link', 'checkbox', 'switch', 'option'],
        }),
        exact: boolean('이름이 정확히 같은 것만', { default: false }),
        index: integer('같은 이름이 여럿일 때 몇 번째(0부터)'),
      }),
      route('POST', '/app/fill', 'app.fill', '입력란을 이름으로 채우기', {
        label: string('입력란의 이름(label·aria-label·placeholder)', { required: true }),
        value: string('넣을 값 (비우면 입력란을 지움)'),
        submit: boolean('채운 뒤 그 양식을 제출', { default: false }),
      }),
      route('POST', '/app/notify', 'app.notify', '앱 안내 또는 시스템 알림 보이기', {
        message: string('알릴 글 (최대 500자)', { required: true }),
        system: boolean('시스템 알림으로 보이기', { default: false }),
        error: boolean('오류로 보이기', { default: false }),
      }),
      route('POST', '/app/reload', 'app.reload', '앱 화면 새로고침'),
      route('POST', '/app/clipboard', 'clipboard.write', '클립보드에 글 복사', {
        text: string('복사할 글', { required: true }),
      }),
    ],
  },
];

const ROUTES = GROUPS.flatMap((group) =>
  group.routes.map((item) => ({ ...item, group: group.id })),
);

const segments = (value) => value.split('/').filter(Boolean);

// The route of a request below API_PREFIX and its path parameters. Literal segments win over parameters, so
// /runs/stats is not taken as the run "stats".
function matchRoute(verb, pathname) {
  const parts = segments(pathname);
  let best = null;
  let allowed = [];
  for (const item of ROUTES) {
    const pattern = segments(item.path);
    if (pattern.length !== parts.length) continue;
    const values = {};
    let literal = 0;
    const fits = pattern.every((piece, index) => {
      if (piece.startsWith('{')) {
        values[piece.slice(1, -1)] = parts[index];
        return true;
      }
      literal++;
      return piece === parts[index];
    });
    if (!fits) continue;
    if (item.verb !== verb) {
      allowed.push(item.verb);
      continue;
    }
    if (!best || literal > best.literal) best = { route: item, params: values, literal };
  }
  if (best) return { route: best.route, params: best.params };
  return { route: null, allowed: [...new Set(allowed)] };
}

module.exports = {
  API_PREFIX,
  DEFAULT_PORT,
  ACCESS_LEVELS,
  DEFAULT_ACCESS,
  SCOPES,
  METHODS,
  FIELD_SCOPES,
  GROUPS,
  ROUTES,
  VIEWS,
  TABS,
  ADMIN_SECTIONS,
  WINDOW_ACTIONS,
  IMPORT_KINDS,
  SOURCE_KINDS,
  ApiError,
  allows,
  forbidden,
  analysisState,
  authorize,
  methodScope,
  matchRoute,
};
