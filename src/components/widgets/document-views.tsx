import { ArrowRight, FileText, Plus } from 'lucide-react';
import { date, plain, type DocumentPage, type DocumentQuery, type WikiDocument } from '@/lib/api';
import { useActions, useQuery, useStats } from '../state';
import { useWidgetFrame, WidgetHead } from '../dashboard';
import EmptyState from '../empty-state';
import { ICONS, LATEST } from './documents';
import { Bars, Metrics } from './parts';

type Kind = keyof typeof ICONS | 'written';
const SOURCES: Record<Kind, string> = {
  written: '직접 작성',
  office: 'Office 문서',
  rtf: 'RTF 문서',
  confluence: 'Confluence',
  jira: 'JIRA',
  text: '텍스트',
  markdown: 'Markdown',
  image: '이미지',
};
const kindOf = (doc: WikiDocument): Kind => doc.source?.kind ?? 'written';
const NONE: never[] = [];

// How many documents the filters keep, as the backend counts them: the total of a page of one document, so no widget
// loads the whole list to count it.
function useCount(filters: DocumentQuery) {
  return useQuery<DocumentPage>('wiki.list', { ...filters, limit: 1 }).data?.total ?? 0;
}

function NoDocuments() {
  const { navigate } = useActions();
  return (
    <EmptyState
      icon={FileText}
      title="첫 번째 지식을 담아 보세요"
      action={
        <button className="text-button" onClick={() => navigate('library', 'import')}>
          자료 가져오기 <ArrowRight size={13} />
        </button>
      }
    >
      메모를 작성하거나 파일과 링크를 가져오세요.
    </EmptyState>
  );
}

// The latest documents as cards: where each came from, its summary and keywords. They are the first page of the
// document list, which says how many there are in all.
export function DocumentCards() {
  const { data } = useQuery<DocumentPage>('wiki.list', LATEST);
  const documents = data?.documents ?? NONE;
  const count = data?.total;
  const { openDocument, newDocument, navigate } = useActions();
  const { size } = useWidgetFrame('documents');
  const shown = documents.slice(0, { s: 3, m: 4, l: 6, xl: 8 }[size]);
  return (
    <>
      <WidgetHead count={count}>
        <button className="button secondary small" onClick={() => newDocument()}>
          <Plus size={14} />새 문서
        </button>
      </WidgetHead>
      {count === 0 && <NoDocuments />}
      <ul className="doc-cards">
        {shown.map((doc) => {
          const Icon = doc.source ? ICONS[doc.source.kind] : FileText;
          return (
            <li key={doc.id}>
              <button className="doc-card" onClick={() => openDocument(doc.id)}>
                <span className="doc-card-kind">
                  <Icon size={13} aria-hidden="true" />
                  {SOURCES[kindOf(doc)]}
                </span>
                <strong>{doc.title}</strong>
                <span className="doc-card-summary">
                  {doc.summary || plain(doc.excerpt ?? '') || '요약 없음'}
                </span>
                {doc.keywords.length > 0 && (
                  <span className="doc-card-tags">
                    {doc.keywords.slice(0, 3).map((keyword) => (
                      <span key={keyword} className="tag">
                        {keyword}
                      </span>
                    ))}
                  </span>
                )}
                <time dateTime={doc.updated_at}>{date(doc.updated_at)}</time>
              </button>
            </li>
          );
        })}
      </ul>
      {!!count && count > shown.length && (
        <button className="text-button" onClick={() => navigate('library')}>
          문서 {count.toLocaleString()}개 모두 보기 <ArrowRight size={13} />
        </button>
      )}
    </>
  );
}

// How much the Wiki holds and how much of it the LLM has analyzed, as the backend counts them.
export function DocumentStats() {
  const stats = useStats();
  const done = useCount({ analysis: 'analyzed' });
  const total = stats.documents;
  const share = total ? Math.round((done / total) * 100) : 0;
  return (
    <>
      <WidgetHead />
      <Metrics
        items={[
          { label: '문서', value: total },
          { label: 'AI 분석', value: done, note: `분석 전 ${Math.max(0, total - done)}개` },
          { label: '키워드', value: stats.keywords },
          { label: '관계', value: stats.relations },
        ]}
      />
      <div className="share-meter">
        <span>
          AI로 분석한 문서 <strong>{share}%</strong>
        </span>
        <span className="share-track" aria-hidden="true">
          <span style={{ width: `${share}%` }} />
        </span>
      </div>
    </>
  );
}

// How many documents each kind of source brought, the largest first: one count of the backend a kind.
function useSourceCounts(): [Kind, number][] {
  return [
    ['written', useCount({ source: 'manual' })],
    ['office', useCount({ source: 'office' })],
    ['rtf', useCount({ source: 'rtf' })],
    ['confluence', useCount({ source: 'confluence' })],
    ['jira', useCount({ source: 'jira' })],
    ['text', useCount({ source: 'text' })],
    ['markdown', useCount({ source: 'markdown' })],
    ['image', useCount({ source: 'image' })],
  ];
}

export function DocumentSources() {
  const stats = useStats();
  const counts = useSourceCounts().filter(([, value]) => value > 0);
  return (
    <>
      <WidgetHead count={stats.documents} />
      {stats.documents ? (
        <Bars
          label="출처별 문서 수"
          items={counts
            .sort((a, b) => b[1] - a[1])
            .map(([kind, value]) => ({ key: kind, label: SOURCES[kind], value }))}
        />
      ) : (
        <NoDocuments />
      )}
    </>
  );
}
