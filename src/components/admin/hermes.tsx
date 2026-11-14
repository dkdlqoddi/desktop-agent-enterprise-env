import { useCallback, useEffect, useState } from 'react';
import {
  Bot,
  CircleAlert,
  CircleCheck,
  Cpu,
  FolderOpen,
  LoaderCircle,
  Plus,
  Send,
  Trash2,
} from 'lucide-react';
import { api, type HermesGateway, type HermesKey, type HermesSettings } from '@/lib/api';
import { useActions } from '../state';
import SecretInput from '../secret-input';
import HermesGuard from './hermes-guard';
import HermesLimits from './hermes-limits';
import HermesMemory from './hermes-memory';

// The key groups listed as plain keys. The messenger gateway's bot tokens (group 'gateway') are set with the gateway,
// beside the accounts each bot answers.
const GROUPS: [HermesKey['group'], string][] = [
  ['web', '웹 검색'],
  ['media', '이미지 생성'],
  ['other', '그 밖의 키'],
];

// The messengers of the gateway: the tokens each needs, and where a user finds the ID of an account.
const MESSENGERS: { name: string; label: string; tokens: string[]; accounts: string }[] = [
  {
    name: 'telegram',
    label: '텔레그램',
    tokens: ['TELEGRAM_BOT_TOKEN'],
    accounts: '숫자로 된 사용자 ID (예: 123456789). @userinfobot에 글을 보내면 알려 줍니다.',
  },
  {
    name: 'discord',
    label: '디스코드',
    tokens: ['DISCORD_BOT_TOKEN'],
    accounts: '사용자 ID. 설정 → 고급 → 개발자 모드를 켜고 프로필에서 ‘ID 복사’를 누릅니다.',
  },
  {
    name: 'slack',
    label: '슬랙',
    tokens: ['SLACK_BOT_TOKEN', 'SLACK_APP_TOKEN'],
    accounts:
      '멤버 ID (U로 시작). 프로필 → 더 보기 → ‘멤버 ID 복사’. Socket Mode를 켠 앱이어야 합니다.',
  },
];
const GATEWAY_STATES: Record<HermesGateway['state'], string> = {
  off: '꺼짐',
  starting: '시작하는 중',
  running: '실행 중',
  stopping: '끄는 중',
  failed: '멈춤',
};

