import { useEffect, useState } from 'react';
import { CircleAlert, LoaderCircle } from 'lucide-react';
import { api, type QuickAccess } from '@/lib/api';
import { useActions } from '../state';

type Change = Partial<Pick<QuickAccess, 'shortcut' | 'tray' | 'keep_running'>>;

// 빠른 호출: the chat from anywhere on the desktop (electron/quick-access.cjs). The main process keeps these settings
// and answers with what it could do: whether the shortcut is registered (another program may hold it), and whether
// this system can show a tray icon at all.
export default function QuickAccessSettings() {
  const { notify } = useActions();
  const [state, setState] = useState<QuickAccess | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<QuickAccess>('quick.get')
      .then(setState)
      .catch((error) => notify((error as Error).message, true));
  }, [notify]);
  async function update(change: Change) {
    setBusy(true);
    // The control moves at once; the answer then says what the main process did.
    setState((current) => current && { ...current, ...change });
    try {
      setState(await api<QuickAccess>('quick.update', change));
    } catch (error) {
      notify((error as Error).message, true);
      // What is kept, not what was asked for.
      await api<QuickAccess>('quick.get')
        .then(setState)
        .catch(() => {});
    } finally {
      setBusy(false);
    }
  }
  if (!state)
    return (
      <p className="muted">
        <LoaderCircle className="spin" size={15} /> 빠른 호출 설정을 불러오고 있습니다.
      </p>
    );
  // Told once the main process has answered: while a change is saved, `registered` is still the last key's.
  const failed = !busy && !!state.shortcut && !state.registered;
  const noTray = !state.tray_available;
  return (
    <fieldset className="quick-access">
      <legend>빠른 호출</legend>
      <div className="setting">
        <span>
          <strong>채팅 바로 가기 키</strong>
          <small>
            {state.shortcut
              ? '어느 프로그램에서든 이 키를 누르면 앱 창이 앞으로 나오고 채팅 입력란에 바로 쓸 수 있습니다.'
              : '꺼져 있습니다. 키를 고르면 어느 프로그램에서든 채팅을 부를 수 있고, 앱이 실행되는 동안 다른 프로그램은 그 키를 쓸 수 없습니다.'}
          </small>
          {failed && (
            <small className="danger" role="status">
              <CircleAlert size={13} aria-hidden="true" /> 이 키를 등록하지 못했습니다. 다른
              프로그램이 쓰고 있을 수 있으니 다른 키를 고르거나 다시 시도해 주세요.
              <button
                type="button"
                className="text-button"
                onClick={() => void update({ shortcut: state.shortcut })}
              >
                다시 시도
              </button>
            </small>
          )}
        </span>
        <select
          aria-label="채팅 바로 가기 키"
          value={state.shortcut}
          disabled={busy}
          onChange={(event) => void update({ shortcut: event.target.value })}
        >
          {state.presets.map((preset) => (
            <option key={preset.shortcut} value={preset.shortcut}>
              {preset.label}
            </option>
          ))}
          <option value="">사용 안 함</option>
        </select>
      </div>
      <label className="setting">
        <span>
          <strong>트레이 아이콘</strong>
          <small>
            {noTray
              ? '이 환경에서는 트레이를 쓸 수 없습니다.'
              : '알림 영역의 아이콘을 누르면 창이 열리고, 메뉴에서 채팅 열기와 종료를 고릅니다.'}
          </small>
        </span>
        <span className="switch">
          <input
            type="checkbox"
            role="switch"
            aria-label="트레이 아이콘"
            checked={state.tray}
            disabled={busy || noTray}
            onChange={(event) => void update({ tray: event.target.checked })}
          />
          <span aria-hidden="true" />
        </span>
      </label>
      <label className="setting">
        <span>
          <strong>창을 닫아도 계속 실행</strong>
          <small>
            창을 닫아도 트레이에 남아 예약한 작업과 메신저 게이트웨이가 계속 동작하며, 앱은 트레이
            메뉴의 ‘종료’로 끝냅니다.
            {!noTray && !state.tray && ' 트레이 아이콘을 켜야 쓸 수 있습니다.'}
          </small>
        </span>
        <span className="switch">
          <input
            type="checkbox"
            role="switch"
            aria-label="창을 닫아도 계속 실행"
            checked={state.keep_running}
            disabled={busy || noTray || !state.tray}
            onChange={(event) => void update({ keep_running: event.target.checked })}
          />
          <span aria-hidden="true" />
        </span>
      </label>
    </fieldset>
  );
}
