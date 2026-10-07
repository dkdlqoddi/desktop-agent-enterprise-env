import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  api,
  type ConfluenceStatus,
  type JiraStatus,
  type LocalImportJob,
  type Routine,
} from '@/lib/api';

export type ImportKind = 'office' | 'rtf' | 'image' | 'text' | 'confluence' | 'jira';
export type ImportJobs = {
  office: LocalImportJob | null;
  rtf: LocalImportJob | null;
  image: LocalImportJob | null;
  text: LocalImportJob | null;
  confluence: ConfluenceStatus['job'];
  jira: JiraStatus['job'];
};
export const activeJob = (status: string | undefined) =>
  !!status && ['scanning', 'login', 'running', 'cancelling'].includes(status);

// What the screens show of the jobs, with a job's results by their count: the same signature means nothing changed,
// and the app is not drawn again every second for nothing (a job keeps up to 5,000 results).
const signature = (jobs: ImportJobs) =>
  JSON.stringify(
    Object.values(jobs).map(
      (job) => job && { ...job, results: (job as { results?: unknown[] }).results?.length ?? 0 },
    ),
  );

// The background imports of this app session. They keep running when their widget is gone, so the
// app watches them and reloads the documents whenever one finishes. An automatic routine's run starts
// its imports one after another, the first once it leaves the queue: while a run is queued or running
// they are looked for too, so the import box shows each one as it starts.
export function useImportJobs(onFinished: () => Promise<void>, enabled: boolean) {
  const [jobs, setJobs] = useState<ImportJobs>({
    office: null,
    rtf: null,
    image: null,
    text: null,
    confluence: null,
    jira: null,
  });
  const [watching, setWatching] = useState(0);
  const known = useRef<Set<string> | null>(null);
  const batches = useRef(new Map<string, number>());
  const shown = useRef('');
  const finished = useRef(onFinished);
  finished.current = onFinished;
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      let delay = 0;
      try {
        const [office, rtf, image, text, confluence, jira] = await Promise.all([
          api<LocalImportJob | null>('office.status'),
          api<LocalImportJob | null>('rtf.status'),
          api<LocalImportJob | null>('image.status'),
          api<LocalImportJob | null>('text.status'),
          api<ConfluenceStatus>('confluence.status'),
          api<JiraStatus>('jira.status'),
        ]);
        if (disposed) return;
        const next = {
          office,
          rtf,
          image,
          text,
          confluence: confluence.job,
          jira: jira.job,
        };
        const changed = signature(next);
        if (changed !== shown.current) {
          shown.current = changed;
          setJobs(next);
        }
        // A folder imported in batches: the documents of a batch show once it is done, not only when the job is.
        const batchesNow = new Map(
          [office, rtf, image, text].flatMap((job) => (job ? [[job.id, job.batch] as const] : [])),
        );
        const batchDone = [...batchesNow].some(
          ([id, batch]) => batch > (batches.current.get(id) ?? batch),
        );
        batches.current = batchesNow;
        const done = Object.values(next).filter((job) => job && !activeJob(job.status));
        if (Object.values(next).some((job) => job && activeJob(job.status))) delay = 1000;
        else if (
          (await api<Routine[]>('routines.list').catch((): Routine[] => [])).some(
            (routine) => routine.running || routine.queued,
          )
        )
          delay = 2000;
        if (disposed) return;
        // Jobs that had already finished when the app (or a reloaded screen) started are not news.
        const first = known.current === null;
        known.current ??= new Set();
        const fresh = done.filter((job) => !known.current!.has(job!.id));
        fresh.forEach((job) => known.current!.add(job!.id));
        if ((fresh.length && !first) || batchDone) await finished.current();
      } catch {
        delay = 3000; // The backend is busy; look again shortly.
      }
      if (!disposed && delay) timer = setTimeout(poll, delay);
    }
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [watching, enabled]);
  // Reloads the jobs after one is started, cancelled or cleared, or a routine's run is started or queued; a running
  // job, or run, is followed until it ends.
  const watch = useCallback(() => setWatching((value) => value + 1), []);
  return useMemo(() => ({ jobs, watch }), [jobs, watch]);
}
