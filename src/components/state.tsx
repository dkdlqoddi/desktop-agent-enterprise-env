'use client';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from 'react';
import {
  api,
  type JobsSnapshot,
  type Method,
  type Preferences,
  type Prompt,
  type Settings,
  type Stats,
  type WikiDocument,
} from '@/lib/api';
import { QueryCache, type QueryState, type Tag } from '@/lib/query';
import { createStore, useStore, type Store } from '@/lib/store';
import type { AdminSection, SiteDraft } from './admin';
import { JobsFeed, NO_JOBS, type ImportJobs, type JobsState } from './import-jobs';
import type { Focus, ReaderTarget } from './reader';
import type { SearchSession } from './search-panel';
import type { ToastAction, ToastMessage } from './toast';
import type { WorkspaceView } from './workspace';

// The app's state, in slices that change apart: where the user is (session), the LLM settings and the user's
// preferences (config), the background jobs (jobs), the backend's answers the screens share (queries, by method and
// parameters, reloaded by tag), the search box's session (SearchContext) and the actions, which never change. Each
// component reads what it shows through a selector hook and draws again only when that changed: a job's progress
// draws the import box and the labels whose state it changed, not the whole app.

export type Session = {
  view: WorkspaceView;
  workspaceTab: string;
  // The admin page's section and the site account to fill in there; `n` counts the drafts, each drawing it afresh.
  admin: { section: AdminSection; draft?: SiteDraft; n: number };
  // What the document window shows, if it is open.
  reader: ReaderTarget | null;
  // The automatic routine to show with its results (a notification was clicked), counted so the same one shows again.
  routineFocus: { id: string; n: number } | null;
  storageDirectory: string;
};

export type Config = { settings: Settings; preferences: Preferences };

export type Actions = {
  navigate: (view: WorkspaceView, tab?: string) => void;
  // Reloads the documents and the templates, and what is made of them; settles once the shown answers came.
  refresh: () => Promise<void>;
  // The runs changed (one was started, ended or deleted, a schedule was changed).
  bumpRuns: () => void;
  invalidate: (tags: readonly Tag[] | null) => Promise<void>;
  // notice: a caution the user should read, which stays until closed as an error does.
  notify: (message: string, error?: boolean, notice?: boolean) => void;
  // A message with a way back, e.g. 되돌리기 after documents went to the trash.
  undoable: (message: string, action: ToastAction) => void;
  savePreferences: (patch: Partial<Preferences>) => Promise<void>;
  reloadPreferences: () => Promise<void>;
  setSettings: (settings: Settings) => void;
  ask: (query: string) => void;
  // A keyword searches every document, whatever the next question is scoped to; false when an answer still being
  // written keeps it from starting (the user is told).
  searchKeyword: (keyword: string) => boolean;
  // Points the search box at one document: the next question answers from it alone.
  askAbout: (doc: { id: string; title: string }) => void;
  openDocument: (id: string, focus?: Focus | null) => void;
  newDocument: (initial?: { title: string; content: string }) => void;
  openRun: (id: string, terms?: string[]) => void;
  closeReader: () => void;
  openAdmin: (section?: AdminSection, draft?: SiteDraft) => void;
  setAdminSection: (section: AdminSection) => void;
  showRoutine: (id: string) => void;
  // Reads the jobs again after one was started, stopped or cleared; its progress then comes by itself.
  watchJobs: () => void;
};

export type AppStores = {
  session: Store<Session>;
  config: Store<Config>;
  jobs: Store<JobsState>;
  queries: QueryCache;
  feed: JobsFeed;
  actions: Actions;
  // The search box's input, which Ctrl+K and 이 문서에 질문 focus.
  omnibox: { current: HTMLInputElement | null };
  // The search session and the toast queue belong to components of their own (app.tsx); the actions reach them here.
  search: { current: SearchSession | null };
  attachToasts: (push: (toast: ToastMessage) => void) => () => void;
  // Loads the document and template lists and the counts, and keeps them loaded: every screen may show them.
  loadLibrary: () => Promise<void>;
};

