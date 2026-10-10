import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  BellRing,
  BrainCircuit,
  ChartGantt,
  Check,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileDiff,
  FileText,
  FolderSync,
  LoaderCircle,
  NotebookPen,
  Pencil,
  Play,
  Plus,
  Radar,
  Square,
  Trash2,
  Workflow,
  type LucideIcon,
} from 'lucide-react';
import {
  api,
  date,
  dayTime,
  relative,
  type Routine,
  type RoutineItem,
  type RoutineRecipe,
  type RoutineRun,
} from '@/lib/api';
import Confirm from '../confirm';
import { WidgetHead } from '../dashboard';
import Diagnosis from '../diagnosis';
import EmptyState from '../empty-state';
import Markdown from '../markdown';
import RoutineForm, {
  describeRoutine,
  draftFrom,
  draftOf,
  RecipeNeeds,
  type RoutineDraft,
} from '../routine-form';
import { useNow } from '../run-history';
import { useActions, useQuery, useRoutineFocus } from '../state';
import { Loading, Metrics } from './parts';

export const ROUTINE_ICONS: Record<string, LucideIcon> = {
  confluence_watch: FileDiff,
  jira_watch: Radar,
  jira_filter: BellRing,
  jira_project: ChartGantt,
  source_refresh: FolderSync,
  analyze_new: BrainCircuit,
  work_report: NotebookPen,
};
const STATUS: Record<NonNullable<Routine['last_status']>, string> = {
  queued: '대기 중',
  running: '실행 중',
  success: '완료',
  error: '실패',
  cancelled: '중지',
};
const ITEM_STATUS: Record<RoutineItem['status'], string> = {
  new: '새로',
  found: '찾음',
  changed: '바뀜',
  edited: '고친 문서',
  removed: '빠짐',
  done: '완료',
  failed: '실패',
  missing: '원본 없음',
};

// The notification click (AppState.routineFocus) last shown, so the routine opens once and not on every visit.
let shownFocus = 0;

// The recipes do not change while the app runs: read once, and again only after a failed read.
let recipes: Promise<RoutineRecipe[]> | null = null;
function useCatalog() {
  const [catalog, setCatalog] = useState<RoutineRecipe[] | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    recipes ??= api<RoutineRecipe[]>('routines.catalog').catch((error) => {
      recipes = null;
      throw error;
    });
    recipes
      .then((found) => live && setCatalog(found))
      .catch((error) => live && setError((error as Error).message));
    return () => {
      live = false;
    };
  }, [attempt]);
  const retry = useCallback(() => {
    setError('');
    setAttempt((value) => value + 1);
  }, []);
  return { catalog, error, retry };
}

