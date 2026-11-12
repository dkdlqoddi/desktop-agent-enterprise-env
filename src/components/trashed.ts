import { useCallback } from 'react';
import { api, type TrashRestored, type Trashed } from '@/lib/api';
import type { Tag } from '@/lib/query';
import { useActions } from './state';

// The deletes whose rows wait in the trash (trash.list, backend/agent/trash.py) for 30 days instead of being gone.
type Delete =
  | 'tasks.delete'
  | 'assistant.delete'
  | 'prompts.delete'
  | 'runs.delete'
  | 'schedules.delete'
  | 'routines.delete';

// Deletes one of those and says so with 되돌리기, which brings it back from the trash (trash.restore) and reloads the
// lists of `tags`. A delete that fails is the caller's to show. Nothing is said when there was nothing left to delete
// (it was deleted elsewhere a moment before): the list the caller reloads shows it gone.
export function useDiscard() {
  const { notify, undoable, invalidate } = useActions();
  return useCallback(
    async (method: Delete, id: string, message: string, tags: readonly Tag[]) => {
      const { trash_ids: ids } = await api<Trashed>(method, { id });
      if (!ids.length) return;
      undoable(message, {
        label: '되돌리기',
        run: async () => {
          try {
            const { restored, notes } = await api<TrashRestored>('trash.restore', { ids });
            await invalidate(tags).catch(() => {});
            if (!restored)
              notify('휴지통에 없는 항목입니다. 이미 복원했거나 영구 삭제했습니다.', true);
            // What did not come back as it was, such as a schedule restored turned off, stays until it is read.
            for (const note of notes) notify(note, false, true);
          } catch (error) {
            notify((error as Error).message, true);
          }
        },
      });
    },
    [notify, undoable, invalidate],
  );
}
