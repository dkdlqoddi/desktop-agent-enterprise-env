import { useState } from 'react';
import { BellRing, Check, CircleCheck, LoaderCircle, Plus, Sparkles, Trash2 } from 'lucide-react';
import EmptyState from '../empty-state';
import { api, date, type Task } from '@/lib/api';
import { useActions, useConfigured, useDocuments } from '../state';
import Confirm from '../confirm';
import { WidgetHead } from '../dashboard';
import { blankTask, TaskFields } from '../task-editor';
import { Loaded, useAssistantData } from './assistant-data';

const FILTERS = { open: '진행 중', paused: '보류', done: '완료', all: '전체' };

// One line that adds a task. The box stays enabled and keeps its focus while the task saves (a disabled box lost the
// focus, and the keys typed next went nowhere): it empties at once for the next task, Enter does nothing while a change
// is saving (what is typed stays), and a title that could not be saved comes back.
export function QuickAdd({
  label,
  placeholder,
  hint,
  busy,
  act,
  onAdded,
}: {
  label: string;
  placeholder: string;
  hint?: string;
  busy: boolean;
  act: (action: () => Promise<unknown>) => Promise<boolean>;
  onAdded?: () => void;
}) {
  const [title, setTitle] = useState('');
  return (
    <form
      className="quick-add"
      onSubmit={(event) => {
        event.preventDefault();
        const text = title.trim();
        if (!text || busy) return;
        setTitle('');
        void act(async () => {
          try {
            await api('tasks.save', { ...blankTask(), title: text });
          } catch (error) {
            setTitle((current) => current || text);
            throw error;
          }
          onAdded?.();
        });
      }}
    >
      <Plus size={17} aria-hidden="true" />
      <input
        aria-label={label}
        placeholder={placeholder}
        title={hint}
        maxLength={200}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
    </form>
  );
}

// The fields of the task being edited, with every document its source field offers: the documents load while a task
// is edited, not with the list of tasks.
function EditedTaskFields(props: { task: Task; onChange: (task: Task) => void }) {
  return <TaskFields {...props} documents={useDocuments()} />;
}

