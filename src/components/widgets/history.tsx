import { useEffect, useState } from 'react';
import { AlertCircle, Check } from 'lucide-react';
import { api, seconds, type RunStats } from '@/lib/api';
import { useApp } from '../app';
import { WidgetHead } from '../dashboard';
import RunHistory, { RunChart, STATUS_FILL } from '../run-history';
import { Loading, Metrics } from './parts';

export default function HistoryWidget() {
  const { stats } = useApp();
  return (
    <>
      <WidgetHead id="history" title="실행 기록" count={undefined}>
        <small className="muted">프롬프트 {stats.prompts}개</small>
      </WidgetHead>
      <RunHistory />
    </>
  );
}

const DAYS = 14;

// The run totals of the last two weeks, as the run history counts them.
function useRunStats() {
  const { runsVersion, notify } = useApp();
  const [stats, setStats] = useState<RunStats | null>(null);
  useEffect(() => {
    let live = true;
    api<RunStats>('runs.stats', { days: DAYS, timezone_offset: new Date().getTimezoneOffset() })
      .then((value) => live && setStats(value))
      .catch((error) => live && notify(error.message, true));
    return () => {
      live = false;
    };
  }, [runsVersion, notify]);
  return stats;
}

// The run history's daily chart alone.
export function HistoryChart() {
  const stats = useRunStats();
  return (
    <>
      <WidgetHead count={stats?.totals.total}>
        <span className="chart-legend">
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
        </span>
      </WidgetHead>
      {stats ? <RunChart days={stats.days} /> : <Loading />}
    </>
  );
}

// The run history's numbers alone.
export function HistoryStats() {
  const stats = useRunStats();
  const totals = stats?.totals;
  const rate = totals?.total ? Math.round((totals.success / totals.total) * 100) : null;
  return (
    <>
      <WidgetHead>
        <small className="muted">최근 {DAYS}일</small>
      </WidgetHead>
      {stats && totals ? (
        <Metrics
          items={[
            { label: '실행', value: totals.total },
            {
              label: '성공률',
              value: rate === null ? '—' : `${rate}%`,
              note: totals.error ? `실패 ${totals.error}회` : '실패 없음',
              alert: totals.error > 0,
            },
            { label: '평균 응답', value: seconds(totals.average_ms) },
            {
              label: '예약 실행',
              value: totals.scheduled,
              note: `켜 둔 예약 ${stats.active_schedules}개`,
            },
          ]}
        />
      ) : (
        <Loading />
      )}
    </>
  );
}
