'use client';
import { useCallback, useEffect, useLayoutEffect, useState } from 'react';
import { flushSync } from 'react-dom';
import { Bell, ChevronRight, Info, LoaderCircle, ShieldCheck } from 'lucide-react';
import { api, type Preferences, type Settings, type StorageInfo } from '@/lib/api';
import Admin, { type AdminSection } from './admin';
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
import { useJobsFeed } from './import-jobs';
import Reader from './reader';
import SearchPanel, { Omnibox, useSearchSession } from './search-panel';
import {
  createAppStores,
  SearchContext,
  StateProvider,
  useActions,
  useConfig,
  useSearch,
  useSession,
  useStores,
  type AppStores,
} from './state';
import StorageSetup from './storage-setup';
import Toasts, { useToasts } from './toast';
import WorkspacePage, { WorkspaceSidebar, WORKSPACE_VIEWS, type WorkspaceView } from './workspace';
import { AssistantData } from './widgets/assistant-data';

// The state's hooks live in ./state; code written before the state was split imports useApp from here.
export { useApp, type AppState } from './state';

// The app holds only what its start needs (where the Wiki is, whether it is loading); everything else lives in the
// state's slices (./state), each owned by the component that shows it, so that a job's progress, a toast or an answer
// being written draws its own part of the screen again and not the whole app.
export default function App() {
  const [stores] = useState(createAppStores);
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [desktop, setDesktop] = useState(true);
  const [error, setError] = useState('');
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
      stores.session.set((current) => ({ ...current, storageDirectory: location.directory || '' }));
      if (!location.initialized) return;
      await stores.loadLibrary();
      const [settings, preferences] = await Promise.all([
        api<Settings>('settings.get'),
        api<Preferences>('preferences.get'),
      ]);
      stores.config.set({ settings, preferences });
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setLoading(false);
    }
  }, [stores]);
  useEffect(() => {
    void initialize();
  }, [initialize]);
  useJobsFeed(stores.feed, !!storage?.initialized && !loading);
  useMainProcess(stores);
  if (storage && !storage.initialized) return <StorageSetup info={storage} onReady={initialize} />;
  return (
    <StateProvider value={stores}>
      <SearchProvider>
        <AssistantData enabled={!loading && (!desktop || !!storage?.initialized)}>
          <Shell desktop={desktop} error={error} loading={loading} onRetry={initialize} />
        </AssistantData>
        <ReaderHost />
        <ToastHost />
      </SearchProvider>
    </StateProvider>
  );
}

// What the main process tells the screen: notifications clicked, scheduled runs and routines ended, import cautions,
// changes an AI tool made through the local API, and the local API's commands to this screen.
function useMainProcess(stores: AppStores) {
  const { session, actions } = stores;
  useEffect(
    () =>
      window.privateAgent?.onAssistantOpen?.(() => {
        // A reminder notification was clicked: show the dashboard with its reminders.
        session.set((current) => ({ ...current, view: 'home' }));
        void actions.invalidate(['tasks']).catch(() => {});
      }),
    [session, actions],
  );
  useEffect(
    () =>
      window.privateAgent?.onScheduleEvent?.((event) => {
        if (event.type === 'open') {
          // A document being written stays: the run waits in the history.
          if (editingDocument())
            actions.notify(
              '작성 중인 문서가 있어 실행 기록을 열지 않았습니다. 저장하거나 닫은 뒤 실행 기록에서 열어 주세요.',
            );
          else actions.openRun(event.run_id);
          return;
        }
        actions.bumpRuns();
        // The main process shows a system notification instead while the window is unfocused.
        if (event.notify && document.hasFocus())
          actions.notify(
            event.status === 'success'
              ? `예약 실행을 마쳤습니다 · ${event.name}`
              : `예약 실행에 실패했습니다 · ${event.name}: ${event.error}`,
            event.status === 'error',
          );
      }),
    [actions],
  );
  useEffect(
    () =>
      window.privateAgent?.onRoutineEvent?.((event) => {
        if (event.type === 'open') return actions.showRoutine(event.routine_id);
        void actions.invalidate(['routines']).catch(() => {});
        // News or a failure; the main process shows a system notification instead while the window is unfocused.
        if (event.notify && (event.changed || event.status === 'error') && document.hasFocus())
          actions.notify(
            event.status === 'error'
              ? `자동 작업이 실패했습니다 · ${event.name}: ${event.error}`
              : `자동 작업 · ${event.name}: ${event.summary}`,
            event.status === 'error',
          );
      }),
    [actions],
  );
  useEffect(
    () =>
      // An import is about to read a file its user should know about, such as a presentation that may
      // open in their own PowerPoint. The main process shows a system notification instead while the
      // window is unfocused.
      window.privateAgent?.onImportNotice?.((message) => actions.notify(message, false, true)),
    [actions],
  );
  // An AI tool changed data through the local API: everything shown loads again, the jobs it may have started are
  // read, and the settings taken.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const off = window.privateAgent?.onApiChange?.(() => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        void actions.invalidate(null).catch(() => {});
        actions.watchJobs();
        void api<Settings>('settings.get')
          .then(actions.setSettings)
          .catch(() => {});
        void actions.reloadPreferences().catch(() => {});
      }, 200);
    });
    return () => {
      clearTimeout(timer);
      off?.();
    };
  }, [actions]);
  // One command at a time; what a command changes is on the screen before it answers (flushSync), as a click's change
  // is: otherwise React draws it in a later task, which the next command (say, a fill after a move to the admin page)
  // may come before.
  useEffect(
    () =>
      window.privateAgent?.onControl?.(async (type, params) =>
        inTurn(() => flushSync(() => control(stores, type, params))),
      ),
    [stores],
  );
  // What the user changes and has not saved holds off the local API's reloads and screen changes, and closing the window.
  useEffect(() => watchDrafts(), []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        stores.omnibox.current?.focus();
        stores.omnibox.current?.select();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [stores]);
}

