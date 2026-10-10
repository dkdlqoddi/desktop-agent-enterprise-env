import { useEffect, useState } from 'react';
import { ArchiveRestore, LoaderCircle, TriangleAlert } from 'lucide-react';
import {
  api,
  dayTime,
  type WikiDocument,
  type WritebackBackup,
  type WritebackBackups,
  type WritebackRestore,
} from '@/lib/api';
import { fileSize } from './admin/backups';
import Confirm from './confirm';
import { useActions } from './state';

// How the write a copy was made for went, when it did not simply write the file.
const STATES: Partial<Record<WritebackBackup['state'], string>> = {
  writing: '반영 중',
  failed: '반영 실패',
  interrupted: '반영이 중단됨',
};

// The copies of the Office file a document came from, kept before each write-back and each restore
// (writeback.backups), and putting one back in the file's place (writeback.restore_backup). The list loads when it is
// opened, as listing it reads the whole file to tell which copy it holds now. It reads this PC's files, so an AI tool
// reaches it only at the "full" access level (data-requires-full), and restoring asks first with a button marked
// data-destructive, as every action that replaces what the user has.
export default function WritebackBackupsList({
  doc,
  disabled,
  onRestored,
}: {
  doc: WikiDocument;
  // A write-back or an update from the file runs: the file is not replaced meanwhile.
  disabled: boolean;
  onRestored: () => void;
}) {
  const { notify } = useActions();
  const [open, setOpen] = useState(false);
  const [found, setFound] = useState<WritebackBackups | null>(null);
  const [error, setError] = useState('');
  async function load() {
    try {
      setFound(await api<WritebackBackups>('writeback.backups', { doc_id: doc.id }));
      setError('');
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    if (open) void load();
  }, [open, doc.id, doc.revision, disabled]);
  async function restore(id: string) {
    try {
      const result = await api<WritebackRestore>('writeback.restore_backup', {
        doc_id: doc.id,
        backup: id,
      });
      notify(result.message);
      if (result.restored) onRestored();
    } catch (e) {
      notify((e as Error).message, true);
    }
    await load();
  }
  return (
    <details
      className="writeback-backups"
      data-requires-full=""
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>원본 백업</summary>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {!found && !error && (
        <p className="muted">
          <LoaderCircle className="spin" size={13} aria-hidden="true" /> 원본 백업을 불러오는 중…
        </p>
      )}
      {found?.interrupted && (
        <p className="banner warn">
          <TriangleAlert size={14} aria-hidden="true" />
          {found.interrupted.message}
        </p>
      )}
      {found &&
        (found.backups.length ? (
          <ul className="backup-list" aria-label="원본 백업 목록">
            {found.backups.map((backup) => {
              const label = dayTime(backup.created_at);
              const state = STATES[backup.state];
              return (
                <li key={backup.id}>
                  <span className={`backup-kind ${backup.kind}`}>
                    {backup.kind === 'restore' ? '복원 전' : '반영 전'}
                  </span>
                  <span className="backup-name">
                    <time dateTime={backup.created_at}>{label}</time>
                    <small className="break-path">
                      {backup.name} · {fileSize(backup.size)}
                      {state && ` · ${state}`}
                      {backup.current && ' · 지금 원본 파일과 같음'}
                    </small>
                  </span>
                  <Confirm
                    label="원본 백업에서 복원"
                    name={`${label} 원본 백업에서 복원`}
                    icon={<ArchiveRestore size={14} />}
                    question={`원본 파일(${found.file})을 ${label} 백업으로 바꿀까요? 지금 파일은 원본 백업에 먼저 남기고, 문서는 ‘원본에서 업데이트’를 누를 때 바뀝니다.`}
                    disabled={disabled || backup.current}
                    onConfirm={() => restore(backup.id)}
                  />
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="muted">
            아직 원본 백업이 없습니다. ‘원본에 반영’할 때 바뀌기 전 파일이 여기에 남습니다.
          </p>
        ))}
    </details>
  );
}
