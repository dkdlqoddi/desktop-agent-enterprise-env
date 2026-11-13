import { useState } from 'react';
import { RotateCcw, Timer } from 'lucide-react';
import type { HermesSettings, HermesTurnLimits } from '@/lib/api';

type Props = {
  state: HermesSettings;
  busy: boolean;
  // Saves a change of the settings (hermes.update); true when it was saved.
  update: (params: Record<string, unknown>, done?: string) => Promise<boolean>;
};

type Name = keyof HermesTurnLimits;

// Each limit as the form shows it: the bounds and the default the main process keeps it to (electron/hermes-settings.cjs,
// TURN_LIMITS; the defaults are the limits from before they could be set) and what it means. The whole turn's 0 is
// automatic.
const FIELDS: {
  name: Name;
  label: string;
  unit: string;
  min: number;
  max: number;
  initial: number;
  hint: string;
}[] = [
  {
    name: 'request_seconds',
    label: '모델 요청 하나',
    unit: '초',
    min: 30,
    max: 300,
    initial: 120,
    hint: '30~300초. LLM 연결의 응답 제한 시간이 더 짧으면 그 시간까지입니다.',
  },
  {
    name: 'iterations',
    label: '반복 횟수',
    unit: '번',
    min: 4,
    max: 200,
    initial: 24,
    hint: '4~200번. 모델에 묻고 도구를 쓰는 한 차례가 한 번입니다.',
  },
  {
    name: 'turn_minutes',
    label: '요청 전체',
    unit: '분',
    min: 0,
    max: 60,
    initial: 0,
    hint: '2~60분, 0은 자동(LLM 연결의 응답 제한 시간의 4배, 2분~15분). 명령 확인과 질문에 답하는 시간은 세지 않습니다.',
  },
  {
    name: 'answer_minutes',
    label: '명령 확인·질문의 대기',
    unit: '분',
    min: 1,
    max: 30,
    initial: 5,
    hint: '1~30분. 이 시간 안에 답이 없으면 명령은 실행하지 않고, 질문은 답 없이 넘어갑니다.',
  },
];

const texts = (limits: HermesTurnLimits) =>
  Object.fromEntries(FIELDS.map(({ name }) => [name, String(limits[name])])) as Record<
    Name,
    string
  >;

// How long and how far one request of the chat may go (a turn of the agent), on the settings screen inside its
// data-user-only form: a model request's time, the agent's iterations, the whole turn's time and how long a command's
// approval or a question waits for the user. The messenger gateway's conversations go by them too. A value out of
// bounds is refused by the main process with what is allowed.
export default function HermesLimits({ state, busy, update }: Props) {
  const saved = state.turn_limits;
  const [draft, setDraft] = useState(() => texts(saved));
  const whole = FIELDS.every(({ name }) => /^\d{1,4}$/.test(draft[name].trim()));
  const changed = FIELDS.some(({ name }) => draft[name].trim() !== String(saved[name]));
  const initial = FIELDS.every(({ name, initial }) => saved[name] === initial);

  const save = (limits: Record<Name, number>, done: string) =>
    void update({ turn_limits: limits }, done).then((ok) => ok && setDraft(texts(limits)));

  return (
    <fieldset className="admin-fieldset">
      <legend>
        <Timer size={14} /> 요청 한도
      </legend>
      <p className="admin-hint">
        채팅에 보낸 요청 하나(메신저에서 받은 요청도)가 쓸 수 있는 시간과 단계입니다. 늘리면 오래
        걸리는 일도 끝까지 하지만, 그만큼 모델 요청이 늘어납니다.
      </p>
      <div className="form-grid">
        {FIELDS.map(({ name, label, unit, min, max, hint }) => (
          <label key={name}>
            {label} ({unit})
            <input
              type="number"
              inputMode="numeric"
              min={min}
              max={max}
              step={1}
              value={draft[name]}
              onChange={(event) =>
                setDraft((current) => ({ ...current, [name]: event.target.value }))
              }
            />
            <small>{hint}</small>
          </label>
        ))}
      </div>
      <div className="form-actions">
        <button
          type="button"
          className="button secondary small"
          disabled={busy || !changed || !whole}
          onClick={() =>
            save(
              Object.fromEntries(
                FIELDS.map(({ name }) => [name, Number(draft[name].trim())]),
              ) as Record<Name, number>,
              '요청 한도를 저장했습니다. 다음 요청부터 적용됩니다.',
            )
          }
        >
          한도 저장
        </button>
        <button
          type="button"
          className="button secondary small"
          disabled={busy || initial}
          onClick={() =>
            save(
              Object.fromEntries(FIELDS.map(({ name, initial }) => [name, initial])) as Record<
                Name,
                number
              >,
              '요청 한도를 기본값으로 되돌렸습니다.',
            )
          }
        >
          <RotateCcw size={14} /> 기본값으로
        </button>
      </div>
    </fieldset>
  );
}
