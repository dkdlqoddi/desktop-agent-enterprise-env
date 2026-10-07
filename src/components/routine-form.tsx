import { useEffect, useState } from 'react';
import { File, FolderPlus, KeyRound, LoaderCircle, Repeat, Sparkles } from 'lucide-react';
import {
  api,
  type Routine,
  type RoutineField,
  type RoutineRecipe,
  type RoutineSchedule,
  type Site,
  type SiteKind,
  type SiteMatch,
} from '@/lib/api';
import { httpUrl } from '@/lib/links';
import { loginSummary } from './admin/sites';
import { useApp } from './app';

// What the form edits: the recipe's fields and the schedule; urls and paths as one entry a line.
export type RoutineDraft = RoutineSchedule & {
  id?: string;
  kind: string;
  name: string;
  config: Record<string, unknown>;
  enabled: boolean;
  notify: boolean;
};

export const draftOf = (recipe: RoutineRecipe): RoutineDraft => ({
  kind: recipe.kind,
  name: recipe.title,
  config: Object.fromEntries(recipe.fields.map((field) => [field.key, field.default])),
  enabled: true,
  notify: recipe.notify,
  ...recipe.schedule,
});

export const draftFrom = (routine: Routine): RoutineDraft => ({
  id: routine.id,
  kind: routine.kind,
  name: routine.name,
  config: routine.config,
  enabled: routine.enabled,
  notify: routine.notify,
  frequency: routine.frequency,
  interval: routine.interval,
  run_time: routine.run_time,
  weekday: routine.weekday,
  month_day: routine.month_day,
});

// How often a routine runs, as backend/agent/routines.py allows (INTERVALS, FREQUENCIES).
const INTERVALS: [number, string][] = [
  [15, '15분마다'],
  [30, '30분마다'],
  [60, '1시간마다'],
  [120, '2시간마다'],
  [180, '3시간마다'],
  [360, '6시간마다'],
  [720, '12시간마다'],
  [1440, '하루마다'],
];
const WEEKDAYS = ['월', '화', '수', '목', '금', '토', '일']; // Monday first, as the backend counts.
const TIME = new Intl.DateTimeFormat('ko-KR', { hour: 'numeric', minute: '2-digit' });
const timeLabel = (value: string) => {
  const match = /^(\d\d):(\d\d)$/.exec(value);
  return match ? TIME.format(new Date(2000, 0, 1, Number(match[1]), Number(match[2]))) : value;
};

// The rule in words: "1시간마다", "평일 오전 9:00", "매주 금요일 오후 5:00".
export function describeRoutine(schedule: RoutineSchedule) {
  switch (schedule.frequency) {
    case 'interval':
      return (
        INTERVALS.find(([minutes]) => minutes === schedule.interval)?.[1] ??
        `${schedule.interval}분마다`
      );
    case 'daily':
      return `매일 ${timeLabel(schedule.run_time)}`;
    case 'weekdays':
      return `평일 ${timeLabel(schedule.run_time)}`;
    case 'weekly':
      return `매주 ${WEEKDAYS[schedule.weekday]}요일 ${timeLabel(schedule.run_time)}`;
    case 'monthly':
      return `매월 ${schedule.month_day}일 ${timeLabel(schedule.run_time)}`;
  }
}

const SITE_LABELS: Record<SiteKind, string> = {
  jira: 'JIRA',
  confluence: 'Confluence',
};
const isList = (field: RoutineField) => field.type === 'urls' || field.type === 'paths';
const lines = (value: unknown) =>
  (Array.isArray(value) ? value : String(value ?? '').split('\n'))
    .map((line) => String(line).trim())
    .filter(Boolean);