// The local API's commands to this screen (electron/app-control.cjs), answered with what happened. A document the user
// is writing in the panel is never replaced or closed.
function control(stores: AppStores, type: string, params: Record<string, unknown>): unknown {
  const { actions } = stores;
  if (
    ['open_document', 'open_run', 'new_document', 'close_reader'].includes(type) &&
    editingDocument()
  )
    throw new Error(
      '사용자가 문서 창에서 글을 쓰고 있어 바꾸지 않았습니다. 저장하거나 취소한 뒤 다시 시도해 주세요.',
    );
  const text = (value: unknown) => (typeof value === 'string' ? value : '');
  const { view, workspaceTab, admin, reader } = stores.session.get();
  const search = stores.search.current?.state ?? null;
  switch (type) {
    case 'state':
      return {
        view,
        tab: workspaceTab || null,
        admin_section: view === 'admin' ? admin.section : null,
        reader:
          reader &&
          (reader.kind === 'new' ? { kind: 'new' } : { kind: reader.kind, id: reader.id }),
        search: search && { query: search.query, asking: !!search.asking },
      };
    case 'navigate':
      // What the user is writing on this screen would be gone with it.
      refuseWhileWriting();
      if (params.view === 'admin')
        actions.openAdmin((text(params.section) || 'llm') as AdminSection);
      else actions.navigate(params.view as WorkspaceView, text(params.tab));
      return {
        view: params.view,
        tab: text(params.tab) || null,
        section: text(params.section) || null,
      };
    case 'search':
      refuseWhileWriting();
      if (search?.asking)
        throw new Error('앱이 이전 질문의 답변을 기다리고 있습니다. 끝난 뒤 다시 시도해 주세요.');
      actions.ask(text(params.query));
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
      actions.openDocument(text(params.id), terms.length ? { terms, sections: [] } : null);
      return { opened: text(params.id) };
    }
    case 'open_run':
      actions.openRun(text(params.id));
      return { opened: text(params.id) };
    case 'new_document':
      actions.newDocument({ title: text(params.title), content: text(params.content) });
      return { opened: 'new' };
    case 'close_reader':
      actions.closeReader();
      return { closed: true };
    case 'toast':
      actions.notify(text(params.message), params.error === true);
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
}

// The search box's session, which changes with every step of a question: only what shows it draws again.
function SearchProvider({ children }: { children: React.ReactNode }) {
  const stores = useStores();
  const configured = useConfig((config) => config.settings.configured);
  const aiAnswer = useConfig((config) => config.preferences.ai_answer);
  const search = useSearchSession({ configured, aiAnswer });
  useLayoutEffect(() => {
    stores.search.current = search;
  }, [stores, search]);
  // A conversation saved or deleted: the lists of recent conversations load again.
  const { saved } = search;
  useEffect(() => {
    if (saved) void stores.queries.invalidate(['conversations']).catch(() => {});
  }, [stores, saved]);
  return <SearchContext.Provider value={search}>{children}</SearchContext.Provider>;
}

// Errors and notices (cautions the user should read) stay until closed, successes close by themselves; a few show at
// once and the rest wait their turn (toast.tsx). The actions' messages come here.
function ToastHost() {
  const { attachToasts } = useStores();
  const { items, push, close } = useToasts();
  useLayoutEffect(() => attachToasts(push), [attachToasts, push]);
  return <Toasts items={items} onClose={close} />;
}

function ReaderHost() {
  const reader = useSession((session) => session.reader);
  const { closeReader } = useActions();
  if (!reader) return null;
  return <Reader key={JSON.stringify(reader)} target={reader} onClose={closeReader} />;
}

function LlmStatus() {
  const configured = useConfig((config) => config.settings.configured);
  const model = useConfig((config) => config.settings.active_model);
  const { openAdmin } = useActions();
  return (
    <button
      className={`llm-status${configured ? ' on' : ''}`}
      onClick={() => openAdmin('llm')}
      title="LLM 연결 설정"
    >
      <span className="dot" aria-hidden="true" />
      {configured ? model : 'LLM 연결 필요'}
    </button>
  );
}

function SearchArea() {
  const search = useSearch();
  if (!search.state) return null;
  return (
    <ErrorBoundary message="검색 결과를 표시하지 못했습니다.">
      <SearchPanel />
    </ErrorBoundary>
  );
}

function Shell({
  desktop,
  error,
  loading,
  onRetry,
}: {
  desktop: boolean;
  error: string;
  loading: boolean;
  onRetry: () => void;
}) {
  const { omnibox } = useStores();
  const view = useSession((session) => session.view);
  const admin = useSession((session) => session.admin);
  const { navigate, setAdminSection } = useActions();
  return (
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
          <LlmStatus />
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
              <span>화면 미리보기 · 문서 저장과 AI 기능은 데스크톱 앱에서 사용할 수 있어요.</span>
            </div>
          )}
          {error && desktop && (
            <div className="banner error" role="alert">
              {error}
              <button className="text-button" onClick={() => void onRetry()}>
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
              <SearchArea />
              {/* Another page is drawn afresh after a failure; on the admin page each section has a boundary of
                  its own (admin.tsx), so its tabs stay usable. */}
              <ErrorBoundary key={`${view}:${admin.n}`} message="이 화면을 표시하지 못했습니다.">
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
                      onSection={setAdminSection}
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
  );
}
