import { useEffect } from 'react';
import type {
  AnalysisJob,
  ImportKind,
  JobKind,
  JobProgressEvent,
  Jobs,
  JobsSnapshot,
  LocalImportJob,
  Routine,
} from '@/lib/api';
import type { Store } from '@/lib/store';

export type { ImportKind } from '@/lib/api';
export type ImportJobs = Omit<Jobs, 'analysis'>;
export const activeJob = (status: string | undefined) =>
  !!status && ['scanning', 'login', 'running', 'cancelling'].includes(status);

// What the screens show of the background jobs: the import box's jobs, the AI analysis of chosen documents, and the
// documents that analysis has still to reach.
export type JobsState = {
  imports: ImportJobs;
  analysis: AnalysisJob | null;
  queued: ReadonlySet<string>;
};
export const NO_JOBS: JobsState = {
  imports: { office: null, rtf: null, image: null, text: null, confluence: null, jira: null },
  analysis: null,
  queued: new Set(),
};

// How often every job is read even without an event: one may have been missed while the window loaded.
export const FALLBACK_MS = 30_000;

// A job's results by their count: the same text means nothing the screens show changed (a job keeps up to 5,000).
const signature = (job: unknown) =>
  JSON.stringify(
    job && { ...(job as object), results: (job as { results?: unknown[] }).results?.length ?? 0 },
  );
const queuedOf = (job: AnalysisJob | null): ReadonlySet<string> =>
  new Set(job?.status === 'running' ? job.queue : []);

// The background jobs of this app session, as the main process reads them (electron/job-progress.cjs): every job once
// as the app starts, then each change as an event while a job runs, and every job again now and then. Each read is
// numbered, and a read older than the one shown is dropped. Jobs keep running when their widget is gone, so this also
// tells when the documents changed: when an import finished, or a batch of a folder imported in batches, or the
// analysis moved on.
export class JobsFeed {
  private seqs = new Map<JobKind, number>();
  private shown = new Map<JobKind, string>();
  // The finished jobs told of; null until the first read, whose finished jobs are no news (the documents were just
  // loaded with the app).
  private known: Set<string> | null = null;
  private batches = new Map<string, number>();
  private analysisMark: string | null = null;

  constructor(
    private options: {
      store: Store<JobsState>;
      request: () => Promise<JobsSnapshot>;
      changed: () => void;
      routines: (routines: Routine[]) => void;
    },
  ) {}

  // Reads every job now: as the app starts, after a job was started, stopped or cleared, and now and then.
  async refresh() {
    let read: JobsSnapshot;
    try {
      read = await this.options.request();
    } catch {
      return; // The backend is busy: the next read, or an event, brings the jobs.
    }
    this.snapshot(read);
  }

  snapshot({ seq, jobs }: JobsSnapshot) {
    const quiet = this.known === null;
    this.known ??= new Set();
    this.take(seq, jobs, quiet);
  }

  event(event: JobProgressEvent) {
    if (event.kind === 'routines') return this.options.routines(event.routines);
    this.take(event.seq, { [event.kind]: event.job }, this.known === null);
  }

  private take(seq: number, jobs: Partial<Jobs>, quiet: boolean) {
    let state = this.options.store.get();
    let news = false;
    for (const kind of Object.keys(jobs) as JobKind[]) {
      if (seq <= (this.seqs.get(kind) ?? 0)) continue;
      this.seqs.set(kind, seq);
      const job = jobs[kind] ?? null;
      news =
        (kind === 'analysis'
          ? this.analysisMoved(job as AnalysisJob | null, quiet)
          : this.importNews(job as ImportJobs[ImportKind], quiet)) || news;
      const text = signature(job);
      if (text === this.shown.get(kind)) continue;
      this.shown.set(kind, text);
      state =
        kind === 'analysis'
          ? { ...state, analysis: job as AnalysisJob | null, queued: queuedOf(job as AnalysisJob) }
          : { ...state, imports: { ...state.imports, [kind]: job } };
    }
    this.options.store.set(state);
    if (news) this.options.changed();
  }

  // A job that finished since the first read, or a batch of a folder done (its documents show then, not only once the
  // whole job is).
  private importNews(job: ImportJobs[ImportKind], quiet: boolean) {
    if (!job) return false;
    let news = false;
    if ('batch' in job) {
      const batch = (job as LocalImportJob).batch;
      news = batch > (this.batches.get(job.id) ?? batch);
      this.batches.set(job.id, batch);
    }
    if (!activeJob(job.status) && !this.known?.has(job.id)) {
      this.known?.add(job.id);
      news ||= !quiet;
    }
    return news;
  }

  // The analysis reached another document, started, or ended: each document takes seconds to minutes.
  private analysisMoved(job: AnalysisJob | null, quiet: boolean) {
    const mark = job ? `${job.id}:${job.processed}:${job.status}` : '';
    const moved = this.analysisMark !== null && mark !== this.analysisMark;
    this.analysisMark = mark;
    return moved && !quiet;
  }
}

// Follows the jobs while the Wiki is open: their changes come as events, every job is read once now and then every
// FALLBACK_MS. The events are listened to before the first read, so that none is lost in between.
export function useJobsFeed(feed: JobsFeed, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const off = window.privateAgent?.onJobProgress?.((event) => feed.event(event));
    void feed.refresh();
    const timer = setInterval(() => void feed.refresh(), FALLBACK_MS);
    return () => {
      off?.();
      clearInterval(timer);
    };
  }, [feed, enabled]);
}
