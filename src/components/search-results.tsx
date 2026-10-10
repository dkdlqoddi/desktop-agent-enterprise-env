import { BookOpen, CornerDownRight, FileText, ListTree, Sparkles } from 'lucide-react';
import { date, plain, type SearchResult, type SearchStep } from '@/lib/api';
import { AnalysisLabel } from './analysis';
import Highlight from './highlight';

type Hit = SearchResult['documents'][number];

function describe(step: SearchStep) {
  switch (step.kind) {
    case 'analyze':
      return `검색어 ${step.terms?.length ?? 0}개를 정했습니다.`;
    case 'keyword':
      return `문서 ${step.count ?? 0}개가 일치했습니다.`;
    case 'agent':
      return (
        (step.reviewed === undefined ? '' : `후보 ${step.reviewed}개 중 `) +
        `문서 ${step.count ?? 0}개를 골랐습니다.${step.detail ? ` ${step.detail}` : ''}`
      );
    case 'search':
      return `‘${step.terms?.join(', ')}’(으)로 다시 찾아 문서 ${step.count ?? 0}개가 일치했습니다.`;
    case 'outline':
      return `‘${step.detail}’의 전체 목차를 확인했습니다.`;
    case 'fallback':
      return `${step.detail} 키워드 검색 순위를 사용했습니다.`;
  }
}

// Prompt search and the assistant's evidence search: analysis, keyword search, AI search steps.
export function SearchSteps({ steps }: { steps: SearchStep[] }) {
  if (!steps.length) return null;
  return (
    <ol className="search-steps" aria-label="검색 과정">
      {steps.map((step, i) => (
        <li key={i} className={step.kind}>
          <strong>{step.label}</strong>
          <span>{describe(step)}</span>
        </li>
      ))}
    </ol>
  );
}

export function ResultCard({
  doc,
  terms,
  busy,
  onOpen,
  onKeyword,
}: {
  doc: Hit;
  terms: string[];
  busy: boolean;
  onOpen: () => void;
  onKeyword: (keyword: string) => void;
}) {
  const { match } = doc;
  // A keyword found by its alias (데이터베이스 for database) shows the alias the search named beside it. The keywords
  // the search matched come first, so the six shown include them.
  const aliases = new Map((match.aliases ?? []).map((item) => [item.keyword, item.alias]));
  const keywords = [...doc.keywords].sort(
    (a, b) => Number(match.matched.includes(b)) - Number(match.matched.includes(a)),
  );
  // The title is the button that opens the document, inside its heading; the button reaches over the whole card (CSS),
  // which stays one place to click without putting the heading inside a button.
  return (
    <article className={`document-card result-card${match.selected ? ' selected' : ''}`}>
      <div className="result-main">
        <div className="document-card-head">
          <div className="result-badges">
            <FileText size={16} />
            {match.selected && (
              <span className="ai-badge">
                <Sparkles size={11} />
                AI 추천
              </span>
            )}
            {doc.index_mode === 'pageindex' && (
              <span className="index-badge" title="긴 문서를 목차 단위로 색인했습니다 (PageIndex)">
                <ListTree size={11} />
                목차 색인
              </span>
            )}
          </div>
          <AnalysisLabel doc={doc} />
        </div>
        <h3>
          <button type="button" className="document-open" disabled={busy} onClick={onOpen}>
            <Highlight text={doc.title} terms={terms} />
          </button>
        </h3>
        {match.reason && (
          <span className="result-reason">
            <Sparkles size={12} />
            {match.reason}
          </span>
        )}
        {match.sections.length > 0 && (
          <span className="result-sections" aria-label="AI가 고른 섹션">
            {match.sections.map((section) => (
              <span key={section.id}>
                <ListTree size={11} />
                {section.title}
              </span>
            ))}
          </span>
        )}
        {match.snippets.length ? (
          <span className="result-snippets">
            {match.snippets.map((snippet, i) => (
              <span key={i}>
                {snippet.section && (
                  <small>
                    <CornerDownRight size={11} />
                    {snippet.section}
                  </small>
                )}
                <Highlight text={snippet.text} terms={terms} />
              </span>
            ))}
          </span>
        ) : (
          <span className="result-summary">{doc.summary || plain(doc.excerpt ?? '')}</span>
        )}
      </div>
      <div className="tags">
        {keywords.slice(0, 6).map((key) => (
          <button
            key={key}
            disabled={busy}
            className={`tag${match.matched.includes(key) ? ' matched' : ''}`}
            title={aliases.has(key) ? `‘${aliases.get(key)}’(으)로 찾은 키워드` : undefined}
            onClick={() => onKeyword(key)}
          >
            # {key}
            {aliases.has(key) && ` (${aliases.get(key)})`}
          </button>
        ))}
        {doc.keywords.length > 6 && <span className="tag muted">+{doc.keywords.length - 6}</span>}
      </div>
      <div className="document-card-footer">
        <span>
          {date(doc.updated_at)} · {doc.size.toLocaleString()}자
        </span>
        <BookOpen size={14} />
      </div>
    </article>
  );
}