// The chat agent's own settings: the folder its terminal and file tools start in, the service keys it reads from its
// environment (only whether each is set ever reaches this screen), the MCP servers, the computer-use toggle, what it
// runs without asking and may not touch (hermes-guard.tsx), what it remembers (hermes-memory.tsx) and the messenger
// gateway, and how long and how far a request may go (hermes-limits.tsx). Model calls always go through 관리 → LLM
// 연결, so model-provider keys are refused here. All of
// it is the user's alone (data-user-only): an AI tool's screen control cannot type or press here at any access level.
export default function HermesSection() {
  const { notify } = useActions();
  const [state, setState] = useState<HermesSettings | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [mcp, setMcp] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [newName, setNewName] = useState('');
  const [users, setUsers] = useState<Record<string, string>>({});
  const [gateway, setGateway] = useState<HermesGateway | null>(null);
  const [busy, setBusy] = useState(false);

  const show = useCallback((next: HermesSettings) => {
    setState(next);
    setWorkspace(next.workspace);
    setUsers(next.users);
  }, []);
  const load = useCallback(async () => {
    const next = await api<HermesSettings>('hermes.get');
    show(next);
    return next;
  }, [show]);
  useEffect(() => {
    load().catch((error) => notify((error as Error).message, true));
  }, [load, notify]);

  // Whether the gateway runs, asked again while this screen is open: it connects and fails in the background.
  const enabled = state?.gateway ?? false;
  useEffect(() => {
    let alive = true;
    const ask = () => {
      api<HermesGateway>('agent.gateway')
        .then((next) => alive && setGateway(next))
        // No Wiki is open yet, or the backend is starting again: asked again in a moment.
        .catch(() => {});
    };
    ask();
    const timer = setInterval(ask, enabled ? 1500 : 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [enabled]);

  async function update(params: Record<string, unknown>, done?: string) {
    setBusy(true);
    try {
      const next = await api<HermesSettings>('hermes.update', params);
      show(next);
      if (next.error) notify(next.error, true);
      else if (done) notify(done);
      return !next.error;
    } catch (error) {
      notify((error as Error).message, true);
      return false;
    } finally {
      setBusy(false);
    }
  }

  // The values typed for `names` (all of them when none is given): what a save sends, and what it empties after.
  const typed = (names?: string[]) =>
    Object.fromEntries(
      Object.entries(drafts)
        .filter(([name, value]) => value.trim() && (!names || names.includes(name)))
        .map(([name, value]) => [name, value.trim()]),
    );
  const forget = (names: string[]) =>
    setDrafts((current) =>
      Object.fromEntries(Object.entries(current).filter(([name]) => !names.includes(name))),
    );

  async function saveKeys() {
    const tokens = MESSENGERS.flatMap((messenger) => messenger.tokens);
    const keys = Object.fromEntries(
      Object.entries(typed()).filter(([name]) => !tokens.includes(name)),
    );
    if (!Object.keys(keys).length) return;
    if (await update({ keys }, '키를 저장했습니다.')) forget(Object.keys(keys));
  }

  async function saveGateway() {
    const keys = typed(MESSENGERS.flatMap((messenger) => messenger.tokens));
    if (await update({ users, keys }, '메신저 게이트웨이 설정을 저장했습니다.'))
      forget(Object.keys(keys));
  }

  async function chooseWorkspace() {
    try {
      const folder = await api<string | null>('hermes.choose');
      if (folder) setWorkspace(folder);
    } catch (error) {
      notify((error as Error).message, true);
    }
  }

  if (!state)
    return (
      <p className="muted">
        <LoaderCircle className="spin" size={15} /> 설정을 불러오고 있습니다.
      </p>
    );

  const keysOf = (group: HermesKey['group']) => state.keys.filter((key) => key.group === group);
  const keyOf = (name: string) => state.keys.find((key) => key.name === name);
  const secret = (key: HermesKey) => (
    <label key={key.name} className="admin-field">
      <span>{key.label || key.name}</span>
      <SecretInput
        label={key.label || key.name}
        name={key.name}
        value={drafts[key.name] ?? ''}
        placeholder={key.has_value ? '●●●●●● (저장됨)' : `${key.name} 값`}
        saved={key.has_value}
        onChange={(value) => setDrafts((current) => ({ ...current, [key.name]: value }))}
      />
      {key.has_value && (
        <button
          type="button"
          className="text-button"
          data-destructive
          disabled={busy}
          onClick={() => void update({ keys: { [key.name]: '' } }, `${key.name}을(를) 지웠습니다.`)}
        >
          <Trash2 size={13} /> 지우기
        </button>
      )}
    </label>
  );
  const gatewayChanged =
    MESSENGERS.some(
      (messenger) => (users[messenger.name] ?? '').trim() !== (state.users[messenger.name] ?? ''),
    ) || Object.keys(typed(MESSENGERS.flatMap((messenger) => messenger.tokens))).length > 0;

  return (
    <div className="admin-form hermes-settings" data-user-only="agent">
      <p className="section-lead">
        채팅의 내장 Hermes 에이전트가 쓰는 서비스 키와 설정입니다. 키는 운영체제 보안 저장소에
        암호화해 두며, 화면과 AI 도구에는 값이 아니라 설정 여부만 보입니다. 모델 호출은 항상 관리 →
        LLM 연결로만 하므로, 모델 제공자 키는 여기에 넣을 수 없습니다. 이 화면은 사용자만 바꿀 수
        있습니다(AI 도구의 화면 제어는 어느 허용 범위에서도 여기에 입력하거나 누르지 못합니다).
      </p>
      {!state.secure_storage && (
        <p className="admin-warn">
          운영체제 보안 저장소를 쓸 수 없어, 입력한 키는 앱을 닫을 때까지만 유지됩니다.
        </p>
      )}

      {GROUPS.map(([group, label]) => {
        const keys = keysOf(group);
        if (group === 'other' && !keys.length) return null;
        return (
          <fieldset key={group} className="admin-fieldset">
            <legend>{label}</legend>
            {keys.map(secret)}
            {group === 'web' && (
              <p className="admin-hint">
                키가 없으면 DuckDuckGo로 검색하고, 페이지 본문을 읽어 주는 서비스는 쓰지
                않습니다(그때 에이전트는 브라우저로 페이지를 엽니다). 키를 여러 개 넣으면 검색은
                Tavily → Exa → Parallel → Firecrawl → Brave, 페이지 읽기는 Firecrawl → Tavily → Exa
                → Parallel 순으로 먼저 있는 하나를 씁니다.
              </p>
            )}
          </fieldset>
        );
      })}

      <fieldset className="admin-fieldset">
        <legend>키 직접 추가</legend>
        <div className="hermes-add-key">
          <input
            aria-label="키 이름"
            placeholder="예) GITHUB_TOKEN"
            value={newName}
            onChange={(event) => setNewName(event.target.value.toUpperCase())}
          />
          <button
            type="button"
            className="button secondary small"
            disabled={!/^[A-Z][A-Z0-9_]{1,63}$/.test(newName)}
            onClick={() => {
              setDrafts((current) => ({ ...current, [newName]: '' }));
              setState((current) =>
                current
                  ? {
                      ...current,
                      keys: current.keys.some((key) => key.name === newName)
                        ? current.keys
                        : [
                            ...current.keys,
                            { name: newName, label: '', group: 'other', has_value: false },
                          ],
                    }
                  : current,
              );
              setNewName('');
            }}
          >
            <Plus size={14} /> 추가
          </button>
        </div>
        <p className="admin-hint">
          영문 대문자·숫자·밑줄. 스킬이나 다른 검색 서비스의 키를 넣을 수 있습니다.
        </p>
      </fieldset>

      <button className="button primary" disabled={busy} onClick={() => void saveKeys()}>
        <Send size={15} /> 키 저장
      </button>

      <fieldset className="admin-fieldset">
        <legend>
          <FolderOpen size={14} /> 작업 폴더
        </legend>
        <p className="admin-hint">
          에이전트의 터미널과 파일 도구가 시작하는 폴더입니다. 비워 두면 Wiki 폴더 안의 에이전트
          전용 폴더(hermes)에서 시작합니다. 시작 위치일 뿐이며, 에이전트는 이 폴더 밖의 파일도 다룰
          수 있습니다.
        </p>
        <div className="hermes-add-key">
          <input
            aria-label="작업 폴더"
            placeholder="폴더의 전체 경로 (비워 두면 에이전트 전용 폴더)"
            value={workspace}
            onChange={(event) => setWorkspace(event.target.value)}
          />
          <button
            type="button"
            className="button secondary small"
            disabled={busy}
            onClick={() => void chooseWorkspace()}
          >
            <FolderOpen size={14} /> 폴더 선택
          </button>
          <button
            type="button"
            className="button secondary small"
            disabled={busy || workspace.trim() === state.workspace}
            onClick={() =>
              void update(
                { workspace: workspace.trim() },
                workspace.trim()
                  ? '작업 폴더를 저장했습니다. 다음 요청부터 적용됩니다.'
                  : '작업 폴더를 비웠습니다. 에이전트 전용 폴더에서 시작합니다.',
              )
            }
          >
            저장
          </button>
        </div>
      </fieldset>

      <fieldset className="admin-fieldset">
        <legend>
          <Cpu size={14} /> 도구
        </legend>
        <label className="admin-toggle">
          <input
            type="checkbox"
            checked={state.computer_use}
            disabled={busy}
            onChange={(event) => void update({ computer_use: event.target.checked })}
          />
          컴퓨터 사용 (화면을 보고 마우스·키보드 조작). 별도 드라이버가 있을 때만 동작합니다.
        </label>
        <label className="admin-field">
          <span>MCP 서버 (JSON)</span>
          <textarea
            className="hermes-mcp"
            rows={4}
            spellCheck={false}
            placeholder='{"files": {"command": "npx", "args": ["-y", "@example/mcp-server"]}}'
            defaultValue={mcp}
            onChange={(event) => setMcp(event.target.value)}
          />
        </label>
        <button
          className="button secondary small"
          disabled={busy}
          onClick={() => void update({ mcp }, 'MCP 서버 설정을 저장했습니다.')}
        >
          MCP 저장
        </button>
        {!!state.mcp_servers.length && (
          <p className="admin-hint">등록된 MCP 서버: {state.mcp_servers.join(', ')}</p>
        )}
      </fieldset>

      <HermesLimits state={state} busy={busy} update={update} />

      <HermesGuard state={state} busy={busy} update={update} notify={notify} />

      <HermesMemory notify={notify} />

      <fieldset className="admin-fieldset">
        <legend>
          <Bot size={14} /> 메신저 게이트웨이
        </legend>
        <p className="admin-hint">
          텔레그램·디스코드·슬랙의 내 봇에 글을 보내 이 PC의 에이전트에 일을 시킵니다. 앱이 켜져
          있는 동안만 동작하고, 아래에 적은 계정에만 답합니다. 그 계정은 채팅과 같은 도구(터미널,
          파일, 브라우저)로 일을 시킬 수 있고, 위험한 명령은 그 메신저에서 확인을 받습니다(위 ‘요청
          한도’의 대기 시간 안에 답이 없으면 실행하지 않습니다). 주고받는 글은 그 메신저 회사의
          서버를 거칩니다.
        </p>
        <label className="admin-toggle">
          <input
            type="checkbox"
            checked={state.gateway}
            disabled={busy}
            onChange={(event) =>
              void update(
                { gateway: event.target.checked },
                event.target.checked
                  ? '메신저 게이트웨이를 켰습니다.'
                  : '메신저 게이트웨이를 껐습니다.',
              )
            }
          />
          메신저 게이트웨이 켜기
        </label>
        {gateway && (state.gateway || gateway.state !== 'off') && (
          <p className={`hermes-gateway-state ${gateway.state}`} role="status">
            {gateway.state === 'running' ? (
              <CircleCheck size={14} />
            ) : gateway.state === 'starting' || gateway.state === 'stopping' ? (
              <LoaderCircle className="spin" size={14} />
            ) : (
              <CircleAlert size={14} />
            )}
            <span>
              게이트웨이: {GATEWAY_STATES[gateway.state]}
              {gateway.message ? ` — ${gateway.message}` : ''}
            </span>
          </p>
        )}
        {MESSENGERS.map((messenger) => {
          const live = gateway?.platforms.find((platform) => platform.name === messenger.name);
          return (
            <div key={messenger.name} className="hermes-messenger">
              <div className="hermes-messenger-head">
                <strong>{messenger.label}</strong>
                {live && (
                  <span className={`hermes-messenger-state ${live.state}`}>{live.message}</span>
                )}
              </div>
              {messenger.tokens.map((name) => {
                const key = keyOf(name);
                return key ? secret(key) : null;
              })}
              <label className="admin-field">
                <span>허용할 계정 ID (쉼표로 구분)</span>
                <input
                  aria-label={`${messenger.label} 허용 계정`}
                  placeholder="이 계정이 보낸 글에만 답합니다"
                  spellCheck={false}
                  value={users[messenger.name] ?? ''}
                  onChange={(event) =>
                    setUsers((current) => ({ ...current, [messenger.name]: event.target.value }))
                  }
                />
              </label>
              <p className="admin-hint">{messenger.accounts}</p>
            </div>
          );
        })}
        <button
          className="button secondary small"
          disabled={busy || !gatewayChanged}
          onClick={() => void saveGateway()}
        >
          게이트웨이 설정 저장
        </button>
        <p className="admin-hint">
          봇 토큰과 허용할 계정을 모두 넣은 메신저만 연결합니다. 계정을 비우면 그 메신저는 연결하지
          않습니다.
        </p>
      </fieldset>
    </div>
  );
}