// The automatic routines: the ready-made jobs to pick from, each routine with its schedule and switch, what its runs
// found, and the form that sets one up, in place of the list.
export default function RoutinesWidget() {
  const routineFocus = useRoutineFocus();
  const { notify, invalidate, watchJobs } = useActions();
  const { catalog, error: catalogError, retry: retryCatalog } = useCatalog();
  // The routines load with the widget and again when they change (a run ended, an AI tool changed one). While a run is
  // queued or running, the main process sends them as they change (electron/job-progress.cjs), with what the run is
  // doing, so the widget does not ask for them every few seconds.
  const { data, error: listError, reload: load } = useQuery<Routine[]>('routines.list');
  const routines = data ?? null;
  // Why the list could not be read, shown while there is none to show.
  const failed = routines === null && listError ? listError.message : '';
  const [mode, setMode] = useState<
    'list' | 'catalog' | { recipe: RoutineRecipe; draft: RoutineDraft }
  >('list');
  const [open, setOpen] = useState<string | null>(null);
  // The routine a notification asked for, shown once its row is on the list.
  const [reveal, setReveal] = useState<string | null>(null);
  const [busy, setBusy] = useState('');
  // A switch flips at once and settles once its own change has been saved and the list read again.
  const [flipped, setFlipped] = useState<Record<string, boolean>>({});
  const now = useNow(60_000);
  useEffect(() => {
    if (listError) notify(listError.message, true);
  }, [listError, notify]);
  // A run that waited or ran has ended: the routines' results load again.
  const active = !!routines?.some((routine) => routine.running || routine.queued);
  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && !active) void invalidate(['routines']).catch(() => {});
    wasActive.current = active;
  }, [active, invalidate]);
  const editing = typeof mode === 'object' ? mode : null;
  // A notification was clicked: show that routine's results. A form being filled in stays; they show once it closes.
  useEffect(() => {
    if (!routineFocus || routineFocus.n <= shownFocus) return;
    shownFocus = routineFocus.n;
    setOpen(routineFocus.id);
    setReveal(routineFocus.id);
    if (editing)
      notify('작성 중인 자동 작업은 그대로 두었습니다. 저장하거나 취소하면 결과가 보입니다.');
    else setMode('list');
  }, [routineFocus, notify, editing]);
  useEffect(() => {
    if (!reveal || !routines || !catalog || mode !== 'list') return;
    const row = document.querySelector(`[data-routine="${reveal}"]`);
    // A routine no longer on the list (deleted meanwhile) has nothing to show.
    if (row || !routines.some((routine) => routine.id === reveal)) setReveal(null);
    row?.scrollIntoView({ block: 'start', behavior: 'instant' });
  }, [reveal, routines, catalog, mode]);
  async function act(key: string, action: () => Promise<unknown>) {
    setBusy(key);
    try {
      await action();
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy('');
      await load().catch(() => {});
    }
  }
  const recipeOf = (kind: string) => catalog?.find((recipe) => recipe.kind === kind);
  const unread = (routines === null && failed) || (catalog === null && catalogError);
  return (
    <>
      <WidgetHead id="routines" title="자동 작업" count={routines?.length}>
        <button
          className="button secondary small"
          disabled={!catalog?.length || mode !== 'list'}
          onClick={() => setMode('catalog')}
        >
          <Plus size={14} />새 자동 작업
        </button>
      </WidgetHead>
      {editing ? (
        <RoutineForm
          key={editing.draft.id ?? editing.recipe.kind}
          recipe={editing.recipe}
          draft={editing.draft}
          onClose={() => setMode('list')}
          onSaved={(routine) => {
            setMode('list');
            setOpen(routine.id);
            void load().catch(() => {});
            // A new interval routine runs at its next check, within a minute.
            const soon =
              !!routine.next_run_at &&
              new Date(routine.next_run_at).getTime() < Date.now() + 60_000;
            notify(
              `‘${routine.name}’ 자동 작업을 저장했습니다.${
                routine.enabled && routine.next_run_at
                  ? soon
                    ? ' 곧 실행합니다.'
                    : ` ${dayTime(routine.next_run_at)}에 실행합니다.`
                  : ''
              }`,
            );
          }}
        />
      ) : mode === 'catalog' || (routines && !routines.length && catalog?.length) ? (
        <RoutineCatalog
          catalog={catalog ?? []}
          onPick={(recipe) => setMode({ recipe, draft: draftOf(recipe) })}
          onClose={routines?.length ? () => setMode('list') : undefined}
        />
      ) : unread ? (
        <p className="inline-error" role="alert">
          <AlertCircle size={14} />
          <span>
            자동 작업을 불러오지 못했습니다. {unread}{' '}
            <button
              type="button"
              className="text-button"
              onClick={() => {
                if (catalog === null) retryCatalog();
                if (routines !== null) return;
                void load().catch(() => {});
              }}
            >
              다시 시도
            </button>
          </span>
        </p>
      ) : routines === null || catalog === null ? (
        <Loading />
      ) : (
        <ul className="routine-list">
          {routines.map((routine) => {
            const recipe = recipeOf(routine.kind);
            const Icon = ROUTINE_ICONS[routine.kind] ?? Workflow;
            const live = routine.running || routine.queued;
            const expanded = open === routine.id;
            return (
              <li
                key={routine.id}
                data-routine={routine.id}
                className={`routine-row${routine.enabled ? '' : ' paused'}${expanded ? ' open' : ''}`}
                // A routine that brings files of this PC to the Wiki: AI tools may only start or switch it
                // with the "full" access level, as the import box.
                {...(routine.files ? { 'data-requires-full': '' } : {})}
              >
                <div className="routine-line">
                  <label className="switch">
                    <input
                      type="checkbox"
                      role="switch"
                      checked={flipped[routine.id] ?? routine.enabled}
                      disabled={!!busy}
                      aria-label={`${routine.name} 자동 작업 켜기`}
                      onChange={(e) => {
                        const enabled = e.target.checked;
                        setFlipped((current) => ({ ...current, [routine.id]: enabled }));
                        // Kept until the change's own reload: a list read or sent before it is saved has the old state.
                        void act(`toggle:${routine.id}`, () =>
                          api('routines.toggle', { id: routine.id, enabled }),
                        ).then(() =>
                          setFlipped((current) => {
                            const next = { ...current };
                            delete next[routine.id];
                            return next;
                          }),
                        );
                      }}
                    />
                    <span aria-hidden="true" />
                  </label>
                  <span className="routine-icon" aria-hidden="true">
                    <Icon size={17} />
                  </span>
                  <button
                    type="button"
                    className="routine-main"
                    aria-expanded={expanded}
                    aria-label={`${routine.name} 결과 ${expanded ? '닫기' : '보기'}`}
                    onClick={() => setOpen(expanded ? null : routine.id)}
                  >
                    <strong>
                      {routine.name}
                      {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </strong>
                    <small>
                      {routine.title} · {describeRoutine(routine)}
                      {' · '}
                      {routine.enabled && routine.next_run_at
                        ? `다음 ${dayTime(routine.next_run_at)} (${relative(routine.next_run_at, now)})`
                        : '꺼져 있음'}
                    </small>
                    <small className="routine-last">
                      {live ? (
                        <span className="run-badge running">
                          <LoaderCircle className="spin" size={11} />
                          {routine.running ? '실행 중' : '대기 중'}
                        </span>
                      ) : (
                        routine.last_status && (
                          <span
                            className={`run-badge ${routine.last_status === 'success' ? (routine.last_changed ? 'changed' : 'success') : routine.last_status}`}
                          >
                            {routine.last_status === 'success' ? (
                              <Check size={11} />
                            ) : (
                              <AlertCircle size={11} />
                            )}
                            {routine.last_status === 'success' && routine.last_changed
                              ? '새 소식'
                              : STATUS[routine.last_status]}
                          </span>
                        )
                      )}
                      <span>
                        {routine.running
                          ? routine.step || '실행하는 중'
                          : routine.last_run_at
                            ? `${date(routine.last_run_at)} · ${routine.last_summary}`
                            : '아직 실행하지 않았습니다'}
                      </span>
                    </small>
                    {routine.last_status === 'error' && routine.last_error && !live && (
                      <small className="danger">{routine.last_error}</small>
                    )}
                  </button>
                  <div className="row-actions">
                    {live ? (
                      <button
                        className="icon-button"
                        aria-label={`${routine.name} 중지`}
                        title="중지"
                        disabled={!!busy}
                        onClick={() =>
                          void act(`cancel:${routine.id}`, () =>
                            api('routines.cancel', { id: routine.id }),
                          )
                        }
                      >
                        <Square size={14} />
                      </button>
                    ) : (
                      <button
                        className="icon-button"
                        aria-label={`${routine.name} 지금 실행`}
                        title="지금 실행"
                        disabled={!!busy}
                        onClick={() =>
                          void act(`run:${routine.id}`, async () => {
                            await api('routines.run', { id: routine.id });
                            setOpen(routine.id);
                            // The import box shows the run's imports as 자동 작업 ‘이름’ while it lasts.
                            watchJobs();
                          })
                        }
                      >
                        {busy === `run:${routine.id}` ? (
                          <LoaderCircle className="spin" size={15} />
                        ) : (
                          <Play size={15} />
                        )}
                      </button>
                    )}
                    <button
                      className="icon-button"
                      aria-label={`${routine.name} 수정`}
                      title="수정"
                      disabled={!!busy || !recipe}
                      onClick={() => recipe && setMode({ recipe, draft: draftFrom(routine) })}
                    >
                      <Pencil size={15} />
                    </button>
                    <Confirm
                      iconOnly
                      name={`${routine.name} 삭제`}
                      label="삭제"
                      icon={<Trash2 size={15} />}
                      question="자동 작업을 삭제할까요? 만든 Wiki 문서는 남습니다."
                      disabled={!!busy}
                      onConfirm={() =>
                        act('delete', async () => {
                          await api('routines.delete', { id: routine.id });
                          notify('자동 작업을 삭제했습니다.');
                        })
                      }
                    />
                  </div>
                </div>
                {expanded && <RoutineResults routine={routine} />}
              </li>
            );
          })}
        </ul>
      )}
      <small className="widget-foot">
        자동 작업은 앱이 켜져 있을 때 한 번에 하나씩 실행합니다. JIRA·Confluence에는 관리 → 사이트
        계정의 계정으로 로그인합니다.
      </small>
    </>
  );
}

