import { useState } from 'react';
import { CircleCheck, LoaderCircle, Sparkles } from 'lucide-react';
import { api, date, type Task } from '@/lib/api';
import { useApp } from '../app';
import { WidgetHead } from '../dashboard';
import EmptyState from '../empty-state';
import Markdown from '../markdown';
import { LoadError, useAssistantData } from './assistant-data';
import { Loading, Metrics, Ring } from './parts';

export default function BriefingWidget() {
  const { configured, openAdmin, notify } = useApp();
  const { briefing, error } = useAssistantData();
  const [summary, setSummary] = useState<{
    content: string;
    generated_at: string;
    omitted_tasks: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const groups: [string, Task[]][] = briefing
    ? [
        ['기한이 지난 일', briefing.overdue],
        ['오늘 마감', briefing.today],
        ['다가오는 마감', briefing.upcoming],
      ]
    : [];
  return (
    <>
      <WidgetHead id="briefing" title="오늘의 브리핑">
        <button
          className="button secondary small"
          disabled={busy || !briefing}
          onClick={async () => {
            if (!configured) return openAdmin('llm');
            setBusy(true);
            try {
              setSummary(
                await api('briefing.generate', { timezone_offset: new Date().getTimezoneOffset() }),
              );
            } catch (e) {
              notify((e as Error).message, true);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}
          {busy ? '요약하는 중…' : 'AI 브리핑 생성'}
        </button>
      </WidgetHead>
      <LoadError />
      {!briefing ? (
        !error && (
          <div className="widget-loading">
            <LoaderCircle className="spin" size={20} />
          </div>
        )
      ) : (
        <>
          <dl className="metrics">
            <div>
              <dt>진행 중</dt>
              <dd>{briefing.counts.open}</dd>
            </div>
            <div>
              <dt>오늘 마감</dt>
              <dd>{briefing.today.length}</dd>
            </div>
            <div className={briefing.overdue.length ? 'alert' : ''}>
              <dt>기한 지남</dt>
              <dd className="overdue-count">{briefing.overdue.length}</dd>
            </div>
            <div>
              <dt>오늘 완료</dt>
              <dd>{briefing.completed_today.length}</dd>
            </div>
          </dl>
          {summary ? (
            <div className="briefing-summary">
              <Markdown>{summary.content}</Markdown>
              <small>
                {date(summary.generated_at)} 생성 · 업무가 바뀌었다면 다시 생성하세요.
                {summary.omitted_tasks > 0 &&
                  ` 항목이 많아 ${summary.omitted_tasks}건은 요약에서 뺐습니다.`}
              </small>
            </div>
          ) : (
            groups.every(([, items]) => !items.length) && (
              <p className="widget-note">
                {briefing.counts.open
                  ? '기한이 정해진 진행 중 업무가 없습니다.'
                  : '진행 중인 할 일이 없습니다. 할 일을 추가하거나 회의록에서 추출해 보세요.'}
              </p>
            )
          )}
          {groups
            .filter(([, items]) => items.length)
            .map(([label, items]) => (
              <div className="briefing-group" key={label}>
                <h3>
                  {label} <span className="count">{items.length}</span>
                </h3>
                <ul>
                  {items.slice(0, 4).map((task) => (
                    <li key={task.id}>
                      <strong>{task.title}</strong>
                      <small>
                        {task.due_at ? date(task.due_at) : '기한 미지정'}
                        {task.assignee && ` · ${task.assignee}`}
                      </small>
                    </li>
                  ))}
                  {items.length > 4 && <li className="more">외 {items.length - 4}건</li>}
                </ul>
              </div>
            ))}
        </>
      )}
    </>
  );
}

// The briefing's four numbers alone.
export function BriefingStats() {
  const { briefing, error } = useAssistantData();
  return (
    <>
      <WidgetHead />
      <LoadError />
      {briefing ? (
        <Metrics
          items={[
            { label: '진행 중', value: briefing.counts.open },
            { label: '오늘 마감', value: briefing.today.length },
            {
              label: '기한 지남',
              value: briefing.overdue.length,
              alert: briefing.overdue.length > 0,
            },
            { label: '오늘 완료', value: briefing.completed_today.length },
          ]}
        />
      ) : (
        !error && <Loading />
      )}
    </>
  );
}

// How much of today's work is done: what was finished today, of that and what is due today or overdue.
export function BriefingProgress() {
  const { briefing, error } = useAssistantData();
  const done = briefing?.completed_today.length ?? 0;
  const left = (briefing?.today.length ?? 0) + (briefing?.overdue.length ?? 0);
  return (
    <>
      <WidgetHead />
      <LoadError />
      {!briefing ? (
        !error && <Loading />
      ) : done + left ? (
        <div className="progress-card">
          <Ring value={done} total={done + left} label="오늘 처리할 일 중 끝낸 일" />
          <dl className="progress-legend">
            <div>
              <dt>오늘 끝낸 일</dt>
              <dd>{done}</dd>
            </div>
            <div>
              <dt>오늘 마감</dt>
              <dd>{briefing.today.length}</dd>
            </div>
            <div className={briefing.overdue.length ? 'alert' : undefined}>
              <dt>기한 지남</dt>
              <dd>{briefing.overdue.length}</dd>
            </div>
          </dl>
        </div>
      ) : (
        <EmptyState icon={CircleCheck} title="오늘 처리할 마감이 없어요">
          마감이 오늘이거나 지난 할 일과 오늘 끝낸 일이 여기에 모입니다.
        </EmptyState>
      )}
    </>
  );
}
