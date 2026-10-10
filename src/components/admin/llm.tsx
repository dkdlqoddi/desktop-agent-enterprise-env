import { useEffect, useRef, useState } from 'react';
import { Check, CircleAlert, LoaderCircle, Plus, Trash2, Unplug } from 'lucide-react';
import { api, type LlmFormat, type LlmHeader, type Settings } from '@/lib/api';
import { useActions, useSettings } from '../state';
import { rove } from '../roving';
import SecretInput from '../secret-input';

// The request formats: where each starts, an example model, and the header its API Key goes in.
const FORMATS: Record<LlmFormat, { label: string; endpoint: string; model: string; auth: string }> =
  {
    openai: {
      label: 'OpenAI 호환',
      endpoint: 'https://api.openai.com/v1/chat/completions',
      model: 'gpt-5',
      auth: 'Authorization: Bearer',
    },
    anthropic: {
      label: 'Anthropic',
      endpoint: 'https://api.anthropic.com/v1/messages',
      model: 'claude-opus-5-5',
      auth: 'x-api-key',
    },
  };
const ENDPOINTS = Object.values(FORMATS).map((format) => format.endpoint);
type Secret = 'api_key';

// The limits of the LLM's requests, with the backend's ranges and words (backend/agent/llm.py WHOLE and DEFAULTS):
// checked here first, so a value out of range is told at its field before anything is saved. `auto` takes 0 too.
const LIMITS = {
  max_tokens: {
    label: '최대 출력 토큰',
    min: 256,
    max: 1_000_000,
    auto: true,
    initial: 0,
    message: '최대 출력 토큰은 0(자동) 또는 256~1,000,000으로 입력해 주세요.',
    help: '답변 하나의 최대 길이입니다. 0이면 자동으로 서버의 기본값을 씁니다(Anthropic 형식은 16,000). 답변이 잘리면 늘려 주세요.',
  },
  context_window: {
    label: '컨텍스트 크기 (토큰)',
    min: 4_096,
    max: 10_000_000,
    auto: false,
    initial: 64_000,
    message: '컨텍스트 크기는 4,096~10,000,000 토큰으로 입력해 주세요.',
    help: '모델이 한 번에 읽고 쓰는 토큰 수입니다(기본 64,000). 질문에 함께 보내는 문서 근거와 대화 기록이 이 크기에 맞춰 늘거나 줄어듭니다.',
  },
  concurrency: {
    label: '동시 요청 수',
    min: 1,
    max: 8,
    auto: false,
    initial: 3,
    message: '동시 요청 수는 1~8로 입력해 주세요.',
    help: 'LLM 서버에 한꺼번에 보내는 요청 수입니다(1~8, 기본 3). 검색창의 질문이 AI 분석 같은 일괄 작업보다 먼저 갑니다. 서버가 요청 수를 제한하면 줄여 주세요.',
  },
} as const;
type Limit = keyof typeof LIMITS;
const LIMIT_NAMES = Object.keys(LIMITS) as Limit[];
// Why a limit's value cannot be saved, or '' when it can. An empty field is NaN: never a value, so never saved.
function limitProblem(name: Limit, value: number) {
  const { min, max, auto, message } = LIMITS[name];
  return Number.isInteger(value) && ((auto && value === 0) || (value >= min && value <= max))
    ? ''
    : message;
}

// The form as the settings start it: an empty endpoint starts at its format's own, and each custom header's value
// empty, as the screens get only whether one is saved (has_value), never the value itself. Fields the settings gain
// later go along as they are; the limits start at the backend's defaults when the settings do not name them.
const formOf = (settings: Settings): Settings => ({
  ...settings,
  base_url: settings.base_url || FORMATS[settings.api_format].endpoint,
  headers: settings.headers.map((header) => ({ ...header, value: header.value ?? '' })),
  max_tokens: settings.max_tokens ?? LIMITS.max_tokens.initial,
  context_window: settings.context_window ?? LIMITS.context_window.initial,
  concurrency: settings.concurrency ?? LIMITS.concurrency.initial,
});

