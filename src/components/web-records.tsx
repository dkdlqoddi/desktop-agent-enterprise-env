import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Download,
  FolderOpen,
  Globe2,
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
  type WebCaptures,
  type WebDeleted,
  type WebExport,
  type WebKept,
  type WebRecords as Records,
  type WebSite,
} from '@/lib/api';
import Confirm from './confirm';
import EmptyState from './empty-state';
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

type Filters = { site: string; kind: string; text: string; since: string; until: string };
const EMPTY: Filters = { site: '', kind: '', text: '', since: '', until: '' };

export default function WebRecords() {
  const { notify } = useActions();
  const tab = useWorkspaceTab();
  const [sites, setSites] = useState<WebSite[]>([]);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [capture, setCapture] = useState(tab.startsWith('capture:') ? tab.slice(8) : '');
  const [offset, setOffset] = useState(0);
  const [records, setRecords] = useState<Records | null>(null);
  const [captures, setCaptures] = useState<WebCaptures | null>(null);
  const [busy, setBusy] = useState('');

  useEffect(() => {
    setCapture(tab.startsWith('capture:') ? tab.slice(8) : '');
    setOffset(0);
  }, [tab]);

  const name = (key: string) => sites.find((site) => site.key === key)?.name || key || '기타';

  // Each load has a number: the answer of an earlier filter that comes after a later one's is dropped.
  const latest = useRef(0);
  const load = useCallback(async () => {
    const id = ++latest.current;
    const params = {
      ...Object.fromEntries(Object.entries(filters).filter(([, value]) => value)),
      ...(capture ? { capture_id: capture } : {}),
      offset,
      limit: PAGE,
    };
    try {
      const [found, recent] = await Promise.all([
        api<Records>('web.records', params),
        api<WebCaptures>('web.captures', { limit: 12, site: filters.site }),
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
  }, []);

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

  const remove = async () => {
    try {
      await api<WebDeleted>('web.delete_capture', { id: capture });
      notify('기록과 스크린샷을 지웠습니다.');
      setCapture('');
      setOffset(0);
    } catch (error) {
      notify(error instanceof Error ? error.message : String(error), true);
    }
  };

  const items = records?.items ?? [];
  return (
    <div className="workspace-page records-page">
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
          <Confirm
            label="이 기록 지우기"
            question="이 페이지의 기록과 스크린샷을 지울까요? 되돌릴 수 없습니다."
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
            <ul>
              {captures.captures.map((item) => (
                <li key={item.id}>
                  <button
                    className={`records-capture ${capture === item.id ? 'selected' : ''}`}
                    aria-pressed={capture === item.id}
                    onClick={() => {
                      setCapture(item.id);
                      setOffset(0);
                    }}
                  >
                    <strong>
                      {name(item.site)}
                      {item.query && ` · ${item.query}`}
                    </strong>
                    <span>
                      광고 {item.ad_count} · 상품 {item.product_count} · 결과 {item.result_count}
                    </span>
                    <small>
                      {dayTime(item.captured_at)} · {SOURCES[item.source]}
                    </small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </div>
  );
}