export const INITIAL_SETTINGS: Settings = {
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
const INITIAL_PREFERENCES: Preferences = { widgets: null, ai_answer: true, web_assist: false };
const LIBRARY: Method[] = ['prompts.list', 'wiki.list', 'stats'];

export function createAppStores(): AppStores {
  const session = createStore<Session>({
    view: 'home',
    workspaceTab: '',
    admin: { section: 'llm', n: 0 },
    reader: null,
    routineFocus: null,
    storageDirectory: '',
  });
  const config = createStore<Config>({
    settings: INITIAL_SETTINGS,
    preferences: INITIAL_PREFERENCES,
  });
  const jobs = createStore<JobsState>(NO_JOBS);
  const queries = new QueryCache((method, params) => api(method, params));
  const search: AppStores['search'] = { current: null };
  const omnibox: AppStores['omnibox'] = { current: null };
  const patch = (change: Partial<Session>) => session.set((current) => ({ ...current, ...change }));
  const invalidate = (tags: readonly Tag[] | null) => queries.invalidate(tags);

  // Messages wait for the toast queue while it is not there yet.
  let push: ((toast: ToastMessage) => void) | null = null;
  const waiting: ToastMessage[] = [];
  const toast = (message: ToastMessage) => (push ? push(message) : waiting.push(message));
  const attachToasts = (next: (toast: ToastMessage) => void) => {
    push = next;
    waiting.splice(0).forEach(next);
    return () => {
      if (push === next) push = null;
    };
  };

  // The backend answers requests side by side, so saves wait for each other and the last change is the one kept; only
  // the last save's answer replaces what the screen shows, as an earlier one would undo the changes made since. Reading
  // the preferences again (an AI tool changed them) takes its turn the same way.
  const saves = { queue: Promise.resolve() as Promise<unknown>, count: 0 };
  const setPreferences = (preferences: Preferences) =>
    config.set((current) => ({ ...current, preferences }));
  async function preferencesRequest(
    send: () => Promise<Preferences>,
    change?: Partial<Preferences>,
  ): Promise<void> {
    const count = ++saves.count;
    if (change)
      config.set((current) => ({ ...current, preferences: { ...current.preferences, ...change } }));
    const done = saves.queue.catch(() => {}).then(send);
    saves.queue = done;
    try {
      const value = await done;
      if (count === saves.count) setPreferences(value);
    } catch (error) {
      // A change that was not saved does not stay on screen: the last one failing shows what is saved.
      if (change && count === saves.count)
        void preferencesRequest(() => api<Preferences>('preferences.get')).catch(() => {});
      throw error;
    }
  }

  const feed = new JobsFeed({
    store: jobs,
    request: () => api<JobsSnapshot>('jobs.status'),
    // An import finished, a batch of one, or the analysis moved on: the documents and what is made of them reload.
    changed: () => void invalidate(['documents']).catch(() => {}),
    routines: (routines) => queries.set('routines.list', {}, routines),
  });

  const navigate = (view: WorkspaceView, tab = '') => {
    patch({ view, workspaceTab: tab });
    window.scrollTo({ top: 0, behavior: 'instant' });
  };
  const notify = (message: string, error = false, notice = false) =>
    toast({ message, error, notice });
  const actions: Actions = {
    navigate,
    refresh: () => invalidate(['documents', 'prompts']),
    bumpRuns: () => void invalidate(['runs']).catch(() => {}),
    invalidate,
    notify,
    undoable: (message, action) => toast({ message, error: false, action }),
    savePreferences: (change) =>
      preferencesRequest(() => api<Preferences>('preferences.save', change), change),
    reloadPreferences: () => preferencesRequest(() => api<Preferences>('preferences.get')),
    setSettings: (settings) => config.set((current) => ({ ...current, settings })),
    ask: (query) => {
      patch({ view: 'home' });
      search.current?.submit(query, false);
    },
    searchKeyword: (keyword) => {
      if (search.current?.state?.asking) {
        notify(
          'AI가 앞의 질문에 답하고 있어 키워드로 찾지 않았습니다. 답변이 끝난 뒤 다시 눌러 주세요.',
        );
        return false;
      }
      patch({ view: 'home' });
      search.current?.submit(keyword, false, true);
      return true;
    },
    askAbout: (doc) => {
      search.current?.setScope({ id: doc.id, title: doc.title });
      patch({ view: 'home', reader: null });
      setTimeout(() => omnibox.current?.focus());
    },
    openDocument: (id, focus = null) => patch({ reader: { kind: 'document', id, focus } }),
    newDocument: (initial) => patch({ reader: { kind: 'new', ...initial } }),
    openRun: (id, terms) => patch({ reader: { kind: 'run', id, terms } }),
    closeReader: () => patch({ reader: null }),
    // The admin page shows the section asked for, keeping what is being typed in it; a new site account to fill in
    // (`draft`) starts the page afresh.
    openAdmin: (section = 'llm', draft) => {
      session.set((current) => ({
        ...current,
        view: 'admin',
        admin: { section, draft, n: draft ? current.admin.n + 1 : current.admin.n },
      }));
      window.scrollTo({ top: 0, behavior: 'instant' });
    },
    setAdminSection: (section) =>
      session.set((current) => ({ ...current, admin: { ...current.admin, section } })),
    showRoutine: (id) => {
      navigate('automation', 'routines');
      session.set((current) => ({
        ...current,
        routineFocus: { id, n: (current.routineFocus?.n ?? 0) + 1 },
      }));
    },
    watchJobs: () => void feed.refresh(),
  };

  // The lists stay loaded while the app runs (a listener that never leaves), so a change reloads them wherever the
  // user is and a screen that shows them never waits for them.
  let kept = false;
  const loadLibrary = async () => {
    const entries = LIBRARY.map((method) => queries.entry(method));
    if (!kept) {
      kept = true;
      for (const entry of entries) queries.subscribe(entry, () => {});
      await Promise.all(entries.map((entry) => queries.load(entry)));
    } else await Promise.all(entries.map((entry) => queries.fetch(entry)));
  };

  return {
    session,
    config,
    jobs,
    queries,
    feed,
    actions,
    omnibox,
    search,
    attachToasts,
    loadLibrary,
  };
}

const StateContext = createContext<AppStores | null>(null);
export const StateProvider = StateContext.Provider;
// The search box's session, which changes as a question is asked and answered.
export const SearchContext = createContext<SearchSession | null>(null);

export function useStores() {
  const stores = useContext(StateContext);
  if (!stores) throw new Error('The app state is only available inside <App>.');
  return stores;
}

export const useSession = <T,>(select: (session: Session) => T) =>
  useStore(useStores().session, select);
export const useView = () => useSession((session) => session.view);
export const useWorkspaceTab = () => useSession((session) => session.workspaceTab);
export const useRoutineFocus = () => useSession((session) => session.routineFocus);
export const useStorageDirectory = () => useSession((session) => session.storageDirectory);

export const useConfig = <T,>(select: (config: Config) => T) =>
  useStore(useStores().config, select);
export const useSettings = () => useConfig((config) => config.settings);
export const useConfigured = () => useConfig((config) => config.settings.configured);
export const usePreferences = () => useConfig((config) => config.preferences);

export const useJobs = <T,>(select: (jobs: JobsState) => T) => useStore(useStores().jobs, select);

// The import box's jobs, with the read that follows a job started, stopped or cleared.
export function useImports() {
  const jobs = useJobs((state) => state.imports);
  const { watchJobs } = useActions();
  return useMemo(() => ({ jobs, watch: watchJobs }), [jobs, watchJobs]);
}

// The AI analysis of the documents chosen in the document list, and the documents it has still to reach.
export function useAnalysis() {
  const job = useJobs((state) => state.analysis);
  const queued = useJobs((state) => state.queued);
  const { watchJobs } = useActions();
  return useMemo(() => ({ job, queued, watch: watchJobs }), [job, queued, watchJobs]);
}

export const useActions = () => useStores().actions;
export const useNotify = () => useStores().actions.notify;

export function useSearch() {
  const search = useContext(SearchContext);
  if (!search) throw new Error('The search session is only available inside <App>.');
  return search;
}

const IDLE: QueryState = { data: undefined, error: null };

// One answer of the backend, shared by every component that shows it: loaded when the first of them appears, again
// when one of its tags is invalidated (src/lib/query.ts TAGS), and on reload(). null parameters load nothing yet;
// `load` false only reads an answer the app loads itself (the lists of loadLibrary).
export function useQuery<T>(
  method: Method,
  params: Record<string, unknown> | null = {},
  { load = true }: { load?: boolean } = {},
) {
  const { queries } = useStores();
  const key = params && QueryCache.key(method, params);
  // The entry follows the method and the parameters' content (key), not the object they come in.
  const entry = useMemo(() => (params ? queries.entry(method, params) : null), [queries, key]);
  const subscribe = useCallback(
    (listener: () => void) => (entry ? queries.subscribe(entry, listener, load) : () => {}),
    [queries, entry, load],
  );
  const read = () => entry?.state ?? IDLE;
  const state = useSyncExternalStore(subscribe, read, read) as QueryState<T>;
  const reload = useCallback(
    () => (entry ? (queries.fetch(entry) as Promise<T>) : Promise.resolve(undefined)),
    [queries, entry],
  );
  return { ...state, reload };
}

// A count that grows whenever one of `tags` is invalidated, for screens that load in their own way (pages of runs,
// several answers at once).
export function useInvalidated(tags: readonly Tag[]) {
  const { queries } = useStores();
  const [count, setCount] = useState(0);
  const key = tags.join();
  useEffect(
    () =>
      queries.watch((changed) => {
        if (!changed || key.split(',').some((tag) => changed.has(tag as Tag)))
          setCount((value) => value + 1);
      }),
    [queries, key],
  );
  return count;
}

const NONE: never[] = [];
const NO_STATS: Stats = { prompts: 0, documents: 0, keywords: 0, relations: 0 };
// The lists every screen may show, which the app loads as it starts and keeps loaded (AppStores.loadLibrary): a
// component that shows them asks for nothing, also not before the Wiki is known.
const LOADED = { load: false };
export const useDocuments = () => useQuery<WikiDocument[]>('wiki.list', {}, LOADED).data ?? NONE;
export const usePrompts = () => useQuery<Prompt[]>('prompts.list', {}, LOADED).data ?? NONE;
export const useStats = () => useQuery<Stats>('stats', {}, LOADED).data ?? NO_STATS;

// Everything at once, as the screens read it before the state was split: a component that uses it draws again with
// any change of any slice. Kept for code written against it; the screens use the hooks above.
export type AppState = Actions &
  Omit<Session, 'admin' | 'reader'> & {
    settings: Settings;
    configured: boolean;
    preferences: Preferences;
    documents: WikiDocument[];
    prompts: Prompt[];
    stats: Stats;
    // Grow when the documents, or the runs, changed.
    version: number;
    runsVersion: number;
    search: SearchSession;
    imports: { jobs: ImportJobs; watch: () => void };
    analysis: ReturnType<typeof useAnalysis>;
  };

export function useApp(): AppState {
  const actions = useActions();
  const { view, workspaceTab, routineFocus, storageDirectory } = useSession((session) => session);
  const { settings, preferences } = useConfig((config) => config);
  return {
    ...actions,
    view,
    workspaceTab,
    routineFocus,
    storageDirectory,
    settings,
    configured: settings.configured,
    preferences,
    documents: useDocuments(),
    prompts: usePrompts(),
    stats: useStats(),
    version: useInvalidated(['documents', 'prompts', 'tasks']),
    runsVersion: useInvalidated(['runs', 'routines']),
    search: useSearch(),
    imports: useImports(),
    analysis: useAnalysis(),
  };
}