// A limit of the LLM's requests: a whole number in its range, with what it does. The field refuses another value with
// the backend's own words when the form is saved (reportValidity).
function LimitField({
  name,
  value,
  onChange,
}: {
  name: Limit;
  value: number;
  onChange: (value: number) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const { label, min, max, auto, help } = LIMITS[name];
  const problem = limitProblem(name, value);
  useEffect(() => {
    input.current?.setCustomValidity(problem);
  }, [problem]);
  const id = `llm-${name.replace('_', '-')}`;
  return (
    <div>
      <label>
        {label}
        <input
          ref={input}
          type="number"
          inputMode="numeric"
          min={auto ? 0 : min}
          max={max}
          step={1}
          value={Number.isNaN(value) ? '' : value}
          placeholder={auto ? '0 = 자동' : undefined}
          aria-describedby={`${id}-help`}
          onChange={(e) => onChange(e.target.value === '' ? NaN : Number(e.target.value))}
        />
      </label>
      <small id={`${id}-help`} className="field-help">
        {help}
      </small>
    </div>
  );
}
// The server an endpoint is on (scheme, host and port), which the saved API Key is kept for.
function serverOf(value: string) {
  try {
    const url = new URL(value.trim());
    return `${url.protocol}//${url.host}`;
  } catch {
    return '';
  }
}
// An endpoint reached over plain http on another host than this PC: the API Key, the headers and whatever the AI
// features send (documents, questions) cross the network unencrypted. A local LLM (localhost, 127.0.0.0/8, ::1) is
// reached without leaving the PC.
function plainHttp(value: string) {
  try {
    const url = new URL(value.trim());
    const host = url.hostname.toLowerCase();
    return (
      url.protocol === 'http:' &&
      host !== 'localhost' &&
      !host.endsWith('.localhost') &&
      host !== '[::1]' &&
      !/^127(?:\.\d{1,3}){3}$/.test(host)
    );
  } catch {
    return false;
  }
}

export default function LlmSettings() {
  const settings = useSettings();
  const { setSettings, notify } = useActions();
  const formRef = useRef<HTMLFormElement>(null);
  const advancedRef = useRef<HTMLDetailsElement>(null);
  const [form, setForm] = useState(() => formOf(settings));
  const [keys, setKeys] = useState<Record<Secret, string>>({ api_key: '' });
  const [busy, setBusy] = useState('');
  const [testResult, setTestResult] = useState('');
  const update = (patch: Partial<Settings>) => {
    setForm({ ...form, ...patch });
    setTestResult('');
  };
  const setKey = (name: Secret, value: string) => {
    setKeys({ ...keys, [name]: value });
    setTestResult('');
  };
  const setHeader = (index: number, patch: Partial<LlmHeader>) =>
    update({
      headers: form.headers.map((header, i) => (i === index ? { ...header, ...patch } : header)),
    });
  const format = FORMATS[form.api_format];
  // The saved key and the saved header values stay with the server they were saved for: on another one they are
  // cleared unless typed again.
  const movedServer = serverOf(form.base_url) !== serverOf(settings.base_url);
  const moved = settings.has_api_key && movedServer;
  const movedHeaders =
    movedServer && form.headers.some((header) => header.has_value && !header.value);

  // What the form still needs before it can be saved and used, as a message; '' when complete.
  function missing() {
    if (movedHeaders)
      return 'API 엔드포인트의 서버가 바뀌었습니다. 등록된 헤더 값을 다시 입력하거나 그 헤더를 빼 주세요.';
    const keyed = keys.api_key.trim() || (settings.has_api_key && !moved);
    const needsKey = form.headers.some(
      (header) => header.value.includes('{api_key}') || (!header.value && header.uses_api_key),
    );
    return !keyed && needsKey ? '헤더의 {api_key} 자리에 넣을 API Key를 입력해 주세요.' : '';
  }

  async function save(test: boolean) {
    // A limit out of range is told at its field, which the advanced settings may be hiding.
    if (advancedRef.current && LIMIT_NAMES.some((name) => limitProblem(name, form[name] ?? NaN)))
      advancedRef.current.open = true;
    // The endpoint and the model, and the advanced settings' ranges, are the form's own checks.
    if (!formRef.current?.reportValidity()) return;
    const problem = missing();
    if (problem) {
      notify(problem, true);
      return;
    }
    setBusy(test ? 'test' : 'save');
    setTestResult('');
    try {
      const result = await api<Settings>('settings.update', {
        provider: 'general',
        api_format: form.api_format,
        base_url: form.base_url,
        model_name: form.model_name,
        // A saved value the user did not retype stays: the header goes by its name alone.
        headers: form.headers.map((header) =>
          header.value || !header.has_value
            ? { name: header.name, value: header.value }
            : { name: header.name },
        ),
        api_key: keys.api_key,
        temperature: form.temperature,
        timeout: form.timeout,
        ca_bundle: form.ca_bundle,
        max_tokens: form.max_tokens,
        context_window: form.context_window,
        concurrency: form.concurrency,
      });
      setSettings(result);
      setForm(formOf(result));
      setKeys({ api_key: '' });
      if (test) setTestResult((await api<{ message: string }>('settings.test')).message);
      else notify('LLM 연결 설정을 저장했습니다.');
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy('');
    }
  }

  async function clear(name: Secret) {
    setBusy('save');
    try {
      const result = await api<Settings>('settings.update', { [`clear_${name}`]: true });
      setSettings(result);
      setForm((current) => ({
        ...current,
        has_api_key: result.has_api_key,
      }));
      notify('API Key를 삭제했습니다.');
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy('');
    }
  }

  // Whether the main process keeps the secrets encrypted by the OS (settings.get secure_storage). Without it a new API
  // Key stays in memory until the app closes, and the headers' values are written to the settings file as they are
  // (electron/main.cjs updateSettings), which the screen says rather than promise an encryption that does not happen.
  const secure = settings.secure_storage;
  const storage = (stored: boolean, name: Secret, what: string) => (
    <>
      {secure
        ? '운영체제 보안 저장소로 암호화해 저장합니다.'
        : 'OS 보안 저장소를 쓸 수 없어 새로 넣은 값은 앱을 끌 때까지 메모리에만 보관합니다.'}
      {stored && (
        <>
          {' '}
          <button
            type="button"
            className="text-button danger"
            disabled={!!busy}
            onClick={() => void clear(name)}
          >
            등록된 {what} 삭제
          </button>
        </>
      )}
    </>
  );

  return (
    <form
      ref={formRef}
      className="admin-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save(false);
      }}
    >
      <p className="section-lead">
        AI 기능은 사용 중인 LLM으로 요청을 보냅니다. 문서와 기록은 이 PC에만 저장되고, AI 기능을 쓸
        때 필요한 내용만 전송됩니다.
      </p>
      {form.load_error && (
        <p className="banner warn" role="alert">
          <CircleAlert size={15} />
          {form.load_error}
        </p>
      )}
      <div className="llm-panel">
        <p className="llm-note">
          OpenAI·Anthropic API나 OpenAI 형식을 따르는 다른 서버·로컬 LLM에 연결합니다. 필요한 헤더는
          Custom header에 더합니다.
        </p>
        <div className="segmented" role="radiogroup" aria-label="API 형식">
          {(Object.entries(FORMATS) as [LlmFormat, (typeof FORMATS)[LlmFormat]][]).map(
            ([id, item]) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={form.api_format === id}
                tabIndex={form.api_format === id ? 0 : -1}
                onKeyDown={rove}
                className={form.api_format === id ? 'active' : ''}
                onClick={() =>
                  update({
                    api_format: id,
                    // A default endpoint follows the format; one typed in stays.
                    base_url:
                      !form.base_url || ENDPOINTS.includes(form.base_url)
                        ? item.endpoint
                        : form.base_url,
                  })
                }
              >
                {item.label}
              </button>
            ),
          )}
        </div>
        <label>
          API 엔드포인트 <span className="required">*</span>
          <input
            value={form.base_url}
            onChange={(e) => update({ base_url: e.target.value })}
            placeholder={format.endpoint}
            type="url"
            required
          />
          <small>
            요청을 보낼 전체 URL입니다. 게이트웨이나 프록시 서버를 거친다면 그 주소를 넣습니다.
          </small>
        </label>
        {plainHttp(form.base_url) && (
          <p className="banner warn" role="status">
            <CircleAlert size={15} />
            http:// 주소는 암호화되지 않아 API Key와 헤더, AI 기능이 보내는 문서·질문이 네트워크에
            그대로 드러날 수 있습니다. 이 PC의 로컬 LLM(localhost·127.0.0.1)이 아니라면 https://
            주소를 쓰세요.
          </p>
        )}
        <label>
          API Key
          <SecretInput
            label="API Key"
            value={keys.api_key}
            saved={form.has_api_key}
            stale={moved}
            onChange={(value) => setKey('api_key', value)}
            placeholder={
              moved
                ? '새 서버에 쓸 키 입력 · 필요 없는 서버면 비워 둡니다'
                : form.has_api_key
                  ? '등록된 키 사용 중 · 바꾸려면 새 키 입력'
                  : '필요 없는 서버면 비워 둡니다'
            }
          />
          <small>
            {format.auth} 헤더로 보냅니다. 헤더 값에 {'{api_key}'}를 쓰면 그 헤더에만 넣습니다.{' '}
            {storage(form.has_api_key, 'api_key', '키')}
          </small>
        </label>
        {(moved || movedHeaders) && (
          <p className="banner warn" role="status">
            <CircleAlert size={15} />
            API 엔드포인트의 서버가 바뀌어, 저장하면 등록된 {moved ? '키' : ''}
            {moved && movedHeaders ? '와 ' : ''}
            {movedHeaders ? '헤더 값' : ''}을 지웁니다. 이 서버에 쓸 값을 다시 입력해 주세요.
          </p>
        )}
        <label>
          모델 이름 <span className="required">*</span>
          <input
            required
            value={form.model_name}
            onChange={(e) => update({ model_name: e.target.value })}
            placeholder={format.model}
          />
        </label>
        <fieldset className="llm-headers">
          <legend>Custom header</legend>
          {form.headers.map((header, index) => (
            <div className="llm-header" key={index}>
              <input
                aria-label={`헤더 ${index + 1} 이름`}
                value={header.name}
                spellCheck={false}
                onChange={(e) => setHeader(index, { name: e.target.value })}
                placeholder="헤더 이름"
              />
              {/* The field keeps its name "헤더 1 값"; its buttons say "헤더 1의 값 …", so looking the field up by its
                  name (a person's screen reader, an AI tool's fill) finds the field alone. */}
              <SecretInput
                label={`헤더 ${index + 1}의 값`}
                name={`헤더 ${index + 1} 값`}
                value={header.value}
                saved={!!header.has_value}
                stale={!!header.has_value && movedServer}
                onChange={(value) => setHeader(index, { value })}
                placeholder={header.has_value ? '등록된 값 사용 중 · 바꾸려면 새 값 입력' : '값'}
              />
              <button
                type="button"
                className="icon-button"
                aria-label={`헤더 ${index + 1} 빼기`}
                onClick={() => update({ headers: form.headers.filter((_, i) => i !== index) })}
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
          <button
            type="button"
            className="button secondary small"
            onClick={() => update({ headers: [...form.headers, { name: '', value: '' }] })}
          >
            <Plus size={14} />
            헤더 추가
          </button>
          <small>
            값의 {'{api_key}'}는 위 API Key로, {'{uuid}'}는 요청마다 새 ID로 바뀝니다. 기본 헤더와
            이름이 같으면 이 값을 보냅니다.{' '}
            {secure
              ? '값은 API Key처럼 운영체제 보안 저장소로 암호화해 저장하고 화면에 다시 보이지 않으며, 바꿀 때는 ‘다시 입력’으로 새 값을 넣습니다.'
              : 'OS 보안 저장소를 쓸 수 없어 값은 암호화하지 않고 앱 설정 폴더의 설정 파일(settings.json)에 그대로 저장합니다. 화면에는 다시 보이지 않으며, 바꿀 때는 ‘다시 입력’으로 새 값을 넣습니다.'}
          </small>
        </fieldset>
        <details className="advanced" ref={advancedRef}>
          <summary>고급 설정</summary>
          <div className="form-grid">
            <label>
              Temperature
              <input
                type="number"
                min="0"
                max="2"
                step="0.1"
                value={form.temperature}
                onChange={(e) => update({ temperature: Number(e.target.value) })}
              />
              {form.api_format === 'anthropic' && (
                <small>Anthropic 형식에는 보내지 않습니다.</small>
              )}
            </label>
            <label>
              응답 제한 시간 (초)
              <input
                type="number"
                min="5"
                max="300"
                value={form.timeout}
                onChange={(e) => update({ timeout: Number(e.target.value) })}
              />
            </label>
            {LIMIT_NAMES.map((name) => (
              <LimitField
                key={name}
                name={name}
                value={form[name] ?? LIMITS[name].initial}
                onChange={(value) => update({ [name]: value })}
              />
            ))}
          </div>
          <label>
            CA 인증서 경로
            <input
              value={form.ca_bundle}
              onChange={(e) => update({ ca_bundle: e.target.value })}
              placeholder="C:\certificates\private-ca.pem"
            />
            <small>
              선택 항목입니다. 사설·자체 서명 인증서를 쓰는 서버라면 그 CA 인증서(PEM)를 지정합니다.
              TLS 인증서 검증은 항상 켜져 있습니다.
            </small>
          </label>
        </details>
      </div>
      {testResult && (
        <p className="success-box" role="status">
          <Check size={16} />
          {testResult}
        </p>
      )}
      <div className="form-actions">
        <button
          type="button"
          className="button secondary"
          disabled={!!busy}
          data-saves
          onClick={() => void save(true)}
        >
          {busy === 'test' ? <LoaderCircle className="spin" size={15} /> : <Unplug size={15} />}
          저장하고 연결 테스트
        </button>
        <button type="submit" className="button primary" disabled={!!busy}>
          {busy === 'save' && <LoaderCircle className="spin" size={15} />}
          저장하고 사용
        </button>
      </div>
    </form>
  );
}
