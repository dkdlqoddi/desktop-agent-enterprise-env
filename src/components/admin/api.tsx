import { useCallback, useEffect, useState } from 'react';
import { BookOpenText, CircleAlert, Copy, ExternalLink, LoaderCircle } from 'lucide-react';
import { api, clock, type ApiAccess, type ApiState } from '@/lib/api';
import { useApp } from '../app';
import Confirm from '../confirm';
import { rove } from '../roving';

type Change = Partial<{ enabled: boolean; port: number; access: ApiAccess }>;

// The local API and MCP server that AI tools on this PC use. The token never comes to this screen: the copy buttons
// put it on the clipboard from the main process, already filled into each tool's settings.
export default function ApiSection({ onManual }: { onManual: () => void }) {
  const { notify } = useApp();
  const [state, setState] = useState<ApiState | null>(null);
  const [port, setPort] = useState('');
  const [client, setClient] = useState('claude-code');
  const [busy, setBusy] = useState('');
  const load = useCallback(async () => {
    const next = await api<ApiState>('api.get');
    setState(next);
    return next;
  }, []);
  useEffect(() => {
    load()
      .then((next) => setPort(String(next.port)))
      .catch((error) => notify(error.message, true));
  }, [load, notify]);
  // Recent requests follow what AI tools do while this section is open.
  const running = !!state?.running;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void load().catch(() => {}), 3000);
    return () => clearInterval(timer);
  }, [running, load]);
  async function update(change: Change, key: string) {
    setBusy(key);
    // The switch and the list move at once; the answer then says what the server really does.
    if (key !== 'port') setState((current) => current && { ...current, ...change });
    try {
      const next = await api<ApiState>('api.update', change);
      setState(next);
      setPort(String(next.port));
      if (next.enabled && next.error) notify(next.error, true);
      else if (change.enabled !== undefined)
        notify(
          next.enabled
            ? `로컬 API·MCP 서버를 켰습니다 · ${next.url}`
            : '로컬 API·MCP 서버를 껐습니다.',
        );
    } catch (error) {
      notify((error as Error).message, true);
      await load().catch(() => {});
    } finally {
      setBusy('');
    }
  }
  async function copy(target: string, label: string) {
    try {
      await api('api.copy', { target });
      notify(`클립보드에 복사했습니다 · ${label}`);
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
  const level = state.levels.find((item) => item.id === state.access);
  const chosen = state.clients.find((item) => item.id === client) ?? state.clients[0];
  return (
    <div className="admin-form api-settings">
      <p className="section-lead">
        Claude Code·Codex·OpenCode·Antigravity 같은 AI 도구가 Private Agent의 검색·문서·할 일과 앱
        화면을 쓰도록 로컬 API와 MCP 서버를 엽니다. 이 PC 안(127.0.0.1)에서만 접속을 받고, 토큰을
        가진 프로그램만 쓸 수 있습니다.
      </p>
      {state.load_error && (
        <p className="banner warn" role="alert">
          <CircleAlert size={15} />
          {state.load_error}
        </p>
      )}
      <label className="setting">
        <span>
          <strong>로컬 API·MCP 서버</strong>
          <small className={state.enabled && state.error ? 'danger' : undefined} role="status">
            {state.running
              ? `실행 중 · ${state.url}`
              : state.enabled && state.error
                ? state.error
                : '꺼져 있습니다. 켜면 AI 도구가 이 PC에서 접속할 수 있습니다.'}
          </small>
        </span>
        <span className="switch">
          <input
            type="checkbox"
            role="switch"
            aria-label="로컬 API·MCP 서버 켜기"
            checked={state.enabled}
            disabled={!!busy}
            onChange={(event) => void update({ enabled: event.target.checked }, 'enabled')}
          />
          <span aria-hidden="true" />
        </span>
      </label>
      <label className="setting">
        <span>
          <strong>허용 범위</strong>
          <small>{level?.description}</small>
        </span>
        <select
          aria-label="허용 범위"
          value={state.access}
          disabled={!!busy}
          onChange={(event) => void update({ access: event.target.value as ApiAccess }, 'access')}
        >
          {state.levels.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <div className="setting">
        <span>
          <strong>포트</strong>
          <small>
            MCP 주소 <code>{state.mcp_url}</code>
          </small>
        </span>
        <form
          className="api-port"
          onSubmit={(event) => {
            event.preventDefault();
            void update({ port: Number(port) }, 'port');
          }}
        >
          <input
            aria-label="포트"
            type="number"
            min={1024}
            max={65535}
            required
            value={port}
            onChange={(event) => setPort(event.target.value)}
          />
          <button
            className="button secondary small"
            disabled={!!busy || Number(port) === state.port}
          >
            {busy === 'port' && <LoaderCircle className="spin" size={13} />}
            적용
          </button>
        </form>
      </div>
      <div className="setting">
        <span>
          <strong>접근 토큰</strong>
          <small>
            <code>{state.token_hint}</code> ·{' '}
            {state.token_stored
              ? 'OS 보안 저장소로 암호화해 보관합니다.'
              : 'OS 보안 저장소를 쓸 수 없어 앱을 끌 때까지만 유지됩니다.'}
          </small>
        </span>
        <span className="api-actions">
          <button
            type="button"
            className="button secondary small"
            onClick={() => void copy('token', '토큰')}
          >
            <Copy size={13} /> 토큰 복사
          </button>
          <Confirm
            label="새로 발급"
            question="새 토큰을 발급할까요? 지금 토큰을 쓰는 AI 도구는 접속이 거절됩니다."
            onConfirm={async () => {
              try {
                setState(await api<ApiState>('api.rotate_token'));
                notify('새 토큰을 발급했습니다. AI 도구의 설정을 새 토큰으로 바꿔 주세요.');
              } catch (error) {
                notify((error as Error).message, true);
              }
            }}
          />
        </span>
      </div>

      <h3>AI 도구 연결</h3>
      <div className="segmented" role="radiogroup" aria-label="AI 도구">
        {state.clients.map((item) => (
          <button
            key={item.id}
            type="button"
            role="radio"
            aria-checked={chosen.id === item.id}
            tabIndex={chosen.id === item.id ? 0 : -1}
            onKeyDown={rove}
            className={chosen.id === item.id ? 'active' : ''}
            onClick={() => setClient(item.id)}
          >
            {item.name}
          </button>
        ))}
      </div>
      {chosen.snippets.map((snippet) => (
        <div className="api-snippet" key={snippet.id}>
          <div className="api-snippet-head">
            <small>{snippet.label}</small>
            <button
              type="button"
              className="text-button"
              onClick={() => void copy(`${chosen.id}/${snippet.id}`, `${chosen.name} 설정`)}
            >
              <Copy size={13} /> 토큰 넣어 복사
            </button>
          </div>
          {/* Focusable, so a keyboard can scroll a long line into view. */}
          <pre tabIndex={0}>
            <code>{snippet.text}</code>
          </pre>
        </div>
      ))}
      <ul className="guide api-notes">
        {chosen.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
        <li>
          표시된 <code>&lt;PRIVATE_AGENT_TOKEN&gt;</code> 자리는 ‘토큰 넣어 복사’로 실제 토큰이
          채워집니다. 서버가 꺼져 있으면 AI 도구가 접속하지 못합니다.
        </li>
      </ul>

      <h3>최근 요청</h3>
      {state.recent.length ? (
        <ul className="api-recent" aria-label="최근 요청">
          {state.recent.slice(0, 12).map((item, index) => (
            <li key={`${item.at}-${index}`}>
              <span className={item.ok ? 'api-ok' : 'danger'}>{item.ok ? '성공' : '실패'}</span>
              <code>{item.name}</code>
              <small>
                {item.transport.toUpperCase()} · {clock(item.at)}
                {item.client && ` · ${item.client}`}
              </small>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">아직 요청이 없습니다. 이 앱을 다시 시작하면 목록이 비워집니다.</p>
      )}

      <div className="form-actions">
        <button type="button" className="button secondary" onClick={onManual}>
          <BookOpenText size={15} /> API 매뉴얼
        </button>
        <button
          type="button"
          className="button secondary"
          disabled={!state.running}
          title={state.running ? state.docs_url : '서버를 켜면 열 수 있습니다.'}
          onClick={() => void api('api.open_docs').catch((error) => notify(error.message, true))}
        >
          <ExternalLink size={15} /> 브라우저에서 매뉴얼 열기
        </button>
      </div>
    </div>
  );
}
