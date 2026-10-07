import { useCallback, useEffect, useState } from 'react';
import {
  ArrowDown,
  ArrowUp,
  Check,
  CircleAlert,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  LogIn,
  Pencil,
  Plus,
  ShieldCheck,
  Square,
  Trash2,
} from 'lucide-react';
import {
  api,
  date,
  type LoginMethod,
  type LoginStep,
  type Site,
  type SiteCheck,
  type SiteKind,
  type SitesState,
  type SiteWindows,
  type WebDiagnosis,
} from '@/lib/api';
import { hasScheme, httpUrl, isConfluencePageUrl, jiraIssueKey } from '@/lib/links';
import { useApp } from '../app';
import Confirm from '../confirm';
import Diagnosis from '../diagnosis';
import { rove } from '../roving';

const KINDS: Record<SiteKind, string> = {
  jira: 'JIRA',
  confluence: 'Confluence',
};
const PLACEHOLDERS: Record<SiteKind, { url: string; name: string; hint: string }> = {
  jira: {
    url: 'https://jira.example.com/jira',
    name: 'JIRA',
    hint: '티켓 링크를 붙여 넣으면 사이트 주소만 남깁니다.',
  },
  confluence: {
    url: 'https://confluence.example.com/wiki',
    name: 'Confluence',
    hint: '페이지 링크를 붙여 넣으면 사이트 주소만 남깁니다.',
  },
};
// An address typed without its scheme opens as in the browser's address bar (backend/agent/browser_import.py _reach).
const SCHEMELESS =
  'http:// 또는 https://를 빼면 https로 먼저 접속해 보고, 안 되면 http로 접속합니다.';
// Choices around the preset methods: detect and remember, the user's own steps, or no typing.
const AUTO = {
  id: 'auto',
  label: '자동 감지 (추천)',
  description:
    '표준 로그인 방식부터 아래 순서대로 화면에 맞는 방식을 찾아 로그인하고, 성공한 방식을 저장해 다음부터 먼저 씁니다. 방식이 화면에 맞지 않거나 로그인에 실패하면 다음 방식을 시도하되, 앞서 보낸 것과 같은 입력란에는 다시 보내지 않습니다.',
};
const CUSTOM = {
  id: 'custom',
  label: '사용자 지정 (단계 직접 편집)',
  description:
    '텍스트 입력과 버튼 클릭을 원하는 순서로 직접 정합니다. 위치는 XPath나 CSS 선택자로, 브라우저 개발자 도구에서 요소를 오른쪽 클릭 → Copy → Copy XPath 또는 Copy selector로 복사합니다. 이 단계로 먼저 로그인하고, 실패하면 다른 표준 로그인 방식부터 다른 방식을 차례로 시도합니다.',
};
const SESSION = {
  id: 'session',
  label: '자동 입력 안 함',
  description:
    '로그인 정보를 입력하지 않고, ‘로그인 창 열기’에서 직접 로그인한 브라우저 세션을 사용합니다. 추가 인증(OTP 등)이 필요한 사이트에 쓰세요.',
};
// A chosen method goes first; the others follow when it does not sign in (electron/sites.cjs methodsOf).
const FALLBACK = '실패하면 다른 표준 로그인 방식부터 다른 방식을 차례로 시도합니다.';
// The method of the fields the LLM found, as electron/sites.cjs names it.
const LEARNED = { id: 'learned', label: 'AI가 찾은 로그인 화면' };
const VALUES = { id: '로그인 ID', password: '비밀번호', text: '직접 입력' } as const;
// Steps before the login button: a method has 16 at most, the login button included.
const MAX_STEPS = 15;

// The origin a saved password belongs to, as electron/sites.cjs originOf: scheme://host[:port], or //host[:port] for an
// address typed without its scheme; '' while it is none.
function originOf(value: string) {
  const url = httpUrl(value);
  if (!url) return '';
  return hasScheme(value) ? url.origin : `//${url.host}`;
}

// How an account signs in, in words: the chosen method, or what automatic detection found.
export function loginSummary(site: Site) {
  if (site.method === 'session') return '자동 입력 안 함 · 브라우저에 남은 로그인 사용';
  if (site.method === 'auto')
    return site.detected_label
      ? `자동 감지됨 · ${site.detected_label}`
      : '자동 감지 · 처음 로그인할 때 여러 방식을 시도합니다';
  return site.method_label;
}

