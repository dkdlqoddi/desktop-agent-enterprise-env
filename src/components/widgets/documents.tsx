import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowRight,
  ChevronsDown,
  Eraser,
  FileCode2,
  FileText,
  Globe,
  Image as ImageIcon,
  Layers,
  ListTree,
  LoaderCircle,
  NotepadText,
  Plus,
  Search,
  Sparkles,
  Ticket,
  Trash2,
  Undo2,
  X,
} from 'lucide-react';
import {
  api,
  date,
  localDay,
  plain,
  type Deleted,
  type DocumentAnalysis,
  type DocumentPage,
  type DocumentQuery,
  type DocumentSource,
  type Trash,
  type WikiDocument,
} from '@/lib/api';
import { AnalysisJobView, AnalysisLabel, analyzed } from '../analysis';
import Confirm from '../confirm';
import { WidgetHead } from '../dashboard';
import EmptyState from '../empty-state';
import { activeJob } from '../import-jobs';
import {
  useActions,
  useConfigured,
  useDocumentPages,
  useJobs,
  useQuery,
  useStats,
  useView,
} from '../state';
import { useWindowing } from '../windowing';

export const ICONS = {
  confluence: Globe,
  jira: Ticket,
  rtf: FileText,
  office: FileText,
  image: ImageIcon,
  text: NotepadText,
  markdown: FileCode2,
};

// The latest documents, which the dashboard's list and cards show: one small page of the document list.
export const LATEST: DocumentQuery = { limit: 8 };
const HOME_ROWS = 5;

// Where a document came from, and its AI analysis, as the library filters by them (the backend's filters).
const SOURCES: Record<DocumentSource, string> = {
  manual: '직접 작성',
  office: 'Office 문서',
  rtf: 'RTF 문서',
  image: '이미지',
  text: '텍스트',
  markdown: 'Markdown',
  confluence: 'Confluence',
  jira: 'JIRA',
};
const ANALYSIS: Record<DocumentAnalysis, string> = {
  analyzed: 'AI 분석 완료',
  not_analyzed: '분석 전',
  failed: '분석 실패',
  running: '분석 중',
};
// When the documents were last changed: the days up to today counted from the start of the first, or days chosen.
const PERIODS = {
  all: '전체 기간',
  today: '오늘',
  week: '최근 7일',
  month: '최근 30일',
  year: '최근 1년',
  custom: '직접 지정',
};
type Period = keyof typeof PERIODS;
const DAYS_BEFORE: Partial<Record<Period, number>> = { today: 0, week: 6, month: 29, year: 364 };

// A row of the library list is taken to be this tall until it is measured (src/components/windowing.ts).
const ROW_HEIGHT = 76;
// The most documents one request on the selection may name (backend MAX_SELECTED).
const MAX_SELECTED = 10_000;
const NONE: never[] = [];

type Column = 'check' | 'open';

// The start of local day `key` (YYYY-MM-DD) and `days` after it, as the backend takes a time: ISO 8601 in UTC.
function dayStart(key: string, days = 0) {
  const [year, month, day] = key.split('-').map(Number);
  return new Date(year, month - 1, day + days).toISOString();
}

// The update times a period keeps: from the start of its first day (and up to the end of the last day chosen), the
// last days counted back from `today`.
function rangeOf(period: Period, from: string, to: string, today: string): DocumentQuery {
  if (period === 'custom')
    return {
      ...(from ? { updated_from: dayStart(from) } : {}),
      ...(to ? { updated_to: dayStart(to, 1) } : {}),
    };
  const days = DAYS_BEFORE[period];
  return days === undefined ? {} : { updated_from: dayStart(today, -days) };
}

// What is typed, once the typing paused: each filter of the list is a request to the backend.
function useSettled<T>(value: T, delay: number) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

// The documents a choice in the list stands for: an import saved in parts is one entry of the list, and what is done
// to it is done to each of its parts.
const idsOf = (docs: readonly WikiDocument[]) => [
  ...new Set(
    docs.flatMap((doc) => (doc.parts?.length ? doc.parts.map((part) => part.id) : [doc.id])),
  ),
];

