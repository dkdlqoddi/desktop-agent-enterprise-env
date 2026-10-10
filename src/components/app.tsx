'use client';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { flushSync } from 'react-dom';
import { Bell, ChevronRight, Info, LoaderCircle, ShieldCheck } from 'lucide-react';
import {
  api,
  type Preferences,
  type Prompt,
  type Settings,
  type Stats,
  type StorageInfo,
  type WikiDocument,
} from '@/lib/api';
import Admin, { type AdminSection, type SiteDraft } from './admin';
import { useAnalysisJob } from './analysis';
import {
  changedForm,
  clickControl,
  editingDocument,
  fillControl,
  inTurn,
  maskSecrets,
  readScreen,
  refuseWhileWriting,
  scrollPanel,
  unmaskSecrets,
  watchDrafts,
} from './app-control';
import Dashboard from './dashboard';
import ErrorBoundary from './error-boundary';
import { useImportJobs } from './import-jobs';
import Reader, { type Focus, type ReaderTarget } from './reader';
import SearchPanel, { Omnibox, useSearchSession, type SearchSession } from './search-panel';
import StorageSetup from './storage-setup';
import Toasts, { useToasts } from './toast';
import WorkspacePage, { WorkspaceSidebar, WORKSPACE_VIEWS, type WorkspaceView } from './workspace';
import { AssistantData } from './widgets/assistant-data';

const initialSettings: Settings = {
  provider: 'general',
  api_format: 'openai',
  base_url: '',
  model_name: '',
  headers: [],
  has_api_key: false,
  key_stored: false,
  temperature: 0.5,
  timeout: 300,
  ca_bundle: '',
  configured: false,
  active_model: '',
  secure_storage: false,
  os_user: '',
  load_error: '',
};

type AppState = {
  view: WorkspaceView;
  workspaceTab: string;
  navigate: (view: WorkspaceView, tab?: string) => void;
  settings: Settings;
  setSettings: (settings: Settings) => void;
  configured: boolean;
  documents: WikiDocument[];
  prompts: Prompt[];
  stats: Stats;
  storageDirectory: string;
  // Reloads the document and prompt lists; `version` tells widgets that derive data (the knowledge
  // graph, recent documents) to load again.
  refresh: () => Promise<void>;
  version: number;
  runsVersion: number;
  bumpRuns: () => void;
  // notice: a caution the user should read, which stays until closed as an error does.
  notify: (message: string, error?: boolean, notice?: boolean) => void;
  // A message with a way back, e.g. 되돌리기 after documents went to the trash.
  undoable: (message: string, action: { label: string; run: () => Promise<void> | void }) => void;
  preferences: Preferences;
  savePreferences: (patch: Partial<Preferences>) => Promise<void>;
  search: SearchSession;
  imports: ReturnType<typeof useImportJobs>;
  // The AI analysis of documents chosen in the document list, running in the background.
  analysis: ReturnType<typeof useAnalysisJob>;
  ask: (query: string) => void;
  // A keyword searches every document, whatever the next question is scoped to; false when an answer still being
  // written keeps it from starting (the user is told).
  searchKeyword: (keyword: string) => boolean;
  // Points the search box at one document: the next question answers from it alone.
  askAbout: (doc: { id: string; title: string }) => void;
  openDocument: (id: string, focus?: Focus | null) => void;
  newDocument: (initial?: { title: string; content: string }) => void;
  openRun: (id: string, terms?: string[]) => void;
  openAdmin: (section?: AdminSection, draft?: SiteDraft) => void;
  // The automatic routine to show with its results (a notification was clicked), counted so the same one shows again.
  routineFocus: { id: string; n: number } | null;
  showRoutine: (id: string) => void;
};

const AppContext = createContext<AppState | null>(null);
export function useApp() {
  const state = useContext(AppContext);
  if (!state) throw new Error('useApp must be used inside <App>.');
  return state;
}

