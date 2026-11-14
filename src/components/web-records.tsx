import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronsDown,
  Download,
  FolderOpen,
  Globe2,
  Image as ImageIcon,
  LoaderCircle,
  MousePointerClick,
  RefreshCw,
  ScanSearch,
  Table2,
  Trash2,
  X,
} from 'lucide-react';
import {
  api,
  dayTime,
  type TrashRestored,
  type WebCaptureSummary,
  type WebCaptures,
  type WebDeleted,
  type WebDeletedMany,
  type WebExport,
  type WebKept,
  type WebRecords as Records,
  type WebRetention,
  type WebScreenshot,
  type WebSite,
} from '@/lib/api';
import Confirm from './confirm';
import EmptyState from './empty-state';
import Modal from './modal';
import { useActions, useWorkspaceTab } from './state';

const PAGE = 50;
const KINDS = [
  ['', '전체'],
  ['ad', '광고'],
  ['product', '상품'],
  ['result', '검색 결과'],
  ['clicked', '누른 광고'],
] as const;
const KIND_NAMES = { ad: '광고', product: '상품', result: '검색 결과' } as const;
const SOURCES = { chat: '채팅', schedule: '예약', manual: '직접' } as const;
// The recorded pages beside the table come this many at a time, and one request gives at most MAX_CAPTURES (the
// backend's MAX_PAGE).
const CAPTURES = 20;
const MAX_CAPTURES = 200;
// How long the records may be kept (web.retention): until deleted, or this many days.
const KEEP: readonly (readonly [number, string])[] = [
  [0, '계속 보관'],
  [7, '7일'],
  [30, '30일'],
  [90, '90일'],
  [180, '180일'],
  [365, '1년'],
];
const keepLabel = (days: number) =>
  KEEP.find(([value]) => value === days)?.[1] ?? `${days.toLocaleString()}일`;

type Filters = { site: string; kind: string; text: string; since: string; until: string };
const EMPTY: Filters = { site: '', kind: '', text: '', since: '', until: '' };

