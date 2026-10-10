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

// A list the backend gives a page at a time (wiki.list with limit and cursor: {documents, next_cursor, total}), as the
// screens show it: the pages loaded so far as one list, how many entries the list has in all and where the next page
// starts.
export type PagedState<T = unknown> = {
  items: readonly T[];
  // The backend's count of what the list holds in all; null until the first page came.
  total: number | null;
  // The cursor of the next page; null once the last page came.
  next: string | null;
  // A page, or the list again from its first page, is on its way; the items stay what came last meanwhile.
  loading: boolean;
  error: Error | null;
  // Whether the first page came, so an empty list is known to be empty.
  loaded: boolean;
};

type Page = { next_cursor?: string | null; total?: number } & Record<string, unknown>;

// The most entries one request may ask for (a page of the document list holds 1~500).
export const MAX_PAGE = 500;
// Lists of other parameters (each filter of a list makes one) that no component shows are kept for going back to them,
// the latest this many.
const IDLE_LISTS = 8;

const failure = (error: unknown) => (error instanceof Error ? error : new Error(String(error)));

// The entries of `next` that `items` does not hold yet, after them: a document saved while the pages were read moves to
// the top, which the cursor of the next page is past, so this keeps only a list that changed in between from showing
// one twice.
function joined<T>(items: readonly T[], next: readonly T[]): T[] {
  const id = (item: unknown) =>
    plain(item) && typeof item.id === 'string' ? (item.id as string) : undefined;
  const seen = new Set(items.map(id).filter(Boolean));
  return [...items, ...next.filter((item) => !seen.has(id(item)))];
}

// One list in pages. It loads its first page when a component first shows it, the next as the screen asks for it
// (more), and again from the first page when a tag of its method is invalidated (reload), as many entries as it shows:
// what it shows stays until all of them came, and the rows that did not change are the very objects shown before
// (keepEqual), so a change of one document redraws its row and neither empties nor scrolls the list.
export class PagedList<T = unknown> {
  state: PagedState<T> = {
    items: [],
    total: null,
    next: null,
    loading: false,
    error: null,
    loaded: false,
  };
  readonly listeners = new Set<() => void>();
  // Each reload is a generation: the pages of an earlier one, and of a page asked for before it, that come after it
  // are dropped.
  private generation = 0;
  // What is on its way, a page or a reload; one at a time.
  private busy: Promise<void> | null = null;

  constructor(
    readonly key: string,
    readonly method: Method,
    readonly params: Record<string, unknown>,
    readonly tags: readonly Tag[],
    readonly size: number,
    private send: Request,
    private field = 'documents',
  ) {}

  // The first listener after none loads the list afresh from its first page; what it showed before stays until then.
  subscribe(listener: () => void) {
    const first = this.listeners.size === 0;
    this.listeners.add(listener);
    if (first) this.reload(this.size).catch(() => {});
    return () => {
      this.listeners.delete(listener);
    };
  }

  // The next page, `count` entries, unless one is on its way (its promise then), the first has not come or the list
  // is whole.
  more(count = this.size): Promise<void> {
    if (this.busy) return this.busy;
    const { loaded, next } = this.state;
    if (!loaded || next === null) return Promise.resolve();
    const generation = this.generation;
    this.update({ loading: true });
    return this.track(
      this.page(Math.min(MAX_PAGE, count), next).then(
        (page) => {
          if (generation !== this.generation) return;
          this.update({
            items: joined(this.state.items, (page[this.field] ?? []) as T[]),
            total: page.total ?? this.state.total,
            next: page.next_cursor ?? null,
            loading: false,
            error: null,
          });
        },
        (error: unknown) => {
          if (generation === this.generation)
            this.update({ loading: false, error: failure(error) });
          throw error;
        },
      ),
    );
  }

  // The list again from its first page: `count` entries (as many as it shows, at least a page), MAX_PAGE a request.
  reload(count = Math.max(this.size, this.state.items.length)): Promise<void> {
    const generation = ++this.generation;
    this.update({ loading: true });
    const run = async () => {
      let items: T[] = [];
      let cursor: string | null = null;
      let total: number | null = null;
      do {
        const page = await this.page(Math.min(MAX_PAGE, count - items.length), cursor);
        if (generation !== this.generation) return;
        total ??= page.total ?? null;
        items = joined(items, (page[this.field] ?? []) as T[]);
        cursor = page.next_cursor ?? null;
      } while (cursor !== null && items.length < count);
      this.update({
        items: keepEqual(this.state.items, items),
        total,
        next: cursor,
        loading: false,
        error: null,
        loaded: true,
      });
    };
    return this.track(
      run().catch((error: unknown) => {
        if (generation === this.generation) this.update({ loading: false, error: failure(error) });
        throw error;
      }),
    );
  }

  // Every page still to come, MAX_PAGE entries a request, until the list holds `max`: for acting on all the entries
  // it holds, which it answers. Stops when a page brought nothing (another request took its place).
  async loadAll(max: number): Promise<readonly T[]> {
    while (this.state.next !== null && this.state.items.length < max) {
      const before = this.state.items.length;
      await this.more(Math.min(MAX_PAGE, max - before));
      if (this.state.items.length <= before) break;
    }
    return this.state.items;
  }

  private page(limit: number, cursor: string | null) {
    return this.send(this.method, {
      ...this.params,
      limit,
      ...(cursor === null ? {} : { cursor }),
    }) as Promise<Page>;
  }

  // What is on its way until it settles; whoever waits for it goes on once the list takes requests again.
  private track(request: Promise<void>) {
    const done: Promise<void> = request.finally(() => {
      if (this.busy === done) this.busy = null;
    });
    this.busy = done;
    return done;
  }

  // Tells the listeners when something of the state changed.
  private update(change: Partial<PagedState<T>>) {
    const next = { ...this.state, ...change };
    const keys = Object.keys(change) as (keyof PagedState<T>)[];
    if (keys.every((key) => Object.is(next[key], this.state[key]))) return;
    this.state = next;
    for (const listener of [...this.listeners]) listener();
  }
}

// The answers of the backend the screens share, one per method and parameters. An answer loads when a component first
// shows it (and again whenever it is shown anew after no component showed it), and again when one of its tags is
// invalidated while it is shown: by the change a screen made, a job that finished, or a notice of the main process.
// Lists in pages (list) are kept and invalidated alike.
export class QueryCache {
  private entries = new Map<string, Entry>();
  private lists = new Map<string, PagedList>();
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

  // The list of `method` with `params` in pages of `size` (wiki.list's documents): one per method, parameters and size.
  // The latest are kept at the end, so the lists dropped beyond IDLE_LISTS are those no component showed for longest.
  list<T>(method: Method, params: Record<string, unknown> = {}, size = 100): PagedList<T> {
    const key = `${QueryCache.key(method, params)} ${size}`;
    let list = this.lists.get(key);
    if (list) this.lists.delete(key);
    else list = new PagedList(key, method, params, TAGS[method] ?? [], size, this.send);
    this.lists.set(key, list);
    const idle = [...this.lists.values()].filter((item) => !item.listeners.size && item !== list);
    for (const old of idle.slice(0, Math.max(0, idle.length - IDLE_LISTS)))
      this.lists.delete(old.key);
    return list as PagedList<T>;
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
    for (const list of this.lists.values())
      if (list.listeners.size && (!wanted || list.tags.some((tag) => wanted.has(tag))))
        requests.push(list.reload());
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
