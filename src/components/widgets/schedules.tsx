import { useEffect, useState } from 'react';
import { AlertCircle, Check, LoaderCircle, Pencil, Play, Plus, Trash2 } from 'lucide-react';
import { api, dayTime, relative, type Run, type Schedule } from '@/lib/api';
import { useApp } from '../app';
import Confirm from '../confirm';
import { WidgetHead } from '../dashboard';
import {
  describeSchedule,
  ScheduleForm,
  scheduleDraft,
  type ScheduleDraft,
} from '../prompt-schedules';
import { useActiveRuns, useNow } from '../run-history';

export default function SchedulesWidget() {
  const { prompts, configured, openAdmin, notify, runsVersion, bumpRuns } = useApp();
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  const [editing, setEditing] = useState<ScheduleDraft | null>(null);
  const [busy, setBusy] = useState('');
  // The schedules run now with ▶: each waits for the LLM, up to minutes, while the other rows stay usable.
  const [starting, setStarting] = useState<ReadonlySet<string>>(new Set());
  // A switch flips at once and settles to the saved state when the list reloads.
  const [flipped, setFlipped] = useState<Record<string, boolean>>({});
  const running = new Set(useActiveRuns(bumpRuns).map((run) => run.schedule_id));
  const now = useNow(60_000);
  useEffect(() => {
    let live = true;
    api<Schedule[]>('schedules.list')
      .then((items) => {
        if (!live) return;
        setSchedules(items);
        setFlipped({});
      })
      .catch((error) => live && notify(error.message, true));
    return () => {
      live = false;
    };
  }, [prompts, runsVersion, notify]);
  async function act(key: string, action: () => Promise<void>) {
    setBusy(key);
    try {
      await action();
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy('');
      bumpRuns();
    }
  }
  async function runNow(schedule: Schedule) {
    setStarting((current) => new Set(current).add(schedule.id));
    try {
      const run = await api<Run>('schedules.run', { id: schedule.id });
      notify(
        run.status === 'success'
          ? `‘${schedule.name}’을(를) 실행했습니다.`
          : `‘${schedule.name}’ 실행에 실패했습니다: ${run.error}`,
        run.status === 'error',
      );
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setStarting((current) => new Set([...current].filter((id) => id !== schedule.id)));
      bumpRuns();
    }
  }
  return (
    <>
      <WidgetHead id="schedules" title="예약 실행" count={schedules?.length}>
        <button
          className="button secondary small"
          disabled={!prompts.length || !!editing}
          onClick={() => (configured ? setEditing(scheduleDraft(prompts[0].id)) : openAdmin('llm'))}
        >
          <Plus size={14} />새 예약
        </button>
      </WidgetHead>
      {editing ? (
        <ScheduleForm
          draft={editing}
          prompts={prompts}
          onClose={() => setEditing(null)}
          onSaved={(schedule) => {
            setEditing(null);
            bumpRuns();
            notify(
              schedule.next_run_at
                ? `예약을 저장했습니다. ${dayTime(schedule.next_run_at)}에 실행합니다.`
                : '예약을 저장했습니다.',
            );
          }}
        />
      ) : schedules === null ? (
        <div className="widget-loading">
          <LoaderCircle className="spin" size={20} />
        </div>
      ) : !schedules.length ? (
        <p className="widget-note">
          매일 아침 업무 정리, 매주 월요일 주간 보고 초안처럼 반복되는 요청을 예약해 두면 정해진
          시간에 실행하고 결과를 실행 기록에 남깁니다.
        </p>
      ) : (
        <ul className="schedule-list">
          {schedules.map((schedule) => {
            const live = running.has(schedule.id) || schedule.last_status === 'running';
            const finished =
              schedule.frequency === 'once' && !schedule.enabled && !!schedule.last_run_at;
            return (
              <li key={schedule.id} className={`schedule-row${schedule.enabled ? '' : ' paused'}`}>
                <label className="switch">
                  <input
                    type="checkbox"
                    role="switch"
                    checked={flipped[schedule.id] ?? schedule.enabled}
                    disabled={!!busy || finished}
                    aria-label={`${schedule.name} 예약 켜기`}
                    onChange={(e) => {
                      const enabled = e.target.checked;
                      setFlipped({ ...flipped, [schedule.id]: enabled });
                      void act(`toggle:${schedule.id}`, () =>
                        api('schedules.toggle', { id: schedule.id, enabled }),
                      );
                    }}
                  />
                  <span aria-hidden="true" />
                </label>
                <div className="schedule-main">
                  <strong>{schedule.name}</strong>
                  <small>
                    {schedule.prompt_title} · {describeSchedule(schedule)}
                  </small>
                  <small>
                    {schedule.next_run_at
                      ? `다음 실행 ${dayTime(schedule.next_run_at)} (${relative(schedule.next_run_at, now)})`
                      : finished
                        ? '실행을 마쳤습니다'
                        : '일시정지됨'}
                    {live ? (
                      <span className="run-badge running">
                        <LoaderCircle className="spin" size={11} />
                        실행 중
                      </span>
                    ) : (
                      schedule.last_run_at && (
                        <span className={`run-badge ${schedule.last_status}`}>
                          {schedule.last_status === 'success' ? (
                            <Check size={11} />
                          ) : (
                            <AlertCircle size={11} />
                          )}
                          {schedule.last_status === 'success' ? '성공' : '실패'}
                        </span>
                      )
                    )}
                  </small>
                  {schedule.last_status === 'error' && schedule.last_error && !live && (
                    <small className="danger">{schedule.last_error}</small>
                  )}
                </div>
                <div className="row-actions">
                  <button
                    className="icon-button"
                    aria-label={`${schedule.name} 지금 실행`}
                    title="지금 실행"
                    disabled={!!busy || live || starting.has(schedule.id)}
                    onClick={() => (configured ? void runNow(schedule) : openAdmin('llm'))}
                  >
                    {starting.has(schedule.id) ? (
                      <LoaderCircle className="spin" size={15} />
                    ) : (
                      <Play size={15} />
                    )}
                  </button>
                  <button
                    className="icon-button"
                    aria-label={`${schedule.name} 수정`}
                    title="수정"
                    disabled={!!busy}
                    // Editing a one-time schedule that already ran re-arms it for its new time.
                    onClick={() =>
                      setEditing({
                        ...schedule,
                        enabled:
                          schedule.enabled ||
                          (schedule.frequency === 'once' && !!schedule.last_run_at),
                      })
                    }
                  >
                    <Pencil size={15} />
                  </button>
                  <Confirm
                    iconOnly
                    name={`${schedule.name} 삭제`}
                    label="삭제"
                    icon={<Trash2 size={15} />}
                    question="예약을 삭제할까요? 실행 기록은 남습니다."
                    disabled={!!busy}
                    onConfirm={() =>
                      act('delete', async () => {
                        await api('schedules.delete', { id: schedule.id });
                        notify('예약을 삭제했습니다.');
                      })
                    }
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <small className="widget-foot">
        예약은 앱이 켜져 있을 때 실행됩니다. 앱이 꺼져 있던 동안 놓친 예약은 다음 실행 때 한 번만
        실행합니다.
      </small>
    </>
  );
}