// Why the list shows nothing, by what it was asked for.
function emptyNote(filters: DocumentQuery) {
  if (filters.query) return '일치하는 문서가 없습니다. 다른 검색어를 입력해 보세요.';
  if (Object.keys(filters).length === 1 && filters.analysis)
    return {
      analyzed: 'AI로 분석한 문서가 없습니다. 문서를 골라 AI 분석을 실행해 보세요.',
      not_analyzed: '분석 전인 문서가 없습니다.',
      failed: '분석에 실패한 문서가 없습니다.',
      running: '지금 AI로 분석하고 있는 문서가 없습니다.',
    }[filters.analysis];
  return '조건에 맞는 문서가 없습니다. 거르는 조건을 바꿔 보세요.';
}

// The dashboard shows the latest documents; the library all of them, a page at a time, with its filters and actions.
export default function DocumentsWidget() {
  return useView() === 'library' ? <LibraryDocuments /> : <LatestDocuments />;
}

function LatestDocuments() {
  const { openDocument, newDocument, navigate } = useActions();
  const { data } = useQuery<DocumentPage>('wiki.list', LATEST);
  const documents = data?.documents.slice(0, HOME_ROWS) ?? NONE;
  // The backend's count of every document, not the rows of the page.
  const count = data?.total;
  return (
    <>
      <WidgetHead id="documents" title="문서" count={count}>
        <button className="button secondary small" onClick={() => newDocument()}>
          <Plus size={14} />새 문서
        </button>
      </WidgetHead>
      <AnalysisJobView />
      {count === 0 && <NoDocuments />}
      <ul className="document-list">
        {documents.map((doc) => (
          <DocumentRow key={doc.id} doc={doc} onOpen={openDocument} />
        ))}
      </ul>
      {!!count && count > documents.length && (
        <button className="text-button" onClick={() => navigate('library')}>
          문서 {count.toLocaleString()}개 모두 보기 <ArrowRight size={13} />
        </button>
      )}
    </>
  );
}

function NoDocuments() {
  const { navigate } = useActions();
  return (
    <EmptyState
      icon={FileText}
      title="첫 번째 지식을 담아 보세요"
      action={
        <button className="text-button" onClick={() => navigate('library', 'import')}>
          자료 가져오기 <ArrowRight size={13} />
        </button>
      }
    >
      메모를 작성하거나 파일과 링크를 가져오세요.
    </EmptyState>
  );
}

