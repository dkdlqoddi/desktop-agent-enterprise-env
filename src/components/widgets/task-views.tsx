import { useState } from 'react';
import {
  ArrowRight,
  CalendarRange,
  Check,
  CircleCheck,
  Pause,
  Play,
  RotateCcw,
  type LucideIcon,
} from 'lucide-react';
import { api, clock, date, localDay, type Task } from '@/lib/api';
import { useApp } from '../app';
import { useWidgetFrame, WidgetHead } from '../dashboard';
import EmptyState from '../empty-state';
import { useNow } from '../run-history';
import { Loaded, useAssistantData } from './assistant-data';
import { Bars, Columns } from './parts';
import { QuickAdd } from './tasks';

type Status = Task['status'];

// Saves one change to the tasks and reloads them, reporting a failure; the formats share it.
function useTaskAction() {
  const { notify } = useApp();
  const { refresh } = useAssistantData();
  const [busy, setBusy] = useState(false);
  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      await refresh();
      return true;
    } catch (e) {
      notify((e as Error).message, true);
      return false;
    } finally {
      setBusy(false);
    }
  }
  const setStatus = (task: Task, status: Status) =>
    act(() => api('tasks.save', { id: task.id, status, expected_updated_at: task.updated_at }));
  return { busy, act, setStatus };
}

const overdue = (task: Task, now: number) =>
  task.status === 'open' && !!task.due_at && new Date(task.due_at).getTime() < now;

