import type { Backups } from '@/lib/api';

// What the backups screen tells of a restore it asked for. The backend prepares the restore by checking the backup and
// copying it, which takes minutes for a large Wiki on a slow drive, and the window's request for it gives up after 15
// minutes: then the screen sees a failure while the backend goes on, and the restore may be scheduled later without
// the user having been told. So the screen reads wiki.backups again and tells from it where things stand.

// A request for the restore of `file` failed (it ran out of time, say) and wiki.backups was read again: the copy is
// still being made ('preparing'), it was finished after the request gave up ('scheduled'), or the restore was never
// asked for ('failed': the request's own error is the one to show).
export function afterFailedRestore(
  backups: Backups | null,
  file: string,
): 'preparing' | 'scheduled' | 'failed' {
  if (backups?.preparing_restore?.file === file) return 'preparing';
  if (backups?.pending_restore?.file === file) return 'scheduled';
  return 'failed';
}

// How the copy of `before`, which an earlier reading of wiki.backups showed being made (empty: none), ended as of this
// reading: the restore of it now waits for the next start ('scheduled'), or nothing waits for it ('failed'). Null while
// it is still being made, and when there was none.
export function endOfPreparing(before: string, backups: Backups): 'scheduled' | 'failed' | null {
  if (!before || backups.preparing_restore?.file === before) return null;
  return backups.pending_restore?.file === before ? 'scheduled' : 'failed';
}