// A routine of a recipe, as it is set up or changed: the recipe's own fields, how often it runs and whether it tells
// the user. Shown in place of the list that opened it.
export default function RoutineForm({
  recipe,
  draft,
  onClose,
  onSaved,
}: {
  recipe: RoutineRecipe;
  draft: RoutineDraft;
  onClose: () => void;
  onSaved: (routine: Routine) => void;
}) {
  const { configured, openAdmin } = useApp();
  const [form, setForm] = useState(() => ({
    ...draft,
    // Lists are edited as text, one entry a line.
    config: Object.fromEntries(
      recipe.fields.map((field) => {
        const value = draft.config[field.key] ?? field.default;
        return [field.key, isList(field) ? lines(value).join('\n') : value];
      }),
    ),
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const set = (patch: Partial<RoutineDraft>) => setForm((current) => ({ ...current, ...patch }));
  const setValue = (key: string, value: unknown) =>
    setForm((current) => ({ ...current, config: { ...current.config, [key]: value } }));
  // A recipe that reads files on this PC: AI tools may only submit its form with the "full" access level
  // (src/components/app-control.ts).
  const readsFiles = recipe.files;
  const frequency = form.frequency === 'interval' ? `interval:${form.interval}` : form.frequency;
  async function submit() {
    setBusy(true);
    setError('');
    try {
      const config = Object.fromEntries(
        recipe.fields.map((field) => [
          field.key,
          isList(field) ? lines(form.config[field.key]) : form.config[field.key],
        ]),
      );
      const saved = await api<Routine>('routines.save', {
        // A change leaves the switch as it is now: the app may have turned the routine off since the form opened, after
        // a refused login, and sending the old state would turn it on again.
        ...(form.id ? { id: form.id } : { kind: recipe.kind, enabled: form.enabled }),
        name: form.name.trim(),
        config,
        frequency: form.frequency,
        interval: form.interval,
        run_time: form.run_time,
        weekday: form.weekday,
        month_day: form.month_day,
        notify: form.notify,
      });
      onSaved(saved);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="routine-form"
      aria-label={form.id ? `${recipe.title} 수정` : `새 자동 작업 · ${recipe.title}`}
      {...(readsFiles ? { 'data-requires-full': '' } : {})}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <fieldset disabled={busy}>
        <p className="routine-form-lead">
          <strong>{recipe.title}</strong>
          <span>{recipe.description}</span>
        </p>
        {recipe.llm === 'required' && !configured && (
          <p className="inline-error">
            LLM을 연결해야 실행할 수 있는 작업입니다.{' '}
            <button type="button" className="text-button" onClick={() => openAdmin('llm')}>
              LLM 연결
            </button>
          </p>
        )}
        <label>
          이름
          <input
            maxLength={80}
            value={form.name}
            placeholder={recipe.title}
            onChange={(e) => set({ name: e.target.value })}
          />
        </label>
        {recipe.fields.map((field) => (
          <FieldInput
            key={field.key}
            field={field}
            value={form.config[field.key]}
            onChange={(value) => setValue(field.key, value)}
            disabled={field.type === 'switch' && field.key === 'analyze' && !configured}
          />
        ))}
        <fieldset className="frequency-field">
          <legend>실행 주기</legend>
          <div className="form-grid">
            <label>
              반복
              <select
                aria-label="반복"
                value={frequency}
                onChange={(e) => {
                  const [kind, minutes] = e.target.value.split(':');
                  set(
                    kind === 'interval'
                      ? { frequency: 'interval', interval: Number(minutes) }
                      : { frequency: kind as RoutineDraft['frequency'] },
                  );
                }}
              >
                {INTERVALS.map(([minutes, label]) => (
                  <option key={minutes} value={`interval:${minutes}`}>
                    {label}
                  </option>
                ))}
                <option value="daily">매일 정한 시각에</option>
                <option value="weekdays">평일 정한 시각에</option>
                <option value="weekly">매주 정한 요일에</option>
                <option value="monthly">매월 정한 날에</option>
              </select>
            </label>
            {form.frequency === 'weekly' && (
              <label>
                요일
                <select
                  value={form.weekday}
                  onChange={(e) => set({ weekday: Number(e.target.value) })}
                >
                  {WEEKDAYS.map((day, index) => (
                    <option key={day} value={index}>
                      {day}요일
                    </option>
                  ))}
                </select>
              </label>
            )}
            {form.frequency === 'monthly' && (
              <label>
                날짜
                <select
                  value={form.month_day}
                  onChange={(e) => set({ month_day: Number(e.target.value) })}
                >
                  {Array.from({ length: 31 }, (_, i) => i + 1).map((day) => (
                    <option key={day} value={day}>
                      {day}일
                    </option>
                  ))}
                </select>
              </label>
            )}
            {form.frequency !== 'interval' && (
              <label>
                시각
                <input
                  type="time"
                  required
                  value={form.run_time}
                  onChange={(e) => e.target.value && set({ run_time: e.target.value })}
                />
              </label>
            )}
          </div>
        </fieldset>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={form.notify}
            onChange={(e) => set({ notify: e.target.checked })}
          />
          찾은 것이 있거나 실패하면 알림 받기
        </label>
        <p className="schedule-summary">
          <Repeat size={15} />
          <span>
            <strong>{describeRoutine(form)}</strong> 실행합니다. 앱이 켜져 있을 때 실행하고, 꺼져
            있던 동안 놓친 실행은 앱을 켜면 한 번 합니다.
            {form.frequency === 'interval' && !form.id && ' 저장하면 곧 처음 실행합니다.'}
          </span>
        </p>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="form-actions">
          <button type="button" className="button secondary small" onClick={onClose}>
            취소
          </button>
          <span className="spacer" />
          <button type="submit" className="button primary small">
            {busy && <LoaderCircle className="spin" size={14} />}
            {form.id ? '저장' : '만들기'}
          </button>
        </div>
      </fieldset>
    </form>
  );
}

function FieldInput({
  field,
  value,
  onChange,
  disabled,
}: {
  field: RoutineField;
  value: unknown;
  onChange: (value: unknown) => void;
  disabled?: boolean;
}) {
  const site = field.group as SiteKind;
  const help = field.help && <small>{field.help}</small>;
  if (field.type === 'switch')
    return (
      <div className="routine-switch">
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={!!value}
            disabled={disabled}
            onChange={(e) => onChange(e.target.checked)}
          />
          {field.label}
          {disabled && ' (LLM 연결 필요)'}
        </label>
        {help}
      </div>
    );
  if (field.type === 'select') {
    // A value the list no longer has (saved by an older version) shows as an empty choice, so that picking any
    // option, the first too, changes it.
    const chosen = field.options.findIndex((option) => option.value === value);
    return (
      <label>
        {field.label}
        <select
          value={chosen}
          onChange={(e) => onChange(field.options[Number(e.target.value)].value)}
        >
          {chosen < 0 && (
            <option value={-1} disabled>
              골라 주세요
            </option>
          )}
          {field.options.map((option, index) => (
            <option key={String(option.value)} value={index}>
              {option.label}
            </option>
          ))}
        </select>
        {help}
      </label>
    );
  }
  const text = String(value ?? '');
  const multiline = field.type === 'textarea' || isList(field);
  return (
    <div className="routine-field">
      <label>
        {field.label}
        {field.required && <span className="required"> *</span>}
        {multiline ? (
          <textarea
            rows={isList(field) ? 3 : 2}
            required={field.required}
            maxLength={isList(field) ? 4096 * (field.max ?? 1) : (field.max ?? undefined)}
            value={text}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value)}
          />
        ) : (
          <input
            required={field.required}
            maxLength={field.max ?? undefined}
            value={text}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value)}
          />
        )}
        {help}
      </label>
      {field.type === 'paths' && <PathPicker value={text} onChange={onChange} />}
      {site in SITE_LABELS && <SiteAccount kind={site} url={lines(text)[0] ?? ''} />}
    </div>
  );
}

