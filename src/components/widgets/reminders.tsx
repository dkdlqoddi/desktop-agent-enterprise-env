import { useState } from 'react';
import { Bell, BellRing } from 'lucide-react';
import EmptyState from '../empty-state';
import { api, clock, date, localDay, relative, type Task } from '@/lib/api';
import { useApp } from '../app';
import { WidgetHead } from '../dashboard';
import { useNow } from '../run-history';
import { Loaded, useAssistantData } from './assistant-data';
import { Agenda, Metrics } from './parts';

export default function RemindersWidget() {
  const { notify } = useApp();
  const { briefing, notification, refresh } = useAssistantData();
  const [busy, setBusy] = useState(false);
  const reminders = briefing?.reminders ?? [];
  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      await refresh();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <WidgetHead id="reminders" title="알림" count={reminders.length} />
      {/* What the main process says now: an error it has since cleared goes with the next load. */}
      {notification && (!notification.supported || notification.error) && (
        <p className="inline-error">
          {notification.error ||
            '이 환경에서는 시스템 알림을 표시할 수 없습니다. 도래한 알림은 여기에서 확인하세요.'}
        </p>
      )}
      <Loaded>
        {!reminders.length && (
          <EmptyState icon={Bell} title="모두 확인했어요">
            다가오는 알림이 여기에 표시됩니다.
          </EmptyState>
        )}
      </Loaded>
      <ul className="reminder-list">
        {reminders.map((task) => (
          <li className="reminder-row" key={task.id}>
            <div>
              <strong>{task.title}</strong>
              <small>{date(task.snoozed_until ?? task.remind_at!)}</small>
            </div>
            <button
              className="button secondary small"
              disabled={busy}
              onClick={() => void act(() => api('tasks.snooze', { id: task.id, minutes: 10 }))}
            >
              10분 뒤
            </button>
            <button
              className="button primary small"
              disabled={busy}
              onClick={() =>
                void act(() => api('reminders.ack', { id: task.id, remind_at: task.remind_at }))
              }
            >
              확인
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

const REPEAT = { none: '', daily: '매일', weekly: '매주' };

// The reminders still to come: an open task's next one is at its snooze or reminder time, until it has been shown.
function upcoming(tasks: Task[], now: number) {
  return tasks
    .filter((task) => task.status === 'open' && task.remind_at && !task.notified_at)
    .map((task) => ({ task, at: task.snoozed_until ?? task.remind_at! }))
    .filter(({ at }) => new Date(at).getTime() > now)
    .sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime());
}

// The reminders due now, then the coming ones under their days.
export function ReminderAgenda() {
  const { tasks, briefing } = useAssistantData();
  const now = useNow(60_000);
  const due = briefing?.reminders ?? [];
  const next = upcoming(tasks, now).slice(0, 12);
  return (
    <>
      <WidgetHead count={due.length + next.length} />
      <Loaded>
        {due.length + next.length ? (
          <Agenda
            items={[
              ...due.map((task) => ({
                id: task.id,
                at: task.snoozed_until ?? task.remind_at!,
                title: task.title,
                meta: '확인하지 않은 알림',
                group: '지금',
                alert: true,
              })),
              ...next.map(({ task, at }) => ({
                id: task.id,
                at,
                title: task.title,
                meta: [REPEAT[task.repeat], task.due_at && `마감 ${date(task.due_at)}`]
                  .filter(Boolean)
                  .join(' · '),
              })),
            ]}
          />
        ) : (
          <EmptyState icon={BellRing} title="예정된 알림이 없어요">
            할 일에 알림 시간을 정하면 여기에 차례로 표시됩니다.
          </EmptyState>
        )}
      </Loaded>
    </>
  );
}

// How many reminders wait to be checked, how many are left today, and when the next one comes.
export function ReminderStats() {
  const { tasks, briefing } = useAssistantData();
  const now = useNow(60_000);
  const next = upcoming(tasks, now);
  const today = localDay(now);
  const due = briefing?.reminders.length;
  return (
    <>
      <WidgetHead />
      <Loaded>
        <Metrics
          items={[
            { label: '확인할 알림', value: due ?? '—', alert: !!due },
            {
              label: '오늘 남은 알림',
              value: next.filter(({ at }) => localDay(at) === today).length,
            },
            {
              label: '다음 알림',
              value: next[0] ? clock(next[0].at) : '없음',
              note: next[0] && `${relative(next[0].at, now)} · ${next[0].task.title}`,
            },
          ]}
        />
      </Loaded>
    </>
  );
}