export default function TasksWidget() {
  const configured = useConfigured();
  const { openAdmin, notify } = useActions();
  const { tasks, refresh } = useAssistantData();
  const [filter, setFilter] = useState<keyof typeof FILTERS>('open');
  const [editing, setEditing] = useState<Task | null>(null);
  const [extracting, setExtracting] = useState(false);
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
  const visible = tasks.filter((task) => filter === 'all' || task.status === filter);
  const open = tasks.filter((task) => task.status === 'open').length;
  return (
    <>
      <WidgetHead id="tasks" title="할 일" count={open}>
        <select
          aria-label="할 일 상태"
          value={filter}
          onChange={(e) => setFilter(e.target.value as keyof typeof FILTERS)}
        >
          {Object.entries(FILTERS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <button
          className="icon-button"
          aria-label="할 일 추출"
          title="회의록·메일에서 AI로 할 일 추출"
          aria-pressed={extracting}
          onClick={() => (configured ? setExtracting(!extracting) : openAdmin('llm'))}
        >
          <Sparkles size={16} />
        </button>
      </WidgetHead>
      {extracting ? (
        <ExtractTasks
          onClose={() => setExtracting(false)}
          onSaved={async (count) => {
            await refresh();
            setExtracting(false);
            notify(`할 일 ${count}개를 저장했습니다.`);
          }}
        />
      ) : (
        <>
          <QuickAdd
            label="새 할 일"
            placeholder="새 할 일을 입력하고 Enter"
            hint="추가한 할 일을 누르면 마감과 알림을 설정할 수 있습니다"
            busy={busy}
            act={act}
            onAdded={() => setFilter((current) => (current === 'all' ? current : 'open'))}
          />
          <Loaded>
            {!visible.length && (
              <EmptyState
                icon={CircleCheck}
                title={
                  filter === 'open'
                    ? '가벼운 마음으로 시작해요'
                    : `${FILTERS[filter]} 할 일이 없습니다`
                }
              >
                {filter === 'open'
                  ? '오늘 집중할 일을 하나씩 추가해 보세요.'
                  : '다른 상태를 선택해 할 일을 확인하세요.'}
              </EmptyState>
            )}
          </Loaded>
          <ul className="task-list">
            {visible.map((task) => {
              const overdue =
                task.status === 'open' &&
                !!task.due_at &&
                new Date(task.due_at).getTime() < Date.now();
              return (
                <li key={task.id} className={`task-row${task.status === 'done' ? ' done' : ''}`}>
                  <div className="task-line">
                    <button
                      className="task-check"
                      aria-label={`${task.title} ${task.status === 'done' ? '다시 진행' : '완료'}`}
                      disabled={busy}
                      onClick={() =>
                        void act(() =>
                          api('tasks.save', {
                            id: task.id,
                            status: task.status === 'done' ? 'open' : 'done',
                            expected_updated_at: task.updated_at,
                          }),
                        )
                      }
                    >
                      {task.status === 'done' && <Check size={13} />}
                    </button>
                    <button
                      className="task-open"
                      aria-expanded={editing?.id === task.id}
                      onClick={() => setEditing(editing?.id === task.id ? null : task)}
                    >
                      <strong>{task.title}</strong>
                      <small>
                        {task.assignee || '담당자 미지정'}
                        {task.due_at && (
                          <span className={overdue ? 'overdue' : ''}>
                            {' '}
                            · 마감 {date(task.due_at)}
                          </span>
                        )}
                        {task.remind_at && task.status === 'open' && (
                          <span>
                            {' '}
                            · 알림 {date(task.snoozed_until ?? task.remind_at)}
                            {task.repeat === 'daily'
                              ? ' 매일'
                              : task.repeat === 'weekly'
                                ? ' 매주'
                                : ''}
                          </span>
                        )}
                        {task.source_title && <span> · {task.source_title}</span>}
                      </small>
                    </button>
                  </div>
                  {editing?.id === task.id && (
                    <form
                      className="task-form"
                      onSubmit={(event) => {
                        event.preventDefault();
                        void act(async () => {
                          await api('tasks.save', {
                            ...editing,
                            expected_updated_at: editing.updated_at,
                          });
                          setEditing(null);
                        });
                      }}
                    >
                      <fieldset disabled={busy}>
                        <EditedTaskFields task={editing} onChange={setEditing} />
                        <div className="form-actions">
                          <Confirm
                            label="삭제"
                            icon={<Trash2 size={14} />}
                            question="이 할 일과 알림을 삭제할까요?"
                            onConfirm={() =>
                              void act(async () => {
                                await api('tasks.delete', { id: task.id });
                                setEditing(null);
                              })
                            }
                          />
                          {task.status === 'open' && task.remind_at && (
                            <button
                              type="button"
                              className="button secondary small"
                              onClick={() =>
                                void act(async () => {
                                  await api('tasks.snooze', { id: task.id, minutes: 60 });
                                  setEditing(null);
                                })
                              }
                            >
                              <BellRing size={14} />
                              1시간 뒤 알림
                            </button>
                          )}
                          <span className="spacer" />
                          <button
                            type="button"
                            className="button secondary small"
                            onClick={() => setEditing(null)}
                          >
                            취소
                          </button>
                          <button className="button primary small">할 일 저장</button>
                        </div>
                      </fieldset>
                    </form>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </>
  );
}

// Candidate tasks from a document or pasted text; nothing is saved until the user picks them.
function ExtractTasks({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: (count: number) => Promise<void>;
}) {
  const documents = useDocuments();
  const [source, setSource] = useState('');
  const [content, setContent] = useState('');
  const [drafts, setDrafts] = useState<Task[] | null>(null);
  // What the extraction left out (candidates without evidence in the text) or read only in part.
  const [notice, setNotice] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <form
      className="extract"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        setError('');
        try {
          if (drafts === null) {
            const result = await api<{ tasks: Task[]; notice?: string }>('tasks.extract', {
              source_id: source || null,
              content,
              timezone_offset: new Date().getTimezoneOffset(),
            });
            setNotice(result.notice ?? '');
            setDrafts(result.tasks);
            setSelected(new Set(result.tasks.map((task) => task.id)));
          } else {
            const chosen = drafts.filter((task) => selected.has(task.id));
            await api('tasks.save_many', { tasks: chosen });
            await onSaved(chosen.length);
          }
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <fieldset disabled={busy}>
        {drafts === null ? (
          <>
            <label>
              추출할 문서
              <select value={source} onChange={(e) => setSource(e.target.value)}>
                <option value="">직접 입력</option>
                {documents.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title}
                  </option>
                ))}
              </select>
            </label>
            {!source && (
              <label>
                회의록·메일 또는 알림 요청
                <textarea
                  required
                  rows={5}
                  maxLength={32000}
                  placeholder="예: 내일 오전 9시에 주간 보고서 작성을 알려줘."
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                />
              </label>
            )}
            <small>
              날짜만 있는 마감은 오후 6시, 시간이 없는 알림 요청은 오전 9시로 제안합니다. 저장하기
              전에 모든 항목을 검토할 수 있습니다.
            </small>
          </>
        ) : drafts.length === 0 ? (
          <p className="widget-note">
            할 일을 찾지 못했습니다. 담당 업무나 요청이 있는 내용을 넣어 주세요.
          </p>
        ) : (
          drafts.map((task, i) => (
            <section className="task-candidate" key={task.id}>
              <label className="check">
                <input
                  type="checkbox"
                  checked={selected.has(task.id)}
                  onChange={(e) =>
                    setSelected((current) => {
                      const next = new Set(current);
                      if (e.target.checked) next.add(task.id);
                      else next.delete(task.id);
                      return next;
                    })
                  }
                />
                이 항목 저장
              </label>
              <blockquote>{task.evidence}</blockquote>
              <fieldset disabled={!selected.has(task.id)}>
                <TaskFields
                  task={task}
                  documents={documents}
                  onChange={(next) =>
                    setDrafts((current) => current!.map((t, index) => (index === i ? next : t)))
                  }
                />
              </fieldset>
            </section>
          ))
        )}
        {drafts !== null && notice && <p className="widget-note">{notice}</p>}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="form-actions">
          <button
            type="button"
            className="button secondary small"
            onClick={() => (drafts === null ? onClose() : setDrafts(null))}
          >
            {drafts === null ? '닫기' : '다시 입력'}
          </button>
          <span className="spacer" />
          <button
            className="button primary small"
            disabled={drafts !== null && selected.size === 0}
          >
            {busy && <LoaderCircle className="spin" size={14} />}
            {drafts === null ? 'AI로 추출' : `선택한 ${selected.size}개 저장`}
          </button>
        </div>
      </fieldset>
    </form>
  );
}