export default function WebRecords() {
  const { notify, undoable } = useActions();
  const tab = useWorkspaceTab();
  const [sites, setSites] = useState<WebSite[]>([]);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [capture, setCapture] = useState(tab.startsWith('capture:') ? tab.slice(8) : '');
  const [offset, setOffset] = useState(0);
  const [records, setRecords] = useState<Records | null>(null);
  const [captures, setCaptures] = useState<WebCaptures | null>(null);
  const [busy, setBusy] = useState('');
  // The recorded pages chosen to delete together, the one whose screenshot is shown, and how long they are kept.
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const [viewing, setViewing] = useState<WebCaptureSummary | null>(null);
  const [keep, setKeep] = useState<number | null>(null);

  useEffect(() => {
    setCapture(tab.startsWith('capture:') ? tab.slice(8) : '');
    setOffset(0);
  }, [tab]);

  // A page of a site the records know by name, or of another site (`other`).
  const name = (key: string) =>
    sites.find((site) => site.key === key)?.name || (key && key !== 'other' ? key : '기타');
  // What a recorded page is called: its site and what was searched for, or its title.
  const captureName = (item: WebCaptureSummary) =>
    [name(item.site), item.query || item.title].filter(Boolean).join(' · ');

  // Each load has a number: the answer of an earlier filter that comes after a later one's is dropped.
  const latest = useRef(0);
  // How many recorded pages the list beside the table shows: 더 보기 adds a page, and loading the list again (a page
  // chosen, a filter changed) keeps as many, up to the most one request gives; another site starts from the first.
  const shownPages = useRef({ site: '', count: CAPTURES });
  const load = useCallback(async () => {
    const id = ++latest.current;
    const params = {
      ...Object.fromEntries(Object.entries(filters).filter(([, value]) => value)),
      ...(capture ? { capture_id: capture } : {}),
      offset,
      limit: PAGE,
    };
    if (shownPages.current.site !== filters.site)
      shownPages.current = { site: filters.site, count: CAPTURES };
    try {
      const [found, recent] = await Promise.all([
        api<Records>('web.records', params),
        api<WebCaptures>('web.captures', {
          limit: Math.min(MAX_CAPTURES, shownPages.current.count),
          site: filters.site,
        }),
      ]);
      if (id !== latest.current) return;
      setRecords(found);
      setCaptures(recent);
    } catch (error) {
      if (id === latest.current)
        notify(error instanceof Error ? error.message : String(error), true);
    }
  }, [filters, capture, offset, notify]);

  useEffect(() => {
    api<WebSite[]>('web.sites')
      .then(setSites)
      .catch(() => setSites([]));
    api<WebRetention>('web.retention')
      .then((kept) => setKeep(kept.days))
      .catch(() => setKeep(0));
  }, []);

  // The next recorded pages after those shown, for the same site; a load of the list meanwhile takes their place.
  const moreCaptures = async () => {
    const id = latest.current;
    const shown = captures?.captures ?? [];
    try {
      const next = await api<WebCaptures>('web.captures', {
        offset: shown.length,
        limit: CAPTURES,
        site: filters.site,
      });
      if (id !== latest.current) return;
      shownPages.current.count = shown.length + next.captures.length;
      setCaptures({ total: next.total, captures: [...shown, ...next.captures] });
    } catch (error) {
      notify(error instanceof Error ? error.message : String(error), true);
    }
  };

  useEffect(() => {
    void load();
  }, [load]);

  const change = (patch: Partial<Filters>) => {
    setFilters((current) => ({ ...current, ...patch }));
    setOffset(0);
  };

  const run = async (label: string, task: () => Promise<void>) => {
    setBusy(label);
    try {
      await task();
    } catch (error) {
      notify(error instanceof Error ? error.message : String(error), true);
    } finally {
      setBusy('');
    }
  };

  const exportCsv = () =>
    run('export', async () => {
      const params = {
        ...Object.fromEntries(Object.entries(filters).filter(([, value]) => value)),
        ...(capture ? { capture_id: capture } : {}),
      };
      const saved = await api<WebExport>('web.export', params);
      notify(`기록 ${saved.rows.toLocaleString()}건을 CSV로 저장했습니다: ${saved.path}`);
    });

  const capturePage = () =>
    run('capture', async () => {
      const kept = await api<WebKept>('web.capture_page', { source: 'manual' });
      notify(
        `열린 페이지에서 광고 ${kept.counts.ad}건, 상품 ${kept.counts.product}건, 검색 결과 ${kept.counts.result}건을 기록했습니다.`,
      );
      setCapture(kept.capture_id);
      setOffset(0);
    });

  // The record goes to the trash with its screenshot, from where 되돌리기 (or 관리 → 휴지통) brings it back.
  const remove = async () => {
    try {
      const { trash_ids: ids } = await api<WebDeleted>('web.delete_capture', { id: capture });
      undoable('기록을 휴지통으로 옮겼습니다.', {
        label: '되돌리기',
        run: async () => {
          try {
            const { restored } = await api<TrashRestored>('trash.restore', { ids });
            if (!restored)
              notify('휴지통에 없는 기록입니다. 이미 복원했거나 영구 삭제했습니다.', true);
          } catch (error) {
            notify(error instanceof Error ? error.message : String(error), true);
          }
          await load();
        },
      });
      setCapture('');
      setOffset(0);
    } catch (error) {
      notify(error instanceof Error ? error.message : String(error), true);
    }
  };

  // The pages chosen among those shown, deleted together: to the trash with their screenshots, from where 되돌리기
  // (or 관리 → 휴지통) brings them back.
  const shownCaptures = captures?.captures;
  const chosen = useMemo(
    () => (shownCaptures ?? []).filter((item) => selected.has(item.id)),
    [shownCaptures, selected],
  );
  const choose = (items: readonly WebCaptureSummary[], on: boolean) =>
    setSelected((current) => {
      const changed = new Set(current);
      for (const item of items) {
        if (on) changed.add(item.id);
        else changed.delete(item.id);
      }
      return changed;
    });
  const removeChosen = (items: readonly WebCaptureSummary[]) =>
    run('remove', async () => {
      const ids = items.map((item) => item.id);
      const moved = await api<WebDeletedMany>('web.delete_captures', { ids });
      setSelected(new Set());
      if (ids.includes(capture)) setCapture('');
      setOffset(0);
      await load();
      undoable(`기록 ${moved.deleted.toLocaleString()}개를 휴지통으로 옮겼습니다.`, {
        label: '되돌리기',
        run: async () => {
          try {
            const { restored } = await api<TrashRestored>('trash.restore', {
              ids: moved.trash_ids,
            });
            if (!restored)
              notify('휴지통에 없는 기록입니다. 이미 복원했거나 영구 삭제했습니다.', true);
          } catch (error) {
            notify(error instanceof Error ? error.message : String(error), true);
          }
          await load();
        },
      });
    });
  // How long the records are kept: the retention policy moves the older ones to the trash once a day.
  const saveKeep = (days: number) =>
    run('keep', async () => {
      const kept = await api<WebRetention>('web.retention_save', { days });
      setKeep(kept.days);
      notify(
        kept.days
          ? `웹 기록을 ${keepLabel(kept.days)} 동안 보관합니다. 지난 기록은 하루 한 번 휴지통으로 옮깁니다.`
          : '웹 기록을 지울 때까지 보관합니다.',
      );
    });

  const items = records?.items ?? [];
  const current = shownCaptures?.find((item) => item.id === capture);
  const shownAll = !!shownCaptures?.length && chosen.length === shownCaptures.length;
  // The user's alone (data-user-only), the panel with a page's screenshot inside it too: what the sites showed the user
  // and the ads they pressed are no AI tool's, as no method of the local API reaches them. The local API's screen
  // control (app-control.ts) types and presses nothing here, at any access level.
  return (
    <div className="workspace-page records-page" data-user-only="records">
      <header className="page-heading">
        <div>
          <p className="eyebrow">WEB RECORDS</p>
          <h1>웹 기록</h1>
          <p>사이트에서 본 광고와 상품, 직접 누른 광고의 도착 페이지를 내 PC에 남깁니다.</p>
        </div>
        <div className="agent-heading-tools">
          <button
            className="button secondary"
            onClick={() => void capturePage()}
            disabled={!!busy}
            title="브라우저 창에 지금 열린 페이지를 읽어 기록합니다"
          >
            {busy === 'capture' ? (
              <LoaderCircle className="spin" size={15} />
            ) : (
              <ScanSearch size={15} />
            )}
            열린 페이지 기록
          </button>
          <button className="button secondary" onClick={() => void exportCsv()} disabled={!!busy}>
            {busy === 'export' ? (
              <LoaderCircle className="spin" size={15} />
            ) : (
              <Download size={15} />
            )}
            CSV 내보내기
          </button>
          <button
            className="button secondary"
            onClick={() => void run('folder', () => api('web.open_exports').then(() => {}))}
            disabled={!!busy}
          >
            <FolderOpen size={15} /> 내보내기 폴더
          </button>
        </div>
      </header>

      <section className="records-filters" aria-label="기록 거르기">
        <label>
          사이트
          <select value={filters.site} onChange={(event) => change({ site: event.target.value })}>
            <option value="">전체</option>
            {sites.map((site) => (
              <option key={site.key} value={site.key}>
                {site.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          종류
          <select value={filters.kind} onChange={(event) => change({ kind: event.target.value })}>
            {KINDS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="grow">
          검색어
          <input
            type="search"
            value={filters.text}
            placeholder="제목·검색어로 찾기"
            onChange={(event) => change({ text: event.target.value })}
          />
        </label>
        <label>
          시작일
          <input
            type="date"
            value={filters.since}
            onChange={(event) => change({ since: event.target.value })}
          />
        </label>
        <label>
          종료일
          <input
            type="date"
            value={filters.until}
            onChange={(event) => change({ until: event.target.value })}
          />
        </label>
        <button
          className="icon-button"
          aria-label="새로고침"
          title="새로고침"
          onClick={() => void load()}
        >
          <RefreshCw size={16} />
        </button>
      </section>

      {capture && (
        <p className="records-scope">
          기록 하나만 보는 중입니다.
          <button className="button secondary small" onClick={() => setCapture('')}>
            <X size={13} /> 모두 보기
          </button>
          {current?.screenshot && (
            <button className="button secondary small" onClick={() => setViewing(current)}>
              <ImageIcon size={13} /> 스크린샷 보기
            </button>
          )}
          <Confirm
            label="이 기록 지우기"
            question="이 페이지의 기록과 스크린샷을 휴지통으로 옮길까요? 30일 안에 복원할 수 있습니다."
            icon={<Trash2 size={13} />}
            onConfirm={remove}
          />
        </p>
      )}

      <div className="records-layout">
        <section className="records-table-wrap" aria-label="기록 목록">
          {records === null ? (
            <div className="agent-loading">
              <LoaderCircle className="spin" size={22} /> 기록을 불러오는 중
            </div>
          ) : items.length === 0 ? (
            <EmptyState icon={Table2} title="기록이 없습니다">
              채팅에서 &ldquo;쿠팡에서 노트북 광고 기록해 줘&rdquo;처럼 맡기거나, 브라우저 창에 연
              페이지를 &lsquo;열린 페이지 기록&rsquo;으로 남겨 보세요.
            </EmptyState>
          ) : (
            <>
              <table className="records-table">
                <caption className="sr-only">웹 기록 {records.total.toLocaleString()}건</caption>
                <thead>
                  <tr>
                    <th scope="col">기록 시각</th>
                    <th scope="col">사이트 · 검색어</th>
                    <th scope="col">종류</th>
                    <th scope="col">제목</th>
                    <th scope="col">가격</th>
                    <th scope="col">링크</th>
                    <th scope="col">직접 누름</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td>{dayTime(item.captured_at)}</td>
                      <td>
                        {name(item.site)}
                        {item.query && <span className="records-query"> · {item.query}</span>}
                      </td>
                      <td>
                        <span className={`record-kind ${item.kind}`}>{KIND_NAMES[item.kind]}</span>
                      </td>
                      <td className="records-title">
                        {item.title || <span className="muted">(제목 없음)</span>}
                        {item.display_url && <small>{item.display_url}</small>}
                      </td>
                      <td>{item.price}</td>
                      <td>
                        {item.link_url && (
                          <button
                            className="link-button"
                            title={item.link_url}
                            onClick={() =>
                              void run('open', () =>
                                api('web.open', { url: item.link_url }).then(() => {}),
                              )
                            }
                          >
                            <Globe2 size={13} /> 브라우저에서 열기
                          </button>
                        )}
                      </td>
                      <td>
                        {item.clicked_at && (
                          <span className="records-clicked" title={item.landing_url || undefined}>
                            <MousePointerClick size={13} /> {dayTime(item.clicked_at)}
                            {item.landing_title && <small>{item.landing_title}</small>}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <nav className="records-pager" aria-label="기록 쪽">
                <button
                  className="button secondary small"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE))}
                >
                  이전
                </button>
                <span>
                  {offset + 1}–{Math.min(offset + PAGE, records.total)} /{' '}
                  {records.total.toLocaleString()}
                </span>
                <button
                  className="button secondary small"
                  disabled={offset + PAGE >= records.total}
                  onClick={() => setOffset(offset + PAGE)}
                >
                  다음
                </button>
              </nav>
            </>
          )}
        </section>
        <aside className="records-captures" aria-label="최근 기록한 페이지">
          <h2>최근 기록한 페이지</h2>
          {!captures?.captures.length ? (
            <p className="muted">아직 없습니다.</p>
          ) : (
            <>
              <div className="records-bulk" role="group" aria-label="선택한 기록 작업">
                <label className="check" title="지금 보이는 기록한 페이지를 모두 선택합니다">
                  <input
                    type="checkbox"
                    checked={shownAll}
                    ref={(box) => {
                      if (box) box.indeterminate = !!chosen.length && !shownAll;
                    }}
                    onChange={(event) => choose(captures.captures, event.target.checked)}
                  />
                  모두 선택
                </label>
                {chosen.length > 0 && (
                  <>
                    <strong className="selection-count" aria-live="polite">
                      {chosen.length.toLocaleString()}개 선택
                    </strong>
                    <Confirm
                      label="지우기"
                      icon={<Trash2 size={13} />}
                      question={`선택한 기록 ${chosen.length.toLocaleString()}개를 스크린샷과 함께 휴지통으로 옮길까요? 30일 안에 복원할 수 있습니다.`}
                      disabled={!!busy}
                      onConfirm={() => removeChosen(chosen)}
                    />
                  </>
                )}
              </div>
              <ul>
                {captures.captures.map((item) => {
                  const label = captureName(item);
                  return (
                    <li key={item.id}>
                      <input
                        type="checkbox"
                        aria-label={`${label} 선택`}
                        checked={selected.has(item.id)}
                        onChange={(event) => choose([item], event.target.checked)}
                      />
                      <button
                        className={`records-capture ${capture === item.id ? 'selected' : ''}`}
                        aria-pressed={capture === item.id}
                        onClick={() => {
                          setCapture(item.id);
                          setOffset(0);
                        }}
                      >
                        <strong>{label}</strong>
                        <span>
                          광고 {item.ad_count} · 상품 {item.product_count} · 결과{' '}
                          {item.result_count}
                        </span>
                        <small>
                          {dayTime(item.captured_at)} · {SOURCES[item.source]}
                        </small>
                      </button>
                      {item.screenshot && (
                        <button
                          type="button"
                          className="icon-button"
                          aria-label={`${label} 스크린샷 보기`}
                          title="스크린샷 보기"
                          onClick={() => setViewing(item)}
                        >
                          <ImageIcon size={15} />
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
              {captures.total > captures.captures.length && (
                <button
                  type="button"
                  className="text-button records-more"
                  onClick={() => void moreCaptures()}
                >
                  <ChevronsDown size={13} /> 더 보기
                  <small>
                    {captures.captures.length.toLocaleString()} / {captures.total.toLocaleString()}
                  </small>
                </button>
              )}
            </>
          )}
          <label className="records-retention">
            보관 기간
            <select
              value={keep ?? 0}
              disabled={keep === null || busy === 'keep'}
              onChange={(event) => void saveKeep(Number(event.target.value))}
            >
              {[
                ...KEEP,
                ...(keep && !KEEP.some(([days]) => days === keep) ? [[keep, '']] : []),
              ].map(([days]) => (
                <option key={days} value={days}>
                  {keepLabel(Number(days))}
                </option>
              ))}
            </select>
          </label>
          <p className="records-retention-note">
            {keep
              ? `${keepLabel(keep)}이 지난 기록은 하루 한 번 휴지통으로 옮기고, 30일 뒤 스크린샷과 함께 지웁니다.`
              : '지울 때까지 보관합니다. 지운 기록은 스크린샷과 함께 30일 동안 휴지통에 있습니다.'}
          </p>
        </aside>
      </div>
      {viewing && (
        <Screenshot
          capture={viewing}
          label={captureName(viewing)}
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  );
}

// A recorded page's screenshot (web.screenshot), the whole page as it was read, in a panel of its own.
function Screenshot({
  capture,
  label,
  onClose,
}: {
  capture: WebCaptureSummary;
  label: string;
  onClose: () => void;
}) {
  const [data, setData] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    api<WebScreenshot>('web.screenshot', { id: capture.id })
      .then((shot) => live && setData(shot.data))
      .catch(
        (failure) => live && setError(failure instanceof Error ? failure.message : String(failure)),
      );
    return () => {
      live = false;
    };
  }, [capture.id]);
  return (
    <Modal
      title="스크린샷"
      subtitle={`${label} · ${dayTime(capture.captured_at)}`}
      onClose={onClose}
    >
      <div className="drawer-body records-shot">
        {data ? (
          // A data: URL of a whole page, drawn at the panel's width and its own height: there is nothing for
          // next/image to size or optimize (the export leaves pictures unoptimized).
          // eslint-disable-next-line @next/next/no-img-element -- see above
          <img src={data} alt={`${label} 페이지의 스크린샷`} />
        ) : error ? (
          <p className="inline-error" role="alert">
            {error}
          </p>
        ) : (
          <p className="muted">
            <LoaderCircle className="spin" size={14} /> 스크린샷을 불러오는 중
          </p>
        )}
      </div>
    </Modal>
  );
}
