import { useEffect, useState } from 'react';
import { Braces, LoaderCircle, Pencil, Plus, Trash2 } from 'lucide-react';
import { api, variablesOf, type Prompt, type Schedule } from '@/lib/api';
import { useActions, usePrompts } from '../state';
import Confirm from '../confirm';

const blank = {
  title: '',
  description: '',
  category: '나만의 프롬프트',
  system_prompt: '당신은 신뢰할 수 있는 업무 비서입니다. 한국어로 정확하고 간결하게 답하세요.',
  template: '',
};
type Draft = typeof blank & { id?: string };

export default function TemplatesSection() {
  const prompts = usePrompts();
  const { refresh, notify } = useActions();
  const [editing, setEditing] = useState<Draft | null>(null);
  return (
    <div className="templates">
      <p className="section-lead">
        대시보드의 Prompt 실행 위젯에서 쓰는 요청 템플릿입니다. <code>{'{{변수 이름}}'}</code>을
        넣은 자리는 실행할 때마다 입력합니다.
      </p>
      {editing && !editing.id ? (
        <TemplateForm
          draft={editing}
          onCancel={() => setEditing(null)}
          onSaved={() => setEditing(null)}
        />
      ) : (
        !editing && (
          <button className="button secondary" onClick={() => setEditing({ ...blank })}>
            <Plus size={15} />새 템플릿
          </button>
        )
      )}
      <ul className="template-list">
        {prompts.map((prompt) => (
          <li key={prompt.id}>
            {editing?.id === prompt.id ? (
              <TemplateForm
                draft={editing}
                onCancel={() => setEditing(null)}
                onSaved={() => setEditing(null)}
              />
            ) : (
              <div className="template-row">
                <div>
                  <strong>{prompt.title}</strong>
                  <small>
                    {prompt.category} · 입력 {variablesOf(prompt.template).length}개
                    {prompt.description && ` · ${prompt.description}`}
                  </small>
                </div>
                <button
                  className="icon-button"
                  aria-label={`${prompt.title} 수정`}
                  title="수정"
                  // One form at a time: another row would throw away what is being written.
                  disabled={!!editing}
                  onClick={() => setEditing({ ...prompt })}
                >
                  <Pencil size={15} />
                </button>
                <Confirm
                  iconOnly
                  name={`${prompt.title} 삭제`}
                  label="삭제"
                  icon={<Trash2 size={15} />}
                  question="템플릿과 그 예약을 삭제할까요? 실행 기록은 남습니다."
                  onConfirm={async () => {
                    try {
                      await api('prompts.delete', { id: prompt.id });
                      await refresh();
                      notify('템플릿을 삭제했습니다.');
                    } catch (e) {
                      notify((e as Error).message, true);
                    }
                  }}
                />
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function TemplateForm({
  draft: initial,
  onCancel,
  onSaved,
}: {
  draft: Draft;
  onCancel: () => void;
  onSaved: (prompt: Prompt) => void;
}) {
  const { refresh, notify } = useActions();
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const names = variablesOf(draft.template);
  // The template's schedules keep the inputs they were saved with: an input added or renamed here has no value in
  // them, and they fail until each is opened and filled in. Saving such a change asks first.
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  useEffect(() => {
    if (!initial.id) return;
    let live = true;
    api<Schedule[]>('schedules.list')
      .then((items) => live && setSchedules(items.filter((item) => item.prompt_id === initial.id)))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [initial.id]);
  const lacking = (schedule: Schedule) => names.filter((name) => !schedule.variables[name]?.trim());
  const affected = schedules.filter((schedule) => lacking(schedule).length);
  const missing = [...new Set(affected.flatMap(lacking))].map((name) => `‘${name}’`).join(', ');
  async function save() {
    setBusy(true);
    setError('');
    try {
      const saved = await api<Prompt>('prompts.save', draft);
      await refresh();
      notify('템플릿을 저장했습니다.');
      onSaved(saved);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="template-form"
      aria-label={draft.id ? '템플릿 수정' : '새 템플릿'}
      onSubmit={(event) => {
        event.preventDefault();
        if (!affected.length) void save();
      }}
    >
      <fieldset disabled={busy}>
        <div className="form-grid">
          <label>
            이름
            <input
              required
              maxLength={120}
              value={draft.title}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              placeholder="예: 주간 보고서 작성"
            />
          </label>
          <label>
            분류
            <input
              required
              maxLength={40}
              list="template-categories"
              value={draft.category}
              onChange={(e) => setDraft({ ...draft, category: e.target.value })}
            />
          </label>
        </div>
        <datalist id="template-categories">
          <option>업무</option>
          <option>개발</option>
          <option>기획</option>
        </datalist>
        <label>
          간단한 설명
          <input
            maxLength={300}
            value={draft.description}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            placeholder="어떤 일을 돕는 템플릿인가요?"
          />
        </label>
        <label>
          AI의 역할
          <textarea
            rows={2}
            maxLength={10000}
            value={draft.system_prompt}
            onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })}
          />
        </label>
        <label>
          프롬프트
          <textarea
            className="code-input"
            required
            rows={6}
            maxLength={40000}
            value={draft.template}
            onChange={(e) => setDraft({ ...draft, template: e.target.value })}
            placeholder={'다음 업무 내용을 주간 보고서로 정리해 주세요.\n\n{{업무 내용}}'}
          />
          <small>
            {names.length ? (
              <span className="variable-list">
                입력란:
                {names.map((name) => (
                  <span className="tag" key={name}>
                    <Braces size={11} />
                    {name}
                  </span>
                ))}
              </span>
            ) : (
              '{{변수 이름}}을 넣으면 실행할 때 입력란이 생깁니다.'
            )}
          </small>
        </label>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="form-actions">
          <button type="button" className="button secondary" onClick={onCancel}>
            취소
          </button>
          {affected.length ? (
            <Confirm
              label="템플릿 저장"
              question={`이 템플릿을 쓰는 예약 ${affected.length}개에 ${missing} 값이 없어, 예약을 열어 채우기 전까지 실행에 실패합니다. 저장할까요?`}
              disabled={busy}
              onConfirm={save}
            />
          ) : (
            <button className="button primary">
              {busy && <LoaderCircle className="spin" size={15} />}
              템플릿 저장
            </button>
          )}
        </div>
      </fieldset>
    </form>
  );
}