// A step of the user's own method while it is edited, before the login button; key tells the rows apart as they move,
// and skip shows its condition (unless), which few steps need.
type StepDraft = {
  key: number;
  action: 'click' | 'fill';
  value: 'id' | 'password' | 'text';
  text: string;
  target: string;
  optional: boolean;
  unless: string;
  skip: boolean;
};
type Draft = {
  id?: string;
  kind: SiteKind;
  name: string;
  url: string;
  login_id: string;
  password: string;
  method: string;
  steps: StepDraft[];
  // The login button of the user's own method; '' presses Enter in the field typed last.
  submit: string;
};

let stepKeys = 0;
const blankStep = (action: StepDraft['action'], value: StepDraft['value'] = 'id'): StepDraft => ({
  key: ++stepKeys,
  action,
  value,
  text: '',
  target: '',
  optional: action === 'click',
  unless: '',
  skip: false,
});

// An XPath as the browser's developer tools copy it: the xpath= that the site store puts before it is left out.
const shownTarget = (target = '') => target.replace(/^xpath=(?=\(?\/)/, '');

// Steps of a method (the catalog's, the LLM's or the user's) as the editor holds them.
function draftSteps(steps: LoginStep[]): Pick<Draft, 'steps' | 'submit'> {
  const last = steps.at(-1);
  return {
    steps: steps
      .filter((step) => step.action !== 'submit')
      .map((step) => ({
        ...blankStep(step.action === 'click' ? 'click' : 'fill', step.value ?? 'id'),
        text: step.text ?? '',
        target: shownTarget(step.target),
        optional: !!step.optional,
        unless: shownTarget(step.unless),
        skip: !!step.unless,
      })),
    submit: last?.action === 'submit' ? shownTarget(last.target) : '',
  };
}

// The user's own steps as the site store takes them, the login button last.
function stepsOf(draft: Draft): LoginStep[] {
  return [
    ...draft.steps.map((step): LoginStep => {
      const flexible = step.action === 'click' || step.value === 'text';
      return {
        action: step.action,
        target: step.target,
        ...(step.action === 'fill' && { value: step.value }),
        ...(step.action === 'fill' && step.value === 'text' && { text: step.text }),
        ...(flexible && { optional: step.optional }),
        ...(flexible && step.skip && step.unless.trim() && { unless: step.unless }),
      };
    }),
    { action: 'submit', target: draft.submit },
  ];
}

// A new user-defined method types the ID and the password.
const startSteps = () => ({
  steps: [blankStep('fill', 'id'), blankStep('fill', 'password')],
  submit: '',
});

const draftOf = (site: Site): Draft => ({
  id: site.id,
  kind: site.kind,
  name: site.name,
  url: site.url,
  login_id: site.login_id,
  password: '',
  method: site.method,
  ...(site.custom.steps.length ? draftSteps(site.custom.steps) : startSteps()),
});
const blankDraft = (url = '', kind?: SiteKind): Draft => ({
  kind: kind ?? (!url || jiraIssueKey(url) || !isConfluencePageUrl(url) ? 'jira' : 'confluence'),
  name: '',
  url,
  login_id: '',
  password: '',
  method: 'auto',
  ...startSteps(),
});

// The elements that show a user-defined screen, as electron/sites.cjs detectOf finds them.
function detectOf(steps: LoginStep[]) {
  const first = steps.findIndex((step) => step.action === 'fill');
  const targets = steps.slice(0, first + 1).map((step) => step.target ?? '');
  return [...new Set(targets.filter(Boolean))].slice(-4);
}

// The methods an auto site tries, in order, as electron/sites.cjs methodsOf: standard SSO methods,
// then the rest; the saved method (`saved`, or the LLM's fields) first in its group.
function orderOf(methods: LoginMethod[], saved: string | null, learned: LoginStep[] | null) {
  const found: LoginMethod | null =
    saved === LEARNED.id && learned
      ? {
          ...LEARNED,
          description: '로그인 방식이 맞지 않던 화면에서 AI가 찾아 로그인한 입력란입니다.',
          priority: false,
          detect: detectOf(learned),
          steps: learned,
        }
      : (methods.find((method) => method.id === saved) ?? null);
  const priority = methods.filter((method) => method.priority && method !== found);
  const others = methods.filter((method) => !method.priority && method !== found);
  if (!found) return [...priority, ...others];
  return found.priority ? [found, ...priority, ...others] : [...priority, found, ...others];
}

export default function SitesSection({ draft }: { draft?: { url: string; kind?: SiteKind } }) {
  const { notify, configured, preferences, savePreferences } = useApp();
  const [state, setState] = useState<SitesState | null>(null);
  const [windows, setWindows] = useState<SiteWindows | null>(null);
  const [editing, setEditing] = useState<Draft | null>(
    draft ? blankDraft(draft.url, draft.kind) : null,
  );
  const [checking, setChecking] = useState('');
  const [stopping, setStopping] = useState(false);
  const [results, setResults] = useState<
    Record<string, { ok: boolean; message: string; diagnosis?: WebDiagnosis | null }>
  >({});
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    // The status first: once it shows a login check has ended, the list read after it has the check's result.
    const status = await api<SiteWindows>('sites.status');
    setState(await api<SitesState>('sites.list'));
    setWindows(status);
  }, []);
  useEffect(() => {
    void load().catch((e) => setError(e.message));
  }, [load]);
  // The login check that is running: this screen's own, or one that kept running while another section was shown.
  const running =
    checking ||
    (windows && Object.values(windows).find((window) => window.checking)?.checking) ||
    '';
  // Follow an open login window until the user closes it, and a check this screen did not start until it ends.
  const following =
    windows && Object.values(windows).some((window) => window.open || window.checking);
  useEffect(() => {
    if (!following) return;
    const timer = setInterval(() => void load().catch(() => {}), 1500);
    return () => clearInterval(timer);
  }, [following, load]);
  const replace = (site: Site) =>
    setState(
      (current) =>
        current && {
          ...current,
          sites: current.sites.some((s) => s.id === site.id)
            ? current.sites.map((s) => (s.id === site.id ? site : s))
            : [...current.sites, site],
        },
    );
  async function check(site: Site) {
    setChecking(site.id);
    setResults(({ [site.id]: _, ...rest }) => rest);
    try {
      const result = await api<SiteCheck>('sites.check', { id: site.id });
      // A stopped check changed nothing; the stop says so. A site deleted meanwhile comes back without one.
      if (!result.stopped) {
        if (result.site) replace(result.site);
        setResults((current) => ({ ...current, [site.id]: result }));
      }
    } catch (e) {
      setResults((current) => ({
        ...current,
        [site.id]: { ok: false, message: (e as Error).message },
      }));
    } finally {
      // A status read while the check ran would show it running still.
      await load().catch(() => {});
      setChecking('');
    }
  }
  // sites.check_cancel answers once the check has ended.
  async function stopCheck(site: Site) {
    setStopping(true);
    try {
      const { stopped } = await api<{ stopped: boolean }>('sites.check_cancel', { id: site.id });
      if (stopped) notify('로그인 확인을 중지했습니다.');
      await load().catch(() => {});
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setStopping(false);
    }
  }
  async function toggleWindow(site: Site) {
    try {
      const status = windows?.[site.kind];
      setWindows(
        await api<SiteWindows>(
          status?.open && status.site_id === site.id ? 'sites.login_close' : 'sites.login',
          { id: site.id },
        ),
      );
    } catch (e) {
      notify((e as Error).message, true);
    }
  }
  if (!state)
    return error ? (
      <p className="inline-error">{error}</p>
    ) : (
      <div className="loading-page small">
        <LoaderCircle className="spin" size={22} />
      </div>
    );
  return (
    <div className="sites">
      <p className="section-lead">
        JIRA·Confluence 주소마다 로그인 계정과 로그인 방식을 저장합니다. 가져오기는 링크 주소에 맞는
        계정으로 백그라운드에서 로그인하고, 처음 로그인하는 주소는 여러 방식을 시도해 성공한 방식을
        저장합니다.
      </p>
      <label className="setting sites-assist">
        <span>
          <strong>로그인·접속 실패 시 AI 분석</strong>
          <small>
            저장된 방식이 맞지 않는 로그인 화면은 LLM이 입력란을 찾아 한 번 로그인하고, 열리지 않은
            페이지는 다시 열지 판단합니다. 끝내 실패하면 원인과 해결 방법을 알려 줍니다. LLM에는
            화면의 주소(쿼리 제외)·제목·보이는 글·입력란 설명과 오류를 보내며, ID·비밀번호와
            입력란의 값은 보내지 않습니다.
            {!configured && ' LLM을 연결하면 동작합니다.'}
          </small>
        </span>
        <span className="switch">
          <input
            type="checkbox"
            role="switch"
            checked={preferences.web_assist}
            onChange={(e) =>
              void savePreferences({ web_assist: e.target.checked }).catch((error) =>
                notify(error.message, true),
              )
            }
          />
          <span aria-hidden="true" />
        </span>
      </label>
      {state.error && (
        <p className="banner warn" role="alert">
          <CircleAlert size={15} />
          {state.error}
        </p>
      )}
      {!state.secure_storage && (
        <p className="banner warn">
          <CircleAlert size={15} />
          OS 보안 저장소를 쓸 수 없어 비밀번호는 앱을 끌 때까지만 보관합니다. 로그인 ID와 방식은
          저장됩니다.
        </p>
      )}
      {editing && !editing.id && (
        <SiteForm
          draft={editing}
          methods={state.methods}
          onCancel={() => setEditing(null)}
          onSaved={(site) => {
            replace(site);
            setEditing(null);
            notify('사이트 계정을 저장했습니다. 로그인 확인으로 로그인 방식을 확인하세요.');
          }}
        />
      )}
      {!editing && (
        <button
          className="button secondary"
          disabled={!!state.error}
          onClick={() => setEditing(blankDraft())}
        >
          <Plus size={15} />
          사이트 추가
        </button>
      )}
      {!state.sites.length && !editing && (
        <p className="empty-note">
          등록된 사이트가 없습니다. 계정이 없는 주소는 ‘로그인 창 열기’로 직접 로그인한 세션을
          사용합니다.
        </p>
      )}
      <ul className="site-list">
        {state.sites.map((site) => {
          const status = windows?.[site.kind];
          const windowOpen = !!status?.open && status.site_id === site.id;
          const result = results[site.id];
          return (
            <li key={site.id} className="site" aria-label={`${site.name} 사이트`}>
              {editing?.id === site.id ? (
                <SiteForm
                  draft={editing}
                  site={site}
                  methods={state.methods}
                  onCancel={() => setEditing(null)}
                  onSaved={(saved) => {
                    replace(saved);
                    setEditing(null);
                    setResults(({ [site.id]: _, ...rest }) => rest);
                    notify('사이트 계정을 저장했습니다.');
                  }}
                />
              ) : (
                <>
                  <div className="site-head">
                    <span className={`kind-badge ${site.kind}`}>{KINDS[site.kind]}</span>
                    <strong>{site.name}</strong>
                    <span className="site-state">
                      {site.last_error ? (
                        <span className="danger">
                          <CircleAlert size={13} />
                          로그인 실패
                        </span>
                      ) : site.verified_at ? (
                        <span className="ok">
                          <Check size={13} />
                          로그인 확인 {date(site.verified_at)}
                        </span>
                      ) : (
                        <span className="muted">아직 로그인하지 않음</span>
                      )}
                    </span>
                  </div>
                  <p className="site-url break-path">
                    {site.url || '주소 없음 · 이전 버전에서 옮긴 계정'}
                  </p>
                  {!site.url && (
                    <p className="inline-error">
                      주소가 없는 계정은 로그인에 쓰지 않습니다. 비밀번호가 다른 사이트에 입력되지
                      않도록, 수정에서 이 계정을 쓸 {KINDS[site.kind]} 주소와 비밀번호를 입력해
                      주세요.
                    </p>
                  )}
                  <p className="site-login">
                    <KeyRound size={13} />
                    {site.login_id || 'ID 없음'}
                    {site.method !== 'session' && !site.has_password && ' · 비밀번호 없음'}
                    {' · '}
                    {loginSummary(site)}
                  </p>
                  {site.last_error && !result && <p className="inline-error">{site.last_error}</p>}
                  {site.last_error && !result && site.last_diagnosis && (
                    <Diagnosis diagnosis={site.last_diagnosis} />
                  )}
                  {result && (
                    <p className={result.ok ? 'success-box' : 'inline-error'} role="status">
                      {result.ok && <ShieldCheck size={15} />}
                      {result.message}
                    </p>
                  )}
                  {result?.diagnosis && <Diagnosis diagnosis={result.diagnosis} />}
                  {windowOpen && (
                    <p className="site-window" role="status">
                      <LogIn size={14} />
                      로그인 창이 열려 있습니다. 인증을 완료한 뒤 창을 닫아 주세요.
                    </p>
                  )}
                  {status?.site_id === site.id && !status.open && status.error && (
                    <p className="inline-error">{status.error}</p>
                  )}
                  <div className="site-actions">
                    <button
                      className="button secondary small"
                      disabled={!!running || !site.url || site.method === 'session' || windowOpen}
                      title={
                        !site.url
                          ? '주소를 입력하면 확인할 수 있습니다'
                          : site.method === 'session'
                            ? '자동 입력하지 않는 계정은 로그인 창에서 확인하세요'
                            : undefined
                      }
                      onClick={() => void check(site)}
                    >
                      {running === site.id ? (
                        <LoaderCircle className="spin" size={14} />
                      ) : (
                        <ShieldCheck size={14} />
                      )}
                      {running !== site.id
                        ? '로그인 확인'
                        : stopping
                          ? '중지하는 중…'
                          : '로그인 확인 중…'}
                    </button>
                    {running === site.id && (
                      <button
                        className="button secondary small"
                        disabled={stopping}
                        onClick={() => void stopCheck(site)}
                      >
                        <Square size={14} />
                        중지
                      </button>
                    )}
                    <button
                      className="button secondary small"
                      disabled={!!running || !site.url || (!!status?.open && !windowOpen)}
                      onClick={() => void toggleWindow(site)}
                    >
                      <LogIn size={14} />
                      {windowOpen ? '로그인 창 닫기' : '로그인 창 열기'}
                    </button>
                    <span className="spacer" />
                    {/* Not while another form is open, which would lose what was typed in it, nor while the site
                        signs in: its result is about the address it had then. */}
                    <button
                      className="icon-button"
                      aria-label={`${site.name} 수정`}
                      title="수정"
                      disabled={!!running || !!editing || windowOpen}
                      onClick={() => setEditing(draftOf(site))}
                    >
                      <Pencil size={15} />
                    </button>
                    <Confirm
                      iconOnly
                      name={`${site.name} 삭제`}
                      label="삭제"
                      icon={<Trash2 size={15} />}
                      question="이 사이트 계정을 삭제할까요?"
                      disabled={!!running || windowOpen}
                      onConfirm={async () => {
                        try {
                          await api('sites.delete', { id: site.id });
                          setState(
                            (current) =>
                              current && {
                                ...current,
                                sites: current.sites.filter((s) => s.id !== site.id),
                              },
                          );
                          notify('사이트 계정을 삭제했습니다.');
                        } catch (e) {
                          notify((e as Error).message, true);
                        }
                      }}
                    />
                  </div>
                </>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function SiteForm({
  draft: initial,
  site,
  methods,
  onCancel,
  onSaved,
}: {
  draft: Draft;
  site?: Site;
  methods: LoginMethod[];
  onCancel: () => void;
  onSaved: (site: Site) => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const set = (patch: Partial<Draft>) => setDraft((current) => ({ ...current, ...patch }));
  const choices = [AUTO, ...methods, CUSTOM, SESSION];
  const chosen = choices.find((method) => method.id === draft.method) ?? AUTO;
  const preset = methods.find((method) => method.id === draft.method);
  // Any method's steps, copied into the user's own to change them.
  const copy = (steps: LoginStep[]) => set({ method: CUSTOM.id, ...draftSteps(steps) });
  // An auto site's saved method counts while the form keeps its address, kind and method.
  const saved =
    site?.method === 'auto' && draft.kind === site.kind && draft.url.trim() === site.url
      ? site.detected_method
      : null;
  // The saved password belongs to the address it was typed for: moved to another origin or kind, the account keeps
  // one only when it is typed again with the change (electron/sites.cjs).
  const moved =
    !!site?.has_password &&
    (draft.kind !== site.kind || originOf(draft.url) !== originOf(site.url));
  return (
    <form
      className="site-form"
      aria-label={site ? `${site.name} 수정` : '사이트 추가'}
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        setError('');
        try {
          onSaved(
            await api<Site>('sites.save', {
              id: draft.id,
              kind: draft.kind,
              name: draft.name,
              url: draft.url,
              login_id: draft.login_id,
              password: draft.password,
              method: draft.method,
              // Another method keeps the steps saved before, for when the user comes back to them.
              ...(draft.method === CUSTOM.id && { custom: { steps: stepsOf(draft) } }),
            }),
          );
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <fieldset disabled={busy}>
        <div className="segmented" role="radiogroup" aria-label="사이트 종류">
          {(Object.entries(KINDS) as [SiteKind, string][]).map(([kind, label]) => (
            <button
              key={kind}
              type="button"
              role="radio"
              aria-checked={draft.kind === kind}
              tabIndex={draft.kind === kind ? 0 : -1}
              onKeyDown={rove}
              className={draft.kind === kind ? 'active' : ''}
              onClick={() =>
                set({
                  kind,
                  url: draft.url,
                })
              }
            >
              {label}
            </button>
          ))}
        </div>
        <div className="form-grid">
          <label>
            주소
            <input
              inputMode="url"
              spellCheck={false}
              required
              value={draft.url}
              maxLength={2048}
              onChange={(e) => set({ url: e.target.value })}
              placeholder={PLACEHOLDERS[draft.kind].url}
            />
            <small>
              {PLACEHOLDERS[draft.kind].hint} {SCHEMELESS}
            </small>
          </label>
          <label>
            이름
            <input
              value={draft.name}
              maxLength={80}
              onChange={(e) => set({ name: e.target.value })}
              placeholder={PLACEHOLDERS[draft.kind].name}
            />
          </label>
          <label>
            로그인 ID
            <input
              value={draft.login_id}
              maxLength={256}
              autoComplete="username"
              required={draft.method !== 'session'}
              onChange={(e) => set({ login_id: e.target.value })}
              placeholder="계정 ID"
            />
          </label>
          <label>
            비밀번호
            <span className="secret-input">
              <KeyRound size={15} />
              <input
                type={showPassword ? 'text' : 'password'}
                value={draft.password}
                maxLength={1024}
                autoComplete="new-password"
                required={moved && draft.method !== 'session'}
                onChange={(e) => set({ password: e.target.value })}
                placeholder={
                  moved
                    ? '새 주소에 쓸 비밀번호'
                    : site?.has_password
                      ? '저장된 비밀번호 사용 중 · 바꾸려면 입력'
                      : '계정 비밀번호'
                }
              />
              <button
                type="button"
                className="icon-button"
                aria-label={showPassword ? '비밀번호 숨기기' : '비밀번호 표시'}
                onClick={() => setShowPassword(!showPassword)}
              >
                {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </span>
          </label>
        </div>
        {moved && (
          <p className="banner warn" role="status">
            <CircleAlert size={15} />
            주소나 종류를 바꾸면 저장된 비밀번호를 지웁니다. 비밀번호가 다른 사이트에 입력되지
            않도록, 새 주소에 쓸 비밀번호를 다시 입력해 주세요.
          </p>
        )}
        <label>
          로그인 방식
          <select value={draft.method} onChange={(e) => set({ method: e.target.value })}>
            <option value={AUTO.id}>{AUTO.label}</option>
            <optgroup label="SSO 링크·AD SSO (먼저 시도)">
              {methods
                .filter((method) => method.priority)
                .map((method) => (
                  <option key={method.id} value={method.id}>
                    {method.label}
                  </option>
                ))}
            </optgroup>
            <optgroup label="그 밖의 로그인 화면">
              {methods
                .filter((method) => !method.priority)
                .map((method) => (
                  <option key={method.id} value={method.id}>
                    {method.label}
                  </option>
                ))}
            </optgroup>
            <option value={CUSTOM.id}>{CUSTOM.label}</option>
            <option value={SESSION.id}>{SESSION.label}</option>
          </select>
          <small>{chosen.description}</small>
          {preset && <small>{FALLBACK}</small>}
          {site?.method === 'auto' && draft.method === 'auto' && site.detected_label && (
            <small>지금 저장된 방식: {site.detected_label}</small>
          )}
        </label>
        {draft.method === AUTO.id && (
          <MethodOrder
            methods={orderOf(methods, saved, site?.learned?.steps ?? null)}
            saved={saved}
            onCopy={copy}
          />
        )}
        {preset && (
          <div className="method-chosen" aria-label={`${preset.label} 단계`} role="group">
            <MethodSteps method={preset} onCopy={copy} />
          </div>
        )}
        {draft.method === CUSTOM.id && <StepEditor draft={draft} set={set} />}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="form-actions">
          <button type="button" className="button secondary" onClick={onCancel}>
            취소
          </button>
          <button className="button primary">
            {busy && <LoaderCircle className="spin" size={15} />}
            사이트 저장
          </button>
        </div>
      </fieldset>
    </form>
  );
}

// What each step of a method does: the value it types or the button it clicks, and where.
function StepList({ steps }: { steps: LoginStep[] }) {
  return (
    <ol className="login-steps">
      {steps.map((step, index) => (
        <li key={index}>
          <span className="login-step-what">
            {step.action === 'fill'
              ? `텍스트 입력 · ${step.value === 'text' ? `‘${step.text}’` : VALUES[step.value ?? 'id']}`
              : step.action === 'click'
                ? '버튼 클릭'
                : '로그인 버튼 클릭'}
            {step.optional && ' · 있을 때만'}
          </span>
          <code className="break-path">
            {step.target ? shownTarget(step.target) : 'Enter (마지막 입력란에서)'}
          </code>
          {step.unless && (
            <small>
              이 요소가 보이면 건너뜀:{' '}
              <code className="break-path">{shownTarget(step.unless)}</code>
            </small>
          )}
        </li>
      ))}
    </ol>
  );
}

// A method's steps and the elements that tell its login screen, with a button to change a copy of them.
function MethodSteps({
  method,
  onCopy,
}: {
  method: LoginMethod;
  onCopy: (steps: LoginStep[]) => void;
}) {
  return (
    <div className="method-steps">
      <StepList steps={method.steps} />
      <p className="method-detect">
        <small>로그인 화면 확인: 다음 중 하나가 보이면 이 방식의 화면으로 봅니다.</small>
        {method.detect.map((target) => (
          <code key={target} className="break-path">
            {shownTarget(target)}
          </code>
        ))}
      </p>
      <button type="button" className="button secondary small" onClick={() => onCopy(method.steps)}>
        <Copy size={14} />
        복사해서 사용자 지정으로 고치기
      </button>
    </div>
  );
}

// The order an auto site tries the methods in, each with its steps.
function MethodOrder({
  methods,
  saved,
  onCopy,
}: {
  methods: LoginMethod[];
  saved: string | null;
  onCopy: (steps: LoginStep[]) => void;
}) {
  return (
    <div className="method-order" role="group" aria-label="시도 순서">
      <strong>로그인할 때 시도하는 순서</strong>
      <ol>
        {methods.map((method) => (
          <li key={method.id}>
            <details>
              <summary>
                {method.label}
                {method.priority && <span className="method-tag">우선 시도</span>}
                {method.id === saved && <span className="method-tag saved">저장된 방식</span>}
                <small>단계 {method.steps.length}개</small>
              </summary>
              <MethodSteps method={method} onCopy={onCopy} />
            </details>
          </li>
        ))}
      </ol>
      <small>
        방식 이름을 누르면 어느 위치(XPath·선택자)에 무엇을 입력하고 어떤 버튼을 누르는지 보입니다.
      </small>
    </div>
  );
}

// The user's own steps: texts to type and buttons to click in any order, then the login button.
function StepEditor({ draft, set }: { draft: Draft; set: (patch: Partial<Draft>) => void }) {
  const { steps } = draft;
  const change = (index: number, patch: Partial<StepDraft>) =>
    set({ steps: steps.map((step, i) => (i === index ? { ...step, ...patch } : step)) });
  const move = (index: number, by: number) => {
    const next = [...steps];
    const [step] = next.splice(index, 1);
    next.splice(index + by, 0, step);
    set({ steps: next });
  };
  return (
    <fieldset className="step-editor">
      <legend>로그인 단계</legend>
      <ol className="step-rows">
        {steps.map((step, index) => {
          const number = index + 1;
          // Clicks and texts of the user's may be on some screens only, and be skipped when an element shows.
          const flexible = step.action === 'click' || step.value === 'text';
          return (
            <li key={step.key} className="step-row">
              <div className="step-row-head">
                <span className="step-number">{number}</span>
                <select
                  aria-label={`${number}번째 단계 동작`}
                  value={step.action}
                  onChange={(e) => {
                    const action = e.target.value as StepDraft['action'];
                    change(index, { action, optional: action === 'click' });
                  }}
                >
                  <option value="fill">텍스트 입력</option>
                  <option value="click">버튼 클릭</option>
                </select>
                {step.action === 'fill' && (
                  <select
                    aria-label={`${number}번째 단계 입력값`}
                    value={step.value}
                    onChange={(e) =>
                      change(index, {
                        value: e.target.value as StepDraft['value'],
                        optional: false,
                      })
                    }
                  >
                    <option value="id">{VALUES.id}</option>
                    <option value="password">{VALUES.password}</option>
                    <option value="text">{VALUES.text}</option>
                  </select>
                )}
                {step.action === 'fill' && step.value === 'text' && (
                  <input
                    aria-label={`${number}번째 단계 입력할 글자`}
                    required
                    maxLength={200}
                    value={step.text}
                    onChange={(e) => change(index, { text: e.target.value })}
                    placeholder="입력할 글자"
                  />
                )}
                {flexible && (
                  <label className="check">
                    <input
                      type="checkbox"
                      aria-label={`${number}번째 단계 있을 때만`}
                      checked={step.optional}
                      onChange={(e) => change(index, { optional: e.target.checked })}
                    />
                    있을 때만
                  </label>
                )}
                {flexible && !step.skip && (
                  <button
                    type="button"
                    className="text-button"
                    aria-label={`${number}번째 단계 건너뛸 조건 추가`}
                    onClick={() => change(index, { skip: true })}
                  >
                    건너뛸 조건
                  </button>
                )}
                <span className="spacer" />
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`${number}번째 단계 위로`}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  <ArrowUp size={14} />
                </button>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`${number}번째 단계 아래로`}
                  disabled={index === steps.length - 1}
                  onClick={() => move(index, 1)}
                >
                  <ArrowDown size={14} />
                </button>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`${number}번째 단계 빼기`}
                  onClick={() => set({ steps: steps.filter((_, i) => i !== index) })}
                >
                  <Trash2 size={14} />
                </button>
              </div>
              <input
                className="selector"
                aria-label={`${number}번째 단계 위치`}
                required
                maxLength={500}
                spellCheck={false}
                value={step.target}
                onChange={(e) => change(index, { target: e.target.value })}
                placeholder={
                  step.action === 'click'
                    ? '누를 버튼·링크의 XPath(/html/…) 또는 CSS 선택자'
                    : '입력란의 XPath(/html/…) 또는 CSS 선택자'
                }
              />
              {flexible && step.skip && (
                <div className="step-skip">
                  <input
                    className="selector"
                    aria-label={`${number}번째 단계 건너뛸 조건`}
                    maxLength={500}
                    spellCheck={false}
                    value={step.unless}
                    onChange={(e) => change(index, { unless: e.target.value })}
                    placeholder="이 요소(XPath 또는 CSS 선택자)가 보이면 이 단계를 건너뜀"
                  />
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`${number}번째 단계 건너뛸 조건 빼기`}
                    onClick={() => change(index, { skip: false, unless: '' })}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              )}
            </li>
          );
        })}
        <li className="step-row">
          <div className="step-row-head">
            <span className="step-number">{steps.length + 1}</span>
            <strong>로그인 버튼 클릭</strong>
          </div>
          <input
            className="selector"
            aria-label="로그인 버튼 위치"
            maxLength={500}
            spellCheck={false}
            value={draft.submit}
            onChange={(e) => set({ submit: e.target.value })}
            placeholder="비우면 마지막 입력란에서 Enter"
          />
        </li>
      </ol>
      <div className="step-tools">
        <button
          type="button"
          className="button secondary small"
          disabled={steps.length >= MAX_STEPS}
          onClick={() => set({ steps: [...steps, blankStep('fill', 'text')] })}
        >
          <Plus size={14} />
          텍스트 입력 추가
        </button>
        <button
          type="button"
          className="button secondary small"
          disabled={steps.length >= MAX_STEPS}
          onClick={() => set({ steps: [...steps, blankStep('click')] })}
        >
          <Plus size={14} />
          버튼 클릭 추가
        </button>
      </div>
      <small>
        위에서부터 차례로 실행합니다. 로그인 ID와 비밀번호를 입력하는 단계가 있어야 하며, ‘있을
        때만’ 단계는 화면에 없으면 건너뜁니다. 직접 입력한 글자는 암호화하지 않고 저장하므로
        비밀번호는 ‘비밀번호’ 값으로 입력하세요.
      </small>
    </fieldset>
  );
}
