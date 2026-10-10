import { useState } from 'react';
import {
  AlertCircle,
  BookOpen,
  CalendarClock,
  Check,
  Copy,
  LoaderCircle,
  Play,
  SlidersHorizontal,
} from 'lucide-react';
import {
  api,
  date,
  plain,
  seconds,
  submitsOnEnter,
  variablesOf,
  type Run,
  type RunItem,
  type Schedule,
} from '@/lib/api';
import { WidgetHead } from '../dashboard';
import Markdown from '../markdown';
import { ScheduleForm, scheduleDraft, type ScheduleDraft } from '../prompt-schedules';
import { useNow } from '../run-history';
import { useActions, useConfigured, usePrompts, useQuery } from '../state';

const NO_RUNS: RunItem[] = [];

export default function PromptWidget() {
  const prompts = usePrompts();
  const configured = useConfigured();
  const { openAdmin, notify, refresh, openRun, bumpRuns } = useActions();
  const [promptId, setPromptId] = useState('');
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [output, setOutput] = useState<Run | null>(null);
  const [scheduling, setScheduling] = useState<ScheduleDraft | null>(null);
  const [busy, setBusy] = useState('');
  const [startedAt, setStartedAt] = useState(0);
  const now = useNow(1000, busy === 'run');
  const prompt = prompts.find((p) => p.id === promptId) ?? prompts[0];
  const names = prompt ? variablesOf(prompt.template) : [];
  const categories = [...new Set(prompts.map((p) => p.category))];
  // This prompt's latest runs until a new answer arrives; loaded again when the runs change (a run ends).
  const latest = useQuery<RunItem[]>(
    'runs.list',
    prompt ? { prompt_id: prompt.id, limit: 5 } : null,
  );
  const recent = (!latest.error && latest.data) || NO_RUNS;
  async function act(key: string, action: () => Promise<void>) {
    setBusy(key);
    try {
      await action();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy('');
    }
  }
  const run = () => {
    setStartedAt(Date.now());
    void act('run', async () => {
      setOutput(await api<Run>('prompts.run', { prompt_id: prompt!.id, variables }));
      bumpRuns();
    });
  };
  return (
    <>
      <WidgetHead id="prompt" title="Prompt 실행">
        <button
          className="icon-button"
          aria-label="프롬프트 템플릿 관리"
          title="템플릿 만들기·고치기"
          onClick={() => openAdmin('templates')}
        >
          <SlidersHorizontal size={16} />
        </button>
      </WidgetHead>
      {!prompt ? (
        <p className="widget-note">
          프롬프트 템플릿이 없습니다. 관리 화면에서 템플릿을 만들어 주세요.
        </p>
      ) : scheduling ? (
        <ScheduleForm
          draft={scheduling}
          prompts={prompts}
          onClose={() => setScheduling(null)}
          onSaved={(schedule: Schedule) => {
            setScheduling(null);
            bumpRuns();
            notify(
              schedule.next_run_at
                ? `예약을 저장했습니다. ${date(schedule.next_run_at)}에 처음 실행합니다.`
                : '예약을 저장했습니다.',
            );
          }}
        />
      ) : (
        <form
          className="prompt-form"
          onSubmit={(event) => {
            event.preventDefault();
            run();
          }}
        >
          <label>
            프롬프트
            <select
              value={prompt.id}
              disabled={!!busy}
              onChange={(e) => {
                setPromptId(e.target.value);
                setVariables({});
                setOutput(null);
              }}
            >
              {categories.map((category) => (
                <optgroup key={category} label={category}>
                  {prompts
                    .filter((p) => p.category === category)
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.title}
                      </option>
                    ))}
                </optgroup>
              ))}
            </select>
            {prompt.description && <small>{prompt.description}</small>}
          </label>
          {names.length ? (
            names.map((name) => (
              <label key={name}>
                {name}
                <textarea
                  required
                  rows={names.length === 1 ? 5 : 3}
                  maxLength={40000}
                  value={variables[name] || ''}
                  disabled={!!busy}
                  onChange={(e) => setVariables({ ...variables, [name]: e.target.value })}
                  onKeyDown={(e) => {
                    if (!submitsOnEnter(e)) return;
                    e.preventDefault();
                    e.currentTarget.form?.requestSubmit();
                  }}
                  placeholder="Enter 실행 · Shift+Enter 줄바꿈"
                />
              </label>
            ))
          ) : (
            <p className="template-text">{prompt.template}</p>
          )}
          <div className="form-actions">
            <button
              type="button"
              className="button secondary small"
              disabled={!!busy}
              onClick={() => setScheduling(scheduleDraft(prompt.id, variables))}
            >
              <CalendarClock size={14} />이 입력으로 예약
            </button>
            <span className="spacer" />
            {configured ? (
              <button className="button primary small" disabled={!!busy}>
                {busy === 'run' ? <LoaderCircle size={14} className="spin" /> : <Play size={14} />}
                {busy === 'run' ? '응답을 기다리는 중' : '실행'}
              </button>
            ) : (
              <button
                type="button"
                className="button primary small"
                onClick={() => openAdmin('llm')}
              >
                LLM 연결하기
              </button>
            )}
          </div>
          <div className="prompt-output" aria-live="polite">
            {busy === 'run' ? (
              <p className="thinking">
                <LoaderCircle size={15} className="spin" />
                LLM이 응답하고 있어요
                {/* The seconds tick on screen only; the live region speaks when the run starts and ends. */}
                <span aria-hidden="true"> · {seconds(Math.max(0, now - startedAt))}</span>
              </p>
            ) : output ? (
              <>
                {output.status === 'error' ? (
                  <p className="inline-error" role="alert">
                    {output.error}
                  </p>
                ) : (
                  <Markdown>{output.output}</Markdown>
                )}
                <div className="output-foot">
                  <small>
                    {date(output.created_at)} · 응답 {seconds(output.duration_ms)}
                  </small>
                  {output.output && (
                    <>
                      <button
                        type="button"
                        className="button secondary small"
                        disabled={!!busy}
                        onClick={() =>
                          void act('copy', async () => {
                            await api('clipboard.write', { text: output.output });
                            notify('응답을 복사했습니다.');
                          })
                        }
                      >
                        <Copy size={14} />
                        응답 복사
                      </button>
                      <button
                        type="button"
                        className="button secondary small"
                        disabled={!!busy}
                        onClick={() =>
                          void act('wiki', async () => {
                            await api('wiki.save', {
                              title: `${output.title} · ${date(output.created_at)}`,
                              content: output.output,
                            });
                            await refresh();
                            notify('응답을 Wiki 문서로 저장했습니다.');
                          })
                        }
                      >
                        <BookOpen size={14} />
                        Wiki에 저장
                      </button>
                    </>
                  )}
                </div>
              </>
            ) : (
              recent.length > 0 && (
                <div className="recent-runs">
                  <h3>최근 실행</h3>
                  {recent.map((item) => (
                    <button type="button" key={item.id} onClick={() => openRun(item.id)}>
                      <span className={`run-status ${item.status}`}>
                        {item.status === 'success' ? (
                          <Check size={12} />
                        ) : (
                          <AlertCircle size={12} />
                        )}
                      </span>
                      <span>{date(item.created_at)}</span>
                      <span className="recent-preview">{plain(item.preview) || '응답 없음'}</span>
                    </button>
                  ))}
                </div>
              )
            )}
          </div>
        </form>
      )}
    </>
  );
}