// Open tasks to tick off, the most urgent first (the list comes ordered by deadline); a line adds one.
export function TaskChecklist() {
  const { navigate } = useApp();
  const { tasks, briefing } = useAssistantData();
  const { size } = useWidgetFrame('tasks');
  const { busy, act, setStatus } = useTaskAction();
  const now = useNow(60_000);
  const open = tasks.filter((task) => task.status === 'open');
  const limit = size === 's' ? 6 : 10;
  const finished = briefing?.completed_today.length ?? 0;
  return (
    <>
      <WidgetHead count={open.length} />
      <QuickAdd
        label="체크리스트에 할 일 추가"
        placeholder="할 일을 입력하고 Enter"
        busy={busy}
        act={act}
      />
      <Loaded>
        {open.length ? (
          <ul className="checklist">
            {open.slice(0, limit).map((task) => (
              <li key={task.id}>
                <button
                  className="task-check"
                  aria-label={`${task.title} 완료`}
                  disabled={busy}
                  onClick={() => void setStatus(task, 'done')}
                />
                <span className="checklist-title">{task.title}</span>
                {task.due_at && (
                  <small className={overdue(task, now) ? 'overdue' : undefined}>
                    {overdue(task, now) ? '기한 지남 · ' : ''}
                    {date(task.due_at)}
                  </small>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState icon={CircleCheck} title="모두 끝냈어요">
            새 할 일을 입력하고 Enter를 누르세요.
          </EmptyState>
        )}
        <div className="widget-foot-row">
          {finished > 0 && <small>오늘 {finished}개 완료</small>}
          {open.length > limit && (
            <button className="text-button" onClick={() => navigate('tasks')}>
              외 {open.length - limit}개 모두 보기 <ArrowRight size={13} />
            </button>
          )}
        </div>
      </Loaded>
    </>
  );
}

const COLUMNS: [Status, string][] = [
  ['open', '진행 중'],
  ['paused', '보류'],
  ['done', '완료'],
];
const MOVES: Record<Status, [Status, string, LucideIcon][]> = {
  open: [
    ['done', '완료', Check],
    ['paused', '보류', Pause],
  ],
  paused: [
    ['open', '다시 진행', Play],
    ['done', '완료', Check],
  ],
  done: [['open', '다시 진행', RotateCcw]],
};

// Tasks in columns by status; each card moves to the next column with a button.
export function TaskBoard() {
  const { tasks } = useAssistantData();
  const { busy, setStatus } = useTaskAction();
  const now = useNow(60_000);
  return (
    <>
      <WidgetHead count={tasks.length} />
      <Loaded>
        <div className="task-board">
          {COLUMNS.map(([status, label]) => {
            const items = tasks.filter((task) => task.status === status);
            // Finished tasks show the latest few; the others as many as fit a column.
            const shown =
              status === 'done'
                ? [...items]
                    .sort((a, b) => (b.completed_at ?? '').localeCompare(a.completed_at ?? ''))
                    .slice(0, 5)
                : items.slice(0, 12);
            return (
              <div key={status} className={`board-column ${status}`}>
                <h3>
                  {label} <span className="count">{items.length}</span>
                </h3>
                <ul>
                  {shown.map((task) => (
                    <li key={task.id} className="board-card">
                      <strong>{task.title}</strong>
                      <small className={overdue(task, now) ? 'overdue' : undefined}>
                        {task.due_at ? `마감 ${date(task.due_at)}` : '기한 없음'}
                        {task.assignee && ` · ${task.assignee}`}
                      </small>
                      <span className="board-actions">
                        {MOVES[status].map(([to, action, Icon]) => (
                          <button
                            key={to}
                            className="icon-button"
                            aria-label={`${task.title} ${action}`}
                            title={action}
                            disabled={busy}
                            onClick={() => void setStatus(task, to)}
                          >
                            <Icon size={14} />
                          </button>
                        ))}
                      </span>
                    </li>
                  ))}
                  {items.length > shown.length && (
                    <li className="more">외 {items.length - shown.length}개</li>
                  )}
                  {!items.length && <li className="board-empty">없음</li>}
                </ul>
              </div>
            );
          })}
        </div>
      </Loaded>
    </>
  );
}

const WEEKDAY = new Intl.DateTimeFormat('ko-KR', { weekday: 'short' });

// The deadlines of the next seven days, a column per day, with what is already overdue before them.
export function TaskWeek() {
  const { tasks } = useAssistantData();
  const now = useNow(60_000);
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const days = Array.from(
    { length: 7 },
    (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i),
  );
  const keys = days.map(localDay);
  const week = tasks.filter(
    (task) => task.status !== 'paused' && task.due_at && keys.includes(localDay(task.due_at)),
  );
  const late = tasks.filter(
    (task) =>
      task.status === 'open' && task.due_at && new Date(task.due_at).getTime() < start.getTime(),
  );
  const undated = tasks.filter((task) => task.status === 'open' && !task.due_at).length;
  return (
    <>
      <WidgetHead count={week.filter((task) => task.status === 'open').length} />
      <Loaded>
        {late.length > 0 && (
          <p className="week-late" role="note">
            <strong>지난 마감 {late.length}개</strong>
            {late
              .slice(0, 3)
              .map((task) => task.title)
              .join(' · ')}
            {late.length > 3 && ` 외 ${late.length - 3}개`}
          </p>
        )}
        {!week.length && !late.length ? (
          <EmptyState icon={CalendarRange} title="이번 주 마감이 없어요">
            할 일에 마감을 정하면 날짜별로 여기에 놓입니다.
          </EmptyState>
        ) : (
          <ol className="week-strip">
            {days.map((day, index) => {
              const items = week.filter((task) => localDay(task.due_at!) === keys[index]);
              return (
                <li key={keys[index]} className={index === 0 ? 'today' : undefined}>
                  <h3>
                    <span>{index === 0 ? '오늘' : WEEKDAY.format(day)}</span>
                    <strong>{day.getDate()}</strong>
                  </h3>
                  {items.length ? (
                    <ul>
                      {items.map((task) => (
                        <li
                          key={task.id}
                          className={
                            task.status === 'done'
                              ? 'done'
                              : overdue(task, now)
                                ? 'overdue'
                                : undefined
                          }
                        >
                          <time dateTime={task.due_at!}>{clock(task.due_at!)}</time>
                          <span>{task.title}</span>
                          {task.status === 'done' && <span className="sr-only">완료</span>}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="week-free">마감 없음</p>
                  )}
                </li>
              );
            })}
          </ol>
        )}
        {undated > 0 && (
          <small className="widget-foot">
            기한을 정하지 않은 할 일 {undated}개는 빠져 있습니다.
          </small>
        )}
      </Loaded>
    </>
  );
}

// How many tasks are in each state, and how many were finished each of the last seven days.
export function TaskChart() {
  const { tasks } = useAssistantData();
  const now = useNow(60_000);
  const today = new Date(now);
  const week = Array.from(
    { length: 7 },
    (_, i) => new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6 + i),
  );
  const finished = (day: Date) =>
    tasks.filter(
      (task) =>
        task.status === 'done' &&
        task.completed_at &&
        localDay(task.completed_at) === localDay(day),
    ).length;
  return (
    <>
      <WidgetHead count={tasks.length} />
      <Loaded>
        <Bars
          label="상태별 할 일"
          items={COLUMNS.map(([status, label]) => ({
            key: status,
            label,
            value: tasks.filter((task) => task.status === status).length,
          }))}
        />
        <h3 className="chart-caption">최근 7일 완료</h3>
        <Columns
          items={week.map((day, index) => {
            const value = finished(day);
            const name = index === 6 ? '오늘' : `${day.getMonth() + 1}/${day.getDate()}`;
            return { key: localDay(day), label: name, value, text: `${name} ${value}개 완료` };
          })}
        />
      </Loaded>
    </>
  );
}
