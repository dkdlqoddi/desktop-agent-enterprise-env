import { useCallback, useEffect, useState } from 'react';
import { Bot, Cpu, FolderOpen, LoaderCircle, Plus, Send, Trash2 } from 'lucide-react';
import { api, type HermesKey, type HermesSettings } from '@/lib/api';
import { useActions } from '../state';
import SecretInput from '../secret-input';

// The key groups the screen takes values for. The messenger gateway's bot tokens (group 'gateway') are not among them:
// this version runs no gateway, so a token typed here would do nothing.
const GROUPS: [HermesKey['group'], string][] = [
  ['web', '웹 검색'],
  ['media', '이미지 생성'],
  ['other', '그 밖의 키'],
];

// The chat agent's own settings: the folder its terminal and file tools start in, the service keys it reads from its
// environment (only whether each is set ever reaches this screen), the MCP servers and the computer-use toggle. Model
// calls always go through 관리 → LLM 연결, so model-provider keys are refused here.
export default function HermesSection() {
  const { notify } = useActions();
  const [state, setState] = useState<HermesSettings | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [mcp, setMcp] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const next = await api<HermesSettings>('hermes.get');
    setState(next);
    setWorkspace(next.workspace);
    return next;
  }, []);
  useEffect(() => {
    load().catch((error) => notify((error as Error).message, true));
  }, [load, notify]);

  async function update(params: Record<string, unknown>, done?: string) {
    setBusy(true);
    try {
      const next = await api<HermesSettings>('hermes.update', params);
      setState(next);
      setWorkspace(next.workspace);
      if (next.error) notify(next.error, true);
      else if (done) notify(done);
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  }

  async function saveKeys() {
    const keys: Record<string, string> = {};
    for (const [name, value] of Object.entries(drafts)) if (value.trim()) keys[name] = value.trim();
    if (!Object.keys(keys).length) return;
    await update({ keys }, '키를 저장했습니다.');
    setDrafts({});
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
  // Bot tokens a version that offered the gateway saved: shown so that they can be deleted, nothing more.
  const botTokens = keysOf('gateway').filter((key) => key.has_value);

  return (
    <div className="admin-form hermes-settings">
      <p className="section-lead">
        채팅의 내장 Hermes 에이전트가 쓰는 서비스 키와 설정입니다. 키는 운영체제 보안 저장소에
        암호화해 두며, 화면과 AI 도구에는 값이 아니라 설정 여부만 보입니다. 모델 호출은 항상 관리 →
        LLM 연결로만 하므로, 모델 제공자 키는 여기에 넣을 수 없습니다.
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
            {keys.map((key) => (
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
                    onClick={() =>
                      void update({ keys: { [key.name]: '' } }, `${key.name}을(를) 지웠습니다.`)
                    }
                  >
                    <Trash2 size={13} /> 지우기
                  </button>
                )}
              </label>
            ))}
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

      <fieldset className="admin-fieldset">
        <legend>
          <Bot size={14} /> 메신저 게이트웨이
        </legend>
        <p className="admin-hint">
          텔레그램·디스코드·슬랙에서 이 PC의 에이전트에 일을 시키는 메신저 게이트웨이는 이 버전에서
          아직 지원하지 않습니다. 봇 토큰을 넣어도 쓰이지 않으므로 입력란을 두지 않았습니다.
        </p>
        {botTokens.map((key) => (
          <div key={key.name} className="admin-field">
            <span>{key.label || key.name} (저장되어 있지만 쓰이지 않음)</span>
            <button
              type="button"
              className="text-button"
              data-destructive
              disabled={busy}
              onClick={() =>
                void update({ keys: { [key.name]: '' } }, `${key.name}을(를) 지웠습니다.`)
              }
            >
              <Trash2 size={13} /> 지우기
            </button>
          </div>
        ))}
      </fieldset>
    </div>
  );
}