function LibraryDocuments() {
  const stats = useStats();
  const { openDocument, newDocument, refresh } = useActions();
  // The filters, each the backend's (wiki.list): what is typed, once the typing paused, finds documents by their title,
  // summary, keywords or where they came from (a folder, a file name, a JIRA key or a page address).
  const [typed, setTyped] = useState('');
  const query = useSettled(typed.trim(), 250);
  const [source, setSource] = useState<DocumentSource | ''>('');
  const [analysis, setAnalysis] = useState<DocumentAnalysis | ''>('');
  const [period, setPeriod] = useState<Period>('all');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  // The update times the period keeps, worked out as it is chosen: the last days count back from that day.
  const [updated, setUpdated] = useState<DocumentQuery>({});
  const choosePeriod = (chosen: Period, first = from, last = to) => {
    setPeriod(chosen);
    setFrom(first);
    setTo(last);
    setUpdated(rangeOf(chosen, first, last, localDay(Date.now())));
  };
  const filters = useMemo<DocumentQuery>(
    () => ({
      ...(query ? { query } : {}),
      ...(source ? { source } : {}),
      ...(analysis ? { analysis } : {}),
      ...updated,
    }),
    [query, source, analysis, updated],
  );
  const filtered = Object.keys(filters).length > 0;
  const clearFilters = () => {
    setTyped('');
    setSource('');
    setAnalysis('');
    choosePeriod('all', '', '');
  };
  const list = useDocumentPages(filters);
  const { items, total, next, loading, error, more, loadAll, reload } = list;
  // The trash, shown in place of the list; loaded again with every reload of the documents (its tag).
  const [trash, setTrash] = useState(false);
  const bin = useQuery<Trash>('wiki.trash').data ?? null;
  const binned = bin?.documents.length ?? 0;

  // Documents chosen for the bulk actions, on any page loaded; the actions take those the filters show.
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const anchor = useRef<string | null>(null);
  const chosen = useMemo(() => items.filter((doc) => selected.has(doc.id)), [items, selected]);
  const select = (docs: readonly WikiDocument[], on: boolean) =>
    setSelected((current) => {
      const next = new Set(current);
      for (const doc of docs) {
        if (on) next.add(doc.id);
        else next.delete(doc.id);
      }
      return next;
    });
  // Shift-click selects every document between the last one clicked and this one, across the pages loaded.
  function toggle(doc: WikiDocument, on: boolean, range: boolean) {
    const first = range ? items.findIndex((item) => item.id === anchor.current) : -1;
    const last = items.indexOf(doc);
    select(first < 0 ? [doc] : items.slice(Math.min(first, last), Math.max(first, last) + 1), on);
    anchor.current = doc.id;
  }
  // The rows take one toggle that stays the same, so a row draws again only when its document or its check changed.
  const latest = useRef(toggle);
  useLayoutEffect(() => {
    latest.current = toggle;
  });
  const onToggle = useCallback(
    (doc: WikiDocument, on: boolean, range: boolean) => latest.current(doc, on, range),
    [],
  );
  // Every document the filters keep, the pages still to come loaded first, for 모두 선택 across all of them.
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

  // Only the rows near what the list box shows are drawn (useWindowing).
  const keys = useMemo(() => items.map((doc) => doc.id), [items]);
  const windowing = useWindowing(keys, ROW_HEIGHT);
  const { box, start, end, reveal } = windowing;

  // The keyboard moves through the rows with the arrows, Page Up/Down, Home and End, and between a row's checkbox and
  // its document with Left and Right; only one row is in the Tab order (a roving tab stop), the one last reached, or
  // the first drawn while that one is scrolled away. Past the last row the next page comes.
  const [active, setActive] = useState<{ key: string | null; column: Column }>({
    key: null,
    column: 'open',
  });
  const reached = keys.indexOf(active.key ?? '');
  const tabStop = reached >= start && reached < end ? reached : start;
  const focusPending = useRef(false);
  const onFocusRow = useCallback(
    (key: string, column: Column) =>
      setActive((current) =>
        current.key === key && current.column === column ? current : { key, column },
      ),
    [],
  );
  function moveTo(index: number, column: Column) {
    const target = Math.min(Math.max(index, 0), items.length - 1);
    if (target < 0) return;
    focusPending.current = true;
    setActive({ key: items[target].id, column });
    reveal(target);
  }
  function onKeyDown(event: React.KeyboardEvent<HTMLUListElement>) {
    const target = event.target as HTMLElement;
    const row = target.closest<HTMLElement>('[data-index]');
    if (!row || event.altKey || event.ctrlKey || event.metaKey) return;
    const index = Number(row.dataset.index);
    const column: Column = target.dataset.column === 'check' ? 'check' : 'open';
    const steps: Record<string, number> = {
      ArrowDown: 1,
      ArrowUp: -1,
      PageDown: windowing.page,
      PageUp: -windowing.page,
    };
    let goal: number;
    if (event.key in steps) goal = index + steps[event.key];
    else if (event.key === 'Home') goal = 0;
    else if (event.key === 'End') goal = items.length - 1;
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      moveTo(index, event.key === 'ArrowLeft' ? 'check' : 'open');
      return;
    } else return;
    event.preventDefault();
    if (goal >= items.length && next !== null) void more().catch(() => {});
    moveTo(goal, column);
  }
  // The row the keyboard went to takes the focus once it is drawn (a scroll may draw it first).
  useLayoutEffect(() => {
    if (!focusPending.current || !box) return;
    const index = keys.indexOf(active.key ?? '');
    const element = box.querySelector<HTMLElement>(
      `[data-index="${index}"] [data-column="${active.column}"]`,
    );
    if (element) {
      focusPending.current = false;
      element.focus({ preventScroll: true });
    }
  });

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
  // Whether the documents shown are those of the current filters (useDocumentPages), not a ref.
  const currentList = list.current;
  useEffect(() => {
    if (near && currentList && !loading && !error && next !== null) void more().catch(() => {});
  }, [near, currentList, loading, error, next, more]);

  const shownAll = items.length > 0 && chosen.length === items.length;
  const nothing = list.loaded && list.current && !items.length;
  const tools = stats.documents > 0 || binned > 0 || filtered || items.length > 0;
  return (
    <>
      <WidgetHead id="documents" title="문서" count={stats.documents}>
        <button className="button secondary small" onClick={() => newDocument()}>
          <Plus size={14} />새 문서
        </button>
      </WidgetHead>
      {tools && (
        <div className="document-tools">
          <label className="document-filter">
            <Search size={16} />
            <input
              aria-label="보관함 문서 필터"
              placeholder="제목, 요약, 키워드, 출처로 찾기"
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
            />
            {loading && !list.current && <LoaderCircle className="spin" size={14} />}
            {total !== null && <small>{total.toLocaleString()}개</small>}
          </label>
          <label className="document-select">
            출처
            <select
              value={source}
              onChange={(event) => setSource(event.target.value as DocumentSource | '')}
            >
              <option value="">전체</option>
              {(Object.keys(SOURCES) as DocumentSource[]).map((id) => (
                <option key={id} value={id}>
                  {SOURCES[id]}
                </option>
              ))}
            </select>
          </label>
          <label className="document-select">
            AI 분석
            <select
              value={analysis}
              onChange={(event) => setAnalysis(event.target.value as DocumentAnalysis | '')}
            >
              <option value="">전체</option>
              {(Object.keys(ANALYSIS) as DocumentAnalysis[]).map((id) => (
                <option key={id} value={id}>
                  {ANALYSIS[id]}
                </option>
              ))}
            </select>
          </label>
          <label className="document-select">
            수정일
            <select value={period} onChange={(event) => choosePeriod(event.target.value as Period)}>
              {(Object.keys(PERIODS) as Period[]).map((id) => (
                <option key={id} value={id}>
                  {PERIODS[id]}
                </option>
              ))}
            </select>
          </label>
          {period === 'custom' && (
            <span className="document-range">
              <input
                type="date"
                aria-label="수정일 시작"
                value={from}
                max={to || undefined}
                onChange={(event) => choosePeriod('custom', event.target.value)}
              />
              <span aria-hidden="true">~</span>
              <input
                type="date"
                aria-label="수정일 끝"
                value={to}
                min={from || undefined}
                onChange={(event) => choosePeriod('custom', from, event.target.value)}
              />
            </span>
          )}
          {(filtered || typed) && (
            <button type="button" className="text-button" onClick={clearFilters}>
              <X size={13} />
              조건 지우기
            </button>
          )}
          <button
            type="button"
            className={`button secondary small${trash ? ' active' : ''}`}
            aria-pressed={trash}
            onClick={() => setTrash(!trash)}
          >
            <Trash2 size={14} />
            휴지통{binned ? ` ${binned}` : ''}
          </button>
        </div>
      )}
      {trash && <TrashView bin={bin} onChanged={refresh} />}
      {!trash && <AnalysisJobView />}
      {!trash && error && (
        <p className="inline-error" role="alert">
          문서 목록을 불러오지 못했습니다: {error.message}{' '}
          <button
            type="button"
            className="text-button"
            onClick={() => void reload().catch(() => {})}
          >
            다시 시도
          </button>
        </p>
      )}
      {!trash && nothing && !filtered && <NoDocuments />}
      {!trash && nothing && filtered && <p className="widget-note">{emptyNote(filters)}</p>}
      {!trash && items.length > 0 && (
        <BulkActions
          chosen={chosen}
          all={shownAll}
          onSelectAll={(on) => select(items, on)}
          onClear={() => setSelected(new Set())}
          every={
            shownAll && next !== null && total !== null
              ? { total, busy: selectingAll, select: () => void selectEvery() }
              : null
          }
        />
      )}
      <ul
        ref={windowing.ref}
        className="document-list paged"
        aria-label="문서 목록"
        aria-busy={loading}
        hidden={trash}
        onKeyDown={onKeyDown}
      >
        {windowing.before > 0 && (
          <li aria-hidden="true" className="window-spacer" style={{ height: windowing.before }} />
        )}
        {items.slice(start, end).map((doc, offset) => (
          <DocumentRow
            key={doc.id}
            doc={doc}
            index={start + offset}
            count={next === null ? items.length : -1}
            onOpen={openDocument}
            checked={selected.has(doc.id)}
            onToggle={onToggle}
            tabbable={start + offset === tabStop}
            onFocusRow={onFocusRow}
          />
        ))}
        {windowing.after > 0 && (
          <li aria-hidden="true" className="window-spacer" style={{ height: windowing.after }} />
        )}
        {next !== null && items.length > 0 && (
          <li ref={setSentinel} className="list-more">
            <button
              type="button"
              className="text-button"
              disabled={loading}
              onClick={() => void more().catch(() => {})}
            >
              {loading ? <LoaderCircle className="spin" size={13} /> : <ChevronsDown size={13} />}
              {loading ? '불러오는 중' : '더 보기'}
            </button>
            {total !== null && (
              <small>
                {items.length.toLocaleString()} / {total.toLocaleString()}
              </small>
            )}
          </li>
        )}
      </ul>
    </>
  );
}

