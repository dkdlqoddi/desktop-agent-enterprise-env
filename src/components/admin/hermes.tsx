import { useCallback, useEffect, useState } from 'react';
import { Bot, Cpu, LoaderCircle, Plus, Send, Trash2 } from 'lucide-react';
import { api, type HermesKey, type HermesSettings } from '@/lib/api';
import { useActions } from '../state';
import SecretInput from '../secret-input';

const GROUPS: [HermesKey['group'], string][] = [
  ['web', '웹 검색'],
  ['media', '이미지·음성'],
  ['gateway', '메신저 봇'],
  ['other', '그 밖의 키'],
];
const PLATFORMS: [string, string][] = [
  ['telegram', '텔레그램'],
  ['discord', '디스코드'],
  ['slack', '슬랙'],
];

// The chat agent's own settings: the service keys and messenger tokens it reads from its environment (only whether
// each is set ever reaches this screen), the MCP servers, the messenger accounts it answers, and the computer-use
// and gateway toggles. Model calls always go through 관리 → LLM 연결, so model-provider keys are refused here.
export default function HermesSection() {
  const { notify } = useActions();
  const [state, setState] = useState<HermesSettings | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [mcp, setMcp] = useState('');
  const [accounts, setAccounts] = useState<Record<string, string>>({});
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const next = await api<HermesSettings>('hermes.get');
    setState(next);
    setAccounts(next.users);
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
      setAccounts(next.users);
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

  if (!state)
    return (
      <p className="muted">
        <LoaderCircle className="spin" size={15} /> 설정을 불러오고 있습니다.
      </p>
    );

  const keysOf = (group: HermesKey['group']) => state.keys.filter((key) => key.group === group);
  const custom = state.keys.filter(
    (key) => !GROUPS.some(([g]) => g === key.group && key.group !== 'other'),
  );

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
        const keys = group === 'other' ? custom : keysOf(group);
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
          켜면 텔레그램·디스코드·슬랙에서 이 PC의 에이전트에 일을 시킬 수 있습니다. 봇 토큰은 위의
          “메신저 봇”에 넣고, 아래에 허용할 계정만 답하게 하세요. 연결은 이 PC에서 바깥으로만
          열립니다.
        </p>
        <label className="admin-toggle">
          <input
            type="checkbox"
            checked={state.gateway}
            disabled={busy}
            onChange={(event) => void update({ gateway: event.target.checked })}
          />
          메신저 게이트웨이 켜기
        </label>
        {state.gateway &&
          PLATFORMS.map(([platform, label]) => (
            <label key={platform} className="admin-field">
              <span>{label} 허용 계정</span>
              <input
                aria-label={`${label} 허용 계정`}
                placeholder="계정 ID를 쉼표로 구분"
                value={accounts[platform] ?? ''}
                onChange={(event) =>
                  setAccounts((current) => ({ ...current, [platform]: event.target.value }))
                }
                onBlur={() => void update({ users: { [platform]: accounts[platform] ?? '' } })}
              />
            </label>
          ))}
      </fieldset>
    </div>
  );
}