// Folders and files chosen in Windows' own dialog, added as lines.
function PathPicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { notify } = useApp();
  async function add(mode: 'directory' | 'file') {
    try {
      const path = await api<string | null>('import.choose', { mode });
      if (path) onChange([...lines(value), path].join('\n'));
    } catch (e) {
      notify((e as Error).message, true);
    }
  }
  return (
    <span className="path-picker">
      <button type="button" className="text-button" onClick={() => void add('directory')}>
        <FolderPlus size={13} /> 폴더 추가
      </button>
      <button type="button" className="text-button" onClick={() => void add('file')}>
        <File size={13} /> 파일 추가
      </button>
    </span>
  );
}

// The site account the field's link signs in with, as the import box shows it.
function SiteAccount({ kind, url }: { kind: SiteKind; url: string }) {
  const { openAdmin } = useApp();
  const [match, setMatch] = useState<SiteMatch | null>(null);
  useEffect(() => {
    if (!httpUrl(url)) return setMatch(null);
    let live = true;
    const timer = setTimeout(() => {
      api<SiteMatch>('sites.match', { url, kind })
        .then((found) => live && setMatch(found))
        .catch(() => live && setMatch(null));
    }, 300);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [url, kind]);
  if (!match) return null;
  return (
    <AccountLine
      site={match.site}
      kind={kind}
      onManage={() => openAdmin('sites', match.site ? undefined : { url, kind })}
    />
  );
}

function AccountLine({
  site,
  kind,
  onManage,
}: {
  site: Site | null;
  kind: SiteKind;
  onManage: () => void;
}) {
  return (
    <p className="site-line">
      <KeyRound size={14} />
      {site ? (
        <span>
          <strong>{site.name}</strong>
          {site.login_id && ` · ${site.login_id}`} · {loginSummary(site)}
          {site.method !== 'session' &&
            !site.has_password &&
            ' · 비밀번호가 없어 자동 로그인하지 않습니다'}
        </span>
      ) : (
        <span>
          등록된 {SITE_LABELS[kind]} 계정이 없습니다. 브라우저에 남은 로그인으로 시도합니다.
        </span>
      )}
      <button type="button" className="text-button" onClick={onManage}>
        {site ? '계정 관리' : '계정 등록'}
      </button>
    </p>
  );
}

// The badges of a recipe in the catalog: what it needs before it can run.
export function RecipeNeeds({ recipe }: { recipe: RoutineRecipe }) {
  return (
    <span className="recipe-needs">
      {recipe.llm !== 'none' && (
        <span className={`chip${recipe.llm === 'required' ? ' strong' : ''}`}>
          <Sparkles size={11} />
          {recipe.llm === 'required' ? 'LLM 필요' : 'LLM 선택'}
        </span>
      )}
      {recipe.sites.map((site) => (
        <span key={site} className="chip">
          {SITE_LABELS[site]} 계정
        </span>
      ))}
      {recipe.files && <span className="chip">이 PC 파일</span>}
    </span>
  );
}