// The trash: the deleted documents wait `days` days to be restored, or to be deleted for good by the user.
function TrashView({ bin, onChanged }: { bin: Trash | null; onChanged: () => Promise<void> }) {
  const { notify } = useActions();
  const [busy, setBusy] = useState('');
  async function run(key: string, action: () => Promise<string>) {
    setBusy(key);
    try {
      const message = await action();
      await onChanged();
      notify(message);
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy('');
    }
  }
  if (!bin) return <p className="widget-note">휴지통을 불러오는 중입니다.</p>;
  if (!bin.documents.length) return <p className="widget-note">휴지통이 비어 있습니다.</p>;
  return (
    <>
      <p className="trash-head">
        <span>
          삭제한 문서는 {bin.days}일 뒤 자동으로 지워집니다. 복원하면 AI 분석 결과와 할 일의 출처도
          돌아옵니다.
        </span>
        <Confirm
          label="휴지통 비우기"
          icon={<Trash2 size={14} />}
          question={`휴지통의 문서 ${bin.documents.length}개를 영구 삭제할까요? 되돌릴 수 없습니다.`}
          disabled={!!busy}
          onConfirm={() =>
            run('all', async () => {
              const { purged } = await api<{ purged: number }>('wiki.purge', { all: true });
              return `문서 ${purged}개를 영구 삭제했습니다.`;
            })
          }
        />
      </p>
      <ul className="document-list" aria-label="휴지통">
        {bin.documents.map((doc) => {
          const Icon = doc.source ? ICONS[doc.source.kind] : FileText;
          return (
            <li key={doc.id} className="trash-row">
              <Icon size={15} aria-hidden="true" />
              <span className="document-title">
                <strong>{doc.title}</strong>
                <small>{doc.summary || plain(doc.excerpt ?? '')}</small>
              </span>
              <time dateTime={doc.deleted_at ?? undefined}>
                {doc.deleted_at ? date(doc.deleted_at) : ''}
              </time>
              <span className="trash-actions">
                <button
                  type="button"
                  className="button secondary small"
                  disabled={!!busy}
                  onClick={() =>
                    void run(doc.id, async () => {
                      await api('wiki.restore', { ids: [doc.id] });
                      return `‘${doc.title}’ 문서를 복원했습니다.`;
                    })
                  }
                >
                  <Undo2 size={14} />
                  복원
                </button>
                <Confirm
                  label="영구 삭제"
                  icon={<Trash2 size={14} />}
                  iconOnly
                  name={`${doc.title} 영구 삭제`}
                  question="영구 삭제할까요? 되돌릴 수 없습니다."
                  disabled={!!busy}
                  onConfirm={() =>
                    run(doc.id, async () => {
                      await api('wiki.purge', { ids: [doc.id] });
                      return `‘${doc.title}’ 문서를 영구 삭제했습니다.`;
                    })
                  }
                />
              </span>
            </li>
          );
        })}
      </ul>
    </>
  );
}

