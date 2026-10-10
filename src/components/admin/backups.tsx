import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArchiveRestore,
  CircleAlert,
  CircleCheck,
  DatabaseBackup,
  LoaderCircle,
  Power,
  Undo2,
} from 'lucide-react';
import {
  api,
  dayTime,
  type BackupKind,
  type BackupMade,
  type Backups,
  type RestoreScheduled,
} from '@/lib/api';
import { changedForm } from '../app-control';
import Confirm from '../confirm';
import { useActions } from '../state';

const KINDS: Record<BackupKind, string> = {
  auto: '자동',
  manual: '직접',
  before_restore: '복원 전',
};

// A file's size as the lists show it.
export const fileSize = (bytes: number) =>
  bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024)).toLocaleString()}KB`
    : `${(bytes / 1024 / 1024).toFixed(1)}MB`;

// The Wiki's backups (wiki.backups): the daily ones, those made here, and the Wiki as it was before a restore. A restore
// cannot replace the Wiki under the backend that has it open, so it waits for the next start of the app: the list says
// which one waits, offers to start again now or to take the restore back, and tells how the last restore went. Restoring
// asks first, and its button is marked data-destructive: an AI tool reaches it only at the "full" access level, as the
// local API itself allows the restore (electron/api-catalog.cjs).
export default function BackupsSection() {
  const { notify } = useActions();
  const [backups, setBackups] = useState<Backups | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<'' | 'backup' | 'cancel' | 'restart'>('');
  // A restore just asked for: the keyboard goes on to 지금 다시 시작, as the row's 복원 it came from is now disabled.
  const restartButton = useRef<HTMLButtonElement>(null);
  const [scheduled, setScheduled] = useState(false);
  useEffect(() => {
    if (!scheduled || !restartButton.current) return;
    restartButton.current.focus();
    setScheduled(false);
  }, [scheduled, backups]);
  const load = useCallback(async () => {
    try {
      setBackups(await api<Backups>('wiki.backups'));
      setError('');
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  // A backup now, beside the daily one the app makes by itself.
  async function backUp() {
    setBusy('backup');
    try {
      const made = await api<BackupMade>('wiki.backup');
      notify(`Wiki를 백업했습니다: ${made.file}`);
      await load();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy('');
    }
  }
  async function restore(file: string) {
    try {
      await api<RestoreScheduled>('wiki.restore_backup', { file });
      setScheduled(true);
    } catch (e) {
      notify((e as Error).message, true);
    }
    await load();
  }
  async function cancel() {
    setBusy('cancel');
    try {
      const result = await api<RestoreScheduled>('wiki.restore_backup', { cancel: true });
      notify(result.message);
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy('');
      await load();
    }
  }
  // The app closes and starts again, and the backend restores the Wiki as it starts. What the user is writing would be
  // gone with the window, so it is kept and they are told.
  async function restart() {
    if (changedForm()) {
      notify(
        '저장하지 않은 내용이 있어 다시 시작하지 않았습니다. 저장하거나 취소한 뒤 다시 시작해 주세요.',
        true,
      );
      return;
    }
    setBusy('restart');
    try {
      await api('app.relaunch');
    } catch (e) {
      notify((e as Error).message, true);
      setBusy('');
    }
  }

  const pending = backups?.pending_restore ?? null;
  const last = backups?.last_restore ?? null;
  const when = (file: string) => {
    const found = backups?.backups.find((backup) => backup.file === file);
    return found ? `${dayTime(found.created_at)} 백업(${file})` : `백업 ${file}`;
  };
  return (
    <section className="backups" aria-labelledby="backups-title">
      <h3 id="backups-title">백업과 복원</h3>
      <p>
        자동 백업과 직접 만든 백업은 종류마다 최근 {backups?.keep ?? 7}개를 남깁니다. 백업으로
        되돌리면 지금의 Wiki는 ‘복원 전’ 백업으로 남아, 복원도 되돌릴 수 있습니다.
        {backups?.folder && (
          <>
            {' '}
            백업 폴더: <span className="break-path">{backups.folder}</span>
          </>
        )}
      </p>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {last &&
        (last.status === 'restored' ? (
          <p className="success-box restore-result">
            <CircleCheck size={15} aria-hidden="true" />
            <span>
              {dayTime(last.at)}에 Wiki를 백업 {last.file}(으)로 되돌렸습니다.
              {last.before && ` 그 전의 Wiki는 ${last.before}(으)로 남겼습니다.`}
            </span>
          </p>
        ) : (
          <p className="inline-error restore-result">
            <CircleAlert size={15} aria-hidden="true" />
            <span>
              {dayTime(last.at)}에 백업 {last.file || ''}(으)로 되돌리지 못했습니다. {last.error}
            </span>
          </p>
        ))}
      {pending && (
        <div className="banner warn restore-pending" role="status">
          <CircleAlert size={15} aria-hidden="true" />
          <span>
            앱을 다시 시작하면 Wiki를 {when(pending.file)}의 내용으로 되돌립니다. 되돌리지 않으려면
            다시 시작하기 전에 복원을 취소해 주세요.
          </span>
          <span className="restore-actions">
            <button
              ref={restartButton}
              type="button"
              className="button primary small"
              disabled={!!busy}
              onClick={() => void restart()}
            >
              {busy === 'restart' ? (
                <LoaderCircle className="spin" size={14} aria-hidden="true" />
              ) : (
                <Power size={14} aria-hidden="true" />
              )}
              지금 다시 시작
            </button>
            <button
              type="button"
              className="button secondary small"
              disabled={!!busy}
              onClick={() => void cancel()}
            >
              <Undo2 size={14} aria-hidden="true" />
              복원 취소
            </button>
          </span>
        </div>
      )}
      {backups &&
        (backups.backups.length ? (
          <ul className="backup-list" aria-label="백업 목록">
            {backups.backups.map((backup) => {
              const label = dayTime(backup.created_at);
              const waiting = pending?.file === backup.file;
              return (
                <li key={backup.file}>
                  <span className={`backup-kind ${backup.kind}`}>{KINDS[backup.kind]}</span>
                  <span className="backup-name">
                    <time dateTime={backup.created_at}>{label}</time>
                    <small className="break-path">
                      {backup.file} · {fileSize(backup.size)}
                      {waiting && ' · 복원 예약됨'}
                    </small>
                  </span>
                  <Confirm
                    label="복원"
                    name={`${label} 백업 복원`}
                    icon={<ArchiveRestore size={14} />}
                    question={`${label} 백업으로 Wiki를 되돌릴까요? 앱을 다시 시작할 때 복원하며, 지금의 Wiki는 ‘복원 전’ 백업으로 남깁니다.`}
                    disabled={!!busy || waiting}
                    onConfirm={() => restore(backup.file)}
                  />
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="muted">아직 백업이 없습니다. ‘지금 백업’으로 바로 만들 수 있습니다.</p>
        ))}
      <div className="form-actions">
        <button className="button secondary" disabled={!!busy} onClick={() => void backUp()}>
          {busy === 'backup' ? (
            <LoaderCircle className="spin" size={15} />
          ) : (
            <DatabaseBackup size={15} />
          )}
          지금 백업
        </button>
      </div>
    </section>
  );
}
