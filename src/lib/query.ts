import type { Method } from './api';

// The data the screens show, by what changes it: a change invalidates its tags, and only the answers that carry one of
// them are asked again. Finishing an analysis thus reloads the documents and what is made of them (the graph, the
// trash, the counts), not the runs, the routines or the conversations. The runs are those of the prompt templates, by
// hand or on a schedule; the routines are the automatic routines with their own runs.
export type Tag = 'documents' | 'prompts' | 'runs' | 'routines' | 'tasks' | 'conversations';

// What each answer depends on. A schedule shows its template's title and its last run; the tasks and today's briefing
// the titles of the documents they came from.
export const TAGS: Partial<Record<Method, readonly Tag[]>> = {
  'wiki.list': ['documents'],
  'wiki.graph': ['documents'],
  'wiki.trash': ['documents'],
  stats: ['documents', 'prompts'],
  'prompts.list': ['prompts'],
  'runs.list': ['runs'],
  'runs.stats': ['runs'],
  'schedules.list': ['runs', 'prompts'],
  'routines.list': ['routines'],
  'routines.runs': ['routines'],
  'tasks.list': ['tasks', 'documents'],
  'briefing.get': ['tasks', 'documents'],
  'reminders.status': ['tasks'],
  'assistant.conversations': ['conversations'],
};

// An answer as the screens see it: the last data that came (kept while it loads again, so that a reload draws nothing
// until something changed) and why the last request failed.
export type QueryState<T = unknown> = {
  data: T | undefined;
  error: Error | null;
};

export type Entry = {
  key: string;
  method: Method;
  params: Record<string, unknown>;
  tags: readonly Tag[];
  state: QueryState;
  listeners: Set<() => void>;
  // The number of the last request asked and of the last whose answer is shown: the backend answers side by side, so
  // an earlier request's answer that comes after a later one's is dropped.
  asked: number;
  shown: number;
  request: Promise<unknown> | null;
};

type Request = (method: Method, params: Record<string, unknown>) => Promise<unknown>;

const plain = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype;

// `next` with every part that equals the same part of `previous` replaced by that part: an answer that did not change
// is the very object shown before, so nothing draws again, and one that did keeps the objects of its unchanged parts,
// so a list draws again only the rows whose item changed. Items with an id are matched by it, as a list's order may
// change (a document analyzed moves to the top).
export function keepEqual<T>(previous: unknown, next: T): T {
  if (Object.is(previous, next)) return previous as T;
  if (Array.isArray(previous) && Array.isArray(next)) {
    const byId = new Map(
      previous.flatMap((item) =>
        plain(item) && typeof item.id === 'string' ? [[item.id, item]] : [],
      ),
    );
    let same = previous.length === next.length;
    const merged = next.map((item, index) => {
      const match =
        plain(item) && typeof item.id === 'string' ? byId.get(item.id) : previous[index];
      const kept = keepEqual(match, item);
      if (kept !== previous[index]) same = false;
      return kept;
    });
    return (same ? previous : merged) as T;
  }
  if (plain(previous) && plain(next)) {
    const keys = Object.keys(next);
    let same = keys.length === Object.keys(previous).length;
    const merged: Record<string, unknown> = {};
    for (const key of keys) {
      merged[key] = keepEqual(previous[key], next[key]);
      if (merged[key] !== previous[key] || !(key in previous)) same = false;
    }
    return (same ? previous : merged) as T;
  }
  return next;
}

// The same text for the same parameters, whatever order their keys were written in.
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  if (value && typeof value === 'object')
    return `{${Object.keys(value)
      .filter((key) => (value as Record<string, unknown>)[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stable((value as Record<string, unknown>)[key])}`)
      .join(',')}}`;
  return JSON.stringify(value) ?? 'null';
}

// The answers of the backend the screens share, one per method and parameters. An answer loads when a component first
// shows it (and again whenever it is shown anew after no component showed it), and again when one of its tags is
// invalidated while it is shown: by the change a screen made, a job that finished, or a notice of the main process.
export class QueryCache {
  private entries = new Map<string, Entry>();
  private watchers = new Set<(tags: ReadonlySet<Tag> | null) => void>();

  constructor(private send: Request) {}

  static key(method: Method, params: Record<string, unknown> = {}) {
    return `${method} ${stable(params)}`;
  }

  entry(method: Method, params: Record<string, unknown> = {}): Entry {
    const key = QueryCache.key(method, params);
    let entry = this.entries.get(key);
    if (!entry) {
      entry = {
        key,
        method,
        params,
        tags: TAGS[method] ?? [],
        state: { data: undefined, error: null },
        listeners: new Set(),
        asked: 0,
        shown: 0,
        request: null,
      };
      this.entries.set(key, entry);
    }
    return entry;
  }

  // Listens to an entry. The first listener after none loads it afresh, unless a request is under way or the listener
  // only reads what something else loads (`load` false).
  subscribe(entry: Entry, listener: () => void, load = true) {
    const first = entry.listeners.size === 0;
    entry.listeners.add(listener);
    if (load && first && !entry.request) this.fetch(entry).catch(() => {});
    return () => {
      entry.listeners.delete(listener);
    };
  }

  // Asks for the entry again; the answer replaces what is shown unless a later request's answer came first.
  fetch(entry: Entry): Promise<unknown> {
    const number = ++entry.asked;
    const request = this.send(entry.method, entry.params).then(
      (data) => {
        if (number > entry.shown) {
          entry.shown = number;
          this.update(entry, { data: keepEqual(entry.state.data, data), error: null });
        }
        return data;
      },
      (error: unknown) => {
        if (number > entry.shown) {
          entry.shown = number;
          this.update(entry, {
            data: entry.state.data,
            error: error instanceof Error ? error : new Error(String(error)),
          });
        }
        throw error;
      },
    );
    entry.request = request;
    const settle = () => {
      if (entry.request === request) entry.request = null;
    };
    request.then(settle, settle);
    return request;
  }

  // The answer a request under way will give, or a new request's.
  load(entry: Entry) {
    return entry.request ?? this.fetch(entry);
  }

  // Data that came another way (an event of the main process), shown as the latest answer.
  set(method: Method, params: Record<string, unknown>, data: unknown) {
    const entry = this.entry(method, params);
    entry.shown = ++entry.asked;
    this.update(entry, { data: keepEqual(entry.state.data, data), error: null });
  }

  // Asks again for the shown answers that carry one of `tags` (all of them for null); the others load when they are
  // shown next. Settles once those answers came, failing as the first failed one did.
  invalidate(tags: readonly Tag[] | null): Promise<void> {
    const wanted = tags && new Set(tags);
    const requests: Promise<unknown>[] = [];
    for (const entry of this.entries.values())
      if (entry.listeners.size && (!wanted || entry.tags.some((tag) => wanted.has(tag))))
        requests.push(this.fetch(entry));
    for (const watcher of [...this.watchers]) watcher(wanted);
    return Promise.all(requests).then(() => undefined);
  }

  // Hears every invalidation, with its tags (null: everything), for screens that load in their own way.
  watch(watcher: (tags: ReadonlySet<Tag> | null) => void) {
    this.watchers.add(watcher);
    return () => {
      this.watchers.delete(watcher);
    };
  }

  // Tells the listeners only when the data or the failure changed.
  private update(entry: Entry, state: QueryState) {
    if (state.data === entry.state.data && state.error === entry.state.error) return;
    entry.state = state;
    for (const listener of [...entry.listeners]) listener();
  }
}