export default function App() {
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [desktop, setDesktop] = useState(true);
  const [error, setError] = useState('');
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [documents, setDocuments] = useState<WikiDocument[]>([]);
  const [stats, setStats] = useState<Stats>({
    prompts: 0,
    documents: 0,
    keywords: 0,
    relations: 0,
  });
  const [settings, setSettings] = useState(initialSettings);
  const [preferences, setPreferences] = useState<Preferences>({
    widgets: null,
    ai_answer: true,
    web_assist: false,
  });
  const [version, setVersion] = useState(0);
  const [runsVersion, setRunsVersion] = useState(0);
  const [view, setView] = useState<WorkspaceView>('home');
  const [workspaceTab, setWorkspaceTab] = useState('');
  const navigate = useCallback((next: WorkspaceView, tab = '') => {
    setView(next);
    setWorkspaceTab(tab);
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, []);
  const [admin, setAdmin] = useState<{ section: AdminSection; draft?: SiteDraft; n: number }>({
    section: 'llm',
    n: 0,
  });
  const [reader, setReader] = useState<ReaderTarget | null>(null);
  const [routineFocus, setRoutineFocus] = useState<{ id: string; n: number } | null>(null);
  const showRoutine = useCallback(
    (id: string) => {
      navigate('automation', 'routines');
      setRoutineFocus((current) => ({ id, n: (current?.n ?? 0) + 1 }));
    },
    [navigate],
  );
  // Errors and notices (cautions the user should read) stay until closed, successes close by themselves; a few show at
  // once and the rest wait their turn (toast.tsx).
  const toasts = useToasts();
  const { push: pushToast } = toasts;
  const omnibox = useRef<HTMLInputElement>(null);
  const notify = useCallback(
    (message: string, error = false, notice = false) => pushToast({ message, error, notice }),
    [pushToast],
  );
  const undoable = useCallback(
    (message: string, action: { label: string; run: () => Promise<void> | void }) =>
      pushToast({ message, error: false, action }),
    [pushToast],
  );
  // The backend answers requests side by side, so a reload's answer may come after a later one's: it is shown only
  // when no later reload's answer is already on screen.
  const reloads = useRef({ asked: 0, shown: 0 });
  const refresh = useCallback(async () => {
    const reload = ++reloads.current.asked;
    const [p, d, s] = await Promise.all([
      api<Prompt[]>('prompts.list'),
      api<WikiDocument[]>('wiki.list'),
      api<Stats>('stats'),
    ]);
    if (reload < reloads.current.shown) return;
    reloads.current.shown = reload;
    setPrompts(p);
    setDocuments(d);
    setStats(s);
    setVersion((value) => value + 1);
  }, []);
  const initialize = useCallback(async () => {
    setLoading(true);
    setError('');
    setDesktop(!!window.privateAgent);
    if (!window.privateAgent) {
      setLoading(false);
      return;
    }
    try {
      const location = await api<StorageInfo>('storage.get');
      setStorage(location);
      if (!location.initialized) return;
      await refresh();
      const [current, prefs] = await Promise.all([
        api<Settings>('settings.get'),
        api<Preferences>('preferences.get'),
      ]);
      setSettings(current);
      setPreferences(prefs);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setLoading(false);
    }
  }, [refresh]);
  useEffect(() => {
    void initialize();
  }, [initialize]);
  // The backend answers requests side by side, so saves wait for each other and the last change is the one kept; only
  // the last save's answer replaces what the screen shows, as an earlier one would undo the changes made since. Reading
  // the preferences again (an AI tool changed them) takes its turn the same way.
  const preferenceSaves = useRef<{ queue: Promise<unknown>; count: number }>({
    queue: Promise.resolve(),
    count: 0,
  });
  const preferencesRequest = useCallback(async function request(
    send: () => Promise<Preferences>,
    change?: Partial<Preferences>,
  ): Promise<void> {
    const saves = preferenceSaves.current;
    const count = ++saves.count;
    if (change) setPreferences((current) => ({ ...current, ...change }));
    const done = saves.queue.catch(() => {}).then(send);
    saves.queue = done;
    try {
      const value = await done;
      if (count === saves.count) setPreferences(value);
    } catch (error) {
      // A change that was not saved does not stay on screen: the last one failing shows what is saved.
      if (change && count === saves.count)
        void request(() => api<Preferences>('preferences.get')).catch(() => {});
      throw error;
    }
  }, []);
  const savePreferences = useCallback(
    (patch: Partial<Preferences>) =>
      preferencesRequest(() => api<Preferences>('preferences.save', patch), patch),
    [preferencesRequest],
  );
  const imports = useImportJobs(refresh, !!storage?.initialized && !loading);
  const analysis = useAnalysisJob(refresh, !!storage?.initialized && !loading);
  const search = useSearchSession({
    configured: settings.configured,
    aiAnswer: preferences.ai_answer,
  });
  const ask = useCallback(
    (query: string) => {
      setView('home');
      search.submit(query, false);
    },
    [search],
  );
  const searchKeyword = useCallback(
    (keyword: string) => {
      if (search.state?.asking) {
        notify(
          'AI가 앞의 질문에 답하고 있어 키워드로 찾지 않았습니다. 답변이 끝난 뒤 다시 눌러 주세요.',
        );
        return false;
      }
      setView('home');
      search.submit(keyword, false, true);
      return true;
    },
    [search, notify],
  );
  // The admin page shows the section asked for, keeping what is being typed in it; a new site account to fill in
  // (`draft`) starts the page afresh.
  const openAdmin = useCallback((section: AdminSection = 'llm', draft?: SiteDraft) => {
    setView('admin');
    window.scrollTo({ top: 0, behavior: 'instant' });
    setAdmin((current) => ({ section, draft, n: draft ? current.n + 1 : current.n }));
  }, []);
  useEffect(
    () =>
      window.privateAgent?.onAssistantOpen?.(() => {
        // A reminder notification was clicked: show the dashboard with its reminders.
        setView('home');
        setVersion((value) => value + 1);
      }),
    [],
  );
  useEffect(
    () =>
      window.privateAgent?.onScheduleEvent?.((event) => {
        if (event.type === 'open') {
          // A document being written stays: the run waits in the history.
          if (editingDocument())
            notify(
              '작성 중인 문서가 있어 실행 기록을 열지 않았습니다. 저장하거나 닫은 뒤 실행 기록에서 열어 주세요.',
            );
          else setReader({ kind: 'run', id: event.run_id });
          return;
        }
        setRunsVersion((value) => value + 1);
        // The main process shows a system notification instead while the window is unfocused.
        if (event.notify && document.hasFocus())
          notify(
            event.status === 'success'
              ? `예약 실행을 마쳤습니다 · ${event.name}`
              : `예약 실행에 실패했습니다 · ${event.name}: ${event.error}`,
            event.status === 'error',
          );
      }),
    [notify],
  );
  useEffect(
    () =>
      window.privateAgent?.onRoutineEvent?.((event) => {
        if (event.type === 'open') return showRoutine(event.routine_id);
        setRunsVersion((value) => value + 1);
        // News or a failure; the main process shows a system notification instead while the window is unfocused.
        if (event.notify && (event.changed || event.status === 'error') && document.hasFocus())
          notify(
            event.status === 'error'
              ? `자동 작업이 실패했습니다 · ${event.name}: ${event.error}`
              : `자동 작업 · ${event.name}: ${event.summary}`,
            event.status === 'error',
          );
      }),
    [notify, showRoutine],
  );
  useEffect(
    () =>
      // An import is about to read a file its user should know about, such as a presentation that may
      // open in their own PowerPoint. The main process shows a system notification instead while the
      // window is unfocused.
      window.privateAgent?.onImportNotice?.((message) =>
        pushToast({ message, error: false, notice: true }),
      ),
    [pushToast],
  );
  // An AI tool changed data through the local API: reload the lists, follow the jobs, and take the settings.
  const { watch: watchImports } = imports;
  const { watch: watchAnalysis } = analysis;
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const off = window.privateAgent?.onApiChange?.(() => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        void refresh().catch(() => {});
        setRunsVersion((value) => value + 1);
        watchImports();
        watchAnalysis();
        void api<Settings>('settings.get')
          .then(setSettings)
          .catch(() => {});
        void preferencesRequest(() => api<Preferences>('preferences.get')).catch(() => {});
      }, 200);
    });
    return () => {
      clearTimeout(timer);
      off?.();
    };
  }, [refresh, watchImports, watchAnalysis, preferencesRequest]);
  // The local API's commands to this screen (electron/app-control.cjs), answered with what happened. A document
  // the user is writing in the panel is never replaced or closed.
  const control = useRef<(type: string, params: Record<string, unknown>) => unknown>(() => null);
  control.current = (type, params) => {
    if (
      ['open_document', 'open_run', 'new_document', 'close_reader'].includes(type) &&
      editingDocument()
    )
      throw new Error(
        '사용자가 문서 창에서 글을 쓰고 있어 바꾸지 않았습니다. 저장하거나 취소한 뒤 다시 시도해 주세요.',
      );
    const text = (value: unknown) => (typeof value === 'string' ? value : '');
    switch (type) {
      case 'state':
        return {
          view,
          tab: workspaceTab || null,
          admin_section: view === 'admin' ? admin.section : null,
          reader:
            reader &&
            (reader.kind === 'new' ? { kind: 'new' } : { kind: reader.kind, id: reader.id }),
          search: search.state && { query: search.state.query, asking: !!search.state.asking },
        };
      case 'navigate':
        // What the user is writing on this screen would be gone with it.
        refuseWhileWriting();
        if (params.view === 'admin') openAdmin((text(params.section) || 'llm') as AdminSection);
        else navigate(params.view as WorkspaceView, text(params.tab));
        return {
          view: params.view,
          tab: text(params.tab) || null,
          section: text(params.section) || null,
        };
      case 'search':
        refuseWhileWriting();
        if (search.state?.asking)
          throw new Error('앱이 이전 질문의 답변을 기다리고 있습니다. 끝난 뒤 다시 시도해 주세요.');
        ask(text(params.query));
        return { searched: text(params.query) };
      case 'can_reload':
        refuseWhileWriting();
        return { ok: true };
      // Before the window closes: what the user changed and has not saved.
      case 'unsaved':
        return { unsaved: changedForm() };
      case 'mask_secrets':
        return maskSecrets();
      case 'unmask_secrets':
        return unmaskSecrets();
      case 'open_document': {
        const terms = Array.isArray(params.terms) ? (params.terms as string[]) : [];
        setReader({
          kind: 'document',
          id: text(params.id),
          focus: terms.length ? { terms, sections: [] } : null,
        });
        return { opened: text(params.id) };
      }
      case 'open_run':
        setReader({ kind: 'run', id: text(params.id) });
        return { opened: text(params.id) };
      case 'new_document':
        setReader({ kind: 'new', title: text(params.title), content: text(params.content) });
        return { opened: 'new' };
      case 'close_reader':
        setReader(null);
        return { closed: true };
      case 'toast':
        pushToast({ message: text(params.message), error: params.error === true });
        return { shown: true };
      case 'read_screen':
        return { view, reader: reader?.kind ?? null, ...readScreen() };
      case 'click':
        return clickControl(params as Parameters<typeof clickControl>[0]);
      case 'fill':
        return fillControl(params as Parameters<typeof fillControl>[0]);
      case 'scroll':
        return scrollPanel(Number(params.to) || 0);
      default:
        throw new Error(`알 수 없는 화면 명령입니다: ${type}`);
    }
  };
  // One command at a time; what a command changes is on the screen before it answers (flushSync), as a click's change
  // is: otherwise React draws it in a later task, which the next command (say, a fill after a move to the admin page)
  // may come before.
  useEffect(
    () =>
      window.privateAgent?.onControl?.(async (type, params) =>
        inTurn(() => flushSync(() => control.current(type, params))),
      ),
    [],
  );
  // What the user changes and has not saved holds off the local API's reloads and screen changes, and closing the window.
  useEffect(() => watchDrafts(), []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        omnibox.current?.focus();
        omnibox.current?.select();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  const bumpRuns = useCallback(() => setRunsVersion((value) => value + 1), []);
  const { setScope } = search;
  const askAbout = useCallback(
    (doc: { id: string; title: string }) => {
      setScope({ id: doc.id, title: doc.title });
      setView('home');
      setReader(null);
      setTimeout(() => omnibox.current?.focus());
    },
    [setScope],
  );
  const openDocument = useCallback(
    (id: string, focus: Focus | null = null) => setReader({ kind: 'document', id, focus }),
    [],
  );
  const newDocument = useCallback(
    (initial?: { title: string; content: string }) => setReader({ kind: 'new', ...initial }),
    [],
  );
  const openRun = useCallback(
    (id: string, terms?: string[]) => setReader({ kind: 'run', id, terms }),
    [],
  );
  // The same value until something in it changes: every widget reads it, and draws again only then.
  const storageDirectory = storage?.directory || '';
  const state = useMemo<AppState>(
    () => ({
      view,
      workspaceTab,
      navigate,
      settings,
      setSettings,
      configured: settings.configured,
      documents,
      prompts,
      stats,
      storageDirectory,
      refresh,
      version,
      runsVersion,
      bumpRuns,
      notify,
      undoable,
      preferences,
      savePreferences,
      search,
      imports,
      analysis,
      ask,
      searchKeyword,
      askAbout,
      openDocument,
      newDocument,
      openRun,
      openAdmin,
      routineFocus,
      showRoutine,
    }),
    [
      view,
      workspaceTab,
      navigate,
      settings,
      documents,
      prompts,
      stats,
      storageDirectory,
      refresh,
      version,
      runsVersion,
      bumpRuns,
      notify,
      undoable,
      preferences,
      savePreferences,
      search,
      imports,
      analysis,
      ask,
      searchKeyword,
      askAbout,
      openDocument,
      newDocument,
      openRun,
      openAdmin,
      routineFocus,
      showRoutine,
    ],
  );
  if (storage && !storage.initialized) return <StorageSetup info={storage} onReady={initialize} />;
  return (
    <AppContext.Provider value={state}>
      <AssistantData enabled={!loading && (!desktop || !!storage?.initialized)}>
        <div className="app">
          <a className="skip-link" href="#main-content">
            본문으로 이동
          </a>
          <WorkspaceSidebar />
          <div className="workspace-shell">
            <header className="topbar">
              <div className="breadcrumb">
                <span>워크스페이스</span>
                <ChevronRight size={13} />
                <strong>{WORKSPACE_VIEWS[view].title}</strong>
              </div>
              <Omnibox ref={omnibox} onSubmit={() => navigate('home')} />
              <button
                className={`llm-status${settings.configured ? ' on' : ''}`}
                onClick={() => openAdmin('llm')}
                title="LLM 연결 설정"
              >
                <span className="dot" aria-hidden="true" />
                {settings.configured ? settings.active_model : 'LLM 연결 필요'}
              </button>
              <button
                className="icon-button notification-button"
                aria-label="알림 보기"
                title="알림 보기"
                onClick={() => navigate('tasks', 'reminders')}
              >
                <Bell size={19} />
              </button>
            </header>
            <main className="main" id="main-content" tabIndex={-1}>
              {!desktop && (
                <div className="banner preview-banner">
                  <Info size={15} />
                  <span>
                    화면 미리보기 · 문서 저장과 AI 기능은 데스크톱 앱에서 사용할 수 있어요.
                  </span>
                </div>
              )}
              {error && desktop && (
                <div className="banner error" role="alert">
                  {error}
                  <button className="text-button" onClick={() => void initialize()}>
                    다시 시도
                  </button>
                </div>
              )}
              {loading ? (
                <div className="loading-page">
                  <LoaderCircle className="spin" size={28} />
                  <p>워크스페이스를 준비하고 있어요.</p>
                </div>
              ) : (
                <>
                  {/* A part that fails to draw shows its error and 다시 시도; the menu, the search box and the
                      document window stay. Another page draws afresh. */}
                  {search.state && (
                    <ErrorBoundary message="검색 결과를 표시하지 못했습니다.">
                      <SearchPanel />
                    </ErrorBoundary>
                  )}
                  {/* Another page is drawn afresh after a failure; on the admin page each section has a boundary of
                      its own (admin.tsx), so its tabs stay usable. */}
                  <ErrorBoundary
                    key={`${view}:${admin.n}`}
                    message="이 화면을 표시하지 못했습니다."
                  >
                    {view === 'home' ? (
                      <Dashboard />
                    ) : view === 'admin' ? (
                      <>
                        <header className="page-heading">
                          <div>
                            <p className="eyebrow">MAKE IT YOURS</p>
                            <h1>워크스페이스 설정</h1>
                            <p>{WORKSPACE_VIEWS.admin.description}</p>
                          </div>
                        </header>
                        <Admin
                          key={admin.n}
                          section={admin.section}
                          draft={admin.draft}
                          onSection={(section) => setAdmin((current) => ({ ...current, section }))}
                        />
                      </>
                    ) : (
                      <WorkspacePage key={view} view={view} />
                    )}
                  </ErrorBoundary>
                </>
              )}
              <footer className="workspace-footer">
                <span>
                  Private Agent <span className="footer-dot">·</span> 나만의 업무 공간
                </span>
                <span>
                  <ShieldCheck size={12} /> 자료는 내 PC에, AI는 내가 정한 연결로
                </span>
              </footer>
            </main>
          </div>
        </div>
      </AssistantData>
      {reader && (
        <Reader key={JSON.stringify(reader)} target={reader} onClose={() => setReader(null)} />
      )}
      <Toasts items={toasts.items} onClose={toasts.close} />
    </AppContext.Provider>
  );
}
