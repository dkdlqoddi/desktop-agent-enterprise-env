import { useEffect, useState } from 'react';
import { ArrowRight, CalendarClock } from 'lucide-react';
import { api, type Schedule } from '@/lib/api';
import { useApp } from '../app';
import { WidgetHead } from '../dashboard';
import EmptyState from '../empty-state';
import { describeSchedule } from '../prompt-schedules';
import { Agenda, Loading } from './parts';

// The schedules that are on, in the order they run next, under their days.
export function ScheduleAgenda() {
  const { prompts, runsVersion, notify, navigate } = useApp();
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  useEffect(() => {
    let live = true;
    api<Schedule[]>('schedules.list')
      .then((items) => live && setSchedules(items))
      .catch((error) => live && notify(error.message, true));
    return () => {
      live = false;
    };
  }, [prompts, runsVersion, notify]);
  const next = (schedules ?? [])
    .filter((schedule) => schedule.enabled && schedule.next_run_at)
    .sort((a, b) => new Date(a.next_run_at!).getTime() - new Date(b.next_run_at!).getTime());
  const off = (schedules?.length ?? 0) - next.length;
  return (
    <>
      <WidgetHead count={next.length} />
      {schedules === null ? (
        <Loading />
      ) : next.length ? (
        <Agenda
          items={next.slice(0, 10).map((schedule) => ({
            id: schedule.id,
            at: schedule.next_run_at!,
            title: schedule.name,
            meta: `${schedule.prompt_title} · ${describeSchedule(schedule)}`,
            alert: schedule.last_status === 'error',
          }))}
        />
      ) : (
        <EmptyState
          icon={CalendarClock}
          title="켜 둔 예약이 없어요"
          action={
            <button className="text-button" onClick={() => navigate('automation', 'schedules')}>
              예약 실행 열기 <ArrowRight size={13} />
            </button>
          }
        >
          반복되는 요청을 예약하면 다음 실행 시각이 여기에 차례로 놓입니다.
        </EmptyState>
      )}
      {off > 0 && <small className="widget-foot">꺼 두었거나 마친 예약 {off}개</small>}
    </>
  );
}
