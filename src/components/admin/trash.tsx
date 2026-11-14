import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import {
  Bot,
  CalendarClock,
  ChevronsDown,
  Globe2,
  History,
  ListChecks,
  LoaderCircle,
  MessageSquareText,
  MessagesSquare,
  Repeat2,
  Trash2,
  Undo2,
  type LucideIcon,
} from 'lucide-react';
import {
  api,
  date,
  type TrashEntries,
  type TrashEntry,
  type TrashKind,
  type TrashRestored,
} from '@/lib/api';
import type { Tag } from '@/lib/query';
import { rove } from '../roving';
import { useActions, useQuery, useStores } from '../state';
import { useWindowing } from '../windowing';
import Confirm from '../confirm';

// Each kind of entry as the screens name it, and as the object of a sentence.
const KINDS: Record<TrashKind, { label: string; object: string; icon: LucideIcon }> = {
  conversation: { label: '대화', object: '대화를', icon: MessagesSquare },
  run: { label: '실행 기록', object: '실행 기록을', icon: History },
  task: { label: '할 일', object: '할 일을', icon: ListChecks },
  prompt: { label: '프롬프트 템플릿', object: '프롬프트 템플릿을', icon: MessageSquareText },
  schedule: { label: '예약', object: '예약을', icon: CalendarClock },
  routine: { label: '자동 작업', object: '자동 작업을', icon: Repeat2 },
  chat: { label: '채팅', object: '채팅을', icon: Bot },
  web_capture: { label: '웹 기록', object: '웹 기록을', icon: Globe2 },
};
// An entry of a kind this version does not know (a later version deleted it) is listed all the same.
const OTHER = { label: '항목', object: '항목을', icon: Trash2 };
const kindOf = (kind: string) => KINDS[kind as TrashKind] ?? OTHER;
// What a restore changes on the other screens; the trash's own list carries every one of them (query.ts TAGS).
const CHANGED: readonly Tag[] = ['prompts', 'runs', 'routines', 'tasks', 'conversations'];
// A page of the list holds this many entries, as many again coming as the list is scrolled.
const PAGE = 100;
// A row of the list is taken to be this tall until it is measured (src/components/windowing.ts).
const ROW_HEIGHT = 54;
// The most entries one choice may hold (모두 선택 across the pages), and one request may name (backend MAX_ENTRIES).
const MAX_SELECTED = 10_000;
const MAX_REQUEST = 1_000;
// The smallest page of the whole list, for how many entries there are of each kind and how long they wait.
const COUNTS = { limit: 1 };

// `items` in runs of `size`, for requests that name at most that many.
function slices<T>(items: readonly T[], size: number) {
  const found: T[][] = [];
  for (let start = 0; start < items.length; start += size)
    found.push(items.slice(start, start + size));
  return found;
}

// The trash's list in pages, of every kind or one (trash.list with limit and cursor, QueryCache.list), as the library
// loads the documents (useDocumentPages): the entries loaded so far, how many the list holds in all (total), the next
// page as the list is scrolled (more) and all of them (loadAll). It loads when it is first shown and again from its
// first page when a delete or a restore changed it (its tags), showing what it had meanwhile; the list of another kind
// shows the last one's entries until its first page has come.
function useTrashPages(kind: string) {
  const { queries } = useStores();
  const list = useMemo(
    () => queries.list<TrashEntry>('trash.list', kind ? { kind } : {}, PAGE, 'items'),
    [queries, kind],
  );
  const subscribe = useCallback((listener: () => void) => list.subscribe(listener), [list]);
  const read = () => list.state;
  const state = useSyncExternalStore(subscribe, read, read);
  // The last list that had come, kept from drawing to drawing as React has it (a state set while drawing).
  const [shown, setShown] = useState(state);
  if (shown !== state && (state.loaded || !shown.loaded)) setShown(state);
  const more = useCallback(() => list.more(), [list]);
  const reload = useCallback(() => list.reload(), [list]);
  const loadAll = useCallback((max: number) => list.loadAll(max), [list]);
  const view = state.loaded ? state : { ...shown, loading: true, error: state.error };
  return { ...view, current: state.loaded, more, reload, loadAll };
}