// Select all loaded documents (and then every one the filters keep), and act on the chosen ones: analyze them with
// the LLM, take back their analysis or delete them. An entry that stands for an import saved in parts takes each part.
function BulkActions({
  chosen,
  all,
  onSelectAll,
  onClear,
  every,
}: {
  chosen: WikiDocument[];
  all: boolean;
  onSelectAll: (on: boolean) => void;
  onClear: () => void;
  // The documents the filters keep beyond the loaded pages, which 모두 선택 can take too.
  every: { total: number; busy: boolean; select: () => void } | null;
}) {
  const configured = useConfigured();
  const { openAdmin, notify, undoable, refresh, watchJobs } = useActions();
  const [busy, setBusy] = useState(false);
  const ids = idsOf(chosen);
  const running = useJobs((jobs) => !!jobs.analysis && activeJob(jobs.analysis.status));
  async function run(action: () => Promise<string | void>) {
    setBusy(true);
    try {
      const message = await action();
      onClear();
      if (message) notify(message);
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="document-bulk" role="group" aria-label="선택한 문서 작업">
      <label className="check" title="지금 목록에 불러온 문서를 모두 선택합니다">
        <input
          type="checkbox"
          checked={all}
          ref={(box) => {
            if (box) box.indeterminate = !!chosen.length && !all;
          }}
          onChange={(event) => onSelectAll(event.target.checked)}
        />
        모두 선택
      </label>
      {every && (
        <button
          type="button"
          className="text-button"
          disabled={every.busy}
          title="아직 불러오지 않은 쪽까지 불러와 조건에 맞는 문서를 모두 선택합니다"
          onClick={every.select}
        >
          {every.busy && <LoaderCircle className="spin" size={13} />}
          조건에 맞는 문서 {every.total.toLocaleString()}개 모두 선택
        </button>
      )}
      {chosen.length > 0 && (
        <>
          <strong className="selection-count" aria-live="polite">
            {chosen.length}개 선택
          </strong>
          <button
            type="button"
            className="button secondary small"
            disabled={busy || running}
            title={
              running
                ? '진행 중인 AI 분석이 끝나면 다시 실행할 수 있습니다'
                : 'LLM으로 차례로 분석합니다. 이미 분석한 문서는 다시 분석합니다'
            }
            onClick={() =>
              configured
                ? void run(async () => {
                    await api('analysis.start', { ids });
                    watchJobs();
                  })
                : openAdmin('llm')
            }
          >
            <Sparkles size={14} />
            AI 분석
          </button>
          {chosen.some((doc) => analyzed(doc) || doc.status === 'error') && (
            <Confirm
              label="분석 결과 지우기"
              icon={<Eraser size={14} />}
              question={`선택한 문서 ${ids.length}개의 AI 분석 결과(요약·키워드·관계)를 지울까요?`}
              disabled={busy}
              onConfirm={() =>
                run(async () => {
                  const result = await api<{ cleared: number; busy: number }>(
                    'wiki.clear_analysis',
                    { ids },
                  );
                  await refresh();
                  const kept = result.busy
                    ? `분석 중인 문서 ${result.busy}개는 그대로 두었습니다.`
                    : '';
                  return result.cleared
                    ? `문서 ${result.cleared}개의 AI 분석 결과를 지웠습니다. ${kept}`.trim()
                    : kept || '지울 AI 분석 결과가 없습니다.';
                })
              }
            />
          )}
          <Confirm
            label="삭제"
            icon={<Trash2 size={14} />}
            question={`선택한 문서 ${ids.length}개를 휴지통으로 옮길까요? 30일 안에 복원할 수 있습니다.`}
            disabled={busy}
            onConfirm={() =>
              run(async () => {
                const moved = await api<Deleted>('wiki.delete_many', { ids });
                await refresh();
                undoable(`문서 ${moved.deleted}개를 휴지통으로 옮겼습니다.`, {
                  label: '되돌리기',
                  run: async () => {
                    await api('wiki.restore', { ids: moved.ids });
                    await refresh();
                  },
                });
              })
            }
          />
          <button type="button" className="text-button" onClick={onClear}>
            선택 해제
          </button>
        </>
      )}
    </div>
  );
}

// A row of the list, drawn again only when its document (kept as the same object while it does not change, see
// keepEqual in src/lib/query.ts), its check or its place changed. Without `checked` it has no checkbox; without
// `tabbable` it keeps the browser's own Tab order (the dashboard's few rows). An entry that stands for an import
// saved in parts says how many parts it has.
const DocumentRow = memo(function DocumentRow({
  doc,
  onOpen,
  index,
  count,
  checked,
  onToggle,
  tabbable,
  onFocusRow,
}: {
  doc: WikiDocument;
  onOpen: (id: string) => void;
  // The row's place in the list and the list's length (-1 while pages are still to come), for screen readers.
  index?: number;
  count?: number;
  checked?: boolean;
  onToggle?: (doc: WikiDocument, on: boolean, range: boolean) => void;
  tabbable?: boolean;
  onFocusRow?: (key: string, column: Column) => void;
}) {
  const Icon = doc.source ? ICONS[doc.source.kind] : FileText;
  const parts = doc.parts?.length ?? 0;
  const tabIndex = tabbable === undefined ? undefined : tabbable ? 0 : -1;
  return (
    <li
      className={checked === undefined ? undefined : `selectable${checked ? ' selected' : ''}`}
      data-window-key={index === undefined ? undefined : doc.id}
      data-index={index}
      aria-posinset={index === undefined ? undefined : index + 1}
      aria-setsize={index === undefined ? undefined : count}
    >
      {checked !== undefined && (
        <input
          type="checkbox"
          className="document-check"
          data-column="check"
          tabIndex={tabIndex}
          aria-label={`${doc.title} 선택`}
          checked={checked}
          onFocus={() => onFocusRow?.(doc.id, 'check')}
          // React reports a checkbox change with the click that made it, so Shift can be read.
          onChange={(event) =>
            onToggle?.(doc, event.target.checked, !!(event.nativeEvent as MouseEvent).shiftKey)
          }
        />
      )}
      <button
        className="document-row"
        data-column="open"
        tabIndex={tabIndex}
        onFocus={() => onFocusRow?.(doc.id, 'open')}
        onClick={() => onOpen(doc.id)}
      >
        <Icon size={15} aria-hidden="true" />
        <span className="document-title">
          <strong>{doc.title}</strong>
          <small>{doc.summary || plain(doc.excerpt ?? '')}</small>
        </span>
        {parts > 1 && (
          <span
            className="index-badge parts-badge"
            title={`긴 글을 나누어 저장한 문서 ${parts}개를 한 줄로 보여 줍니다. 선택하면 ${parts}개 모두에 적용됩니다.`}
          >
            <Layers size={11} />
            {parts}개로 나눔
          </span>
        )}
        {doc.index_mode === 'pageindex' && (
          <span className="index-badge" title="긴 문서를 목차 단위로 색인했습니다 (PageIndex)">
            <ListTree size={11} />
            목차 색인
          </span>
        )}
        <AnalysisLabel doc={doc} />
        <time dateTime={doc.updated_at}>{date(doc.updated_at)}</time>
      </button>
    </li>
  );
});
