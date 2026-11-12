import { useState } from 'react';
import {
  CalendarClock,
  History,
  ListChecks,
  MessageSquareText,
  MessagesSquare,
  Repeat2,
  Trash2,
  Undo2,
  type LucideIcon,
} from 'lucide-react';
import {
  api,
  date,
  type TrashEntries,
  type TrashEntry,
  type TrashKind,
  type TrashRestored,
} from '@/lib/api';
import type { Tag } from '@/lib/query';
import { useActions, useQuery } from '../state';
import Confirm from '../confirm';

// Each kind of entry as the screens name it, and as the object of a sentence.
const KINDS: Record<TrashKind, { label: string; object: string; icon: LucideIcon }> = {
  conversation: { label: '대화', object: '대화를', icon: MessagesSquare },
  run: { label: '실행 기록', object: '실행 기록을', icon: History },
  task: { label: '할 일', object: '할 일을', icon: ListChecks },
  prompt: { label: '프롬프트 템플릿', object: '프롬프트 템플릿을', icon: MessageSquareText },
  schedule: { label: '예약', object: '예약을', icon: CalendarClock },
  routine: { label: '자동 작업', object: '자동 작업을', icon: Repeat2 },
};
// An entry of a kind this version does not know (a later version deleted it) is listed all the same.
const OTHER = { label: '항목', object: '항목을', icon: Trash2 };
// What a restore changes on the other screens; the trash's own list carries every one of them (query.ts TAGS).
const CHANGED: readonly Tag[] = ['prompts', 'runs', 'routines', 'tasks', 'conversations'];

// The trash of what is deleted beside documents (trash.list): conversations, prompt runs, tasks, prompt templates,
// schedules and automatic routines wait here to be restored, or to be deleted for good by the user. The documents'
// trash stays with the document list.
export default function TrashSection() {
  const { notify, invalidate } = useActions();
  const { data, error, reload } = useQuery<TrashEntries>('trash.list');
  const [busy, setBusy] = useState('');
  async function run(key: string, action: () => Promise<void>) {
    setBusy(key);
    try {
      await action();
    } catch (failure) {
      notify((failure as Error).message, true);
      // The entry may be gone, or come back another way: the list says what is there now.
      await reload().catch(() => {});
    } finally {
      setBusy('');
    }
  }
  const restore = (entry: TrashEntry) =>
    run(entry.id, async () => {
      const { restored, notes } = await api<TrashRestored>('trash.restore', { ids: [entry.id] });
      await invalidate(CHANGED).catch(() => {});
      if (restored) notify(`‘${entry.title}’ ${(KINDS[entry.kind] ?? OTHER).object} 복원했습니다.`);
      else notify('휴지통에 없는 항목입니다. 이미 복원했거나 영구 삭제했습니다.', true);
      // What did not come back as it was, such as a schedule restored turned off, stays until it is read.
      for (const note of notes) notify(note, false, true);
    });
  const lead = (
    <p className="section-lead">
      지운 대화·실행 기록·할 일·프롬프트 템플릿·예약·자동 작업은 {data?.days ?? 30}일 동안 여기에
      있다가 자동으로 지워집니다. 복원하면 함께 지워진 메시지·예약·실행 기록도 돌아오고, 예약과 자동
      작업은 휴지통에 있던 동안 지난 실행을 건너뛰고 다음 차례부터 실행합니다. 지운 문서는 지식
      보관함 → 문서의 휴지통에 있습니다.
    </p>
  );
  if (!data)
    return (
      <div className="trash-section">
        {lead}
        {error ? (
          <p className="inline-error" role="alert">
            {error.message}
            <button
              type="button"
              className="text-button"
              onClick={() => void reload().catch(() => {})}
            >
              다시 시도
            </button>
          </p>
        ) : (
          <p className="muted">휴지통을 불러오는 중입니다.</p>
        )}
      </div>
    );
  return (
    <div className="trash-section">
      {lead}
      {error && (
        <p className="inline-error" role="alert">
          {error.message}
        </p>
      )}
      {data.items.length ? (
        <>
          <p className="trash-head">
            <span>
              {data.total > data.items.length
                ? `휴지통의 ${data.total.toLocaleString()}개 중 최근에 지운 ${data.items.length.toLocaleString()}개입니다.`
                : `휴지통에 ${data.total.toLocaleString()}개가 있습니다.`}
            </span>
            <Confirm
              label="휴지통 비우기"
              icon={<Trash2 size={14} />}
              question={`휴지통의 ${data.total.toLocaleString()}개를 모두 영구 삭제할까요? 되돌릴 수 없습니다.`}
              disabled={!!busy}
              onConfirm={() =>
                run('all', async () => {
                  const { purged } = await api<{ purged: number }>('trash.purge', { all: true });
                  await reload();
                  notify(`휴지통의 ${purged.toLocaleString()}개를 영구 삭제했습니다.`);
                })
              }
            />
          </p>
          <ul className="trash-entries" aria-label="휴지통">
            {data.items.map((entry) => {
              const kind = KINDS[entry.kind] ?? OTHER;
              const Icon = kind.icon;
              return (
                <li key={entry.id}>
                  <Icon size={15} aria-hidden="true" />
                  <span className="document-title">
                    <strong>{entry.title}</strong>
                    <small>{[kind.label, entry.detail].filter(Boolean).join(' · ')}</small>
                  </span>
                  <time dateTime={entry.deleted_at}>{date(entry.deleted_at)}</time>
                  <span className="trash-actions">
                    <button
                      type="button"
                      className="button secondary small"
                      aria-label={`${entry.title} 복원`}
                      disabled={!!busy}
                      onClick={() => void restore(entry)}
                    >
                      <Undo2 size={14} />
                      복원
                    </button>
                    <Confirm
                      label="영구 삭제"
                      icon={<Trash2 size={14} />}
                      iconOnly
                      name={`${entry.title} 영구 삭제`}
                      question="영구 삭제할까요? 되돌릴 수 없습니다."
                      disabled={!!busy}
                      onConfirm={() =>
                        run(entry.id, async () => {
                          await api('trash.purge', { ids: [entry.id] });
                          await reload();
                          notify(`‘${entry.title}’ ${kind.object} 영구 삭제했습니다.`);
                        })
                      }
                    />
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      ) : (
        <p className="muted">휴지통이 비어 있습니다.</p>
      )}
    </div>
  );
}
