import { useCallback, useEffect, useState } from 'react';
import { FolderOpen, Plus, RotateCcw, ShieldCheck, ShieldOff, Trash2 } from 'lucide-react';
import {
  api,
  dayTime,
  type AgentApprovals,
  type AgentGrants,
  type HermesSettings,
} from '@/lib/api';

const RESULTS: Record<string, string> = {
  once: '이번만 실행',
  session: '이 대화에서 허용',
  always: '항상 허용',
  deny: '거부',
  timeout: '응답 없음',
  stopped: '중지함',
};
const REFUSED = new Set(['deny', 'timeout', 'stopped']);

type Props = {
  state: HermesSettings;
  busy: boolean;
  // Saves a change of the settings (hermes.update); true when it was saved.
  update: (params: Record<string, unknown>, done?: string) => Promise<boolean>;
  notify: (message: string, error?: boolean) => void;
};

// What the chat agent runs without asking and what it may not touch, on the settings screen (inside its data-user-only
// form): the commands allowed for good and for this conversation, each of which can be taken back, the commands the
// agent asked about lately, the protected folders and the commands refused outright.
export default function HermesGuard({ state, busy, update, notify }: Props) {
  const [grants, setGrants] = useState<AgentGrants>({ session: [] });
  const [approvals, setApprovals] = useState<AgentApprovals['approvals']>([]);
  const [folder, setFolder] = useState('');
  const [blocked, setBlocked] = useState(state.blocked.join('\n'));

  // What the conversation allows and what was asked lately, as the backend has them now. No Wiki may be open yet:
  // the lists then stay empty.
  const load = useCallback(async () => {
    try {
      const [granted, asked] = await Promise.all([
        api<AgentGrants>('agent.grants'),
        api<AgentApprovals>('agent.activity', { limit: 20 }),
      ]);
      setGrants(granted);
      setApprovals(asked.approvals);
    } catch {
      /* Shown empty. */
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const forget = async () => {
    try {
      setGrants(await api<AgentGrants>('agent.forget_grants'));
      notify('이 대화에서 허용한 명령을 다시 묻습니다.');
    } catch (error) {
      notify((error as Error).message, true);
    }
  };

  const choose = async () => {
    try {
      const picked = await api<string | null>('hermes.choose', { purpose: 'protect' });
      if (picked) setFolder(picked);
    } catch (error) {
      notify((error as Error).message, true);
    }
  };

  const addFolder = async () => {
    const added = folder.trim();
    if (!added || state.protected.folders.includes(added)) return;
    const saved = await update(
      { protected: { folders: [...state.protected.folders, added] } },
      '보호할 폴더를 추가했습니다. 다음 요청부터 적용됩니다.',
    );
    if (saved) setFolder('');
  };

  const patterns = blocked
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  const blockedChanged = patterns.join('\n') !== state.blocked.join('\n');

  return (
    <>
      <fieldset className="admin-fieldset">
        <legend>
          <ShieldCheck size={14} /> 묻지 않고 실행하는 명령
        </legend>
        <p className="admin-hint">
          에이전트는 위험해 보이는 명령 앞에서 멈추고 묻습니다. 그때 ‘항상 허용’을 고른 명령은
          글자까지 같은 명령에 한해 다시 묻지 않으며, 이 PC의 앱 설정에만 저장합니다(Wiki 폴더에는
          두지 않습니다). 메신저에서 한 ‘항상 허용’은 그 대화에서만 유효합니다.
        </p>
        {state.allowed.length ? (
          <ul className="hermes-list" aria-label="항상 허용한 명령">
            {state.allowed.map((entry) => (
              <li key={entry.command}>
                <code>{entry.command}</code>
                {entry.added_at && (
                  <span className="hermes-list-note">{dayTime(entry.added_at)}</span>
                )}
                <button
                  type="button"
                  className="text-button"
                  data-destructive
                  disabled={busy}
                  onClick={() =>
                    void update(
                      { disallow: [entry.command] },
                      '허용을 취소했습니다. 다음부터 다시 묻습니다.',
                    )
                  }
                >
                  <Trash2 size={13} /> 허용 취소
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="admin-hint">항상 허용한 명령이 없습니다.</p>
        )}
        {!!grants.session.length && (
          <>
            <p className="admin-hint">
              이 대화에서 허용한 명령 종류(대화를 비우거나 앱을 닫으면 사라집니다):
            </p>
            <ul className="hermes-list" aria-label="이 대화에서 허용한 명령 종류">
              {grants.session.map((kind) => (
                <li key={kind}>
                  <code>{kind}</code>
                </li>
              ))}
            </ul>
            <button
              type="button"
              className="button secondary small"
              disabled={busy}
              onClick={() => void forget()}
            >
              <RotateCcw size={14} /> 모두 다시 묻기
            </button>
          </>
        )}
        {!!approvals.length && (
          <details className="hermes-history">
            <summary>최근에 확인을 받은 명령 {approvals.length}건</summary>
            <ul className="hermes-list" aria-label="최근에 확인을 받은 명령">
              {approvals.map((entry, index) => (
                <li key={`${entry.seq}-${index}`}>
                  <code title={entry.note || undefined}>{entry.text}</code>
                  <span className="hermes-list-note">{dayTime(entry.at)}</span>
                  <span
                    className={`hermes-list-result ${REFUSED.has(entry.result) ? 'refused' : ''}`}
                  >
                    {RESULTS[entry.result] ?? entry.result}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </fieldset>

      <fieldset className="admin-fieldset">
        <legend>
          <ShieldOff size={14} /> 보호 구역
        </legend>
        <p className="admin-hint">
          에이전트의 파일 도구가 읽거나 쓰지 않고, 경로를 적은 명령도 실행하지 않는 곳입니다. 앱의
          설정 폴더와 Wiki의 데이터베이스·백업은 항상 보호합니다. 문서는 앱의 도구로 읽고 씁니다.
        </p>
        <label className="admin-toggle">
          <input
            type="checkbox"
            checked={state.protected.credentials}
            disabled={busy}
            onChange={(event) =>
              void update(
                { protected: { credentials: event.target.checked } },
                event.target.checked
                  ? '자격 증명이 있는 곳을 보호합니다.'
                  : '자격 증명이 있는 곳의 보호를 껐습니다.',
              )
            }
          />
          자격 증명이 있는 곳 보호 (SSH·GPG 키, 클라우드·패키지 저장소 로그인, 브라우저 프로필)
        </label>
        {!!state.protected.folders.length && (
          <ul className="hermes-list" aria-label="보호하는 폴더">
            {state.protected.folders.map((item) => (
              <li key={item}>
                <code>{item}</code>
                <button
                  type="button"
                  className="text-button"
                  data-destructive
                  disabled={busy}
                  onClick={() =>
                    void update(
                      {
                        protected: {
                          folders: state.protected.folders.filter((other) => other !== item),
                        },
                      },
                      '보호를 풀었습니다. 다음 요청부터 적용됩니다.',
                    )
                  }
                >
                  <Trash2 size={13} /> 보호 풀기
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="hermes-add-key">
          <input
            aria-label="보호할 폴더"
            placeholder="보호할 폴더의 전체 경로"
            spellCheck={false}
            value={folder}
            onChange={(event) => setFolder(event.target.value)}
          />
          <button
            type="button"
            className="button secondary small"
            disabled={busy}
            onClick={() => void choose()}
          >
            <FolderOpen size={14} /> 폴더 선택
          </button>
          <button
            type="button"
            className="button secondary small"
            disabled={busy || !folder.trim()}
            onClick={() => void addFolder()}
          >
            <Plus size={14} /> 추가
          </button>
        </div>
        <label className="admin-field">
          <span>실행하지 않을 명령 (한 줄에 하나, *는 아무 글자)</span>
          <textarea
            className="hermes-mcp"
            aria-label="실행하지 않을 명령"
            rows={3}
            spellCheck={false}
            placeholder={'git push --force*\n*shutdown*'}
            value={blocked}
            onChange={(event) => setBlocked(event.target.value)}
          />
        </label>
        <button
          type="button"
          className="button secondary small"
          disabled={busy || !blockedChanged}
          onClick={() =>
            void update({ blocked: patterns }, '실행하지 않을 명령을 저장했습니다.').then(
              (saved) => saved && setBlocked(patterns.join('\n')),
            )
          }
        >
          실행하지 않을 명령 저장
        </button>
        <p className="admin-hint">
          여기에 맞는 명령은 허용 여부와 관계없이 실행하지 않습니다. 보호 구역은 경로 이름을 보고
          막는 두 번째 방어선입니다. 에이전트는 사용자 계정으로 동작하므로, 스크립트가 실행 중에
          만든 경로나 링크를 거친 접근까지 막지는 못합니다.
        </p>
      </fieldset>
    </>
  );
}