function RoutineCatalog({
  catalog,
  onPick,
  onClose,
}: {
  catalog: RoutineRecipe[];
  onPick: (recipe: RoutineRecipe) => void;
  onClose?: () => void;
}) {
  const groups = [...new Set(catalog.map((recipe) => recipe.group))];
  return (
    <div className="routine-catalog" role="region" aria-label="자동 작업 종류">
      <p className="widget-note">
        미리 만들어 둔 작업을 골라 주소·조건과 주기만 정하면, 앱이 그 주기마다 확인하고 찾은 것을
        알려 줍니다.
      </p>
      {groups.map((group) => (
        <section key={group}>
          <h3>{group}</h3>
          <div className="recipe-grid">
            {catalog
              .filter((recipe) => recipe.group === group)
              .map((recipe) => {
                const Icon = ROUTINE_ICONS[recipe.kind] ?? Workflow;
                return (
                  <button
                    key={recipe.kind}
                    type="button"
                    className="recipe-card"
                    onClick={() => onPick(recipe)}
                  >
                    <span className="routine-icon" aria-hidden="true">
                      <Icon size={18} />
                    </span>
                    <span>
                      <strong>{recipe.title}</strong>
                      <small>{recipe.summary}</small>
                      <RecipeNeeds recipe={recipe} />
                    </span>
                  </button>
                );
              })}
          </div>
        </section>
      ))}
      {onClose && (
        <div className="form-actions">
          <button type="button" className="button secondary small" onClick={onClose}>
            취소
          </button>
        </div>
      )}
    </div>
  );
}

