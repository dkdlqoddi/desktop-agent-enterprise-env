import { useState } from 'react';
import { LoaderCircle, Repeat } from 'lucide-react';
import {
  api,
  dayTime,
  isoFromLocalInput,
  localInputValue,
  variablesOf,
  type Frequency,
  type Prompt,
  type Schedule,
} from '@/lib/api';
import { rove } from './roving';

export type ScheduleDraft = Pick<
  Schedule,
  | 'prompt_id'
  | 'name'
  | 'variables'
  | 'frequency'
  | 'run_time'
  | 'weekday'
  | 'month_day'
  | 'run_at'
  | 'enabled'
  | 'notify'
> & { id?: string };

export const scheduleDraft = (
  promptId: string,
  variables: Record<string, string> = {},
): ScheduleDraft => ({
  prompt_id: promptId,
  name: '',
  variables,
  frequency: 'daily',
  run_time: '09:00',
  weekday: 0,
  month_day: 1,
  run_at: null,
  enabled: true,
  notify: true,
});

const WEEKDAYS = ['월', '화', '수', '목', '금', '토', '일']; // Monday first, as the backend counts.
const FREQUENCIES: [Frequency, string][] = [
  ['once', '한 번'],
  ['hourly', '매시간'],
  ['daily', '매일'],
  ['weekdays', '평일'],
  ['weekly', '매주'],
  ['monthly', '매월'],
];
const TIME = new Intl.DateTimeFormat('ko-KR', { hour: 'numeric', minute: '2-digit' });
// A time that is not HH:MM (a cleared time box, a schedule saved elsewhere) is shown as it is: formatting an invalid
// date would throw while the screen draws.
const timeLabel = (value: string) => {
  const match = /^(\d\d):(\d\d)$/.exec(value);
  return match ? TIME.format(new Date(2000, 0, 1, Number(match[1]), Number(match[2]))) : value;
};
export function describeSchedule(schedule: ScheduleDraft) {
  const minute = Number(/^\d\d:(\d\d)$/.exec(schedule.run_time ?? '')?.[1] ?? 0);
  switch (schedule.frequency) {
    case 'once':
      return schedule.run_at ? `한 번 · ${dayTime(schedule.run_at)}` : '한 번';
    case 'hourly':
      return minute ? `매시간 ${minute}분` : '매시간 정각';
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

// Where and how often a prompt runs by itself; shown in place of the list that opened it.
export function ScheduleForm({
  draft,
  prompts,
  onClose,
  onSaved,
}: {
  draft: ScheduleDraft;
  prompts: Prompt[];
  onClose: () => void;
  onSaved: (schedule: Schedule) => void;
}) {
  const [form, setForm] = useState(draft);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const prompt = prompts.find((p) => p.id === form.prompt_id);
  const names = prompt ? variablesOf(prompt.template) : [];
  const set = (patch: Partial<ScheduleDraft>) => setForm({ ...form, ...patch });
  const minute = form.run_time.slice(3);
  const daily = ['daily', 'weekdays', 'weekly', 'monthly'].includes(form.frequency);
  return (
    <form
      className="schedule-form"
      aria-label={form.id ? '예약 수정' : '새 예약'}
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError('');
        try {
          onSaved(await api<Schedule>('schedules.save', { ...form, name: form.name.trim() }));
        } catch (error) {
          setError((error as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <fieldset disabled={busy}>
        <div className="form-grid">
          <label>
            프롬프트
            <select
              required
              value={form.prompt_id}
              onChange={(e) => set({ prompt_id: e.target.value, variables: {} })}
            >
              {prompts.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            예약 이름
            <input
              maxLength={120}
              value={form.name}
              onChange={(e) => set({ name: e.target.value })}
              placeholder={prompt?.title || '예약 이름'}
            />
          </label>
        </div>
        {names.map((name) => (
          <label key={name}>
            {name}
            <textarea
              required
              rows={3}
              maxLength={40000}
              value={form.variables[name] || ''}
              onChange={(e) => set({ variables: { ...form.variables, [name]: e.target.value } })}
              placeholder="예약할 때마다 이 내용으로 실행합니다."
            />
          </label>
        ))}
        <fieldset className="frequency-field">
          <legend>반복</legend>
          <div className="segmented" role="radiogroup" aria-label="반복 주기">
            {FREQUENCIES.map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={form.frequency === value}
                tabIndex={form.frequency === value ? 0 : -1}
                onKeyDown={rove}
                className={form.frequency === value ? 'active' : ''}
                onClick={() =>
                  set({
                    frequency: value,
                    // Hourly keeps only the minute; other rules start again at 9 a.m.
                    run_time:
                      value === 'hourly'
                        ? `00:${Number(minute) % 5 ? '00' : minute}`
                        : form.frequency === 'hourly'
                          ? '09:00'
                          : form.run_time,
                  })
                }
              >
                {label}
              </button>
            ))}
          </div>
        </fieldset>
        <div className="form-grid">
          {form.frequency === 'once' && (
            <label>
              실행 일시
              <input
                type="datetime-local"
                required
                min={localInputValue(new Date().toISOString())}
                value={localInputValue(form.run_at)}
                onChange={(e) => set({ run_at: isoFromLocalInput(e.target.value) })}
              />
            </label>
          )}
          {form.frequency === 'hourly' && (
            <label>
              매시 몇 분에
              <select value={minute} onChange={(e) => set({ run_time: `00:${e.target.value}` })}>
                {Array.from({ length: 12 }, (_, i) => String(i * 5).padStart(2, '0')).map((m) => (
                  <option key={m} value={m}>
                    {Number(m) ? `${Number(m)}분` : '정각'}
                  </option>
                ))}
              </select>
            </label>
          )}
          {form.frequency === 'weekly' && (
            <label>
              요일
              <select
                value={form.weekday}
                onChange={(e) => set({ weekday: Number(e.target.value) })}
              >
                {WEEKDAYS.map((day, i) => (
                  <option key={day} value={i}>
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
              {form.month_day > 28 && <small>이 날짜가 없는 달은 마지막 날에 실행합니다.</small>}
            </label>
          )}
          {daily && (
            <label>
              시각
              <input
                type="time"
                required
                value={form.run_time}
                // A box cleared with Backspace keeps the last time; a new one replaces it.
                onChange={(e) => e.target.value && set({ run_time: e.target.value })}
              />
            </label>
          )}
        </div>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={form.notify}
            onChange={(e) => set({ notify: e.target.checked })}
          />
          실행이 끝나면 알림 받기
        </label>
        <p className="schedule-summary">
          <Repeat size={15} />
          <span>
            <strong>{describeSchedule(form)}</strong>에 ‘{prompt?.title || '프롬프트'}’을(를)
            실행합니다. 결과는 실행 기록에 남고, 앱이 꺼져 있던 동안 놓친 예약은 다음 실행 때 한 번
            실행합니다.
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
            예약 저장
          </button>
        </div>
      </fieldset>
    </form>
  );
}
