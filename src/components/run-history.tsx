import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  BarChart3,
  CalendarClock,
  Check,
  History,
  LoaderCircle,
  Search,
  Table2,
} from 'lucide-react';
import {
  api,
  clock,
  dayLabel,
  localDay,
  plain,
  seconds,
  type ActiveRun,
  type RunItem,
  type RunStats,
} from '@/lib/api';
import Highlight from './highlight';
import { rove } from './roving';
import { useActions, useInvalidated, usePrompts } from './state';

const PAGE = 30;
// The theme's pair (theme.css), validated for color vision: CVD ΔE 14 (deutan), and 16 for the dark theme's (CIEDE2000
// on a Machado deuteranopia simulation). Marks also carry an icon and a label.
export const STATUS_FILL = { success: 'var(--chart-success)', error: 'var(--chart-error)' };

/** Runs in progress anywhere (this window, "run now", or the scheduler); calls onDone as they finish. It looks every
 * 2.5 seconds while a run is in progress and every 5 seconds otherwise, and draws again only when the runs change. */
export function useActiveRuns(onDone: () => void) {
  const [active, setActive] = useState<ActiveRun[]>([]);
  const done = useRef(onDone);
  useEffect(() => {
    done.current = onDone;
  });
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    let previous = new Set<string>();
    let shown = '[]';
    const poll = async () => {
      try {
        const runs = await api<ActiveRun[]>('runs.active');
        if (!live) return;
        const signature = JSON.stringify(runs);
        if (signature !== shown) {
          shown = signature;
          setActive(runs);
        }
        const ids = new Set(runs.map((run) => run.id));
        if ([...previous].some((id) => !ids.has(id))) done.current();
        previous = ids;
      } catch {
        /* The next poll retries; actions report their own errors. */
      }
      if (live) timer = setTimeout(poll, previous.size ? 2500 : 5000);
    };
    void poll();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, []);
  return active;
}

export function useNow(interval: number, enabled = true) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now()); // The clock may have been idle; never show a stale elapsed time.
    const timer = setInterval(() => setNow(Date.now()), interval);
    return () => clearInterval(timer);
  }, [interval, enabled]);
  return now;
}

// The newest `count` runs of the list, asked for in pages of at most 200 (the most runs.list gives at once), and
// whether there may be older ones.
async function newestRuns(
  filters: Record<string, string | undefined>,
  count: number,
): Promise<[RunItem[], boolean]> {
  const runs: RunItem[] = [];
  for (;;) {
    const limit = Math.min(200, count - runs.length);
    const page = await api<RunItem[]>('runs.list', {
      ...filters,
      limit,
      before: runs.at(-1)?.created_at,
    });
    runs.push(...page);
    if (page.length < limit) return [runs, false];
    if (runs.length >= count) return [runs, true];
  }
}

const kindLabel = (run: Pick<RunItem, 'kind' | 'schedule_id' | 'schedule_name'>) =>
  `${run.kind === 'scheduled' ? '예약 실행' : '직접 실행'}${
    run.schedule_id ? ` · ${run.schedule_name || '삭제된 예약'}` : ''
  }`;