// What a routine's latest runs found: the chosen run in full, the earlier ones by a line.
function RoutineResults({ routine }: { routine: Routine }) {
  const { notify } = useActions();
  const { data, error, reload } = useQuery<RoutineRun[]>('routines.runs', {
    id: routine.id,
    limit: 6,
  });
  const runs = data ?? null;
  const [chosen, setChosen] = useState(0);
  useEffect(() => setChosen(0), [data]);
  useEffect(() => {
    if (error) notify(error.message, true);
  }, [error, notify]);
  // The routine's last run changed (one started or ended): its runs load again.
  const last = `${routine.last_run_at}|${routine.last_status}`;
  const seen = useRef(last);
  useEffect(() => {
    if (seen.current === last) return;
    seen.current = last;
    void reload().catch(() => {});
  }, [last, reload]);
  if (runs === null) return <Loading />;
  const run = runs[chosen];
  return (
    <div className="routine-results">
      {routine.documents.length > 0 && (
        <DocumentLinks documents={routine.documents} label="이 작업이 고치는 문서" />
      )}
      {!run ? (
        <p className="widget-note">아직 실행 기록이 없습니다. ▶을 누르면 지금 한 번 실행합니다.</p>
      ) : (
        <RunDetail run={run} />
      )}
      {runs.length > 1 && (
        <ol className="routine-history" aria-label="지난 실행">
          {runs.map((item, index) => (
            <li key={item.id}>
              <button
                type="button"
                aria-current={index === chosen ? 'true' : undefined}
                onClick={() => setChosen(index)}
              >
                <span
                  className={`run-dot ${item.status === 'success' && item.changed ? 'changed' : item.status}`}
                />
                <span>{date(item.finished_at ?? item.created_at)}</span>
                <span>{item.status === 'success' ? item.summary : STATUS[item.status]}</span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function DocumentLinks({
  documents,
  label,
}: {
  documents: { id: string; title: string }[];
  label: string;
}) {
  const { openDocument } = useActions();
  return (
    <p className="routine-docs">
      <span>{label}</span>
      {documents.map((doc) => (
        <button
          key={doc.id}
          type="button"
          className="text-button"
          onClick={() => openDocument(doc.id)}
        >
          <FileText size={13} />
          {doc.title}
        </button>
      ))}
    </p>
  );
}

function RunDetail({ run }: { run: RoutineRun }) {
  const { openDocument } = useActions();
  return (
    <div className="routine-run">
      <p className="routine-run-head">
        <strong>
          {run.status === 'success'
            ? run.summary
            : `${STATUS[run.status]}${run.error ? ` · ${run.error}` : ''}`}
        </strong>
        <small>
          {run.started_by === 'manual' ? '직접 실행' : '자동 실행'} ·{' '}
          {date(run.finished_at ?? run.started_at ?? run.created_at)}
        </small>
      </p>
      {run.diagnosis && <Diagnosis diagnosis={run.diagnosis} />}
      {run.metrics.length > 0 && <Metrics items={run.metrics.map((metric) => ({ ...metric }))} />}
      {run.notes.map((note) => (
        <p key={note} className="job-notice">
          <AlertCircle size={14} />
          {note}
        </p>
      ))}
      {run.documents.length > 0 && <DocumentLinks documents={run.documents} label="만든 문서" />}
      {run.text && (
        <details className="routine-text" open>
          <summary>AI 정리</summary>
          <Markdown>{run.text}</Markdown>
        </details>
      )}
      {run.items.length > 0 && (
        <ul className="routine-items">
          {run.items.map((item, index) => (
            <li key={index} className={item.status}>
              <span className={`item-status ${item.status}`}>{ITEM_STATUS[item.status]}</span>
              <span className="routine-item-main">
                <strong className="break-path">{item.title}</strong>
                {item.message && <small>{item.message}</small>}
                {item.change && (
                  <details className="change-lines">
                    <summary>
                      바뀐 줄 · +{item.change.added} −{item.change.removed}
                    </summary>
                    <ul>
                      {item.change.lines.map((line, number) => (
                        <li key={number} className={line.op === '+' ? 'added' : 'removed'}>
                          <span aria-label={line.op === '+' ? '추가' : '삭제'}>
                            {line.op === '+' ? '+' : '−'}
                          </span>
                          {line.text}
                        </li>
                      ))}
                      {item.change.more > 0 && (
                        <li className="more">바뀐 줄 {item.change.more}개 더</li>
                      )}
                    </ul>
                  </details>
                )}
              </span>
              {item.doc_id && (
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`${item.title} 문서 열기`}
                  title="문서 열기"
                  onClick={() => openDocument(item.doc_id!)}
                >
                  <ExternalLink size={14} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// The latest runs of every routine, newest first, for the dashboard.
export function RoutineFeed() {
  const { notify, navigate, openDocument, showRoutine } = useActions();
  const { data, error } = useQuery<RoutineRun[]>('routines.runs', { limit: 8 });
  const runs = data ?? null;
  useEffect(() => {
    if (error) notify(error.message, true);
  }, [error, notify]);
  return (
    <>
      <WidgetHead count={runs?.filter((run) => run.changed).length} />
      {runs === null ? (
        <Loading />
      ) : runs.length ? (
        <ul className="routine-feed">
          {runs.map((run) => {
            const Icon = ROUTINE_ICONS[run.kind] ?? Workflow;
            return (
              <li key={run.id} className={run.changed ? 'changed' : run.status}>
                <button
                  type="button"
                  onClick={() =>
                    run.documents[0]
                      ? openDocument(run.documents[0].id)
                      : showRoutine(run.routine_id)
                  }
                >
                  <span className="routine-icon" aria-hidden="true">
                    <Icon size={15} />
                  </span>
                  <span>
                    <strong>{run.name}</strong>
                    <small>
                      {date(run.finished_at ?? run.created_at)} ·{' '}
                      {run.status === 'success'
                        ? run.summary
                        : `${STATUS[run.status]} ${run.error}`}
                    </small>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState
          icon={Workflow}
          title="아직 실행한 자동 작업이 없어요"
          action={
            <button className="text-button" onClick={() => navigate('automation', 'routines')}>
              자동 작업 열기 <ArrowRight size={13} />
            </button>
          }
        >
          Confluence·JIRA 변경 확인, 업무 보고 같은 작업을 켜 두면 결과가 여기에 쌓입니다.
        </EmptyState>
      )}
    </>
  );
}
