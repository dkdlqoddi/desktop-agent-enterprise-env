import { AlertCircle, Check, LoaderCircle, Sparkles, Square, X } from 'lucide-react';
import { api, type AnalysisJob, type WikiDocument } from '@/lib/api';
import { activeJob } from './import-jobs';
import { useActions, useJobs } from './state';

// Whether the LLM has post-processed a document: it has a summary, keywords and relations of its current text.
// Editing the text takes them back; a failed second analysis keeps those of the first.
export const analyzed = (doc: Pick<WikiDocument, 'index_mode'>) => doc.index_mode !== '';

type Labelled = Pick<WikiDocument, 'id' | 'status' | 'index_mode' | 'error'>;
const LABELS = {
  running: ['분석 중', 'LLM이 이 문서를 분석하고 있습니다.'],
  queued: [
    '분석 대기',
    '선택한 문서를 차례로 분석하고 있습니다. 순서가 되면 이 문서를 분석합니다.',
  ],
  done: ['AI 분석 완료', 'LLM이 요약·키워드·관계를 추출한 문서입니다.'],
  failed: ['분석 실패', ''],
  none: ['분석 전', 'LLM으로 분석하지 않은, 가져온 그대로의 문서입니다.'],
} as const;

// The background analysis of chosen documents keeps running when the document list is gone; the jobs' feed
// (import-jobs.ts) follows it and reloads the documents whenever one is done.
function stateOf(doc: Labelled, job: AnalysisJob | null, queued: ReadonlySet<string>) {
  if (doc.status === 'indexing' || (job && activeJob(job.status) && job.current?.id === doc.id))
    return 'running';
  if (queued.has(doc.id)) return 'queued';
  if (analyzed(doc)) return 'done';
  return doc.status === 'error' ? 'failed' : 'none';
}

// Whether the LLM has analyzed a document, wherever documents are listed and in the document window. A label draws
// again only when its own document's state changes, not with every step of the analysis.
export function AnalysisLabel({ doc }: { doc: Labelled }) {
  const state = useJobs((jobs) => stateOf(doc, jobs.analysis, jobs.queued));
  const [label, title] = LABELS[state];
  return (
    <span className={`status-label ${state}`} title={state === 'failed' ? doc.error : title}>
      {state === 'running' ? (
        <LoaderCircle className="spin" size={11} />
      ) : state === 'done' ? (
        <Sparkles size={11} />
      ) : state === 'failed' ? (
        <AlertCircle size={11} />
      ) : null}
      {label}
    </span>
  );
}

function statusText(job: AnalysisJob) {
  switch (job.status) {
    case 'running':
      return '선택한 문서를 차례로 분석하는 중';
    case 'cancelling':
      return '분석 중인 문서까지 마치고 멈추는 중';
    case 'completed':
      return job.results.length
        ? '끝났습니다 · 분석하지 못한 문서가 있습니다'
        : '분석을 마쳤습니다';
    case 'cancelled':
      return '중지했습니다';
    default:
      return '멈췄습니다';
  }
}

// The progress of the chosen documents' analysis, above the document list.
export function AnalysisJobView() {
  const job = useJobs((jobs) => jobs.analysis);
  const { notify, openDocument, watchJobs } = useActions();
  if (!job || job.dismissed) return null;
  const running = activeJob(job.status);
  const act = (
    method: 'analysis.cancel' | 'analysis.dismiss',
    params: Record<string, unknown> = {},
  ) => api(method, params).then(watchJobs, (error: Error) => notify(error.message, true));
  return (
    <div className={`job analysis-job ${job.status}`}>
      {/* Only the state is live, so a screen reader hears it change, not every document counted. */}
      <div className="job-head">
        {running ? (
          <LoaderCircle className="spin" size={15} />
        ) : job.results.length || job.error ? (
          <AlertCircle size={15} />
        ) : (
          <Check size={15} />
        )}
        <strong>AI 분석</strong>
        <span role="status">{statusText(job)}</span>
        <span className="job-progress">
          {job.processed} / {job.total}
        </span>
        {running ? (
          <button
            className="icon-button"
            aria-label="AI 분석 중지"
            title="분석 중인 문서까지 마치고 멈춥니다"
            disabled={job.status === 'cancelling'}
            onClick={() => void act('analysis.cancel')}
          >
            <Square size={14} />
          </button>
        ) : (
          <button
            className="icon-button"
            aria-label="AI 분석 진행 상황 닫기"
            onClick={() => void act('analysis.dismiss', { id: job.id })}
          >
            <X size={14} />
          </button>
        )}
      </div>
      <progress max={Math.max(job.total, 1)} value={job.processed} aria-label="AI 분석 진행률" />
      <p className="job-counts">
        <span>완료 {job.analyzed}</span>
        <span>실패 {job.failed}</span>
        {job.skipped > 0 && <span>건너뜀 {job.skipped}</span>}
      </p>
      {job.current && <p className="job-detail break-path">{job.current.title}</p>}
      {job.error && (
        <p className="inline-error" role="alert">
          {job.error}
        </p>
      )}
      {job.results.length > 0 && (
        <details className="job-results">
          <summary>분석하지 못한 문서 {job.results.length}건</summary>
          <ul>
            {job.results.map((item) => (
              <li key={item.id} className={item.status}>
                {item.title && (
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => openDocument(item.id)}
                  >
                    <strong className="break-path">{item.title}</strong>
                  </button>
                )}
                <span>{item.message}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