export default function RunHistory() {
  const prompts = usePrompts();
  // Its pages of runs load in their own way: again whenever the runs change.
  const version = useInvalidated(['runs']);
  const { openRun, notify } = useActions();
  const [promptId, setPromptId] = useState('');
  const [days, setDays] = useState<7 | 14 | 30>(14);
  const [status, setStatus] = useState<'' | 'success' | 'error'>('');
  const [kind, setKind] = useState<'' | 'manual' | 'scheduled'>('');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [stats, setStats] = useState<RunStats | null>(null);
  const [items, setItems] = useState<RunItem[] | null>(null);
  const [more, setMore] = useState(false);
  const [table, setTable] = useState(false);
  const [busy, setBusy] = useState('');
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((value) => value + 1), []);
  const active = useActiveRuns(reload);
  const now = useNow(1000, active.length > 0);
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);
  const filters = useMemo(
    () => ({
      prompt_id: promptId || undefined,
      status: status || undefined,
      kind: kind || undefined,
      query: query || undefined,
    }),
    [promptId, status, kind, query],
  );
  // How many runs the list shows: the pages ‘이전 기록 더 보기’ loaded stay when the list loads again for a run that
  // ended, until the filters change. Each load is counted, so that a page asked for before it is dropped.
  const loaded = useRef({ scope: '', count: PAGE, load: 0 });
  useEffect(() => {
    let live = true;
    const state = loaded.current;
    const scope = JSON.stringify(filters);
    if (scope !== state.scope) Object.assign(state, { scope, count: PAGE });
    state.load += 1;
    Promise.all([
      api<RunStats>('runs.stats', {
        days,
        prompt_id: promptId || undefined,
        timezone_offset: new Date().getTimezoneOffset(),
      }),
      newestRuns(filters, state.count),
    ])
      .then(([summary, [list, full]]) => {
        if (!live) return;
        setStats(summary);
        setItems(list);
        setMore(full);
      })
      .catch((error) => live && notify(error.message, true));
    return () => {
      live = false;
    };
  }, [days, promptId, filters, version, tick, notify]);
  async function act(key: string, fn: () => Promise<void>) {
    setBusy(key);
    try {
      await fn();
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy('');
    }
  }
  const open = (id: string) => openRun(id, query ? [query] : []);
  const groups = useMemo(() => {
    const byDay = new Map<string, RunItem[]>();
    for (const item of items || []) {
      const key = localDay(item.created_at);
      byDay.set(key, [...(byDay.get(key) || []), item]);
    }
    return [...byDay];
  }, [items]);
  const totals = stats?.totals;
  const rate = totals?.total ? Math.round((totals.success / totals.total) * 100) : null;
  // What a screen reader hears of the runs in progress: which ones, once as they start and end, never their seconds.
  const running = active.length
    ? `지금 실행 중 ${active.length}개: ${active.map((run) => run.title).join(', ')}`
    : '';
  return (
    <div className="run-dashboard">
      <p className="sr-only" role="status">
        {running}
      </p>
      <div className="history-scope">
        <label>
          프롬프트
          <select value={promptId} onChange={(e) => setPromptId(e.target.value)}>
            <option value="">전체 프롬프트</option>
            {prompts.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
              </option>
            ))}
          </select>
        </label>
        <div className="segmented" role="radiogroup" aria-label="요약 기간">
          {([7, 14, 30] as const).map((value) => (
            <button
              key={value}
              role="radio"
              aria-checked={days === value}
              tabIndex={days === value ? 0 : -1}
              onKeyDown={rove}
              className={days === value ? 'active' : ''}
              onClick={() => setDays(value)}
            >
              최근 {value}일
            </button>
          ))}
        </div>
      </div>
      <div className="stat-row">
        <div className="stat-tile">
          <span>실행</span>
          <strong>{totals ? totals.total.toLocaleString() : '—'}</strong>
          <small>최근 {days}일</small>
        </div>
        <div className="stat-tile">
          <span>성공률</span>
          <strong>{rate === null ? '—' : `${rate}%`}</strong>
          <small>{totals?.error ? `실패 ${totals.error}회` : '실패 없음'}</small>
        </div>
        <div className="stat-tile">
          <span>평균 응답 시간</span>
          <strong>{seconds(totals?.average_ms ?? null)}</strong>
          <small>성공한 실행 기준</small>
        </div>
        <div className="stat-tile">
          <span>예약 실행</span>
          <strong>{totals ? totals.scheduled.toLocaleString() : '—'}</strong>
          <small>켜 둔 예약 {stats?.active_schedules ?? 0}개</small>
        </div>
      </div>
      {active.length > 0 && (
        <section className="running-now">
          <h3>
            <LoaderCircle className="spin" size={16} />
            지금 실행 중 <span>{active.length}</span>
          </h3>
          {active.map((run) => (
            <div className="running-row" key={run.id}>
              <strong>{run.title}</strong>
              <span className={`run-kind ${run.kind}`}>{kindLabel(run)}</span>
              <span className="running-time">
                {run.started_at
                  ? `${seconds(Math.max(0, now - new Date(run.started_at).getTime()))} 경과`
                  : '실행 중'}
              </span>
              <span className="running-bar" aria-hidden="true" />
            </div>
          ))}
        </section>
      )}
      <section className="chart-card">
        <header>
          <div>
            <h3>일별 실행</h3>
            <p>하루 동안 끝난 실행을 성공과 실패로 나누어 보여줍니다.</p>
          </div>
          <div className="chart-legend">
            <span>
              <i style={{ background: STATUS_FILL.success }} />
              <Check size={12} />
              성공
            </span>
            <span>
              <i style={{ background: STATUS_FILL.error }} />
              <AlertCircle size={12} />
              실패
            </span>
            <button className="text-button" aria-pressed={table} onClick={() => setTable(!table)}>
              {table ? <BarChart3 size={14} /> : <Table2 size={14} />}
              {table ? '차트로 보기' : '표로 보기'}
            </button>
          </div>
        </header>
        {stats &&
          (table ? (
            <div className="chart-table-wrap">
              <table className="chart-table">
                <thead>
                  <tr>
                    <th>날짜</th>
                    <th>성공</th>
                    <th>실패</th>
                    <th>예약 실행</th>
                  </tr>
                </thead>
                <tbody>
                  {[...stats.days].reverse().map((day) => (
                    <tr key={day.date}>
                      <td>{dayLabel(day.date)}</td>
                      <td>{day.success}</td>
                      <td>{day.error}</td>
                      <td>{day.scheduled}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <RunChart days={stats.days} />
          ))}
      </section>
      <section className="history-section">
        <div className="history-filters">
          <h3>실행 기록</h3>
          <div className="segmented" role="radiogroup" aria-label="실행 결과">
            {(
              [
                ['', '전체'],
                ['success', '성공'],
                ['error', '실패'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={label}
                role="radio"
                aria-checked={status === value}
                tabIndex={status === value ? 0 : -1}
                onKeyDown={rove}
                className={status === value ? 'active' : ''}
                onClick={() => setStatus(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="segmented" role="radiogroup" aria-label="실행 방식">
            {(
              [
                ['', '모든 방식'],
                ['manual', '직접 실행'],
                ['scheduled', '예약 실행'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={label}
                role="radio"
                aria-checked={kind === value}
                tabIndex={kind === value ? 0 : -1}
                onKeyDown={rove}
                className={kind === value ? 'active' : ''}
                onClick={() => setKind(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="history-search">
            <Search size={14} />
            <input
              aria-label="실행 기록 검색"
              placeholder="제목·입력·응답 검색"
              value={search}
              maxLength={200}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        </div>
        {items === null ? (
          <div className="empty compact">
            <LoaderCircle className="spin" size={26} />
          </div>
        ) : items.length ? (
          <div className="run-timeline">
            {groups.map(([day, runs]) => (
              <div key={day} className="run-day">
                <h4>{dayLabel(day)}</h4>
                {runs.map((run) => (
                  <button
                    key={run.id}
                    className="run-row"
                    disabled={!!busy}
                    onClick={() => open(run.id)}
                  >
                    <span className={`run-status ${run.status}`}>
                      {run.status === 'success' ? <Check size={13} /> : <AlertCircle size={13} />}
                      <span className="sr-only">{run.status === 'success' ? '성공' : '실패'}</span>
                    </span>
                    <span className="run-time">{clock(run.created_at)}</span>
                    <span className="run-main">
                      <strong>
                        <Highlight text={run.title} terms={query ? [query] : []} />
                      </strong>
                      <span>
                        <Highlight
                          text={plain(run.preview) || '응답 없음'}
                          terms={query ? [query] : []}
                        />
                      </span>
                    </span>
                    <span className={`run-kind ${run.kind}`}>
                      {run.kind === 'scheduled' && <CalendarClock size={12} />}
                      {kindLabel(run)}
                    </span>
                    <span className="run-duration">{seconds(run.duration_ms)}</span>
                  </button>
                ))}
              </div>
            ))}
            {more && (
              <button
                className="button secondary"
                disabled={!!busy}
                onClick={() =>
                  void act('more', async () => {
                    const state = loaded.current;
                    const load = state.load;
                    const next = await api<RunItem[]>('runs.list', {
                      ...filters,
                      limit: PAGE,
                      before: items[items.length - 1].created_at,
                    });
                    // The list loaded again meanwhile (another filter, a run that ended): the page was of the old one.
                    if (load !== state.load) return;
                    state.count = items.length + next.length;
                    setItems([...items, ...next]);
                    setMore(next.length === PAGE);
                  })
                }
              >
                {busy === 'more' && <LoaderCircle className="spin" size={15} />}
                이전 기록 더 보기
              </button>
            )}
          </div>
        ) : (
          <div className="empty compact">
            <div className="empty-icon">
              <History size={26} />
            </div>
            <h3>
              {status || kind || query || promptId
                ? '조건에 맞는 실행 기록이 없어요'
                : '아직 실행 기록이 없어요'}
            </h3>
            <p>프롬프트를 실행하거나 예약하면 결과가 이곳에 차곡차곡 쌓입니다.</p>
          </div>
        )}
      </section>
    </div>
  );
}

// Integer ticks roughly three steps apart, e.g. 0·5·10 or 0·10·20·30.
function yScale(value: number) {
  const top = Math.max(4, value);
  const power = 10 ** Math.floor(Math.log10(top / 3));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= top / 3)!;
  const max = Math.ceil(top / step) * step;
  return { max, ticks: Array.from({ length: max / step + 1 }, (_, i) => i * step) };
}
// A column segment: 4px rounded data-end on top, square at the baseline.
function segment(x: number, top: number, width: number, bottom: number, rounded: boolean) {
  const r = rounded ? Math.min(4, width / 2, bottom - top) : 0;
  return `M${x},${bottom}V${top + r}${r ? `A${r},${r} 0 0 1 ${x + r},${top}` : ''}H${x + width - r}${
    r ? `A${r},${r} 0 0 1 ${x + width},${top + r}` : ''
  }V${bottom}Z`;
}

export function RunChart({ days }: { days: RunStats['days'] }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const element = wrap.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(240, Math.round(entry.contentRect.width))),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const height = 200;
  const margin = { top: 16, right: 8, bottom: 30, left: 36 };
  const plot = { w: width - margin.left - margin.right, h: height - margin.top - margin.bottom };
  const { max, ticks } = yScale(Math.max(...days.map((d) => d.success + d.error)));
  const slot = plot.w / days.length;
  const bar = Math.min(24, slot * 0.6);
  const y = (value: number) => margin.top + plot.h - (value / max) * plot.h;
  const every = Math.ceil(days.length / 10);
  const today = localDay(new Date());
  const empty = days.every((d) => !d.success && !d.error);
  const shown = hover === null ? null : days[hover];
  // Beside the column, on the side with more room, so it never covers the column or legend.
  const column = hover === null ? 0 : margin.left + slot * hover + slot / 2;
  const side =
    hover !== null && hover >= days.length / 2
      ? { right: width - column + bar / 2 + 10 }
      : { left: column + bar / 2 + 10 };
  return (
    <div className="run-chart" ref={wrap}>
      <svg
        width={width}
        height={height}
        role="group"
        aria-label={`최근 ${days.length}일 동안의 일별 실행 수. 표로 보기에서 날짜별 값을 확인할 수 있습니다.`}
      >
        {ticks.map((tick) => (
          <g key={tick} aria-hidden="true">
            <line
              className="chart-grid"
              x1={margin.left}
              x2={width - margin.right}
              y1={y(tick)}
              y2={y(tick)}
            />
            <text className="chart-tick" x={margin.left - 8} y={y(tick) + 3.5} textAnchor="end">
              {tick}
            </text>
          </g>
        ))}
        {days.map((day, i) => {
          const x = margin.left + slot * i + (slot - bar) / 2;
          const base = y(0);
          const top = y(day.success + day.error);
          const split = y(day.success);
          // A 2px surface gap separates stacked segments; tiny ones keep at least 1px.
          const errorBase = day.success > 0 ? Math.max(top + 1, split - 2) : base;
          return (
            <g key={day.date} opacity={hover !== null && hover !== i ? 0.45 : 1}>
              {day.success > 0 && (
                <path
                  d={segment(x, split, bar, base, day.error === 0)}
                  style={{ fill: STATUS_FILL.success }}
                />
              )}
              {day.error > 0 && (
                <path
                  d={segment(x, top, bar, errorBase, true)}
                  style={{ fill: STATUS_FILL.error }}
                />
              )}
              {(days.length - 1 - i) % every === 0 && (
                <text
                  className="chart-tick"
                  x={x + bar / 2}
                  y={height - 10}
                  textAnchor="middle"
                  aria-hidden="true"
                >
                  {day.date === today
                    ? '오늘'
                    : `${Number(day.date.slice(5, 7))}/${Number(day.date.slice(8))}`}
                </text>
              )}
              <rect
                className="chart-hit"
                x={margin.left + slot * i}
                y={margin.top}
                width={slot}
                height={plot.h}
                tabIndex={0}
                role="img"
                aria-label={`${dayLabel(day.date)} 성공 ${day.success}회, 실패 ${day.error}회`}
                onPointerEnter={() => setHover(i)}
                onPointerLeave={() => setHover(null)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
              />
            </g>
          );
        })}
      </svg>
      {empty && <p className="chart-empty">이 기간에는 끝난 실행이 없어요.</p>}
      {shown && hover !== null && (
        // Visual only: each column's focusable hit area carries the same values as its label.
        <div className="chart-tooltip" aria-hidden="true" style={{ top: margin.top, ...side }}>
          <small>{dayLabel(shown.date)}</small>
          <span>
            <i style={{ background: STATUS_FILL.success }} />
            <strong>{shown.success}</strong> 성공
          </span>
          <span>
            <i style={{ background: STATUS_FILL.error }} />
            <strong>{shown.error}</strong> 실패
          </span>
          {shown.scheduled > 0 && <small>예약 실행 {shown.scheduled}회</small>}
        </div>
      )}
    </div>
  );
}