// The trash of what is deleted beside documents (trash.list): conversations, prompt runs, tasks, prompt templates,
// schedules and automatic routines, the chat cleared and the web records deleted wait here to be restored, or to be
// deleted for good by the user, one or many at a time. The list loads a page at a time and draws only the rows near
// what it shows, so a trash of thousands (the runs the retention policy moves here among them) stays quick; it shows
// one kind or all. The documents' trash stays with the document list.
//
// It is the user's alone (data-user-only="trash"): what the chat and the web records put here is no AI tool's to
// restore or delete, as the chat and the web records are not, so the local API's screen control presses and types
// nothing here, and neither reads nor captures it, at any access level (app-control.ts). A tool has trash.list,
// trash.restore and trash.purge, which leave those entries out (electron/trash.cjs).
export default function TrashSection() {
  const { notify, invalidate } = useActions();
  const counts = useQuery<TrashEntries>('trash.list', COUNTS);
  const [kind, setKind] = useState('');
  const list = useTrashPages(kind);
  const { items, next, loading, more, loadAll } = list;
  const [busy, setBusy] = useState('');
  const refresh = () => Promise.all([counts.reload(), list.reload()]);
  async function run(key: string, action: () => Promise<void>) {
    setBusy(key);
    try {
      await action();
    } catch (failure) {
      notify((failure as Error).message, true);
      // The entry may be gone, or come back another way: the list says what is there now.
      await refresh().catch(() => {});
    } finally {
      setBusy('');
    }
  }

  // Entries chosen for the actions on several, on any page loaded; the actions take those the list shows.
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const anchor = useRef<string | null>(null);
  const chosen = useMemo(() => items.filter((entry) => selected.has(entry.id)), [items, selected]);
  const select = (entries: readonly TrashEntry[], on: boolean) =>
    setSelected((current) => {
      const changed = new Set(current);
      for (const entry of entries) {
        if (on) changed.add(entry.id);
        else changed.delete(entry.id);
      }
      return changed;
    });
  // Shift-click chooses every entry between the last one clicked and this one, across the pages loaded.
  function toggle(entry: TrashEntry, on: boolean, range: boolean) {
    const first = range ? items.findIndex((item) => item.id === anchor.current) : -1;
    const last = items.indexOf(entry);
    select(first < 0 ? [entry] : items.slice(Math.min(first, last), Math.max(first, last) + 1), on);
    anchor.current = entry.id;
  }
  // Every entry of the list, the pages still to come loaded first, for 모두 선택 across all of them.
  const [selectingAll, setSelectingAll] = useState(false);
  async function selectEvery() {
    setSelectingAll(true);
    try {
      select(await loadAll(MAX_SELECTED), true);
    } catch {
      // The list says why it could not load.
    } finally {
      setSelectingAll(false);
    }
  }

  const restore = (entry: TrashEntry) =>
    run(entry.id, async () => {
      const { restored, notes } = await api<TrashRestored>('trash.restore', { ids: [entry.id] });
      await invalidate(CHANGED).catch(() => {});
      if (restored) notify(`‘${entry.title}’ ${kindOf(entry.kind).object} 복원했습니다.`);
      else notify('휴지통에 없는 항목입니다. 이미 복원했거나 영구 삭제했습니다.', true);
      // What did not come back as it was, such as a schedule restored turned off, stays until it is read.
      for (const note of notes) notify(note, false, true);
    });
  // The chosen entries, MAX_REQUEST a request: each request restores all of its entries or none, and one refused
  // ends the rest, after saying how many came back before it.
  const restoreChosen = (entries: readonly TrashEntry[]) =>
    run('chosen', async () => {
      let restored = 0;
      const notes: string[] = [];
      try {
        for (const part of slices(entries, MAX_REQUEST)) {
          const answer = await api<TrashRestored>('trash.restore', {
            ids: part.map((entry) => entry.id),
          });
          restored += answer.restored;
          notes.push(...answer.notes);
        }
      } catch (failure) {
        if (restored) notify(`${restored.toLocaleString()}개는 복원했습니다.`);
        throw failure;
      } finally {
        setSelected(new Set());
        await invalidate(CHANGED).catch(() => {});
        for (const note of notes) notify(note, false, true);
      }
      if (restored) notify(`${restored.toLocaleString()}개를 복원했습니다.`);
      else notify('휴지통에 없는 항목입니다. 이미 복원했거나 영구 삭제했습니다.', true);
    });
  const purgeChosen = (entries: readonly TrashEntry[]) =>
    run('chosen', async () => {
      let purged = 0;
      try {
        for (const part of slices(entries, MAX_REQUEST))
          purged += (
            await api<{ purged: number }>('trash.purge', { ids: part.map((entry) => entry.id) })
          ).purged;
      } catch (failure) {
        if (purged) notify(`${purged.toLocaleString()}개는 영구 삭제했습니다.`);
        throw failure;
      } finally {
        setSelected(new Set());
        await refresh().catch(() => {});
      }
      notify(`${purged.toLocaleString()}개를 영구 삭제했습니다.`);
    });

  // Only the rows near what the list box shows are drawn (useWindowing), and the row that last had the focus wherever it
  // is: the wheel may scroll it far out of sight while one of its controls has the focus, which it keeps.
  const keys = useMemo(() => items.map((entry) => entry.id), [items]);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const windowing = useWindowing(keys, ROW_HEIGHT, keys.indexOf(focusedKey ?? ''));
  const { box } = windowing;

  // The next page comes as the end of the list nears what the box shows, and with 더 보기.
  const [sentinel, setSentinel] = useState<HTMLLIElement | null>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    if (!sentinel || !box) {
      setNear(false);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => setNear(entries.some((entry) => entry.isIntersecting)),
      { root: box, rootMargin: '0px 0px 320px 0px' },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [sentinel, box]);
  const currentList = list.current;
  const failed = !!list.error;
  useEffect(() => {
    if (near && currentList && !loading && !failed && next !== null) void more().catch(() => {});
  }, [near, currentList, loading, failed, next, more]);
  // The list of another kind starts at its top.
  useLayoutEffect(() => {
    if (box) box.scrollTop = 0;
  }, [box, kind]);

  const days = counts.data?.days ?? 30;
  const lead = (
    <p className="section-lead">
      지운 대화·실행 기록·할 일·프롬프트 템플릿·예약·자동 작업, 비운 채팅과 지운 웹 기록은 {days}일
      동안 여기에 있다가 자동으로 지워집니다. 보관 정책이 정리한 오래된 실행 기록과 대화도 여기로
      옵니다. 복원하면 함께 지워진 메시지·예약·실행 기록도 돌아오고(웹 기록은 스크린샷과 함께),
      예약과 자동 작업은 휴지통에 있던 동안 지난 실행을 건너뛰고 다음 차례부터 실행합니다. 지운
      문서는 지식 보관함 → 문서의 휴지통에 있습니다.
    </p>
  );
  const error = counts.error ?? list.error;
  if (!counts.data || !list.loaded)
    return (
      <div className="trash-section" data-user-only="trash">
        {lead}
        {error ? (
          <p className="inline-error" role="alert">
            {error.message}
            <button
              type="button"
              className="text-button"
              onClick={() => void refresh().catch(() => {})}
            >
              다시 시도
            </button>
          </p>
        ) : (
          <p className="muted">휴지통을 불러오는 중입니다.</p>
        )}
      </div>
    );

  const all = counts.data.total;
  const byKind = counts.data.kinds;
  // The kinds the trash holds, in the screens' order and then those of a later version, and the one chosen though it
  // has none left.
  const offered = [
    ...Object.keys(KINDS).filter((id) => byKind[id] || id === kind),
    ...Object.keys(byKind).filter((id) => !(id in KINDS) && byKind[id]),
  ];
  const total = list.total ?? 0;
  const shownAll = items.length > 0 && chosen.length === items.length;
  return (
    <div className="trash-section" data-user-only="trash">
      {lead}
      {error && (
        <p className="inline-error" role="alert">
          {error.message}
        </p>
      )}
      {all > 0 && (
        <div className="segmented trash-kinds" role="radiogroup" aria-label="종류">
          {['', ...offered].map((id) => (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={kind === id}
              tabIndex={kind === id ? 0 : -1}
              className={kind === id ? 'active' : ''}
              onKeyDown={rove}
              onClick={() => setKind(id)}
            >
              {id ? kindOf(id).label : '전체'}{' '}
              <span className="count">{(id ? (byKind[id] ?? 0) : all).toLocaleString()}</span>
            </button>
          ))}
        </div>
      )}
      {all > 0 ? (
        <>
          <p className="trash-head">
            <span>
              {kind
                ? `휴지통의 ${all.toLocaleString()}개 중 ${kindOf(kind).label} ${total.toLocaleString()}개입니다.`
                : `휴지통에 ${all.toLocaleString()}개가 있습니다.`}
            </span>
            <Confirm
              label="휴지통 비우기"
              icon={<Trash2 size={14} />}
              question={`휴지통의 ${all.toLocaleString()}개를 모두 영구 삭제할까요? 되돌릴 수 없습니다.`}
              disabled={!!busy}
              onConfirm={() =>
                run('all', async () => {
                  const { purged } = await api<{ purged: number }>('trash.purge', { all: true });
                  setSelected(new Set());
                  await refresh();
                  notify(`휴지통의 ${purged.toLocaleString()}개를 영구 삭제했습니다.`);
                })
              }
            />
          </p>
          {items.length > 0 && (
            <div className="document-bulk" role="group" aria-label="선택한 항목 작업">
              <label className="check" title="지금 목록에 불러온 항목을 모두 선택합니다">
                <input
                  type="checkbox"
                  checked={shownAll}
                  ref={(input) => {
                    if (input) input.indeterminate = !!chosen.length && !shownAll;
                  }}
                  onChange={(event) => select(items, event.target.checked)}
                />
                모두 선택
              </label>
              {shownAll && next !== null && (
                <button
                  type="button"
                  className="text-button"
                  disabled={selectingAll}
                  title="아직 불러오지 않은 쪽까지 불러와 목록의 항목을 모두 선택합니다"
                  onClick={() => void selectEvery()}
                >
                  {selectingAll && <LoaderCircle className="spin" size={13} />}
                  {total.toLocaleString()}개 모두 선택
                </button>
              )}
              {chosen.length > 0 && (
                <>
                  <strong className="selection-count" aria-live="polite">
                    {chosen.length.toLocaleString()}개 선택
                  </strong>
                  <button
                    type="button"
                    className="button secondary small"
                    disabled={!!busy}
                    onClick={() => void restoreChosen(chosen)}
                  >
                    <Undo2 size={14} />
                    복원
                  </button>
                  <Confirm
                    label="영구 삭제"
                    icon={<Trash2 size={14} />}
                    question={`선택한 ${chosen.length.toLocaleString()}개를 영구 삭제할까요? 되돌릴 수 없습니다.`}
                    disabled={!!busy}
                    onConfirm={() => purgeChosen(chosen)}
                  />
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => setSelected(new Set())}
                  >
                    선택 해제
                  </button>
                </>
              )}
            </div>
          )}
          {!items.length && !loading && (
            <p className="muted">휴지통에 {kindOf(kind).label} 항목이 없습니다.</p>
          )}
          <ul
            ref={windowing.ref}
            className="trash-entries"
            aria-label="휴지통"
            aria-busy={loading}
            hidden={!items.length}
            onFocus={(event) =>
              setFocusedKey(
                (event.target as HTMLElement)
                  .closest('[data-window-key]')
                  ?.getAttribute('data-window-key') ?? null,
              )
            }
          >
            {windowing.parts.map((part, place) => {
              if ('gap' in part)
                return (
                  <li
                    key={`gap-${place}`}
                    aria-hidden="true"
                    className="window-spacer"
                    style={{ height: part.gap }}
                  />
                );
              const index = part.index;
              const entry = items[index];
              const meta = kindOf(entry.kind);
              const Icon = meta.icon;
              const name = entry.title || meta.label;
              const checked = selected.has(entry.id);
              return (
                <li
                  key={entry.id}
                  className={checked ? 'selected' : undefined}
                  data-window-key={entry.id}
                  aria-posinset={index + 1}
                  aria-setsize={next === null ? items.length : -1}
                >
                  <input
                    type="checkbox"
                    className="document-check"
                    aria-label={`${name} 선택`}
                    checked={checked}
                    // React reports a checkbox change with the click that made it, so Shift can be read.
                    onChange={(event) =>
                      toggle(
                        entry,
                        event.target.checked,
                        !!(event.nativeEvent as MouseEvent).shiftKey,
                      )
                    }
                  />
                  <Icon size={15} aria-hidden="true" />
                  <span className="document-title">
                    <strong>{entry.title}</strong>
                    <small>{[meta.label, entry.detail].filter(Boolean).join(' · ')}</small>
                  </span>
                  <time dateTime={entry.deleted_at}>{date(entry.deleted_at)}</time>
                  <span className="trash-actions">
                    <button
                      type="button"
                      className="button secondary small"
                      aria-label={`${name} 복원`}
                      disabled={!!busy}
                      onClick={() => void restore(entry)}
                    >
                      <Undo2 size={14} />
                      복원
                    </button>
                    <Confirm
                      label="영구 삭제"
                      icon={<Trash2 size={14} />}
                      iconOnly
                      name={`${name} 영구 삭제`}
                      question="영구 삭제할까요? 되돌릴 수 없습니다."
                      disabled={!!busy}
                      onConfirm={() =>
                        run(entry.id, async () => {
                          await api('trash.purge', { ids: [entry.id] });
                          await refresh();
                          notify(`‘${entry.title}’ ${meta.object} 영구 삭제했습니다.`);
                        })
                      }
                    />
                  </span>
                </li>
              );
            })}
            {next !== null && items.length > 0 && (
              <li ref={setSentinel} className="list-more">
                <button
                  type="button"
                  className="text-button"
                  disabled={loading}
                  onClick={() => void more().catch(() => {})}
                >
                  {loading ? (
                    <LoaderCircle className="spin" size={13} />
                  ) : (
                    <ChevronsDown size={13} />
                  )}
                  {loading ? '불러오는 중' : '더 보기'}
                </button>
                <small>
                  {items.length.toLocaleString()} / {total.toLocaleString()}
                </small>
              </li>
            )}
          </ul>
        </>
      ) : (
        <p className="muted">휴지통이 비어 있습니다.</p>
      )}
    </div>
  );
}
